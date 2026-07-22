#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""技能检索评估脚本 — 基于 Precision@K / Recall@K / MRR 评估 SkillLoader.match

用法:
    python scripts/eval_skill_retrieval.py
    python scripts/eval_skill_retrieval.py --top-k 5
    python scripts/eval_skill_retrieval.py --report-format json --output report.json
    python scripts/eval_skill_retrieval.py --golden-set tests/eval/skill_retrieval_golden_set.json

指标:
    - Precision@K: Top-K 实际召回中命中期望技能的比例（负样本：实际为空才算命中）
    - Recall@K:    期望技能出现在 Top-K 的比例（负样本固定为 1.0）
    - MRR:         平均倒数排名（首个命中期望技能的倒数排名，无命中记 0）
    - 按难度（easy/medium/hard/tricky）与类别分组统计

退出码:
    0 — 整体 Precision@3 >= 阈值（默认 0.6）
    1 — 整体 Precision@3 < 阈值（CI 守卫触发）
    2 — 黄金集校验失败（expected_skill_ids 与实际技能 ID 不一致）

【不易】不改 loader.py / searcher.py 算法；不改技能定义；脚本独立运行不依赖 app 启动
【简易】仅依赖标准库 + agent.skills_mgmt 既有 import
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 让脚本可独立运行：把项目根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.skills_mgmt.loader import SkillLoader  # noqa: E402

# ════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════

DEFAULT_GOLDEN_SET = _PROJECT_ROOT / "tests" / "eval" / "skill_retrieval_golden_set.json"
DEFAULT_TOP_K = 3
DEFAULT_PRECISION_THRESHOLD = 0.6  # CI 守卫阈值
# 基线持久化路径：保存 TF-IDF 基线值供升级向量检索后对比
DEFAULT_BASELINE_PATH = _PROJECT_ROOT / "tests" / "eval" / "baseline_tfidf.json"


# ════════════════════════════════════════════════════════════
#  黄金集加载与校验
# ════════════════════════════════════════════════════════════

