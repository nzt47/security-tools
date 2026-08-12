"""ReAct循环引擎

推理与行动交替执行
"""

import asyncio
import json
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from .models import Plan, PlanState, Task
from .models.action import Action, ActionType, ActionResult
from .models.react import ReActStep, ReActResult, ThoughtResult

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
        self._token_used = 0
        self._cost = 0.0

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

        # P2 修复：区分三种终止原因（真超时/循环检测/迭代异常），
        # 避免三条 break 路径全部误报为"超时"（post-loop 按原因返回对应语义）。
        termination_reason: str = "timeout"  # 默认：迭代耗尽（真超时）
        termination_iteration: int = self.max_iterations
        termination_exception: Optional[Exception] = None

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

            # D13 优化：三层预算检查（deadline / token / cost），超限立即终止
            # （budget_ask_user 启用时降级为"征求用户"暂停，见 _budget_result）
            elapsed = (datetime.now() - start_time).total_seconds()
            if self.deadline_seconds is not None and elapsed >= self.deadline_seconds:
                logger.warning(f"⚠️ [ReAct循环] 超出时间预算({self.deadline_seconds}s)，终止")
                return self._budget_result(
                    f"超出时间预算({self.deadline_seconds}s)", steps, iteration, elapsed
                )
            if self.token_budget is not None and self._token_used >= self.token_budget:
                logger.warning(f"⚠️ [ReAct循环] 超出token预算({self._token_used}/{self.token_budget})，终止")
                return self._budget_result(
                    f"超出token预算({self._token_used}/{self.token_budget})", steps, iteration, elapsed
                )
            if self.cost_budget is not None and self._cost >= self.cost_budget:
                logger.warning(f"⚠️ [ReAct循环] 超出成本预算(${self._cost:.4f}/{self.cost_budget})，终止")
                return self._budget_result(
                    f"超出成本预算(${self._cost:.4f}/{self.cost_budget})", steps, iteration, elapsed
                )
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
                        reflection = await self.reflector.step_reflect(reflect_task, action_result, context)
                        if reflection.adjustments:
                            logger.info(f"   💡 反思建议: {reflection.adjustments[:100]}{'...' if len(reflection.adjustments) > 100 else ''}")
                            # D12 修复：调整建议写入 context，供后续 _think 提示词消费（闭环）
                            hints = context.setdefault("_hints", [])
                            if isinstance(hints, list):
                                hints.extend(str(a) for a in reflection.adjustments)
                        else:
                            logger.info(f"   ✅ 反思通过，无调整建议")
                    except Exception as e:
                        # D2 修复：反思异常不得被静默吞掉——提升为 error 并记录 trace_id，
                        # 便于追踪定位（ReAct 路径的反思闭环因此可被观测与修复）。
                        logger.error(
                            f"   ❌ 反思执行失败: {type(e).__name__}: {e}"
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
        # P2 修复：按终止原因生成不同的错误语义（真超时/循环检测/迭代异常）
        if termination_reason == "loop_detected":
            result_text = "检测到反馈循环,已终止执行"
            error_text = "检测到执行循环"
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

        # D17 修复：思考阶段复用 reflector 历史经验（get_advice_for_task），
        # 成功模式/常见陷阱嵌入提示词；查询失败不影响思考主流程（降级为无经验）。
        if self.reflector:
            try:
                advice = self.reflector.get_advice_for_task(str(task))
                if advice:
                    lines = []
                    patterns = advice.get("successful_patterns") or []
                    pitfalls = advice.get("common_pitfalls") or []
                    if patterns:
                        lines.append("成功模式（历史经验）:")
                        for p in patterns:
                            lines.append(f"- [{p['id']}] {p['description']} → {p['output']}")
                    if pitfalls:
                        lines.append("常见陷阱（历史教训）:")
                        for p in pitfalls:
                            lines.append(f"- [{p['id']}] {p['description']}（失败点: {p['failure']}）")
                    if lines:
                        prompt += "\n\n【历史经验】\n" + "\n".join(lines)
            except Exception as e:
                logger.warning(f"[D17] 获取历史经验失败: {e}")

        if self.planner.llm:
            try:
                logger.debug("   [思考] 正在调用LLM...")
                response = await self.planner.llm.chat([{"role": "user", "content": prompt}])
                # D13 优化：token/cost 可观测（估算：prompt + 响应按字符/3 近似，
                # 不依赖 LLM 响应 usage 结构，兼容纯文本返回）
                self._token_used += self._estimate_tokens(prompt) + self._estimate_tokens(response)
                self._cost = self._token_used / 1000 * self.token_price
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
                    return ActionResult.failure_result(f"工具调用超时(>{self.tool_timeout}s): {tool_name}")
                except Exception as e:
                    logger.error(f"   [行动] ❌ 工具执行失败: {e}")
                    return ActionResult.failure_result(f"工具执行失败: {e}")
            else:
                logger.warning(f"   [行动] ⚠️ 工具不存在: {tool_name}")
                return ActionResult.failure_result(f"工具不存在: {tool_name}")

        if thought.result:
            logger.info("   [行动] 行动类型: LLM回复")
            return ActionResult.success_result(
                output=thought.result,
                observation="使用LLM回复"
            )

        logger.warning("   [行动] ⚠️ 无法确定执行动作")
        return ActionResult.failure_result("无法确定执行动作")

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
        """检测执行循环"""
        if len(steps) < max_similar * 2:
            return False

        recent_steps = steps[-max_similar:]
        actions = [step.action for step in recent_steps]
        if len(set(actions)) == 1 and actions:
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
                iterations: int, duration_ms: float, error: Optional[str] = None) -> ReActResult:
        """统一构造 ReActResult，透出 token/cost 预算可观测字段"""
        return ReActResult(
            success=success,
            result=result,
            steps=steps,
            iterations=iterations,
            total_duration_ms=int(duration_ms),
            error=error,
            token_used=self._token_used,
            cost=self._cost,
        )

    @staticmethod
    def _estimate_tokens(text: Any) -> int:
        """估算 token 数（字符/3 近似，中英混合通用；不依赖 LLM usage 结构）"""
        if not text:
            return 0
        return max(1, len(str(text)) // 3)
