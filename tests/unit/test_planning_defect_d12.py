"""D12 复现测试：反思调整不应用

缺陷（P1）：reflection.adjustments 只打日志，不更新 context / plan / 后续思考提示词，
闭环（反思→调整→计划）断裂。

预期失败：反射返回调整建议后 context 应被更新（写入 _hints 等命名空间）
→ 当前调整建议只打日志、不落 context → 断言失败即复现成功。

说明：本测试使用 stub Reflector 直接返回调整建议，以隔离 D2 的 AttributeError 干扰，
聚焦"调整建议是否被应用"这一行为。
"""
import json
import tempfile
import pytest
from unittest.mock import AsyncMock

from planning.executor import ToolRegistry
from planning.react import ReActLoop
from planning.reflector import Reflector, ReflectionResult


class _StubReflector(Reflector):
    """stub：step_reflect 返回带调整建议的结果（避开 D2 的 AttributeError）"""

    async def step_reflect(self, task, result, context=None):
        return ReflectionResult(
            assessment="需要调整",
            confidence=0.8,
            adjustments=["调整建议A: 重新规划后续步骤"],
        )


class TestDefectD12:
    """D12：反思调整建议应应用到执行上下文"""

    @pytest.mark.asyncio
    async def test_reflection_adjustments_applied_to_context(self):
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = [
            json.dumps({
                "reasoning": "使用工具",
                "action_type": "tool_call",
                "action": {"tool": "search", "params": {"query": "测试"}, "description": "搜索"},
            }),
            json.dumps({"reasoning": "完成", "action_type": "finish", "result": "完成"}),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            planner = type("P", (), {})()
            planner.llm = mock_llm
            planner.tool_registry = ToolRegistry()
            planner.tool_registry.register("search", lambda query: "结果")

            loop = ReActLoop(planner, _StubReflector(persist_dir=tmp_dir), max_iterations=5)
            # 注意：run() 内 `context = context or {}` 会替换空 dict，
            # 必须传非空 context 才能观察到调整建议是否被写入
            context = {"user": "测试用户"}
            result = await loop.run("搜索测试", context)

            # 目标行为：调整建议应写入 context（如 _hints 命名空间），而非仅打日志
            assert result.success is True
            assert "调整建议A" in str(context)
