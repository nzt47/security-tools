"""D4 复现测试：双路径割裂（Plan 执行路径 与 ReAct 路径 互不共享状态/记录）

缺陷（P1）：plan()+execute_plan() 与 chat()→ReAct 互不共享状态与记录；
ReAct 路径的"思考-行动-观察"步骤从未写入 executor.execution_history。

预期失败：ReAct 路径执行后 execution_history 应为空（未被写入）
→ 断言非空失败 → 复现成功。
"""
import json
import tempfile
import pytest
from unittest.mock import AsyncMock

from planning.core import PlanningCore


class TestDefectD4:
    """D4：ReAct 步骤应写入统一执行记录（与 execute_plan 路径共享）"""

    @pytest.mark.asyncio
    async def test_react_steps_shared_into_execution_history(self):
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = json.dumps({
            "reasoning": "直接完成",
            "action_type": "finish",
            "result": "任务完成",
        })

        with tempfile.TemporaryDirectory() as tmp_dir:
            core = PlanningCore(
                llm_service=mock_llm,
                config={"reflector": {"persist_dir": tmp_dir}},
            )

            chat_result = await core.chat("帮我完成一个复杂的任务")

            assert chat_result.used_planning is True
            # 目标行为：ReAct 步骤应写入统一执行记录（与 execute_plan 共享）
            assert len(core.executor.execution_history) > 0
