#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reranker Precision@3 对比评估 — 基线 vs 实验组

【不易】不改 loader/reranker 代码；纯评估脚本
【变易】基线/实验组对比，复用 eval_skill_retrieval 的 golden set
【简易】自包含指标计算，避免 scripts/ 包路径问题

对比设计:
    基线    : use_vector=True + use_bm25=True + fusion_mode="rrf" + use_reranker=False
    实验组  : 同基线 + use_reranker=True（ONNX 优先，失败降级 PyTorch）

验收: Precision@3 相对提升 ≥ 20%

用法:
    $env:PYTHONIOENCODING="utf-8"
    $env:SKILLS_OFFLINE="1"   # 关闭真向量下载（仅技能检索评估）
    # 指向已下载的 jina-reranker-v2 量化版（ONNX）
    $env:SKILL_RERANKER_MODEL="C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual"
    $env:SKILL_RERANKER_USE_ONNX="true"
    $env:SKILL_RERANKER_ONNX_VARIANT="model_quantized.onnx"
    $env:SKILL_RERANKER_ENABLED="true"
    python scripts/eval_reranker_precision_compare.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

# 项目根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 【不易修复】配置 root logger，让 observability 的 INFO 日志可见
# 原因：app_server.py 入口有 basicConfig，但评估脚本不经过 app_server，
# 导致 observability logger 的 INFO 日志走 lastResort（WARNING+）丢失，
# reranker 调用链（reranker.init / rrf.rerank.applied / rerank.completed）
# 和 sigmoid 分数范围日志全部不可见。
# format="%(message)s"：observability 日志已是 JSON，直接输出 message 即可。
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    stream=sys.stderr,
    force=True,
)

from agent.skills_mgmt.loader import SkillLoader

# 黄金集路径（与 eval_skill_retrieval.py 同源）
_GOLDEN_SET_PATH = _PROJECT_ROOT / "tests" / "eval" / "skill_retrieval_golden_set.json"
_REPORT_PATH = _PROJECT_ROOT / "docs" / "RERANKER_PRECISION_EVAL_REPORT.json"

# 验收阈值
_PRECISION_LIFT_THRESHOLD = 20.0  # %


# ════════════════════════════════════════════════════════════
#  指标计算（复用 eval_skill_retrieval.py 逻辑，自包含避免 import）
# ════════════════════════════════════════════════════════════

def _per_case_metrics(
    actual_ids: List[str],
    expected_ids: List[str],
    k: int,
) -> Tuple[float, float, float]:
    """单个 case 的 Precision@K / Recall@K / MRR

    负样本（expected 空）:
        - actual 也为空 → Precision=1（正确拒绝）
        - actual 非空   → Precision=0（误召回）
    """
    topk = actual_ids[:k]
    if not expected_ids:
        precision = 1.0 if len(topk) == 0 else 0.0
        recall = 1.0
        mrr = 1.0 if len(topk) == 0 else 0.0
        return precision, recall, mrr
    expected_set = set(expected_ids)
    hits = [sid for sid in topk if sid in expected_set]
    precision = len(hits) / k if k > 0 else 0.0
    recall = len(hits) / len(expected_set)
    mrr = 0.0
    for idx, sid in enumerate(topk, start=1):
        if sid in expected_set:
            mrr = 1.0 / idx
            break
    return precision, recall, mrr


