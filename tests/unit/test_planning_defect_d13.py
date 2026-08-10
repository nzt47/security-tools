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
from unittest.mock import AsyncMock

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
