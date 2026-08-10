"""规划引擎核心

协调各模块工作
"""

import asyncio
import json
import logging
import os
from typing import Dict, Any, Optional, List
from datetime import datetime

from .models import Plan, PlanState
from .models.action import Action, ActionResult
from .models.record import ExecutionRecord
from .models.react import ReActResult
from .decomposer import TaskDecomposer
from .executor import PlanExecutor, ToolRegistry
from .reflector import Reflector
from .state_machine import PlanStateMachine, InvalidStateTransitionError
from .react import ReActLoop

logger = logging.getLogger(__name__)


class PlanningError(Exception):
    """规划引擎异常"""
    pass


class ChatResult:
    """对话结果"""

    def __init__(self, response: str, plan: Plan = None, react_result: ReActResult = None, used_planning: bool = False):
        self.response = response
        self.plan = plan
        self.react_result = react_result
        self.used_planning = used_planning
        self.timestamp = datetime.now()

    def to_dict(self) -> dict:
        return {
            "response": self.response,
            "used_planning": self.used_planning,
            "plan_id": self.plan.id if self.plan else None,
            "iterations": self.react_result.iterations if self.react_result else None,
            "success": self.react_result.success if self.react_result else False,
            "timestamp": self.timestamp.isoformat()
        }


class PlanningCore:
    """
    规划引擎核心

    协调任务分解、执行和反思的完整流程
    """

    def __init__(self, llm_service=None, tool_registry: ToolRegistry = None, memory_manager=None, config: Dict = None):
        """
        初始化规划引擎

        Args:
            llm_service: LLM服务
            tool_registry: 工具注册表
            memory_manager: 记忆管理器
            config: 配置
        """
        self.llm = llm_service
        self.memory = memory_manager
        self.config = config or {}

        # D16 修复：规划可观测指标计数器（total_plans/success/iterations/cost）
        # 埋点：execute_plan 开始 +1；收尾 COMPLETED +1 success；迭代数 = plan.current_step；
        # total_cost 预留 token 计费接入点（当前 0.0）。
        self._metrics_total_plans = 0
        self._metrics_success_count = 0
        self._metrics_total_iterations = 0
        self._metrics_total_cost = 0.0

        logger.info("="*60)
        logger.info("开始初始化规划引擎核心...")
        logger.info(f"LLM服务: {'已配置' if llm_service else '未配置 (将使用规则模式)'}")
        logger.info(f"工具注册表: {'外部提供' if tool_registry else '新建空注册表'}")
        logger.info(f"记忆管理器: {'已配置' if memory_manager else '未配置'}")

        self.tool_registry = tool_registry if tool_registry else ToolRegistry()
        logger.info(f"工具注册表初始化完成，当前工具数: {len(self.tool_registry.list_tools())}")

        decomposer_config = self.config.get("decomposer", {})
        self.decomposer = TaskDecomposer(llm_service, decomposer_config)
        logger.info(f"任务分解器初始化完成，最大子任务数: {self.decomposer.max_subtasks}")

        executor_config = self.config.get("executor", {})
        self.executor = PlanExecutor(
            self.tool_registry,
            llm_service,
            max_retries=executor_config.get("max_retries", 3),
            config=executor_config
        )
        logger.info(f"执行引擎初始化完成，最大重试次数: {self.executor.max_retries}")

        reflector_config = self.config.get("reflector", {})
        self.reflector = Reflector(llm_service, memory_manager, reflector_config)
        logger.info("反思引擎初始化完成")

        self.state_machine = PlanStateMachine()
        logger.info("状态机初始化完成")

        # 注入状态机：计划收尾状态变更（EXECUTING->COMPLETED/FAILED）走状态机，
        # 触发钩子并记录转换历史（修复 C1 短路缺陷）
        self.executor.state_machine = self.state_machine
        logger.info("执行引擎已注入状态机")

        react_config = self.config.get("react", {})
        self.react_loop = ReActLoop(
            self,
            self.reflector,
            max_iterations=react_config.get("max_iterations", 10),
            config=react_config
        )
        logger.info(f"ReAct循环初始化完成，最大迭代次数: {self.react_loop.max_iterations}")

        persist_config = self.config.get("planning", {}) or {}
        self.persist_dir = persist_config.get("persist_dir") \
            or self.config.get("persist_dir") or os.path.join("data", "plans")
        os.makedirs(self.persist_dir, exist_ok=True)

        self._active_plans: Dict[str, Plan] = self._load_plans_from_disk()
        logger.info(f"已从检查点目录恢复 {len(self._active_plans)} 个未完成计划: {self.persist_dir}")
        self.complexity_threshold = self.config.get("complexity_threshold", 0.5)
        logger.info(f"复杂度阈值: {self.complexity_threshold}")

        logger.info("="*60)
        logger.info("✅ 规划引擎核心初始化完成")
        logger.info("="*60)

    def save_plan_checkpoint(self, plan: Plan) -> str:
        """保存计划检查点到磁盘（D9 修复：持久化恢复）"""
        path = os.path.join(self.persist_dir, f"{plan.id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(plan.to_dict(), f, ensure_ascii=False, indent=2)
        return path

    def _load_plans_from_disk(self) -> Dict[str, Plan]:
        """从检查点目录恢复未完成计划（D9 修复）"""
        plans: Dict[str, Plan] = {}
        if not os.path.isdir(self.persist_dir):
            return plans
        for fname in os.listdir(self.persist_dir):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.persist_dir, fname), encoding="utf-8") as f:
                    data = json.load(f)
                plan = Plan.from_dict(data)
                if plan.state in (PlanState.INIT, PlanState.DECOMPOSING,
                                  PlanState.READY, PlanState.EXECUTING, PlanState.PAUSED):
                    plans[plan.id] = plan
            except Exception as e:
                logger.warning(f"加载计划检查点失败 {fname}: {e}")
        return plans

    async def plan(self, task: str, context: Dict = None) -> Plan:
        """
        创建执行计划

        Args:
            task: 任务描述
            context: 执行上下文

        Returns:
            分解后的执行计划
        """
        context = context or {}
        logger.info("="*60)
        logger.info("🔍 [规划引擎] 开始创建执行计划")
        logger.info(f"   任务描述: {task[:100]}{'...' if len(task) > 100 else ''}")
        logger.info(f"   上下文键: {list(context.keys())}")
        logger.info("-"*60)

        try:
            logger.info("📋 步骤1: 调用任务分解器...")
            plan = await self.decomposer.decompose(task, context)

            if plan.state == PlanState.READY:
                logger.info(f"✅ 任务分解成功!")
                logger.info(f"   计划ID: {plan.id}")
                logger.info(f"   子任务数: {len(plan.tasks)}")
                for i, t in enumerate(plan.tasks[:5]):
                    logger.info(f"      子任务{i+1}: {t.description[:50]}...")
                if len(plan.tasks) > 5:
                    logger.info(f"      ... 还有 {len(plan.tasks) - 5} 个子任务")

                self._active_plans[plan.id] = plan
                self.save_plan_checkpoint(plan)
                logger.info(f"✅ 计划已添加到活跃计划列表并保存检查点 (当前活跃计划数: {len(self._active_plans)})")
                logger.info("="*60)
                return plan
            else:
                error_msg = f"任务分解失败: {plan.error}"
                logger.error(f"❌ {error_msg}")
                logger.info("="*60)
                raise PlanningError(error_msg)
        except Exception as e:
            logger.error(f"❌ 创建计划异常: {e}")
            logger.error(f"异常类型: {type(e).__name__}")
            import traceback
            logger.error(f"堆栈跟踪:\n{traceback.format_exc()}")
            logger.info("="*60)
            raise PlanningError(f"创建计划失败: {e}")

    async def execute_plan(self, plan: Plan) -> Plan:
        """
        执行计划

        Args:
            plan: 要执行的计划

        Returns:
            执行完成的计划
        """
        if plan.id not in self._active_plans:
            self._active_plans[plan.id] = plan
            logger.info(f"计划 {plan.id} 已添加到活跃计划列表")

        # D16 修复：规划计数埋点（每次 execute_plan 记 1 次）
        self._metrics_total_plans += 1

        logger.info("="*60)
        logger.info("🚀 [规划引擎] 开始执行计划")
        logger.info(f"   计划ID: {plan.id}")
        logger.info(f"   任务描述: {plan.original_task[:80]}...")
        logger.info(f"   任务数: {len(plan.tasks)}")
        logger.info(f"   当前状态: {plan.state.value}")
        logger.info("-"*60)

        try:
            logger.info("📊 步骤1: 状态转换 INIT -> EXECUTING")
            self.state_machine.transition(plan, PlanState.EXECUTING, "开始执行")
            logger.info(f"   ✅ 状态已转换到: {plan.state.value}")

            logger.info("⚙️ 步骤2: 调用执行引擎...")
            plan = await self.executor.execute_plan(plan)
            logger.info(f"   ✅ 执行引擎返回，任务完成数: {sum(1 for t in plan.tasks if t.status.value == 'completed')}/{len(plan.tasks)}")

            if self.reflector:
                try:
                    logger.info("🧠 步骤3: 执行计划反思...")
                    await self.reflector.plan_reflect(plan)
                    logger.info("   ✅ 反思完成")
                except Exception as e:
                    logger.warning(f"   ⚠️ 反思执行失败: {e}")

            logger.info("📋 步骤4: 校验最终状态（已由执行器经状态机完成收尾）...")
            # 最终状态已由 executor._finalize_state 经状态机完成 EXECUTING -> COMPLETED/FAILED。
            # 边界 #3：取消竞态下 executor.cancel_plan 异步 task（直接赋值）可能将状态改为 CANCELLED，
            # 属合法终态，仅记录日志不抛错；其余非预期状态仍保持严格断言防回归。
            if plan.state == PlanState.CANCELLED:
                logger.warning("计划执行收尾后状态为 CANCELLED（取消竞态），跳过最终状态校验")
            elif plan.state not in (PlanState.COMPLETED, PlanState.FAILED):
                raise AssertionError(f"计划执行后状态异常: {plan.state.value}")

            logger.info(f"📈 执行进度: {plan.progress():.1%}")
            logger.info("="*60)
            logger.info("✅ 计划执行完成")
            logger.info("="*60)

            # D16 修复：收尾埋点（COMPLETED 计成功；迭代数 = 计划步数）
            self._metrics_total_iterations += plan.current_step
            if plan.state == PlanState.COMPLETED:
                self._metrics_success_count += 1

            return plan

        except InvalidStateTransitionError as e:
            logger.error(f"❌ 状态转换错误: {e}")
            logger.error(f"   当前状态: {plan.state.value}")
            plan.error = str(e)
            # 边界 #6：恢复路径统一经 _finalize_state（状态机优先、非法降级），
            # 不再直接赋值绕过状态机；若计划已取消则保留 CANCELLED（边界 #2 同逻辑）
            self.executor._finalize_state(plan, PlanState.FAILED, reason="状态转换失败，计划标记失败")
            logger.info("="*60)
            return plan

    async def chat(self, message: str, context: Dict = None) -> ChatResult:
        """
        对话式任务处理

        智能选择直接执行或启用规划

        Args:
            message: 用户消息
            context: 执行上下文

        Returns:
            ChatResult: 处理结果
        """
        context = context or {}

        logger.info("="*60)
        logger.info("💬 [规划引擎] 收到对话请求")
        logger.info(f"   用户消息: {message[:100]}{'...' if len(message) > 100 else ''}")
        logger.info(f"   上下文键: {list(context.keys())}")
        logger.info("-"*60)

        if self._needs_planning(message):
            logger.info("🤔 任务复杂度评估: 需要规划")
            logger.info("✅ 决策: 启用规划模式")
            logger.info("="*60)
            return await self._plan_chat(message, context)
        else:
            logger.info("🤔 任务复杂度评估: 简单任务")
            logger.info("✅ 决策: 直接执行模式")
            logger.info("="*60)
            return await self._direct_chat(message, context)

    def _needs_planning(self, message: str) -> bool:
        """判断是否需要规划"""
        complex_indicators = [
            "帮我完成", "帮我创建", "帮我分析",
            "帮我构建", "流程", "系统",
            "第一步", "第二步", "然后"
        ]
        complex_count = sum(1 for indicator in complex_indicators if indicator in message)

        action_keywords = ["检查", "分析", "创建", "生成", "整理", "监控"]
        action_count = sum(1 for keyword in action_keywords if keyword in message.lower())

        needs = complex_count >= 1 or action_count >= 2

        logger.info(f"   复杂指示器匹配: {complex_count} 个")
        logger.info(f"   动作关键词匹配: {action_count} 个")
        logger.info(f"   阈值: 复杂>=1 或 动作>=2")
        logger.info(f"   需要规划: {needs}")

        return needs

    async def _plan_chat(self, message: str, context: Dict) -> ChatResult:
        """规划模式处理复杂任务"""
        logger.info("🧠 [规划模式] 开始处理复杂任务")
        logger.info("-"*60)

        try:
            logger.info("🔄 步骤1: 启动ReAct循环...")
            react_result = await self.react_loop.run(message, context)

            # D4 修复：ReAct 步骤写入统一执行记录（与 execute_plan 路径共享）
            for step in react_result.steps:
                self.executor.execution_history.append(ExecutionRecord(
                    step=step.iteration,
                    task_id=f"react_{step.iteration}",
                    action=Action.llm_action(prompt=step.thought, description=step.action),
                    result=ActionResult(success=step.success, output=step.observation, observation=step.observation),
                    reasoning=step.thought,
                ))

            # D4 边界：一步 finish 完成（无中间步骤）也计入统一执行记录，保持双路径记录完整
            if not react_result.steps and react_result.success:
                self.executor.execution_history.append(ExecutionRecord(
                    step=0,
                    task_id="react_finish",
                    action=Action.llm_action(prompt="", description="finish"),
                    result=ActionResult(success=True, output=react_result.result, observation=react_result.result),
                    reasoning="",
                ))

            logger.info(f"📊 步骤2: ReAct循环执行结果")
            logger.info(f"   成功: {react_result.success}")
            logger.info(f"   迭代次数: {react_result.iterations}")
            logger.info(f"   执行时长: {react_result.total_duration_ms}ms")
            if react_result.error:
                logger.info(f"   错误: {react_result.error}")

            if react_result.success:
                logger.info("✅ 任务执行成功，生成响应...")
                response = str(react_result.result)
            else:
                logger.warning("⚠️ 任务执行遇到问题，生成错误响应...")
                response = f"我遇到了一些问题: {react_result.error}"

            if react_result.iterations > 1:
                response += f"\n\n(经过 {react_result.iterations} 步处理)"
                logger.info(f"   已添加迭代信息到响应")

            logger.info("✅ 规划模式处理完成")
            logger.info("="*60)

            return ChatResult(
                response=response,
                react_result=react_result,
                used_planning=True
            )
        except Exception as e:
            logger.error(f"❌ 规划处理失败: {e}")
            logger.error(f"异常类型: {type(e).__name__}")
            import traceback
            logger.error(f"堆栈跟踪:\n{traceback.format_exc()}")
            logger.info("="*60)
            return ChatResult(response=f"抱歉,处理这个任务时遇到了问题: {e}")

    async def _direct_chat(self, message: str, context: Dict) -> ChatResult:
        """直接模式处理简单任务"""
        if not self.llm:
            return ChatResult(response="抱歉,当前无法处理请求(LLM服务不可用)")

        try:
            prompt = self._build_direct_prompt(message, context)
            response = await self.llm.chat([{"role": "user", "content": prompt}])
            return ChatResult(response=response)
        except Exception as e:
            logger.error(f"直接对话失败: {e}")
            return ChatResult(response=f"处理失败: {e}")

    def _build_direct_prompt(self, message: str, context: Dict) -> str:
        """构建直接对话提示词"""
        parts = [f"用户: {message}"]

        if context.get("body_status"):
            parts.append(f"\n当前身体状态:\n{context['body_status']}")

        if context.get("memory_context"):
            parts.append(f"\n记忆上下文:\n{context['memory_context']}")

        parts.append("\n请以云枢的身份回复用户")

        return "\n".join(parts)

    def cancel_plan(self, plan_id: str) -> bool:
        """
        取消计划

        Args:
            plan_id: 计划ID

        Returns:
            是否取消成功
        """
        plan = self._active_plans.get(plan_id)
        if not plan:
            logger.warning(f"计划不存在: {plan_id}")
            return False

        try:
            self.state_machine.transition(plan, PlanState.CANCELLED, "用户取消")
            asyncio.create_task(self.executor.cancel_plan(plan))
            return True
        except InvalidStateTransitionError:
            return False

    def get_plan_status(self, plan_id: str) -> Optional[Dict]:
        """获取计划状态"""
        plan = self._active_plans.get(plan_id)
        if not plan:
            return None

        return {
            "id": plan.id,
            "state": plan.state.value,
            "state_description": self.state_machine.get_state_description(plan.state),
            "progress": f"{plan.progress():.1%}",
            "current_step": plan.current_step,
            "total_tasks": len(plan.tasks),
            "completed_tasks": sum(1 for t in plan.tasks if t.status.value == "completed"),
            "error": plan.error
        }

    def get_active_plans(self) -> List[Dict]:
        """获取所有活跃计划"""
        return [
            self.get_plan_status(pid)
            for pid in self._active_plans
        ]

    def register_tool(self, name: str, func, schema: Dict = None):
        """注册工具到注册表"""
        self.tool_registry.register(name, func, schema)
        logger.info(f"工具已注册到规划引擎: {name}")

    def get_stats(self) -> Dict:
        """获取统计信息（D16 修复：暴露规划成功率/迭代数/成本可观测指标）"""
        success_rate = (
            round(self._metrics_success_count / self._metrics_total_plans, 4)
            if self._metrics_total_plans > 0 else 0.0
        )
        return {
            "active_plans": len(self._active_plans),
            "executor_history": len(self.executor.execution_history),
            "learning_stats": self.reflector.get_learning_stats() if self.reflector else {},
            "registered_tools": self.tool_registry.list_tools(),
            # D16 新增：规划可观测指标
            "total_plans": self._metrics_total_plans,
            "success_count": self._metrics_success_count,
            "success_rate": success_rate,
            "total_iterations": self._metrics_total_iterations,
            "total_cost": self._metrics_total_cost,
        }
