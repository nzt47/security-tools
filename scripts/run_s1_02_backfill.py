"""TASK-S1-02 存量资产字段回填 — 摸底/干跑/实跑 操作入口

用法（项目根）:
    python scripts/run_s1_02_backfill.py            # 摸底 + 实跑（写 data/descriptors.json）
    python scripts/run_s1_02_backfill.py --dry-run   # 摸底 + 干跑（不落库）
    python scripts/run_s1_02_backfill.py --json      # 输出 JSON（供 CI/验收对账）

产出:
    data/descriptors_s1_02/摸底表_<ts>.md / survey_<ts>.json   （步骤 1 摸底表）
    data/descriptors_s1_02/plan_<ts>.json                      （步骤 2 dry-run 计划）
    data/descriptors_s1_02/run_<ts>.json / needs_*.json        （步骤 3 实跑/待补清单）
    data/descriptors.json                                      （Descriptor 台账本体）
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.descriptors.backfill import (  # noqa: E402
    needs_markdown,
    plan_backfill,
    run_backfill,
    survey_markdown,
)

DEFAULT_RESOLUTION_PATH = str(Path("data/descriptors/resolutions.jsonl"))
DEFAULT_MAIN_PATH = str(Path("data/skills_mgmt.json"))
DEFAULT_REGISTRY_PATH = str(Path("data/descriptors.json"))


def _resolution_store(path: str, *, enabled: bool) -> object:
    """裁定留痕台账（D4）；``--no-resolutions`` 或路径不可用时返回 None（零行为变化）"""
    if not enabled:
        return None
    try:
        from agent.digestion.resolutions import ResolutionStore
    except Exception as exc:  # noqa: BLE001  台账不可用不得让清单产出失败
        print(f"[warn] 裁定台账不可用，按未接入处理: {exc}")
        return None
    return ResolutionStore(path=path)


def _equal(a, b) -> bool:
    return json.dumps(a, ensure_ascii=False, sort_keys=True) == \
        json.dumps(b, ensure_ascii=False, sort_keys=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="S1-02 存量资产字段回填")
    ap.add_argument("--dry-run", action="store_true",
                    help="只摸底 + 干跑（两次一致性校验），不落库")
    ap.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    ap.add_argument("--out-dir", default=str(Path("data/descriptors_s1_02")),
                    help="报告落点（默认 data/descriptors_s1_02）")
    ap.add_argument("--batch-size", type=int, default=200)
    ap.add_argument("--resolutions", default=DEFAULT_RESOLUTION_PATH,
                    help="人工裁定留痕台账路径（D4：已裁定项不再重复提醒）")
    ap.add_argument("--no-resolutions", action="store_true",
                    help="不接入裁定台账（与 S8-05 之前的行为逐字一致）")
    ap.add_argument("--main-path", default="",
                    help=f"技能主轨路径（默认 {DEFAULT_MAIN_PATH}）")
    ap.add_argument("--registry-path", default="",
                    help=f"descriptor 台账路径（默认 {DEFAULT_REGISTRY_PATH}）")
    args = ap.parse_args()

    store = _resolution_store(args.resolutions, enabled=not args.no_resolutions)
    planned = plan_backfill(
        main_path=Path(args.main_path) if args.main_path else None,
        resolutions=store)
    registry_path = Path(args.registry_path) if args.registry_path else None
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 步骤 1：摸底表
    survey = planned["survey"]
    md = survey_markdown(survey)
    (out / "摸底表_latest.md").write_text(md, encoding="utf-8")
    (out / "survey_latest.json").write_text(
        json.dumps({"summary": survey["summary"], "rows": survey["rows"]},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    # 步骤 2：dry-run 两次一致性（确定性可复现）
    d1 = run_backfill(planned, dry_run=True)
    d2 = run_backfill(planned, dry_run=True)
    deterministic = _equal(d1, d2)
    (out / "dryrun_latest.json").write_text(
        json.dumps({"deterministic": deterministic,
                    "run1": {k: v for k, v in d1.items() if k != "run_id"},
                    "run2": {k: v for k, v in d2.items() if k != "run_id"}},
                   ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    # 待办清单（含已裁定项及其依据 —— D4"依据可见"）
    (out / "needs_review_latest.md").write_text(
        needs_markdown(planned["needs"]), encoding="utf-8")

    if args.dry_run:
        result = {"dry_run": True, "deterministic": deterministic,
                  "summary": survey["summary"],
                  "coverage": planned["coverage"],
                  "needs": planned["needs"]}
    else:
        # 步骤 3：实跑（逐条审计 + 分批回滚防护）+ 步骤 4 全量重校验
        run = run_backfill(planned, batch_size=args.batch_size,
                           registry_path=registry_path)
        (out / "run_latest.json").write_text(
            json.dumps(run, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8")
        (out / "needs_review_latest.json").write_text(
            json.dumps(run["pending"]["needs_review"], ensure_ascii=False,
                       indent=1), encoding="utf-8")
        (out / "needs_undo_hint_latest.json").write_text(
            json.dumps(run["pending"]["needs_undo_hint"], ensure_ascii=False,
                       indent=1), encoding="utf-8")
        (out / "validation_latest.json").write_text(
            json.dumps(run["validation"], ensure_ascii=False, indent=1),
            encoding="utf-8")
        result = {"dry_run": False,
                  "deterministic": deterministic,
                  "summary": survey["summary"],
                  "coverage": planned["coverage"],
                  "needs": planned["needs"],
                  "run": {k: v for k, v in run.items()
                          if k not in ("pending", "coverage", "validation")},
                  "validation": run["validation"]}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    else:
        needs = planned["needs"]
        print(f"资产规模: {survey['summary']['asset_count']}")
        print(f"来源分布: {survey['summary']['by_source']}")
        print(f"dry-run 两次一致性: {deterministic}")
        print(f"计划覆盖率: {planned['coverage']}")
        print(f"NEEDS_REVIEW: {len(needs['needs_review'])} 条, "
              f"NEEDS_UNDO_HINT: {len(needs['needs_undo_hint'])} 条")
        print(f"已裁定不再提醒: {needs.get('resolution_skipped', 0)} 条"
              f"（台账 {needs.get('resolution_summary', {}).get('active', 0)} 条生效裁定）")
        if not args.dry_run:
            v = run["validation"]
            print(f"实跑批次全 ok: {all(b['ok'] for b in run['batches'])} "
                  f"(回滚事件 {len(run['rollback_events'])})")
            print(f"全量重校验: {v['total']} 条, valid={v['valid']}, "
                  f"errors={v['with_errors']}, warnings={v['with_warnings']}")
            print(f"写入计数: {run['applied']}")
        print(f"报告目录: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
