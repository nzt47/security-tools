# -*- coding: utf-8 -*-
"""存量脏工作流退役 CLI（TASK-S10-01）

只标记不删除：把命中**结构性准入否决**（单字触发词 / 步骤数下限）的存量条目
置为 `archived`，并把证据快照追加进审计台账 `data/learned_workflows_retired.jsonl`。

判定源与运行时**同一份**（`agent.workflow_learning.admission`），故脚本读数与
`WorkflowLearningService.admission_report()` 必然一致，可复算。

用法：
    # 1) 试运行（默认）：只出清单与前后数字，不写任何东西
    python scripts/retire_dirty_workflows.py
    # 2) 真正退役（写 status=archived + 追加台账）
    python scripts/retire_dirty_workflows.py --apply
    # 3) 指定仓库/台账（默认 data/learned_workflows.json 与其同目录 jsonl）
    python scripts/retire_dirty_workflows.py --apply --repo data/learned_workflows.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agent.workflow_learning.admission import (  # noqa: E402
    MIN_STEPS, MIN_TRIGGER_CHARS, check_match_eligibility,
    single_char_triggers,
)
from agent.workflow_learning.repository import WorkflowRepository  # noqa: E402
from agent.workflow_learning.retirement import (  # noqa: E402
    default_ledger_path, retire_dirty_workflows,
)


def _print_report(repo: WorkflowRepository, summary: dict) -> None:
    all_wf = repo.list_all()
    print("=" * 78)
    print("存量脏工作流退役（只标记不删除 + 追加台账）")
    print("=" * 78)
    print(f"仓库: {repo._path}")
    print(f"台账: {summary['ledger_path']}")
    print(f"判定阈值: MIN_STEPS={MIN_STEPS}  MIN_TRIGGER_CHARS={MIN_TRIGGER_CHARS}")
    print(f"模式: {'APPLY(已写入)' if summary['applied'] else 'DRY-RUN(未写入)'}")
    print("-" * 78)
    print(f"条目总数: {len(all_wf)}")
    print(f"命中脏条件(结构性否决)条数: {summary['dirty_before']}")
    print(f"退役后仍为脏且未归档的条数: {summary['dirty_after']}")
    print(f"本轮实际迁移(active→archived): {len(summary['retired'])}")
    print(f"已是 archived（幂等跳过）: {len(summary['already_archived'])}"
          f" {summary['already_archived']}")
    print(f"台账追加行数: {summary['ledger_records']}")
    print("-" * 78)
    if summary["retired"]:
        print("退役清单（workflow_id | 步骤数 | 单字触发词 | 拒绝码 | 已转技能）:")
        for r in summary["retired"]:
            print(f"  - {r['workflow_id']} | steps={r['step_count']} | "
                  f"triggers={r['trigger_patterns']} | "
                  f"codes={r['codes']} | "
                  f"skill={r['converted_to_skill_id'] or '-'} | "
                  f"session={r['source_session_id']}")
    else:
        print("退役清单: 空（无命中条数或已全部归档）")
    print("-" * 78)
    print("退役后各条状态与匹配候选资格:")
    for wf in sorted(all_wf, key=lambda w: w.id):
        status = str(getattr(wf.status, "value", wf.status))
        dec = check_match_eligibility(wf)
        bad = single_char_triggers(wf.trigger_patterns)
        print(f"  {wf.id:>18} | status={status:<10} | steps={len(wf.steps or [])}"
              f" | 候选={'是' if dec.admitted else '否'}"
              f" | 单字触发词={bad or '-'}"
              f" | 触发词={list(wf.trigger_patterns)}")
    print("=" * 78)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="存量脏工作流退役（只标记不删除）")
    ap.add_argument("--apply", action="store_true",
                    help="真正写入（默认 dry-run，只出清单）")
    ap.add_argument("--repo", default=None,
                    help="工作流仓库 JSON 路径（默认 data/learned_workflows.json）")
    ap.add_argument("--ledger", default=None, help="审计台账 jsonl 路径")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出汇总")
    args = ap.parse_args(argv)

    repo = WorkflowRepository(path=args.repo)
    ledger = pathlib.Path(args.ledger) if args.ledger else None
    summary = retire_dirty_workflows(repo, apply=args.apply, ledger_path=ledger)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_report(repo, summary)
        if not args.apply and summary["retired"]:
            print(f"[提示] 以上为试运行结果；加 --apply 才会标记并写入台账 "
                  f"{default_ledger_path(args.repo)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
