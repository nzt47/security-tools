"""计划执行器

执行分解后的任务计划
重构版本 - 使用 Phase 3 的 core/registry.py
保持 100% API 向后兼容
"""

import asyncio
import logging
import time
import traceback
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime

from .models import Task, TaskStatus, Plan, PlanState
from .models.action import Action, ActionResult, ActionType
from .models.record import ExecutionRecord
from .state_machine import InvalidStateTransitionError, PlanStateMachine
from .validator import PlanValidationError, validate_plan_or_raise
from .budget import BudgetManager, BudgetStatus, PlanBudget

# 使用 Phase 3 的统一注册表抽象
from core.registry import SimpleRegistry

# 导入错误处理类
from agent.error_handler import RecoverableError

logger = logging.getLogger(__name__)


def _ts() -> str:
    """wall-clock 毫秒时间戳（并发时序日志统一入口，便于交叉比对任务时间线）"""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


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
                 state_machine: Optional[PlanStateMachine] = None,
                 decomposer=None, reflector=None):
        """
        初始化执行器

        Args:
            tool_registry: 工具注册表
            llm_service: LLM服务
            max_retries: 最大重试次数
            config: 配置
            state_machine: 计划状态机（可选）。提供时收尾状态变更走状态机（触发钩子/记录转换历史）；
                          不提供时保持原有直接赋值行为，兼容独立使用场景。
            decomposer: 任务分解器（可选）。阶段 3（D14）重规划 Plan B 依赖：高优先级任务
                        失败时调用 decompose.refine() 调整任务集后继续执行；未注入则不触发重规划。
            reflector: 反思引擎（可选）。阶段 3（D14）失败归因：learn_from_experience 记录教训。
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
        # 阶段 2（D5）：并行执行开关——默认关闭保持串行（与重构前行为一致），
        # 配置 executor.parallel_execution=true 才启用 asyncio.gather 并发，降低回归风险
        self.parallel_execution = bool(self.config.get("parallel_execution", False))
        # 阶段 3（D14）：高优先级任务失败重规划开关（planner.replan_on_failure，默认 true）
        self.replan_on_failure = bool(self.config.get("replan_on_failure", True))
        # 阶段 3（D14）：重规划与失败归因依赖（由 core 注入）
        self.decomposer = decomposer
        self.reflector = reflector
        # 阶段 3（D13）：执行器级预算（默认全 None 不限制 = 零行为变化）
        self.budget_manager = BudgetManager(
            PlanBudget.from_config(self.config),
            token_price_per_1k=self.config.get("token_price_per_1k", 0.002),
        )

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
        """执行前验证计划结构（D11）：委托独立 validator 模块（依赖/环/工具/空描述）

        Raises:
            PlanValidationError: 计划结构非法
        """
        validate_plan_or_raise(plan, self.tool_registry, getattr(self, "llm", None))

    def _resolve_deadlocked_tasks(self, plan: Plan) -> bool:
        """死锁消解：将依赖已终结性失败（FAILED/SKIPPED）的 PENDING 任务标记为 SKIPPED。

        迭代至不动点：被跳过任务的后续依赖任务同样不可满足，需继续处理（传递链）。
        仅由"无可执行任务"分支调用——max_steps 中断导致的 PENDING 不受影响（防误伤）。

        Returns:
            是否有任务被标记（供主循环重新调度）。
        """
        terminal_failed = {TaskStatus.FAILED, TaskStatus.SKIPPED}
        changed = False
        # 残留 RUNNING 重置：本方法仅在"无可执行任务"分支与收尾期调用（此刻无任务
        # 执行中），RUNNING 只可能来自崩溃恢复/异常中断的遗留。重置为 PENDING 后由
        # 主循环重新调度（依赖满足则重执行；依赖已终结性失败则下轮被消解为 SKIPPED），
        # 避免 RUNNING 任务永不执行、下游 PENDING 依赖悬挂（与依赖悬挂同源边界）。
        for task in plan.tasks:
            if task.status == TaskStatus.RUNNING:
                task.status = TaskStatus.PENDING
                task.started_at = None
                logger.info(f"[死锁消解] 任务 {task.id} 残留 RUNNING，重置为 PENDING 重新调度")
                changed = True
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

        # 阶段 3（D13）：开始预算记账（steps/elapsed 按本次执行计，token/cost 为实例生命周期累计），
        # 初始快照写入 plan.metadata 供收尾/观测使用
        self.budget_manager.start()
        plan.metadata["budget"] = self.budget_manager.snapshot()

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

                # 阶段 3（D13）：预算超限检查——超出任一度量（steps/seconds/tokens/cost）
                # 即正常收尾返回部分结果（不抛异常，区别于异常路径）；快照落 metadata 可观测
                budget_status = self.budget_manager.check()
                if budget_status != BudgetStatus.OK:
                    snap = self.budget_manager.snapshot()
                    logger.info(
                        f"[预算] 超限快照 | steps={snap['steps']} iterations={snap['iterations']}"
                        f" | elapsed={snap['elapsed_seconds']}s | tokens={snap['tokens']}"
                        f" | cost=${snap['cost']}"
                    )
                    logger.warning(
                        f"[预算] 超出预算（{budget_status.value}），正常收尾返回部分结果: {plan.id}"
                    )
                    plan.metadata["budget"] = self.budget_manager.snapshot()
                    plan.metadata["budget"]["status"] = budget_status.value
                    break

                _sched_start = time.monotonic()
                next_tasks = plan.get_next_executable_tasks()
                _sched_elapsed = (time.monotonic() - _sched_start) * 1000
                logger.debug(
                    f"[时序] 调度决策 @{_ts()} | 耗时: {_sched_elapsed:.1f}ms"
                    f" | 可执行任务: {len(next_tasks)}"
                )
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

                if self.parallel_execution and len(next_tasks) > 1:
                    # D5 修复：互不依赖的任务并行执行（executor.parallel_execution 开关控制，
                    # 默认 false 串行，与重构前行为一致；配置 true 才启用并发）
                    # D5 边界修正：并行批不越过 max_steps 额度，超限时截断本批，
                    # 剩余任务留待下轮循环（最终残留 PENDING → 收尾 FAILED，不误判成功）
                    remaining = plan.max_steps - step_count
                    if len(next_tasks) > remaining:
                        next_tasks = next_tasks[:remaining]
                    # 供执行器参考的并行组声明（分解器写入 plan.metadata["parallel_groups"]）
                    parallel_groups = (plan.metadata or {}).get("parallel_groups") or []
                    if parallel_groups:
                        logger.info(
                            f"[并行执行] 计划声明的并行组: {parallel_groups}"
                            f" | 本批任务: {[t.id for t in next_tasks]}"
                        )
                    # 并发共享状态校验警告：同批任务不得写同一资源（如 file_contents 类工具）
                    _precheck_start = time.monotonic()
                    self._warn_parallel_resource_conflicts(next_tasks)
                    _precheck_elapsed = (time.monotonic() - _precheck_start) * 1000
                    logger.debug(
                        f"[时序] 资源冲突预检 @{_ts()} | 耗时: {_precheck_elapsed:.1f}ms"
                        f" | 任务数: {len(next_tasks)}"
                    )
                    batch_start = time.monotonic()
                    logger.info(
                        f"[时序] 并行批调度 @{_ts()} | {len(next_tasks)} 个任务"
                        f" | ids: {[t.id for t in next_tasks]}"
                        f" | 计划总步数: {plan.current_step}/{plan.max_steps}"
                    )
                    # D5 状态收尾修正：每任务独立执行+立即标记（不等整批 gather 结束），
                    # 保证取消时已完成任务的结果/状态不滞留 RUNNING（竞态）。
                    await asyncio.gather(
                        *[self._execute_task_with_retry_and_record(plan, t) for t in next_tasks]
                    )
                    batch_elapsed = time.monotonic() - batch_start
                    step_count += len(next_tasks)
                    plan.current_step += len(next_tasks)
                    # 阶段 3（D13）：预算记步（并行批按批任务数累计，下一轮循环顶部 check）
                    self.budget_manager.record_step(len(next_tasks))
                    logger.info(
                        f"[时序] 并行批完成 @{_ts()} | 批耗时: {batch_elapsed:.2f}s"
                        f" | ids: {[t.id for t in next_tasks]}"
                        f" | 批次状态: "
                        f"{[(t.id, t.status.value) for t in next_tasks]}"
                        f" | 计划总步数: {plan.current_step}"
                    )
                else:
                    task = next_tasks[0]
                    logger.info(
                        f"[时序] 任务开始 @{_ts()} | {task.id} | 描述: {task.description[:60]}"
                        f" | 优先级: {task.priority}"
                    )
                    _task_start = time.monotonic()
                    result = await self._execute_task_with_retry(task)

                    self._record_execution(plan, task, result)

                    if result.success:
                        task.mark_completed(result.output)
                        await self._trigger_callbacks("on_task_complete", task, result)
                        logger.info(
                            f"[时序] 任务完成 @{_ts()} | {task.id}"
                            f" | 耗时: {time.monotonic() - _task_start:.2f}s"
                        )
                    else:
                        task.mark_failed(result.error or "未知错误")
                        await self._trigger_callbacks("on_task_fail", task, result)
                        logger.warning(
                            f"[时序] 任务失败 @{_ts()} | {task.id}"
                            f" | 耗时: {time.monotonic() - _task_start:.2f}s"
                            f" | 错误: {str(result.error)[:100]}"
                        )
                        # 阶段 3（D14）：失败归因（分类写入 task.metadata + reflector 记教训），
                        # 不阻断主流程（异常仅告警）
                        await self._on_task_failed(plan, task, result)

                        if task.priority >= 4:
                            logger.warning(
                                f"[重规划] 高优先级任务 {task.id} 失败"
                                f"（priority={task.priority} ≥ 4，尝试重规划）"
                                f" | fallback_actions={task.fallback_actions}"
                            )
                            if await self._replan_on_failure(plan, task):
                                logger.warning(
                                    f"[重规划] 高优先级任务 {task.id} 失败后计划已修正，继续执行"
                                )
                                # 失败步骤计入步数（本轮不随 continue 跳过）
                                step_count += 1
                                plan.current_step += 1
                                self.budget_manager.record_step(1)
                                continue
                            else:
                                logger.error(
                                    f"[重规划] 高优先级任务 {task.id} 失败，"
                                    f"重规划不可用或无调整空间 → 走中断路径"
                                )
                                break
                        else:
                            logger.warning(
                                f"[重规划] 任务 {task.id} 失败（priority={task.priority} < 4），"
                                f"不触发重规划，仅标记失败由收尾判定处理"
                            )

                    step_count += 1
                    plan.current_step += 1
                    # 阶段 3（D13）：预算记步（steps 维度按任务数累计，下一轮循环顶部 check）
                    self.budget_manager.record_step(1)
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

    def _warn_parallel_resource_conflicts(self, tasks: List[Task]) -> None:
        """并发共享状态校验警告（阶段 2 / D5）：并发任务不得写同一资源。

        启发式：解析每任务的目标工具与关键参数（filename/path/file/content），
        同批内两任务命中同一（工具, 资源键）时打 WARNING。仅警告不阻断——
        由上层依据工具语义决定是否规避并发写同一资源。
        """
        try:
            signatures: List[tuple] = []
            for task in tasks:
                tool_name = self.tool_registry.find_tool(task.description) or ""
                params = self._extract_params(task, tool_name)
                key_params = tuple(sorted(
                    (k, str(v)) for k, v in params.items()
                    if k in ("filename", "path", "file", "content")
                ))
                signatures.append((tool_name, key_params))
            seen = {}
            for i, sig in enumerate(signatures):
                tool_name, key_params = sig
                if not tool_name or not key_params:
                    continue
                if sig in seen:
                    logger.warning(
                        f"[并行执行] 并发资源冲突警告: 任务 {seen[sig].id}"
                        f"（{str(seen[sig].description)[:50]}）与任务 {tasks[i].id}"
                        f"（{str(tasks[i].description)[:50]}）"
                        f" 均写 {tool_name}（资源键 {dict(key_params)}），"
                        f"并发任务不得写同一资源"
                    )
                else:
                    seen[sig] = tasks[i]
        except Exception:
            logger.debug("[并行执行] 资源冲突预检失败（跳过，不阻断）")

    def _finalize_state(self, plan: Plan, target: PlanState, *, reason: str) -> None:
        """收尾状态变更：优先走状态机（触发钩子/记录转换历史），无状态机时降级直接赋值

        不变量：计划状态变更必须经状态机，确保合法性校验、转换历史与钩子回调不被旁路；
        但兼容 executor 独立使用（未注入状态机）时的直接赋值行为。
        边界 #2：若计划已处于 CANCELLED（取消竞态先行生效），保留取消状态不被收尾覆盖。
        边界 #H（漏洞H修复）：终态保护扩展——计划已处于任一终态（COMPLETED/FAILED/
        CANCELLED）时，非法收尾转换保留原终态，不被降级覆盖。否则重复执行已完成计划
        会在状态转换异常路径被错误降级为 FAILED（成功计划被标记失败）。
        """
        prev_state = plan.state
        logger.info(
            f"[时序] 收尾状态变更 @{_ts()} | {plan.id}: {prev_state.value} -> {target.value}"
        )
        if self.state_machine is not None:
            try:
                self.state_machine.transition(plan, target, reason)
            except InvalidStateTransitionError as e:
                if plan.state in (PlanState.COMPLETED, PlanState.FAILED, PlanState.CANCELLED):
                    # 终态优先于收尾：终态计划不被后续收尾覆盖（含取消竞态边界 #2）
                    logger.warning(
                        f"计划已处于终态（{plan.id}: {plan.state.value}），"
                        f"保留原状态，跳过收尾变更: {target.value}"
                    )
                    return
                logger.warning(f"状态机收尾转换失败，降级直接赋值: {e}")
                plan.state = target
        else:
            plan.state = target
        # 阶段 2（D9）：状态转换增量落库（审计可追溯）；失败仅告警不影响主流程
        if self.persistence is not None and plan.state != prev_state:
            try:
                self.persistence.record_transition(
                    plan_id=plan.id,
                    from_state=prev_state.value,
                    to_state=plan.state.value,
                    reason=reason,
                )
            except Exception as e:
                logger.warning(f"[D9] 状态转换落库失败: {e}")
            # D9 恢复正确性修复：同步计划终态到 plans 表。此前仅 record_transition 落
            # transition_history，plans.state 停留 READY；而 _RECOVERABLE_STATES 含 READY，
            # 崩溃恢复时会把已完成的计划误判为"未完成"恢复（执行记录/转换历史均已证明
            # 完成）。收尾恰为最后一次状态变更，此处 upsert 一次即可（非整树循环落库）。
            try:
                self.persistence.upsert_plan(plan)
            except Exception as e:
                logger.warning(f"[D9] 计划终态落库失败: {e}")

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

        _chain_start = time.perf_counter()
        total = len(backup_tools)
        for idx, backup_name in enumerate(backup_tools, start=1):
            if not self.tool_registry.has(backup_name):
                logger.warning(
                    f"[D14降级链] 备份工具不存在，跳过 [{idx}/{total}]: {backup_name}"
                )
                continue
            backup_action = Action.tool_action(
                tool_name=backup_name,
                params=action.tool_params,
                description=action.description,
            )
            _try_start = time.perf_counter()
            logger.info(
                f"[D14降级链] 主工具 {action.tool_name} 失败，尝试备份工具 [{idx}/{total}]: {backup_name}"
            )
            result = await self._execute_action(backup_action)
            _try_elapsed = time.perf_counter() - _try_start
            if result.success:
                result.observation = (
                    f"主工具 {action.tool_name} 失败，已降级至备份工具 {backup_name} 执行成功"
                )
                logger.info(
                    f"[D14降级链] 降级成功 [{idx}/{total}]: {action.tool_name} -> {backup_name}"
                    f" | 单次耗时: {_try_elapsed:.3f}s"
                    f" | 链总耗时: {time.perf_counter() - _chain_start:.3f}s"
                )
                return result
            logger.warning(
                f"[D14降级链] 备份工具失败 [{idx}/{total}]: {backup_name}"
                f" | 错误: {str(result.error)[:200]} | 单次耗时: {_try_elapsed:.3f}s"
            )
        logger.warning(
            f"[D14降级链] 全部 {total} 个备份工具失败 | 链总耗时: {time.perf_counter() - _chain_start:.3f}s"
        )
        return None
    
    async def _execute_task_with_retry(self, task: Task) -> ActionResult:
        """带重试的任务执行"""
        task.mark_running()
        try:
            return await self._execute_task_with_retry_internal(task)
        except Exception as e:
            last_error = str(e)
            # 阶段 3（D14）：任务级降级链——主工具重试耗尽后按 Task.fallback_actions
            # 顺序尝试备份工具；任一成功即返回成功，全部失败保留主工具错误
            if task.fallback_actions:
                logger.warning(
                    f"[D14任务级降级链] 任务 {task.id} 重试耗尽({self.max_retries} 次)进入降级链"
                    f" | fallback_actions={task.fallback_actions}"
                    f" | 末次错误: {last_error[:300]}"
                )
                fallback_result = await self._try_task_fallback(task)
                if fallback_result is not None:
                    return fallback_result
            logger.error(f"任务执行失败: {e}")
            return ActionResult.failure_result(last_error or "重试次数耗尽")

    async def _try_task_fallback(self, task: Task) -> Optional[ActionResult]:
        """D14：任务级降级链——主工具重试耗尽后按 Task.fallback_actions 顺序尝试备份工具

        Returns:
            任一备份工具成功 -> 成功结果（observation 标注已降级）；否则 None
            （由调用方保留主工具错误）。
        """
        fallback_start = time.perf_counter()
        total = len(task.fallback_actions)
        for idx, backup_name in enumerate(task.fallback_actions, start=1):
            if not self.tool_registry.has(backup_name):
                logger.warning(
                    f"[D14任务级降级链] 备份工具未注册，跳过 [{idx}/{total}]: {backup_name}"
                )
                continue
            _try_start = time.perf_counter()
            logger.info(
                f"[D14任务级降级链] 任务 {task.id} 主工具重试耗尽，尝试备份工具 {idx}/{total}: {backup_name}"
            )
            try:
                result = await self._execute_action(
                    Action.tool_action(
                        tool_name=backup_name,
                        params=self._extract_params(task, backup_name),
                        description=task.description,
                    )
                )
            except Exception as exc:
                logger.warning(
                    f"[D14任务级降级链] 备份工具 {backup_name} 抛异常: {exc}"
                )
                continue
            _try_elapsed = time.perf_counter() - _try_start
            if result.success:
                result.observation = (
                    f"主工具失败，已降级至备份工具 {backup_name} 执行成功（任务级降级链）"
                )
                logger.info(
                    f"[D14任务级降级链] 降级成功 {idx}/{total}: 任务 {task.id}"
                    f" | 备份工具: {backup_name} | 单次耗时: {_try_elapsed:.3f}s"
                    f" | 链总耗时: {time.perf_counter() - fallback_start:.3f}s"
                )
                return result
            logger.warning(
                f"[D14任务级降级链] 备份工具失败 {idx}/{total}: {backup_name}"
                f" | 错误: {str(result.error)[:200]} | 单次耗时: {_try_elapsed:.3f}s"
            )
        logger.warning(
            f"[D14任务级降级链] 任务 {task.id} 全部 {total} 个备份工具失败，保留主错误"
            f" | 链总耗时: {time.perf_counter() - fallback_start:.3f}s"
        )
        return None

    async def _replan_on_failure(self, plan: Plan, failed_task: Task) -> bool:
        """D14：高优先级任务失败后的重规划（Plan B）

        条件：replan_on_failure 开关开启 + decomposer 已注入（缺任一不可重规划）。
        流程：decomposer.refine(plan, feedback) 修正任务集 → 比对 refine 前后任务
        id 集合是否有变化判定"有调整空间"；有 → 记录 replanned 元数据返回 True；
        无（refine 原样返回/空调整/抛异常）→ 返回 False 由调用方走中断路径。

        Returns:
            True: 计划已修正，调用方应 continue 继续执行；
            False: 重规划不可用或无调整空间，调用方走中断路径。
        """
        if not self.replan_on_failure:
            logger.info(f"[重规划] replan_on_failure=false，跳过重规划: {failed_task.id}")
            return False
        if self.decomposer is None:
            logger.info(f"[重规划] 未注入 decomposer，无法重规划: {failed_task.id}")
            return False

        previous_ids = {t.id for t in plan.tasks}
        try:
            feedback = (
                f"任务 {failed_task.id}（优先级 {failed_task.priority}）执行失败："
                f"{str(failed_task.error)[:300]}。请调整计划：移除该任务或替换为可行替代。"
            )
            await self.decomposer.refine(plan, feedback)
        except Exception as e:
            logger.warning(f"[重规划] refine 失败（降级走中断路径）: {e}")
            return False

        new_ids = {t.id for t in plan.tasks}
        if new_ids == previous_ids:
            logger.info(
                f"[重规划] refine 无调整空间（任务集未变化），走中断路径: {failed_task.id}"
            )
            return False

        logger.info(
            f"[重规划] 任务集已调整 | 移除: {sorted(previous_ids - new_ids)}"
            f" | 新增: {sorted(new_ids - previous_ids)}"
        )
        plan.metadata["replanned"] = {
            "failed_task": failed_task.id,
            "previous_tasks": sorted(previous_ids),
            "new_tasks": sorted(new_ids),
        }
        return True

    async def _on_task_failed(self, plan: Plan, task: Task, result: ActionResult) -> None:
        """D14 失败归因：分类失败原因写入 task.metadata，并调 reflector 记录教训。

        不变量：归因不阻断主流程——reflector 异常仅告警（学习失败不影响执行结果）。
        """
        reason = self._classify_failure(task, result)
        task.metadata["failure_reason"] = reason
        logger.info(
            f"[失败归因] 任务 {task.id} 失败原因归类: {reason}"
            f" | 错误: {str(result.error or '')[:200]}"
        )
        if self.reflector is not None:
            try:
                # 签名对齐：learn_from_experience(task_description: str, result: ActionResult)
                # （旧实现误传单 dict，导致 TypeError 仅告警不落库；此处按现有接口传
                #  任务描述与失败结果，lesson 以 result.error 为 failure_point 落盘）
                await self.reflector.learn_from_experience(task.description, result)
            except Exception as e:
                logger.warning(f"[失败归因] reflector.learn_from_experience 失败（不阻断主流程）: {e}")

    @staticmethod
    def _classify_failure(task: Task, result: ActionResult) -> str:
        """D14 失败归因四分类：工具缺失 / 超时 / LLM 错误 / 逻辑错误"""
        error = str(result.error or "")
        if "工具不存在" in error:
            return "工具缺失"
        if "超时" in error or "Timeout" in error.lower():
            return "超时"
        if "LLM" in error:
            return "LLM错误"
        return "逻辑错误"

    async def _execute_task_with_retry_and_record(self, plan: Plan, task: Task) -> ActionResult:
        """执行单个任务并立即记录/标记状态（供并行批使用，D5 修复）

        不变量：任务完成（成功/失败）即更新状态与记录，不等待整批并行任务收尾，
        避免取消时已完成任务的状态滞留 RUNNING（竞态）。
        日志：任务级开始/动作解析/完成结果与耗时全链路打印，配合并行批日志
        还原并发时序，便于排查资源冲突等并行问题。
        """
        start = time.monotonic()
        logger.info(
            f"[时序] 并行任务开始 @{_ts()} | {task.id}"
            f" | 描述: {str(task.description)[:80]} | 优先级: {task.priority}"
        )
        tool_name = self.tool_registry.find_tool(task.description) or ""
        logger.info(
            f"[时序] 动作解析 @{_ts()} | {task.id} -> 工具: {tool_name or 'llm/response'}"
            f" | 参数: {self._extract_params(task, tool_name or None)}"
        )
        result = await self._execute_task_with_retry(task)
        elapsed = time.monotonic() - start
        self._record_execution(plan, task, result)
        if result.success:
            task.mark_completed(result.output)
            await self._trigger_callbacks("on_task_complete", task, result)
            logger.info(
                f"[时序] 并行任务完成 @{_ts()} | {task.id} | 耗时: {elapsed:.2f}s"
                f" | 输出: {str(result.output)[:100]}"
            )
        else:
            task.mark_failed(result.error or "未知错误")
            await self._trigger_callbacks("on_task_fail", task, result)
            logger.warning(
                f"[时序] 并行任务失败 @{_ts()} | {task.id} | 耗时: {elapsed:.2f}s"
                f" | 错误: {str(result.error)[:150]}"
            )
            # 阶段 3（D14）：失败归因（并行路径同样记录，便于统一审计）
            await self._on_task_failed(plan, task, result)
            if task.priority >= 4:
                logger.error(
                    f"高优先级任务失败: {task.id}"
                    f"（并行路径不触发重规划，仅标记失败由收尾判定处理）"
                )
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

        logger.info(f"[时序] 工具调用开始 @{_ts()} | {action.tool_name}")
        logger.debug(f"[工具调用] DEBUG: 参数: {action.tool_params}")
        _call_start = time.monotonic()

        try:
            timeout = self.config.get('tool_timeout', 30)
            
            if asyncio.iscoroutinefunction(tool):
                try:
                    output = await asyncio.wait_for(
                        tool(**action.tool_params),
                        timeout=timeout
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        f"[时序] 工具调用超时 @{_ts()} | {action.tool_name}"
                        f" | 耗时: {(time.monotonic() - _call_start) * 1000:.0f}ms"
                        f" | 超时时间: {timeout}秒"
                    )
                    logger.error(f"[工具调用] TIMEOUT: 参数: {action.tool_params}")
                    return ActionResult.failure_result(
                        f"工具调用超时: {action.tool_name} (超时时间: {timeout}秒)"
                    )
            else:
                output = tool(**action.tool_params)

            logger.info(
                f"[时序] 工具调用成功 @{_ts()} | {action.tool_name}"
                f" | 耗时: {(time.monotonic() - _call_start) * 1000:.0f}ms"
            )
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
        # D9：取消状态同步落库（保持 plans 表与 transition_history 一致，避免恢复误判）
        if self.persistence is not None:
            try:
                self.persistence.upsert_plan(plan)
            except Exception as e:
                logger.warning(f"[D9] 取消状态落库失败: {e}")
        task = self._running_tasks.get(plan.id)
        if task is not None and not task.done():
            task.cancel()
        return plan

    def get_history(self, limit: int = 50) -> List[Dict]:
        """获取执行历史"""
        records = self.execution_history[-limit:]
        return [r.to_dict() for r in records]
