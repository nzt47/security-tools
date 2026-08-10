"""D19 复现测试：代码质量

缺陷（P2）：
1. `reflector.py` 中 `learn_from_experience` 重复定义（旧版死代码被新版覆盖）；
2. ReAct 循环 context 中 `_last_result_{i}` 无限累积不清理。

预期失败：
- 目标: `learn_from_experience` 应只定义一次 → 当前源码中出现 2 次 → 断言失败即复现成功。
- 目标: context 中的 `_last_result_*` 缓存应有界 → 当前每次迭代追加且从不清理 → 断言失败即复现成功。
"""
import asyncio
import inspect
import json
import tempfile
import pytest
from unittest.mock import AsyncMock

import planning.reflector as reflector_module
from planning.executor import ToolRegistry
from planning.react import ReActLoop
from planning.reflector import Reflector


class TestDefectD19:
    """D19：重复定义死代码 + context 缓存无限累积"""

    def test_learn_from_experience_defined_once(self):
        """目标：learn_from_experience 应只定义一次（无旧版死代码）"""
        source = inspect.getsource(reflector_module)
        count = source.count("def learn_from_experience")
        assert count == 1, f"目标: learn_from_experience 应仅定义一次, 实际 {count} 次(存在死代码覆盖)"

    @pytest.mark.asyncio
    async def test_react_context_last_result_is_bounded(self):
        """目标：context 中 `_last_result_{i}` 应被清理，而非无限累积"""
        tool_calls = [
            json.dumps({
                "reasoning": f"调用工具{i}",
                "action_type": "tool_call",
                "action": {"tool": "search", "params": {"query": f"q{i}"}, "description": f"搜索{i}"},
            })
            for i in range(3)
        ]
        tool_calls.append(json.dumps({"reasoning": "完成", "action_type": "finish", "result": "完成"}))

        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = tool_calls

        with tempfile.TemporaryDirectory() as tmp_dir:
            planner = type("P", (), {})()
            planner.llm = mock_llm
            planner.tool_registry = ToolRegistry()
            planner.tool_registry.register("search", lambda query: "结果")

            loop = ReActLoop(planner, Reflector(persist_dir=tmp_dir), max_iterations=5)
            # 注意：run() 内 `context = context or {}` 会替换空 dict，
            # 必须传非空 context 才能观察 _last_result_* 累积
            context = {"user": "测试用户"}
            result = await loop.run("连续搜索", context)

            assert result.success is True
            leftover = [k for k in context if k.startswith("_last_result_")]
            assert len(leftover) <= 2, f"目标: _last_result_* 缓存应有界, 实际累积 {len(leftover)} 个"
