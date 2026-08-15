"""云枢学习管线 — 感知侧学习（TASK-06 新颖性感知学习管线）

- novelty_hooks: ChangeDetector 出口学习钩子（分类 → 分级 → 记忆/建议草稿）
- behavior_drift: 行为漂移周级检测调度器（基线跨会话持久化 + 对比）

本包保持轻量：不 import 重型依赖，外部依赖在方法体内延迟导入。
"""

__version__ = "0.1.0"
