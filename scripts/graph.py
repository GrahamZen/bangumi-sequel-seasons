"""从 prepare.py 的产出生成 bgm-series-graph.tsv: Bangumi 动画的前传 / 续集关系图, 连同客户端走系列时要用的条目字段.

客户端拿它代替一跳一个请求的 `/p1/subjects/{id}/relations`: 系列索引、续作换季、TMDB 找系列主条目、数据源搜索的系列名
都在本地照同一套走法算. 字段与客户端从接口构造的节点 (SeriesNode) 逐项对应, 口径同 runner/BgmSequelSeasonsRunner.kt:
- 关联只看动画, 按 (order, id) 排好后取前 RELATIONS_PAGE_SIZE 条 (= 接口一页), 其中关系类型 2 是前传、3 是续集;
- 首播日、本篇集数、nsfw 取自导出; 类型只存条目官方标签里第一个属于 CATEGORY 的 (客户端判断「是不是一季」只看这一个).

收录: 有前传或续集的动画, 以及被它们指到的 (名字与字段要从这一行取). 表覆盖到 (id 不超过 max_id) 却不在表里 = 没有前传与续集.
"""
import argparse
import json

RELATIONS_PAGE_SIZE = 50  # 客户端 SERIES_RELATIONS_PAGE_SIZE
PREQUEL, SEQUEL = 2, 3  # 客户端 SERIES_RELATION_PREQUEL / SERIES_RELATION_SEQUEL
# 客户端 CanonicalTagKind.Category 的取值, 顺序无关 (取的是条目标签里第一个落在这里面的)
CATEGORY = {"短片", "剧场版", "TV", "OVA", "MV", "CM", "WEB", "PV", "动态漫画"}


def clean(text):
    return (text or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    anime = {}
    with open(f"{args.work}/anime.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                a = json.loads(line)
                anime[a["id"]] = a

    edges = {}
    with open(f"{args.work}/relations.jsonl", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            # [关联条目 id, 关系类型, order], prepare.py 已按 (order, id) 排好
            page = [x for x in r["rel"] if x[0] in anime][:RELATIONS_PAGE_SIZE]
            pre = [x[0] for x in page if x[1] == PREQUEL]
            seq = [x[0] for x in page if x[1] == SEQUEL]
            if pre or seq:
                edges[r["id"]] = (pre, seq)

    members = set(edges)
    for pre, seq in edges.values():
        members.update(pre)
        members.update(seq)

    rows = []
    for i in sorted(members):
        a = anime[i]
        category = next((t for t in a.get("meta") or [] if t in CATEGORY), "")
        pre, seq = edges.get(i, ([], []))
        rows.append("\t".join([
            str(i),
            clean(a.get("name")),
            clean(a.get("nameCN")),
            clean(a.get("date")),
            str(a["eps"]) if a.get("eps") else "",
            category,
            "1" if a.get("nsfw") else "",
            ",".join(map(str, pre)),
            ",".join(map(str, seq)),
        ]))

    dump = open(f"{args.work}/dump.name", encoding="utf-8").read().strip() or "unknown"
    header = (f"# bangumi-series-graph v1 max_id={max(anime)} dump={dump}"
              " | 列: bgm_id, 原名, 中文名, 首播日, 本篇集数, 类型, nsfw, 前传, 续集 (逗号分隔的 bgm id)."
              " 收有前传或续集的动画与它们指到的; 表覆盖到 (id 不超过 max_id) 却不在表里 = 没有前传与续集. 说明见 README.")
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join([header] + rows) + "\n")
    print(f"系列关系图: {len(anime)} 部动画里 {len(rows)} 部 (有关系的 {len(edges)}) -> {args.out}")


main()
