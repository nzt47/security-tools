"""D5 复现测试：并行执行未实现

缺陷（P1）：分解器输出 parallel_groups 后被丢弃；执行器只取 next_tasks[0] 串行执行；
TaskType.PARALLEL 从未生效。

预期失败：两个互不依赖的任务应按并行执行（start_b 先于 end_a）
→ 当前串行执行（start_a,end_a,start_b,end_b）→ 断言失败即复现成功。
"""
import asyncio
import pytest

from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task


class TestDefectD5:
    """D5：互不依赖的任务应并行执行"""

    @pytest.mark.asyncio
    async def test_independent_tasks_execute_in_parallel(self):
        registry = ToolRegistry()
        events = []

        async def tool_a():
            events.append("start_a")
            await asyncio.sleep(0.2)
            events.append("end_a")

        async def tool_b():
            events.append("start_b")
            await asyncio.sleep(0.2)
            events.append("end_b")

        registry.register("ta", tool_a)
        registry.register("tb", tool_b)

        executor = PlanExecutor(registry)
        plan = Plan(original_task="并行任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="调用ta"))
        plan.add_task(Task(id="b", description="调用tb"))
        plan.state = PlanState.READY

        await executor.execute_plan(plan)

        # 目标行为：并行执行时 start_b 应先于 end_a 出现
        assert events.index("start_b") < events.index("end_a")
