"""工作流生成器 — 把学习者产出的骨架转换为可执行工作流

职责:
    1. 验证步骤的工具是否在云枢工具系统中可用 (软校验，缺失仅警告)
    2. 补全缺失的 timeout / condition 等字段
    3. 注入标准化的错误处理步骤 (最后一步可选)
    4. 计算初始优先级 (基于工具稀有度、步骤数)
    5. 注册到仓库 + 索引
"""

from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional

from .models import LearnedWorkflow, WorkflowStep
from .exceptions import WorkflowLearningError, ErrorCode
from .observability import logger, emit_metric, track_event, traced_action
from .repository import WorkflowRepository
from .matcher import WorkflowMatcher


class WorkflowGenerator:
    """工作流生成器"""

    def __init__(self, repo: WorkflowRepository, matcher: WorkflowMatcher,
                 *, tool_validator: Optional[Callable[[str], bool]] = None):
        self._repo = repo
        self._matcher = matcher
        self._tool_validator = tool_validator

    def generate_and_store(self, wf: LearnedWorkflow) -> LearnedWorkflow:
        """补全字段、验证、注册并持久化"""
        with traced_action("wf_generate", workflow_id=wf.id) as ctx:
            # 1) 工具校验 (软校验)
            missing_tools = self._validate_tools(wf.steps)
            if missing_tools:
                logger.warning("[Generator] 工作流 %s 引用了未知工具: %s",
                               wf.id, missing_tools)
                # 不阻塞生成，但记入 description
                wf.description += (
                    f"\n[警告] 引用了未知工具: {missing_tools}，"
                    "可能无法执行。"
                )

            # 2) 补全字段
            for step in wf.steps:
                if not step.timeout_ms:
                    step.timeout_ms = 30000

            # 3) 计算初始优先级
            wf.priority = self._compute_priority(wf)

            # 4) 持久化 + 索引
            existing = self._repo.get(wf.id)
            if existing:
                # 合并统计 (保留已有 success/failure 计数)
                wf.success_count = existing.success_count
                wf.failure_count = existing.failure_count
                wf.confidence = existing.confidence
                wf.created_at = existing.created_at
            wf.touch()
            self._repo.upsert(wf)
            self._matcher.register(wf)

            ctx["steps"] = len(wf.steps)
            ctx["priority"] = wf.priority
            track_event("wf_generated", {
                "workflow_id": wf.id, "steps": len(wf.steps),
            })
            emit_metric("yunshu_wf_generated_total",
                        labels={"success": "true"}, kind="counter")
            logger.info("[Generator] 工作流已生成并注册: %s (%d 步)",
                        wf.id, len(wf.steps))
            return wf

    # ─── 内部 ───

    def _validate_tools(self, steps: List[WorkflowStep]) -> List[str]:
        """返回未通过校验的工具名列表"""
        if not self._tool_validator:
            return []
        missing: List[str] = []
        for step in steps:
            try:
                if not self._tool_validator(step.tool_name):
                    missing.append(step.tool_name)
            except Exception:  # noqa: BLE001
                missing.append(step.tool_name)
        return list(set(missing))

    @staticmethod
    def _compute_priority(wf: LearnedWorkflow) -> int:
        """计算初始优先级

        策略:
            - 步骤数少 → 优先级高 (简单工作流更通用)
            - 工具稀有度高 → 优先级高 (独占工作流)
            - 基础 50，调整范围 ±30
        """
        base = 50
        # 步骤数影响: 1-3 步 +20, 4-6 步 +0, 7+ 步 -10
        n_steps = len(wf.steps)
        if n_steps <= 3:
            base += 20
        elif n_steps <= 6:
            base += 0
        else:
            base -= 10
        # 触发模式多 → 通用性强 → 优先级高
        if len(wf.trigger_patterns) >= 3:
            base += 10
        return max(0, min(100, base))
