"""学习有效性度量查询 API — TASK-03 / 任务2（触发监控）/ 任务4（护栏状态）

只读接口：
- GET /api/learning/metrics            返回 LearningMetrics.get_snapshot() JSON
                                        （7 项 KPI + 近 7 日趋势；任务2 起
                                         evolution_adoption_rate 含 insufficient_data/
                                         min_candidates 扩展字段，既有字段不变）
- GET /api/learning/metrics/weekly     周级滚动统计（7 项 KPI 周序列，任务2）
- GET /api/learning/metrics/trigger    报告 §3.3/§5.2 五条触发条件逐条计算（任务2，
                                        可传 replay_coverage / audit_ok 等外部输入）
- GET /api/learning/guards             护栏 G1-G5 状态聚合（任务4，只读；
                                        数据源 agent/learning/guard_status.py）

全部纯只读：不触发任何写操作，不影响主链路；异常 → 500 明确失败（只读接口契约）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from agent.learning_metrics import get_learning_metrics

logger = logging.getLogger(__name__)

learning_metrics_bp = Blueprint('learning_metrics', __name__)


def get_learning_metrics_snapshot(metrics: Optional[Any] = None, days: int = 7) -> Dict[str, Any]:
    """只读聚合视图（供 API / 集成测试复用），metrics 可注入便于测试隔离"""
    metrics = metrics if metrics is not None else get_learning_metrics()
    return metrics.get_snapshot(days=days)


def get_learning_metrics_weekly(metrics: Optional[Any] = None,
                                weeks: int = 8) -> Dict[str, Any]:
    """周级滚动统计（任务2；metrics 可注入便于测试隔离）"""
    from datetime import datetime
    metrics = metrics if metrics is not None else get_learning_metrics()
    weeks = max(1, min(52, int(weeks)))
    rows = metrics.get_weekly_kpis(weeks=weeks)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "requested_weeks": weeks,
        "weekly": rows,
        "count": len(rows),
    }


def get_learning_metrics_trigger(metrics: Optional[Any] = None,
                                 weeks: int = 4,
                                 **kwargs) -> Dict[str, Any]:
    """触发条件逐条计算（任务2；metrics 可注入便于测试隔离）"""
    metrics = metrics if metrics is not None else get_learning_metrics()
    weeks = max(1, min(26, int(weeks)))
    return metrics.evaluate_trigger_conditions(weeks=weeks, **kwargs)


def _int_arg(name: str, default: int) -> int:
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


def _float_arg(name: str, default: Optional[float]) -> Optional[float]:
    raw = request.args.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _bool_arg(name: str, default: Optional[bool]) -> Optional[bool]:
    raw = request.args.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _safe_jsonify(handler) -> Any:
    try:
        return jsonify(handler())
    except Exception as exc:  # 只读接口异常 → 500 明确失败，不影响主链路
        logger.warning("[学习度量API] 查询生成失败: %s", exc)
        return jsonify({"error": "learning_metrics_unavailable"}), 500


@learning_metrics_bp.route('/api/learning/metrics', methods=['GET'])
def learning_metrics_view():
    return _safe_jsonify(lambda: get_learning_metrics_snapshot())


@learning_metrics_bp.route('/api/learning/metrics/weekly', methods=['GET'])
def learning_metrics_weekly_view():
    weeks = _int_arg("weeks", 8)
    return _safe_jsonify(lambda: get_learning_metrics_weekly(weeks=weeks))


@learning_metrics_bp.route('/api/learning/metrics/trigger', methods=['GET'])
def learning_metrics_trigger_view():
    weeks = _int_arg("weeks", 4)
    replay_coverage = _float_arg("replay_coverage", None)
    audit_ok = _bool_arg("audit_ok", None)
    g1_g5_ready = _bool_arg("g1_g5_ready", None)
    decision_approval = _bool_arg("decision_approval", None)
    if replay_coverage is None:
        replay_coverage = _auto_replay_coverage()
    return _safe_jsonify(lambda: get_learning_metrics_trigger(
        weeks=weeks,
        replay_coverage=replay_coverage,
        audit_ok=audit_ok,
        g1_g5_ready=g1_g5_ready,
        decision_approval=decision_approval,
    ))


def _auto_replay_coverage() -> Optional[float]:
    """未显式传 replay_coverage 时，从回放审计自动计算（任务6 遗留项接线）。

    审计缺失/无 manifest → None → TC-4 sandbox_replay 保持 unknown（绝不伪造）；
    任何异常静默回退 None，不影响只读端点契约。
    """
    try:
        from agent.learning.replay import compute_replay_coverage
        return compute_replay_coverage()
    except Exception as exc:  # noqa: BLE001 自动注入失败 → 显式 None，不掩盖
        logger.debug("[学习度量API] replay_coverage 自动计算失败（回退 None）: %s", exc)
        return None


@learning_metrics_bp.route('/api/learning/guards', methods=['GET'])
def learning_guards_view():
    """护栏 G1-G5 状态聚合（任务4，只读）

    数据源 agent/learning/guard_status.get_guard_status()；
    与 metrics 端点同契约：纯只读，异常 → 500 明确失败。
    """
    def _handler():
        from agent.learning.guard_status import get_guard_status
        return get_guard_status()
    return _safe_jsonify(_handler)


__all__ = [
    "learning_metrics_bp",
    "get_learning_metrics_snapshot",
    "get_learning_metrics_weekly",
    "get_learning_metrics_trigger",
]
