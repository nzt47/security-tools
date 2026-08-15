"""TASK-05 · feedback_agent.execute_recommendations 本地演示脚本

用途:
    用 mock 反馈数据（4 类 recommended_action）本地跑一轮
    execute_recommendations，验证:
      1) 正式执行（默认 dry_run=False）: promote/merge/deprecate/improve
         动作执行正确，状态变更可见，审计 JSONL 逐条落盘（含快照版本）
      2) --dry-run 预演: 报告（planned）正确、零状态变更、零审计文件写入
      3) 核心分支 logger.info 排查日志输出

运行:
    python scripts/demo_feedback_agent.py          # 正式执行（默认）
    python scripts/demo_feedback_agent.py --dry-run  # 仅预演（零副作用）

守【不易】: 只 mock get_skill_feedback_summary 返回值与
get_feedback_manager（防真实反馈库副作用）；临时 store/审计文件均在
temp 目录，不污染真实数据；不触碰 feedback.py / models.py / 既有模块。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

# scripts/ 下执行时项目根不在 sys.path（sys.path[0]=scripts/）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.skills_mgmt.feedback_agent import (  # noqa: E402
    ACTION_DEPRECATE_MERGE,
    ACTION_IMPROVE,
    ACTION_PROMOTE,
    FeedbackAgent,
)
from agent.skills_mgmt.models import ReviewResult, ReviewStatus, SkillStatus  # noqa: E402
from agent.skills_mgmt.service import SkillsMgmtService  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


def _summary(action: str, *, satisfaction: float = 95.0,
             total: int = 8, avg: float = 4.5) -> dict:
    """构造 mock 反馈汇总（recommended_action 由调用方决定）。"""
    return {
        "skill_id": "x", "time_range_days": 30, "total_feedback": total,
        "total_rated": total, "like_count": 0, "dislike_count": 0,
        "satisfaction_rate_percent": satisfaction, "avg_rating": avg,
        "by_type": {}, "by_category": {}, "quality_cases_count": 0,
        "recent_dislike_comments": [], "recommended_action": action,
    }


def _build_stack(tmp_dir: str):
    """构造临时技能库 + mock 反馈分派，返回 (svc, summary_map, audit_path)。"""
    svc = SkillsMgmtService(store_path=os.path.join(tmp_dir, "skills.json"))

    # s-promote：已过审核链（PASSED）→ 应成功发布
    promoted = svc.create_manual({"id": "s-promote", "name": "s-promote"})
    promoted.review = ReviewResult(
        status=ReviewStatus.PASSED, score=80.0, duplicate_score=0.0,
        security_score=90.0, quality_score=80.0,
        reviewed_at="2026-08-14T00:00:00", reviewed_by="tester")
    svc.store.upsert(promoted)

    # s-noreview：未过审核链 → promote 应被拒（TASK-04 强制链联动）
    svc.create_manual({"id": "s-noreview", "name": "s-noreview"})

    # s-merge-src / s-merge-dst：相同内容 → 高 Jaccard → merge
    merge_src = svc.create_manual({"id": "s-merge-src", "name": "s-merge-src"})
    merge_src.content = "重复的技能内容：处理订单查询的通用步骤"
    merge_src.metrics.usage_count = 1
    svc.store.upsert(merge_src)
    merge_dst = svc.create_manual({"id": "s-merge-dst", "name": "s-merge-dst"})
    merge_dst.content = "重复的技能内容：处理订单查询的通用步骤"
    merge_dst.metrics.usage_count = 100
    svc.store.upsert(merge_dst)

    # s-deprecate：独有内容 → 无高相似技能 → DEPRECATED
    deprecate = svc.create_manual({"id": "s-deprecate", "name": "s-deprecate"})
    deprecate.content = "独一无二的内容：仅此技能包含的特殊处理逻辑，无重复"
    svc.store.upsert(deprecate)

    # s-improve：平均评分低 → improve_params
    svc.create_manual({"id": "s-improve", "name": "s-improve"})

    summary_map = {
        "s-promote": _summary(ACTION_PROMOTE),
        "s-noreview": _summary(ACTION_PROMOTE),
        "s-merge-src": _summary(ACTION_DEPRECATE_MERGE, satisfaction=30.0),
        "s-deprecate": _summary(ACTION_DEPRECATE_MERGE, satisfaction=20.0),
        "s-improve": _summary(ACTION_IMPROVE, avg=2.0),
    }
    # s-merge-dst 无反馈 → no_data（保持其内容，仅作为 merge 保留方）
    summary_map["s-merge-dst"] = _summary("no_data", total=0)

    audit_path = os.path.join(tmp_dir, "feedback_agent_audit.jsonl")
    return svc, summary_map, audit_path


def _show_report(title: str, report: dict) -> None:
    print(f"\n=== {title} ===")
    print(f"  dry_run        : {report['dry_run']}")
    print(f"  total/processed: {report['total_skills']}/{report['processed']}")
    print(f"  actions        : {report['actions']}")
    if report["planned"]:
        print("  planned        :")
        for p in report["planned"]:
            print(f"    - {p['skill_id']:<14} action={p['action']:<28} "
                  f"reason={p['reason']}")
    if report["executed"]:
        print("  executed       :")
        for e in report["executed"]:
            print(f"    - {e['skill_id']:<14} result={e['result']:<18} "
                  f"snapshot={e.get('snapshot_version')}"
                  + (f"  merged_into={e.get('merged_into')}"
                     if e.get("merged_into") else ""))
    if report["rejected"]:
        print("  rejected       :")
        for r in report["rejected"]:
            print(f"    - {r['skill_id']:<14} error={r.get('error')}")
    if report["errors"]:
        print("  errors         :")
        for err in report["errors"]:
            print(f"    - {err}")


def _show_audit(audit_path: str) -> None:
    print(f"\n=== 审计日志 JSONL（{audit_path}）===")
    if not Path(audit_path).exists():
        print("  （文件不存在 — dry_run 模式零审计写入，符合预期）")
        return
    for line in Path(audit_path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            print(f"  {rec['ts']} event={rec['event']} skill={rec['skill_id']} "
                  f"action={rec['action']} result={rec['result']} "
                  f"snapshot={rec.get('snapshot_version')}")
            for key in ("merged_into", "jaccard", "optimized", "reason"):
                if key in rec:
                    print(f"      {key}={rec[key]}")


def _show_status(svc, tmp_dir: str) -> None:
    print("\n=== 各技能最终状态 ===")
    for skill in svc.store.list_all():
        print(f"  {skill.id:<14} status={skill.status}"
              + (f"  usage={skill.metrics.usage_count}"
                 if skill.metrics.usage_count else ""))
    print(f"  技能总数: {svc.store.count()}（s-merge-src 已被 merge 移除）")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="feedback_agent.execute_recommendations 本地演示")
    parser.add_argument("--dry-run", action="store_true",
                        help="只做 dry-run 预演（零副作用，验证报告与零审计）")
    args = parser.parse_args()

    # mock 反馈库绑定（防 merge rebind 初始化真实反馈库）
    import agent.feedback as feedback_mod
    feedback_mod.get_feedback_manager = lambda: None  # type: ignore[attr-defined]

    tmp_dir = tempfile.mkdtemp(prefix="feedback_agent_demo_")
    print(f"临时目录: {tmp_dir}")
    svc, summary_map, audit_path = _build_stack(tmp_dir)
    svc.get_skill_feedback_summary = (  # type: ignore[method-assign]
        lambda skill_id, days=30: summary_map[skill_id])

    agent = FeedbackAgent(service=svc, audit_path=audit_path)

    if args.dry_run:
        # dry-run 预演轮：只出报告，零副作用（审计文件应不存在）
        report_dry = agent.execute_recommendations(dry_run=True)
        _show_report("dry-run 预演（零副作用）", report_dry)
        assert report_dry["executed"] == [] and report_dry["rejected"] == []
        assert not Path(audit_path).exists(), "dry_run 不应写审计文件"
        print("\n[dry-run 校验] 零状态变更 / 零审计写入：通过")
        for skill in svc.store.list_all():
            assert skill.status == SkillStatus.DRAFT, f"{skill.id} 状态不应变更"
        return

    # 正式执行轮：实际动作 + 状态变更 + 审计落盘（每次运行均用全新临时目录）
    report = agent.execute_recommendations(dry_run=False)
    _show_report("正式执行（dry_run=False）", report)
    _show_audit(audit_path)
    _show_status(svc, tmp_dir)

    print("\n=== 演示完成 ===")
    print("  预期：s-promote=published（已过审）；s-noreview=被拒（强制链联动）；")
    print("        s-merge-src 被合并移除（并入 s-merge-dst）；")
    print("        s-deprecate=deprecated；s-improve 参数已优化；")
    print("        审计 4 行（promote/merge/deprecate/improve 各 1 行）。")


if __name__ == "__main__":
    main()
