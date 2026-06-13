"""动作数据模型

定义Action、ActionType、ActionResult等核心数据结构
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum


class ActionType(Enum):
    """动作类型"""
    TOOL_CALL = "tool_call"
    LLM_REASONING = "llm_reasoning"
    RESPONSE = "response"
    WAIT = "wait"
    QUERY = "query"


@dataclass
class Action:
    """可执行动作

    代表一个具体的执行动作
    """
    id: str
    tool_name: str = ""
    tool_params: Dict[str, Any] = field(default_factory=dict)
    action_type: ActionType = ActionType.TOOL_CALL
    preconditions: List[str] = field(default_factory=list)
    postconditions: List[str] = field(default_factory=list)
    rollback_action: Optional[str] = None
    description: str = ""
    estimated_duration_ms: int = 1000

    @classmethod
    def tool_action(cls, tool_name: str, params: Dict[str, Any], description: str = "") -> "Action":
        """创建工具调用动作"""
        return cls(
            id=f"action_{tool_name}_{id(params)}",
            tool_name=tool_name,
            tool_params=params,
            action_type=ActionType.TOOL_CALL,
            description=description or f"调用{tool_name}"
        )

    @classmethod
    def llm_action(cls, prompt: str, description: str = "") -> "Action":
        """创建LLM推理动作"""
        return cls(
            id=f"action_llm_{hash(prompt) % 10000}",
            action_type=ActionType.LLM_REASONING,
            tool_params={"prompt": prompt},
            description=description or "LLM推理"
        )

    @classmethod
    def response_action(cls, response: str) -> "Action":
        """创建响应动作"""
        return cls(
            id=f"action_response_{hash(response) % 10000}",
            action_type=ActionType.RESPONSE,
            tool_params={"response": response},
            description="生成回复"
        )


@dataclass
class ActionResult:
    """动作执行结果"""
    success: bool
    output: Any = None
    observation: str = ""
    state_changes: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration_ms: int = 0

    @classmethod
    def success_result(cls, output: Any, observation: str = "", state_changes: List[str] = None) -> "ActionResult":
        """创建成功结果"""
        return cls(
            success=True,
            output=output,
            observation=observation,
            state_changes=state_changes or []
        )

    @classmethod
    def failure_result(cls, error: str) -> "ActionResult":
        """创建失败结果"""
        return cls(
            success=False,
            error=error,
            observation=f"执行失败: {error}"
        )

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "success": self.success,
            "output": str(self.output) if self.output else None,
            "observation": self.observation,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }
