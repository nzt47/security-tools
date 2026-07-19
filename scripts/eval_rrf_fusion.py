"""RRF 融合检索评估脚本 — 四方对比 TF-IDF / Vector / RRF / RRF+Reranker

用法:
    # 默认四方对比
    python scripts/eval_rrf_fusion.py

    # 自定义 K 值
    python scripts/eval_rrf_fusion.py --top-k 5

    # 详细模式：打印每个用例的实际召回
    python scripts/eval_rrf_fusion.py --verbose

    # 保存对比报告到文件
    python scripts/eval_rrf_fusion.py --output tests/eval/rrf_fusion_report.json

    # 只跑 reranker 评估（验证 Cross-Encoder 精排）
    python scripts/eval_rrf_fusion.py --only rrf_rerank --verbose

【不易】不改黄金集、不改技能定义、不改 loader.match 现有签名
【变易】新增 fusion_mode="rrf_rerank" 路径评估
【简易】复用 eval_vector_vs_tfidf.py 的指标计算逻辑
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.skills_mgmt.loader import SkillLoader  # noqa: E402
import eval_skill_retrieval as ev  # noqa: E402


def _evaluate_method(
    method: str,
    golden_set_path: Path,
    top_k: int,
    *,
    verbose: bool = False,
) -> Dict[str, Any]:
    """评估单一检索方法

    Args:
        method: "tfidf" / "vector" / "rrf"
        golden_set_path: 黄金集路径
        top_k: 评估 K 值
        verbose: 是否打印每个用例的召回结果
    """
    with golden_set_path.open("r", encoding="utf-8") as f:
        golden = json.load(f)

    loader = SkillLoader()
    available_skills = sorted(loader.fs.load_metadata_index().keys())

    # 方法路由：
    # - tfidf: use_vector=False (默认走 TF-IDF)
    # - vector: use_vector=True, fusion_mode="none" (单路向量)
    # - rrf: use_vector=True, fusion_mode="rrf" (双路融合)
    # - rrf_rerank: use_vector=True, use_reranker=True, fusion_mode="rrf" (双路融合+精排)
    #   （fusion_mode 会在 loader 内自动升级为 rrf_rerank）
    use_vector = method in ("vector", "rrf", "rrf_rerank")
    fusion_mode = "rrf" if method in ("rrf", "rrf_rerank") else "none"
    use_reranker = (method == "rrf_rerank")

    cases_metrics: List[Dict[str, Any]] = []

    for case in golden["test_cases"]:
        query = case["query"]
        expected = case.get("expected_skill_ids", [])

        result = loader.match(
            query, top_k=top_k, enabled_only=True,
            use_vector=use_vector, fusion_mode=fusion_mode,
            use_reranker=use_reranker,
        )
        actual_ids = [m.skill_id for m in result.matches]
        actual_scores = [m.score for m in result.matches]
        # RRF 模式额外收集 score_breakdown 用于诊断
        actual_breakdowns = [m.score_breakdown for m in result.matches]

        precision, recall, mrr = ev._per_case_metrics(actual_ids, expected, top_k)
        fallback = result.fallback_used

        case_detail = {
            "case_id": case["case_id"],
            "query": query,
            "expected": expected,
            "actual": actual_ids,
            "actual_scores": actual_scores,
            "actual_breakdowns": actual_breakdowns,
            "difficulty": case.get("difficulty", "unknown"),
            "category": case.get("category", "unknown"),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "mrr": round(mrr, 4),
            "hit": precision > 0,
            "fallback_used": fallback,
            "retrieval_method": result.retrieval_method,
        }
        cases_metrics.append(case_detail)

        if verbose:
            fallback_marker = " [fallback]" if fallback else ""
            hit_marker = "✓" if precision > 0 else "✗"
            print(f"  {hit_marker} {case['case_id']:<10} "
                  f"P={precision:.2f} R={recall:.2f} MRR={mrr:.2f}"
                  f"{fallback_marker}  {query[:35]}")
            print(f"    expected: {expected}")
            print(f"    actual  : {[(s, round(sc, 3)) for s, sc in zip(actual_ids, actual_scores)]}")

    overall_p = ev._aggregate([c["precision"] for c in cases_metrics])["mean"]
    overall_r = ev._aggregate([c["recall"] for c in cases_metrics])["mean"]
    overall_m = ev._aggregate([c["mrr"] for c in cases_metrics])["mean"]

    zero_cases = [c for c in cases_metrics if c["precision"] == 0.0]
    fallback_count = sum(1 for c in cases_metrics if c["fallback_used"])

    return {
        "method": method,
        "top_k": top_k,
        "total_cases": len(cases_metrics),
        "overall": {
            "precision": overall_p,
            "recall": overall_r,
            "mrr": overall_m,
        },
        "zero_case_count": len(zero_cases),
        "fallback_count": fallback_count,
        "by_difficulty": ev._group_by(cases_metrics, "difficulty"),
        "by_category": ev._group_by(cases_metrics, "category"),
        "cases": cases_metrics,
        "available_skills": available_skills,
    }


def _print_three_way_comparison(
    tfidf_report: Dict,
    vector_report: Dict,
    rrf_report: Dict,
) -> None:
    """打印 TF-IDF / Vector / RRF 三方对比表"""
    sep = "═" * 92
    print()
    print(sep)
    print("  TF-IDF vs Vector vs RRF 三方检索对比报告")
    print(sep)
    print()
    print(f"  黄金集用例数: {tfidf_report['total_cases']}")
    print(f"  可用技能数  : {len(tfidf_report['available_skills'])} "
          f"({', '.join(tfidf_report['available_skills'])})")
    print(f"  Top-K       : {tfidf_report['top_k']}")
    print()

    # ── 整体指标对比 ──
    print("【整体指标对比】")
    print("  " + "-" * 88)
    header = (f"  {'指标':<10} {'TF-IDF':>12} {'Vector':>12} {'RRF':>12} "
              f"{'RRF-T':>12} {'RRF-V':>12}")
    print(header)
    print("  " + "-" * 88)
    for metric_name in ["precision", "recall", "mrr"]:
        t = tfidf_report["overall"][metric_name]
        v = vector_report["overall"][metric_name]
        r = rrf_report["overall"][metric_name]
        delta_t = r - t
        delta_v = r - v
        print(f"  {metric_name.upper():<10} {t:>12.4f} {v:>12.4f} {r:>12.4f} "
              f"{delta_t:>+12.4f} {delta_v:>+12.4f}")
    print("  " + "-" * 88)
    print(f"  {'0 分用例':<10} {tfidf_report['zero_case_count']:>12} "
          f"{vector_report['zero_case_count']:>12} "
          f"{rrf_report['zero_case_count']:>12}")
    print(f"  {'fallback':<10} {tfidf_report['fallback_count']:>12} "
          f"{vector_report['fallback_count']:>12} "
          f"{rrf_report['fallback_count']:>12}")
    print()

    # ── 按难度分组对比 ──
    print("【按难度分组对比（Precision）】")
    print("  " + "-" * 88)
    print(f"  {'难度':<10} {'TF-IDF':>12} {'Vector':>12} {'RRF':>12} "
          f"{'RRF-T':>12} {'RRF-V':>12}")
    print("  " + "-" * 88)
    for diff in ["easy", "medium", "hard", "tricky"]:
        t = tfidf_report["by_difficulty"].get(diff, {}).get("precision", 0)
        v = vector_report["by_difficulty"].get(diff, {}).get("precision", 0)
        r = rrf_report["by_difficulty"].get(diff, {}).get("precision", 0)
        delta_t = r - t
        delta_v = r - v
        marker = ""
        if delta_t > 0.001 and delta_v > 0.001:
            marker = " ← win-win"
        elif delta_t > 0.001 or delta_v > 0.001:
            marker = " ← improved"
        print(f"  {diff:<10} {t:>12.4f} {v:>12.4f} {r:>12.4f} "
              f"{delta_t:>+12.4f} {delta_v:>+12.4f}{marker}")
    print()

    # ── 按类别分组对比 ──
    print("【按类别分组对比（Precision，仅显示有变化的）】")
    print("  " + "-" * 88)
    print(f"  {'类别':<18} {'TF-IDF':>12} {'Vector':>12} {'RRF':>12} "
          f"{'RRF-T':>12} {'RRF-V':>12}")
    print("  " + "-" * 88)
    all_cats = sorted(set(tfidf_report["by_category"].keys()) |
                      set(vector_report["by_category"].keys()) |
                      set(rrf_report["by_category"].keys()))
    for cat in all_cats:
        t = tfidf_report["by_category"].get(cat, {}).get("precision", 0)
        v = vector_report["by_category"].get(cat, {}).get("precision", 0)
        r = rrf_report["by_category"].get(cat, {}).get("precision", 0)
        delta_t = r - t
        delta_v = r - v
        marker = ""
        if abs(delta_t) > 0.001 or abs(delta_v) > 0.001:
            marker = " ←"
        print(f"  {cat:<18} {t:>12.4f} {v:>12.4f} {r:>12.4f} "
              f"{delta_t:>+12.4f} {delta_v:>+12.4f}{marker}")
    print()

    # ── 逐用例变化（RRF 视角）──
    print("【逐用例变化（对比 RRF vs TF-IDF，仅显示有变化的用例）】")
    print("  " + "-" * 88)
    changed = 0
    improved = 0
    regressed = 0
    for tc, rc in zip(tfidf_report["cases"], rrf_report["cases"]):
        if tc["precision"] != rc["precision"] or tc["actual"] != rc["actual"]:
            changed += 1
            delta = rc["precision"] - tc["precision"]
            if delta > 0:
                improved += 1
            elif delta < 0:
                regressed += 1
            print(f"  {tc['case_id']:<10} [{tc['difficulty']:<7}/{tc['category']:<14}] "
                  f"P: {tc['precision']:.2f} → {rc['precision']:.2f} (Δ={delta:+.2f})")
            print(f"    query    : {tc['query']}")
            print(f"    expected : {tc['expected']}")
            print(f"    tfidf    : {tc['actual']}")
            print(f"    rrf      : {rc['actual']}")
            # 透出 RRF 排名分项，便于诊断
            if rc["actual_breakdowns"]:
                for sid, bd in zip(rc["actual"], rc["actual_breakdowns"]):
                    if bd:
                        print(f"      {sid:<15} "
                              f"tfidf_rank={bd.get('tfidf_rank')} "
                              f"vector_rank={bd.get('vector_rank')} "
                              f"rrf={bd.get('rrf_normalized', 0):.3f}")
            if rc["fallback_used"]:
                print(f"    ⚠ rrf fallback to tfidf (vector unavailable)")
            print()
    if changed == 0:
        print("  (无变化)")
    else:
        print(f"  共 {changed} 个用例发生变化："
              f"{improved} 改善, {regressed} 退化, {changed - improved - regressed} 平移")
    print()

    # ── CI 守卫判断 ──
    print(sep)
    threshold = 0.6
    rrf_p = rrf_report["overall"]["precision"]
    tfidf_p = tfidf_report["overall"]["precision"]
    vector_p = vector_report["overall"]["precision"]

    print(f"  RRF Precision@{rrf_report['top_k']} = {rrf_p:.4f}")
    print(f"  vs TF-IDF baseline: {rrf_p - tfidf_p:+.4f} "
          f"({(rrf_p - tfidf_p) / tfidf_p * 100:+.1f}%)" if tfidf_p > 0 else "")
    print(f"  vs Vector single:  {rrf_p - vector_p:+.4f} "
          f"({(rrf_p - vector_p) / vector_p * 100:+.1f}%)" if vector_p > 0 else "")

    if rrf_p >= threshold:
        print(f"  ✅ RRF Precision@{rrf_report['top_k']} >= {threshold} 阈值，CI 守卫通过")
    else:
        print(f"  ❌ RRF Precision@{rrf_report['top_k']} < {threshold} 阈值，未达预期")
        if rrf_p > tfidf_p:
            print(f"     但相比 TF-IDF 基线提升 {rrf_p - tfidf_p:+.4f}，方向正确")
        if rrf_p > vector_p:
            print(f"     但相比 Vector 单路提升 {rrf_p - vector_p:+.4f}，融合有效")
    print(sep)


def _print_four_way_comparison(
    tfidf_report: Dict,
    vector_report: Dict,
    rrf_report: Dict,
    rrf_rerank_report: Dict,
) -> None:
    """打印 TF-IDF / Vector / RRF / RRF+Reranker 四方对比表

    【简易】复用三方对比结构，增加 RRF+Reranker 列与对应增量
    【变易】允许 reranker 降级场景（retrieval_method 显示降级信息）
    """
    sep = "═" * 108
    print()
    print(sep)
    print("  TF-IDF vs Vector vs RRF vs RRF+Reranker 四方检索对比报告")
    print(sep)
    print()
    print(f"  黄金集用例数: {tfidf_report['total_cases']}")
    print(f"  可用技能数  : {len(tfidf_report['available_skills'])} "
          f"({', '.join(tfidf_report['available_skills'])})")
    print(f"  Top-K       : {tfidf_report['top_k']}")
    print()

    # 检测 reranker 是否降级（retrieval_method 不是 rrf_rerank 表示降级）
    rerank_degraded = any(
        c.get("retrieval_method") != "rrf_rerank"
        for c in rrf_rerank_report["cases"]
    )
    if rerank_degraded:
        print("  ⚠ RRF+Reranker 出现降级（Cross-Encoder 模型不可用，退化为 RRF）")
        print()
    rerank_methods = {c.get("retrieval_method", "?") for c in rrf_rerank_report["cases"]}
    print(f"  RRF+Reranker 实际方法: {sorted(rerank_methods)}")
    print()

    # ── 整体指标对比 ──
    print("【整体指标对比】")
    print("  " + "-" * 104)
    header = (f"  {'指标':<10} {'TF-IDF':>12} {'Vector':>12} {'RRF':>12} "
              f"{'RRF+Rk':>12} {'Rk-T':>10} {'Rk-V':>10} {'Rk-RRF':>10}")
    print(header)
    print("  " + "-" * 104)
    for metric_name in ["precision", "recall", "mrr"]:
        t = tfidf_report["overall"][metric_name]
        v = vector_report["overall"][metric_name]
        r = rrf_report["overall"][metric_name]
        k = rrf_rerank_report["overall"][metric_name]
        d_t = k - t
        d_v = k - v
        d_rrf = k - r
        print(f"  {metric_name.upper():<10} {t:>12.4f} {v:>12.4f} {r:>12.4f} "
              f"{k:>12.4f} {d_t:>+10.4f} {d_v:>+10.4f} {d_rrf:>+10.4f}")
    print("  " + "-" * 104)
    print(f"  {'0 分用例':<10} {tfidf_report['zero_case_count']:>12} "
          f"{vector_report['zero_case_count']:>12} "
          f"{rrf_report['zero_case_count']:>12} "
          f"{rrf_rerank_report['zero_case_count']:>12}")
    print(f"  {'fallback':<10} {tfidf_report['fallback_count']:>12} "
          f"{vector_report['fallback_count']:>12} "
          f"{rrf_report['fallback_count']:>12} "
          f"{rrf_rerank_report['fallback_count']:>12}")
    print()

    # ── 按难度分组对比 ──
    print("【按难度分组对比（Precision）】")
    print("  " + "-" * 104)
    print(f"  {'难度':<10} {'TF-IDF':>12} {'Vector':>12} {'RRF':>12} "
          f"{'RRF+Rk':>12} {'Rk-RRF':>10}")
    print("  " + "-" * 104)
    for diff in ["easy", "medium", "hard", "tricky"]:
        t = tfidf_report["by_difficulty"].get(diff, {}).get("precision", 0)
        v = vector_report["by_difficulty"].get(diff, {}).get("precision", 0)
        r = rrf_report["by_difficulty"].get(diff, {}).get("precision", 0)
        k = rrf_rerank_report["by_difficulty"].get(diff, {}).get("precision", 0)
        delta = k - r
        marker = ""
        if delta > 0.001:
            marker = " ← rerank 提升"
        elif delta < -0.001:
            marker = " ← rerank 退化"
        print(f"  {diff:<10} {t:>12.4f} {v:>12.4f} {r:>12.4f} "
              f"{k:>12.4f} {delta:>+10.4f}{marker}")
    print()

    # ── 按类别分组对比 ──
    print("【按类别分组对比（Precision，仅显示 RRF→Rerank 有变化的）】")
    print("  " + "-" * 104)
    print(f"  {'类别':<18} {'TF-IDF':>12} {'Vector':>12} {'RRF':>12} "
          f"{'RRF+Rk':>12} {'Rk-RRF':>10}")
    print("  " + "-" * 104)
    all_cats = sorted(set(tfidf_report["by_category"].keys()) |
                      set(vector_report["by_category"].keys()) |
                      set(rrf_report["by_category"].keys()) |
                      set(rrf_rerank_report["by_category"].keys()))
    for cat in all_cats:
        t = tfidf_report["by_category"].get(cat, {}).get("precision", 0)
        v = vector_report["by_category"].get(cat, {}).get("precision", 0)
        r = rrf_report["by_category"].get(cat, {}).get("precision", 0)
        k = rrf_rerank_report["by_category"].get(cat, {}).get("precision", 0)
        delta = k - r
        marker = ""
        if abs(delta) > 0.001:
            marker = " ←"
        print(f"  {cat:<18} {t:>12.4f} {v:>12.4f} {r:>12.4f} "
              f"{k:>12.4f} {delta:>+10.4f}{marker}")
    print()

    # ── 逐用例变化（RRF+Reranker vs RRF，仅显示有变化的用例）──
    print("【逐用例变化（对比 RRF+Reranker vs RRF，仅显示有变化的用例）】")
    print("  " + "-" * 104)
    changed = 0
    improved = 0
    regressed = 0
    for rc, kc in zip(rrf_report["cases"], rrf_rerank_report["cases"]):
        if rc["precision"] != kc["precision"] or rc["actual"] != kc["actual"]:
            changed += 1
            delta = kc["precision"] - rc["precision"]
            if delta > 0:
                improved += 1
            elif delta < 0:
                regressed += 1
            print(f"  {rc['case_id']:<10} [{rc['difficulty']:<7}/{rc['category']:<14}] "
                  f"P: {rc['precision']:.2f} → {kc['precision']:.2f} (Δ={delta:+.2f})")
            print(f"    query    : {rc['query']}")
            print(f"    expected : {rc['expected']}")
            print(f"    rrf      : {[(s, round(sc, 3)) for s, sc in zip(rc['actual'], rc['actual_scores'])]}")
            print(f"    rrf+rk   : {[(s, round(sc, 3)) for s, sc in zip(kc['actual'], kc['actual_scores'])]}")
            # 透出 rerank_score 分项（若存在）
            if kc["actual_breakdowns"]:
                for sid, bd in zip(kc["actual"], kc["actual_breakdowns"]):
                    if bd and "rerank_score" in bd:
                        print(f"      {sid:<15} rrf={bd.get('rrf_normalized', 0):.3f} "
                              f"rerank={bd.get('rerank_score', 0):+.3f} "
                              f"orig_rank={bd.get('original_rank')}")
            if kc["fallback_used"]:
                print(f"    ⚠ rrf+rk fallback (reranker unavailable)")
            print()
    if changed == 0:
        print("  (无变化 — Reranker 与 RRF 排序结果一致)")
    else:
        print(f"  共 {changed} 个用例发生变化："
              f"{improved} 改善, {regressed} 退化, {changed - improved - regressed} 平移")
    print()

    # ── CI 守卫判断 ──
    print(sep)
    threshold = 0.6
    k_p = rrf_rerank_report["overall"]["precision"]
    t_p = tfidf_report["overall"]["precision"]
    v_p = vector_report["overall"]["precision"]
    r_p = rrf_report["overall"]["precision"]

    print(f"  RRF+Reranker Precision@{rrf_rerank_report['top_k']} = {k_p:.4f}")
    print(f"  vs TF-IDF baseline : {k_p - t_p:+.4f} "
          f"({(k_p - t_p) / t_p * 100:+.1f}%)" if t_p > 0 else "")
    print(f"  vs Vector single   : {k_p - v_p:+.4f} "
          f"({(k_p - v_p) / v_p * 100:+.1f}%)" if v_p > 0 else "")
    print(f"  vs RRF fused       : {k_p - r_p:+.4f} "
          f"({(k_p - r_p) / r_p * 100:+.1f}%)" if r_p > 0 else "")

    if k_p >= threshold:
        print(f"  ✅ RRF+Reranker Precision@{rrf_rerank_report['top_k']} >= {threshold} 阈值，CI 守卫通过")
    else:
        print(f"  ❌ RRF+Reranker Precision@{rrf_rerank_report['top_k']} < {threshold} 阈值，未达预期")
        if k_p > r_p:
            print(f"     但相比 RRF 提升 {k_p - r_p:+.4f}，Cross-Encoder 精排有效")
        if k_p > t_p:
            print(f"     但相比 TF-IDF 基线提升 {k_p - t_p:+.4f}，方向正确")
    print(sep)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="RRF 融合检索 vs TF-IDF vs Vector 三方对比评估",
    )
    parser.add_argument(
        "--golden-set", type=Path,
        default=ev.DEFAULT_GOLDEN_SET,
        help="黄金集 JSON 路径",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument(
        "--only", choices=["tfidf", "vector", "rrf", "rrf_rerank"], default=None,
        help="只评估单一方法（默认四方对比）",
    )
    parser.add_argument("--verbose", action="store_true", help="打印每个用例的召回")
    parser.add_argument("--output", type=Path, default=None, help="保存对比报告到 JSON")
    args = parser.parse_args(argv)

    print("=" * 92)
    print(f"RRF 融合检索 四方评估  (K={args.top_k})")
    print("=" * 92)

    tfidf_report = None
    vector_report = None
    rrf_report = None
    rrf_rerank_report = None

    if args.only in (None, "tfidf"):
        print(f"\n>>> 评估 TF-IDF 基线...")
        tfidf_report = _evaluate_method(
            "tfidf", args.golden_set, args.top_k, verbose=args.verbose,
        )
        print(f"  Precision@{args.top_k} = {tfidf_report['overall']['precision']:.4f}")
        print(f"  Recall@{args.top_k}    = {tfidf_report['overall']['recall']:.4f}")
        print(f"  MRR                = {tfidf_report['overall']['mrr']:.4f}")
        print(f"  0 分用例数         = {tfidf_report['zero_case_count']}")

    if args.only in (None, "vector"):
        print(f"\n>>> 评估 Vector 单路检索（首次构建索引可能需要数秒）...")
        vector_report = _evaluate_method(
            "vector", args.golden_set, args.top_k, verbose=args.verbose,
        )
        print(f"  Precision@{args.top_k} = {vector_report['overall']['precision']:.4f}")
        print(f"  Recall@{args.top_k}    = {vector_report['overall']['recall']:.4f}")
        print(f"  MRR                = {vector_report['overall']['mrr']:.4f}")
        print(f"  0 分用例数         = {vector_report['zero_case_count']}")
        print(f"  fallback 次数      = {vector_report['fallback_count']}")

    if args.only in (None, "rrf"):
        print(f"\n>>> 评估 RRF 融合检索（TF-IDF + 向量双路融合）...")
        rrf_report = _evaluate_method(
            "rrf", args.golden_set, args.top_k, verbose=args.verbose,
        )
        print(f"  Precision@{args.top_k} = {rrf_report['overall']['precision']:.4f}")
        print(f"  Recall@{args.top_k}    = {rrf_report['overall']['recall']:.4f}")
        print(f"  MRR                = {rrf_report['overall']['mrr']:.4f}")
        print(f"  0 分用例数         = {rrf_report['zero_case_count']}")
        print(f"  fallback 次数      = {rrf_report['fallback_count']}")

    if args.only in (None, "rrf_rerank"):
        print(f"\n>>> 评估 RRF+Reranker 检索（首次加载 BGE-reranker 可能需要数分钟）...")
        rrf_rerank_report = _evaluate_method(
            "rrf_rerank", args.golden_set, args.top_k, verbose=args.verbose,
        )
        print(f"  Precision@{args.top_k} = {rrf_rerank_report['overall']['precision']:.4f}")
        print(f"  Recall@{args.top_k}    = {rrf_rerank_report['overall']['recall']:.4f}")
        print(f"  MRR                = {rrf_rerank_report['overall']['mrr']:.4f}")
        print(f"  0 分用例数         = {rrf_rerank_report['zero_case_count']}")
        print(f"  fallback 次数      = {rrf_rerank_report['fallback_count']}")

    # 四方对比打印
    if tfidf_report and vector_report and rrf_report and rrf_rerank_report:
        _print_four_way_comparison(
            tfidf_report, vector_report, rrf_report, rrf_rerank_report,
        )
    elif tfidf_report and vector_report and rrf_report:
        _print_three_way_comparison(tfidf_report, vector_report, rrf_report)

    if args.output:
        report = {
            "tfidf": tfidf_report,
            "vector": vector_report,
            "rrf": rrf_report,
            "rrf_rerank": rrf_rerank_report,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"\n对比报告已保存: {args.output}")

    # 退出码：RRF+Reranker 未达阈值且未提升时返回 1
    final_report = rrf_rerank_report or rrf_report
    if final_report:
        threshold = 0.6
        if final_report["overall"]["precision"] < threshold:
            if tfidf_report:
                delta = (rrf_report["overall"]["precision"] -
                         tfidf_report["overall"]["precision"])
                if delta <= 0:
                    return 1
            else:
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
