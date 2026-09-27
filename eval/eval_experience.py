#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""eval_experience.py —— 经验库检索评测（方案 P5）。

指标（方案 §5D）：
  recall@5   正题的期望条目是否落在 top-5      —— 方案原话「召回率」
  recall@1   同上，top-1                        —— 方案原话「精确率」(top-1 是否相关)
  MRR        平均倒数排名
  负题命中率 负题（语料内本无对应）有多少返回了命中 —— 越低越好，测乱命中
  同类率     top-5 中 task_type 与查询同类的比例 —— 无人工标注时的相关性代理

【诚实声明】方案要求「人工抽检 10 条是否真用了库内容」，本脚本**不做**该主观判定。
  上述指标均为自动可得；自检索类指标存在"查询与文档同源"的乐观偏差，已在报告中标注。

零 token：纯本地。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--samples", required=True)
    ap.add_argument("--persist-dir", default=os.path.join("data", "skill_vectors", "experience"))
    ap.add_argument("--vector", action="store_true", help="启用向量腿（需加载 BGE-m3）")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--json-out", help="结果落盘")
    a = ap.parse_args()

    with open(a.questions, encoding="utf-8") as fh:
        spec = yaml.safe_load(fh) or {}
    questions: List[Dict[str, Any]] = spec.get("questions") or []
    if not questions:
        print("[FATAL] questions.yaml 为空", file=sys.stderr)
        return 2

    from agent.skills_mgmt.experience_index import ExperienceIndex
    t0 = time.time()
    idx = ExperienceIndex(a.samples, persist_dir=a.persist_dir, use_vector=a.vector)
    n = idx.load()
    st = idx.build()
    build_sec = time.time() - t0
    print("语料 %d 条；构建 %s；用时 %.1fs" % (n, st, build_sec))
    print("=" * 72)

    # 全量命中（含未验证），以便对 expect 做判定
    pos = [q for q in questions if q.get("expect")]
    neg = [q for q in questions if not q.get("expect")]

    hit1 = hit5 = 0
    rr_sum = 0.0
    same_type_num = same_type_den = 0
    rows: List[Dict[str, Any]] = []

    for q in pos:
        hits = idx.search(q["query"], top_k=a.top_k, include_unverified=True)
        ids = [h["id"] for h in hits]
        rank = ids.index(q["expect"]) + 1 if q["expect"] in ids else 0
        if rank == 1:
            hit1 += 1
        if 1 <= rank <= a.top_k:
            hit5 += 1
        if rank:
            rr_sum += 1.0 / rank
        for h in hits:
            same_type_den += 1
            if h["meta"].get("_task_type") == q.get("task_type"):
                same_type_num += 1
        rows.append({"id": q["id"], "q": q["query"], "expect": q["expect"],
                     "rank": rank, "top": ids[:a.top_k], "n_hits": len(hits)})

    neg_hit = 0
    neg_rows: List[Dict[str, Any]] = []
    for q in neg:
        hits = idx.search(q["query"], top_k=a.top_k, include_unverified=True)
        if hits:
            neg_hit += 1
        neg_rows.append({"id": q["id"], "q": q["query"], "n_hits": len(hits),
                         "top_score": (hits[0]["score"] if hits else 0.0)})

    print("正题 %d 道（期望命中指定条目）" % len(pos))
    print("  recall@1      : %d/%d = %.1f%%" % (hit1, len(pos), 100.0 * hit1 / max(len(pos), 1)))
    print("  recall@%d      : %d/%d = %.1f%%" % (a.top_k, hit5, len(pos), 100.0 * hit5 / max(len(pos), 1)))
    print("  MRR           : %.3f" % (rr_sum / max(len(pos), 1)))
    print("  top-5 同类率  : %d/%d = %.1f%%（task_type 与查询同类，无标注时的相关性代理）"
          % (same_type_num, same_type_den, 100.0 * same_type_num / max(same_type_den, 1)))
    print()
    print("负题 %d 道（语料内本无对应，理想为 0 命中）" % len(neg))
    print("  有命中        : %d/%d = %.1f%%" % (neg_hit, len(neg), 100.0 * neg_hit / max(len(neg), 1)))
    if neg_rows:
        mx = max(r["top_score"] for r in neg_rows)
        print("  负题最高分    : %.6f" % mx)
    print()
    print("逐题：")
    for r in rows:
        mark = "OK " if r["rank"] == 1 else ("TOP%d" % r["rank"] if r["rank"] else "MISS")
        print("  %-8s %-6s rank=%-5s hits=%d  %s" % (r["id"], mark, r["rank"] or "-", r["n_hits"], r["q"][:44]))
    for r in neg_rows:
        print("  %-8s %-6s hits=%d top=%.6f  %s" % (r["id"], "NEG", r["n_hits"], r["top_score"], r["q"][:40]))

    result = {
        "corpus_size": n, "build_sec": round(build_sec, 1), "vector": bool(a.vector),
        "positives": len(pos), "negatives": len(neg),
        "recall@1": hit1 / max(len(pos), 1),
        "recall@%d" % a.top_k: hit5 / max(len(pos), 1),
        "mrr": rr_sum / max(len(pos), 1),
        "same_type_rate": same_type_num / max(same_type_den, 1),
        "negative_hit_rate": neg_hit / max(len(neg), 1),
        "rows": rows, "negative_rows": neg_rows,
    }
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        print("\n[ok] -> %s" % a.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
