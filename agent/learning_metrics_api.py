"""学习有效性度量查询 API — TASK-03

只读接口：GET /api/learning/metrics 返回 LearningMetrics.get_snapshot() JSON
（7 项 KPI + 近 7 日趋势）。纯只读，不触发任何写操作，不影响主链路。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify

from agent.learning_metrics import get_learning_metrics

logger = logging.getLogger(__name__)

learning_metrics_bp = Blueprint('learning_metrics', __name__)


def get_learning_metrics_snapshot(metrics: Optional[Any] = None, days: int = 7) -> Dict[str, Any]:
    """只读聚合视图（供 API / 集成测试复用），metrics 可注入便于测试隔离"""
    metrics = metrics if metrics is not None else get_learning_metrics()
    return metrics.get_snapshot(days=days)


@learning_metrics_bp.route('/api/learning/metrics', methods=['GET'])
def learning_metrics_view():
    try:
        return jsonify(get_learning_metrics_snapshot())
    except Exception as exc:  # 只读接口异常 → 500 明确失败，不影响主链路
        logger.warning("[学习度量API] 快照生成失败: %s", exc)
        return jsonify({"error": "learning_metrics_unavailable"}), 500


__all__ = ["learning_metrics_bp", "get_learning_metrics_snapshot"]
