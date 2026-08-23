"""学习触发条件 Prometheus 指标（任务2 触发监控 · 查询层 → 告警引用）

查询层 `LearningMetrics.evaluate_trigger_conditions()`（报告 TASK-08 §3.3/§5.2
五条触发条件逐条计算）完成后，经 `sync_trigger_gauges()` 把命中状态写入
Prometheus gauge，供既有告警设施（monitoring/alerts*/prometheus/rules/*）引用：

    yunshu_learning_trigger_condition{condition="judge_intro", status="hit"} 1

labels 语义：
    condition: judge_intro / solver_enhancement / course_adaptation /
               sandbox_replay / l3_research（报告 §5.2 远期能力）
    status:    hit / not_hit / insufficient_data / unknown
    值:        仅 (实际 status, hit=True) 组合为 1，其余为 0
    → 告警表达式：`yunshu_learning_trigger_condition{status="hit"} == 1`

gauge 在查询层每次计算时刷新（API 触发 / 运维脚本触发 / 周期性任务可选接入）；
Prometheus 抓取周期内保持上次值。"连续 4 周"判定完全由查询层计算，
告警只引用其结果（守"无主观判断型门槛"过程验收项）。

【不易】prometheus_client 不可用 / 重复注册时安全降级（沿用 _safe_gauge 模式，
        _NoopGauge.labels()/set() 均为 no-op）；任何异常静默，不影响主链路。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agent.monitoring.prometheus import _safe_gauge

logger = logging.getLogger(__name__)

# 与 learning_metrics._TRIGGER_CONDITIONS 对齐（避免循环导入，此处显式声明）
_CONDITION_IDS = (
    "judge_intro", "solver_enhancement", "course_adaptation",
    "sandbox_replay", "l3_research",
)
_STATUSES = ("hit", "not_hit", "insufficient_data", "unknown")

yunshu_learning_trigger_condition = _safe_gauge(
    'yunshu_learning_trigger_condition',
    'Learning trigger condition hit status (1=hit, 0=otherwise); '
    'labels: condition (report §5.2 capability), status (hit/not_hit/'
    'insufficient_data/unknown)',
    ['condition', 'status'],
)


def sync_trigger_gauges(result: Dict[str, Any]) -> None:
    """把 evaluate_trigger_conditions() 结果同步到 gauge（幂等；异常静默）

    Args:
        result: evaluate_trigger_conditions() 的返回 dict（含 conditions 节）。
    """
    try:
        conditions = (result or {}).get("conditions", {}) or {}
        for cid in _CONDITION_IDS:
            entry = conditions.get(cid) or {}
            status = str(entry.get("status") or "unknown")
            if status not in _STATUSES:
                status = "unknown"
            hit = bool(entry.get("hit"))
            for s in _STATUSES:
                val = 1.0 if (s == status and hit) else 0.0
                yunshu_learning_trigger_condition.labels(
                    condition=cid, status=s).set(val)
    except Exception:
        logger.debug("[学习触发监控] gauge 同步失败（静默）")


__all__ = [
    "sync_trigger_gauges",
    "yunshu_learning_trigger_condition",
    "_CONDITION_IDS",
    "_STATUSES",
]
