"""D2 复现测试：ReAct 路径反思永远静默失败（AttributeError 被吞）

缺陷（P0 正确性）：react.py 调用 reflector.step_reflect(task, ...) 传入字符串 task，
而签名期望 Task 对象并访问 task.description → AttributeError 被 try/except 静默吞掉，
ReAct 路径的反思闭环完全断裂。

预期失败（阶段 0 复现）：使用真实 Reflector + mock LLM（tool_call → finish），
断言反思真实执行（reflection_history 有记录）→ 当前为空 → 断言失败即复现成功。
修复后（阶段 1）：react.py 构造最小 Task 传入 step_reflect，反思真实执行且异常
不再被静默吞掉（日志提升为 error 并记录 trace_id）。
"""
import json
import tempfile
import pytest
from unittest.mock import AsyncMock

from planning.core import PlanningCore


class TestDefectD2:
    """D2：ReAct 路径反思应真实执行且无 AttributeError"""

    @pytest.mark.asyncio
    async def test_react_reflection_runs_without_attribute_error(self):
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = [
            json.dumps({
                "reasoning": "使用工具",
                "action_type": "tool_call",
                "action": {"tool": "search", "params": {"query": "测试"}, "description": "搜索"},
            }),
            json.dumps({"reasoning": "完成", "action_type": "finish", "result": "任务完成"}),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            core = PlanningCore(
                llm_service=mock_llm,
                config={"reflector": {"persist_dir": tmp_dir}},
            )
            core.register_tool("search", lambda query: f"结果:{query}")

            result = await core.react_loop.run("使用search搜索", {})

            # 目标行为：反思真实执行且无 AttributeError（step_reflect 不被吞异常）
            assert result.success is True
            assert len(core.reflector.reflection_history) == 1
            # 反思结果确实写入 reflection_history（步骤级反思）
            assert core.reflector.reflection_history[0]["type"] == "step"
