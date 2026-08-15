"""ReAct循环引擎单元测试"""

import pytest
import json
import logging
import asyncio
from unittest.mock import AsyncMock, MagicMock
from planning.react import ReActLoop, ReActStep, ReActResult, ThoughtResult
from planning.executor import ToolRegistry
from planning.models import Action, ActionType, ActionResult
from planning.core import PlanningCore


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
        """测试检测循环 - 交替振荡（A/B/A/B）应检测为循环（漏洞F修复：
        交替模式连续重复检测不到，会一直跑到迭代耗尽并被误报为超时）"""
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
        
        assert react_loop._detect_loop(steps) is True

    def test_detect_loop_cycle_three_oscillation_detected(self):
        """测试检测循环 - 周期3振荡（A/B/C 循环）应检测为循环"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector)
        
        steps = [
            ReActStep(iteration=0, thought="思考1", action="动作A", observation="观察1", success=True),
            ReActStep(iteration=1, thought="思考2", action="动作B", observation="观察2", success=True),
            ReActStep(iteration=2, thought="思考3", action="动作C", observation="观察3", success=True),
            ReActStep(iteration=3, thought="思考4", action="动作A", observation="观察4", success=True),
            ReActStep(iteration=4, thought="思考5", action="动作B", observation="观察5", success=True),
            ReActStep(iteration=5, thought="思考6", action="动作C", observation="观察6", success=True)
        ]
        
        assert react_loop._detect_loop(steps) is True

    def test_detect_loop_alternating_then_break_not_loop(self):
        """测试检测循环 - 前4步交替但后2步打破模式 → 不应误判为循环"""
        mock_planner = MagicMock()
        mock_reflector = MagicMock()
        
        react_loop = ReActLoop(mock_planner, mock_reflector)
        
        steps = [
            ReActStep(iteration=0, thought="思考1", action="动作A", observation="观察1", success=True),
            ReActStep(iteration=1, thought="思考2", action="动作B", observation="观察2", success=True),
            ReActStep(iteration=2, thought="思考3", action="动作A", observation="观察3", success=True),
            ReActStep(iteration=3, thought="思考4", action="动作B", observation="观察4", success=True),
            ReActStep(iteration=4, thought="思考5", action="动作C", observation="观察5", success=True),
            ReActStep(iteration=5, thought="思考6", action="动作D", observation="观察6", success=True)
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

        # 意图：验证"达到最大迭代次数 → 超时"。mock 每轮返回相同 response（状态恒定），
        # 任务5 状态哈希会把它判为循环——用 loop_max_repeats 调大阈值隔离新检测，
        # 保住"超时"路径的测试意图（与 test_run_budget_timeout 用 timeout_seconds 同理）
        react_loop = ReActLoop(mock_planner, mock_reflector, max_iterations=3,
                               config={"loop_max_repeats": 99})

        result = await react_loop.run("复杂任务", {})
        
        assert result.success is False
        assert result.error == "超时"
        assert result.iterations == 3

    # ── P2 修复测试：三种终止原因区分（真超时/循环检测/迭代异常） ──────────────

    @pytest.mark.asyncio
    async def test_run_loop_detection_distinct_error(self):
        """P2 终止原因区分：循环检测不再误报'超时'，返回专属 error='检测到执行循环'"""
        registry = ToolRegistry()
        registry.register("tool_a", lambda x: f"结果{x}")

        mock_planner = MagicMock()
        mock_planner.tool_registry = registry
        mock_planner.llm = AsyncMock()
        # 每次思考返回同一工具调用 → 连续动作描述相同 → 触发循环检测。
        # 任务5 状态哈希接入后：同工具同参数指纹恒定，第 3 次重复即命中（旧 _detect_loop 需 6 步）
        mock_planner.llm.chat.return_value = json.dumps({
            "reasoning": "反复执行相同操作",
            "action_type": "tool_call",
            "action": {"tool": "tool_a", "params": {"x": 1}, "description": "调用tool_a"},
        })

        mock_reflector = MagicMock()
        react_loop = ReActLoop(mock_planner, mock_reflector, max_iterations=8)

        result = await react_loop.run("循环任务", {})

        assert result.success is False
        assert result.error == "检测到执行循环"
        assert "超时" not in result.error
        # 状态哈希检测在第 3 次重复（第 3 步）终止，而非耗尽 max_iterations（8）
        assert result.iterations == 3
        # 任务5：解释性摘要写入 final_state
        assert "loop_summary" in result.final_state

    @pytest.mark.asyncio
    async def test_run_iteration_error_distinct_error(self):
        """P2 终止原因区分：迭代内部未捕获异常不再误报'超时'，返回专属 error='迭代异常: ...'"""
        mock_planner = MagicMock()
        mock_planner.llm = AsyncMock()
        mock_reflector = MagicMock()
        react_loop = ReActLoop(mock_planner, mock_reflector, max_iterations=5)

        # 注入迭代边界异常：验证循环以'迭代异常'终止而非落入'超时'桶
        async def boom(*args, **kwargs):
            raise RuntimeError("思考内部故障")

        react_loop._think = boom

        result = await react_loop.run("异常任务", {})

        assert result.success is False
        assert result.error.startswith("迭代异常")
        assert "RuntimeError" in result.error
        assert "思考内部故障" in result.error
        assert "超时" not in result.error

    # ── P2 修复测试：finish 空值防御 ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_run_finish_without_result(self):
        """P2 finish 空值防御：LLM 返回 finish 缺 result 字段 → 不崩溃，正常成功返回"""
        mock_planner = MagicMock()
        mock_planner.llm = AsyncMock()
        # 缺 result 字段 → thought.result 为 None（修复前日志行切片崩溃 → 误报超时）
        mock_planner.llm.chat.return_value = json.dumps({
            "reasoning": "任务已完成",
            "action_type": "finish",
        })

        mock_reflector = MagicMock()
        react_loop = ReActLoop(mock_planner, mock_reflector, max_iterations=3)

        result = await react_loop.run("简单任务", {})

        assert result.success is True
        assert result.result == "任务完成"  # 空值兜底语义
        assert result.iterations == 1

    # ── 边界检查修复测试：预算终止迭代计数一致性 ─────────────────────────────

    @pytest.mark.asyncio
    async def test_run_budget_timeout_iterations_consistent(self):
        """边界：预算终止路径 iterations 与其他终止路径一致（1-based），首迭代即超限也应 >=1"""
        mock_planner = MagicMock()
        mock_planner.llm = None
        mock_reflector = MagicMock()
        react_loop = ReActLoop(mock_planner, mock_reflector, max_iterations=5,
                               config={"timeout_seconds": 0})

        result = await react_loop.run("预算任务", {})

        assert result.success is False
        assert result.iterations >= 1  # 修复前首迭代超限返回 0（0-based 不一致）
        assert "预算" in result.error

    # ── 漏洞F/G 修复测试：交替振荡循环检测 / _hints 无上限膨胀 ──────────────

    @pytest.mark.asyncio
    async def test_run_alternating_oscillation_detected(self):
        """漏洞F 修复：LLM 交替调用两个工具（A/B/A/B 振荡）→ 第 6 步触发循环检测，
        终止原因正确报 '检测到执行循环' 而非耗尽 max_iterations 误报超时"""
        registry = ToolRegistry()
        registry.register("tool_a", lambda x: f"结果A{x}")
        registry.register("tool_b", lambda x: f"结果B{x}")

        mock_planner = MagicMock()
        mock_planner.tool_registry = registry
        mock_planner.llm = AsyncMock()
        # 交替返回 tool_a / tool_b 调用 → 动作序列 A,B,A,B,...（周期2振荡）
        calls = [
            json.dumps({"reasoning": "先A", "action_type": "tool_call",
                        "action": {"tool": "tool_a", "params": {"x": 1}, "description": "调用tool_a"}}),
            json.dumps({"reasoning": "再B", "action_type": "tool_call",
                        "action": {"tool": "tool_b", "params": {"x": 1}, "description": "调用tool_b"}}),
        ]
        counter = {"i": 0}

        def fake_chat(*a, **k):
            resp = calls[counter["i"] % 2]
            counter["i"] += 1
            return resp

        mock_planner.llm.chat.side_effect = fake_chat

        mock_reflector = MagicMock()
        react_loop = ReActLoop(mock_planner, mock_reflector, max_iterations=10)

        result = await react_loop.run("振荡任务", {})

        assert result.success is False
        assert result.error == "检测到执行循环"
        assert "超时" not in result.error
        # 任务5 状态哈希接入后：A 在窗口内第 3 次出现（第 5 步）即命中
        # （旧 _detect_loop 需 6 步周期检测）；不耗尽 max_iterations（10）
        assert result.iterations == 5

    @pytest.mark.asyncio
    async def test_run_hints_capped(self):
        """漏洞G 修复：反思调整建议写回 context['_hints'] 后截断到上限（20 条），
        防止 context 无限膨胀并随计划持久化"""
        mock_planner = MagicMock()
        mock_planner.llm = AsyncMock()
        calls = [
            json.dumps({"reasoning": "先A", "action_type": "tool_call",
                        "action": {"tool": "tool_a", "params": {"x": 1}, "description": "调用tool_a"}}),
            json.dumps({"reasoning": "再B", "action_type": "tool_call",
                        "action": {"tool": "tool_b", "params": {"x": 1}, "description": "调用tool_b"}}),
        ]
        counter = {"i": 0}

        def fake_chat(*a, **k):
            resp = calls[counter["i"] % 2]
            counter["i"] += 1
            return resp

        mock_planner.llm.chat.side_effect = fake_chat

        registry = ToolRegistry()
        registry.register("tool_a", lambda x: f"结果A{x}")
        registry.register("tool_b", lambda x: f"结果B{x}")
        mock_planner.tool_registry = registry

        mock_reflector = MagicMock()
        reflection = MagicMock()
        reflection.adjustments = [f"建议{i}" for i in range(5)]  # 每轮反思追加 5 条
        mock_reflector.step_reflect = AsyncMock(return_value=reflection)

        react_loop = ReActLoop(mock_planner, mock_reflector, max_iterations=10)
        # 非空 context：run() 内 `context or {}` 对空 dict 会新建对象，需保持引用一致
        context = {"_seed": 1}

        await react_loop.run("振荡任务", context)

        # 6 轮反思 × 5 条 = 30 条 → 截断后保留最近 20 条
        assert len(context["_hints"]) == 20


class TestLoopTerminationCoreP1:
    """任务5 P1：_plan_chat 识别"决策循环终止"并与普通失败区分（core.py）"""

    @pytest.mark.asyncio
    async def test_plan_chat_loop_termination_response(self, tmp_path):
        """循环终止 → 响应含"决策循环"+摘要，且不重试（react_loop.run 仅调用 1 次）"""
        core = PlanningCore(llm_service=AsyncMock(), config={"reflector": {"persist_dir": str(tmp_path)}})
        react_result = ReActResult(
            success=False,
            result="检测到反馈循环,已终止执行",
            error="检测到执行循环",
            iterations=3,
            total_duration_ms=100.0,
            cost=0.0,
            steps=[],
            final_state={"loop_summary": "tool_a 重复出现 3 次 (window=8)"},
        )
        core.react_loop.run = AsyncMock(return_value=react_result)

        result = await core.chat("帮我完成一个复杂任务", {})

        assert result.used_planning is True
        assert "决策循环" in result.response
        assert "tool_a 重复出现 3 次" in result.response
        core.react_loop.run.assert_awaited_once()  # 不重试
        assert result.react_result is react_result

    @pytest.mark.asyncio
    async def test_plan_chat_ordinary_failure_keeps_original(self, tmp_path):
        """普通失败（无 loop_summary）→ 保持原文案，不受 P1 影响"""
        core = PlanningCore(llm_service=AsyncMock(), config={"reflector": {"persist_dir": str(tmp_path)}})
        react_result = ReActResult(
            success=False,
            result="出错了",
            error="工具调用失败",
            iterations=2,
            total_duration_ms=80.0,
            cost=0.0,
            steps=[],
            final_state={},
        )
        core.react_loop.run = AsyncMock(return_value=react_result)

        result = await core.chat("帮我完成一个复杂任务", {})

        assert result.used_planning is True
        assert "我遇到了一些问题: 工具调用失败" in result.response
        core.react_loop.run.assert_awaited_once()


class TestSnapshotIntegration:
    """任务5 步骤3：迭代头快照 + 反思超限还原重试（C1-C12 核心场景）

    公共装置：registry 注册 bad_tool（恒失败）；mock LLM 序列可复现失败→还原→重试。
    """

    @staticmethod
    def _loop(mock_llm, mock_reflector, tmp_path, **config):
        registry = ToolRegistry()
        registry.register("bad_tool", lambda: (_ for _ in ()).throw(Exception("boom")))
        mock_planner = MagicMock()
        mock_planner.tool_registry = registry
        mock_planner.llm = mock_llm
        return ReActLoop(mock_planner, mock_reflector, max_iterations=6,
                         config={"reflection_retries": 1, "snapshot_root": str(tmp_path), **config})

    @staticmethod
    def _tool_call(reasoning):
        return json.dumps({"reasoning": reasoning, "action_type": "tool_call",
                           "action": {"tool": "bad_tool", "params": {}, "description": "调用bad_tool"}})

    @staticmethod
    def _reflection():
        r = MagicMock()
        r.repair_actions = ["换思路"]
        return r

    @pytest.mark.asyncio
    async def test_restore_retry_once_success(self, tmp_path, caplog):
        """C2/C11：反思超限 → 快照还原 → 重试一轮成功；steps 完整记录失败步骤"""
        caplog.set_level(logging.INFO)
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = [
            self._tool_call("试工具"),
            self._tool_call("再试工具"),
            json.dumps({"reasoning": "完成", "action_type": "finish",
                        "result": "成功完成", "confidence": 0.9}),
        ]
        mock_reflector = MagicMock()
        mock_reflector.failure_reflect = AsyncMock(return_value=self._reflection())

        loop = self._loop(mock_llm, mock_reflector, tmp_path)
        result = await loop.run("失败后还原重试", {"session_id": "sess_c2"})

        assert result.success is True
        assert result.iterations == 3                 # 2 失败 + 1 finish
        assert len(result.steps) == 2                 # finish 不 append（既有行为），失败步骤完整
        assert all(not s.success for s in result.steps)
        assert "snapshot_restored" in caplog.text     # 验收7：还原日志含标记
        assert mock_reflector.failure_reflect.await_count == 1  # 超限轮不再反思

    @pytest.mark.asyncio
    async def test_restore_only_once(self, tmp_path, caplog):
        """C4：还原后重试仍失败 → 不再二次还原（_restore_attempted 拦截）"""
        caplog.set_level(logging.INFO)
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = [
            self._tool_call("第1次"),
            self._tool_call("第2次"),     # 超限 → 还原 → continue
            self._tool_call("第3次"),     # 重试仍失败，反思计数重置后第 1 次
            self._tool_call("第4次"),     # 反思超限，但 _restore_attempted=True → 升级
        ]
        mock_reflector = MagicMock()
        mock_reflector.failure_reflect = AsyncMock(return_value=self._reflection())

        loop = self._loop(mock_llm, mock_reflector, tmp_path)
        result = await loop.run("还原一次", {"session_id": "sess_c4"})

        assert result.success is False
        # 还原动作仅一次（日志含两条 snapshot_restored 标记：还原动作 + 主循环重试确认，
        # 故统计"已回滚到最近快照"动作日志而非标记本身）
        assert caplog.text.count("已回滚到最近快照") == 1

    @pytest.mark.asyncio
    async def test_snapshot_every_step_disabled(self, tmp_path):
        """C7：snapshot_every_step=False → 零行为变化、不产生快照文件"""
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = [json.dumps({"reasoning": "直接完成", "action_type": "finish",
                                                 "result": "ok", "confidence": 0.9})]
        mock_reflector = MagicMock()

        loop = self._loop(mock_llm, mock_reflector, tmp_path, snapshot_every_step=False)
        result = await loop.run("关闭快照", {"session_id": "sess_c7"})

        assert result.success is True
        assert not (tmp_path / "sess_c7").exists()      # 未创建快照目录

    @pytest.mark.asyncio
    async def test_restore_retry_disabled(self, tmp_path, caplog):
        """C8：restore_retry=False → 反思超限直接升级，不还原"""
        caplog.set_level(logging.INFO)
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = [
            self._tool_call("第1次"),
            self._tool_call("第2次"),     # 超限但 restore_retry=False → 升级，无 continue
            json.dumps({"reasoning": "完成", "action_type": "finish",
                        "result": "ok", "confidence": 0.9}),
        ]
        mock_reflector = MagicMock()
        mock_reflector.failure_reflect = AsyncMock(return_value=self._reflection())

        loop = self._loop(mock_llm, mock_reflector, tmp_path, restore_retry=False)
        result = await loop.run("关闭还原", {"session_id": "sess_c8"})

        assert result.success is True
        assert "snapshot_restored" not in caplog.text

    @pytest.mark.asyncio
    async def test_no_snapshot_available_falls_to_upgrade(self, tmp_path):
        """C10：无可用快照（save 全失败）→ 超限走升级路径，不炸"""
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = [
            self._tool_call("第1次"),
            self._tool_call("第2次"),     # 超限 → restore_snapshot(None) → None → 升级
            json.dumps({"reasoning": "完成", "action_type": "finish",
                        "result": "ok", "confidence": 0.9}),
        ]
        mock_reflector = MagicMock()
        mock_reflector.failure_reflect = AsyncMock(return_value=self._reflection())

        blocker = tmp_path / "blocker"
        blocker.write_text("x")           # 文件占用 snapshot_root → save 全失败
        loop = self._loop(mock_llm, mock_reflector, tmp_path, snapshot_root=str(blocker))
        result = await loop.run("无快照", {"session_id": "sess_c10"})

        assert result.success is True      # 升级路径不阻断，后续 finish 成功

    @pytest.mark.asyncio
    async def test_snapshot_failure_not_blocking(self, tmp_path, caplog):
        """C1：快照写入失败 → 主循环继续且仅告警一次（验收5）"""
        caplog.set_level(logging.INFO)
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = [
            self._tool_call("第1次"),
            json.dumps({"reasoning": "完成", "action_type": "finish",
                        "result": "ok", "confidence": 0.9}),
        ]
        mock_reflector = MagicMock()
        mock_reflector.failure_reflect = AsyncMock(return_value=self._reflection())

        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        loop = self._loop(mock_llm, mock_reflector, tmp_path, snapshot_root=str(blocker))
        result = await loop.run("快照失败", {"session_id": "sess_c1"})

        assert result.success is True
        assert caplog.text.count("快照保存失败") == 1   # 仅告警一次

    @pytest.mark.asyncio
    async def test_session_id_fallback_no_none_dir(self, tmp_path):
        """C13：context 无 session_id → 快照不落入 None/ 目录（str(None) 真值 bug 回归）。

        旧实现 `str(context.get("session_id")) or ...` 中 str(None)="None" 为真值，
        or 兜底永不生效，真实运行快照会写入 data/snapshots/None/（曾实证）。
        且 session_id 须在循环外确定一次：若每步重新生成（get_trace_id() 无上下文
        时返回 None → 每步新时间戳），多步循环会产生多个 react_* 碎片目录、轮转失效。
        """
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = [
            self._tool_call("第1次"),
            self._tool_call("第2次"),
            json.dumps({"reasoning": "完成", "action_type": "finish",
                        "result": "ok", "confidence": 0.9}),
        ]
        mock_reflector = MagicMock()

        loop = self._loop(mock_llm, mock_reflector, tmp_path)
        result = await loop.run("无会话ID", {})

        assert result.success is True
        assert not (tmp_path / "None").exists()          # 不落入 None/ 目录
        dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        assert dirs
        assert all(d.name != "None" for d in dirs)
        # 多步循环仅 1 个 react_* 目录（session 稳定 → 轮转/治理有效）
        react_dirs = [d for d in dirs if d.name.startswith("react_")]
        assert len(react_dirs) == 1
        # 同目录内多份 step 快照（轮转语义生效的前提）
        assert len(list(react_dirs[0].glob("step_*.json"))) == 3

    @pytest.mark.asyncio
    async def test_concurrent_runs_snapshot_session_isolated(self, tmp_path):
        """C14：同一实例并发 run() → 快照按各自 session_id 分目录，不串话/无 None 碎片。

        并发防御回归：快照运行态（session_id/最近快照/还原标记）若用实例属性，
        同一实例并发 run() 会互相覆盖 → 快照写入对方目录（碎片串话）或还原错快照。
        现为 run() 局部变量：两个并发 run 的快照必须严格落在各自 session 目录，
        且快照内容互不含对方任务文本。
        """
        counts = {"A": 0, "B": 0}

        async def chat_fn(messages):
            # 按任务文本区分两个并发 run 的 LLM 响应序列（稳定隔离，不抢 side_effect）
            content = messages[0]["content"]
            if "并发任务A" in content:
                counts["A"] += 1
                if counts["A"] == 1:
                    return json.dumps({"reasoning": "A步1", "action_type": "tool_call",
                                       "action": {"tool": "noop", "params": {}, "description": "A第1步"}})
                return json.dumps({"reasoning": "A完成", "action_type": "finish",
                                   "result": "okA", "confidence": 0.9})
            if "并发任务B" in content:
                counts["B"] += 1
                if counts["B"] == 1:
                    return json.dumps({"reasoning": "B步1", "action_type": "tool_call",
                                       "action": {"tool": "noop", "params": {}, "description": "B第1步"}})
                return json.dumps({"reasoning": "B完成", "action_type": "finish",
                                   "result": "okB", "confidence": 0.9})
            return json.dumps({"reasoning": "未知", "action_type": "finish",
                               "result": "?", "confidence": 0.9})

        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = chat_fn
        mock_reflector = MagicMock()
        mock_reflector.failure_reflect = AsyncMock(return_value=self._reflection())

        loop = self._loop(mock_llm, mock_reflector, tmp_path)
        loop.planner.tool_registry.register("noop", lambda: "ok")

        results = await asyncio.gather(
            loop.run("并发任务A", {"session_id": "sess_A"}),
            loop.run("并发任务B", {"session_id": "sess_B"}),
        )

        assert all(r.success for r in results)
        # 两个 session 目录各自独立（不串话），均 2 次迭代 → 2 份快照
        a_dir, b_dir = tmp_path / "sess_A", tmp_path / "sess_B"
        assert a_dir.is_dir() and b_dir.is_dir()
        assert len(list(a_dir.glob("step_*.json"))) == 2
        assert len(list(b_dir.glob("step_*.json"))) == 2
        # 无 None/ 碎片目录
        assert not (tmp_path / "None").exists()
        # 快照内容互不含对方任务文本（防 session 串话）
        a_last = a_dir.joinpath("step_1.json").read_text(encoding="utf-8")
        b_last = b_dir.joinpath("step_1.json").read_text(encoding="utf-8")
        assert "并发任务A" in a_last and "并发任务B" not in a_last
        assert "并发任务B" in b_last and "并发任务A" not in b_last
