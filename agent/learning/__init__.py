"""云枢学习管线 — 感知侧学习（TASK-06 新颖性感知学习管线）

- novelty_hooks: ChangeDetector 出口学习钩子（分类 → 分级 → 记忆/建议草稿）
- behavior_drift: 行为漂移周级检测调度器（基线跨会话持久化 + 对比）

本包保持轻量：不 import 重型依赖，外部依赖在方法体内延迟导入。
"""

__version__ = "0.1.0"

# ── 包自声明：「感知周期采集」是一个功能单位（2026-11-12）──────────────
# 声明格式与登记逻辑见 agent/modules_discovery.py / agent/monitoring/declared_units.py。
# 只写**字面量**：启动期不 import 本包；probe 在单位看板真正调它时才 import。
# 它让"新功能自动进单位看板"有一条款 —— 忘了往手写清单加一行时，这里就是兜底。
MODULE_UNITS = [
    {
        "id": "perception.cycle",
        "label": "感知周期采集",
        "family": "perception",
        "probe": "agent.learning.perception_cycle:unit_probe",
        "description": "文件事件 drain → file_change；快照 diff 贵/便宜分频；状态落 perception_status.json",
    },
]
