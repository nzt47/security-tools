# -*- coding: utf-8 -*-
r"""留一法同族检索评测（P4 决策的**正确**判据）。

【为什么不能用概念题抽检做判据】
    经验库的真实用途是：「我现在要做一个和以前做过的某次相似的任务 —— 以前踩过什么坑？」
    它不是问答库。用「上下文工程与提示词工程的关系」这类**设计观点题**去抽检，
    测的是「库里有没有设计论文」，答案必然是否 —— 这不能证明检索坏了。

【本评测的判据（不依赖 LLM 主观打分，不循环论证）】
    真值 = **改动文件集重叠**（与文本无关的独立信号）：
      若 A、B 两条经验改动了同一批文件且 task_type 相同 ⇒ 判定为「同族」。
    对每条有同族兄弟的样本：
      用它的 task 文本检索（排除自身），看同族兄弟排在第几 ⇒ MRR / recall@k。
    对照组：对**没有**同族兄弟的样本做同样检索 —— 它们的 top1 分数分布应与有同族者**可分**，
      否则闸门无法工作。

输出三件事：
  1) MRR@10 / recall@1 / recall@5 / recall@10
  2) 同族 top1 分数 vs 无同族 top1 分数 的分布与可分性（闸门可行性）
  3) 若不可分，明确指出「闸门这条路走不通」，并给出下一步选项
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import defaultdict
from typing import Dict, List, Set

sys.stdout.reconfigure(encoding="utf-8")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agent.skills_mgmt.experience_index import ExperienceIndex  # noqa: E402

SAMPLES = os.path.join(_ROOT, "data", "experience", "samples.ndjson")


def load() -> List[dict]:
    return [json.loads(l) for l in open(SAMPLES, encoding="utf-8") if l.strip()]


def fileset(s: dict) -> Set[str]:
    return {d.get("path", "") for d in (s.get("diffs") or []) if d.get("path")}


def main() -> int:
    rows = load()
    print("样本 %d 条" % len(rows))

    fs = {s["id"]: fileset(s) for s in rows}
    tt = {s["id"]: s.get("task_type") for s in rows}

    # ── 真值构造：文件集 Jaccard >= 0.5 且 task_type 相同 → 同族 ──
    byfile: Dict[str, List[str]] = defaultdict(list)
    for s in rows:
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
            inter = len(fs[sid] & fs[o])
            union = len(fs[sid] | fs[o])
            if union and inter / union >= 0.5:
                fam[sid].add(o)

    have = [s["id"] for s in rows if fam.get(s["id"])]
    none = [s["id"] for s in rows if not fam.get(s["id"]) and fs[s["id"]]]
    print("有同族兄弟的样本: %d | 有改动但无同族: %d | 无改动: %d"
          % (len(have), len(none), len(rows) - len(have) - len(none)))

    idx = ExperienceIndex(SAMPLES, persist_dir=os.path.join("data", "cache", "loo"),
                          use_vector=False)
    n = idx.load()
    idx.build()
    print("已建索引 %d 条（仅词汇腿）" % n)
    print("=" * 78)

    def probe(sid: str, k: int = 10):
        q = (next(s for s in rows if s["id"] == sid).get("task") or "").strip()
        if not q:
            return None
        hits = idx.search(q, top_k=k + 1, include_unverified=True, min_bm25_score=0.0)
        return [h for h in hits if h["id"] != sid][:k]

    # ── 正样本：同族兄弟的排名 ──
    rr: List[float] = []
    r1 = r5 = r10 = 0
    hit_scores: List[float] = []
    miss_examples: List[str] = []
    for sid in have:
        got = probe(sid)
        if got is None:
            continue
        pos = None
        for r, h in enumerate(got, 1):
            if h["id"] in fam[sid]:
                pos = r
                break
        if pos:
            rr.append(1.0 / pos)
            hit_scores.append(got[0]["raw_bm25"])
            r1 += pos == 1
            r5 += pos <= 5
            r10 += pos <= 10
        else:
            miss_examples.append(sid)
    N = len(rr) + len(miss_examples)
    rr.extend([0.0] * len(miss_examples))

    print("【正样本】可评测 %d 条" % N)
    print("  MRR@10   = %.3f" % (sum(rr) / max(N, 1)))
    print("  recall@1 = %d/%d = %.1f%%" % (r1, N, 100.0 * r1 / max(N, 1)))
    print("  recall@5 = %d/%d = %.1f%%" % (r5, N, 100.0 * r5 / max(N, 1)))
    print("  recall@10= %d/%d = %.1f%%" % (r10, N, 100.0 * r10 / max(N, 1)))

    # ── 对照：无同族者的 top1 分数 ──
    ctrl_scores: List[float] = []
    for sid in none:
        got = probe(sid, k=1)
        if got:
            ctrl_scores.append(got[0]["raw_bm25"])

    def q(v: List[float], p: float) -> float:
        if not v:
            return 0.0
        v = sorted(v)
        return v[min(len(v) - 1, int(p * len(v)))]

    print()
    print("【闸门可行性】top1 原始 BM25 分分布")
    for tag, v in (("有同族（应放行）", hit_scores), ("无同族（应拦截）", ctrl_scores)):
        if not v:
            print("  %-18s 无数据" % tag)
            continue
        print("  %-18s n=%3d  p10=%.1f p50=%.1f p90=%.1f  min=%.1f max=%.1f"
              % (tag, len(v), q(v, .10), q(v, .50), q(v, .90), min(v), max(v)))
    if hit_scores and ctrl_scores:
        lo = q(hit_scores, .10)
        hi = q(ctrl_scores, .90)
        print("  可分性：同族 p10=%.1f  vs  无同族 p90=%.1f  ⇒ %s"
              % (lo, hi, "存在分隔带" if lo > hi else "**不可分**（区间重叠）"))
    print()
    if miss_examples:
        print("  完全没找到同族兄弟的样本 %d 条，示例：%s"
              % (len(miss_examples), ", ".join(miss_examples[:5])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
