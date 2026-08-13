"""D3 复现测试：ask_user 伪实现，不真正等待用户

缺陷（P0 正确性）：ask_user 行动直接返回成功"等待用户输入"，不真正暂停等待用户，
循环继续执行，人机协同名存实亡，用户无法在关键节点介入。

预期失败（阶段 0 复现）：ask_user 后循环应停止等待用户（iterations==1）
→ 当前继续执行直到迭代耗尽 → 断言失败即复现成功。
修复后（阶段 1）：ask_user 行动返回 success=False + observation="awaiting_user_input"，
ReAct 循环检测到该标记即终止循环并以 ReActResult(success=False, error="等待用户输入") 返回。
"""
import json
import tempfile
import pytest
from unittest.mock import AsyncMock

from planning.core import PlanningCore


class TestDefectD3:
    """D3：ask_user 后循环应停止等待用户"""

    @pytest.mark.asyncio
    async def test_ask_user_stops_loop_waiting_for_user(self):
        ask_user_response = json.dumps({
            "reasoning": "需要用户确认",
            "action_type": "ask_user",
            "result": "请确认是否继续",
        })
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = ask_user_response  # LLM 一直要求询问用户

        with tempfile.TemporaryDirectory() as tmp_dir:
            core = PlanningCore(
                llm_service=mock_llm,
                config={"reflector": {"persist_dir": tmp_dir}},
            )

            result = await core.react_loop.run("需要确认的任务", {})

            # 目标行为：循环应在第一次 ask_user 后停止，返回"等待用户输入"语义
            assert result.success is False
            assert result.error == "等待用户输入"
            assert result.iterations == 1
            # D3 修复规格：ask_user 行动不得假装成功——行动结果为失败，
            # 且观察带 awaiting_user_input 专用标记（供上层识别等待用户状态）
            assert result.steps[0].success is False
            assert result.steps[0].observation == "awaiting_user_input"
