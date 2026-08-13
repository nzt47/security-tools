"""规划模块缺失能力基线测试（阶段 0）

这些测试编码《规划模块理想设计.md》中的目标能力规格。
各规格曾在对应重构阶段以 pytest.mark.skip 标注"待实现"，完成后移除 skip 并通过；当前 5 项全部启用。

对应关系（2026-08-11 更新）：
- 并行执行     → D5（已实现,已移除 skip）
- 计划验证     → D11（已实现：悬空依赖/环/工具可用性预检,已移除 skip）
- 持久化恢复   → D9（已实现 SQLite 落库,已移除 skip）
- 预算超限     → D13（已实现 deadline/token/cost 三层预算 + 硬超时；预算超限征求用户分支可配 budget_ask_user 启用,默认关闭保持向后兼容）
- 降级链       → D14（已实现,已移除 skip）
"""
import pytest


@pytest.mark.asyncio
async def test_parallel_execution_uses_parallel_groups():
    """目标：互不依赖任务并行执行（D5 已实现：executor 对全部可执行任务 gather 并行）"""
    import asyncio
    import time

    from planning.executor import PlanExecutor, ToolRegistry
    from planning.models import Plan, PlanState, Task

    registry = ToolRegistry()

    async def tool_a():
        await asyncio.sleep(0.2)

    async def tool_b():
        await asyncio.sleep(0.2)

    registry.register("ta", tool_a)
    registry.register("tb", tool_b)

    executor = PlanExecutor(registry, config={"parallel_execution": True})
    plan = Plan(original_task="并行任务", state=PlanState.READY)
    plan.add_task(Task(id="a", description="调用ta"))
    plan.add_task(Task(id="b", description="调用tb"))
    plan.state = PlanState.READY

    t0 = time.monotonic()
    await executor.execute_plan(plan)
    elapsed = time.monotonic() - t0

    # 目标行为：两个 0.2s 任务并行执行，总耗时应显著小于串行和（0.4s）
    assert elapsed < 0.4


@pytest.mark.asyncio
async def test_plan_validation_before_execution():
    """目标：执行前校验依赖完整性、环检测、工具可用性（D11 已实现三类检查）"""
    from planning.executor import PlanExecutor, ToolRegistry
    from planning.models import Plan, PlanState, Task

    registry = ToolRegistry()
    registry.register("ta", lambda: "ok")
    executor = PlanExecutor(registry)

    # 悬空依赖：执行前拦截，错误指明"依赖不存在"
    plan = Plan(original_task="悬空依赖任务", state=PlanState.READY)
    plan.add_task(Task(id="a", description="调用ta"))
    plan.add_task(Task(id="b", description="调用ta", dependencies=["missing"]))
    plan.state = PlanState.READY
    result = await executor.execute_plan(plan)
    assert result.state == PlanState.FAILED
    assert "依赖" in (result.error or "")

    # 环检测：循环依赖被验证器拦截
    plan2 = Plan(original_task="循环依赖任务", state=PlanState.READY)
    plan2.add_task(Task(id="a", description="调用ta", dependencies=["b"]))
    plan2.add_task(Task(id="b", description="调用ta", dependencies=["a"]))
    plan2.state = PlanState.READY
    result2 = await executor.execute_plan(plan2)
    assert result2.state == PlanState.FAILED
    assert "循环" in (result2.error or "")


def test_plan_persistence_and_recovery():
    """目标：计划/任务/执行记录落库（SQLite），进程重启后恢复未完成计划（D9 已实现）"""
    import asyncio
    import os
    import tempfile

    from planning.core import PlanningCore
    from planning.models import PlanState

    with tempfile.TemporaryDirectory() as tmp_dir:
        cfg = {"planning": {"persist_dir": tmp_dir}}

        core1 = PlanningCore(config=cfg)
        plan = asyncio.run(core1.plan("首先打开文件然后保存"))
        plan.state = PlanState.EXECUTING  # 模拟未完成
        core1.save_plan_checkpoint(plan)

        # SQLite 落库文件存在
        db_path = os.path.join(tmp_dir, "plans.db")
        assert os.path.exists(db_path)

        # 进程重启：新实例恢复未完成计划
        core2 = PlanningCore(config=cfg)
        assert plan.id in core2._active_plans


@pytest.mark.asyncio
async def test_budget_exceeded_triggers_degrade():
    """目标：预算超限终止（D13 已实现 deadline/token/cost 三层预算；征求用户分支可配 budget_ask_user 启用）"""
    import asyncio
    import json
    import tempfile
    from unittest.mock import AsyncMock

    from planning.executor import ToolRegistry
    from planning.react import ReActLoop
    from planning.reflector import Reflector

    async def slow_tool(**kwargs):
        await asyncio.sleep(0.5)
        return "慢工具完成"

    mock_llm = AsyncMock()
    mock_llm.chat.side_effect = [
        json.dumps({
            "reasoning": "调用慢工具",
            "action_type": "tool_call",
            "action": {"tool": "slow_tool", "params": {}, "description": "慢工具"},
        }),
        json.dumps({"reasoning": "完成", "action_type": "finish", "result": "完成"}),
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:
        planner = type("P", (), {})()
        planner.llm = mock_llm
        planner.tool_registry = ToolRegistry()
        planner.tool_registry.register("slow_tool", slow_tool)

        loop = ReActLoop(
            planner,
            Reflector(persist_dir=tmp_dir),
            max_iterations=3,
            config={"timeout_seconds": 0.05},
        )
        result = await loop.run("超预算任务", {})

        # 目标行为：超出 deadline 预算时应终止循环并返回预算/超时错误
        assert result.success is False
        assert result.error is not None
        assert "预算" in result.error or "超时" in result.error


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
