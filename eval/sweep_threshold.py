#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""sweep_threshold.py —— 相关性下限扫描（方案 P5 的可用性关键）。

【要解决的问题】实测负题 8/8 全部返回 top-5 命中，且 RRF 分数是排名派生量
（rank1 恒为 w/(k+1)=0.006557），**没有绝对意义** —— 于是「命中率」这一方案核心 KPI
无法计算：系统永远"有命中"。

【本脚本】改用 BM25 **原始分**作为相关性度量，扫描阈值 T：
    raw_top1 >= T  ⇒ 系统判定"命中"，返回结果
    raw_top1 <  T  ⇒ 系统判定"未命中"，返回空
  对每个 T 输出：
    recall@5   正题中被回答且期望条目在 top-5 的比例（直接决定命中率上限）
    neg_reject 负题中被正确拒绝（返回空）的比例
  由此给出精确率/召回率的取舍曲线，供选工作点。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

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
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--json-out")
    a = ap.parse_args()

    with open(a.questions, encoding="utf-8") as fh:
        questions = (yaml.safe_load(fh) or {}).get("questions") or []
    rows: List[Dict[str, Any]] = []
    with open(a.samples, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass

    from agent.skills_mgmt.bm25_searcher import BM25SkillSearcher
    from agent.skills_mgmt.experience_index import _sample_to_meta

    docs = {r["id"]: _sample_to_meta(r) for r in rows}
    s = BM25SkillSearcher()
    s.build_index([dict(v, id=k) for k, v in docs.items()])

    pos = [q for q in questions if q.get("expect")]
    neg = [q for q in questions if not q.get("expect")]

    def top_ids(q: str, k: int) -> List[str]:
        return [h.skill_id for h in s.search(q, top_k=k)]

    def raw_top1(q: str) -> float:
        h = s.search(q, top_k=1)
        return float(h[0].score) if h else 0.0

    pos_rows = []
    for q in pos:
        ids = top_ids(q["query"], a.top_k)
        pos_rows.append({"id": q["id"], "raw": raw_top1(q["query"]),
                         "in5": q["expect"] in ids,
                         "rank": (ids.index(q["expect"]) + 1) if q["expect"] in ids else 0})
    neg_rows = [{"id": q["id"], "raw": raw_top1(q["query"])} for q in neg]

    allv = sorted({r["raw"] for r in pos_rows + neg_rows})
    print("语料 %d 条 | 正题 %d 负题 %d" % (len(rows), len(pos), len(neg)))
    print()
    print("正题 top1 原始分：", " ".join("%.1f" % r["raw"] for r in sorted(pos_rows, key=lambda x: x["raw"])))
    print("负题 top1 原始分：", " ".join("%.1f" % r["raw"] for r in sorted(neg_rows, key=lambda x: x["raw"])))
    print()
    print("阈值扫描（命中判定 = raw_top1 >= T）：")
    print("  %-10s %-14s %-14s %s" % ("T", "recall@%d" % a.top_k, "负题拒绝率", "系统回答率"))
    best = None
    for T in allv:
        kept = [r for r in pos_rows if r["raw"] >= T]
        recalled = [r for r in kept if r["in5"]]
        nrej = [r for r in neg_rows if r["raw"] < T]
        rec = len(recalled) / max(len(pos_rows), 1)
        nre = len(nrej) / max(len(neg_rows), 1)
        answered = (len(kept) + (len(neg_rows) - len(nrej))) / max(len(pos_rows) + len(neg_rows), 1)
        f1 = (2 * rec * nre / (rec + nre)) if (rec + nre) else 0.0
        print("  %-10.2f %-14s %-14s %.1f%%" % (T, "%.1f%%" % (100 * rec), "%.1f%%" % (100 * nre), 100 * answered))
        if best is None or f1 > best[1]:
            best = (T, f1, rec, nre)
    print()
    if best:
        print("按 (recall 与 负题拒绝率) 的 F1 最优工作点：T=%.2f  recall=%.1f%%  负题拒绝=%.1f%%"
              % (best[0], 100 * best[2], 100 * best[3]))
        print("  说明：该点是自动选出的折中，实际取值应按'宁可少不要脏'（方案硬约束⑤）偏向提高拒绝率。")
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            json.dump({"positives": pos_rows, "negatives": neg_rows,
                       "best": {"T": best[0], "recall": best[2], "neg_reject": best[3]} if best else None},
                      fh, ensure_ascii=False, indent=2)
        print("\n[ok] -> %s" % a.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
