"""负样本拒绝能力评估脚本 — 验证 Reranker 对跨领域负样本的拒绝能力

策略:
1. 在原黄金集（45 用例）上跑 RRF / RRF+Reranker，作为基线
2. 在扩展负样本集（25 用例）上单独跑，统计拒绝率
3. 合并 70 用例，统计整体 P@3 / Recall / 拒绝率

【不易】不改原黄金集，扩展集独立文件
【变易】支持多种 reranker 模型对比（v2-m3 / base）
【简易】复用 eval_rrf_fusion 的 _evaluate_method 逻辑
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.skills_mgmt.loader import SkillLoader  # noqa: E402
import eval_skill_retrieval as ev  # noqa: E402


def _evaluate_negatives(
    negative_set_path: Path,
    top_k: int,
    *,
    use_reranker: bool,
    rerank_min_score: float = 0.001,
    verbose: bool = True,
) -> Dict[str, Any]:
    """在负样本集上评估拒绝能力

    Returns:
        {
            "total": N,
            "correctly_rejected": N,  # actual 为空 []
            "wrongly_recalled": N,    # actual 非空（误召回）
            "rejection_rate": float,
            "cases": [...],
        }
    """
    with negative_set_path.open("r", encoding="utf-8") as f:
        neg_set = json.load(f)

    loader = SkillLoader()

    cases_result = []
    correctly_rejected = 0
    wrongly_recalled = 0

    for case in neg_set["test_cases"]:
        query = case["query"]
        result = loader.match(
            query, top_k=top_k, enabled_only=True,
            use_vector=True, fusion_mode="rrf",
            use_reranker=use_reranker,
        )
        actual_ids = [m.skill_id for m in result.matches]
        actual_scores = [m.score for m in result.matches]
        actual_breakdowns = [m.score_breakdown for m in result.matches]

        # 负样本：actual 为空才是正确拒绝
        is_correctly_rejected = len(actual_ids) == 0
        if is_correctly_rejected:
            correctly_rejected += 1
        else:
            wrongly_recalled += 1

        case_detail = {
            "case_id": case["case_id"],
            "query": query,
            "category": case.get("category", "unknown"),
            "difficulty": case.get("difficulty", "unknown"),
            "actual": actual_ids,
            "actual_scores": actual_scores,
            "actual_breakdowns": actual_breakdowns,
            "correctly_rejected": is_correctly_rejected,
            "retrieval_method": result.retrieval_method,
            "fallback_used": result.fallback_used,
        }
        cases_result.append(case_detail)

        if verbose:
            marker = "✓" if is_correctly_rejected else "✗"
            status = "rejected" if is_correctly_rejected else "RECALLED"
            print(f"  {marker} {case['case_id']:<10} [{case['category']:<22}] "
                  f"{status:<10}  {query[:30]}")
            if not is_correctly_rejected:
                for sid, sc, bd in zip(actual_ids, actual_scores, actual_breakdowns):
                    rerank_sc = bd.get("rerank_score", 0) if bd else 0
                    print(f"      {sid:<22} rrf={sc:.3f} rerank={rerank_sc:+.4f}")

    total = len(neg_set["test_cases"])
    return {
        "total": total,
        "correctly_rejected": correctly_rejected,
        "wrongly_recalled": wrongly_recalled,
        "rejection_rate": correctly_rejected / total if total > 0 else 0,
        "cases": cases_result,
    }


def _evaluate_positives(
    golden_set_path: Path,
    top_k: int,
    *,
    use_reranker: bool,
    verbose: bool = False,
) -> Dict[str, Any]:
    """在正样本集上评估 P@3 / Recall / MRR（基线对比）"""
    import eval_rrf_fusion as evf
    method = "rrf_rerank" if use_reranker else "rrf"
    return evf._evaluate_method(method, golden_set_path, top_k, verbose=verbose)


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(
        description="负样本拒绝能力评估 — 验证 Reranker 的拒绝能力",
    )
    parser.add_argument(
        "--golden-set", type=Path,
        default=ev.DEFAULT_GOLDEN_SET,
        help="正样本黄金集 JSON 路径",
    )
    parser.add_argument(
        "--negative-set", type=Path,
        default=Path("tests/eval/negative_samples_extended.json"),
        help="扩展负样本集 JSON 路径",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument(
        "--rerank-min-score", type=float, default=0.001,
        help="rerank_score 阈值（默认 0.001，拒绝负样本保留真匹配）",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    # 设置环境变量
    import os
    os.environ["SKILL_RERANK_MIN_SCORE"] = str(args.rerank_min_score)
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

    sep = "═" * 100
    print(sep)
    print(f"  负样本拒绝能力评估  (top_k={args.top_k}, threshold={args.rerank_min_score})")
    print(sep)

    # 1. 正样本基线
    print(f"\n>>> [1/4] 正样本黄金集评估 (RRF 基线, 无 reranker)...")
    pos_rrf = _evaluate_positives(
        args.golden_set, args.top_k, use_reranker=False, verbose=False,
    )
    print(f"  P@3={pos_rrf['overall']['precision']:.4f} "
          f"R@3={pos_rrf['overall']['recall']:.4f} "
          f"MRR={pos_rrf['overall']['mrr']:.4f}")

    print(f"\n>>> [2/4] 正样本黄金集评估 (RRF + Reranker, threshold={args.rerank_min_score})...")
    pos_rerank = _evaluate_positives(
        args.golden_set, args.top_k, use_reranker=True, verbose=False,
    )
    print(f"  P@3={pos_rerank['overall']['precision']:.4f} "
          f"R@3={pos_rerank['overall']['recall']:.4f} "
          f"MRR={pos_rerank['overall']['mrr']:.4f}")

    # 2. 负样本拒绝能力
    print(f"\n>>> [3/4] 负样本拒绝评估 (RRF 基线, 无 reranker)...")
    neg_rrf = _evaluate_negatives(
        args.negative_set, args.top_k,
        use_reranker=False, verbose=args.verbose,
    )
    print(f"\n  RRF 拒绝率: {neg_rrf['rejection_rate']:.2%} "
          f"({neg_rrf['correctly_rejected']}/{neg_rrf['total']})")
    print(f"  误召回: {neg_rrf['wrongly_recalled']} 个用例")

    print(f"\n>>> [4/4] 负样本拒绝评估 (RRF + Reranker, threshold={args.rerank_min_score})...")
    neg_rerank = _evaluate_negatives(
        args.negative_set, args.top_k,
        use_reranker=True, rerank_min_score=args.rerank_min_score,
        verbose=args.verbose,
    )
    print(f"\n  RRF+Reranker 拒绝率: {neg_rerank['rejection_rate']:.2%} "
          f"({neg_rerank['correctly_rejected']}/{neg_rerank['total']})")
    print(f"  误召回: {neg_rerank['wrongly_recalled']} 个用例")

    # 3. 汇总
    print()
    print(sep)
    print("  综合对比")
    print(sep)
    print(f"  {'方法':<20} {'正样本P@3':>12} {'正样本R@3':>12} "
          f"{'正样本MRR':>12} {'负样本拒绝率':>14}")
    print("  " + "-" * 80)
    print(f"  {'RRF':<20} "
          f"{pos_rrf['overall']['precision']:>12.4f} "
          f"{pos_rrf['overall']['recall']:>12.4f} "
          f"{pos_rrf['overall']['mrr']:>12.4f} "
          f"{neg_rrf['rejection_rate']:>14.2%}")
    print(f"  {'RRF+Reranker':<20} "
          f"{pos_rerank['overall']['precision']:>12.4f} "
          f"{pos_rerank['overall']['recall']:>12.4f} "
          f"{pos_rerank['overall']['mrr']:>12.4f} "
          f"{neg_rerank['rejection_rate']:>14.2%}")

    delta_p = pos_rerank['overall']['precision'] - pos_rrf['overall']['precision']
    delta_r = neg_rerank['rejection_rate'] - neg_rrf['rejection_rate']
    print(f"\n  Reranker 增益: P@3 {delta_p:+.4f}, 拒绝率 {delta_r:+.2%}")

    # 4. 按类别分组拒绝率
    print()
    print("  按类别分组拒绝率（RRF+Reranker）")
    print("  " + "-" * 60)
    from collections import defaultdict
    by_cat = defaultdict(lambda: {"total": 0, "rejected": 0})
    for c in neg_rerank["cases"]:
        cat = c["category"]
        by_cat[cat]["total"] += 1
        if c["correctly_rejected"]:
            by_cat[cat]["rejected"] += 1
    for cat in sorted(by_cat.keys()):
        s = by_cat[cat]
        rate = s["rejected"] / s["total"] if s["total"] > 0 else 0
        print(f"  {cat:<28} {s['rejected']}/{s['total']}  {rate:.2%}")

    if args.output:
        report = {
            "config": {
                "top_k": args.top_k,
                "rerank_min_score": args.rerank_min_score,
                "golden_set": str(args.golden_set),
                "negative_set": str(args.negative_set),
            },
            "positives_rrf": pos_rrf,
            "positives_rerank": pos_rerank,
            "negatives_rrf": neg_rrf,
            "negatives_rerank": neg_rerank,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"\n报告已保存: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
