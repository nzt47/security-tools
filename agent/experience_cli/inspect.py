# -*- coding: utf-8 -*-
r"""learn inspect —— 人工抽检辅助（方案 P5「人工抽检 10 条」）。

做一件事：把"某个真实问题能检索到什么"摊开成一张**待打勾的表**，
让抽检者只需判断"这条与我的问题相不相关"，不需读代码、不需记 id。

输出两样：
  1) 屏幕上的可读清单（含条目标题、类型、语言、踩坑数、来源日期）
  2) --mark-out 指定的 TSV 标注表（含"相关?"空列，填 y/n 即可）

【为何人工必须做】检索分数只能证明"文本像"，不能证明"答案用得上"。
  方案要求的正是这一步主观判定，任何自动指标都替代不了。
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List


def _load_questions(args) -> List[str]:
    if args.question:
        return [q for q in args.question if q.strip()]
    if args.questions_file:
        with open(args.questions_file, encoding="utf-8") as fh:
            return [ln.strip() for ln in fh
                    if ln.strip() and not ln.strip().startswith("#")]
    return []


def cmd_inspect(args: argparse.Namespace) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    questions = _load_questions(args)
    if not questions:
        print("[FATAL] 需要 --question 或 --questions-file", file=sys.stderr)
        return 2

    from agent.skills_mgmt.experience_index import (ExperienceIndex,
                                                    _DEFAULT_MIN_BM25_SCORE)
    idx = ExperienceIndex(args.samples, persist_dir=args.persist_dir,
                          use_vector=not args.no_vector)
    n = idx.load()
    idx.build()
    print("语料 %d 条 | 相关性下限 %s" % (n, args.min_score))
    print("=" * 78)

    rows: List[dict] = []
    miss = 0
    for qi, q in enumerate(questions, 1):
        hits = idx.search(q, top_k=args.top_k, include_unverified=True,
                          min_bm25_score=args.min_score)
        # 【归因必须准确】"阈值拒绝"与"库里真没有"是两回事，后果也相反：
        #   前者是检索配置问题（改阈值/换腿即可），后者才是"该经验确实没沉淀"。
        #   此前一律报"库中无相关内容"，会让人工抽检（方案唯一的非自动判据）得出错误结论。
        rejected_by_score = False
        if not hits and args.min_score > 0:
            probe = idx.search(q, top_k=args.top_k, include_unverified=True, min_bm25_score=0.0)
            rejected_by_score = bool(probe)
        print()
        print("问题 %d/%d: %s" % (qi, len(questions), q))
        if not hits:
            miss += 1
            if rejected_by_score:
                print("  （未命中：**被相关性下限 %.1f 拒绝**，库中其实有条目但分数不足）"
                      % args.min_score)
                print("    建议加 --min-score 0 复看实际召回了什么，再决定是调阈值还是补语料")
            else:
                print("  （未命中：库中确无相关内容 —— 这本身是有效结论，请记为 'n'）")
            rows.append({"q": "Q%02d" % qi, "question": q, "rank": "-",
                         "id": "-", "task": "（未命中）", "type": "-", "lang": "-",
                         "pitfalls": "-", "date": "-", "score": "-", "relevant": ""})
            continue
        for r, h in enumerate(hits, 1):
            m = h["meta"]
            task = (m.get("description") or "")[:70].replace("\n", " ")
            print("  %d. [%s] %s" % (r, h["id"][:8], task))
            print("     类型=%s 语言=%s 踩坑=%s 来源=%s 分=%.1f"
                  % (m.get("_task_type"), m.get("_lang"), m.get("_n_pitfalls"),
                     (m.get("_created_at") or "")[:10], h.get("raw_bm25", 0.0)))
            rows.append({"q": "Q%02d" % qi, "question": q, "rank": r,
                         "id": h["id"], "task": task,
                         "type": m.get("_task_type"), "lang": m.get("_lang"),
                         "pitfalls": m.get("_n_pitfalls"),
                         "date": (m.get("_created_at") or "")[:10],
                         "score": round(h.get("raw_bm25", 0.0), 2), "relevant": ""})

    print()
    print("=" * 78)
    print("汇总：问题 %d 个，其中 %d 个未命中" % (len(questions), miss))
    if args.mark_out:
        cols = ["q", "question", "rank", "id", "task", "type", "lang",
                "pitfalls", "date", "score", "relevant"]
        with open(args.mark_out, "w", encoding="utf-8") as fh:
            fh.write("# 在 relevant 列填 y / n（是否与你的问题相关）。y 记 1 分。\n")
            fh.write("\t".join(cols) + "\n")
            for r in rows:
                fh.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")
        print("标注表 -> %s" % args.mark_out)
        print("用法：逐行在 relevant 列填 y/n，然后统计 y 的比例 = 抽检精确率。")
    return 0
