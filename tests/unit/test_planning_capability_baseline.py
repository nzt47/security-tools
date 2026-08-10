"""规划模块缺失能力基线测试（阶段 0）

这些测试编码《规划模块理想设计.md》中的目标能力规格，当前实现尚未具备。
先以 pytest.mark.skip 标注"待实现"，在对应重构阶段完成后移除 skip 并使其通过。

对应关系：
- 并行执行     → D5（阶段 2）
- 计划验证     → D11（阶段 2）
- 持久化恢复   → D9（阶段 2）
- 预算超限     → D13（阶段 3）
- 降级链       → D14（阶段 3）
"""
import pytest


@pytest.mark.skip(reason="待实现: 并行执行 (D5, 建议修复阶段 2)")
def test_parallel_execution_uses_parallel_groups():
    """目标：decomposer 输出的 parallel_groups 应被 executor 并行执行，而非只取 next_tasks[0]"""


@pytest.mark.skip(reason="待实现: 计划验证 (D11, 建议修复阶段 2)")
def test_plan_validation_before_execution():
    """目标：执行前校验依赖完整性、环检测、工具可用性，拦截悬空依赖等畸形计划"""


@pytest.mark.skip(reason="待实现: 持久化恢复 (D9, 建议修复阶段 2)")
def test_plan_persistence_and_recovery():
    """目标：计划/任务/执行记录落库（SQLite），进程重启后恢复未完成计划"""


@pytest.mark.skip(reason="待实现: 预算超限 (D13, 建议修复阶段 3)")
def test_budget_exceeded_triggers_degrade():
    """目标：token/cost/deadline 三层预算超限时触发降级或征求用户"""


@pytest.mark.asyncio
async def test_task_degrade_chain():
    """目标：任务失败时按任务级降级链尝试 Plan B，而非直接标记失败/中断（D14 已实现）"""
    from planning.executor import PlanExecutor, ToolRegistry
    from planning.models import Plan, PlanState, Task

    registry = ToolRegistry()

    def primary(**kwargs):
        raise RuntimeError("主工具故障")

    def backup(**kwargs):
        return "降级成功"

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
    task_a = result.get_task("a")
    assert task_a is not None
    assert task_a.status.value == "completed"
    assert "降级成功" in str(task_a.result)
