"""推荐目录: 客户端「探索页推荐」按排名 / 热度搜候选时, 改在本地这份目录里按同样的条件筛, 不再一条条问 bangumi 搜索.

数据全部取自 bangumi 的 v0 搜索接口 (官方周导出每部只给 11 个标签, 而搜索按约 30 个标签匹配, 用导出建的目录
带标签的那几路只能找回现场结果的一到七成; 搜索接口每部返回 23~30 个标签, 连同收藏数、封面一起).

产出 (都在 rec/ 下, 文件名带内容摘要, CDN 缓存永远不会给出与清单对不上的旧文件):
  manifest.json       清单: 各文件名、行数、sha256, 标签字典, 收录范围 (客户端据此判断一个查询能不能本地答)
  static-<k>.*.tsv    主目录里不常变的部分 (名字、首播日、集数、公共标签、封面), 按 id 每 10 万一片: 只有新进来的条目才改它
  stats.*.tsv         主目录里每周会变的部分 (分数、评分人数、排名、五项收藏数、标签): 标签也在这里, 低票标签每周进进出出 (排第 30 名上下的),
                      放在不常变的那几片里会让每一片每周都变
  recent.*.tsv        近期分片: 近几个月开播的, 按热度取到客户端用得着的深度, 整行 (每天重出)
  recs-<k>.*.tsv      「看过的人还看了」(/p1/subjects/{id}/recs, bangumi 这份数据停在 2023 年上半年, 取过的不再取), 同样按 id 分片

用法:
  python3 scripts/rec.py catalog  --repo . --izuko-raw <izuko-tv raw 根>   每周: 主目录 + recs (只补新条目)
  python3 scripts/rec.py recent   --repo . --izuko-raw <...>               每天: 近期分片
  两个命令都会重写清单, 并删掉新旧两份清单都不引用的文件.
"""
import argparse
import datetime
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.request

API = "https://api.bgm.tv/v0/search/subjects"
UA = {"User-Agent": "GrahamZen/izuko-tv (recommendation catalog; https://github.com/GrahamZen/bangumi-sequel-seasons)"}
PAGE = 20  # 搜索接口一页最多 20 条 (limit 给大了也只回 20)
HIT_CAP = 1000  # 一个查询最多翻到 1000 条
RANK_WINDOW = 500  # 按排名分段取, 每段远小于 HIT_CAP
MIN_VOTES = 1000  # 客户端 RecommendationRepository.MIN_RATING_COUNT
FEED_RECENT_DAYS = 180  # 客户端 FEED_RECENT_DAYS
FEED_CLASSIC_DEPTH = 80 + 20  # 客户端 FEED_CLASSIC_COUNT + TrendsRepository.TRENDING_LIMIT
FEED_RECENT_DEPTH = 120 + 20  # 客户端 FEED_RECENT_COUNT + TRENDING_LIMIT
FEED_MAX_PAGES = 20  # 客户端 FEED_MAX_PAGES
SEASON_AUDIENCE = 600  # 客户端 MIN_SEASON_AUDIENCE: 本季 / 大家最近在看要的在看 + 想看下限
RECENT_MONTHS = 7  # 近期分片从几个月前的 1 号起: 客户端最早要到「大家最近在看」的 6 个月前那个月 1 号
HEAT_MARGIN = 0.9  # 按热度翻时多翻的余量, 见 crawl_by_heat
COVER_PREFIX = "https://lain.bgm.tv/pic/cover/l/"
STATS_KEYS = ("wish", "collect", "doing", "on_hold", "dropped")
FORMAT = 1
requests = 0


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def call(url, body=None):
    global requests
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={**UA, **({"Content-Type": "application/json"} if data else {})})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                requests += 1
                time.sleep(0.4)
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            log(f"  重试 {attempt + 1}: {url} {e}")
            time.sleep(3 * (attempt + 1))
    raise SystemExit(f"请求一直失败, 不发布: {url}")


def search(filters, sort, offset):
    body = {"keyword": "", "sort": sort, "filter": {"type": [2], "nsfw": False, **filters}}
    return call(f"{API}?limit={PAGE}&offset={offset}", body)


def total_of(s):
    c = s.get("collection") or {}
    return sum(int(c.get(k) or 0) for k in STATS_KEYS)


# ---------------- 标签字典与判据 (从 izuko-tv 源码读, 两边对不上时客户端不用这份目录)

