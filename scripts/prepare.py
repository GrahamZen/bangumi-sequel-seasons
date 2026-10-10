"""从 Bangumi 官方数据导出取出出表器 (runner/BgmSequelSeasonsRunner.kt) 的输入, 写到 --work 目录:

  anime.jsonl      全部动画条目: id / name / nameCN / date (首播日) / eps (本篇集数) / nsfw / meta (官方标签) /
                   firstSort (本篇第一集的 sort, 没有本篇时为 null) / inlineSpecials (本篇中间的特别篇数)
  relations.jsonl  动画条目之间的关联, 每条目一行, 按 (order, id) 排好 (与 `/p1/subjects/{id}/relations` 的顺序一致)

集数按导出里本篇 (type 0) 分集的个数算, 与接口精简条目 `info` 里的「N话」是同一份数据.
本篇中间的特别篇: sort 不是整数、落在本篇第一集到最后一集之后那一格之间的特别篇 (type 1), 如 86 的 11.5 集.
后两项供客户端识别「一季拆成几个条目」(拆分季): 后一段第一集的 sort 接着前一段编.
"""
import argparse
import io
import json
import os
import sys
import zipfile

ANIME = 2


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

    with open(f"{args.work}/anime.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for sid, s in subjects.items():
            f.write(json.dumps({
                "id": sid, "name": s["name"], "nameCN": s["name_cn"], "date": s.get("date") or "",
                "eps": episodes.get(sid, 0), "nsfw": bool(s.get("nsfw")), "meta": s.get("meta_tags") or [],
                "firstSort": first_sort(sid), "inlineSpecials": inline_specials(sid),
            }, ensure_ascii=False) + "\n")
    with open(f"{args.work}/relations.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for sid, rows in relations.items():
            rows.sort()
            f.write(json.dumps({"id": sid, "rel": [[rid, rtype, order] for order, rid, rtype in rows]}) + "\n")
    print(f"动画 {len(subjects)} 部, 有关联的 {len(relations)} 部")


if __name__ == "__main__":
    main()
