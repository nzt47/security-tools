"""工作流学习数据模型"""

from __future__ import annotations
import enum
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, ConfigDict


class WorkflowStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


# 工具调用步骤的参数模板支持引用:
#   $input            — 用户原始输入
#   $prev_output      — 上一步的输出
#   $step.<n>.output  — 第 n 步的输出 (0-indexed)
#   $param.<name>     — 调用方传入的参数
class WorkflowStep(BaseModel):
    """工作流步骤 — 一个工具调用"""
    step_id: str = Field(..., description="步骤ID (在 workflow 内唯一)")
    tool_name: str = Field(..., description="工具名")
    params_template: Dict[str, Any] = Field(
        default_factory=dict,
        description="参数模板 (支持 $input / $prev_output / $step.<n>.output / $param.<key>)"
    )
    output_key: str = Field("", description="本步输出在上下文中的键名")
    condition: Optional[str] = Field(
        None, description="执行条件 (简化 JS 表达式，如 '$prev_output.includes(\"yes\")')"
    )
    description: str = ""
    timeout_ms: int = Field(30000, ge=100, le=600000)

    model_config = ConfigDict(use_enum_values=True)


class LearnedWorkflow(BaseModel):
    """学习到的工作流"""
    id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    task_signature: str = Field(..., description="任务规范化签名 (用于匹配)")
    trigger_patterns: List[str] = Field(default_factory=list,
                                        description="触发模式 (关键词或正则)")
    steps: List[WorkflowStep] = Field(default_factory=list)
    expected_output_pattern: str = Field("", description="预期输出特征 (正则)")
    source_session_id: str = Field("", description="来源会话ID")
    source_user_input: str = Field("", description="来源用户输入 (用于匹配回溯)")

    # 统计
    success_count: int = 0
    failure_count: int = 0
    confidence: float = Field(0.5, ge=0.0, le=1.0,
                              description="置信度 (基于成功率与次数)")
    priority: int = Field(50, ge=0, le=100,
                          description="优先级 (高者优先匹配)")
    status: WorkflowStatus = WorkflowStatus.ACTIVE
    enabled: bool = True

    # 元数据
    tags: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    last_used_at: Optional[str] = None

    # 转换为 Skill 的状态跟踪（避免重复转换）
    # 空字符串表示未转换；非空时为对应的 skill_id
    converted_to_skill_id: str = ""

    model_config = ConfigDict(use_enum_values=True)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9_\-]*$", v):
            raise ValueError(
                f"工作流ID必须为 kebab_case (got: {v})")
        return v

    def touch(self) -> None:
        self.updated_at = datetime.now().isoformat()

    def record_execution(self, success: bool) -> None:
        """记录一次执行结果，更新置信度"""
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        total = self.success_count + self.failure_count
        # 置信度 = 成功率 * 衰减因子(次数越多越稳定)
        if total > 0:
            rate = self.success_count / total
            # 用对数饱和函数: 5次时 0.78，10次时 0.93，20次时 0.99
            import math
            factor = 1.0 - math.exp(-total / 5.0)
            self.confidence = rate * factor
        self.last_used_at = datetime.now().isoformat()
        self.touch()


class LearningRecord(BaseModel):
    """一次 LLM 交互的学习记录"""
    session_id: str
    user_input: str
    tool_calls: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="LLM 调用过的工具列表 [{name, params, output, success}]"
    )
    final_output: str = ""
    success: bool = True
    duration_ms: float = 0.0
    learned_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    model_config = ConfigDict(use_enum_values=True)


class WorkflowExecutionResult(BaseModel):
    """工作流执行结果"""
    matched: bool = False
    workflow_id: str = ""
    workflow_name: str = ""
    similarity: float = 0.0
    confidence: float = 0.0
    output: Any = None
    steps_executed: int = 0
    success: bool = False
    skipped_llm: bool = False  # 是否跳过了 LLM 调用
    execution_time_ms: float = 0.0
    error: Optional[str] = None