def load_golden_set(path: Path) -> Dict[str, Any]:
    """加载黄金测试集 JSON"""
    if not path.exists():
        raise FileNotFoundError(f"黄金测试集不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if "test_cases" not in data:
        raise ValueError(f"黄金测试集缺少 test_cases 字段: {path}")
    return data


def validate_expected_skill_ids(
    golden: Dict[str, Any],
    available_skill_ids: List[str],
) -> List[str]:
    """校验 expected_skill_ids 与实际技能 ID 一致

    【不易防御】黄金集 ID 必须与 data/skills_repo/ 实际存在的一致，
    避免期望无法命中的技能被误判为算法缺陷。

    Returns: 校验错误列表（空列表表示通过）
    """
    errors: List[str] = []
    available = set(available_skill_ids)
    for case in golden["test_cases"]:
        for sid in case.get("expected_skill_ids", []):
            if sid not in available:
                errors.append(
                    f"{case['case_id']}: expected_skill_id '{sid}' "
                    f"不在实际技能仓库中（可用: {sorted(available)}）"
                )
    return errors


# ════════════════════════════════════════════════════════════
#  指标计算
# ════════════════════════════════════════════════════════════

def _per_case_metrics(
    actual_ids: List[str],
    expected_ids: List[str],
    k: int,
) -> Tuple[float, float, float]:
    """计算单个 case 的 Precision@K / Recall@K / MRR

    负样本（expected 空）：
        - actual 也为空 → Precision=1（正确拒绝）
        - actual 非空   → Precision=0（误召回）
        - Recall 固定为 1.0（无期望即全部满足）
        - MRR 同 Precision 逻辑
    """
    topk = actual_ids[:k]

    if not expected_ids:
        # 负样本：期望空
        precision = 1.0 if len(topk) == 0 else 0.0
        recall = 1.0
        mrr = 1.0 if len(topk) == 0 else 0.0
        return precision, recall, mrr

    # 正样本
    expected_set = set(expected_ids)
    hits = [sid for sid in topk if sid in expected_set]
    precision = len(hits) / k if k > 0 else 0.0
    recall = len(hits) / len(expected_set)

    # MRR: 首个命中期望技能的倒数排名
    mrr = 0.0
    for idx, sid in enumerate(topk, start=1):
        if sid in expected_set:
            mrr = 1.0 / idx
            break

    return precision, recall, mrr


def _aggregate(values: List[float]) -> Dict[str, float]:
    """聚合统计：均值、最小、最大"""
    if not values:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "count": 0}
    return {
        "mean": round(sum(values) / len(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "count": len(values),
    }


def _group_by(
    cases_metrics: List[Dict[str, Any]],
    field: str,
) -> Dict[str, Dict[str, float]]:
    """按指定字段（difficulty / category）分组聚合指标"""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for c in cases_metrics:
        key = c.get(field, "unknown")
        groups.setdefault(key, []).append(c)

    result: Dict[str, Dict[str, float]] = {}
    for key, items in groups.items():
        result[key] = {
            "precision": _aggregate([c["precision"] for c in items])["mean"],
            "recall": _aggregate([c["recall"] for c in items])["mean"],
            "mrr": _aggregate([c["mrr"] for c in items])["mean"],
            "count": len(items),
        }
    return result


# ════════════════════════════════════════════════════════════
#  评估主函数
# ════════════════════════════════════════════════════════════

def evaluate(
    golden_set_path: Path = DEFAULT_GOLDEN_SET,
    top_k: int = DEFAULT_TOP_K,
    enabled_only: bool = True,
    loader: Optional[SkillLoader] = None,
) -> Dict[str, Any]:
    """评估主函数 — 返回完整报告字典

    Args:
        golden_set_path: 黄金集 JSON 路径
        top_k: 评估用的 K 值
        enabled_only: 透传给 SkillLoader.match
        loader: 可选的 SkillLoader 实例（便于测试注入），None 则新建

    Returns:
        {
            "version", "top_k", "total_cases",
            "overall": {"precision": float, "recall": float, "mrr": float},
            "by_difficulty": {...},
            "by_category": {...},
            "cases": [...],
            "available_skills": [...],
            "validation_errors": [...],
        }
    """
    golden = load_golden_set(golden_set_path)

    # 独立实例化 SkillLoader，不依赖 app 启动
    ld = loader or SkillLoader()

    # 校验：实际可用技能 ID
    available_skills = sorted(ld.fs.load_metadata_index().keys())
    validation_errors = validate_expected_skill_ids(golden, available_skills)

    cases_metrics: List[Dict[str, Any]] = []
    for case in golden["test_cases"]:
        query = case["query"]
        expected = case.get("expected_skill_ids", [])

        result = ld.match(query, top_k=top_k, enabled_only=enabled_only)
        actual_ids = [m.skill_id for m in result.matches]

        precision, recall, mrr = _per_case_metrics(actual_ids, expected, top_k)

        cases_metrics.append({
            "case_id": case["case_id"],
            "query": query,
            "expected": expected,
            "actual": actual_ids,
            "difficulty": case.get("difficulty", "unknown"),
            "category": case.get("category", "unknown"),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "mrr": round(mrr, 4),
            "hit": precision > 0,
        })

    overall_p = _aggregate([c["precision"] for c in cases_metrics])["mean"]
    overall_r = _aggregate([c["recall"] for c in cases_metrics])["mean"]
    overall_m = _aggregate([c["mrr"] for c in cases_metrics])["mean"]

    return {
        "version": golden.get("version", "unknown"),
        "golden_set_path": str(golden_set_path),
        "top_k": top_k,
        "enabled_only": enabled_only,
        "total_cases": len(cases_metrics),
        "available_skills": available_skills,
        "overall": {
            "precision": overall_p,
            "recall": overall_r,
            "mrr": overall_m,
        },
        "by_difficulty": _group_by(cases_metrics, "difficulty"),
        "by_category": _group_by(cases_metrics, "category"),
        "cases": cases_metrics,
        "validation_errors": validation_errors,
    }


# ════════════════════════════════════════════════════════════
#  报告渲染
# ════════════════════════════════════════════════════════════

def render_table(report: Dict[str, Any]) -> str:
    """渲染人类可读的表格报告"""
    lines: List[str] = []
    sep = "─" * 80

    lines.append(sep)
    lines.append("技能检索评估报告 — SkillLoader.match (TF-IDF 基线)")
    lines.append(sep)
    lines.append(f"黄金集版本      : {report['version']}")
    lines.append(f"黄金集路径      : {report['golden_set_path']}")
    lines.append(f"Top-K           : {report['top_k']}")
    lines.append(f"enabled_only    : {report['enabled_only']}")
    lines.append(f"用例总数        : {report['total_cases']}")
    lines.append(f"可用技能 ({len(report['available_skills'])}) : {', '.join(report['available_skills'])}")

    if report["validation_errors"]:
        lines.append("")
        lines.append("⚠ 校验错误（expected_skill_ids 与实际技能不一致）：")
        for err in report["validation_errors"]:
            lines.append(f"  - {err}")

    lines.append("")
    lines.append("【整体指标】")
    o = report["overall"]
    lines.append(f"  Precision@{report['top_k']} : {o['precision']:.4f}")
    lines.append(f"  Recall@{report['top_k']}    : {o['recall']:.4f}")
    lines.append(f"  MRR                  : {o['mrr']:.4f}")

    # 基线对比（如有）
    if "baseline" in report:
        b = report["baseline"]
        d = b["delta"]
        lines.append("")
        lines.append("【基线对比】")
        lines.append(f"  基线版本        : {b['version']}")
        lines.append(f"  基线 Precision@{b['top_k']} : {b['overall']['precision']:.4f}  (delta: {d['precision']:+.4f})")
        lines.append(f"  基线 Recall@{b['top_k']}    : {b['overall']['recall']:.4f}  (delta: {d['recall']:+.4f})")
        lines.append(f"  基线 MRR              : {b['overall']['mrr']:.4f}  (delta: {d['mrr']:+.4f})")

    lines.append("")
    lines.append("【按难度分组】")
    lines.append(f"  {'难度':<10} {'Precision':>12} {'Recall':>12} {'MRR':>10} {'用例数':>8}")
    for diff in ["easy", "medium", "hard", "tricky"]:
        if diff in report["by_difficulty"]:
            g = report["by_difficulty"][diff]
            lines.append(
                f"  {diff:<10} {g['precision']:>12.4f} {g['recall']:>12.4f} "
                f"{g['mrr']:>10.4f} {g['count']:>8}"
            )

    lines.append("")
    lines.append("【按类别分组】")
    lines.append(f"  {'类别':<16} {'Precision':>12} {'Recall':>12} {'MRR':>10} {'用例数':>8}")
    for cat in sorted(report["by_category"].keys()):
        g = report["by_category"][cat]
        lines.append(
            f"  {cat:<16} {g['precision']:>12.4f} {g['recall']:>12.4f} "
            f"{g['mrr']:>10.4f} {g['count']:>8}"
        )

    lines.append("")
    lines.append("【逐用例明细】")
    lines.append(f"  {'case_id':<10} {'难度':<8} {'类别':<14} {'P':>6} {'R':>6} {'MRR':>6}  query")
    for c in report["cases"]:
        query_preview = c["query"][:40] + ("..." if len(c["query"]) > 40 else "")
        lines.append(
            f"  {c['case_id']:<10} {c['difficulty']:<8} {c['category']:<14} "
            f"{c['precision']:>6.2f} {c['recall']:>6.2f} {c['mrr']:>6.2f}  {query_preview}"
        )

    # 未命中用例汇总
    missed = [c for c in report["cases"] if not c["hit"]]
    if missed:
        lines.append("")
        lines.append(f"【未命中用例（{len(missed)}/{report['total_cases']}）】")
        for c in missed:
            lines.append(
                f"  {c['case_id']}: expected={c['expected']} actual={c['actual']}"
            )

    lines.append("")
    lines.append(sep)
    return "\n".join(lines)


def render_json(report: Dict[str, Any]) -> str:
    """渲染 JSON 报告"""
    return json.dumps(report, ensure_ascii=False, indent=2)


# ════════════════════════════════════════════════════════════
#  CLI 入口
# ════════════════════════════════════════════════════════════

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="技能检索评估 — Precision@K / Recall@K / MRR",
    )
    parser.add_argument(
        "--golden-set", type=Path, default=DEFAULT_GOLDEN_SET,
        help=f"黄金集 JSON 路径（默认: {DEFAULT_GOLDEN_SET}）",
    )
    parser.add_argument(
        "--top-k", type=int, default=DEFAULT_TOP_K,
        help=f"评估 K 值（默认: {DEFAULT_TOP_K}）",
    )
    parser.add_argument(
        "--report-format", choices=["table", "json"], default="table",
        help="报告格式（默认: table）",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="输出到文件（默认: stdout）",
    )
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_PRECISION_THRESHOLD,
        help=f"Precision@K 守卫阈值（默认: {DEFAULT_PRECISION_THRESHOLD}）",
    )
    parser.add_argument(
        "--include-disabled", action="store_true",
        help="包含禁用技能（默认 enabled_only=True）",
    )
    parser.add_argument(
        "--save-baseline", type=Path, default=None,
        help="将当前报告保存为基线文件（供未来升级向量检索后对比）",
    )
    parser.add_argument(
        "--baseline", type=Path, default=None,
        help="加载历史基线进行对比，输出 delta（current - baseline）",
    )
    args = parser.parse_args(argv)

    report = evaluate(
        golden_set_path=args.golden_set,
        top_k=args.top_k,
        enabled_only=not args.include_disabled,
    )

    # 加载历史基线对比（如有）
    if args.baseline and args.baseline.exists():
        with args.baseline.open("r", encoding="utf-8") as f:
            baseline_data = json.load(f)
        b = baseline_data.get("overall", {})
        cur = report["overall"]
        report["baseline"] = {
            "path": str(args.baseline),
            "version": baseline_data.get("version", "unknown"),
            "top_k": baseline_data.get("top_k"),
            "overall": b,
            "delta": {
                "precision": round(cur["precision"] - b.get("precision", 0), 4),
                "recall": round(cur["recall"] - b.get("recall", 0), 4),
                "mrr": round(cur["mrr"] - b.get("mrr", 0), 4),
            },
        }

    # 校验失败 → 退出码 2
    if report["validation_errors"]:
        sys.stderr.write("黄金集校验失败:\n")
        for err in report["validation_errors"]:
            sys.stderr.write(f"  - {err}\n")
        # 仍然输出报告便于排查
        output = render_json(report) if args.report_format == "json" else render_table(report)
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output)
        return 2

    # 渲染报告
    output = render_json(report) if args.report_format == "json" else render_table(report)
    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"报告已写入: {args.output}")
    else:
        print(output)

    # 持久化基线（显式 --save-baseline 触发，无副作用）
    if args.save_baseline:
        args.save_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.save_baseline.write_text(
            render_json(report), encoding="utf-8",
        )
        print(f"基线已保存: {args.save_baseline}")

    # CI 守卫：Precision@K < 阈值 → 退出码 1
    precision = report["overall"]["precision"]
    if precision < args.threshold:
        sys.stderr.write(
            f"\n❌ CI 守卫失败: Precision@{args.top_k}={precision:.4f} < 阈值 {args.threshold}\n"
        )
        return 1

    sys.stderr.write(
        f"\n✅ CI 守卫通过: Precision@{args.top_k}={precision:.4f} >= 阈值 {args.threshold}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