def fetch_text(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def read_client(izuko_raw):
    tag_kt = fetch_text(f"{izuko_raw}/app/shared/app-data/src/commonMain/kotlin/data/models/subject/Tag.kt")
    body = tag_kt[tag_kt.index("sealed class CanonicalTagKind"):]
    vocab = sorted(set(re.findall(r'"([^"\n]{1,12})"', body)))
    repo_kt = fetch_text(f"{izuko_raw}/app/shared/app-data/src/commonMain/kotlin/data/network/RecommendationRepository.kt")
    m = re.search(r"TAG_CONFIDENCE = ([0-9.]+)", repo_kt)
    if not m:
        raise SystemExit("izuko-tv 里找不到 TAG_CONFIDENCE")
    return vocab, float(m.group(1))


def row_tags(s, vidx, confidence):
    """词表里的标签, 高票的 (票数 >= 最高票 x confidence) 加 * 排前面 (按票数原序: 客户端判形态时取第一个),
    其余按下标排 (只用来判有没有, 票数相同的接口每次给的顺序不一样, 按原序排文件会无谓地变)."""
    tags = s.get("tags") or []
    top = max((int(t.get("count") or 0) for t in tags), default=0)
    confident, rest = [], []
    for t in tags:
        i = vidx.get(t["name"])
        if i is None:
            continue
        if top > 0 and int(t.get("count") or 0) / top >= confidence:
            confident.append(f"{i}*")
        else:
            rest.append(i)
    return ",".join(confident + [str(i) for i in sorted(rest)])


def clean(text):
    return (text or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def cover_of(s):
    url = (s.get("images") or {}).get("large") or s.get("image") or ""
    return url[len(COVER_PREFIX):] if url.startswith(COVER_PREFIX) else ""


def static_cols(s, graph_ids):
    named = s["id"] not in graph_ids
    return [
        str(s["id"]),
        clean(s.get("name_cn")) if named else "",
        clean(s.get("name")) if named else "",
        s.get("date") or "",
        str(s.get("eps") or 0),
        ",".join(s.get("meta_tags") or []),
        cover_of(s),
    ]


def stats_cols(s, vidx, confidence):
    r = s.get("rating") or {}
    c = s.get("collection") or {}
    return [
        str(s["id"]),
        str(round(float(r.get("score") or 0) * 10)),
        str(int(r.get("total") or 0)),
        str(int(r.get("rank") or 0)),
        ",".join(str(int(c.get(k) or 0)) for k in STATS_KEYS),
        row_tags(s, vidx, confidence),
    ]


# ---------------- 取数

def crawl_ranked():
    """有排名、评分人数 >= MIN_VOTES 的全部动画, 按排名分段取 (一个查询最多 HIT_CAP 条)."""
    found = {}
    lo, empty = 1, 0
    while empty < 3:
        hi = lo + RANK_WINDOW
        f = {"rank": [f">={lo}", f"<{hi}"], "rating_count": [f">={MIN_VOTES}"]}
        offset, got = 0, 0
        while True:
            d = search(f, "rank", offset)
            if d.get("total", 0) >= HIT_CAP:
                raise SystemExit(f"排名段 [{lo}, {hi}) 撞到 {HIT_CAP} 条上限, 要把 RANK_WINDOW 调小")
            page = d.get("data") or []
            for s in page:
                found[s["id"]] = s
            got += len(page)
            offset += len(page)
            if not page or offset >= d.get("total", 0):
                break
        log(f"  排名 [{lo}, {hi}): {got} 部")
        empty = empty + 1 if got == 0 else 0
        lo = hi
    return found


def crawl_by_heat(filters, depth, key, extra_floor=None, date_from=None, max_pages=None):
    """按热度往下翻, 照客户端 searchTopBy 的收手规则: 一页末条的收藏总数低于手上第 depth 名的 key 时停.
    [date_from] 给了时只拿首播在它之后的那部分算第 depth 名 (近期分片比新番榜的窗口宽).
    [extra_floor] 给了时还要翻到收藏总数低于它为止 (本季 / 大家最近在看的在看 + 想看下限).
    bangumi 的热度序用的是它搜索索引里的收藏数, 比接口当下给的略旧 (相邻两部偶尔对调), 所以多翻 HEAT_MARGIN 的余量:
    停在末条低于「要的那条线 x HEAT_MARGIN」时, 当下收藏数在线以上的都已经取到. 返回 (取到的, 为什么停, 保证收全的那条线)."""
    found = []
    offset, pages = 0, 0
    while True:
        d = search(filters, "heat", offset)
        page = d.get("data") or []
        found += page
        pages += 1
        offset += len(page)
        if not page or offset >= min(d.get("total", 0), HIT_CAP):
            return found, "翻到头了", 0
        if max_pages and pages >= max_pages:
            return found, f"到了 {max_pages} 页", None
        scope = [s for s in found if not date_from or (s.get("date") or "") >= date_from]
        keys = sorted((key(s) for s in scope), reverse=True)
        threshold = keys[depth - 1] if len(keys) >= depth else None
        last = total_of(page[-1])
        need = [x for x in (threshold, extra_floor) if x is not None]
        if threshold is not None and need and last < min(need) * HEAT_MARGIN:
            return found, f"末条收藏 {last} 已低于 {min(need)} x {HEAT_MARGIN}", min(need)


def fetch_recs(ids, known):
    """[ids] 里还没取过的取一次「看过的人还看了」: 只留动画, 按 bangumi 给的顺序."""
    out = dict(known)
    todo = [i for i in ids if i not in out]
    log(f"  recs: 已有 {len(known)} 部, 这次取 {len(todo)} 部")
    for n, i in enumerate(todo):
        d = call(f"https://next.bgm.tv/p1/subjects/{i}/recs?limit=10&offset=0")
        out[i] = [(x.get("subject") or {}).get("id") for x in d.get("data") or [] if (x.get("subject") or {}).get("type") == 2]
        if n % 200 == 199:
            log(f"    {n + 1}/{len(todo)}")
    return out


# ---------------- 文件

def write_tsv(repo, name, header, rows):
    text = header + "\n" + "\n".join("\t".join(r) for r in rows) + "\n"
    data = text.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    path = f"rec/{name}.{digest[:10]}.tsv"
    full = os.path.join(repo, path)
    if not os.path.exists(full):
        with open(full, "wb") as f:
            f.write(data)
    return {"path": path, "rows": len(rows), "sha256": digest, "bytes": len(data), "gzip": len(gzip.compress(data, 9))}


def load_manifest(repo):
    p = os.path.join(repo, "rec/manifest.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def read_tsv(repo, entry):
    rows = []
    for line in open(os.path.join(repo, entry["path"]), encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        rows.append(line.rstrip("\n").split("\t"))
    return rows


def write_manifest(repo, old, new):
    def paths(m):
        return {f["path"] for f in m.get("static", []) + m.get("recs", [])} | {m[k]["path"] for k in ("stats", "recent") if k in m}
    keep = paths(new) | (paths(old) if old else set())
    for name in os.listdir(os.path.join(repo, "rec")):
        path = f"rec/{name}"
        if name != "manifest.json" and path not in keep:
            os.remove(os.path.join(repo, path))
    with open(os.path.join(repo, "rec/manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(new, f, ensure_ascii=False, indent=1)
        f.write("\n")
    total = sum(f["gzip"] for f in new["static"] + new["recs"]) + sum(new[k]["gzip"] for k in ("stats", "recent"))
    log(f"清单: 静态 {len(new['static'])} 片 / 统计 / 近期 / recs, 合计 gzip {total / 1024:.0f} KB, 请求 {requests} 个")
    # 提交说明用 (scripts/ci.sh rec 收进 summary 文件)
    print(f"推荐目录 {new['catalog']['rows']} 部 ({new['catalog']['date']}), 近期 {new['recent']['rows']} 部, 请求 {requests} 个")


def today_utc8():
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).date()


def graph_ids_of(repo):
    ids, dump = set(), ""
    for line in open(os.path.join(repo, "bgm-series-graph.tsv"), encoding="utf-8"):
        if line.startswith("#"):
            m = re.search(r"dump=(\S+)", line)
            dump = m.group(1) if m else ""
            continue
        ids.add(int(line.split("\t", 1)[0]))
    return ids, dump


def crawl_recent(vidx, confidence):
    """近期分片: RECENT_MONTHS 个月前那个月 1 号起开播的, 按热度取到客户端新番榜 / 本季 / 大家最近在看用得着的深度."""
    today = today_utc8()
    m = today.year * 12 + (today.month - 1) - RECENT_MONTHS
    since = f"{m // 12}-{m % 12 + 1:02d}-01"
    feed_cutoff = (today - datetime.timedelta(days=FEED_RECENT_DAYS)).isoformat()
    log(f"近期分片: {since} 起开播的, 按热度")
    found, why, level = crawl_by_heat({"air_date": [f">={since}"]}, FEED_RECENT_DEPTH,
                               key=lambda s: int((s.get("collection") or {}).get("doing") or 0),
                               extra_floor=SEASON_AUDIENCE, date_from=feed_cutoff)
    floor = level or 0
    log(f"  {len(found)} 部 ({why}), 收全到收藏 {floor}")
    rows = [static_cols(s, set()) + stats_cols(s, vidx, confidence)[1:] for s in found]
    scope = {"since": since, "min_collection": floor, "date": today.isoformat()}
    return found, rows, scope


def recent_entry(repo, rows, scope):
    return write_tsv(repo, "recent",
                     f"# bgm-rec-recent v{FORMAT} since={scope['since']} | 列: 同 static (名字都在), 再接 stats 除 id 外各列; 按热度序",
                     rows)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_catalog(args):
    vocab, confidence = read_client(args.izuko_raw)
    vidx = {t: i for i, t in enumerate(vocab)}
    graph_ids, graph_dump = graph_ids_of(args.repo)
    old = load_manifest(args.repo)
    log("主目录: 按排名段取")
    found = crawl_ranked()
    cutoff = (today_utc8() - datetime.timedelta(days=FEED_RECENT_DAYS)).isoformat()
    log("没收藏时的老番榜: 按热度取到客户端用得着的深度")
    classics, why, _ = crawl_by_heat({"rank": [">=1"], "air_date": [f"<{cutoff}"]}, FEED_CLASSIC_DEPTH,
                                  key=lambda s: int((s.get("collection") or {}).get("collect") or 0), max_pages=FEED_MAX_PAGES)
    extra = [s for s in classics if s["id"] not in found]
    for s in classics:
        found.setdefault(s["id"], s)
    log(f"  老番榜 {len(classics)} 部 ({why}), 主目录外另收 {len(extra)} 部")
    if old and abs(len(found) - old["catalog"]["rows"]) > old["catalog"]["rows"] * 0.05:
        raise SystemExit(f"主目录 {len(found)} 部, 与上次 {old['catalog']['rows']} 部相差超过 5%, 不发布")
    recent_items, recent_rows, scope = crawl_recent(vidx, confidence)
    items = sorted(found.values(), key=lambda s: s["id"])
    shards = {}
    for s in items:
        shards.setdefault(s["id"] // 100000, []).append(static_cols(s, graph_ids))
    static = [write_tsv(args.repo, f"static-{k}",
                        f"# bgm-rec-static v{FORMAT} | 列: id, 中文名, 原名 (两个都空 = 取系列关系图里的), 首播日, 集数, 公共标签, 封面",
                        rows) for k, rows in sorted(shards.items())]
    stats = write_tsv(args.repo, "stats",
                      f"# bgm-rec-stats v{FORMAT} | 列: id, 分数 x10, 评分人数, 排名, 想看/看过/在看/搁置/抛弃, 标签 (字典下标, * = 高票)",
                      [stats_cols(s, vidx, confidence) for s in items])
    # recs: 取过的接着用 (这份数据 bangumi 已不再更新); 近期分片里的也取, 播放器「相关推荐」打开新番时不用再问
    known = {}
    for entry in (old or {}).get("recs", []):
        for r in read_tsv(args.repo, entry):
            known[int(r[0])] = [int(x) for x in r[1].split(",") if x] if len(r) > 1 else []
    ids = sorted({s["id"] for s in items} | {s["id"] for s in recent_items})
    recs = fetch_recs(ids, known)
    recs_shards = {}
    for i in ids:
        recs_shards.setdefault(i // 100000, []).append([str(i), ",".join(map(str, recs[i]))])
    recs_entries = [write_tsv(args.repo, f"recs-{k}", f"# bgm-rec-recs v{FORMAT} | 列: id, 看过的人还看了 (动画, bangumi 原序; 空 = 没有)", rows)
                    for k, rows in sorted(recs_shards.items())]
    new = {
        "format": FORMAT,
        "generated": now_iso(),
        "tags": vocab,
        "tag_confidence": confidence,
        "names_from_graph": graph_dump,
        "catalog": {"date": today_utc8().isoformat(), "rows": len(items), "min_votes": MIN_VOTES,
                    "classics_before": cutoff, "classics_depth": FEED_CLASSIC_DEPTH},
        "static": static,
        "stats": stats,
        "recent": recent_entry(args.repo, recent_rows, scope),
        "recent_scope": scope,
        "recs": recs_entries,
    }
    write_manifest(args.repo, old, new)


def cmd_recent(args):
    old = load_manifest(args.repo)
    if not old:
        log("还没有清单, 整份出一次")
        return cmd_catalog(args)
    vocab, confidence = read_client(args.izuko_raw)
    if vocab != old["tags"] or confidence != old["tag_confidence"]:
        log("izuko-tv 的标签词表或高票判据变了, 整份重出")
        return cmd_catalog(args)
    vidx = {t: i for i, t in enumerate(vocab)}
    _, rows, scope = crawl_recent(vidx, confidence)
    new = dict(old)
    new["generated"] = now_iso()
    new["recent"] = recent_entry(args.repo, rows, scope)
    new["recent_scope"] = scope
    write_manifest(args.repo, old, new)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["catalog", "recent"])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--izuko-raw", default="https://raw.githubusercontent.com/GrahamZen/izuko-tv/main")
    args = ap.parse_args()
    os.makedirs(os.path.join(args.repo, "rec"), exist_ok=True)
    if args.command == "catalog":
        cmd_catalog(args)
    else:
        cmd_recent(args)


if __name__ == "__main__":
    main()
