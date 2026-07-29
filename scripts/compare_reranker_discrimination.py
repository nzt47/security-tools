#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比 bge-reranker-v2-m3 和 jina-reranker-v2 在黄金集上的区分度

【不易】不改 loader/reranker 代码；纯对比脚本；复用黄金集保持评估一致性
【变易】支持双模型并行对比；ONNX 优先 + PyTorch 降级；模型不可用时跳过
【简易】单脚本自包含；控制台摘要 + JSON 报告

对比维度:
    1. score_stddev（区分度核心指标）—— 诊断 reranker 是否能区分候选优劣
    2. 排序变化（与 RRF 原序对比）—— 诊断 reranker 是否改变排序
    3. Precision@3（最终业务指标）—— 诊断 reranker 是否带来精度提升

背景:
    评估发现 jina-reranker-v2 量化 ONNX 在黄金集上 score_stddev=0.0（所有候选
    sigmoid 分数完全相同），未改变排序，Precision@3 提升 0%。
    本脚本对比 bge-reranker-v2-m3（~2.3GB，中文 SOTA）是否能提供区分度。

用法:
    # 确保两个模型已下载
    $env:PYTHONIOENCODING="utf-8"
    $env:SKILLS_OFFLINE="1"
    # 可选：指定模型路径（默认从 HuggingFace 缓存读取）
    $env:JINA_MODEL_PATH="C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual"
    $env:BGE_MODEL_PATH="BAAI/bge-reranker-v2-m3"
    python scripts/compare_reranker_discrimination.py

    # 或通过命令行参数
    python scripts/compare_reranker_discrimination.py --jina <path> --bge <path>

前置条件:
    - bge-reranker-v2-m3 需先下载（~2.3GB）:
      python scripts/download_bge_reranker_base_modelscope.py
      或: huggingface-cli download BAAI/bge-reranker-v2-m3
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 项目根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 【不易修复】配置 root logger，让 observability 的 INFO 日志可见
# 与 eval_reranker_precision_compare.py 同源配置
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    stream=sys.stderr,
    force=True,
)

from agent.skills_mgmt.loader import SkillLoader
from agent.skills_mgmt.reranker import SkillReranker

# 黄金集路径（与 eval_reranker_precision_compare.py 同源）
_GOLDEN_SET_PATH = _PROJECT_ROOT / "tests" / "eval" / "skill_retrieval_golden_set.json"
_REPORT_PATH = _PROJECT_ROOT / "docs" / "RERANKER_DISCRIMINATION_COMPARE_REPORT.json"

# 候选池大小（与 loader.py rerank_pool_size = max(top_k*2, 10) 一致）
_CANDIDATE_POOL_SIZE = 10
_TOP_K = 3


# ════════════════════════════════════════════════════════════
#  指标计算
# ════════════════════════════════════════════════════════════

def compute_score_stats(scores: List[float]) -> Dict[str, float]:
    """计算 sigmoid 分数统计量（区分度诊断核心）

    score_stddev 是区分度的核心指标：
    - stddev=0.0 → 所有候选分数相同，reranker 无区分能力
    - stddev>0.0 → 候选分数有差异，reranker 能区分优劣
    """
    if not scores:
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "stddev": 0.0, "range": 0.0}
    mean = sum(scores) / len(scores)
    var = sum((s - mean) ** 2 for s in scores) / len(scores)
    stddev = math.sqrt(var)
    return {
        "min": round(min(scores), 6),
        "max": round(max(scores), 6),
        "mean": round(mean, 6),
        "stddev": round(stddev, 6),
        "range": round(max(scores) - min(scores), 6),
    }


