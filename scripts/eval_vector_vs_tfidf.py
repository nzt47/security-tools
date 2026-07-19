"""向量检索评估脚本 — 对比 TF-IDF 基线与向量检索的 Precision@K

用法:
    # 默认对比（推荐）
    python scripts/eval_vector_vs_tfidf.py

    # 只跑向量检索评估
    python scripts/eval_vector_vs_tfidf.py --only vector

    # 自定义 K 值
    python scripts/eval_vector_vs_tfidf.py --top-k 5

    # 详细模式：打印每个用例的实际召回
    python scripts/eval_vector_vs_tfidf.py --verbose

    # 保存对比报告到文件
    python scripts/eval_vector_vs_tfidf.py --output tests/eval/vector_vs_tfidf_report.json

【不易】不改黄金集，不改技能定义；脚本独立运行
【简易】复用 eval_skill_retrieval.py 的指标计算逻辑
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
        method: "tfidf" 或 "vector"
        golden_set_path: 黄金集路径
        top_k: 评估 K 值
        verbose: 是否打印每个用例的召回结果
    """
    # 加载黄金集
    with golden_set_path.open("r", encoding="utf-8") as f:
        golden = json.load(f)

    # 独立 loader 实例
    loader = SkillLoader()
    available_skills = sorted(loader.fs.load_metadata_index().keys())

    use_vector = (method == "vector")
    cases_metrics: List[Dict[str, Any]] = []

    for case in golden["test_cases"]:
        query = case["query"]
        expected = case.get("expected_skill_ids", [])

        # 调用 match（vector 模式开启 use_vector）
        result = loader.match(
            query, top_k=top_k, enabled_only=True, use_vector=use_vector,
        )
        actual_ids = [m.skill_id for m in result.matches]
        actual_scores = [m.score for m in result.matches]

        precision, recall, mrr = ev._per_case_metrics(actual_ids, expected, top_k)
        fallback = result.fallback_used

        case_detail = {
            "case_id": case["case_id"],
            "query": query,
            "expected": expected,
            "actual": actual_ids,
            "actual_scores": actual_scores,
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


def _print_comparison(tfidf_report: Dict, vector_report: Dict) -> None:
    """打印 TF-IDF vs Vector 对比表"""
    sep = "═" * 80
    print()
    print(sep)
    print("  TF-IDF vs Vector 检索对比报告")
    print(sep)
    print()
    print(f"  黄金集用例数: {tfidf_report['total_cases']}")
    print(f"  可用技能数  : {len(tfidf_report['available_skills'])} "
          f"({', '.join(tfidf_report['available_skills'])})")
    print(f"  Top-K       : {tfidf_report['top_k']}")
    print()
    print("【整体指标对比】")
    print("  " + "-" * 70)
    print(f"  {'指标':<15} {'TF-IDF':>12} {'Vector':>12} {'Delta':>12} {'变化':>10}")
    print("  " + "-" * 70)
    for metric_name in ["precision", "recall", "mrr"]:
        t = tfidf_report["overall"][metric_name]
        v = vector_report["overall"][metric_name]
        delta = v - t
        change_pct = (delta / t * 100) if t > 0 else float("inf")
        change_str = f"{change_pct:+.1f}%" if t > 0 else "N/A"
        print(f"  {metric_name.upper():<15} {t:>12.4f} {v:>12.4f} "
              f"{delta:>+12.4f} {change_str:>10}")
    print("  " + "-" * 70)
    print(f"  {'0 分用例数':<15} {tfidf_report['zero_case_count']:>12} "
          f"{vector_report['zero_case_count']:>12} "
          f"{vector_report['zero_case_count'] - tfidf_report['zero_case_count']:>+12}")
    print(f"  {'fallback 次数':<13} {tfidf_report['fallback_count']:>12} "
          f"{vector_report['fallback_count']:>12}")
    print()
    print("【按难度分组对比】")
    print("  " + "-" * 70)
    print(f"  {'难度':<10} {'TF-IDF P':>12} {'Vector P':>12} {'Delta':>12}")
    print("  " + "-" * 70)
    for diff in ["easy", "medium", "hard", "tricky"]:
        t = tfidf_report["by_difficulty"].get(diff, {}).get("precision", 0)
        v = vector_report["by_difficulty"].get(diff, {}).get("precision", 0)
        delta = v - t
        print(f"  {diff:<10} {t:>12.4f} {v:>12.4f} {delta:>+12.4f}")
    print()
    print("【按类别分组对比（仅显示有变化的）】")
    print("  " + "-" * 70)
    print(f"  {'类别':<18} {'TF-IDF P':>12} {'Vector P':>12} {'Delta':>12}")
    print("  " + "-" * 70)
    all_cats = sorted(set(tfidf_report["by_category"].keys()) |
                      set(vector_report["by_category"].keys()))
    for cat in all_cats:
        t = tfidf_report["by_category"].get(cat, {}).get("precision", 0)
        v = vector_report["by_category"].get(cat, {}).get("precision", 0)
        delta = v - t
        marker = " ←" if abs(delta) > 0.001 else ""
        print(f"  {cat:<18} {t:>12.4f} {v:>12.4f} {delta:>+12.4f}{marker}")
    print()
    print("【逐用例变化（仅显示有变化的用例）】")
    print("  " + "-" * 70)
    changed = 0
    for tc, vc in zip(tfidf_report["cases"], vector_report["cases"]):
        if tc["precision"] != vc["precision"] or tc["actual"] != vc["actual"]:
            changed += 1
            delta = vc["precision"] - tc["precision"]
            print(f"  {tc['case_id']:<10} [{tc['difficulty']:<7}/{tc['category']:<14}] "
                  f"P: {tc['precision']:.2f} → {vc['precision']:.2f} (Δ={delta:+.2f})")
            print(f"    query    : {tc['query']}")
            print(f"    expected : {tc['expected']}")
            print(f"    tfidf    : {tc['actual']}")
            print(f"    vector   : {vc['actual']}")
            if vc["fallback_used"]:
                print(f"    ⚠ vector fallback to tfidf")
            print()
    if changed == 0:
        print("  (无变化)")
    else:
        print(f"  共 {changed} 个用例发生变化")

    print()
    print(sep)
    # CI 守卫判断
    threshold = 0.6
    if vector_report["overall"]["precision"] >= threshold:
        print(f"  ✅ 向量检索 Precision@{vector_report['top_k']} = "
              f"{vector_report['overall']['precision']:.4f} >= {threshold} 阈值，CI 守卫通过")
    else:
        print(f"  ❌ 向量检索 Precision@{vector_report['top_k']} = "
              f"{vector_report['overall']['precision']:.4f} < {threshold} 阈值，未达预期")
        delta = vector_report["overall"]["precision"] - tfidf_report["overall"]["precision"]
        if delta > 0:
            print(f"     但相比 TF-IDF 基线提升 {delta:+.4f}，方向正确")
    print(sep)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="向量检索 vs TF-IDF 对比评估",
    )
    parser.add_argument(
        "--golden-set", type=Path,
        default=ev.DEFAULT_GOLDEN_SET,
        help="黄金集 JSON 路径",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument(
        "--only", choices=["tfidf", "vector"], default=None,
        help="只评估单一方法（默认两者对比）",
    )
    parser.add_argument("--verbose", action="store_true", help="打印每个用例的召回")
    parser.add_argument("--output", type=Path, default=None, help="保存对比报告到 JSON")
    args = parser.parse_args(argv)

    print("=" * 80)
    print(f"向量检索 vs TF-IDF 评估  (K={args.top_k})")
    print("=" * 80)

    # 评估 TF-IDF
    tfidf_report = None
    if args.only in (None, "tfidf"):
        print(f"\n>>> 评估 TF-IDF 基线...")
        tfidf_report = _evaluate_method(
            "tfidf", args.golden_set, args.top_k, verbose=args.verbose,
        )
        print(f"  Precision@{args.top_k} = {tfidf_report['overall']['precision']:.4f}")
        print(f"  Recall@{args.top_k}    = {tfidf_report['overall']['recall']:.4f}")
        print(f"  MRR                = {tfidf_report['overall']['mrr']:.4f}")
        print(f"  0 分用例数         = {tfidf_report['zero_case_count']}")

    # 评估向量检索
    vector_report = None
    if args.only in (None, "vector"):
        print(f"\n>>> 评估 Vector 检索（首次构建索引可能需要数秒）...")
        vector_report = _evaluate_method(
            "vector", args.golden_set, args.top_k, verbose=args.verbose,
        )
        print(f"  Precision@{args.top_k} = {vector_report['overall']['precision']:.4f}")
        print(f"  Recall@{args.top_k}    = {vector_report['overall']['recall']:.4f}")
        print(f"  MRR                = {vector_report['overall']['mrr']:.4f}")
        print(f"  0 分用例数         = {vector_report['zero_case_count']}")
        print(f"  fallback 次数      = {vector_report['fallback_count']}")

    # 对比
    if tfidf_report and vector_report:
        _print_comparison(tfidf_report, vector_report)

    # 保存报告
    if args.output:
        report = {
            "tfidf": tfidf_report,
            "vector": vector_report,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"\n对比报告已保存: {args.output}")

    # 退出码：向量检索未达阈值且未提升时返回 1
    if vector_report:
        threshold = 0.6
        if vector_report["overall"]["precision"] < threshold:
            if tfidf_report:
                delta = (vector_report["overall"]["precision"] -
                         tfidf_report["overall"]["precision"])
                if delta <= 0:
                    return 1
            else:
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
