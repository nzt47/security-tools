"""计划执行器

执行分解后的任务计划
重构版本 - 使用 Phase 3 的 core/registry.py
保持 100% API 向后兼容
"""

import asyncio
import logging
import traceback
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime

from .models import Task, TaskStatus, Plan, PlanState
from .models.action import Action, ActionResult, ActionType
from .models.record import ExecutionRecord
from .state_machine import InvalidStateTransitionError, PlanStateMachine

# 使用 Phase 3 的统一注册表抽象
from core.registry import SimpleRegistry

# 导入错误处理类
from agent.error_handler import RecoverableError

logger = logging.getLogger(__name__)


class PlanValidationError(Exception):
    """计划验证失败（D11 修复）"""
    pass


class ToolRegistry:
    """工具注册表

    重构版本 - 使用 Phase 3 的 core/registry.SimpleRegistry
    保持 100% API 向后兼容

    中文工具匹配说明：
    find_tool() 支持中文任务描述匹配，通过 _TOOL_KEYWORDS_ZH 映射表
    将中文关键词映射到英文工具名，解决中文描述无法匹配英文工具名的问题。
    """

    # 中文关键词 -> 英文工具名映射表
    # 用于支持中文任务描述匹配英文工具名
    # 【不易·D10】只保留有明确工具意图的短语，禁单字/过宽连词（如"将"），
    #   避免无关描述误匹配（"请将这段文字展示给我"不得命中 write_file）。
    _TOOL_KEYWORDS_ZH: Dict[str, List[str]] = {
        "create_file": ["创建文件", "创建一个", "新建文件", "创建名为", "创建一个名为"],
        "write_file": ["写入文件", "写入到", "将搜索结果写入", "写入内容"],
        "read_file": ["读取文件", "读取"],
        "search": ["搜索", "查找", "查询"],
        "send_email": ["发送邮件", "通知", "发邮件"],
    }

    def __init__(self):
        logger.info("[ToolRegistry] __init__ 开始初始化")

        # 使用 Phase 3 的统一注册表
        self._tool_registry = SimpleRegistry("ToolRegistry")
        self._tool_schemas: Dict[str, Dict] = {}

        logger.info("[ToolRegistry] __init__ 初始化完成")

    def register(self, name: str, func: Callable, schema: Dict = None):
        """注册工具（保持原有 API）"""
        logger.info(f"[ToolRegistry.register] 注册工具: {name}")
        
        self._tool_registry.register(name, func)
        if schema:
            self._tool_schemas[name] = schema
        
        logger.info(f"工具已注册: {name}")

    def get(self, name: str) -> Optional[Callable]:
        """获取工具（保持原有 API）"""
        logger.debug(f"[ToolRegistry.get] 获取工具: {name}")
        return self._tool_registry.get(name)

    def has(self, name: str) -> bool:
        """检查工具是否存在（保持原有 API）"""
        logger.debug(f"[ToolRegistry.has] 检查工具: {name}")
        return self._tool_registry.has(name)

    def list_tools(self) -> List[str]:
        """列出所有工具（保持原有 API）"""
        logger.debug("[ToolRegistry.list_tools] 列出所有工具")
        return self._tool_registry.list()

    def get_schema(self, name: str) -> Optional[Dict]:
        """获取工具schema（保持原有 API）"""
        logger.debug(f"[ToolRegistry.get_schema] 获取schema: {name}")
        return self._tool_schemas.get(name)

    def find_tool(self, description: str) -> Optional[str]:
        """根据描述查找匹配的工具（保持原有 API）

        匹配策略：
        1. 英文精确匹配：检查英文工具名是否为描述的子串（原有逻辑）
        2. 中文关键词匹配：通过 _TOOL_KEYWORDS_ZH 映射表，检查中文关键词是否出现在描述中
           解决中文任务描述无法匹配英文工具名的问题（如"创建文件" -> "create_file"）
        """
        logger.debug(f"[ToolRegistry.find_tool] 查找工具: {description}")

        desc_lower = description.lower()

        # 策略1：英文工具名精确匹配（原有逻辑，向后兼容）
        for tool_name in self._tool_registry.list():
            if tool_name in desc_lower:
                return tool_name

        # 策略2：中文关键词匹配（新增，支持中文任务描述）
        for tool_name, keywords in self._TOOL_KEYWORDS_ZH.items():
            if not self._tool_registry.has(tool_name):
                continue
            for kw in keywords:
                if kw in description:
                    logger.debug(f"[ToolRegistry.find_tool] 中文匹配命中: {kw} -> {tool_name}")
                    return tool_name

        return None


