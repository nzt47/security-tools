"""agent.evolution — 轻量自我进化机制（任务6）

失败案例回流 → 修复策略库 → 运行时注入 → 参数联动。

【不易】约束:
  - 无任何模型微调/训练管线（train/fine_tune/sft 零新增）
  - 策略库只追加不删除（deprecated 标记淘汰）
  - 注入策略必须携带 strategy_id 可追溯（日志 + trace）
"""

from .defect_case import FailureCase, build_failure_case, score_failure_case
from .selector import (
    SAFETY_RED_LINE,
    STATUS_ACTIVE,
    STATUS_DEPRECATED,
    Strategy,
    composite_score,
    generate_candidates,
    generate_llm_candidates,
    select_strategies,
)
from .injector import StrategyInjector, get_injector, get_evolution_config

__all__ = [
    "FailureCase",
    "build_failure_case",
    "score_failure_case",
    "Strategy",
    "SAFETY_RED_LINE",
    "STATUS_ACTIVE",
    "STATUS_DEPRECATED",
    "composite_score",
    "generate_candidates",
    "generate_llm_candidates",
    "select_strategies",
    "StrategyInjector",
    "get_injector",
    "get_evolution_config",
]
