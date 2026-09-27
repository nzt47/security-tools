# -*- coding: utf-8 -*-
r"""learn eval —— 检索质量评测（方案 P5）。

指标：recall@1 / recall@N / MRR / 负题命中率 / top-N 同类率；可选阈值扫描。

【为何必须有相关性下限】RRF 分数是排名派生量（rank1 恒为 w/(k+1)），没有绝对意义
⇒ 不加下限时系统对任何查询都返回 top-K，「命中率」无法计算（实测 8/8 负题全部"有命中"）。
扫出可分间隔后，"--min-score" 即可把命中率变成可测指标。
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _load_questions(path: str):
    import yaml
    with open(path, encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("questions") or []


def cmd_eval(args: argparse.Namespace) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    qs = _load_questions(args.questions)
    if not qs:
        print("[FATAL] 题目为空", file=sys.stderr)
        return 2

    from agent.skills_mgmt.experience_index import ExperienceIndex

    idx = ExperienceIndex(args.samples, persist_dir=args.persist_dir, use_vector=not args.no_vector)
    n = idx.load()
    st = idx.build()
    print("语料 %d 条；构建 %s" % (n, st))
    print("=" * 72)

    pos = [q for q in qs if q.get("expect")]
    neg = [q for q in qs if not q.get("expect")]
    hit1 = hitn = 0
    rr = 0.0
    same_num = same_den = 0
    rows = []
    for q in pos:
        hits = idx.search(q["query"], top_k=args.top_k, include_unverified=True,
                          min_bm25_score=args.min_score)
        ids = [h["id"] for h in hits]
        rank = ids.index(q["expect"]) + 1 if q["expect"] in ids else 0
        hit1 += 1 if rank == 1 else 0
        hitn += 1 if 1 <= rank <= args.top_k else 0
        rr += (1.0 / rank) if rank else 0.0
        for h in hits:
            same_den += 1
            same_num += 1 if h["meta"].get("_task_type") == q.get("task_type") else 0
        rows.append({"id": q["id"], "rank": rank, "hits": len(hits)})

    neg_hit = 0
    for q in neg:
        if idx.search(q["query"], top_k=args.top_k, include_unverified=True,
                      min_bm25_score=args.min_score):
            neg_hit += 1

    print("正题 %d 道（阈值 %.1f）" % (len(pos), args.min_score))
    print("  recall@1     : %d/%d = %.1f%%" % (hit1, len(pos), 100.0 * hit1 / max(len(pos), 1)))
    print("  recall@%-5d : %d/%d = %.1f%%" % (args.top_k, hitn, len(pos), 100.0 * hitn / max(len(pos), 1)))
    print("  MRR          : %.3f" % (rr / max(len(pos), 1)))
    print("  top-%d 同类率: %.1f%%" % (args.top_k, 100.0 * same_num / max(same_den, 1)))
    print()
    print("负题 %d 道（理想 0 命中）" % len(neg))
    print("  有命中       : %d/%d = %.1f%%" % (neg_hit, len(neg), 100.0 * neg_hit / max(len(neg), 1)))
    for r in rows:
        mark = "OK" if r["rank"] == 1 else ("TOP%d" % r["rank"] if r["rank"] else "MISS")
        print("  %-8s %-6s rank=%-4s hits=%d" % (r["id"], mark, r["rank"] or "-", r["hits"]))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump({"corpus_size": n, "threshold": args.min_score,
                       "recall@1": hit1 / max(len(pos), 1),
                       "recall@%d" % args.top_k: hitn / max(len(pos), 1),
                       "mrr": rr / max(len(pos), 1),
                       "negative_hit_rate": neg_hit / max(len(neg), 1), "rows": rows},
                      fh, ensure_ascii=False, indent=2)
        print("\n[ok] -> %s" % args.json_out)
    return 0
