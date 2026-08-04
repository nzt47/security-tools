#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断 reranker 是否被调用 + sigmoid 后分数范围

【简易】单文件诊断脚本，跑 3 个 query 打印完整链路
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 降低 logger 级别到 INFO，看 rerank.completed / rrf.rerank.applied
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("agent.skills_mgmt").setLevel(logging.INFO)

from agent.skills_mgmt.loader import SkillLoader

# 3 个代表性 query：直接 ID、中文语义、负样本
TEST_QUERIES = [
    ("self_reflection", "self_reflection"),       # easy：直接 ID
    ("请帮我反思刚才的回答", "self_reflection"),  # hard：中文语义
    ("今天天气真好", None),                        # tricky：负样本
]

def main():
    loader = SkillLoader()
    # Step 0: 验证 reranker 可用
    reranker = loader._get_reranker()
    if reranker is None:
        print("✗ _get_reranker() 返回 None")
        return 1
    print(f"reranker 实例: {type(reranker).__name__}")
    print(f"模型: {reranker._model_name}")
    print(f"use_onnx_env: {reranker._use_onnx_env}")
    t0 = time.time()
    avail = reranker.is_available()
    print(f"is_available: {avail} (加载 {time.time()-t0:.2f}s, use_onnx={reranker._use_onnx})")
    if not avail:
        print("✗ reranker 不可用")
        return 1
    print()

    # Step 1: 跑 3 个 query，对比 use_reranker=False vs True
    for query, expected in TEST_QUERIES:
        print("=" * 70)
        print(f"query: {query!r}  expected: {expected}")
        # 基线
        r_base = loader.match(
            query, top_k=3, enabled_only=True,
            use_vector=True, use_bm25=True,
            fusion_mode="rrf", use_reranker=False,
        )
        # 实验组
        r_exp = loader.match(
            query, top_k=3, enabled_only=True,
            use_vector=True, use_bm25=True,
            fusion_mode="rrf", use_reranker=True,
        )
        print(f"  baseline : method={r_base.retrieval_method} reranked={r_base.reranked}")
        for i, m in enumerate(r_base.matches):
            print(f"    [{i+1}] {m.skill_id} score={m.score} "
                  f"breakdown={m.score_breakdown}")
        print(f"  experiment: method={r_exp.retrieval_method} reranked={r_exp.reranked}")
        for i, m in enumerate(r_exp.matches):
            rrs = m.score_breakdown.get("rerank_score") if m.score_breakdown else None
            print(f"    [{i+1}] {m.skill_id} score={m.score} "
                  f"rerank_score={rrs} breakdown={m.score_breakdown}")
        # 对比
        base_ids = [m.skill_id for m in r_base.matches]
        exp_ids = [m.skill_id for m in r_exp.matches]
        same = base_ids == exp_ids
        print(f"  排序{'相同' if same else '不同'}: base={base_ids} exp={exp_ids}")
        print()

if __name__ == "__main__":
    sys.exit(main() or 0)
