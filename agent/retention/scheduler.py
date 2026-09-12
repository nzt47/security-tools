"""保留策略调度（TASK-S8-01 步骤 3；**默认关闭** + 首跑强制 dry-run）。

【安全底线（与 S7 各调度器同一条）】
    `CP_RETENTION_ENABLED=true`（或 `config.yaml retention.enabled: true`）才注册。
    未开启 → 返回 `{"status": "disabled"}` 并且**不构造调度器、不碰任何文件**。

【首跑强制 dry-run】
    即使运维显式开了 `CP_RETENTION_DRY_RUN=false`，**本进程内的第一次运行仍然是
    dry-run**：先把"将归档哪些类、多少文件、多少字节"打出来，第二次才真正落盘。
    理由：归档是本仓库里少见的"会移动/删除历史数据"的自动动作，
    第一次无人值守就跑真格，等于把"数据挪走了才发现"变成常态。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from agent.retention.archiver import Archiver, RetentionReport
from agent.retention.policy import RetentionPolicy, load_policy

logger = logging.getLogger("agent.retention.scheduler")

#: 调度任务名（`TaskScheduler.add_cron_task` 的 name）
TASK_NAME = "数据保留策略归档"

#: 默认调度：每周日 03:00（`day_of_week` 用 Python weekday 语义，0=周一）
DEFAULT_SCHEDULE = {"day_of_week": 6, "hour": 3, "minute": 0}


def _run_once(archiver: Archiver, *, first: bool) -> RetentionReport:
    """执行一次：`first=True` 时**强制 dry-run**（首跑纪律）。"""
    if first:
        report = archiver.plan()
        report.notes.append(
            "首跑强制 dry-run（TASK-S8-01 §二.2 / 批次总表 §二②）："
            "本次仅列出清单与体积，未落盘、未删除")
        return report
    return archiver.run(confirm=not archiver.policy.dry_run)


def register_retention_job(scheduler: Any = None, *,
                           policy: Optional[RetentionPolicy] = None,
                           enabled: Optional[bool] = None,
                           dry_run: Optional[bool] = None,
                           day_of_week: Optional[int] = None,
                           hour: Optional[int] = None,
                           minute: Optional[int] = None,
                           archiver: Optional[Archiver] = None
                           ) -> Dict[str, Any]:
    """把保留策略归档注册为 `TaskScheduler` 的 cron 任务（**默认关闭**）。

    Args:
        scheduler: `agent.task_scheduler.TaskScheduler`；None 时自行 `get_scheduler()`。
        policy: 策略表（None → `load_policy()`）。
        enabled: 显式开关；None → 读 `CP_RETENTION_ENABLED` / `config.yaml`。
        dry_run: 显式 dry-run；None → 读策略（默认 True）。
        day_of_week/hour/minute: 覆盖调度时刻。
        archiver: 注入归档器（测试用）。

    Returns:
        `{status, ...}`：`disabled` / `scheduled` / `error`（**永不抛**）。
    """
    pol = policy or load_policy()
    if enabled is None:
        enabled = bool(pol.enabled)
    if dry_run is not None:
        pol.dry_run = bool(dry_run)
    if not enabled:
        logger.info("[RetentionScheduler] 未启用（CP_RETENTION_ENABLED / "
                    "config.yaml retention.enabled）")
        return {"status": "disabled",
                "note": "保留策略归档默认关闭（安全底线）；开启："
                        "CP_RETENTION_ENABLED=true",
                "policy": pol.summary()}

    try:
        if scheduler is None:
            from agent.task_scheduler import get_scheduler

            scheduler = get_scheduler()
    except Exception as e:  # noqa: BLE001 调度器不可用不阻断主流程
        logger.error("[RetentionScheduler] 调度器不可用：%s", e)
        return {"status": "error", "error": str(e)}

    schedule = dict(DEFAULT_SCHEDULE)
    schedule.update(getattr(pol, "schedule", {}) or {})
    if day_of_week is not None:
        schedule["day_of_week"] = int(day_of_week)
    if hour is not None:
        schedule["hour"] = int(hour)
    if minute is not None:
        schedule["minute"] = int(minute)

    box = archiver or Archiver(pol)
    state = {"runs": 0}

    def _tick() -> Dict[str, Any]:
        """调度任务入口：**首跑强制 dry-run**；失败不抛（调度线程不得挂掉）。"""
        first = state["runs"] == 0
        state["runs"] += 1
        try:
            report = _run_once(box, first=first)
            logger.info("[RetentionScheduler] 第 %d 次运行：%s", state["runs"],
                        " | ".join(report.summary_lines()))
            return {"status": "ok", "dry_run": report.dry_run,
                    "first_run_forced_dry_run": first,
                    "totals": report.totals, "audit": report.audit}
        except Exception as e:  # noqa: BLE001 单次失败不得让调度线程挂掉
            logger.error("[RetentionScheduler] 运行失败：%s", e, exc_info=True)
            return {"status": "error", "error": str(e)}

    try:
        kwargs: Dict[str, Any] = {"name": TASK_NAME, "func": _tick,
                                  "hour": schedule["hour"],
                                  "minute": schedule["minute"]}
        # add_cron_task 的 day_of_week 支持 None（每日）；仅当有值时才传
        if schedule.get("day_of_week") is not None:
            kwargs["day_of_week"] = schedule["day_of_week"]
        scheduler.add_cron_task(**kwargs)
    except Exception as e:  # noqa: BLE001 注册失败不阻断
        logger.warning("[RetentionScheduler] 注册失败：%s", e)
        return {"status": "error", "error": str(e)}

    task_id = ""
    for task in reversed(getattr(scheduler, "tasks", []) or []):
        if task.get("name") == TASK_NAME:
            task_id = str(task.get("task_id") or "")
            break
    return {
        "status": "scheduled",
        "task_id": task_id,
        "schedule": schedule,
        "dry_run": bool(pol.dry_run),
        "first_run_forced_dry_run": True,
        "delete_source": bool(pol.delete_source),
        "archive_dir": pol.archive_dir,
        "classes": len(list(pol.selected())),
        "note": ("每次执行入链式审计（action=retention.run，记条数与体积）；"
                 "默认只归档不删除；首跑强制 dry-run"),
    }


__all__ = ["TASK_NAME", "DEFAULT_SCHEDULE", "register_retention_job"]
