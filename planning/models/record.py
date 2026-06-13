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

    记录每个执行步骤的详细信息
    """
    step: int
    task_id: str
    action: Action
    result: ActionResult
    reasoning: str = ""
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
            "observation": self.result.observation,
            "error": self.result.error,
            "reasoning": self.reasoning,
            "timestamp": self.timestamp.isoformat(),
            "duration_ms": self.duration_ms,
        }
