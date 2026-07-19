"""打印 0 分用例的原始查询与实际召回结果 — 方便手动确认根因

用法:
    python scripts/print_zero_score_cases.py
    python scripts/print_zero_score_cases.py --top-k 5
    python scripts/print_zero_score_cases.py --show-meta   # 同时打印技能的 front matter

【不易】只读，不修改任何技能定义
【简易】单文件，标准库依赖
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.skills_mgmt.loader import SkillLoader  # noqa: E402


def _print_skill_front_matter(skill_id: str, loader: SkillLoader) -> None:
    """打印技能的 front matter 元数据（不读 body）"""
    meta = loader.fs.get_metadata(skill_id)
    if not meta:
        print(f"    [meta] {skill_id}: <未找到>")
        return
    # 过滤掉内部字段 _dir
    clean = {k: v for k, v in meta.items() if not k.startswith("_")}
    print(f"    [meta] {skill_id}:")
    for k, v in clean.items():
        v_repr = repr(v) if isinstance(v, str) and not v else v
        print(f"      {k}: {v_repr}")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="打印 0 分用例的查询与召回结果",
    )
    parser.add_argument(
        "--golden-set", type=Path,
        default=_PROJECT_ROOT / "tests" / "eval" / "skill_retrieval_golden_set.json",
        help="黄金集 JSON 路径",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument(
        "--show-meta", action="store_true",
        help="同时打印期望技能的 front matter 元数据",
    )
    args = parser.parse_args(argv)

    # 加载黄金集
    with args.golden_set.open("r", encoding="utf-8") as f:
        golden = json.load(f)

    loader = SkillLoader()

    print("=" * 100)
    print(f"0 分用例明细（K={args.top_k}）— 手动根因确认")
    print("=" * 100)
    print()

    zero_cases: List[Dict[str, Any]] = []
    all_cases: List[Dict[str, Any]] = []

    for case in golden["test_cases"]:
        result = loader.match(case["query"], top_k=args.top_k, enabled_only=True)
        actual_ids = [m.skill_id for m in result.matches]
        actual_scores = [m.score for m in result.matches]
        expected = case.get("expected_skill_ids", [])

        # 负样本：actual 非空即 0 分；正样本：actual 中无期望即 0 分
        if expected:
            is_zero = not any(sid in expected for sid in actual_ids)
        else:
            is_zero = len(actual_ids) > 0

        case_detail = {
            **case,
            "actual_ids": actual_ids,
            "actual_scores": actual_scores,
            "is_zero": is_zero,
        }
        all_cases.append(case_detail)
        if is_zero:
            zero_cases.append(case_detail)

    print(f"总计 {len(zero_cases)} 个 0 分用例 / {len(all_cases)} 个用例")
    print()

    for idx, c in enumerate(zero_cases, start=1):
        print(f"┌─ 0 分用例 {idx}/{len(zero_cases)} ─────────────────────────────────")
        print(f"│ case_id    : {c['case_id']}")
        print(f"│ 难度       : {c['difficulty']}")
        print(f"│ 类别       : {c['category']}")
        print(f"│ 原始 query : {c['query']}")
        print(f"│ notes      : {c.get('notes', '')}")
        print(f"│")
        print(f"│ 期望召回 (expected_skill_ids): {c['expected_skill_ids']}")
        print(f"│ 实际召回 (actual, top_k={args.top_k}):")
        if not c["actual_ids"]:
            print(f"│   (空) — 算法未召回任何技能")
        else:
            for i, (sid, sc) in enumerate(zip(c["actual_ids"], c["actual_scores"]), 1):
                marker = " ✓" if sid in c["expected_skill_ids"] else ""
                print(f"│   {i}. {sid:<25} score={sc:.4f}{marker}")
        print(f"│")
        print(f"│ 召回正确性 : {'❌ 全部错误' if not any(s in c['expected_skill_ids'] for s in c['actual_ids']) else '⚠ 部分命中但 Precision=0'}")
        print(f"└{'─' * 80}")
        print()

        if args.show_meta and c["expected_skill_ids"]:
            print("  期望技能的 front matter 元数据（验证 description 是否为空）：")
            for sid in c["expected_skill_ids"]:
                _print_skill_front_matter(sid, loader)
            print()

    # 汇总根因
    print("=" * 100)
    print("根因汇总")
    print("=" * 100)
    expected_ids_in_zero: Dict[str, int] = {}
    for c in zero_cases:
        for sid in c["expected_skill_ids"]:
            expected_ids_in_zero[sid] = expected_ids_in_zero.get(sid, 0) + 1
    print(f"  0 分用例期望命中但未命中的技能分布：")
    for sid, cnt in sorted(expected_ids_in_zero.items(), key=lambda x: -x[1]):
        meta = loader.fs.get_metadata(sid) or {}
        desc = meta.get("description", "")
        desc_status = f"description 为空 ({len(desc)} 字符)" if not desc else f"description 非空 ({len(desc)} 字符)"
        print(f"    {sid:<25} 出现 {cnt} 次  [{desc_status}]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
