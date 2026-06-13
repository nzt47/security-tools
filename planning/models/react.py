"""ReAct循环数据模型

定义ReActStep、ReActResult、ThoughtResult等数据结构
"""

from dataclasses import dataclass, field
from typing import List, Optional, Any, Dict
from datetime import datetime

from .action import Action, ActionResult


@dataclass
class ThoughtResult:
    """思考结果"""
    reasoning: str
    action_type: str
    action: Optional[Action] = None
    confidence: float = 0.5
    result: Optional[str] = None
    next_steps: List[str] = field(default_factory=list)


@dataclass
class ReActStep:
    """ReAct执行步骤"""
    iteration: int
    thought: str
    action: str
    observation: str
    success: bool
    timestamp: datetime = field(default_factory=datetime.now)
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "iteration": self.iteration,
            "thought": self.thought,
            "action": self.action,
            "observation": self.observation,
            "success": self.success,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ReActResult:
    """ReAct执行结果"""
    success: bool
    result: Any
    steps: List[ReActStep]
    iterations: int
    total_duration_ms: int = 0
    error: Optional[str] = None
    final_state: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "result": str(self.result) if self.result else None,
            "iterations": self.iterations,
            "steps_count": len(self.steps),
            "total_duration_ms": self.total_duration_ms,
            "error": self.error,
        }

    @property
    def summary(self) -> str:
        """生成执行摘要"""
        if self.success:
            return f"成功完成,耗时{self.iterations}步"
        return f"失败({self.error}),执行{self.iterations}步"
