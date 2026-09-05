"""工作流学习数据模型"""

from __future__ import annotations
import enum
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict


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
    """工作流步骤 — 一个工具调用（或一个 LLM 决策步骤）

    默认语义是"本地工具调用"（免 LLM，DAG 执行）。
    当 need_llm=True 时，本步由注入的 llm_step_runner 执行（步骤级 LLM 混合）：
    prompt_template 描述要让 LLM 做什么，可引用 $input/$prev_output/$step.<n>.output。
    """
    step_id: str = Field(..., description="步骤ID (在 workflow 内唯一)")
    tool_name: str = Field(..., description="工具名；need_llm=True 时可为空")
    params_template: Dict[str, Any] = Field(
        default_factory=dict,
        description="参数模板 (支持 $input / $prev_output / $step.<n>.output / $param.<key>)"
    )
    output_key: str = Field("", description="本步输出在上下文中的键名")
    output_schema: Optional[Dict[str, Any]] = Field(
        None, description="黑板写入时的 json-schema 子集校验 (None 不校验)")
    condition: Optional[str] = Field(
        None, description="执行条件 (简化 JS 表达式，如 '$prev_output.includes(\"yes\")')"
    )
    description: str = ""
    # 【工作流技能 vs 工作流】步骤级 LLM 混合：
    #   False（默认）= 本地工具步骤（免 LLM）；True = 本步调 LLM 决策/生成。
    #   资产类型 workflow_type="toolchain"（工作流技能）禁止出现 need_llm=True 步骤。
    need_llm: bool = False
    prompt_template: str = Field(
        "", description="need_llm=True 时，本步要 LLM 执行的指令模板"
        "（支持 $input/$prev_output/$step.<n>.output 引用）")
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
    # 【工作流技能 vs 工作流】资产类型声明（定义时显式指定，替代纯按结构自动判）：
    #   "toolchain" — 工作流技能：纯工具链，免 LLM 0-Token（强制 DAG，
    #                 校验禁 need_llm 步骤）
    #   "hybrid"    — 工作流：可含 need_llm 步骤（步骤级 LLM 混合），
    #                 或超过 DAG 上限时整条走 Agent 模式
    #   默认 "toolchain"（向后兼容：既有 workflow 都是纯工具链）
    workflow_type: str = Field(
        "toolchain", description="toolchain（免 LLM 工作流技能）| hybrid（可含 LLM 步骤的工作流）")
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

    @field_validator("workflow_type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in ("toolchain", "hybrid"):
            raise ValueError(
                f"workflow_type 必须是 toolchain|hybrid (got: {v})")
        return v

    @model_validator(mode="after")
    def _check_toolchain_no_llm_steps(self):
        """工作流技能（toolchain）必须是纯工具链：禁止 need_llm 步骤。"""
        if self.workflow_type == "toolchain":
            for s in self.steps or []:
                if getattr(s, "need_llm", False):
                    raise ValueError(
                        f"toolchain 工作流禁含 LLM 步骤 (step={s.step_id})；"
                        "需要 LLM 步骤请声明 workflow_type='hybrid'")
        return self

    def touch(self) -> None:
        self.updated_at = datetime.now().isoformat()

    def record_execution(self, success: bool) -> None:
        """记录一次执行结果，更新置信度（从当前值出发，单调演进）。

        冷启动死锁修复：旧实现对总次数做对数饱和重算，首次成功会把
        confidence 从初值 0.4 砸到 ~0.18（factor=1-e^-0.2），再次跌破
        matcher.min_confidence 门槛 → 刚学到的工作流执行一次后反而
        永远匹配不到。现改为相对调整：
            - 成功：+0.1（上限 0.99），执行次数越多步长越小的对数衰减
              （第 1 次 +0.1、第 5 次约 +0.05……），保持"多次成功→高置信"
              语义且单调不减；
            - 失败：×0.9 温和下调（下限 0.05），仍保留失败惩罚；
            保证 confidence 与匹配门槛（0.4）解耦，冷启动可执行、演化不断档。
        """
        if success:
            self.success_count += 1
            # 步长随总次数对数衰减：1 次 +0.10，5 次 +0.05，10 次 +0.034…
            import math
            step = 0.1 * math.exp(-(self.success_count - 1) / 5.0)
            self.confidence = min(0.99, self.confidence + step)
        else:
            self.failure_count += 1
            self.confidence = max(0.05, self.confidence * 0.9)
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
