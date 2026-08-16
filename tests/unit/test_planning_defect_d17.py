"""D17 复现测试：经验只存不用

缺陷（P2）：reflector 积累的 experiences/lessons 从未被 decompose/think 查询复用。
Reflector 已实现 get_advice_for_task()，但 ReActLoop._think() 构建提示词时
从不查询反射器、不把历史经验嵌入思考输入。

预期失败：思考提示词应嵌入历史经验建议（get_advice_for_task 的输出）
→ 当前提示词固定为 THINKING_PROMPT，不含任何经验 → 断言失败即复现成功。
"""
import json
import tempfile
import pytest
from unittest.mock import AsyncMock

from planning.executor import ToolRegistry
from planning.react import ReActLoop
from planning.reflector import Reflector, Experience


class TestDefectD17:
    """D17：思考阶段应复用历史经验"""

    @pytest.mark.asyncio
    async def test_thinking_prompt_embeds_historical_experience(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            reflector = Reflector(persist_dir=tmp_dir)
            # 预置一条同类任务的历史成功经验
            reflector.experiences.append(
                Experience(
                    id="exp_001",
                    task_type="query",
                    task_description="检查系统状态",
                    success=True,
                    output="状态正常",
                    error=None,
                    timestamp="2024-01-01T00:00:00",
                )
            )

            captured_prompts = []

            def recording_chat(messages):
                captured_prompts.append(messages[0]["content"])
                return json.dumps({"reasoning": "完成", "action_type": "finish", "result": "完成"})

            mock_llm = AsyncMock()
            mock_llm.chat.side_effect = recording_chat

            planner = type("P", (), {})()
            planner.llm = mock_llm
            planner.tool_registry = ToolRegistry()

            loop = ReActLoop(planner, reflector, max_iterations=3)
            await loop.run("检查系统状态", {})

            # 目标行为：思考提示词应嵌入 reflector 的历史经验建议
            assert captured_prompts, "思考阶段应调用 LLM"
            assert any("历史经验" in p or "成功模式" in p for p in captured_prompts), (
                "目标: 思考提示词应包含 get_advice_for_task 返回的历史经验"
            )
