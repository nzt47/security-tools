"""D13 复现测试：无资源/成本约束

缺陷（P2）：无 token/cost 预算、无 deadline（`timeout_seconds` 配置未实现）、无资源调度。
ReActLoop 的 config 参数被保存但从未被读取使用。

预期失败：配置 deadline 预算后，循环应在超出预算时终止并返回预算超限错误
→ 当前 config["timeout_seconds"] 完全被忽略，循环照常跑完 → 断言失败即复现成功。
"""
import asyncio
import json
import tempfile
import pytest
from unittest.mock import AsyncMock, MagicMock

from planning.executor import ToolRegistry
from planning.react import ReActLoop
from planning.reflector import Reflector


class TestDefectD13:
    """D13：ReAct 循环应执行 deadline / 预算约束"""

    @pytest.mark.asyncio
    async def test_react_loop_enforces_timeout_budget(self):
        async def slow_tool(**kwargs):
            # 模拟一次"超预算"的慢工具调用（0.5s 远超 0.05s 预算）
            await asyncio.sleep(0.5)
            return "慢工具完成"

        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = [
            json.dumps({
                "reasoning": "调用慢工具",
                "action_type": "tool_call",
                "action": {"tool": "slow_tool", "params": {}, "description": "慢工具"},
            }),
            json.dumps({"reasoning": "完成", "action_type": "finish", "result": "完成"}),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            planner = type("P", (), {})()
            planner.llm = mock_llm
            planner.tool_registry = ToolRegistry()
            planner.tool_registry.register("slow_tool", slow_tool)

            loop = ReActLoop(
                planner,
                Reflector(persist_dir=tmp_dir),
                max_iterations=3,
                config={"timeout_seconds": 0.05},
            )
            result = await loop.run("超预算任务", {})

            # 目标行为：超出 deadline 预算时应终止循环并返回预算/超时错误
            assert result.success is False
            assert result.error is not None
            assert "预算" in result.error or "超时" in result.error

    @pytest.mark.asyncio
    async def test_token_budget_exceeded(self):
        """D13 优化：token 预算超限 → 迭代级终止并返回 token 预算错误（估算累计）"""
        mock_llm = AsyncMock()
        # 第一次返回 tool_call 推动迭代继续，第二次返回 finish；
        # 超长提示词/响应使单次思考即超出极小 token 预算
        mock_llm.chat.side_effect = [
            json.dumps({
                "reasoning": "调用工具",
                "action_type": "tool_call",
                "action": {"tool": "missing_tool", "params": {}, "description": "调用"},
            }),
            json.dumps({"reasoning": "完成", "action_type": "finish", "result": "结果" * 200}),
        ]

        planner = MagicMock()
        planner.llm = mock_llm
        planner.tool_registry = ToolRegistry()

        loop = ReActLoop(planner, None, max_iterations=5, config={"token_budget": 10})
        result = await loop.run("测试任务", {})

        # 目标行为：累计估算 token 超预算时终止，错误指明 token 预算
        assert result.success is False
        assert "token" in result.error
        assert result.token_used > 10

    @pytest.mark.asyncio
    async def test_cost_budget_exceeded(self):
        """D13 优化：成本预算超限（token × 单价）→ 迭代级终止并返回成本错误"""
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = [
            json.dumps({
                "reasoning": "调用工具",
                "action_type": "tool_call",
                "action": {"tool": "missing_tool", "params": {}, "description": "调用"},
            }),
            json.dumps({"reasoning": "完成", "action_type": "finish", "result": "完成"}),
        ]

        planner = MagicMock()
        planner.llm = mock_llm
        planner.tool_registry = ToolRegistry()

        # 单价放大 + 极小成本预算，确保单次思考即超限
        loop = ReActLoop(
            planner, None, max_iterations=5,
            config={"cost_budget": 0.000001, "token_price_per_1k": 10.0},
        )
        result = await loop.run("测试任务", {})

        # 目标行为：累计成本超预算时终止，错误指明成本预算
        assert result.success is False
        assert "成本" in result.error
        assert result.cost > 0

    @pytest.mark.asyncio
    async def test_tool_timeout_hard_cutoff(self):
        """D13 优化：异步工具调用硬超时（wait_for 包裹），慢工具不拖死循环"""
        async def slow_tool(**kwargs):
            await asyncio.sleep(10)  # 远超 tool_timeout
            return "太慢"

        registry = ToolRegistry()
        registry.register("slow_tool", slow_tool)

        planner = MagicMock()
        planner.llm = None  # 走规则思考 → tool_call 路径
        planner.tool_registry = registry

        loop = ReActLoop(planner, None, max_iterations=3, config={"tool_timeout_seconds": 0.05})
        result = await loop.run("调用slow_tool", {})

        # 目标行为：工具调用被硬超时中断，observation 含超时信息
        assert result.steps
        assert "超时" in result.steps[0].observation
        assert result.total_duration_ms < 10_000  # 未被 10s 慢工具拖死
