"""行为漂移周级检测调度器（TASK-06 Step 3）

行为基线跨会话持久化 + 周级对比: 偏差超阈值 → behavior_drift NoveltyEvent
（记录 + 草稿，同 Step 2 分级钩子；仅 DRAFT，不注册技能）。

【不易】约束:
    - 不修改 behavior_sensor 6 维度采集逻辑（仅新增 capture/save/list/load 持久化方法）
    - 默认观察模式: sensor_learning.enabled=false → schedule() 返回 disabled，零副作用
    - 基线文件路径与格式写死（供后续任务对接）:
        ~/.Yunshu/baselines/behavior_<周一日期>.json
        {"week": ..., "captured_at": ..., "metrics": {指标名: 数值}}
    - 产物仅: 基线文件 + 记忆记录 + 建议草稿（绝不注册技能）
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from agent.learning.novelty_hooks import (
    _baseline_retention_weeks,
    _drift_threshold,
    _sensor_learning_enabled,
    _write_draft,
    handle_novelty_event,
)
from sensor.behavior_sensor import DEFAULT_BASELINE_DIR, ActivityBehaviorSensor
from sensor.novelty import compute_drift_score, detect_behavior_drift

logger = logging.getLogger(__name__)

TASK_NAME = "行为漂移检测"
DEFAULT_INTERVAL_HOURS = 24 * 7  # 周级（每周一份基线，滚动保留）


class BehaviorDriftScheduler:
    """周级行为漂移检测调度器（默认关闭 = 观察模式）"""

    def __init__(self, *, sensor: Optional[Any] = None,
                 baseline_dir: Optional[str] = None,
                 draft_dir: Optional[str] = None,
                 audit_path: Optional[str] = None,
                 memory_dir: Optional[str] = None):
        """Args:
            sensor: ActivityBehaviorSensor 实例（None 时懒加载）。
            baseline_dir: 行为基线目录（None → 默认 ~/.Yunshu/baselines）。
            draft_dir / audit_path / memory_dir: 沉淀路径（None → 按配置/默认）。
        """
        self._sensor = sensor
        self._baseline_dir = baseline_dir
        self._draft_dir = draft_dir
        self._audit_path = audit_path
        self._memory_dir = memory_dir
        self._interval_hours = DEFAULT_INTERVAL_HOURS
        self._scheduled_task_id: Optional[str] = None

    def _get_sensor(self) -> ActivityBehaviorSensor:
        if self._sensor is None:
            self._sensor = ActivityBehaviorSensor()
        return self._sensor

    # ─── 调度注册 ───

    def schedule(self, *, interval_hours: Optional[int] = None) -> Dict[str, Any]:
        """注册周级漂移检测任务（默认关闭 = 观察模式）。"""
        self._interval_hours = interval_hours or DEFAULT_INTERVAL_HOURS
        if not _sensor_learning_enabled():
            logger.warning(
                "[BehaviorDrift] 漂移检测默认关闭（观察模式）；"
                "开启: config learning.sensor_learning.enabled=true "
                "/ .env SENSOR_LEARNING_ENABLED=true")
            return {
                "status": "disabled",
                "interval_hours": self._interval_hours,
                "note": "learning.sensor_learning.enabled=false，漂移检测默认关闭（观察模式）",
            }
        try:
            from agent.task_scheduler import get_scheduler
            sched = get_scheduler()
        except Exception as e:  # noqa: BLE001 调度器不可用
            logger.error("[BehaviorDrift] 调度器不可用: %s", e)
            return {"status": "error", "error": str(e)}
        sched.add_interval_task(TASK_NAME, func=self.run,
                                interval_seconds=self._interval_hours * 3600)
        self._scheduled_task_id = (
            sched.tasks[-1]["task_id"] if sched.tasks else "behavior_drift")
        logger.info("[BehaviorDrift] 周级漂移检测已注册 interval_hours=%d task_id=%s",
                    self._interval_hours, self._scheduled_task_id)
        return {
            "status": "scheduled",
            "task_id": self._scheduled_task_id,
            "interval_hours": self._interval_hours,
            "note": "周级对比最近两份行为基线，漂移超阈值产 behavior_drift（记忆+草稿，不注册技能）",
        }

    def unschedule(self) -> bool:
        """注销漂移检测任务（按固定任务名定位，可跨实例）。"""
        try:
            from agent.task_scheduler import get_scheduler
            sched = get_scheduler()
        except Exception as e:  # noqa: BLE001
            logger.error("[BehaviorDrift] 调度注销失败: %s", e)
            return False
        for task in sched.tasks:
            if task.get("name") == TASK_NAME:
                removed = sched.remove_task(task["task_id"])
                self._scheduled_task_id = None
                return removed
        return False

    # ─── 定时执行 ───

    def run(self) -> Dict[str, Any]:
        """执行一轮漂移检测: 采集→保存当前周基线→对比上一份→产事件（记录+草稿）。"""
        sensor = self._get_sensor()
        base_dir = self._baseline_dir or DEFAULT_BASELINE_DIR

        # 1. 采集并保存当前周基线（超保留期滚动清理）
        try:
            saved = sensor.save_baseline(base_dir, _baseline_retention_weeks())
        except Exception as e:  # noqa: BLE001 基线保存失败不阻断调度线程
            logger.error("[BehaviorDrift] 基线保存失败: %s", e)
            return {"status": "error", "error": str(e), "stage": "save_baseline"}

        # 2. 对比最近两份基线
        baselines = sensor.list_baselines(base_dir)
        if len(baselines) < 2:
            logger.info("[BehaviorDrift] 基线不足两份（当前 %d 份），跳过漂移检测",
                        len(baselines))
            return {"status": "skipped", "reason": "insufficient_baselines",
                    "baseline_count": len(baselines), "week": saved.get("week")}
        previous = baselines[-2]["baseline"]
        current = baselines[-1]["baseline"]
        threshold = _drift_threshold()
        event = detect_behavior_drift(previous, current, threshold)
        if event is None:
            score = compute_drift_score(
                (previous or {}).get("metrics") or {},
                (current or {}).get("metrics") or {})
            logger.info("[BehaviorDrift] 漂移未超阈值 score=%.4f < threshold=%.2f，不产事件",
                        score, threshold)
            return {"status": "no_drift", "week": saved.get("week"),
                    "drift_score": round(score, 4), "threshold": threshold}

        # 3. 产事件: 记录（中置信 → 记忆）+ 草稿（验收"记录 + 草稿"；仅 DRAFT）
        handle_novelty_event(event, memory_dir=self._memory_dir,
                             draft_dir=self._draft_dir, audit_path=self._audit_path)
        _write_draft(event, self._draft_dir)
        logger.warning("[BehaviorDrift] 检测到行为漂移 score=%s ≥ threshold=%.2f",
                       event.detail.get("drift_score"), threshold)
        return {"status": "drift_detected", "week": saved.get("week"),
                "drift_score": event.detail.get("drift_score"),
                "threshold": threshold,
                "event": event.to_dict()}


__all__ = ["BehaviorDriftScheduler", "TASK_NAME", "DEFAULT_INTERVAL_HOURS"]
