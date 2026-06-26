"""规划器（增强版）— 将目标分解为子任务 DAG，支持计划确认和回退

增强功能：
- 计划确认环节（复杂任务必须先输出执行方案）
- 计划预览和人工确认
- 计划失败后的回退机制
- 结构化日志（trace_id, module_name, action, duration_ms）

设计文档：P2 云枢架构升级 — Plan Mode Enforcement (5.1)
"""

import json
import time
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Callable
from enum import Enum

from agent.task_planner.enhanced_dag import (
    EnhancedDAG,
    EnhancedTaskNode,
    PlanStatus,
)

logger = logging.getLogger(__name__)


class TaskComplexity(Enum):
    """任务复杂度"""
    TRIVIAL = "trivial"      # 简单任务（无需确认）
    SIMPLE = "simple"       # 普通任务（自动执行）
    MODERATE = "moderate"   # 中等复杂（建议确认）
    COMPLEX = "complex"     # 复杂任务（强制确认）


@dataclass
class PlanPreview:
    """计划预览

    用于在执行前向用户展示计划全貌。
    """
    plan_id: str
    goal: str
    complexity: TaskComplexity
    estimated_duration: float  # 总预估时间（秒）
    task_count: int
    tasks: list[dict]  # 任务摘要列表
    requires_confirmation: bool
    warnings: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "complexity": self.complexity.value,
            "estimated_duration": self.estimated_duration,
            "task_count": self.task_count,
            "tasks": self.tasks,
            "requires_confirmation": self.requires_confirmation,
            "warnings": self.warnings,
            "created_at": self.created_at,
        }

    def get_summary_text(self, use_emoji: bool = True) -> str:
        """生成计划摘要文本（用于展示）

        Args:
            use_emoji: 是否使用 emoji（Windows 终端可能不支持）
        """
        if use_emoji:
            lines = [
                f"📋 计划ID: {self.plan_id}",
                f"🎯 目标: {self.goal}",
                f"📊 复杂度: {self.complexity.value}",
                f"⏱️ 预估时间: {self.estimated_duration:.1f}秒",
                f"📝 任务数: {self.task_count}",
                "",
                "📌 执行步骤:",
            ]
        else:
            lines = [
                f"[计划ID] {self.plan_id}",
                f"[目标] {self.goal}",
                f"[复杂度] {self.complexity.value}",
                f"[预估时间] {self.estimated_duration:.1f}秒",
                f"[任务数] {self.task_count}",
                "",
                "[执行步骤]",
            ]

        for i, task in enumerate(self.tasks, 1):
            confirm_mark = " [需确认]" if task.get("requires_confirmation") else ""
            lines.append(f"  {i}. {task['description']}{confirm_mark}")

        if self.warnings:
            if use_emoji:
                lines.append("")
                lines.append("⚠️ 警告:")
            else:
                lines.append("")
                lines.append("[警告]")
            for w in self.warnings:
                lines.append(f"  - {w}")

        return "\n".join(lines)


@dataclass
class ConfirmationResult:
    """确认结果

    Attributes:
        plan_id: 计划 ID
        confirmed: 是否已确认
        confirmed_by: 确认人
        confirmed_tasks: 已确认的任务列表
        rejected_tasks: 被拒绝的任务列表
        message: 确认消息
        timestamp: 确认时间
    """
    plan_id: str
    confirmed: bool
    confirmed_by: str = "user"
    confirmed_tasks: list[str] = field(default_factory=list)
    rejected_tasks: list[str] = field(default_factory=list)
    message: str = ""
    timestamp: float = field(default_factory=time.time)


