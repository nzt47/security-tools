"""执行记录数据模型

定义ExecutionRecord等数据结构
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .action import Action, ActionResult


@dataclass
class ExecutionRecord:
    """执行记录

    记录每个执行步骤的详细信息（阶段 2 扩展：thought/observation 独立字段）

    【不易】新字段带默认值且 to_dict() 仅追加新键（thought），observation 键
    语义升级为"显式字段优先、回落 result.observation"——未显式赋值时行为与
    重构前完全一致，调用方无感知。
    """
    step: int
    task_id: str
    action: Action
    result: ActionResult
    reasoning: str = ""
    thought: str = ""
    observation: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    duration_ms: int = 0

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "step": self.step,
            "task_id": self.task_id,
            "action": self.action.description,
            "action_type": self.action.action_type.value,
            "success": self.result.success,
            "observation": self.observation if self.observation else self.result.observation,
            "error": self.result.error,
            "reasoning": self.reasoning,
            "thought": self.thought,
            "timestamp": self.timestamp.isoformat(),
            "duration_ms": self.duration_ms,
        }