def load_golden_set(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ════════════════════════════════════════════════════════════
#  评估主流程
# ════════════════════════════════════════════════════════════

def run_eval(
    loader: SkillLoader,
    top_k: int,
    use_reranker: bool,
) -> Dict[str, Any]:
    """跑一轮评估，返回整体指标 + 各 case 详情"""
    golden = load_golden_set(_GOLDEN_SET_PATH)
    cases_metrics: List[Dict[str, Any]] = []

    for case in golden["test_cases"]:
        query = case["query"]
        expected = case.get("expected_skill_ids", [])
        # 三路融合（vector+bm25+rrf），use_reranker 控制是否精排
        result = loader.match(
            query,
            top_k=top_k,
            enabled_only=True,
            use_vector=True,
            use_bm25=True,
            fusion_mode="rrf",
            use_reranker=use_reranker,
        )
        actual_ids = [m.skill_id for m in result.matches]
        p, r, m = _per_case_metrics(actual_ids, expected, top_k)
        cases_metrics.append({
            "case_id": case["case_id"],
            "query": query,
            "difficulty": case.get("difficulty", "unknown"),
            "expected": expected,
            "actual": actual_ids,
            "precision": round(p, 4),
            "recall": round(r, 4),
            "mrr": round(m, 4),
            "reranked": getattr(result, "reranked", False),
        })

    overall_p = sum(c["precision"] for c in cases_metrics) / len(cases_metrics)
    overall_r = sum(c["recall"] for c in cases_metrics) / len(cases_metrics)
    overall_m = sum(c["mrr"] for c in cases_metrics) / len(cases_metrics)

    # 按难度分组
    by_diff: Dict[str, Dict[str, float]] = {}
    for c in cases_metrics:
        d = c["difficulty"]
        by_diff.setdefault(d, []).append(c)
    by_difficulty = {
        d: {
            "precision": round(sum(x["precision"] for x in items) / len(items), 4),
            "count": len(items),
        }
        for d, items in by_diff.items()
    }

    return {
        "precision": round(overall_p, 4),
        "recall": round(overall_r, 4),
        "mrr": round(overall_m, 4),
        "total_cases": len(cases_metrics),
        "by_difficulty": by_difficulty,
        "cases": cases_metrics,
    }


def main() -> int:
    print("=" * 72)
    print("Reranker Precision@3 对比评估")
    print("=" * 72)
    print(f"模型配置:")
    print(f"  SKILL_RERANKER_MODEL     = {os.environ.get('SKILL_RERANKER_MODEL', '(default v2-m3)')}")
    print(f"  SKILL_RERANKER_USE_ONNX  = {os.environ.get('SKILL_RERANKER_USE_ONNX', '(default true)')}")
    print(f"  SKILL_RERANKER_ONNX_VARIANT = {os.environ.get('SKILL_RERANKER_ONNX_VARIANT', '(default model_quantized.onnx)')}")
    print(f"  SKILL_RERANKER_ENABLED   = {os.environ.get('SKILL_RERANKER_ENABLED', '(default true)')}")
    print()

    # ── Step 0: 验证 Reranker 可用性 ──
    print("[Step 0] 验证 Reranker 可用性...")
    loader = SkillLoader()
    try:
        reranker = loader._get_reranker()
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ _get_reranker() 异常: {e}")
        return 2
    if reranker is None:
        print("  ✗ Reranker 初始化失败（_get_reranker 返回 None）")
        return 2

    print(f"  模型名: {reranker._model_name}")
    print(f"  ONNX 环境开关: {reranker._use_onnx_env}")
    print(f"  ONNX 变体: {reranker._onnx_variant}")
    print(f"  加载中（懒加载触发）...")
    t0 = time.time()
    try:
        avail = reranker.is_available()
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ is_available() 异常: {e}")
        return 2
    load_time = time.time() - t0
    print(f"  is_available: {avail} (加载耗时 {load_time:.2f}s)")
    print(f"  实际使用 ONNX 后端: {reranker._use_onnx}")
    if not avail:
        print("  ✗ Reranker 不可用，无法做对比评估（请检查模型路径/ONNX 文件）")
        return 2
    print("  ✓ Reranker 可用\n")

    # ── Step 1: 基线评估 ──
    top_k = 3
    print(f"[Step 1] 基线评估（use_reranker=False，{top_k} 个用例 × top_k={top_k}）...")
    t0 = time.time()
    baseline = run_eval(loader, top_k=top_k, use_reranker=False)
    baseline_time = time.time() - t0
    print(f"  Precision@3: {baseline['precision']:.4f}")
    print(f"  Recall@3:    {baseline['recall']:.4f}")
    print(f"  MRR:         {baseline['mrr']:.4f}")
    print(f"  耗时: {baseline_time:.1f}s")
    print(f"  分组: {json.dumps(baseline['by_difficulty'], ensure_ascii=False)}\n")

    # ── Step 2: 实验组评估（含 Reranker） ──
    print(f"[Step 2] 实验组评估（use_reranker=True，{top_k} 个用例 × top_k={top_k}）...")
    t0 = time.time()
    experiment = run_eval(loader, top_k=top_k, use_reranker=True)
    exp_time = time.time() - t0
    print(f"  Precision@3: {experiment['precision']:.4f}")
    print(f"  Recall@3:    {experiment['recall']:.4f}")
    print(f"  MRR:         {experiment['mrr']:.4f}")
    print(f"  耗时: {exp_time:.1f}s")
    print(f"  分组: {json.dumps(experiment['by_difficulty'], ensure_ascii=False)}\n")

    # ── Step 3: 对比 ──
    delta_p = experiment["precision"] - baseline["precision"]
    rel_p = (delta_p / baseline["precision"] * 100.0) if baseline["precision"] > 0 else float("inf")
    delta_r = experiment["recall"] - baseline["recall"]
    delta_m = experiment["mrr"] - baseline["mrr"]

    print("=" * 72)
    print("【对比结果】")
    print(f"  Precision@3 : {baseline['precision']:.4f} → {experiment['precision']:.4f}  "
          f"(Δ={delta_p:+.4f}, 相对 {rel_p:+.1f}%)")
    print(f"  Recall@3    : {baseline['recall']:.4f} → {experiment['recall']:.4f}  (Δ={delta_r:+.4f})")
    print(f"  MRR         : {baseline['mrr']:.4f} → {experiment['mrr']:.4f}  (Δ={delta_m:+.4f})")
    print(f"  推理耗时    : {baseline_time:.1f}s → {exp_time:.1f}s "
          f"({exp_time/max(baseline_time,0.01):.1f}x)")
    status = "✓ 达标" if rel_p >= _PRECISION_LIFT_THRESHOLD else "✗ 未达标"
    print(f"\n  验收标准 (Precision@3 相对提升 ≥ {_PRECISION_LIFT_THRESHOLD}%): {status}")
    print("=" * 72)

    # ── 保存详细报告 ──
    report = {
        "summary": {
            "baseline_precision_at_3": baseline["precision"],
            "experiment_precision_at_3": experiment["precision"],
            "precision_delta": round(delta_p, 4),
            "precision_relative_lift_pct": round(rel_p, 2),
            "acceptance_threshold_pct": _PRECISION_LIFT_THRESHOLD,
            "acceptance_passed": rel_p >= _PRECISION_LIFT_THRESHOLD,
            "baseline_elapsed_s": round(baseline_time, 2),
            "experiment_elapsed_s": round(exp_time, 2),
        },
        "config": {
            "model": reranker._model_name,
            "use_onnx_env": reranker._use_onnx_env,
            "use_onnx_actual": reranker._use_onnx,
            "onnx_variant": reranker._onnx_variant,
            "rerank_timeout": reranker._rerank_timeout,
            "min_score": reranker._min_score,
            "top_k": top_k,
        },
        "baseline": baseline,
        "experiment": experiment,
    }
    _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n详细报告已保存: {_REPORT_PATH}")

    return 0 if rel_p >= _PRECISION_LIFT_THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())
