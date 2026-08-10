"""D14 复现测试：无风险管理

缺陷（P2）：任务失败即标记失败/高优先级即中断；无 Plan B、无降级链、无缓冲。
PlanExecutor.config 支持配置但从未实现"降级链"（degrade chain）。

预期失败：主工具失败时，应按配置的降级链尝试备份工具（Plan B）
→ 当前任务直接被标记失败、无任何备选方案尝试 → 断言失败即复现成功。
"""
import pytest

from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task


class TestDefectD14:
    """D14：任务失败时应按降级链尝试 Plan B"""

    @pytest.mark.asyncio
    async def test_task_failure_uses_degrade_chain(self):
        registry = ToolRegistry()

        def primary(**kwargs):
            raise RuntimeError("主工具故障")

        def backup(**kwargs):
            return "备份工具成功"

        registry.register("primary_tool", primary)
        registry.register("backup_tool", backup)

        executor = PlanExecutor(
            registry,
            max_retries=1,
            config={"degrade_chain": {"primary_tool": ["backup_tool"]}},
        )

        plan = Plan(original_task="降级链任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="primary_tool 执行"))
        plan.state = PlanState.READY

        result = await executor.execute_plan(plan)

        # 目标行为：主工具失败后应沿降级链执行备份工具，任务最终成功
        task_a = result.get_task("a")
        assert task_a is not None
        assert task_a.status.value == "completed"
        assert "备份工具成功" in str(task_a.result)
