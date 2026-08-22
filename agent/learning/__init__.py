"""云枢学习管线 — 感知侧学习（TASK-06 新颖性感知学习管线）+ 任务3/5 放行与判别 + 任务7 课程难度自适应

- novelty_hooks: ChangeDetector 出口学习钩子（分类 → 分级 → 记忆/建议草稿）
- behavior_drift: 行为漂移周级检测调度器（基线跨会话持久化 + 对比）
- rollout_controller: L2 自进化闭环四态放行框架（dry_run/observe/confirm/rollout，任务3）
- judge_channel: LLM-as-Judge 双假设验证 · Judge dry-run 通道
  （同候选集双通道评估，只写 metrics/审计，零干预任何决策；预算 enforce 前置，任务5）
- curriculum: 课程难度自适应策略（读 KPI#4 复杂度维度 → 路由概率调整建议；
  默认关闭 LEARNING_CURRICULUM_ENABLED=false，观察模式——任务7）

本包保持轻量：不 import 重型依赖，外部依赖在方法体内延迟导入。
"""

__version__ = "0.1.0"
