# -*- coding: utf-8 -*-
r"""去重后的留一法评测 —— **修正被重复样本污染的上一版结论**。

【上一版的错误】eval/leave_one_out.py 只排除了"自己这一个 id"，
    但语料里 57.4% 的样本是**同一 task 文本的重复条目**（唯一指纹 309 / 505）。
    于是"查到自己"极易变成"查到自己的 9 个副本"，而副本的文件集必然一致 ⇒
    被真值判为"同族兄弟" ⇒ recall@k 被系统性抬高。
    实测 top5 内有重复任务指纹的查询占 **74.1%** —— 污染是全局性的，不是边角。

【本版修正】三重排除，确保"检索到的是**另一件事**"：
    1) 语料先按 task 指纹去重（同指纹只留信息量最大的一条）；
    2) 查询时排除**所有**与查询同指纹的条目（leave-one-family-out）；
    3) 真值仍用与文本无关的"改动文件集 Jaccard>=0.5 且 task_type 相同"。

这个数字才是"经验库到底有没有用"的诚实答案。
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Set

sys.stdout.reconfigure(encoding="utf-8")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agent.skills_mgmt.experience_index import ExperienceIndex  # noqa: E402

SAMPLES = os.path.join(_ROOT, "data", "experience", "samples.ndjson")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f-]{20,}")


def fp(t: str) -> str:
    return re.sub(r"\s+", "", _UUID.sub("<UUID>", t or ""))[:400]


def score(s: dict) -> tuple:
    """保留信息量最大的代表：踩坑数 + 去重后改动文件数。"""
    return (len(s.get("pitfalls") or []), len({d.get("path") for d in (s.get("diffs") or [])}))


def main() -> int:
    rows = [json.loads(ln) for ln in open(SAMPLES, encoding="utf-8") if ln.strip()]
    grp: Dict[str, List[dict]] = defaultdict(list)
    for s in rows:
        grp[fp(s.get("task") or "")].append(s)
    dedup = [max(v, key=score) for v in grp.values()]
    print("原始 %d 条 → 去重 %d 条（去掉 %d 条同任务重复）"
          % (len(rows), len(dedup), len(rows) - len(dedup)))

    fs = {s["id"]: {d.get("path", "") for d in (s.get("diffs") or []) if d.get("path")} for s in dedup}
    tt = {s["id"]: s.get("task_type") for s in dedup}
    fp_of = {s["id"]: fp(s.get("task") or "") for s in dedup}

    byfile: Dict[str, List[str]] = defaultdict(list)
    for s in dedup:
        for p in fs[s["id"]]:
            byfile[p].append(s["id"])
    fam: Dict[str, Set[str]] = defaultdict(set)
    for sid, paths in fs.items():
        if not paths:
            continue
        cand: Set[str] = set()
        for p in paths:
            cand.update(byfile[p])
        cand.discard(sid)
        for o in cand:
            if tt[o] != tt[sid] or not fs[o]:
                continue
            u = len(fs[sid] | fs[o])
            if u and len(fs[sid] & fs[o]) / u >= 0.5:
                fam[sid].add(o)

    tmp = os.path.join("data", "cache", "loo_dedup")
    os.makedirs(tmp, exist_ok=True)
    dp = os.path.join(tmp, "dedup.ndjson")
    with open(dp, "w", encoding="utf-8") as fh:
        for s in dedup:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")

    idx = ExperienceIndex(dp, persist_dir=tmp + "_idx", use_vector=False)
    idx.load()
    idx.build()

    have = [s["id"] for s in dedup if fam.get(s["id"])]
    print("有同族兄弟: %d / %d = %.1f%%" % (len(have), len(dedup), 100.0 * len(have) / len(dedup)))
    print("=" * 78)

    rr: List[float] = []
    r1 = r5 = r10 = 0
    sib_scores: List[float] = []
    ctrl_scores: List[float] = []
    for s in dedup:
        q = (s.get("task") or "").strip()
        if not q:
            continue
        myfp = fp_of[s["id"]]
        hits = [h for h in idx.search(q, top_k=30, include_unverified=True, min_bm25_score=0.0)
                if fp_of.get(h["id"]) != myfp][:10]
        if not hits:
            continue
        if fam.get(s["id"]):
            pos = next((r for r, h in enumerate(hits, 1) if h["id"] in fam[s["id"]]), None)
            if pos:
                rr.append(1.0 / pos)
                sib_scores.append(hits[0]["raw_bm25"])
                r1 += pos == 1
                r5 += pos <= 5
                r10 += pos <= 10
            else:
                rr.append(0.0)
        else:
            ctrl_scores.append(hits[0]["raw_bm25"])

    N = len(rr)
    print("【正样本 · 去重 + 排除同指纹】可评测 %d 条" % N)
    print("  MRR@10   = %.3f" % (sum(rr) / max(N, 1)))
    print("  recall@1 = %d/%d = %.1f%%" % (r1, N, 100.0 * r1 / max(N, 1)))
    print("  recall@5 = %d/%d = %.1f%%" % (r5, N, 100.0 * r5 / max(N, 1)))
    print("  recall@10= %d/%d = %.1f%%" % (r10, N, 100.0 * r10 / max(N, 1)))

    def qtl(v: List[float], p: float) -> float:
        if not v:
            return 0.0
        v = sorted(v)
        return v[min(len(v) - 1, int(p * len(v)))]

    print()
    print("【对照】top1 原始 BM25 分")
    for tag, v in (("有同族", sib_scores), ("无同族", ctrl_scores)):
        if v:
            print("  %-8s n=%3d p10=%.1f p50=%.1f p90=%.1f" % (tag, len(v), qtl(v, .1), qtl(v, .5), qtl(v, .9)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
