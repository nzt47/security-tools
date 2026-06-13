"""ReAct循环引擎

推理与行动交替执行
"""

import asyncio
import json
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from .models import Plan, PlanState
from .models.action import Action, ActionType, ActionResult
from .models.react import ReActStep, ReActResult, ThoughtResult

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

        logger.info("="*60)
        logger.info("🔄 [ReAct循环] 开始执行")
        logger.info(f"   任务: {task[:100]}{'...' if len(task) > 100 else ''}")
        logger.info(f"   最大迭代次数: {self.max_iterations}")
        logger.info(f"   上下文键: {list(context.keys())}")
        logger.info("="*60)

        for iteration in range(self.max_iterations):
            logger.info("-"*60)
            logger.info(f"🔁 [迭代 {iteration + 1}/{self.max_iterations}] 开始")

            try:
                logger.info("   💭 步骤1: 思考阶段...")
                thought = await self._think(task, context, steps)
                logger.info(f"   ✅ 思考完成:")
                logger.info(f"      推理: {thought.reasoning[:80]}...")
                logger.info(f"      行动类型: {thought.action_type}")
                if thought.action:
                    logger.info(f"      行动: {thought.action.description[:60]}")
                logger.info(f"      置信度: {thought.confidence}")

                if thought.action_type == "finish":
                    duration = (datetime.now() - start_time).total_seconds() * 1000
                    logger.info(f"   🎉 检测到完成信号，结束循环")
                    logger.info(f"   执行时长: {duration:.2f}ms")
                    logger.info("="*60)
                    return ReActResult(
                        success=True,
                        result=thought.result or "任务完成",
                        steps=steps,
                        iterations=iteration + 1,
                        total_duration_ms=int(duration)
                    )

                logger.info("   ⚡ 步骤2: 行动阶段...")
                action_result = await self._act(thought, context)
                logger.info(f"   ✅ 行动完成:")
                logger.info(f"      成功: {action_result.success}")
                if action_result.success:
                    logger.info(f"      输出: {str(action_result.output)[:80]}")
                else:
                    logger.info(f"      错误: {action_result.error}")

                observation = self._format_observation(action_result, thought)
                logger.info(f"      观察: {observation[:100]}")

                step = ReActStep(
                    iteration=iteration,
                    thought=thought.reasoning,
                    action=thought.action.description if thought.action else "",
                    observation=observation,
                    success=action_result.success
                )
                steps.append(step)

                if self.reflector and action_result.success:
                    try:
                        logger.info("   🧠 步骤3: 反思阶段...")
                        reflection = await self.reflector.step_reflect(task, action_result, context)
                        if reflection.adjustments:
                            logger.info(f"   💡 反思建议: {reflection.adjustments}")
                        else:
                            logger.info(f"   ✅ 反思通过，无调整建议")
                    except Exception as e:
                        logger.warning(f"   ⚠️ 反思执行失败: {e}")

                if self._detect_loop(steps):
                    logger.warning("   ⚠️ 检测到执行循环")
                    break

                if action_result.success:
                    key = f"_last_result_{iteration}"
                    context[key] = action_result.output
                    logger.info(f"   💾 结果已缓存到上下文: {key}")

                logger.info(f"   ✅ 迭代 {iteration + 1} 完成")

            except Exception as e:
                logger.error(f"   ❌ 迭代 {iteration + 1} 异常: {e}")
                import traceback
                logger.error(f"   堆栈跟踪:\n{traceback.format_exc()}")
                steps.append(ReActStep(
                    iteration=iteration,
                    thought="发生异常",
                    action="",
                    observation=str(e),
                    success=False
                ))
                break

        duration = (datetime.now() - start_time).total_seconds() * 1000
        logger.warning(f"⚠️ 达到最大迭代次数 {self.max_iterations}，任务未完成")
        logger.info(f"   实际执行: {len(steps)} 步")
        logger.info(f"   总时长: {duration:.2f}ms")
        logger.info("="*60)
        return ReActResult(
            success=False,
            result="达到最大迭代次数,任务未完成",
            steps=steps,
            iterations=self.max_iterations,
            total_duration_ms=int(duration),
            error="超时"
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

        if self.planner.llm:
            try:
                logger.debug("   [思考] 正在调用LLM...")
                response = await self.planner.llm.chat([{"role": "user", "content": prompt}])
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
            logger.info("   [行动] 行动类型: 询问用户")
            return ActionResult.success_result(
                output=thought.result or "需要用户确认",
                observation="等待用户输入"
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
