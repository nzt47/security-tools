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
from .storage import PlanningStorage
from .validator import PlanValidationError, validate_plan, validate_plan_or_raise
from .reflector import Reflector, classify_task
from .state_machine import PlanStateMachine, InvalidStateTransitionError
from .react import ReActLoop
from .metrics import PlanningMetrics
from .summary import build_react_summary, build_react_summary_markdown

logger = logging.getLogger(__name__)


class PlanningError(Exception):
    """规划引擎异常"""
    pass


class ChatResult:
    """对话结果"""

    def __init__(self, response: str, plan: Plan = None, react_result: ReActResult = None,
                 used_planning: bool = False, pending_plan_id: str = None,
                 plan_summary: Optional[Dict] = None):
        self.response = response
        self.plan = plan
        self.react_result = react_result
        self.used_planning = used_planning
        # 阶段 3（D18 恢复语义）：ask_user 暂停时记录挂起的 plan_id（供 resume_plan 恢复）
        self.pending_plan_id = pending_plan_id
        # 阶段 4（D15）：结构化计划摘要（to_dict 追加输出；None 兼容既有调用）
        self.plan_summary = plan_summary
        self.timestamp = datetime.now()

    def to_dict(self) -> dict:
        return {
            "response": self.response,
            "used_planning": self.used_planning,
            "plan_id": self.plan.id if self.plan else None,
            "iterations": self.react_result.iterations if self.react_result else None,
            "success": self.react_result.success if self.react_result else False,
            "timestamp": self.timestamp.isoformat(),
            "plan_summary": self.plan_summary,
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

        # 阶段 4（D16）：规划指标收集器（planning.metrics.enabled 开关，默认 true；
        # 关闭时 PlanningMetrics 全部方法静默跳过，零行为变化）
        self.planning_metrics = PlanningMetrics(
            enabled=self.config.get("metrics", {}).get("enabled", True)
        )

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

        executor_config = dict(self.config.get("executor", {}))
        # 阶段 3（D13/D14）：预算与重规划配置下发——LifecycleManager 将 planning 段整体
        # 传入（顶层即 planning 段），此处把顶层 budget/replan_on_failure/token_price_per_1k
        # 合并进 executor 配置使其生效；显式配置 executor 段时以其覆盖的键为准
        if self.config.get("budget") is not None:
            executor_config["budget"] = self.config["budget"]
        if self.config.get("replan_on_failure") is not None:
            executor_config["replan_on_failure"] = self.config["replan_on_failure"]
        if self.config.get("token_price_per_1k") is not None:
            executor_config["token_price_per_1k"] = self.config["token_price_per_1k"]
        self.executor = PlanExecutor(
            self.tool_registry,
            llm_service,
            max_retries=executor_config.get("max_retries", 3),
            config=executor_config
        )
        # 阶段 3（D14）：注入重规划与失败归因依赖（decomposer 已就绪；reflector 待创建后补注）
        self.executor.decomposer = self.decomposer
        logger.info(f"执行引擎初始化完成，最大重试次数: {self.executor.max_retries}")

        reflector_config = self.config.get("reflector", {})
        self.reflector = Reflector(llm_service, memory_manager, reflector_config)
        # 阶段 3（D14）：失败归因依赖补注（executor.reflector 在 reflector 创建后注入）
        self.executor.reflector = self.reflector
        # 阶段 4（D16/D17）：指标注入 reflector（get_advice_for_task 内部埋点）；
        # 分解器经验注入补注（decomposer 先于 reflector 创建，创建后回填）
        self.reflector.metrics = self.planning_metrics
        self.decomposer.reflector = self.reflector
        logger.info("反思引擎初始化完成")

        self.state_machine = PlanStateMachine()
        logger.info("状态机初始化完成")

        # 注入状态机：计划收尾状态变更（EXECUTING->COMPLETED/FAILED）走状态机，
        # 触发钩子并记录转换历史（修复 C1 短路缺陷）
        self.executor.state_machine = self.state_machine
        logger.info("执行引擎已注入状态机")

        react_config = dict(self.config.get("react", {}))
        # 阶段 3（D13）：预算配置下发——顶层 budget 段合并进 react 配置（ReActLoop 生效）
        if self.config.get("budget") is not None:
            react_config["budget"] = self.config["budget"]
        if self.config.get("token_price_per_1k") is not None:
            react_config["token_price_per_1k"] = self.config["token_price_per_1k"]
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

        # 阶段 2（D9 升级）：存储门面（planning.storage.enabled 可关闭；路径解析
        # 优先级 storage.path > persist_db > persist_dir/plans.db > 默认 ./data/planning/plans.db）
        self.storage_enabled = PlanningStorage.is_enabled(persist_config)
        self.persist_db_path = PlanningStorage.resolve_db_path(persist_config)
        if self.storage_enabled:
            self.db = PlanningStorage(self.persist_db_path)
            self.db.migrate_from_json(self.persist_dir)
            # 执行记录审计埋点：executor 通过属性注入（与 state_machine 注入同模式，不改签名）
            self.executor.persistence = self.db
            logger.info(f"已初始化 SQLite 持久化: {self.persist_db_path}")
        else:
            self.db = None
            self.executor.persistence = None
            logger.info("计划存储已禁用（planning.storage.enabled=false），跳过 SQLite 初始化")

        self._active_plans: Dict[str, Plan] = self._load_plans_from_disk()
        logger.info(f"已恢复 {len(self._active_plans)} 个未完成计划: {self.persist_dir}")
        # D6 统一：与 _needs_planning 的默认判定阈值一致（1.0 = 等价原判定）
        self.complexity_threshold = self.config.get("complexity_threshold", 1.0)
        logger.info(f"复杂度阈值: {self.complexity_threshold}")

        # 阶段 3（D18 恢复语义）：ask_user 等待用户确认超时（秒，默认 300；负数 = 立即超时），
        # 超时自动以"用户未确认"结束；_pending_questions 登记等待中的问题供 resume_plan 恢复
        self.ask_user_timeout = self.config.get("ask_user_timeout_seconds", 300)
        self._pending_questions: Dict[str, Dict[str, Any]] = {}
        logger.info(f"ask_user 等待超时: {self.ask_user_timeout}秒")

        logger.info("="*60)
        logger.info("✅ 规划引擎核心初始化完成")
        logger.info("="*60)

    def save_plan_checkpoint(self, plan: Plan) -> str:
        """保存计划检查点（D9：SQLite 落库，返回落库路径保持调用方语义；
        存储关闭（planning.storage.enabled=false）时静默跳过，仍返回路径）"""
        if self.db is None:
            return self.persist_db_path
        self.db.upsert_plan(plan)
        return self.persist_db_path

    def _load_plans_from_disk(self) -> Dict[str, Plan]:
        """从 SQLite 恢复未完成计划（D9 规格；存储关闭时返回空）"""
        if self.db is None:
            return {}
        return self.db.load_unfinished_plans()

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
            # 阶段 4（D16）：规划生成（decompose）耗时埋点
            decompose_start = datetime.now()
            plan = await self.decomposer.decompose(task, context)
            self.planning_metrics.record_decompose(
                (datetime.now() - decompose_start).total_seconds() * 1000
            )

            if plan.state == PlanState.READY:
                logger.info(f"✅ 任务分解成功!")
                logger.info(f"   计划ID: {plan.id}")
                logger.info(f"   子任务数: {len(plan.tasks)}")
                for i, t in enumerate(plan.tasks[:5]):
                    logger.info(f"      子任务{i+1}: {t.description[:50]}...")
                if len(plan.tasks) > 5:
                    logger.info(f"      ... 还有 {len(plan.tasks) - 5} 个子任务")

                # 阶段 2（D11）：创建期验证——结构性错误（悬空依赖/环/空描述）在此拦截，
                # 标记 FAILED 并指明原因，而非进入执行期卡死。规则分解（无 LLM）下的
                # tool_unavailable 属自由文本拆解的预期噪音，仅告警、交由执行期校验
                # （与 executor.validate_plan 语义一致，避免误拦"打开文件"类规则任务）。
                issues = validate_plan(plan, self.tool_registry, self.llm)
                fatal = [i for i in issues
                         if i.code in ("dangling_dependency", "circular_dependency", "empty_description")]
                if fatal:
                    plan.state = PlanState.FAILED
                    plan.error = "；".join(i.message for i in fatal)
                    logger.error(f"❌ 计划验证失败: {plan.error}")
                    logger.info("="*60)
                    raise PlanningError(f"计划验证失败: {plan.error}")
                for i in issues:
                    logger.warning(f"计划验证警告（不阻断创建）: {i.message}")

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
        # 阶段 4（D16）：执行耗时埋点计时起点
        exec_start = datetime.now()

        logger.info("="*60)
        logger.info("🚀 [规划引擎] 开始执行计划")
        logger.info(f"   计划ID: {plan.id}")
        logger.info(f"   任务描述: {plan.original_task[:80]}...")
        logger.info(f"   任务数: {len(plan.tasks)}")
        logger.info(f"   当前状态: {plan.state.value}")
        logger.info("-"*60)

        try:
            # 阶段 2（D11）：执行前验证——坏计划在进入执行器前被拦截（标记 FAILED + 指明原因），
            # 避免执行期卡死；executor 内部亦验证（双重防线，语义一致）。
            logger.info("📋 步骤0: 执行前规划验证...")
            try:
                validate_plan_or_raise(plan, self.tool_registry, self.llm)
                logger.info("   ✅ 计划验证通过")
            except PlanValidationError as e:
                plan.error = str(e)
                logger.error(f"❌ 计划验证失败: {e}")
                self.executor._finalize_state(plan, PlanState.FAILED, reason="计划验证失败")
                self._record_plan_result(plan, success=False,
                                         duration_ms=(datetime.now() - exec_start).total_seconds() * 1000)
                return plan

            logger.info("📊 步骤1: 状态转换 -> EXECUTING")
            if plan.state != PlanState.EXECUTING:
                self.state_machine.transition(plan, PlanState.EXECUTING, "开始执行")
                logger.info(f"   ✅ 状态已转换到: {plan.state.value}")
            else:
                # 崩溃恢复路径：计划从库中恢复时已处于 EXECUTING，转换幂等放行
                # （EXECUTING -> EXECUTING 非法，直接跳过，否则恢复的计划被误判失败）
                logger.info("   ✅ 计划已处于 EXECUTING（恢复路径），跳过重复转换")

            logger.info("⚙️ 步骤2: 调用执行引擎...")
            plan = await self.executor.execute_plan(plan)
            logger.info(f"   ✅ 执行引擎返回，任务完成数: {sum(1 for t in plan.tasks if t.status.value == 'completed')}/{len(plan.tasks)}")

            if self.reflector:
                try:
                    logger.info("🧠 步骤3: 执行计划反思...")
                    reflection = await self.reflector.plan_reflect(plan)
                    logger.info("   ✅ 反思完成")

                    # 阶段 2（D4 反思闭环）：计划级调整激活 decomposer.refine()——
                    # 反思结论转 feedback 文本，由 refine 生成新任务集合并更新 Plan
                    # （首次激活该能力；无 LLM 时 refine 原样返回，天然幂等）。
                    feedback = self._reflect_to_feedback(reflection)
                    if feedback and self.decomposer.llm is not None:
                        logger.info("🔧 步骤3.1: 依据反思反馈优化计划（refine 激活）...")
                        await self.decomposer.refine(plan, feedback)
                        self.save_plan_checkpoint(plan)
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
            # 阶段 4（D16）：计划路径收尾埋点（task_type 复用 reflector 分类）
            self._record_plan_result(plan, success=(plan.state == PlanState.COMPLETED),
                                     duration_ms=(datetime.now() - exec_start).total_seconds() * 1000)

            return plan

        except InvalidStateTransitionError as e:
            logger.error(f"❌ 状态转换错误: {e}")
            logger.error(f"   当前状态: {plan.state.value}")
            plan.error = str(e)
            # 边界 #6：恢复路径统一经 _finalize_state（状态机优先、非法降级），
            # 不再直接赋值绕过状态机；若计划已取消则保留 CANCELLED（边界 #2 同逻辑）
            self.executor._finalize_state(plan, PlanState.FAILED, reason="状态转换失败，计划标记失败")
            # 阶段 4（D16）：状态转换失败同样计入埋点（failed）
            self._record_plan_result(plan, success=False,
                                     duration_ms=(datetime.now() - exec_start).total_seconds() * 1000)
            logger.info("="*60)
            return plan

    def _record_plan_result(self, plan: Plan, *, success: bool, duration_ms: float) -> None:
        """阶段 4（D16）：计划路径收尾埋点（task_type 复用 reflector 分类；
        cost 优先取预算快照，缺省 0.0；埋点内部异常隔离不阻断主流程）"""
        budget = (plan.metadata or {}).get("budget") or {}
        self.planning_metrics.record_plan_result(
            task_type=classify_task(plan.original_task),
            success=success,
            iterations=plan.current_step,
            duration_ms=duration_ms,
            cost=float(budget.get("cost") or 0.0),
        )

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
        """判断是否需要规划（D6 修复：complexity_threshold 配置参与判定）"""
        complex_indicators = [
            "帮我完成", "帮我创建", "帮我分析",
            "帮我构建", "流程", "系统",
            "第一步", "第二步", "然后"
        ]
        complex_count = sum(1 for indicator in complex_indicators if indicator in message)

        action_keywords = ["检查", "分析", "创建", "生成", "整理", "监控"]
        action_count = sum(1 for keyword in action_keywords if keyword in message.lower())

        # D6 修复：复杂度分数 = 复杂指示器数 + 0.5×动作关键词数，
        # 超过 config.complexity_threshold（默认 1.0 = 等价原判定：复杂>=1 或 动作>=2）才规划。
        # 调高阈值可收严判定（测试：threshold=10 时普通报告任务不再触发规划）。
        threshold = self.config.get("complexity_threshold", 1.0)
        score = complex_count + action_count * 0.5
        needs = score >= threshold

        logger.info(f"   复杂指示器匹配: {complex_count} 个")
        logger.info(f"   动作关键词匹配: {action_count} 个")
        logger.info(f"   阈值: {threshold}（分数 {score}）")
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
            # 阶段 2（D4/D12 升级）：thought/observation 字段显式记录，与 ExecutionRecord
            # 新增字段一一对应，保持 to_dict() 向后兼容（未赋值时 observation 回退 result）。
            for step in react_result.steps:
                self.executor.execution_history.append(ExecutionRecord(
                    step=step.iteration,
                    task_id=f"react_{step.iteration}",
                    action=Action.llm_action(prompt=step.thought, description=step.action),
                    result=ActionResult(success=step.success, output=step.observation, observation=step.observation),
                    reasoning=step.thought,
                    thought=step.thought,
                    observation=step.observation,
                ))

            # D4 边界：一步 finish 完成（无中间步骤）也计入统一执行记录，保持双路径记录完整
            if not react_result.steps and react_result.success:
                self.executor.execution_history.append(ExecutionRecord(
                    step=0,
                    task_id="react_finish",
                    action=Action.llm_action(prompt="", description="finish"),
                    result=ActionResult(success=True, output=react_result.result, observation=react_result.result),
                    reasoning="",
                    thought="",
                    observation=react_result.result,
                ))

            logger.info(f"📊 步骤2: ReAct循环执行结果")
            logger.info(f"   成功: {react_result.success}")
            logger.info(f"   迭代次数: {react_result.iterations}")
            logger.info(f"   执行时长: {react_result.total_duration_ms}ms")
            if react_result.error:
                logger.info(f"   错误: {react_result.error}")

            # 阶段 3（D18 恢复语义）：ReAct 返回"等待用户输入"（ask_user 暂停）→
            # 登记等待中的问题并标记挂起 plan_id（供 resume_plan 恢复执行）
            pending_plan_id = None
            if not react_result.success and "等待用户输入" in str(react_result.error or ""):
                plan_id = context.get("session_id") or f"plan_{datetime.now().strftime('%H%M%S')}"
                question = str(react_result.result or "需要用户确认")
                self.ask_user(plan_id, question, task=message, context=context)
                pending_plan_id = plan_id
                logger.info(f"   ⏸️ ask_user 暂停登记: plan_id={plan_id} | 问题: {question[:60]}")

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

            # 阶段 4（D16/D15）：ReAct 结束点埋点 + 结构化执行摘要
            # （task_type 复用 reflector 分类；cost 取 react_result 预算记账）
            self.planning_metrics.record_plan_result(
                task_type=classify_task(message),
                success=react_result.success,
                iterations=react_result.iterations,
                duration_ms=react_result.total_duration_ms,
                cost=react_result.cost,
            )
            plan_summary = build_react_summary(message, react_result)

            # 阶段 4（D15）：markdown 计划摘要追加到响应流（控制台/调用方直接可见
            # 格式化摘要）；摘要渲染失败仅告警，不影响主响应（不变量：response 可读）。
            try:
                summary_md = build_react_summary_markdown(plan_summary)
                if summary_md:
                    response += f"\n\n---\n\n{summary_md}"
            except Exception as e:
                logger.warning(f"[D15] 摘要追加响应失败（不阻断）: {e}")

            return ChatResult(
                response=response,
                react_result=react_result,
                used_planning=True,
                pending_plan_id=pending_plan_id,
                plan_summary=plan_summary,
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

    @staticmethod
    def _reflect_to_feedback(reflection: Optional[Dict[str, Any]]) -> str:
        """将 plan_reflect 输出转换为 refine 的 feedback 文本（无输出/空内容返回空串）。

        优先级：summary > insight > improvements 拼接 > 整包 JSON 序列化。
        """
        if not reflection:
            return ""
        feedback = reflection.get("summary") or reflection.get("insight") or ""
        improvements = reflection.get("improvements") or []
        if not feedback and improvements:
            feedback = "；".join(str(i) for i in improvements)
        if not feedback:
            feedback = json.dumps(reflection, ensure_ascii=False)
        return feedback

    def ask_user(self, plan_id: str, question: str, task: str = None,
                 context: Dict = None) -> bool:
        """阶段 3（D18 恢复语义）：登记等待用户确认的问题（供 resume_plan 恢复）。

        不变量：空问题拒绝登记（防脏数据）；同 plan_id 重复登记覆盖旧问题。
        """
        if not question or not str(question).strip():
            logger.warning(f"[ask_user] 拒绝空问题登记: plan_id={plan_id}")
            return False
        self._pending_questions[plan_id] = {
            "question": str(question),
            "task": task,
            "context": context or {},
            "timed_out": False,
            "created_at": datetime.now(),
        }
        logger.info(
            f"[ask_user] 已登记等待中的问题 | plan_id={plan_id}"
            f" | 问题: {str(question)[:60]}"
        )
        return True

    def get_pending_question(self, plan_id: str) -> Optional[Dict]:
        """查询等待中的问题（含超时标记）；无则返回 None"""
        pending = self._pending_questions.get(plan_id)
        if pending is None:
            return None
        return dict(pending)

    async def resume_plan(self, plan_id: str, user_answer: str) -> ChatResult:
        """阶段 3（D18 恢复语义）：恢复等待用户输入的计划。

        流程：取等待中的问题 → 超时检查（ask_user_timeout_seconds，超时以"用户未确认"
        结束）→ 用户答案写入执行上下文（user_answer 键）后重新进入规划模式处理。

        Returns:
            ChatResult: 恢复后的处理结果（used_planning=True）。
        """
        pending = self._pending_questions.get(plan_id)
        if pending is None:
            logger.warning(f"[ask_user] 无等待中的问题，无法恢复: {plan_id}")
            return ChatResult(response="没有等待中的问题，无法恢复计划")

        age = (datetime.now() - pending["created_at"]).total_seconds()
        if age > self.ask_user_timeout:
            self._pending_questions.pop(plan_id, None)
            pending["timed_out"] = True
            logger.warning(
                f"[ask_user] 等待超时({self.ask_user_timeout}s)，以'用户未确认'结束: {plan_id}"
            )
            return ChatResult(response="用户未确认，计划已结束")

        self._pending_questions.pop(plan_id, None)
        context = dict(pending.get("context") or {})
        context["user_answer"] = user_answer
        task = pending.get("task") or str(pending.get("question", ""))
        logger.info(
            f"[ask_user] 恢复计划 {plan_id}，用户答案已写入上下文: {str(user_answer)[:50]}"
        )
        return await self._plan_chat(task, context)

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

    def get_planning_metrics(self) -> Dict:
        """阶段 4（D16）：规划指标汇总（供状态面板/健康检查复用；
        planning.metrics.enabled=false 时返回 {"enabled": False}）"""
        return self.planning_metrics.get_metrics()