def precision_at_k(actual_ids: List[str], expected_ids: List[str], k: int) -> float:
    """单个 case 的 Precision@K

    负样本（expected 空）:
        - actual 也为空 → Precision=1（正确拒绝）
        - actual 非空   → Precision=0（误召回）
    """
    topk = actual_ids[:k]
    if not expected_ids:
        return 1.0 if len(topk) == 0 else 0.0
    expected_set = set(expected_ids)
    hits = [sid for sid in topk if sid in expected_set]
    return len(hits) / k if k > 0 else 0.0


def rank_change(original_order: List[str], reranked_order: List[str]) -> Dict[str, Any]:
    """计算排序变化（与 RRF 原序对比）

    Returns:
        {
            "changed": bool,           # 排序是否改变
            "top1_changed": bool,     # top1 是否改变
            "kendall_tau": float,     # Kendall Tau 相关系数（-1~1，1=完全一致）
            "swaps": int,             # 逆序对数（0=完全一致）
        }
    """
    if not original_order or not reranked_order:
        return {"changed": False, "top1_changed": False, "kendall_tau": 1.0, "swaps": 0}

    n = min(len(original_order), len(reranked_order))
    orig = original_order[:n]
    reranked = reranked_order[:n]

    # 计算逆序对数（Kendall Tau 距离）
    pos_in_reranked = {sid: i for i, sid in enumerate(reranked)}
    swaps = 0
    total_pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            total_pairs += 1
            if pos_in_reranked.get(orig[i], 0) > pos_in_reranked.get(orig[j], 0):
                swaps += 1

    kendall_tau = 1.0 - 2.0 * swaps / total_pairs if total_pairs > 0 else 1.0
    return {
        "changed": orig != reranked,
        "top1_changed": orig[0] != reranked[0] if orig and reranked else False,
        "kendall_tau": round(kendall_tau, 4),
        "swaps": swaps,
    }


# ════════════════════════════════════════════════════════════
#  候选池获取
# ════════════════════════════════════════════════════════════

def get_candidate_pool(loader: SkillLoader, query: str,
                       pool_size: int = _CANDIDATE_POOL_SIZE) -> List[Dict[str, Any]]:
    """获取 RRF 候选池（不 rerank）

    【不易】use_reranker=False，获取 RRF 原序候选池作为两个模型的统一输入
    【简易】转为 dict 格式（reranker.rerank 期望的输入类型）
    """
    result = loader.match(
        query,
        top_k=pool_size,
        enabled_only=True,
        use_vector=True,
        use_bm25=True,
        fusion_mode="rrf",
        use_reranker=False,  # 关键：不 rerank，获取 RRF 原序
    )
    # 转为 dict 格式（深拷贝避免两个模型互相污染）
    return [
        {
            "skill_id": m.skill_id,
            "name": m.name,
            "description": m.description,
            "score": m.score,
            "category": m.category,
            "tags": list(m.tags) if m.tags else [],
        }
        for m in result.matches
    ]


# ════════════════════════════════════════════════════════════
#  模型 rerank 封装
# ════════════════════════════════════════════════════════════

