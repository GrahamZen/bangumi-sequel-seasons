"""从 Bangumi 官方数据导出取出出表器 (runner/BgmSequelSeasonsRunner.kt) 的输入, 写到 --work 目录:

  anime.jsonl      全部动画条目: id / name / nameCN / date (首播日) / eps (本篇集数) / nsfw / meta (官方标签) /
                   firstSort (本篇第一集的 sort, 没有本篇时为 null) / inlineSpecials (本篇中间的特别篇数) /
                   aliases (infobox 里的中文名、别名, 去掉与 name / nameCN 相同的)
  relations.jsonl  动画条目之间的关联, 每条目一行, 按 (order, id) 排好 (与 `/p1/subjects/{id}/relations` 的顺序一致)

集数按导出里本篇 (type 0) 分集的个数算, 与接口精简条目 `info` 里的「N话」是同一份数据.
本篇中间的特别篇: sort 不是整数、落在本篇第一集到最后一集之后那一格之间的特别篇 (type 1), 如 86 的 11.5 集.
后两项供客户端识别「一季拆成几个条目」(拆分季): 后一段第一集的 sort 接着前一段编.
"""
import argparse
import io
import json
import os
import re
import sys
import zipfile

ANIME = 2
# infobox 里取作名字的字段: 「别名」是 {[..] [标签|..]} 块, 其余是一行
NAME_FIELDS = ("中文名", "别名", "英文名", "日文名")
INFOBOX_FIELD = re.compile(r"^\|\s*([^=\n]+?)\s*=\s*(.*)$")


def infobox_names(text):
    """infobox 维基文本里的中文名与别名: 「别名」块的每一项 ([名字] 或 [标签|名字]) 与 NAME_FIELDS 里单行的值."""
    names = []
    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        m = INFOBOX_FIELD.match(lines[i].strip())
        i += 1
        if not m or m.group(1) not in NAME_FIELDS:
            continue
        value = m.group(2).strip()
        if value.startswith("{"):
            while i < len(lines) and not lines[i].strip().startswith("}"):
                item = lines[i].strip()
                if item.startswith("[") and item.endswith("]"):
                    names.append(item[1:-1].split("|", 1)[-1].strip())
                i += 1
        elif value:
            names.append(value)
    return names


def open_member(zf, name):
    return io.TextIOWrapper(zf.open(name), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True, help="Bangumi Archive 的 zip")
    ap.add_argument("--work", required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    os.makedirs(args.work, exist_ok=True)

    subjects = {}
    relations = {}
    episodes = {}
    sort_range = {}  # 本篇 sort 的 (最小, 最大)
    fractional_specials = {}  # sort 不是整数的特别篇
    with zipfile.ZipFile(args.dump) as zf:
        with open_member(zf, "subject.jsonlines") as f:
            for line in f:
                s = json.loads(line)
                if s["type"] == ANIME:
                    subjects[s["id"]] = s
        with open_member(zf, "subject-relations.jsonlines") as f:
            for line in f:
                r = json.loads(line)
                if r["subject_id"] in subjects and r["related_subject_id"] in subjects:
                    relations.setdefault(r["subject_id"], []).append(
                        (r["order"], r["related_subject_id"], r["relation_type"]))
        with open_member(zf, "episode.jsonlines") as f:
            for line in f:
                e = json.loads(line)
                sid = e["subject_id"]
                if sid not in subjects:
                    continue
                kind = e.get("type", 0)
                sort = float(e.get("sort") or 0)
                if kind == 0:
                    episodes[sid] = episodes.get(sid, 0) + 1
                    lo, hi = sort_range.get(sid, (sort, sort))
                    sort_range[sid] = (min(lo, sort), max(hi, sort))
                elif kind == 1 and not sort.is_integer():
                    fractional_specials.setdefault(sid, []).append(sort)

    def first_sort(sid):
        r = sort_range.get(sid)
        return int(r[0] // 1) if r else None

    def inline_specials(sid):
        r = sort_range.get(sid)
        if not r:
            return 0
        return sum(1 for x in fractional_specials.get(sid, ()) if r[0] <= x < r[1] + 1)

    def aliases_of(s):
        seen = {s["name"], s["name_cn"]}
        out = []
        for n in infobox_names(s.get("infobox")):
            if n and n not in seen:
                seen.add(n)
                out.append(n)
        return out

    with open(f"{args.work}/anime.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for sid, s in subjects.items():
            f.write(json.dumps({
                "id": sid, "name": s["name"], "nameCN": s["name_cn"], "date": s.get("date") or "",
                "eps": episodes.get(sid, 0), "nsfw": bool(s.get("nsfw")), "meta": s.get("meta_tags") or [],
                "firstSort": first_sort(sid), "inlineSpecials": inline_specials(sid),
                "aliases": aliases_of(s),
            }, ensure_ascii=False) + "\n")
    with open(f"{args.work}/relations.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for sid, rows in relations.items():
            rows.sort()
            f.write(json.dumps({"id": sid, "rel": [[rid, rtype, order] for order, rid, rtype in rows]}) + "\n")
    print(f"动画 {len(subjects)} 部, 有关联的 {len(relations)} 部")


if __name__ == "__main__":
    main()