class PlanExecutor:
    """计划执行引擎
    
    负责执行分解后的任务计划
    (无改动，保持原样)
    """

    def __init__(self, tool_registry: ToolRegistry, llm_service=None, max_retries: int = 3, config: Dict = None,
                 state_machine: Optional[PlanStateMachine] = None):
        """
        初始化执行器

        Args:
            tool_registry: 工具注册表
            llm_service: LLM服务
            max_retries: 最大重试次数
            config: 配置
            state_machine: 计划状态机（可选）。提供时收尾状态变更走状态机（触发钩子/记录转换历史）；
                          不提供时保持原有直接赋值行为，兼容独立使用场景。
        """
        self.tool_registry = tool_registry
        self.llm = llm_service
        self.max_retries = max_retries
        self.config = config or {}
        # D9：执行记录审计后端（由 core 注入 PlanDB；未注入时静默跳过，不影响主流程）
        self.persistence = None
        # D14：降级链配置（主工具名 -> 备份工具列表），主工具失败时逐个尝试
        # 【不易】仅 TOOL_CALL 动作生效；配置缺失时行为与原有完全一致（零回退成本）
        self.degrade_chain: Dict[str, List[str]] = self.config.get("degrade_chain") or {}
        self.state_machine = state_machine

        # 协作式取消（D18 主缺陷修复，阶段 3 方案 A）：
        # _running_tasks 记录当前执行中的 execute_plan 任务（按 plan_id），供 cancel_plan 传播取消；
        # _cancelled_plan_ids 为 per-plan 取消标志（多计划并发共享本实例时互相隔离）。
        self._running_tasks: Dict[str, "asyncio.Task"] = {}
        self._cancelled_plan_ids: set = set()

        self.execution_history: List[ExecutionRecord] = []
        self._callbacks: Dict[str, List[Callable]] = {
            "on_task_start": [],
            "on_task_complete": [],
            "on_task_fail": [],
            "on_plan_complete": [],
        }
        
        # 延迟导入避免循环依赖
        from agent.error_handler import (
            async_with_retry,
            RecoverableError
        )
        self._execute_task_with_retry_internal = async_with_retry(
            max_retries=self.max_retries,
            initial_delay=1.0,
            backoff_factor=2.0,
            strategy="exponential",
            retryable_exceptions=(RecoverableError,),
            error_counter="executor.task"
        )(self._do_execute_task)

    def register_callback(self, event: str, callback: Callable):
        """注册事件回调"""
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def validate_plan(self, plan: Plan) -> None:
        """执行前验证计划结构：悬空依赖 / 循环依赖 / 工具可用性（D11 修复）

        Raises:
            PlanValidationError: 计划结构非法
        """
        task_ids = {t.id for t in plan.tasks}
        for task in plan.tasks:
            for dep in task.dependencies:
                if dep not in task_ids:
                    raise PlanValidationError(
                        f"任务 '{task.id}' 依赖不存在的任务 '{dep}'（依赖不存在）"
                    )

        task_map = {t.id: t for t in plan.tasks}
        visiting, visited = set(), set()

        def _dfs(tid: str) -> None:
            if tid in visiting:
                raise PlanValidationError(f"检测到循环依赖（涉及任务 '{tid}'）")
            if tid in visited:
                return
            visiting.add(tid)
            for dep in task_map[tid].dependencies:
                _dfs(dep)
            visiting.discard(tid)
            visited.add(tid)

        for t in plan.tasks:
            _dfs(t.id)

        # D11 规格：工具可用性预检——无 LLM 的纯工具执行路径下，任务引用的工具
        # 必须可解析（find_tool 命中英文子串/中文关键词）；有 LLM 时任务可由推理
        # 灵活完成，跳过预检避免误拦截（如"请思考并回答"类描述）。
        if getattr(self, "llm", None) is None:
            for task in plan.tasks:
                if self.tool_registry.find_tool(task.description) is None:
                    raise PlanValidationError(
                        f"任务 '{task.id}' 引用的工具不可用"
                        f"（描述 '{task.description}' 无法解析到已注册工具）"
                    )

    def _resolve_deadlocked_tasks(self, plan: Plan) -> bool:
        """死锁消解：将依赖已终结性失败（FAILED/SKIPPED）的 PENDING 任务标记为 SKIPPED。

        迭代至不动点：被跳过任务的后续依赖任务同样不可满足，需继续处理（传递链）。
        仅由"无可执行任务"分支调用——max_steps 中断导致的 PENDING 不受影响（防误伤）。

        Returns:
            是否有任务被标记（供主循环重新调度）。
        """
        terminal_failed = {TaskStatus.FAILED, TaskStatus.SKIPPED}
        changed = False
        while True:
            blocked_ids = {t.id for t in plan.tasks if t.status in terminal_failed}
            progressed = False
            for task in plan.tasks:
                if task.status == TaskStatus.PENDING and any(
                    dep in blocked_ids for dep in task.dependencies
                ):
                    task.mark_skipped()
                    logger.info(
                        f"[死锁消解] 任务 {task.id} 的依赖已终结性失败，标记为 SKIPPED"
                    )
                    progressed = True
                    changed = True
            if not progressed:
                break
        return changed

    async def execute_plan(self, plan: Plan) -> Plan:
        """
        执行完整计划

        Args:
            plan: 执行计划

        Returns:
            执行完成的计划
        """
        if plan.state not in (PlanState.READY, PlanState.EXECUTING):
            raise ValueError(f"计划状态不正确: {plan.state}")

        # D11 修复：执行前验证计划结构（悬空依赖/循环依赖），失败提前收尾而非执行期卡死
        try:
            self.validate_plan(plan)
        except PlanValidationError as e:
            plan.error = str(e)
            logger.error(f"计划验证失败: {e}")
            self._finalize_state(plan, PlanState.FAILED, reason="计划验证失败")
            return plan

        plan.state = PlanState.EXECUTING
        plan.updated_at = datetime.now()
        logger.info(
            f"开始执行计划: {plan.id} | 任务数: {len(plan.tasks)} | max_steps: {plan.max_steps}"
        )

        step_count = 0
        # 登记当前执行任务，供 cancel_plan 传播取消（finally 中清理）
        self._running_tasks[plan.id] = asyncio.current_task()
        try:
            while not plan.is_complete():
                if plan.id in self._cancelled_plan_ids:
                    # 协作式取消：停止调度后续任务（同步工具执行完当前调用后在此退出）
                    logger.warning(f"计划已被取消，终止执行: {plan.id}")
                    break

                if step_count >= plan.max_steps:
                    logger.warning(f"达到最大步骤数: {plan.max_steps}")
                    break

                next_tasks = plan.get_next_executable_tasks()
                if not next_tasks:
                    # P2 修复：死锁消解——PENDING 任务若存在已终结性失败（FAILED/SKIPPED）
                    # 的依赖，则执行条件永远无法满足。将其标记 SKIPPED（终态）后重试，
                    # 让收尾正确判定为"部分任务失败"而非误报"超时或异常终止"。
                    # 仅在此分支触发：max_steps 中断导致的 PENDING 不受影响（防误伤）。
                    if self._resolve_deadlocked_tasks(plan):
                        logger.info("[执行任务] 死锁消解: 已标记 SKIPPED 的任务, 重新调度")
                        continue
                    logger.warning("无可执行任务,但计划未完成")
                    break

                if len(next_tasks) > 1:
                    # D5 修复：互不依赖的任务并行执行
                    # D5 边界修正：并行批不越过 max_steps 额度，超限时截断本批，
                    # 剩余任务留待下轮循环（最终残留 PENDING → 收尾 FAILED，不误判成功）
                    remaining = plan.max_steps - step_count
                    if len(next_tasks) > remaining:
                        next_tasks = next_tasks[:remaining]
                    logger.info(
                        f"[执行任务] 并行批: {len(next_tasks)} 个任务"
                        f" | ids: {[t.id for t in next_tasks]}"
                    )
                    # D5 状态收尾修正：每任务独立执行+立即标记（不等整批 gather 结束），
                    # 保证取消时已完成任务的结果/状态不滞留 RUNNING（竞态）。
                    await asyncio.gather(
                        *[self._execute_task_with_retry_and_record(plan, t) for t in next_tasks]
                    )
                    step_count += len(next_tasks)
                    plan.current_step += len(next_tasks)
                else:
                    task = next_tasks[0]
                    logger.info(
                        f"[执行任务] 开始: {task.id} | 描述: {task.description[:60]}"
                        f" | 优先级: {task.priority}"
                    )
                    result = await self._execute_task_with_retry(task)

                    self._record_execution(plan, task, result)

                    if result.success:
                        task.mark_completed(result.output)
                        await self._trigger_callbacks("on_task_complete", task, result)
                        logger.info(f"[执行任务] 成功: {task.id}")
                    else:
                        task.mark_failed(result.error or "未知错误")
                        await self._trigger_callbacks("on_task_fail", task, result)
                        logger.warning(
                            f"[执行任务] 失败: {task.id} | 错误: {str(result.error)[:100]}"
                        )

                        if task.priority >= 4:
                            logger.error(f"高优先级任务失败: {task.id}")
                            break

                    step_count += 1
                    plan.current_step += 1
                plan.updated_at = datetime.now()

            if plan.id in self._cancelled_plan_ids:
                # 取消优先于正常收尾：标记 CANCELLED，不触发部分失败/超时收尾
                logger.info(f"[收尾判定] -> CANCELLED（用户取消）: {plan.id}")
                self._finalize_state(plan, PlanState.CANCELLED, reason="用户取消")
            else:
                # P2 修复补充：收尾期死锁消解兜底——覆盖 max_steps 耗尽/高优先级中断等
                # 绕过"无可执行任务"分支的退出路径（这些路径未在循环内消解）。将依赖
                # 已终结性失败（FAILED/SKIPPED）的 PENDING 任务标记 SKIPPED，使收尾能
                # 正确判定为"部分任务失败"。max_steps 残留的 PENDING（无失败依赖）不受影响。
                if self._resolve_deadlocked_tasks(plan):
                    logger.info("[收尾判定] 死锁消解: 依赖已终结性失败的任务被标记 SKIPPED")
                # D1 修复：先基于任务状态计算 all_completed 与 any_failed，再据此设置
                # COMPLETED 与 result。通过 is_success(consider_state=False) 仅依据任务
                # 状态判定成功（不要求 state == COMPLETED），避免计划仍处于 EXECUTING 时
                # 被短路——否则"全部成功"分支永不触发，全成功计划会被误判为"部分任务失败"。
                all_completed = plan.is_success(consider_state=False)
                any_failed = any(t.status == TaskStatus.FAILED for t in plan.tasks)
                status_counts = {
                    s.value: sum(1 for t in plan.tasks if t.status == s)
                    for s in TaskStatus
                }
                logger.info(
                    f"[收尾判定] plan={plan.id} | all_completed={all_completed}"
                    f" | any_failed={any_failed} | 任务状态: {status_counts}"
                )
                if all_completed:
                    logger.info(f"[收尾判定] -> COMPLETED（所有任务执行成功）: {plan.id}")
                    self._finalize_state(plan, PlanState.COMPLETED, reason="所有任务执行成功")
                    plan.result = "所有任务执行成功"
                elif plan.is_complete() and any_failed:
                    logger.info(f"[收尾判定] -> COMPLETED（部分任务失败）: {plan.id}")
                    self._finalize_state(plan, PlanState.COMPLETED, reason="部分任务失败但计划完成")
                    plan.result = "计划执行完成,但部分任务失败"
                elif plan.is_complete():
                    logger.info(f"[收尾判定] -> COMPLETED（计划执行完成）: {plan.id}")
                    self._finalize_state(plan, PlanState.COMPLETED, reason="计划执行完成")
                    plan.result = "计划执行完成"
                else:
                    logger.warning(
                        f"[收尾判定] -> FAILED（超时或异常终止）: {plan.id}"
                        f" | 已执行步数: {step_count}"
                    )
                    self._finalize_state(plan, PlanState.FAILED, reason="计划执行超时或异常终止")
                    plan.error = "计划执行超时或异常终止"

            await self._trigger_callbacks("on_plan_complete", plan)

        except asyncio.CancelledError:
            # 协作式取消：CancelledError 已传播到工具 await 点（经 wait_for），
            # 计划标记 CANCELLED 并正常返回，不再向上传播（取消即为预期结束）。
            logger.warning(f"计划执行被取消: {plan.id}")
            self._finalize_state(plan, PlanState.CANCELLED, reason="用户取消")

        except Exception as e:
            self._finalize_state(plan, PlanState.FAILED, reason=f"计划执行异常: {e}")
            plan.error = str(e)
            logger.error(f"计划执行异常: {e}")

        finally:
            self._running_tasks.pop(plan.id, None)
            self._cancelled_plan_ids.discard(plan.id)

        plan.updated_at = datetime.now()
        logger.info(f"计划执行{plan.state.value}: {plan.progress():.1%}")
        return plan

    def _finalize_state(self, plan: Plan, target: PlanState, *, reason: str) -> None:
        """收尾状态变更：优先走状态机（触发钩子/记录转换历史），无状态机时降级直接赋值

        不变量：计划状态变更必须经状态机，确保合法性校验、转换历史与钩子回调不被旁路；
        但兼容 executor 独立使用（未注入状态机）时的直接赋值行为。
        边界 #2：若计划已处于 CANCELLED（取消竞态先行生效），保留取消状态不被收尾覆盖。
        """
        if self.state_machine is not None:
            try:
                self.state_machine.transition(plan, target, reason)
                return
            except InvalidStateTransitionError as e:
                if plan.state == PlanState.CANCELLED:
                    # 取消优先于收尾：取消竞态下不覆盖 CANCELLED（边界 #2）
                    logger.warning(f"计划已取消（{plan.id}），保留 CANCELLED，跳过收尾变更: {target.value}")
                    return
                logger.warning(f"状态机收尾转换失败，降级直接赋值: {e}")
        plan.state = target

    async def _do_execute_task(self, task: Task) -> ActionResult:
        """实际的任务执行逻辑（不含重试，失败抛出异常）"""
        action = self._determine_action(task)
        result = await self._execute_action(action)
        result.duration_ms = 0

        if result.success:
            return result

        # D14 降级链：主工具失败（无 Plan B）→ 沿配置链尝试备份工具，
        # 任一成功即返回成功（observation 标注降级来源）；全部失败保留主工具错误
        if action.action_type == ActionType.TOOL_CALL:
            backup_result = await self._try_degrade_chain(action)
            if backup_result is not None:
                backup_result.duration_ms = 0
                return backup_result

        raise RecoverableError(f"任务执行失败: {result.error}")

    async def _try_degrade_chain(self, action: Action) -> Optional[ActionResult]:
        """D14：主工具失败后沿降级链尝试备份工具（Plan B）

        Returns:
            任一备份工具成功 -> 成功结果（observation 标注已降级）；否则 None。
        失败语义：备份工具失败不中断，继续尝试下一个；全部失败由调用方
        保留主工具错误并抛 RecoverableError（重试语义不变）。
        """
        backup_tools = self.degrade_chain.get(action.tool_name, [])
        if not backup_tools:
            return None

        for backup_name in backup_tools:
            if not self.tool_registry.has(backup_name):
                logger.warning(f"[D14降级链] 备份工具不存在，跳过: {backup_name}")
                continue
            backup_action = Action.tool_action(
                tool_name=backup_name,
                params=action.tool_params,
                description=action.description,
            )
            logger.info(f"[D14降级链] 主工具 {action.tool_name} 失败，尝试备份工具: {backup_name}")
            result = await self._execute_action(backup_action)
            if result.success:
                result.observation = (
                    f"主工具 {action.tool_name} 失败，已降级至备份工具 {backup_name} 执行成功"
                )
                logger.info(f"[D14降级链] 降级成功: {action.tool_name} -> {backup_name}")
                return result
            logger.warning(f"[D14降级链] 备份工具 {backup_name} 也失败: {result.error}")
        return None
    
    async def _execute_task_with_retry(self, task: Task) -> ActionResult:
        """带重试的任务执行"""
        task.mark_running()
        try:
            return await self._execute_task_with_retry_internal(task)
        except Exception as e:
            last_error = str(e)
            logger.error(f"任务执行失败: {e}")
            return ActionResult.failure_result(last_error or "重试次数耗尽")

    async def _execute_task_with_retry_and_record(self, plan: Plan, task: Task) -> ActionResult:
        """执行单个任务并立即记录/标记状态（供并行批使用，D5 修复）

        不变量：任务完成（成功/失败）即更新状态与记录，不等待整批并行任务收尾，
        避免取消时已完成任务的状态滞留 RUNNING（竞态）。
        """
        result = await self._execute_task_with_retry(task)
        self._record_execution(plan, task, result)
        if result.success:
            task.mark_completed(result.output)
            await self._trigger_callbacks("on_task_complete", task, result)
        else:
            task.mark_failed(result.error or "未知错误")
            await self._trigger_callbacks("on_task_fail", task, result)
            if task.priority >= 4:
                logger.error(f"高优先级任务失败: {task.id}")
        return result

    def _determine_action(self, task: Task) -> Action:
        """根据任务确定执行动作"""
        tool_name = self.tool_registry.find_tool(task.description)

        if tool_name and self.tool_registry.has(tool_name):
            return Action.tool_action(
                tool_name=tool_name,
                params=self._extract_params(task, tool_name),
                description=task.description
            )
        elif self.llm:
            return Action.llm_action(
                prompt=f"执行任务: {task.description}",
                description=task.description
            )
        else:
            return Action.response_action(f"任务无法执行: {task.description}")

    async def _execute_action(self, action: Action) -> ActionResult:
        """执行动作"""
        if action.action_type == ActionType.TOOL_CALL:
            return await self._execute_tool_call(action)
        elif action.action_type == ActionType.LLM_REASONING:
            return await self._execute_llm_reasoning(action)
        elif action.action_type == ActionType.RESPONSE:
            return ActionResult.success_result(
                output=action.tool_params.get("response", ""),
                observation="直接返回响应"
            )
        else:
            return ActionResult.failure_result(f"未知动作类型: {action.action_type}")

    async def _execute_tool_call(self, action: Action) -> ActionResult:
        """执行工具调用"""
        tool = self.tool_registry.get(action.tool_name)
        if not tool:
            logger.error(f"[工具调用] ERROR: 工具不存在: {action.tool_name}")
            return ActionResult.failure_result(f"工具不存在: {action.tool_name}")

        logger.info(f"[工具调用] INFO: 开始执行: {action.tool_name}")
        logger.debug(f"[工具调用] DEBUG: 参数: {action.tool_params}")
        
        try:
            timeout = self.config.get('tool_timeout', 30)
            
            if asyncio.iscoroutinefunction(tool):
                try:
                    output = await asyncio.wait_for(
                        tool(**action.tool_params),
                        timeout=timeout
                    )
                except asyncio.TimeoutError:
                    logger.error(f"[工具调用] TIMEOUT: {action.tool_name}")
                    logger.error(f"[工具调用] TIMEOUT: 超时时间: {timeout}秒")
                    logger.error(f"[工具调用] TIMEOUT: 参数: {action.tool_params}")
                    return ActionResult.failure_result(
                        f"工具调用超时: {action.tool_name} (超时时间: {timeout}秒)"
                    )
            else:
                output = tool(**action.tool_params)

            logger.info(f"[工具调用] SUCCESS: {action.tool_name}")
            logger.debug(f"[工具调用] DEBUG: 输出: {str(output)[:100]}..." if len(str(output)) > 100 else f"[工具调用] DEBUG: 输出: {output}")
            
            return ActionResult.success_result(
                output=output,
                observation=f"工具{action.tool_name}执行成功",
                state_changes=[f"{action.tool_name}已执行"]
            )
        except RecoverableError as e:
            logger.warning(f"[工具调用] WARNING: 可恢复错误: {action.tool_name}")
            logger.warning(f"[工具调用] WARNING: 错误信息: {e}")
            return ActionResult.failure_result(f"工具执行可恢复错误: {e}")
        except Exception as e:
            logger.error(f"[工具调用] ERROR: 执行失败: {action.tool_name}")
            logger.error(f"[工具调用] ERROR: 错误类型: {type(e).__name__}")
            logger.error(f"[工具调用] ERROR: 错误信息: {str(e)}")
            logger.error(f"[工具调用] ERROR: 堆栈跟踪:\n{traceback.format_exc()}")
            return ActionResult.failure_result(f"工具执行失败: {e}")

    async def _execute_llm_reasoning(self, action: Action) -> ActionResult:
        """执行LLM推理"""
        if not self.llm:
            return ActionResult.failure_result("LLM服务不可用")

        try:
            prompt = action.tool_params.get("prompt", "")
            response = await self.llm.chat([{"role": "user", "content": prompt}])

            return ActionResult.success_result(
                output=response,
                observation=f"LLM推理完成: {response[:100]}..."
            )
        except Exception as e:
            return ActionResult.failure_result(f"LLM推理失败: {e}")

    def _extract_params(self, task: Task, tool_name: str = None) -> Dict[str, Any]:
        """从任务描述中提取参数

        使用工具名进行分发，替代原有基于英文字符串匹配的分支逻辑，
        解决中文任务描述无法匹配英文工具名的问题。

        Args:
            task: 任务对象
            tool_name: 已识别的工具名（由 _determine_action 传入，避免重复查找）
        """
        description = task.description
        params = {}

        import re

        # 如果未指定工具名，尝试自动识别（向后兼容）
        if not tool_name:
            tool_name = self.tool_registry.find_tool(description) or ""

        if tool_name == "create_file":
            match = re.search(r'名为\s*["\']?([^"\']+)["\']?\s*的文件', description)
            if match:
                params['filename'] = match.group(1).strip()

        elif tool_name == "write_file":
            # 提取文件名：匹配"写入 report.txt 文件"或"写入到 report.txt"
            match = re.search(r'写入\s*["\']?([^"\']+?)["\']?\s*(?:文件|$)', description)
            if not match:
                match = re.search(r'写入\s*([^\s，,]+)', description)
            if match:
                params['filename'] = match.group(1).strip()
            # 提取写入内容：优先从执行历史中查找搜索结果，其次从描述中提取
            if "搜索结果" in description:
                # 查找之前的搜索任务结果，实现跨任务上下文传递
                search_result = self._lookup_search_result()
                params['content'] = search_result or "搜索结果"
            else:
                params['content'] = "测试内容"

        elif tool_name == "read_file":
            match = re.search(r'读取\s*["\']?([^"\']+)["\']?\s*文件', description)
            if match:
                params['filename'] = match.group(1).strip()

        elif tool_name == "search":
            # 仅在明确匹配"搜索关于...的信息"模式时提取 query 参数，
            # 避免对简单描述（如"搜索信息"）过度提取参数导致不接受参数的工具报错
            match = re.search(r'搜索\s*关于\s*["\']?([^"\']+)["\']?\s*的信息', description)
            if match:
                params['query'] = match.group(1).strip()

        elif tool_name == "send_email":
            match = re.search(r'通知\s*([^\s，,]+)', description)
            if match:
                params['to'] = match.group(1).strip()
            params['subject'] = "任务完成通知"
            params['body'] = "任务已成功完成"

        return params

    def _lookup_search_result(self) -> Optional[str]:
        """从执行历史中查找最近的搜索任务结果

        实现跨任务上下文传递：当 write_file 任务描述中包含"搜索结果"时，
        从 execution_history 中查找最近的 search 任务输出，作为写入内容。

        Returns:
            搜索任务的输出结果，如果没有则返回 None
        """
        for record in reversed(self.execution_history):
            if record.result and record.result.success:
                # 检查是否是搜索任务（通过任务描述判断）
                desc = record.action.description or ""
                if "搜索" in desc or "search" in desc.lower() or "查找" in desc:
                    return str(record.result.output) if record.result.output else None
        return None

    def _record_execution(self, plan: Plan, task: Task, result: ActionResult):
        """记录执行历史"""
        record = ExecutionRecord(
            step=plan.current_step,
            task_id=task.id,
            action=Action(
                id=f"action_{task.id}",
                description=task.description
            ),
            result=result,
            reasoning=f"执行任务: {task.description}"
        )
        self.execution_history.append(record)

        # D9 规格：执行记录落库（审计可追溯）；失败仅告警，不影响主流程
        if self.persistence is not None:
            try:
                self.persistence.append_execution_log(
                    plan_id=plan.id,
                    task_id=task.id,
                    action_type=record.action.action_type.value,
                    tool_name=record.action.tool_name or None,
                    success=result.success,
                    output=str(result.output) if result.output is not None else None,
                    error=result.error,
                )
            except Exception as e:
                logger.warning(f"[D9] 执行记录落库失败: {e}")

    async def _trigger_callbacks(self, event: str, *args):
        """触发事件回调"""
        for callback in self._callbacks.get(event, []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(*args)
                else:
                    callback(*args)
            except Exception as e:
                logger.error(f"回调执行失败: {e}")

    async def cancel_plan(self, plan: Plan) -> Plan:
        """取消计划（协作式取消，D18 主缺陷修复）

        设置 per-plan 取消标志，并取消正在执行的 execute_plan 任务，
        使 CancelledError 传播到工具 await 点（异步工具可协作取消）；
        同步工具无法中断，执行完当前调用后由循环标志检查退出。
        """
        self._cancelled_plan_ids.add(plan.id)
        plan.state = PlanState.CANCELLED
        plan.updated_at = datetime.now()
        logger.info(f"计划已取消: {plan.id}")
        task = self._running_tasks.get(plan.id)
        if task is not None and not task.done():
            task.cancel()
        return plan

    def get_history(self, limit: int = 50) -> List[Dict]:
        """获取执行历史"""
        records = self.execution_history[-limit:]
        return [r.to_dict() for r in records]
