"""TASK-05 · EvolutionScheduler（offline_evolver 周级进化调度）本地演示脚本

用途:
    演示"每周触发一次 offline_evolver 的进化流程":
      1) dry-run 预演: 走真实候选筛选（usage>=10 且 success_rate<0.95），
         零提交 / 零 KPI / 零审计
      2) 正式 run: 用确定性 mock 批次摘要演示调度包装层行为
         （报告摘要 + 进化采纳率 KPI + 审计 JSONL 落盘）
      3) 周级调度注册: interval_days=7 注册到 task_scheduler → 任务列表 → 注销

运行:
    python scripts/demo_evolution_scheduler.py

守【不易】: 不触碰 offline_evolver.py 进化算法与 BatchEvolutionReport 结构；
    正式 run 用注入的 mock 摘要（真实调度触发时执行完整 evolve_batch，
    产 BatchEvolutionReport 审计 + KPI）；临时 store/审计文件均在 temp 目录，
    不污染真实数据。
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

from agent.skills_mgmt.evolution_scheduler import (  # noqa: E402
    EvolutionScheduler,
    TASK_NAME,
)
from agent.skills_mgmt.models import Skill, SkillMetrics  # noqa: E402
from agent.skills_mgmt.service import SkillsMgmtService  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


class _DemoService:
    """包装真实 service：候选筛选走真实 evolver，正式 run 用确定性 mock 摘要。

    演示目的: 展示"每周触发"调度包装层的完整行为（预演/报告/KPI/审计），
    不触发真实 LLM 评估器（正式部署时 evolve_batch 执行完整进化流程）。
    """

    def __init__(self, svc: SkillsMgmtService):
        self._svc = svc

    def _new_evolver(self):
        return self._svc._new_evolver()

    def evolve_batch(self, skill_ids=None, *, max_rounds=1, trigger="scheduler"):
        # 演示用确定性摘要（字段对齐 BatchEvolutionReport 摘要，供调度层消费）
        return {
            "total_skills": 2,
            "evolved_count": 1,
            "skipped_count": 1,
            "failed_count": 0,
            "avg_improvement": 0.07,
            "cost_tokens": 320,
            "budget_breached": False,
        }


def _build_stack(tmp_dir: str) -> tuple:
    """构造临时技能库（含候选/非候选技能），返回 (svc, audit_path)。"""
    svc = SkillsMgmtService(store_path=os.path.join(tmp_dir, "skills.json"))

    def _skill(skill_id: str, usage: int, success: float) -> Skill:
        s = Skill(id=skill_id, name=skill_id,
                  metrics=SkillMetrics(usage_count=usage, success_rate=success))
        svc.store.upsert(s)
        return s

    # 候选（usage>=10 且 success_rate<0.95）→ 会被筛选进预演与正式轮
    _skill("s-cand-1", usage=50, success=0.70)
    _skill("s-cand-2", usage=30, success=0.80)
    # 非候选：成功率已达标 / 使用次数不足 / 零使用
    _skill("s-ok-1", usage=80, success=0.98)
    _skill("s-new-1", usage=3, success=0.60)
    _skill("s-new-2", usage=0, success=0.0)

    audit_path = os.path.join(tmp_dir, "evolution_schedule_audit.jsonl")
    return svc, audit_path


def _show_preview(report: dict) -> None:
    print("\n=== dry-run 预演（候选筛选，零副作用）===")
    print(f"  候选数: {len(report['planned_candidates'])}")
    for c in report["planned_candidates"]:
        print(f"    - {c['skill_id']:<12} usage={c['usage_count']} "
              f"success_rate={c['success_rate']}")


def _show_report(report: dict) -> None:
    print("\n=== 正式 run（进化批次摘要）===")
    for key in ("dry_run", "total_skills", "evolved_count", "skipped_count",
                "failed_count", "avg_improvement", "cost_tokens",
                "adopted_candidates", "total_candidates"):
        print(f"  {key:<20}: {report.get(key)}")


def _show_audit(audit_path: str) -> None:
    print(f"\n=== 审计日志 JSONL（{audit_path}）===")
    if not Path(audit_path).exists():
        print("  （文件不存在 — dry-run 模式零审计写入，符合预期）")
        return
    for line in Path(audit_path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            print(f"  {rec['ts']} event={rec['event']} "
                  f"evolved={rec['evolved_count']} adopted={rec['adopted_candidates']} "
                  f"cost_tokens={rec['cost_tokens']}")


def _show_kpi() -> None:
    from agent.learning_metrics import get_learning_metrics
    metrics = get_learning_metrics()
    print(f"\n=== TASK-03 进化采纳率 KPI ===")
    print(f"  候选总数     : {metrics._evolution_candidates}")
    print(f"  已采纳       : {metrics._evolution_adopted}")
    if metrics._evolution_candidates:
        print(f"  采纳率       : "
              f"{metrics._evolution_adopted / metrics._evolution_candidates:.2%}")


def _show_schedule(svc: SkillsMgmtService, audit_path: str) -> None:
    print("\n=== 周级调度注册（interval_days=7，每周触发一次）===")
    sched = EvolutionScheduler(service=svc, audit_path=audit_path)
    result = sched.schedule(interval_days=7)
    print(f"  register   : status={result['status']} "
          f"interval_days={result.get('interval_days')} "
          f"dry_run={result.get('dry_run')}")
    if result["status"] != "scheduled":
        print(f"  说明: {result.get('note')}")
        return

    from agent.task_scheduler import get_scheduler
    print("  task_scheduler 任务列表:")
    for t in get_scheduler().list_tasks():
        mark = " ← 周级进化" if t["name"] == TASK_NAME else ""
        print(f"    - name={t['name']:<12} type={t['type']} "
              f"interval={t.get('interval_sec')}{mark}")
    assert sched.unschedule() is True
    print("  已注销（unschedule=True），演示结束")


def main() -> None:
    tmp_dir = tempfile.mkdtemp(prefix="evolution_scheduler_demo_")
    print(f"临时目录: {tmp_dir}")
    svc, audit_path = _build_stack(tmp_dir)
    demo_svc = _DemoService(svc)
    sched = EvolutionScheduler(service=demo_svc, audit_path=audit_path)

    # 阶段 1：dry-run 预演（真实候选筛选）
    preview = sched.run(dry_run=True, trigger="manual")
    assert preview["dry_run"] is True
    assert len(preview["planned_candidates"]) == 2, "应筛出 2 个候选"
    _show_preview(preview)
    assert not Path(audit_path).exists(), "dry_run 不应写审计文件"
    print("\n[dry-run 校验] 零 KPI/零审计/零提交：通过")

    # 阶段 2：正式 run（mock 批次摘要 → 报告 + KPI + 审计）
    report = sched.run(dry_run=False, trigger="manual")
    _show_report(report)
    _show_audit(audit_path)
    _show_kpi()
    assert report["adopted_candidates"] == 1
    assert Path(audit_path).exists(), "正式 run 应写审计文件"
    print("\n[正式 run 校验] 报告摘要 + 采纳率 KPI + 审计落盘：通过")

    # 阶段 3：周级调度注册演示
    _show_schedule(svc, audit_path)

    print("\n=== 演示完成 ===")
    print("  预期：dry-run 筛出 s-cand-1/s-cand-2 两个候选（usage>=10 且 success<0.95）；")
    print("        正式 run 记录采纳率 KPI（1/2）并写审计摘要；")
    print("        周级调度 interval_days=7 注册成功后可定时触发完整 evolve_batch。")


if __name__ == "__main__":
    main()