class EnhancedTaskPlanner:
    """增强版任务规划器

    增强功能：
    - 复杂度评估（决定是否需要确认）
    - 计划预览生成
    - 人工确认流程
    - 执行过程中的状态追踪
    - 失败回退机制
    - 结构化日志

    用法:
        planner = EnhancedTaskPlanner()
        
        # 生成计划
        plan = await planner.create_plan("帮我写一个 Web 服务器")
        
        # 预览计划
        preview = planner.get_preview(plan)
        print(preview.get_summary_text())
        
        # 确认计划
        result = await planner.confirm_plan(plan.plan_id, confirmed_by="user")
        
        # 执行计划
        executor = planner.create_executor()
        results = await executor.execute(plan, orchestrator)
        
        # 处理失败
        if plan.has_failed():
            rollback_plan = planner.create_rollback_plan(plan)
    """

    # 复杂度阈值
    COMPLEXITY_KEYWORDS = {
        TaskComplexity.TRIVIAL: ["查", "问", "告诉我", "what is", "how to"],
        TaskComplexity.SIMPLE: ["写", "创建", "生成", "帮我"],
        TaskComplexity.MODERATE: ["分析", "设计", "实现", "开发"],
        TaskComplexity.COMPLEX: ["架构", "系统", "平台", "重构", "迁移", "分布式", "设计一个"],
    }

    # 需要确认的复杂度
    CONFIRM_REQUIRED_COMPLEXITY = TaskComplexity.MODERATE

    def __init__(
        self,
        require_confirmation_threshold: TaskComplexity = TaskComplexity.MODERATE,
        max_plan_age_seconds: float = 300,  # 5 分钟
    ):
        """
        Args:
            require_confirmation_threshold: 需要确认的复杂度阈值
            max_plan_age_seconds: 计划最大有效期
        """
        self._require_confirmation_threshold = require_confirmation_threshold
        self._max_plan_age = max_plan_age_seconds
        self._plans: dict[str, EnhancedDAG] = {}
        self._confirmations: dict[str, ConfirmationResult] = {}

        logger.info("[EnhancedTaskPlanner] 初始化完成: confirm_threshold=%s",
                   require_confirmation_threshold.value)

    # ── 计划创建 ──

    async def create_plan(
        self,
        goal: str,
        task_definitions: Optional[list[dict]] = None,
        context: Optional[dict] = None,
    ) -> EnhancedDAG:
        """创建执行计划

        Args:
            goal: 目标描述
            task_definitions: 任务定义列表（可选）
            context: 上下文信息

        Returns:
            EnhancedDAG 执行计划
        """
        start_time = time.time()
        trace_id = f"plan_{uuid.uuid4().hex[:12]}"

        # 结构化日志 - 计划创建开始
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "task_planner",
            "action": "plan_create_start",
            "goal_preview": goal[:100],
            "goal_length": len(goal),
            "has_task_definitions": task_definitions is not None,
            "duration_ms": 0,
            "timestamp": start_time,
        }, ensure_ascii=False))

        # 创建 DAG
        dag = EnhancedDAG()
        dag.plan_id = trace_id
        dag.status = PlanStatus.DRAFT

        # 评估复杂度
        complexity = self._evaluate_complexity(goal)

        # 结构化日志 - 复杂度评估结果
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "task_planner",
            "action": "complexity_evaluation",
            "goal_preview": goal[:50],
            "complexity": complexity.value,
            "requires_confirmation": complexity.value >= self._require_confirmation_threshold.value,
            "duration_ms": round((time.time() - start_time) * 1000, 2),
            "timestamp": time.time(),
        }, ensure_ascii=False))

        # 如果没有提供任务定义，自动生成
        if not task_definitions:
            decompose_start = time.time()
            task_definitions = self._decompose_goal(goal, complexity)
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "task_planner",
                "action": "goal_decomposition",
                "complexity": complexity.value,
                "task_count": len(task_definitions),
                "duration_ms": round((time.time() - decompose_start) * 1000, 2),
                "timestamp": time.time(),
            }, ensure_ascii=False))

        # 添加任务节点
        add_start = time.time()
        for i, task_def in enumerate(task_definitions):
            node = EnhancedTaskNode(
                id=task_def.get("id", f"step_{i}"),
                description=task_def.get("description", ""),
                depends_on=task_def.get("depends_on", []),
                estimated_duration=task_def.get("estimated_duration", 10.0),
                requires_confirmation=task_def.get("requires_confirmation", False),
                rollback_action=task_def.get("rollback_action"),
            )
            dag.add_task(node)

        logger.debug(json.dumps({
            "trace_id": trace_id,
            "module_name": "task_planner",
            "action": "tasks_added",
            "task_count": len(task_definitions),
            "duration_ms": round((time.time() - add_start) * 1000, 2),
            "timestamp": time.time(),
        }, ensure_ascii=False))

        # 检测循环依赖
        cycle_start = time.time()
        cycles = dag.detect_cycles()
        if cycles:
            logger.error(json.dumps({
                "trace_id": trace_id,
                "module_name": "task_planner",
                "action": "cycle_detected",
                "cycle_count": len(cycles),
                "cycles": cycles,
                "duration_ms": round((time.time() - cycle_start) * 1000, 2),
                "timestamp": time.time(),
            }, ensure_ascii=False))
            raise ValueError(f"计划存在循环依赖: {cycles}")

        # 复杂度警告
        warnings = []
        if complexity == TaskComplexity.COMPLEX:
            warnings.append("这是一个复杂任务，建议仔细审查执行步骤")
        if len(task_definitions) > 10:
            warnings.append(f"任务数量较多（{len(task_definitions)}），执行时间可能较长")

        total_duration_ms = (time.time() - start_time) * 1000

        # 结构化日志 - 计划创建完成
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "task_planner",
            "action": "plan_create_complete",
            "complexity": complexity.value,
            "task_count": len(task_definitions),
            "requires_confirmation": complexity.value >= self._require_confirmation_threshold.value,
            "warnings_count": len(warnings),
            "warnings": warnings,
            "status": "draft",
            "duration_ms": round(total_duration_ms, 2),
            "timestamp": time.time(),
        }, ensure_ascii=False))

        # 保存计划
        self._plans[trace_id] = dag

        return dag

    def _evaluate_complexity(self, goal: str) -> TaskComplexity:
        """评估任务复杂度

        从高复杂度到低复杂度检查，确保更具体的关键词优先匹配。
        """
        goal_lower = goal.lower()

        # 按复杂度从高到低检查（确保更具体的关键词优先）
        complexity_order = [
            TaskComplexity.COMPLEX,
            TaskComplexity.MODERATE,
            TaskComplexity.SIMPLE,
            TaskComplexity.TRIVIAL,
        ]

        for complexity in complexity_order:
            keywords = self.COMPLEXITY_KEYWORDS.get(complexity, [])
            for keyword in keywords:
                if keyword in goal_lower:
                    return complexity

        return TaskComplexity.SIMPLE

    def _decompose_goal(
        self,
        goal: str,
        complexity: TaskComplexity,
    ) -> list[dict]:
        """将目标分解为任务列表"""
        base_steps = {
            TaskComplexity.TRIVIAL: ["理解需求", "执行查询", "返回结果"],
            TaskComplexity.SIMPLE: ["理解需求", "准备数据", "执行任务", "验证结果"],
            TaskComplexity.MODERATE: ["需求分析", "方案设计", "分步实现", "测试验证", "部署交付"],
            TaskComplexity.COMPLEX: [
                "需求调研", "技术选型", "架构设计",
                "模块开发", "集成测试", "性能优化",
                "部署上线", "监控告警", "文档编写"
            ],
        }

        steps = base_steps.get(complexity, base_steps[TaskComplexity.SIMPLE])

        # 为复杂任务添加更多细节
        task_defs = []
        prev_id = None

        for i, step in enumerate(steps):
            task_id = f"step_{i}"
            depends_on = [prev_id] if prev_id else []

            # 复杂任务需要确认
            requires_confirmation = complexity.value in ("moderate", "complex") and i > 0

            task_defs.append({
                "id": task_id,
                "description": step,
                "depends_on": depends_on,
                "estimated_duration": 10.0 * (i + 1),  # 越往后预估时间越长
                "requires_confirmation": requires_confirmation,
                "rollback_action": f"回退到 '{step}' 之前的状态" if i > 0 else None,
            })

            prev_id = task_id

        return task_defs

    # ── 计划预览 ──

    def get_preview(self, plan: EnhancedDAG, goal: str = "") -> PlanPreview:
        """生成计划预览

        Args:
            plan: 执行计划
            goal: 目标描述

        Returns:
            PlanPreview 计划预览
        """
        tasks = []
        for node in plan._nodes.values():
            tasks.append({
                "id": node.id,
                "description": node.description,
                "depends_on": node.depends_on,
                "estimated_duration": node.estimated_duration,
                "requires_confirmation": node.requires_confirmation,
                "status": node.status,
            })

        # 评估复杂度
        complexity = self._evaluate_complexity(goal) if goal else TaskComplexity.MODERATE

        # 是否需要确认
        requires_confirmation = (
            complexity.value >= self._require_confirmation_threshold.value
            or plan.has_unconfirmed()
        )

        # 计算总预估时间
        total_duration = sum(t["estimated_duration"] for t in tasks)

        return PlanPreview(
            plan_id=plan.plan_id,
            goal=goal,
            complexity=complexity,
            estimated_duration=total_duration,
            task_count=len(tasks),
            tasks=tasks,
            requires_confirmation=requires_confirmation,
        )

    # ── 计划确认 ──

    async def confirm_plan(
        self,
        plan_id: str,
        confirmed_by: str = "user",
        task_confirmations: Optional[dict[str, bool]] = None,
    ) -> ConfirmationResult:
        """确认计划

        Args:
            plan_id: 计划 ID
            confirmed_by: 确认人
            task_confirmations: 任务级别的确认字典 {task_id: confirmed}

        Returns:
            ConfirmationResult 确认结果
        """
        start_time = time.time()
        trace_id = f"confirm_{uuid.uuid4().hex[:12]}"

        # 结构化日志 - 确认开始
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "task_planner",
            "action": "plan_confirm_start",
            "plan_id": plan_id,
            "confirmed_by": confirmed_by,
            "has_task_level_confirmations": task_confirmations is not None,
            "duration_ms": 0,
            "timestamp": start_time,
        }, ensure_ascii=False))

        plan = self._plans.get(plan_id)
        if not plan:
            logger.error(json.dumps({
                "trace_id": trace_id,
                "module_name": "task_planner",
                "action": "plan_confirm_error",
                "plan_id": plan_id,
                "error": "plan_not_found",
                "duration_ms": round((time.time() - start_time) * 1000, 2),
                "timestamp": time.time(),
            }, ensure_ascii=False))
            return ConfirmationResult(
                plan_id=plan_id,
                confirmed=False,
                message=f"计划不存在: {plan_id}",
            )

        # 检查计划状态
        logger.debug(json.dumps({
            "trace_id": trace_id,
            "module_name": "task_planner",
            "action": "plan_status_check",
            "plan_id": plan_id,
            "current_status": plan.status.value,
            "total_tasks": len(plan._nodes),
            "unconfirmed_tasks": len(plan.get_unconfirmed_tasks()),
            "duration_ms": round((time.time() - start_time) * 1000, 2),
            "timestamp": time.time(),
        }, ensure_ascii=False))

        # 检查计划是否过期
        age_seconds = time.time() - plan._created_at
        if age_seconds > self._max_plan_age:
            logger.warning(json.dumps({
                "trace_id": trace_id,
                "module_name": "task_planner",
                "action": "plan_expired",
                "plan_id": plan_id,
                "age_seconds": round(age_seconds, 2),
                "max_age_seconds": self._max_plan_age,
                "duration_ms": round((time.time() - start_time) * 1000, 2),
                "timestamp": time.time(),
            }, ensure_ascii=False))
            return ConfirmationResult(
                plan_id=plan_id,
                confirmed=False,
                message="计划已过期，请重新创建",
            )

        # 确认所有需要确认的任务
        confirmed_tasks = []
        rejected_tasks = []
        auto_confirmed = 0
        user_confirmed = 0

        for node in plan._nodes.values():
            task_id = node.id

            if not node.requires_confirmation:
                # 不需要确认的任务自动通过
                node.status = "confirmed"
                node.confirmed_by = "system"
                node.confirmed_at = time.time()
                confirmed_tasks.append(task_id)
                auto_confirmed += 1

                logger.debug(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "task_planner",
                    "action": "task_auto_confirmed",
                    "plan_id": plan_id,
                    "task_id": task_id,
                    "task_description": node.description[:50],
                    "confirmed_by": "system",
                    "duration_ms": round((time.time() - start_time) * 1000, 2),
                    "timestamp": time.time(),
                }, ensure_ascii=False))

            elif task_confirmations and task_id in task_confirmations:
                # 任务级别确认
                if task_confirmations[task_id]:
                    plan.confirm_task(task_id, confirmed_by)
                    confirmed_tasks.append(task_id)
                    user_confirmed += 1

                    logger.info(json.dumps({
                        "trace_id": trace_id,
                        "module_name": "task_planner",
                        "action": "task_confirmed",
                        "plan_id": plan_id,
                        "task_id": task_id,
                        "task_description": node.description[:50],
                        "confirmed_by": confirmed_by,
                        "duration_ms": round((time.time() - start_time) * 1000, 2),
                        "timestamp": time.time(),
                    }, ensure_ascii=False))
                else:
                    rejected_tasks.append(task_id)

                    logger.info(json.dumps({
                        "trace_id": trace_id,
                        "module_name": "task_planner",
                        "action": "task_rejected",
                        "plan_id": plan_id,
                        "task_id": task_id,
                        "task_description": node.description[:50],
                        "rejected_by": confirmed_by,
                        "duration_ms": round((time.time() - start_time) * 1000, 2),
                        "timestamp": time.time(),
                    }, ensure_ascii=False))

            elif node.status != "confirmed":
                # 整体确认
                plan.confirm_task(task_id, confirmed_by)
                confirmed_tasks.append(task_id)
                user_confirmed += 1

                logger.info(json.dumps({
                    "trace_id": trace_id,
                    "module_name": "task_planner",
                    "action": "task_bulk_confirmed",
                    "plan_id": plan_id,
                    "task_id": task_id,
                    "task_description": node.description[:50],
                    "confirmed_by": confirmed_by,
                    "duration_ms": round((time.time() - start_time) * 1000, 2),
                    "timestamp": time.time(),
                }, ensure_ascii=False))

        # 更新计划状态
        final_status = "partial" if plan.has_unconfirmed() else "full"
        if not plan.has_unconfirmed():
            plan.status = PlanStatus.CONFIRMED
            final_status = "full"
        else:
            final_status = "partial"

        total_duration_ms = (time.time() - start_time) * 1000

        # 结构化日志 - 确认完成
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "task_planner",
            "action": "plan_confirm_complete",
            "plan_id": plan_id,
            "confirmed_by": confirmed_by,
            "confirmation_status": final_status,
            "total_tasks": len(plan._nodes),
            "confirmed_count": len(confirmed_tasks),
            "rejected_count": len(rejected_tasks),
            "auto_confirmed": auto_confirmed,
            "user_confirmed": user_confirmed,
            "unconfirmed_count": len(plan.get_unconfirmed_tasks()),
            "duration_ms": round(total_duration_ms, 2),
            "timestamp": time.time(),
        }, ensure_ascii=False))

        result = ConfirmationResult(
            plan_id=plan_id,
            confirmed=len(rejected_tasks) == 0,
            confirmed_by=confirmed_by,
            confirmed_tasks=confirmed_tasks,
            rejected_tasks=rejected_tasks,
            message="计划已确认" if not rejected_tasks else f"部分任务被拒绝: {rejected_tasks}",
        )

        self._confirmations[plan_id] = result

        return result

    async def reject_plan(
        self,
        plan_id: str,
        reason: str = "",
    ) -> ConfirmationResult:
        """拒绝计划

        Args:
            plan_id: 计划 ID
            reason: 拒绝原因

        Returns:
            ConfirmationResult 确认结果
        """
        plan = self._plans.get(plan_id)
        if plan:
            plan.status = PlanStatus.CANCELLED

        return ConfirmationResult(
            plan_id=plan_id,
            confirmed=False,
            message=f"计划被拒绝: {reason}" if reason else "计划被拒绝",
        )

    # ── 计划执行状态 ──

    def get_plan_status(self, plan_id: str) -> Optional[dict]:
        """获取计划状态"""
        plan = self._plans.get(plan_id)
        if not plan:
            return None
        return plan.get_plan_summary()

    def is_plan_ready(self, plan_id: str) -> bool:
        """检查计划是否就绪（已确认且无循环依赖）"""
        plan = self._plans.get(plan_id)
        if not plan:
            return False
        return plan.status == PlanStatus.CONFIRMED and not plan.detect_cycles()

    # ── 回退机制 ──

    def create_rollback_plan(self, failed_plan: EnhancedDAG) -> Optional[EnhancedDAG]:
        """创建回退计划

        当计划执行失败时，生成回退计划。

        Args:
            failed_plan: 失败的计划

        Returns:
            回退计划或 None
        """
        start_time = time.time()
        trace_id = f"rollback_{uuid.uuid4().hex[:12]}"

        # 结构化日志 - 回退计划创建开始
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "task_planner",
            "action": "rollback_plan_start",
            "original_plan_id": failed_plan.plan_id,
            "original_plan_status": failed_plan.status.value,
            "duration_ms": 0,
            "timestamp": start_time,
        }, ensure_ascii=False))

        # 找到失败的任务
        failed_tasks = [n for n in failed_plan._nodes.values() if n.status == "failed"]

        if not failed_tasks:
            logger.warning(json.dumps({
                "trace_id": trace_id,
                "module_name": "task_planner",
                "action": "rollback_plan_no_failed",
                "original_plan_id": failed_plan.plan_id,
                "warning": "no_failed_tasks_found",
                "duration_ms": round((time.time() - start_time) * 1000, 2),
                "timestamp": time.time(),
            }, ensure_ascii=False))
            return None

        # 记录失败任务详情
        failed_task_ids = [t.id for t in failed_tasks]
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "task_planner",
            "action": "rollback_failed_tasks_identified",
            "original_plan_id": failed_plan.plan_id,
            "failed_task_count": len(failed_tasks),
            "failed_task_ids": failed_task_ids,
            "duration_ms": round((time.time() - start_time) * 1000, 2),
            "timestamp": time.time(),
        }, ensure_ascii=False))

        # 创建回退计划
        rollback_plan = EnhancedDAG()
        rollback_plan.plan_id = trace_id
        rollback_plan.status = PlanStatus.DRAFT

        # 获取需要回滚的任务
        rollback_path = []
        rollback_details = []

        for failed_task in failed_tasks:
            path = failed_plan.get_rollback_path(failed_task.id)
            rollback_path.extend(path)

            for task_id in path:
                task = failed_plan.get_task(task_id)
                if task:
                    rollback_details.append({
                        "task_id": task_id,
                        "description": task.description,
                        "rollback_action": task.rollback_action,
                    })

        rollback_path = list(set(rollback_path))  # 去重

        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "task_planner",
            "action": "rollback_path_calculated",
            "original_plan_id": failed_plan.plan_id,
            "rollback_task_count": len(rollback_path),
            "rollback_tasks": rollback_path,
            "duration_ms": round((time.time() - start_time) * 1000, 2),
            "timestamp": time.time(),
        }, ensure_ascii=False))

        # 添加回退任务
        rollback_added = 0
        for i, task_id in enumerate(rollback_path):
            original_task = failed_plan.get_task(task_id)
            if not original_task:
                continue

            rollback_task = EnhancedTaskNode(
                id=f"rollback_{i}",
                description=f"回退: {original_task.description}",
                depends_on=[],
                estimated_duration=5.0,
                requires_confirmation=True,
                rollback_action=None,
            )
            rollback_plan.add_task(rollback_task)
            rollback_added += 1

            logger.debug(json.dumps({
                "trace_id": trace_id,
                "module_name": "task_planner",
                "action": "rollback_task_added",
                "rollback_plan_id": trace_id,
                "original_task_id": task_id,
                "original_description": original_task.description,
                "rollback_task_id": f"rollback_{i}",
                "duration_ms": round((time.time() - start_time) * 1000, 2),
                "timestamp": time.time(),
            }, ensure_ascii=False))

        total_duration_ms = (time.time() - start_time) * 1000

        # 结构化日志 - 回退计划创建完成
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "task_planner",
            "action": "rollback_plan_complete",
            "original_plan_id": failed_plan.plan_id,
            "rollback_plan_id": trace_id,
            "rollback_task_count": rollback_added,
            "requires_confirmation": True,
            "duration_ms": round(total_duration_ms, 2),
            "timestamp": time.time(),
        }, ensure_ascii=False))

        # 保存回退计划
        self._plans[trace_id] = rollback_plan

        return rollback_plan

    # ── 统计信息 ──

    def get_stats(self) -> dict:
        """获取统计信息"""
        plan_count = len(self._plans)
        confirmed_count = sum(
            1 for p in self._plans.values()
            if p.status == PlanStatus.CONFIRMED
        )
        failed_count = sum(
            1 for p in self._plans.values()
            if p.status == PlanStatus.FAILED
        )

        return {
            "total_plans": plan_count,
            "confirmed_plans": confirmed_count,
            "failed_plans": failed_count,
            "require_confirmation_threshold": self._require_confirmation_threshold.value,
            "max_plan_age_seconds": self._max_plan_age,
        }
