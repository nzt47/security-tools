"""ReAct循环引擎

推理与行动交替执行
"""

import asyncio
import inspect
import json
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from .models import Plan, PlanState, Task
from .models.action import Action, ActionType, ActionResult
from .models.react import ReActStep, ReActResult, ThoughtResult
from .budget import BudgetManager, BudgetStatus, PlanBudget
from .reflector import format_advice_section, classify_task, Lesson
from .diagnostics import build_diagnosis
from .loop_detector import LoopDetector

from agent.monitoring.tracing import get_trace_id

logger = logging.getLogger(__name__)


class ReActLoop:
    """
    ReAct (Reasoning + Acting) 循环实现

    核心思想: 在推理和行动之间交替执行,逐步完成任务

    循环流程:
    1. Thought: 分析当前状态,决定下一步行动
    2. Action: 执行选定的行动
    3. Observation: 观察行动结果
    4. 如果任务完成,退出循环
    """

    THINKING_PROMPT = """作为云枢的思考引擎,分析当前状态并决定下一步行动。

当前任务: {task}

执行历史:
{history}

当前上下文:
{context}

可用工具:
{available_tools}

思考过程:
1. 分析当前状态: 我们已经完成了什么?
2. 识别目标: 距离完成任务还差什么?
3. 选择行动: 下一步应该做什么?
4. 制定计划: 具体如何执行?

请分析后输出JSON格式:
{{
    "reasoning": "详细推理过程",
    "action_type": "tool_call|response|finish|ask_user",
    "action": {{
        "tool": "工具名(如果是tool_call)",
        "params": {{参数名: 参数值}},
        "description": "行动描述"
    }},
    "confidence": 0.0-1.0,
    "result": "如果action_type是finish或response,这里放结果",
    "next_hint": "给下一步的提示"
}}"""

    def __init__(self, planner, reflector, max_iterations: int = 10, config: Dict = None):
        """
        初始化ReAct循环

        Args:
            planner: 规划引擎核心
            reflector: 反思引擎
            max_iterations: 最大迭代次数
            config: 配置
        """
        self.planner = planner
        self.reflector = reflector
        self.max_iterations = max_iterations
        self.config = config or {}

        # D13 优化：三层预算 + token/cost 可观测
        # - timeout_seconds   : deadline 预算（迭代级检查，既有 D13 修复）
        # - token_budget      : token 预算（估算累计，超限终止）
        # - cost_budget       : 成本预算（token × 单价，超限终止）
        # - token_price_per_1k: 每千 token 单价（USD），默认 0.002
        # - tool_timeout      : 异步工具调用硬超时（wait_for 包裹）；同步工具由 deadline 兜底
        # - budget_ask_user   : 预算超限行为——默认 False 直接终止（向后兼容）；
        #                       置 True 时降级为"征求用户"暂停，由调用方决定继续/终止
        self.deadline_seconds = self.config.get("timeout_seconds")
        self.token_budget = self.config.get("token_budget")
        self.cost_budget = self.config.get("cost_budget")
        self.token_price = self.config.get("token_price_per_1k", 0.002)
        self.tool_timeout = self.config.get("tool_timeout_seconds")
        self.budget_ask_user = self.config.get("budget_ask_user", False)
        # 任务4（D12/D6）：失败反思独立轮数上限（默认 2，防反思自身循环放大成本）
        self.reflection_retries = int(self.config.get("reflection_retries", 2))
        self._failure_reflection_count = 0
        # 任务5（D6 另一半）：状态哈希循环检测——窗口内同一状态指纹达阈值即终止；
        # 配置可调且必须被消费（防死配置，同 ROLLBACK_WINDOW_MIN 落地教训）
        self.loop_max_repeats = int(self.config.get("loop_max_repeats", 3))
        self.loop_window = int(self.config.get("loop_window", 8))
        self._loop_detector = LoopDetector(
            max_repeats=self.loop_max_repeats,
            window=self.loop_window,
        )
        self._token_used = 0
        self._cost = 0.0
        # 阶段 3（D13）：统一预算管理器（嵌套 budget 段优先，兼容直连键
        # token_budget→max_tokens / timeout_seconds→max_seconds / cost_budget→max_cost；
        # budget.enabled=false 整体关闭 = 零行为变化）
        self.budget_manager = BudgetManager(
            PlanBudget.from_config(self.config),
            token_price_per_1k=self.config.get("token_price_per_1k", 0.002),
        )
        # TD-4：探测 reflector.step_reflect 是否支持记账实例注入——
        # 旧签名 stub/实现（无 budget_manager 参数）调用时不传参，保持向后兼容
        self._reflect_supports_budget = False
        if self.reflector is not None:
            try:
                self._reflect_supports_budget = (
                    "budget_manager"
                    in inspect.signature(self.reflector.step_reflect).parameters
                )
            except (TypeError, ValueError):
                self._reflect_supports_budget = False

    async def run(self, task: str, context: Dict = None) -> ReActResult:
        """
        执行ReAct循环

        Args:
            task: 任务描述
            context: 执行上下文

        Returns:
            ReActResult: 执行结果
        """
        context = context or {}
        steps: List[ReActStep] = []
        start_time = datetime.now()

        # 阶段 3（D13）：开始本次执行预算记账（iterations/elapsed 重置，token/cost 生命周期累计）
        self.budget_manager.start()

        # P2 修复：区分三种终止原因（真超时/循环检测/迭代异常），
        # 避免三条 break 路径全部误报为"超时"（post-loop 按原因返回对应语义）。
        termination_reason: str = "timeout"  # 默认：迭代耗尽（真超时）
        termination_iteration: int = self.max_iterations
        termination_exception: Optional[Exception] = None
        # 任务5：状态哈希循环检测每任务重置（防跨任务状态污染）；命中时记录解释性摘要
        self._loop_detector.reset()
        loop_summary: Optional[str] = None

        logger.info("══════════════════════════════════════════════════════════════════")
        logger.info("🔄 [ReAct循环] =================================================")
        logger.info("🔄 [ReAct循环] 开始执行")
        logger.info(f"🔄 [ReAct循环] 任务: {task[:100]}{'...' if len(task) > 100 else ''}")
        logger.info(f"🔄 [ReAct循环] 最大迭代次数: {self.max_iterations}")
        logger.info(f"🔄 [ReAct循环] 初始上下文键: {list(context.keys())}")
        logger.info(f"🔄 [ReAct循环] 开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        logger.info("══════════════════════════════════════════════════════════════════")

        for iteration in range(self.max_iterations):
            iter_start = datetime.now()

            # 阶段 3（D13）：统一预算检查（steps/iterations/seconds/tokens/cost 维度，
            # 经 BudgetManager 单点判定），超限立即终止（budget_ask_user 启用时降级为
            # "征求用户"暂停，见 _budget_result）；超限快照落日志便于排查
            elapsed = (datetime.now() - start_time).total_seconds()
            budget_status = self.budget_manager.check()
            if budget_status != BudgetStatus.OK:
                snap = self.budget_manager.snapshot()
                logger.info(
                    f"[预算] 超限快照 | steps={snap['steps']} iterations={snap['iterations']}"
                    f" | elapsed={snap['elapsed_seconds']}s | tokens={snap['tokens']}"
                    f" | cost=${snap['cost']}"
                )
                detail_map = {
                    BudgetStatus.EXCEEDED_STEPS: f"超出步数预算({snap['steps']}步)",
                    BudgetStatus.EXCEEDED_ITERATIONS: f"超出迭代预算({snap['iterations']}次)",
                    BudgetStatus.EXCEEDED_SECONDS: f"超出时间预算({snap['elapsed_seconds']}s)",
                    BudgetStatus.EXCEEDED_TOKENS: f"超出token预算({snap['tokens']})",
                    BudgetStatus.EXCEEDED_COST: f"超出成本预算(${snap['cost']})",
                }
                detail = detail_map.get(budget_status, budget_status.value)
                logger.warning(
                    f"⚠️ [ReAct循环] 超出预算（{budget_status.value}），终止: {detail}"
                )
                return self._budget_result(detail, steps, iteration + 1, elapsed)
            # 阶段 3（D13）：记录本次迭代（iterations 维度预算）
            self.budget_manager.record_iteration(1)
            logger.info("──────────────────────────────────────────────────────────────────")
            logger.info(f"🔁 [迭代 {iteration + 1}/{self.max_iterations}] ────────────────")
            logger.info(f"🔁 [迭代 {iteration + 1}] 开始时间: {iter_start.strftime('%H:%M:%S.%f')[:-3]}")
            logger.info(f"🔁 [迭代 {iteration + 1}] 当前步骤数: {len(steps)}")

            try:
                logger.info("   ┌──────────────────────────────────────────────────────────┐")
                logger.info("   │ 💭 步骤1: 思考阶段                                      │")
                logger.info("   └──────────────────────────────────────────────────────────┘")
                thought = await self._think(task, context, steps)
                logger.info(f"   ✅ 思考完成")
                logger.info(f"      ├─ 推理: {thought.reasoning[:120]}{'...' if len(thought.reasoning) > 120 else ''}")
                logger.info(f"      ├─ 行动类型: {thought.action_type}")
                if thought.action:
                    logger.info(f"      ├─ 行动: {thought.action.description[:80]}{'...' if len(thought.action.description) > 80 else ''}")
                    if hasattr(thought.action, 'tool_name') and thought.action.tool_name:
                        logger.info(f"      ├─ 工具名: {thought.action.tool_name}")
                        if hasattr(thought.action, 'tool_params') and thought.action.tool_params:
                            logger.info(f"      └─ 工具参数: {thought.action.tool_params}")
                else:
                    logger.info(f"      └─ 行动: (无)")
                logger.info(f"      └─ 置信度: {thought.confidence:.2f}")

                if thought.action_type == "finish":
                    duration = (datetime.now() - start_time).total_seconds() * 1000
                    logger.info("   ┌──────────────────────────────────────────────────────────┐")
                    logger.info("   │ 🎉 检测到完成信号，结束循环                             │")
                    logger.info("   └──────────────────────────────────────────────────────────┘")
                    logger.info(f"   ✅ 任务已完成")
                    logger.info(f"      ├─ 执行步数: {iteration + 1}")
                    logger.info(f"      ├─ 总时长: {duration:.2f}ms")
                    # P2 修复：result 可能为 None（LLM 未返回 result 字段），
                    # 直接切片会 TypeError 被误判为迭代异常 → 判空兜底
                    logger.info(f"      └─ 结果: {(thought.result or '')[:80]}{'...' if len(thought.result or '') > 80 else ''}")
                    logger.info("══════════════════════════════════════════════════════════════════")
                    return self._result(
                        success=True,
                        result=thought.result or "任务完成",
                        steps=steps,
                        iterations=iteration + 1,
                        duration_ms=duration,
                    )

                logger.info("   ┌──────────────────────────────────────────────────────────┐")
                logger.info("   │ ⚡ 步骤2: 行动阶段                                      │")
                logger.info("   └──────────────────────────────────────────────────────────┘")
                action_result = await self._act(thought, context)
                logger.info(f"   ✅ 行动完成")
                logger.info(f"      ├─ 成功: {'✅ 是' if action_result.success else '❌ 否'}")
                if action_result.success:
                    output_str = str(action_result.output)
                    logger.info(f"      ├─ 输出: {output_str[:100]}{'...' if len(output_str) > 100 else ''}")
                else:
                    logger.info(f"      ├─ 错误: {action_result.error[:100]}{'...' if len(action_result.error or '') > 100 else ''}")

                observation = self._format_observation(action_result, thought)
                # D3 修复：保留 awaiting_user_input 专用观察标记（_format_observation 会将
                # 失败结果格式化为"失败: ..."，这里还原为标记，供上层识别"等待用户输入"状态）
                if action_result.observation == "awaiting_user_input":
                    observation = "awaiting_user_input"
                logger.info(f"      └─ 观察: {observation[:120]}{'...' if len(observation) > 120 else ''}")

                step = ReActStep(
                    iteration=iteration,
                    thought=thought.reasoning,
                    action=thought.action.description if thought.action else "",
                    observation=observation,
                    success=action_result.success
                )
                steps.append(step)
                logger.info(f"   📝 步骤已记录: 迭代={iteration}, 成功={action_result.success}")

                # D3 修复：ask_user 为暂停信号——检测到 awaiting_user_input 标记
                # （或 ask_user 行动类型，向后兼容）即终止当前循环，返回"等待用户输入"，
                # 由上层 orchestrator 向用户询问与恢复（不再继续执行后续行动）。
                awaiting_user = (
                    action_result.observation == "awaiting_user_input"
                    or thought.action_type == "ask_user"
                )
                if awaiting_user:
                    logger.info("   ⏸️ 检测到 ask_user：暂停执行，等待用户输入")
                    duration = (datetime.now() - start_time).total_seconds() * 1000
                    logger.info(
                        f"   ⏸️ [ReAct循环] ask_user 终止循环 | 迭代数: {iteration + 1}"
                        f" | 时长: {duration:.2f}ms"
                        f" | result: {str(thought.result or '需要用户确认')[:60]}"
                    )
                    return self._result(
                        success=False,
                        result=thought.result or "需要用户确认",
                        error="等待用户输入",
                        steps=steps,
                        iterations=iteration + 1,
                        duration_ms=duration,
                    )

                if self.reflector and action_result.success:
                    logger.info("   ┌──────────────────────────────────────────────────────────┐")
                    logger.info("   │ 🧠 步骤3: 反思阶段                                      │")
                    logger.info("   └──────────────────────────────────────────────────────────┘")
                    try:
                        # D2 修复：step_reflect 需要 Task 对象；ReAct 路径 task 为字符串 → 包装转换
                        reflect_task = task if isinstance(task, Task) else Task(
                            id=f"react_step_{iteration}", description=str(task)
                        )
                        if self._reflect_supports_budget:
                            reflection = await self.reflector.step_reflect(reflect_task, action_result, context,
                                                                           budget_manager=self.budget_manager)
                        else:
                            reflection = await self.reflector.step_reflect(reflect_task, action_result, context)
                        if reflection.adjustments:
                            logger.info(f"   💡 反思建议: {reflection.adjustments[:100]}{'...' if len(reflection.adjustments) > 100 else ''}")
                            # D12 修复：调整建议写入 context，供后续 _think 提示词消费（闭环）
                            hints = context.setdefault("_hints", [])
                            if isinstance(hints, list):
                                hints.extend(str(a) for a in reflection.adjustments)
                                # 漏洞 G 修复：限制 _hints 上限（保留最近 20 条），防止
                                # context 无限膨胀并随计划持久化（D19b 仅清理结果缓存）
                                if len(hints) > 20:
                                    del hints[:-20]
                        else:
                            logger.info(f"   ✅ 反思通过，无调整建议")
                    except Exception as e:
                        # D2 修复：反思异常不得被静默吞掉——提升为 error 并记录 trace_id，
                        # 便于追踪定位（ReAct 路径的反思闭环因此可被观测与修复）。
                        logger.error(
                            f"   ❌ 反思执行失败: {type(e).__name__}: {e}"
                            f" (trace_id={get_trace_id()})"
                        )
                elif self.reflector and not action_result.success:
                    # 任务4（D12）：行动失败进入专门失败反思分支——
                    # 结构化诊断 → failure_reflect（根因/修复建议）→ 注入下一轮
                    logger.info("   ┌──────────────────────────────────────────────────────────┐")
                    logger.info("   │ 🧠 步骤3: 失败反思阶段                                  │")
                    logger.info("   └──────────────────────────────────────────────────────────┘")
                    try:
                        await self._failure_reflect(thought, action_result, task, context)
                    except Exception as e:
                        # 反思不阻断主循环：异常仅告警（守不易）
                        logger.error(
                            f"   ❌ 失败反思执行失败: {type(e).__name__}: {e}"
                            f" (trace_id={get_trace_id()})"
                        )
                else:
                    logger.info(f"   ⏭️ 跳过反思阶段 (reflector={self.reflector is not None}, success={action_result.success})")

                logger.info("   ┌──────────────────────────────────────────────────────────┐")
                logger.info("   │ 🔍 步骤4: 循环检测                                      │")
                logger.info("   └──────────────────────────────────────────────────────────┘")
                if self._detect_loop(steps):
                    logger.warning("   ⚠️ ⚠️ ⚠️ 检测到执行循环！")
                    logger.warning(f"      最近3个动作: {[s.action for s in steps[-3:]]}")
                    logger.warning("      强制终止循环以避免无限循环")
                    # P2 修复：记录终止原因为循环检测（区别于"超时"）
                    termination_reason = "loop_detected"
                    termination_iteration = iteration + 1
                    break

                # 任务5（D6 另一半）：状态哈希循环检测——与 _detect_loop 并行增强，
                # 捕获"状态实质重复但动作序列模式未达旧阈值"的循环（如参数级重复）。
                # 命中不直接异常退出：记录终止原因，post-loop 统一按 loop_detected 语义返回。
                loop_signal = self._loop_detector.check(
                    self._loop_detector.state_hash(thought, context, iteration)
                )
                if loop_signal is not None:
                    logger.warning("   ⚠️ ⚠️ ⚠️ [loop_terminated] 状态哈希循环检测触发！")
                    logger.warning(f"      重复状态摘要: {loop_signal.summary}")
                    logger.warning(f"      窗口内出现 {loop_signal.occurrences} 次（阈值 {self.loop_max_repeats}）")
                    termination_reason = "loop_detected"
                    termination_iteration = iteration + 1
                    loop_summary = loop_signal.summary
                    break

                if action_result.success:
                    key = f"_last_result_{iteration}"
                    context[key] = action_result.output
                    # D19b：仅保留最近 2 条结果缓存，防止 context 无限膨胀
                    stale_key = f"_last_result_{iteration - 2}"
                    if stale_key in context:
                        context.pop(stale_key, None)
                    logger.info(f"   💾 结果已缓存到上下文: {key}")

                iter_duration = (datetime.now() - iter_start).total_seconds() * 1000
                logger.info(f"   ✅ 迭代 {iteration + 1} 完成")
                logger.info(f"      └─ 迭代时长: {iter_duration:.2f}ms")

            except Exception as e:
                logger.error("   ┌──────────────────────────────────────────────────────────┐")
                logger.error("   │ ❌ 迭代异常                                              │")
                logger.error("   └──────────────────────────────────────────────────────────┘")
                logger.error(f"   ❌ 迭代 {iteration + 1} 异常: {type(e).__name__}: {e}")
                import traceback
                logger.error(f"   堆栈跟踪:\n{traceback.format_exc()}")
                steps.append(ReActStep(
                    iteration=iteration,
                    thought="发生异常",
                    action="",
                    observation=str(e),
                    success=False
                ))
                # P2 修复：记录终止原因为迭代异常（区别于"超时"）
                termination_reason = "iteration_error"
                termination_iteration = iteration + 1
                termination_exception = e
                break

        duration = (datetime.now() - start_time).total_seconds() * 1000
        # 任务5：状态哈希命中时把解释性摘要透出到 final_state（旧 _detect_loop 命中时无摘要）
        final_state_extra: Optional[Dict[str, Any]] = None
        # P2 修复：按终止原因生成不同的错误语义（真超时/循环检测/迭代异常）
        if termination_reason == "loop_detected":
            result_text = "检测到反馈循环,已终止执行"
            error_text = "检测到执行循环"
            if loop_summary:
                final_state_extra = {"loop_summary": loop_summary}
        elif termination_reason == "iteration_error":
            result_text = "迭代过程中发生异常"
            error_text = (
                f"迭代异常: {type(termination_exception).__name__}: {termination_exception}"
                if termination_exception else "迭代异常"
            )
        else:
            result_text = "达到最大迭代次数,任务未完成"
            error_text = "超时"
        logger.warning("══════════════════════════════════════════════════════════════════")
        logger.warning(f"⚠️ [ReAct循环] 终止 | 终止原因: {termination_reason}")
        logger.info(f"⚠️ [ReAct循环] 实际执行步数: {len(steps)}")
        logger.info(f"⚠️ [ReAct循环] 总时长: {duration:.2f}ms")
        logger.info(f"⚠️ [ReAct循环] 结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        logger.warning("══════════════════════════════════════════════════════════════════")
        return self._result(
            success=False,
            result=result_text,
            steps=steps,
            iterations=termination_iteration,
            duration_ms=duration,
            error=error_text,
            final_state_extra=final_state_extra,
        )

    async def _think(self, task: str, context: Dict, history: List[ReActStep]) -> ThoughtResult:
        """思考: 分析当前状态,决定下一步行动"""
        logger.debug("   [思考] 准备思考提示词...")

        history_text = self._format_history(history)
        context_text = self._format_context(context)
        tools_text = self._format_tools()

        prompt = self.THINKING_PROMPT.format(
            task=task,
            history=history_text,
            context=context_text,
            available_tools=tools_text
        )

        # D12 修复：历史调整建议回灌提示词（闭环，仅最近 5 条防膨胀）
        if context and context.get("_hints"):
            prompt += "\n\n【历史调整建议（需遵循）】\n" + "\n".join(
                f"- {h}" for h in context["_hints"][-5:]
            )

        # 阶段 4（D17）：工具失败后基于教训的下一步提示（_act 写入 context["_next_hint"]）
        if context and context.get("_next_hint"):
            prompt += f"\n\n【下一步提示（基于历史教训）】\n{context['_next_hint']}"

        # 任务4（D12/D6）：注入"此前失败尝试 + 根因 + 修复建议"，强制换思路
        # （第 2 轮起显式声明前 N 次尝试及失败原因，避免 LLM 重复同样错误）
        if context and context.get("_failure_history"):
            lines = []
            for h in context["_failure_history"]:
                lines.append(
                    f"- 第{h.get('attempt')}次失败: {h.get('action') or '?'} → "
                    f"{h.get('error') or '?'}（猜测根因: {h.get('guess') or '未知'}）"
                )
            prompt += "\n\n【失败反思记录（前 N 次尝试及失败原因，请换思路）】\n" + "\n".join(lines)

        # D17 修复：思考阶段复用 reflector 历史经验（get_advice_for_task），
        # 成功模式/常见陷阱嵌入提示词（format_advice_section 统一格式，注入段标注
        # "历史经验"）；查询失败不影响思考主流程（降级为无经验）。
        if self.reflector:
            try:
                advice = self.reflector.get_advice_for_task(str(task))
                section = format_advice_section(advice)
                if section:
                    prompt += "\n\n" + section
            except Exception as e:
                logger.warning(f"[D17] 获取历史经验失败: {e}")

        if self.planner.llm:
            try:
                logger.debug("   [思考] 正在调用LLM...")
                response = await self.planner.llm.chat([{"role": "user", "content": prompt}])
                # 阶段 3（D13）：token/cost 统一经 budget_manager 记账（字符/3 估算），
                # _token_used/_cost 与管理器同步（单一来源，避免双轨漂移）
                self.budget_manager.record_text(prompt)
                self.budget_manager.record_text(response)
                self._token_used = self.budget_manager.tokens
                self._cost = self.budget_manager.cost
                logger.debug(f"   [思考] token 累计: {self._token_used} (估算), cost: ${self._cost:.4f}")
                logger.debug("   [思考] LLM响应已接收")
                return self._parse_thought(response)
            except Exception as e:
                logger.warning(f"   [思考] ⚠️ LLM思考失败: {e}")
                logger.info("   [思考] 回退到规则思考...")

        logger.info("   [思考] 使用规则降级思考")
        return self._rule_based_think(task, context, history)

    async def _act(self, thought: ThoughtResult, context: Dict) -> ActionResult:
        """执行行动"""
        logger.debug(f"   [行动] 开始执行行动，类型: {thought.action_type}")

        if thought.action_type == "response":
            logger.info("   [行动] 行动类型: 直接响应")
            return ActionResult.success_result(
                output=thought.result,
                observation="直接返回响应"
            )

        if thought.action_type == "ask_user":
            logger.info(
                "   [行动] 行动类型: 询问用户"
                "（返回 success=False + awaiting_user_input 标记，等待用户输入）"
            )
            # D3 修复：ask_user 是"等待用户输入"的暂停信号，不是成功结果。
            # 返回 success=False + 专用观察标记 awaiting_user_input，供 ReAct 循环
            # 检测后终止当前循环，由上层 orchestrator 向用户询问并恢复。
            return ActionResult(
                success=False,
                output=thought.result or "需要用户确认",
                observation="awaiting_user_input",
                error="等待用户输入",
            )

        if thought.action and thought.action.tool_name:
            tool_name = thought.action.tool_name
            logger.info(f"   [行动] 行动类型: 工具调用")
            logger.info(f"   [行动] 工具名: {tool_name}")
            logger.info(f"   [行动] 参数: {thought.action.tool_params}")

            tool = self.planner.tool_registry.get(tool_name)
            if tool:
                try:
                    logger.info(f"   [行动] 开始调用工具...")
                    if asyncio.iscoroutinefunction(tool):
                        # D13 优化：异步工具调用硬超时（wait_for 包裹，防慢工具拖死循环）；
                        # 同步工具无法在事件循环内硬中断，由迭代级 deadline 预算兜底
                        if self.tool_timeout is not None:
                            output = await asyncio.wait_for(
                                tool(**thought.action.tool_params), timeout=self.tool_timeout
                            )
                        else:
                            output = await tool(**thought.action.tool_params)
                        logger.info(f"   [行动] ✅ 异步工具调用成功")
                    else:
                        output = tool(**thought.action.tool_params)
                        logger.info(f"   [行动] ✅ 同步工具调用成功")
                    logger.info(f"   [行动] 输出: {str(output)[:80]}")
                    return ActionResult.success_result(
                        output=output,
                        observation=f"{tool_name}执行成功"
                    )
                except asyncio.TimeoutError:
                    logger.error(f"   [行动] ❌ 工具调用超时(>{self.tool_timeout}s): {tool_name}")
                    # 阶段 4（D17）：超时失败触发 lessons_db 检索 + next_hint 注入
                    logger.info(f"   [教训引导] 失败类型=超时 | tool={tool_name} | 开始查询 lessons_db")
                    lessons = self._write_lesson_hint(context, thought, tool_name)
                    self._log_lessons_result(lessons, tool_name)
                    return ActionResult.failure_result(f"工具调用超时(>{self.tool_timeout}s): {tool_name}")
                except Exception as e:
                    logger.error(f"   [行动] ❌ 工具执行失败: {e}")
                    # 阶段 4（D17）：异常失败触发 lessons_db 检索 + next_hint 注入
                    logger.info(f"   [教训引导] 失败类型=异常({type(e).__name__}) | tool={tool_name} | 开始查询 lessons_db")
                    lessons = self._write_lesson_hint(context, thought, tool_name)
                    self._log_lessons_result(lessons, tool_name)
                    return ActionResult.failure_result(f"工具执行失败: {e}")
            else:
                logger.warning(f"   [行动] ⚠️ 工具不存在: {tool_name}")
                # 阶段 4（D17）：工具缺失触发 lessons_db 检索 + next_hint 注入
                logger.info(f"   [教训引导] 失败类型=工具不存在 | tool={tool_name} | 开始查询 lessons_db")
                lessons = self._write_lesson_hint(context, thought, tool_name)
                self._log_lessons_result(lessons, tool_name)
                return ActionResult.failure_result(f"工具不存在: {tool_name}")

        if thought.result:
            logger.info("   [行动] 行动类型: LLM回复")
            return ActionResult.success_result(
                output=thought.result,
                observation="使用LLM回复"
            )

        logger.warning("   [行动] ⚠️ 无法确定执行动作")
        return ActionResult.failure_result("无法确定执行动作")

    def _log_lessons_result(self, lessons: List[Lesson], tool_name: str) -> None:
        """打印 lessons_db 查询的具体返回内容（含 task_description/failure_point/
        solution 全字段），便于验证教训注入逻辑；空结果时打印空列表。"""
        details = [lesson.to_dict() for lesson in lessons]
        logger.info(f"   [教训引导] lessons_db 查询返回内容: {details} | tool={tool_name}")

    def _write_lesson_hint(self, context: Dict, thought: ThoughtResult, tool_name: str) -> List[Lesson]:
        """阶段 4（D17）：工具失败时从教训库查询同类教训，生成下一步提示写入
        context["_next_hint"]（供下轮 _think 注入提示词引导下一步）。

        教训库无同类记录 / 查询失败时静默跳过（不改变失败语义，仅增强引导）。
        日志：失败上下文 → 分类 → 查询 → 命中/未命中 → 注入，全链路可观测。

        Returns:
            List[Lesson]: 本次查询返回的教训列表（供 _act 打印具体内容验证）
        """
        if not self.reflector:
            logger.debug(f"[教训引导] reflector 未配置，跳过 next_hint（tool={tool_name}）")
            return []
        try:
            desc = thought.action.description if thought.action else ""
            task_type = classify_task(desc or tool_name)
            logger.info(
                f"[教训引导] 检索开始 | tool={tool_name}"
                f" | 失败描述: {str(desc)[:60] or '(空)'}"
                f" | task_type={task_type}"
            )
            lessons = self.reflector.query_lessons(task_type, limit=1)
            logger.info(
                f"[教训引导] lessons_db 查询返回 {len(lessons)} 条同类教训"
                f" | task_type={task_type} | tool={tool_name}"
            )
            if lessons:
                lesson = lessons[0]
                logger.info(
                    f"[教训引导] 命中教训 | lesson_id={lesson.id}"
                    f" | task_description={str(lesson.task_description)[:60]}"
                    f" | failure_point={str(lesson.failure_point)[:60]}"
                    f" | solution={str(lesson.solution)[:60]}"
                )
                hint = (
                    f"工具 {tool_name} 执行失败；同类任务曾失败："
                    f"{lesson.failure_point[:80]}。"
                    f"建议 {lesson.solution or '参考该教训调整参数或更换工具后再试'}"
                )
                context["_next_hint"] = hint
                logger.info(f"[教训引导] 已注入 context['_next_hint'] | hint={hint[:100]}")
            else:
                logger.info(
                    f"[教训引导] 教训库无同类记录，跳过注入 | tool={tool_name}"
                    f" | task_type={task_type}（不改变失败语义）"
                )
            return lessons
        except Exception as e:
            logger.warning(f"[教训引导] 教训查询失败（不阻断失败语义）: {e}")
            return []

    async def _failure_reflect(self, thought: ThoughtResult, action_result: ActionResult,
                               task: str, context: Dict) -> None:
        """任务4（D12）：失败反思——结构化诊断 → reflector.failure_reflect → 注入下一轮。

        反思轮数受 reflection_retries 上限约束（D6 收敛），超限终止反思并升级；
        反思不阻断主循环：LLM/教训沉淀异常均仅告警（守不易）。
        """
        if self._failure_reflection_count >= self.reflection_retries:
            logger.warning(
                f"   ⚠️ 失败反思轮数达上限({self.reflection_retries})，终止反思并升级"
                f"（建议上层降级/人工兜底）"
            )
            return
        self._failure_reflection_count += 1

        tool_name = thought.action.tool_name if thought.action else None
        diagnosis = build_diagnosis(
            action_result,
            attempts=self._failure_reflection_count,
            history=self._failure_history(context),
            tool_name=tool_name,
            project_context=self._project_context_summary(),
        )
        logger.info(
            f"   [失败反思] 结构化诊断 | attempt={diagnosis.attempt}"
            f" | error_type={diagnosis.error_type} | tool={tool_name or '(无)'}"
            f" | repair_hints={diagnosis.repair_hints}"
        )

        if not self.reflector:
            return
        # D2 同款包装：ReAct 路径 task 为字符串 → 转 Task 对象
        reflect_task = task if isinstance(task, Task) else Task(
            id=f"react_fail_{diagnosis.attempt}", description=str(task)
        )
        reflection = await self.reflector.failure_reflect(
            reflect_task, action_result, diagnosis, self._failure_reflection_count
        )
        # 防御：反思产物缺失 repair_actions 字段（如 stub/MagicMock）时跳过注入，不阻断循环
        if reflection is None or not isinstance(getattr(reflection, "repair_actions", None), list):
            logger.info("   [失败反思] 反思无有效产出，跳过注入（不阻断循环）")
            return

        # 修复建议注入（复用 _hints 机制，供下一轮 _think 消费；上限 20 条防膨胀）
        if reflection.repair_actions:
            hints = context.setdefault("_hints", [])
            if isinstance(hints, list):
                hints.extend(str(a) for a in reflection.repair_actions)
                if len(hints) > 20:
                    del hints[:-20]
        # 失败历史记录（供第 N 轮"换思路"与 _think 注入；保留最近 3 轮）
        history = context.setdefault("_failure_history", [])
        if isinstance(history, list):
            history.append({
                "attempt": diagnosis.attempt,
                "action": thought.action.description if thought.action else "",
                "error_type": diagnosis.error_type,
                "error": diagnosis.error_message[:100],
                "guess": reflection.root_cause or "",
            })
            if len(history) > 3:
                del history[:-3]
        logger.info(
            f"   [失败反思] 已完成 | attempt={diagnosis.attempt}"
            f" | root_cause={reflection.root_cause}"
            f" | repair_actions={reflection.repair_actions} | 已注入下一轮"
        )

    @staticmethod
    def _failure_history(context: Dict) -> List[Dict]:
        """读取 context 中的失败历史摘要（无则空列表）"""
        return list(context.get("_failure_history") or [])

    def _project_context_summary(self) -> Dict:
        """项目上下文摘要：可用工具清单（裁剪至 token 预算），失败不影响诊断"""
        try:
            tools = self.planner.tool_registry.list_tools()
            return {"available_tools": tools[:20]}
        except Exception:
            return {}

    def _format_history(self, history: List[ReActStep]) -> str:
        """格式化执行历史"""
        if not history:
            return "(无历史,这是第一步)"

        lines = []
        for step in history[-5:]:
            lines.append(f"- 步骤{step.iteration}: {step.thought[:100]}")
            lines.append(f"  行动: {step.action}")
            lines.append(f"  结果: {step.observation[:100]}")
        return "\n".join(lines)

    def _format_context(self, context: Dict) -> str:
        """格式化上下文"""
        if not context:
            return "(无上下文)"

        lines = []
        for key, value in list(context.items()):
            if not key.startswith("_"):
                lines.append(f"- {key}: {str(value)[:50]}")
        return "\n".join(lines) if lines else "(无上下文)"

    def _format_tools(self) -> str:
        """格式化可用工具列表"""
        tools = self.planner.tool_registry.list_tools()
        if not tools:
            return "(无可用工具)"

        lines = ["可用工具:"]
        for tool in tools:
            schema = self.planner.tool_registry.get_schema(tool)
            if schema:
                lines.append(f"- {tool}: {schema.get('description', '')}")
            else:
                lines.append(f"- {tool}")
        return "\n".join(lines)

    def _format_observation(self, result: ActionResult, thought: ThoughtResult) -> str:
        """格式化观察结果"""
        if result.success:
            output = str(result.output)[:100] if result.output else ""
            return f"成功: {output}"
        else:
            return f"失败: {result.error}"

    def _parse_thought(self, response: str) -> ThoughtResult:
        """解析思考结果"""
        try:
            data = json.loads(response)
            action = None

            if data.get("action") and data["action"].get("tool"):
                action = Action(
                    id=f"action_{data['action']['tool']}",
                    tool_name=data["action"]["tool"],
                    tool_params=data["action"].get("params", {}),
                    description=data["action"].get("description", ""),
                    action_type=ActionType.TOOL_CALL
                )
            elif data.get("result"):
                action = Action.response_action(data["result"])

            return ThoughtResult(
                reasoning=data.get("reasoning", ""),
                action_type=data.get("action_type", "finish"),
                action=action,
                confidence=data.get("confidence", 0.5),
                result=data.get("result"),
                next_steps=data.get("next_hint", [])
            )
        except json.JSONDecodeError:
            logger.warning(f"思考结果JSON解析失败")
            return ThoughtResult(
                reasoning=response[:200],
                action_type="finish",
                result=response
            )

    def _rule_based_think(self, task: str, context: Dict, history: List[ReActStep]) -> ThoughtResult:
        """基于规则的思考(降级方案)"""
        if not history:
            tool_name = self.planner.tool_registry.find_tool(task)
            if tool_name:
                return ThoughtResult(
                    reasoning="使用工具执行",
                    action_type="tool_call",
                    action=Action.tool_action(tool_name, {}, task),
                    confidence=0.7
                )
            return ThoughtResult(
                reasoning="直接使用LLM回复",
                action_type="finish",
                result="这是云枢的回复",
                confidence=0.5
            )

        return ThoughtResult(
            reasoning="任务已处理完成",
            action_type="finish",
            result="已完成任务处理",
            confidence=0.9
        )

    def _detect_loop(self, steps: List[ReActStep], max_similar: int = 3) -> bool:
        """检测执行循环（连续重复 / 周期振荡两种模式）。

        - 连续重复：最近 max_similar 步动作完全相同（既有行为）
        - 周期振荡：最近 2*max_similar 步整体呈周期性（相邻等长段动作序列
          一致），覆盖 A/B/A/B 交替振荡与 A/B/C 周期循环——此类模式连续
          重复检测不到，会一直跑到迭代耗尽并被误报为超时（漏洞 F 修复）。
        """
        if len(steps) < max_similar * 2:
            return False

        actions = [s.action for s in steps]

        # 模式1：连续重复——最近 max_similar 步完全相同
        recent = actions[-max_similar:]
        if len(set(recent)) == 1 and recent[0]:
            return True

        # 模式2：周期振荡——窗口内每个位置与其同余周期位置动作一致
        # （window[i] == window[i % p]，p 为候选周期；防止 A/B/A/B... 交替循环漏检）
        window = actions[-(max_similar * 2):]
        for p in range(1, max_similar + 1):
            if all(window[i] == window[i % p] for i in range(len(window))):
                return True

        return False

    def _budget_result(self, detail: str, steps: List[ReActStep], iteration: int,
                       elapsed: float) -> ReActResult:
        """预算超限结果（D13 征求用户分支）。

        默认直接终止并返回预算错误（向后兼容既有语义）；
        启用 budget_ask_user 时降级为"征求用户"暂停——与 D3 ask_user 同一
        "等待用户输入"信号，由调用方向用户展示预算详情，用户可提高预算后
        重新执行或终止。
        """
        if self.budget_ask_user:
            return self._result(
                success=False,
                result=f"{detail}；等待用户确认（可提高预算后继续）",
                steps=steps, iterations=iteration,
                duration_ms=elapsed * 1000,
                error=f"等待用户输入：{detail}",
            )
        return self._result(
            success=False,
            result=detail,
            steps=steps, iterations=iteration,
            duration_ms=elapsed * 1000,
            error=detail,
        )

    def _result(self, *, success: bool, result: Any, steps: List[ReActStep],
                iterations: int, duration_ms: float, error: Optional[str] = None,
                final_state_extra: Optional[Dict[str, Any]] = None) -> ReActResult:
        """统一构造 ReActResult，透出 token/cost 预算可观测字段与 final_state 快照"""
        final_state: Dict[str, Any] = {}
        # 阶段 3（D13）：预算快照写入 final_state（可观测）；预算未启用时保持空 dict（向后兼容）
        if self.budget_manager.budget.enabled:
            snap = self.budget_manager.snapshot()
            snap["status"] = self.budget_manager.check().value
            final_state["budget"] = snap
        # 任务5：循环检测解释性摘要（可选透出，默认 None 零影响）
        if final_state_extra:
            final_state.update(final_state_extra)
        return ReActResult(
            success=success,
            result=result,
            steps=steps,
            iterations=iterations,
            total_duration_ms=int(duration_ms),
            error=error,
            token_used=self._token_used,
            cost=self._cost,
            final_state=final_state,
        )

    @staticmethod
    def _estimate_tokens(text: Any) -> int:
        """估算 token 数（字符/3 近似，中英混合通用；不依赖 LLM usage 结构）"""
        if not text:
            return 0
        return max(1, len(str(text)) // 3)
