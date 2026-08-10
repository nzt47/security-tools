"""D18 复现测试：取消语义不完整

缺陷（P2）：`cancel_plan` 用 `asyncio.create_task` 异步改状态，
进行中的工具调用不真正中断（无协作式取消）。

预期失败：取消计划应传播 CancelledError 到进行中的工具调用
→ 当前 executor.cancel_plan() 只改 plan.state，正在 await 的工具协程继续执行
→ 断言失败即复现成功。
"""
import asyncio
import pytest

from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task


class TestDefectD18:
    """D18：取消应中断进行中的工具调用（协作式取消）"""

    @pytest.mark.asyncio
    async def test_cancel_interrupts_running_tool(self):
        registry = ToolRegistry()
        started = asyncio.Event()
        tool_cancelled = asyncio.Event()

        async def slow_tool(**kwargs):
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                tool_cancelled.set()
                raise
            return "完成"

        registry.register("slow_tool", slow_tool)
        executor = PlanExecutor(registry)

        plan = Plan(original_task="取消测试", state=PlanState.READY)
        plan.add_task(Task(id="a", description="slow_tool 任务"))
        plan.state = PlanState.READY

        run_task = asyncio.create_task(executor.execute_plan(plan))
        await started.wait()

        await executor.cancel_plan(plan)

        # 目标行为：取消应中断进行中的工具调用（协作式取消）
        try:
            await asyncio.wait_for(tool_cancelled.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
        assert tool_cancelled.is_set(), "目标: 取消应传播 CancelledError 到进行中的工具调用"

        # 收尾：终止执行任务，避免悬挂
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass
