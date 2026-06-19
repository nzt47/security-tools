"""ReAct循环引擎单元测试"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from planning.react import ReActLoop, ReActStep, ReActResult, ThoughtResult
from planning.executor import ToolRegistry
from planning.models import Action, ActionType, ActionResult


class TestReActStep:
    """ReAct步骤单元测试"""

    def test_step_creation(self):
        """测试步骤创建"""
        step = ReActStep(
            iteration=0,
            thought="思考内容",
            action="执行动作",
            observation="观察结果",
            success=True
        )
        
        assert step.iteration == 0
        assert step.thought == "思考内容"
        assert step.action == "执行动作"
        assert step.success is True


class TestReActResult:
    """ReAct结果单元测试"""

    def test_result_success(self):
        """测试成功结果"""
        steps = [ReActStep(iteration=0, thought="步骤1", action="动作1", observation="观察1", success=True)]
        result = ReActResult(
            success=True,
            result="成功完成",
            steps=steps,
            iterations=1,
            total_duration_ms=1000
        )
        
        assert result.success is True
        assert result.result == "成功完成"
        assert len(result.steps) == 1
        assert result.iterations == 1

    def test_result_failure(self):
        """测试失败结果"""
        result = ReActResult(
            success=False,
            result="失败",
            steps=[],
            iterations=5,
            total_duration_ms=5000,
            error="超时"
        )
        
        assert result.success is False
        assert result.error == "超时"


class TestThoughtResult:
    """思考结果单元测试"""

    def test_thought_creation(self):
        """测试思考结果创建"""
        thought = ThoughtResult(
            reasoning="推理过程",
            action_type="tool_call",
            action=Action.tool_action("search", {"query": "test"}, "搜索"),
            confidence=0.8,
            result=None,
            next_steps=["下一步"]
        )
        
        assert thought.reasoning == "推理过程"
        assert thought.action_type == "tool_call"
        assert thought.confidence == 0.8


class TestReActLoop:
    """ReAct循环引擎单元测试"""

    def test_react_loop_initialization(self):
        """测试ReAct循环初始化"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector, max_iterations=5)
        
        assert react_loop.planner == mock_planner
        assert react_loop.reflector == mock_reflector
        assert react_loop.max_iterations == 5

    def test_format_history_empty(self):
        """测试格式化空历史"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector)
        history = react_loop._format_history([])
        
        assert "(无历史,这是第一步)" in history

    def test_format_history_with_steps(self):
        """测试格式化历史"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector)
        steps = [
            ReActStep(iteration=0, thought="思考1", action="动作1", observation="观察1", success=True),
            ReActStep(iteration=1, thought="思考2", action="动作2", observation="观察2", success=True)
        ]
        
        history = react_loop._format_history(steps)
        
        assert "步骤0" in history
        assert "步骤1" in history
        assert "动作1" in history

    def test_format_context_empty(self):
        """测试格式化空上下文"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector)
        context = react_loop._format_context({})
        
        assert "(无上下文)" in context

    def test_format_context_with_data(self):
        """测试格式化上下文"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector)
        context = react_loop._format_context({"key1": "value1", "_private": "hidden"})
        
        assert "key1" in context
        assert "value1" in context
        assert "_private" not in context

    def test_format_tools_empty(self):
        """测试格式化空工具列表"""
        mock_planner = MagicMock()
        mock_planner.tool_registry.list_tools.return_value = []
        
        react_loop = ReActLoop(mock_planner, MagicMock())
        tools = react_loop._format_tools()
        
        assert "(无可用工具)" in tools

    def test_format_tools_with_tools(self):
        """测试格式化工具列表"""
        registry = ToolRegistry()
        registry.register("search", lambda: "result", {"description": "搜索工具"})
        
        mock_planner = MagicMock()
        mock_planner.tool_registry = registry
        
        react_loop = ReActLoop(mock_planner, MagicMock())
        tools = react_loop._format_tools()
        
        assert "search" in tools
        assert "搜索工具" in tools

    def test_parse_thought_valid_json(self):
        """测试解析有效JSON思考结果"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector)
        
        json_response = json.dumps({
            "reasoning": "测试推理",
            "action_type": "tool_call",
            "action": {
                "tool": "search",
                "params": {"query": "test"},
                "description": "搜索测试"
            },
            "confidence": 0.8
        })
        
        thought = react_loop._parse_thought(json_response)
        
        assert thought.reasoning == "测试推理"
        assert thought.action_type == "tool_call"
        assert thought.action.tool_name == "search"
        assert thought.confidence == 0.8

    def test_parse_thought_invalid_json(self):
        """测试解析无效JSON思考结果"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector)
        
        thought = react_loop._parse_thought("无效JSON响应")
        
        assert thought.reasoning == "无效JSON响应"
        assert thought.action_type == "finish"

    def test_rule_based_think_first_step_with_tool(self):
        """测试规则思考 - 第一步有工具"""
        registry = ToolRegistry()
        registry.register("search", lambda: "result")
        
        mock_planner = MagicMock()
        mock_planner.tool_registry = registry
        
        react_loop = ReActLoop(mock_planner, MagicMock())
        
        thought = react_loop._rule_based_think("使用search搜索信息", {}, [])
        
        assert thought.action_type == "tool_call"
        assert thought.action.tool_name == "search"

    def test_rule_based_think_first_step_no_tool(self):
        """测试规则思考 - 第一步无工具"""
        mock_planner = MagicMock()
        mock_planner.tool_registry = ToolRegistry()
        
        react_loop = ReActLoop(mock_planner, MagicMock())
        
        thought = react_loop._rule_based_think("简单任务", {}, [])
        
        assert thought.action_type == "finish"

    def test_rule_based_think_subsequent_step(self):
        """测试规则思考 - 后续步骤"""
        mock_planner = MagicMock()
        mock_planner.tool_registry = ToolRegistry()
        
        react_loop = ReActLoop(mock_planner, MagicMock())
        
        steps = [ReActStep(iteration=0, thought="步骤1", action="动作1", observation="观察1", success=True)]
        thought = react_loop._rule_based_think("任务", {}, steps)
        
        assert thought.action_type == "finish"

    def test_detect_loop_no_loop(self):
        """测试检测循环 - 无循环"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector)
        
        steps = [
            ReActStep(iteration=0, thought="思考1", action="动作1", observation="观察1", success=True),
            ReActStep(iteration=1, thought="思考2", action="动作2", observation="观察2", success=True),
            ReActStep(iteration=2, thought="思考3", action="动作3", observation="观察3", success=True)
        ]
        
        assert react_loop._detect_loop(steps) is False

    def test_detect_loop_with_loop(self):
        """测试检测循环 - 有循环"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector)
        
        steps = [
            ReActStep(iteration=0, thought="思考1", action="动作1", observation="观察1", success=True),
            ReActStep(iteration=1, thought="思考2", action="动作2", observation="观察2", success=True),
            ReActStep(iteration=2, thought="思考3", action="相同动作", observation="观察3", success=True),
            ReActStep(iteration=3, thought="思考4", action="相同动作", observation="观察4", success=True),
            ReActStep(iteration=4, thought="思考5", action="相同动作", observation="观察5", success=True),
            ReActStep(iteration=5, thought="思考6", action="相同动作", observation="观察6", success=True)
        ]
        
        assert react_loop._detect_loop(steps) is True

    def test_detect_loop_boundary_exactly_6_steps(self):
        """测试检测循环边界 - 恰好6步时的循环检测"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector)
        
        steps = [
            ReActStep(iteration=i, thought=f"思考{i}", action="相同动作", observation=f"观察{i}", success=True)
            for i in range(6)
        ]
        
        assert react_loop._detect_loop(steps) is True

    def test_detect_loop_boundary_5_steps_no_loop(self):
        """测试检测循环边界 - 5步时不应检测到循环"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector)
        
        steps = [
            ReActStep(iteration=i, thought=f"思考{i}", action="相同动作", observation=f"观察{i}", success=True)
            for i in range(5)
        ]
        
        assert react_loop._detect_loop(steps) is False

    def test_detect_loop_empty_steps(self):
        """测试检测循环 - 空步骤列表"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector)
        
        assert react_loop._detect_loop([]) is False

    def test_detect_loop_alternating_actions(self):
        """测试检测循环 - 交替动作不应检测为循环"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector)
        
        steps = [
            ReActStep(iteration=0, thought="思考1", action="动作A", observation="观察1", success=True),
            ReActStep(iteration=1, thought="思考2", action="动作B", observation="观察2", success=True),
            ReActStep(iteration=2, thought="思考3", action="动作A", observation="观察3", success=True),
            ReActStep(iteration=3, thought="思考4", action="动作B", observation="观察4", success=True),
            ReActStep(iteration=4, thought="思考5", action="动作A", observation="观察5", success=True),
            ReActStep(iteration=5, thought="思考6", action="动作B", observation="观察6", success=True)
        ]
        
        assert react_loop._detect_loop(steps) is False

    def test_detect_loop_mixed_pattern(self):
        """测试检测循环 - 混合模式，前3个相同后3个不同"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector)
        
        steps = [
            ReActStep(iteration=0, thought="思考1", action="动作A", observation="观察1", success=True),
            ReActStep(iteration=1, thought="思考2", action="动作A", observation="观察2", success=True),
            ReActStep(iteration=2, thought="思考3", action="动作A", observation="观察3", success=True),
            ReActStep(iteration=3, thought="思考4", action="动作B", observation="观察4", success=True),
            ReActStep(iteration=4, thought="思考5", action="动作C", observation="观察5", success=True),
            ReActStep(iteration=5, thought="思考6", action="动作D", observation="观察6", success=True)
        ]
        
        assert react_loop._detect_loop(steps) is False

    @pytest.mark.asyncio
    async def test_run_simple_finish(self):
        """测试运行ReAct循环 - 简单完成"""
        mock_planner = MagicMock()
        mock_planner.llm = None
        
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector, max_iterations=3)
        
        result = await react_loop.run("简单任务", {})
        
        assert result.success is True
        assert result.iterations == 2

    @pytest.mark.asyncio
    async def test_run_with_tool_call(self):
        """测试运行ReAct循环 - 工具调用"""
        def search_tool(query):
            return f"搜索结果: {query}"
        
        registry = ToolRegistry()
        registry.register("search", search_tool)
        
        mock_planner = MagicMock()
        mock_planner.tool_registry = registry
        mock_planner.llm = None
        
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector, max_iterations=3)
        
        result = await react_loop.run("使用search搜索", {})
        
        assert result.success is True
        assert result.iterations == 2

    @pytest.mark.asyncio
    async def test_run_max_iterations(self):
        """测试运行ReAct循环 - 达到最大迭代次数"""
        mock_planner = MagicMock()
        mock_planner.llm = AsyncMock()
        mock_planner.llm.chat.return_value = json.dumps({
            "reasoning": "继续思考",
            "action_type": "response",
            "result": "继续"
        })
        
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector, max_iterations=3)
        
        result = await react_loop.run("复杂任务", {})
        
        assert result.success is False
        assert result.error == "超时"
        assert result.iterations == 3
