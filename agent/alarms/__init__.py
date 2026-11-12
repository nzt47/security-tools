"""报警器（2026-11-11）：运行期问题 → 事件台账 → 顶栏铃铛 / 消息中心。

模块：
- `store`：事件库（去重、恢复、已读、确认、静默；持久化在 data/alarms/）
- `collector`：一轮采集（健康探针降级 / 单位看板 warn / 审计链断点；数据源不可用不报警）
- `api`：`/api/alarms/*` 只读查询 + 已读/确认/静默

【与审批收件箱的边界】审批 = 要动作（单次消费 + 审计链）；报警 = 要知悉（可静默、可恢复）。
两者不合并，也不互相写对方的状态。
"""
from agent.alarms.store import (  # noqa: F401
    Alarm,
    AlarmStore,
    get_store,
    reset_store,
)

__all__ = ["Alarm", "AlarmStore", "get_store", "reset_store"]

# ── 包自声明：「报警采集线程」是一个功能单位（2026-11-12）──────────────
# 只写字面量（启动期不 import collector）；probe 见 agent/alarms/collector.py::unit_probe
MODULE_UNITS = [
    {
        "id": "alarms.collector",
        "label": "报警采集线程",
        "family": "ops",
        "probe": "agent.alarms.collector:unit_probe",
        "description": "六条事件源每 60s 一轮：已读源数 / 上报 / 恢复 / 停摆检测",
    },
]