def rerank_with_model(
    model_name: str,
    query: str,
    candidates: List[Dict[str, Any]],
    reranker_instance: Optional[SkillReranker] = None,
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[List[float]], Optional[str]]:
    """用指定模型 rerank，返回排序结果 + sigmoid 分数

    Args:
        model_name: 模型名/路径（仅用于日志）
        query: 查询文本
        candidates: 候选列表（dict 格式）
        reranker_instance: 复用的 SkillReranker 实例（None 则创建）

    Returns:
        (reranked_candidates, sigmoid_scores, error)
        - reranked_candidates: rerank 后的候选列表（含 rerank_score 字段）
        - sigmoid_scores: 所有候选的 sigmoid 分数（过滤前）
        - error: 错误信息（None 表示成功）
    """
    r = reranker_instance or SkillReranker(model_name=model_name)

    # 强制加载模型
    if not r._load_model():
        return None, None, "model_unavailable"

    # rerank（top_k=None 返回全部过滤后候选）
    # 注意：rerank 会修改 candidates 的 dict 字段，需深拷贝
    import copy
    candidates_copy = copy.deepcopy(candidates)
    result = r.rerank(query, candidates_copy, top_k=None)

    if not result:
        return [], [], None

    # 提取 rerank_score（sigmoid 后的分数）
    scores = [c.get("rerank_score", c.get("score", 0.0)) for c in result]
    return result, scores, None


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="对比 bge-reranker-v2-m3 和 jina-reranker-v2 区分度"
    )
    parser.add_argument(
        "--jina", default=os.environ.get(
            "JINA_MODEL_PATH",
            "C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual",
        ),
        help="jina-reranker-v2 模型路径或 HuggingFace ID",
    )
    parser.add_argument(
        "--bge", default=os.environ.get("BGE_MODEL_PATH", "BAAI/bge-reranker-v2-m3"),
        help="bge-reranker-v2-m3 模型路径或 HuggingFace ID",
    )
    parser.add_argument(
        "--no-onnx", action="store_true",
        help="禁用 ONNX，强制走 PyTorch（对比原始模型区分度）",
    )
    args = parser.parse_args()

    # 配置 ONNX
    if args.no_onnx:
        os.environ["SKILL_RERANKER_USE_ONNX"] = "false"
    else:
        os.environ.setdefault("SKILL_RERANKER_USE_ONNX", "true")
    os.environ.setdefault("SKILL_RERANKER_ENABLED", "true")

    print("=" * 72)
    print("Reranker 区分度对比评估（bge-reranker-v2-m3 vs jina-reranker-v2）")
    print("=" * 72)
    print(f"模型配置:")
    print(f"  jina (基线): {args.jina}")
    print(f"  bge  (候选): {args.bge}")
    print(f"  ONNX 启用 : {os.environ.get('SKILL_RERANKER_USE_ONNX')}")
    print(f"  候选池大小: {_CANDIDATE_POOL_SIZE}")
    print(f"  top_k     : {_TOP_K}")
    print()

    # ── Step 0: 加载黄金集 ──
    print("[Step 0] 加载黄金集...")
    with _GOLDEN_SET_PATH.open("r", encoding="utf-8") as f:
        golden = json.load(f)
    test_cases = golden["test_cases"]
    print(f"  黄金集: {len(test_cases)} 个 case")
    print()

    # ── Step 1: 验证两个模型可用性 ──
    print("[Step 1] 验证模型可用性...")
    loader = SkillLoader()

    # 创建两个 SkillReranker 实例（复用避免重复加载）
    jina_reranker = SkillReranker(model_name=args.jina)
    bge_reranker = SkillReranker(model_name=args.bge)

    print(f"  加载 jina-reranker-v2...")
    t0 = time.time()
    jina_avail = jina_reranker.is_available()
    print(f"    is_available: {jina_avail} (耗时 {time.time()-t0:.1f}s)")
    print(f"    实际使用 ONNX: {jina_reranker._use_onnx}")

    print(f"  加载 bge-reranker-v2-m3...")
    t0 = time.time()
    bge_avail = bge_reranker.is_available()
    print(f"    is_available: {bge_avail} (耗时 {time.time()-t0:.1f}s)")
    print(f"    实际使用 ONNX: {bge_reranker._use_onnx}")

    if not jina_avail and not bge_avail:
        print("\n  ✗ 两个模型都不可用，无法对比")
        print("  下载 bge-reranker-v2-m3:")
        print("    python scripts/download_bge_reranker_base_modelscope.py")
        return 2
    if not bge_avail:
        print("\n  ⚠ bge-reranker-v2-m3 不可用，仅评估 jina（单模型基线）")
        print("  下载 bge-reranker-v2-m3:")
        print("    python scripts/download_bge_reranker_base_modelscope.py")
    print()

    # ── Step 2: 对每个 case 对比 ──
    print(f"[Step 2] 对比评估（{len(test_cases)} 个 case × top_k={_TOP_K}）...")

    case_results: List[Dict[str, Any]] = []
    jina_precision_sum = 0.0
    bge_precision_sum = 0.0
    jina_stddev_sum = 0.0
    bge_stddev_sum = 0.0
    jina_changed_count = 0
    bge_changed_count = 0
    valid_case_count = 0

    for idx, case in enumerate(test_cases, start=1):
        query = case["query"]
        expected = case.get("expected_skill_ids", [])
        case_id = case["case_id"]

        # 获取候选池（RRF 原序，不 rerank）
        pool = get_candidate_pool(loader, query, _CANDIDATE_POOL_SIZE)
        rrf_order = [c["skill_id"] for c in pool]

        if not pool:
            print(f"  [{idx:3d}/{len(test_cases)}] {case_id}: 候选池为空，跳过")
            continue

        valid_case_count += 1

        case_result: Dict[str, Any] = {
            "case_id": case_id,
            "query": query,
            "difficulty": case.get("difficulty", "unknown"),
            "expected": expected,
            "rrf_order": rrf_order,
            "pool_size": len(pool),
        }

        # jina rerank
        jina_result, jina_scores, jina_err = (
            rerank_with_model(args.jina, query, pool, jina_reranker)
            if jina_avail else (None, None, "model_unavailable")
        )
        # bge rerank
        bge_result, bge_scores, bge_err = (
            rerank_with_model(args.bge, query, pool, bge_reranker)
            if bge_avail else (None, None, "model_unavailable")
        )

        # 计算 jina 指标
        if jina_result is not None and not jina_err:
            jina_order = [c["skill_id"] for c in jina_result]
            jina_stats = compute_score_stats(jina_scores)
            jina_p = precision_at_k(jina_order, expected, _TOP_K)
            jina_change = rank_change(rrf_order, jina_order)
            case_result["jina"] = {
                "order": jina_order,
                "top3": jina_order[:_TOP_K],
                "precision_at_3": round(jina_p, 4),
                "score_stats": jina_stats,
                "rank_change": jina_change,
            }
            jina_precision_sum += jina_p
            jina_stddev_sum += jina_stats["stddev"]
            if jina_change["changed"]:
                jina_changed_count += 1
        else:
            case_result["jina"] = {"error": jina_err}

        # 计算 bge 指标
        if bge_result is not None and not bge_err:
            bge_order = [c["skill_id"] for c in bge_result]
            bge_stats = compute_score_stats(bge_scores)
            bge_p = precision_at_k(bge_order, expected, _TOP_K)
            bge_change = rank_change(rrf_order, bge_order)
            case_result["bge"] = {
                "order": bge_order,
                "top3": bge_order[:_TOP_K],
                "precision_at_3": round(bge_p, 4),
                "score_stats": bge_stats,
                "rank_change": bge_change,
            }
            bge_precision_sum += bge_p
            bge_stddev_sum += bge_stats["stddev"]
            if bge_change["changed"]:
                bge_changed_count += 1
        else:
            case_result["bge"] = {"error": bge_err}

        case_results.append(case_result)

        # 进度输出（每 10 个 case 一次摘要）
        if idx % 10 == 0 or idx == len(test_cases):
            jina_avg_std = jina_stddev_sum / valid_case_count if valid_case_count else 0
            bge_avg_std = bge_stddev_sum / valid_case_count if valid_case_count else 0
            print(f"  [{idx:3d}/{len(test_cases)}] jina_std={jina_avg_std:.6f} "
                  f"bge_std={bge_avg_std:.6f}")

    # ── Step 3: 汇总对比 ──
    print()
    print("=" * 72)
    print("【区分度对比结果】")
    print("=" * 72)

    summary: Dict[str, Any] = {
        "total_cases": len(test_cases),
        "valid_cases": valid_case_count,
        "models": {},
    }

    if jina_avail:
        jina_avg_p = jina_precision_sum / valid_case_count if valid_case_count else 0
        jina_avg_std = jina_stddev_sum / valid_case_count if valid_case_count else 0
        jina_change_rate = jina_changed_count / valid_case_count if valid_case_count else 0
        summary["models"]["jina_reranker_v2"] = {
            "model": args.jina,
            "use_onnx": jina_reranker._use_onnx,
            "avg_precision_at_3": round(jina_avg_p, 4),
            "avg_score_stddev": round(jina_avg_std, 6),
            "rank_change_rate": round(jina_change_rate, 4),
            "changed_cases": jina_changed_count,
        }
        print(f"  jina-reranker-v2:")
        print(f"    avg Precision@3   : {jina_avg_p:.4f}")
        print(f"    avg score_stddev  : {jina_avg_std:.6f}")
        print(f"    rank_change_rate  : {jina_change_rate:.2%} ({jina_changed_count}/{valid_case_count})")
        print()

    if bge_avail:
        bge_avg_p = bge_precision_sum / valid_case_count if valid_case_count else 0
        bge_avg_std = bge_stddev_sum / valid_case_count if valid_case_count else 0
        bge_change_rate = bge_changed_count / valid_case_count if valid_case_count else 0
        summary["models"]["bge_reranker_v2_m3"] = {
            "model": args.bge,
            "use_onnx": bge_reranker._use_onnx,
            "avg_precision_at_3": round(bge_avg_p, 4),
            "avg_score_stddev": round(bge_avg_std, 6),
            "rank_change_rate": round(bge_change_rate, 4),
            "changed_cases": bge_changed_count,
        }
        print(f"  bge-reranker-v2-m3:")
        print(f"    avg Precision@3   : {bge_avg_p:.4f}")
        print(f"    avg score_stddev  : {bge_avg_std:.6f}")
        print(f"    rank_change_rate  : {bge_change_rate:.2%} ({bge_changed_count}/{valid_case_count})")
        print()

    # 对比结论
    if jina_avail and bge_avail:
        stddev_lift = (
            (bge_avg_std - jina_avg_std) / jina_avg_std * 100.0
            if jina_avg_std > 0 else float("inf")
        )
        precision_lift = (
            (bge_avg_p - jina_avg_p) / jina_avg_p * 100.0
            if jina_avg_p > 0 else 0.0
        )
        summary["comparison"] = {
            "stddev_lift_pct": round(stddev_lift, 2) if stddev_lift != float("inf") else None,
            "precision_lift_pct": round(precision_lift, 2),
            "bge_more_discriminative": bge_avg_std > jina_avg_std,
            "bge_higher_precision": bge_avg_p > jina_avg_p,
        }
        print("  【对比】")
        print(f"    区分度提升 (stddev): {jina_avg_std:.6f} → {bge_avg_std:.6f} "
              f"({'+' if stddev_lift >= 0 else ''}{stddev_lift:.1f}%)")
        print(f"    精度提升  (P@3)   : {jina_avg_p:.4f} → {bge_avg_p:.4f} "
              f"({'+' if precision_lift >= 0 else ''}{precision_lift:.1f}%)")
        print()
        if bge_avg_std > jina_avg_std:
            print("  ✓ bge-reranker-v2-m3 区分度更高，推荐切换")
        else:
            print("  ✗ bge-reranker-v2-m3 区分度未优于 jina，需进一步调研")

    print("=" * 72)

    # ── 保存报告 ──
    report = {
        "summary": summary,
        "config": {
            "jina_model": args.jina,
            "bge_model": args.bge,
            "use_onnx": os.environ.get("SKILL_RERANKER_USE_ONNX"),
            "candidate_pool_size": _CANDIDATE_POOL_SIZE,
            "top_k": _TOP_K,
        },
        "cases": case_results,
    }
    _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n详细报告已保存: {_REPORT_PATH}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
