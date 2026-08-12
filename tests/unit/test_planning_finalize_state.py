"""_finalize_state 单元测试（C1 修复新增方法）

覆盖状态流转逻辑：
1. 无状态机时降级直接赋值（兼容 executor 独立使用场景）
2. 有状态机时经 transition 流转：状态变更 + 转换历史记录 + 钩子触发（C1 核心保证）
3. 非法转换（InvalidStateTransitionError）时捕获降级直接赋值，转换历史不记录
4. execute_plan 集成：全成功 -> COMPLETED；部分失败（低优先级）-> COMPLETED；
   高优先级任务失败中断 -> FAILED（三条收尾路径均经状态机）
"""
import pytest

from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task
from planning.state_machine import PlanStateMachine


class TestFinalizeState:
    """_finalize_state 方法单元测试"""

    def test_without_state_machine_direct_assign(self):
        """无状态机（独立使用兼容）：直接赋值目标状态"""
        executor = PlanExecutor(ToolRegistry())
        plan = Plan(state=PlanState.EXECUTING)

        executor._finalize_state(plan, PlanState.COMPLETED, reason="测试")

        assert plan.state == PlanState.COMPLETED

    def test_through_state_machine_changes_state_and_records_history(self):
        """有状态机：经 transition 流转，状态变更并记录转换历史"""
        sm = PlanStateMachine()
        executor = PlanExecutor(ToolRegistry(), state_machine=sm)
        plan = Plan(state=PlanState.EXECUTING)

        executor._finalize_state(plan, PlanState.COMPLETED, reason="全部成功")

        assert plan.state == PlanState.COMPLETED
        history = sm.get_transition_history(plan_id=plan.id)
        assert len(history) == 1
        assert history[0]["from_state"] == "executing"
        assert history[0]["to_state"] == "completed"
        assert history[0]["reason"] == "全部成功"

    def test_through_state_machine_fires_hook(self):
        """有状态机：触发 (EXECUTING, COMPLETED) 钩子（C1 修复的核心保证）"""
        sm = PlanStateMachine()
        executor = PlanExecutor(ToolRegistry(), state_machine=sm)
        plan = Plan(state=PlanState.EXECUTING)
        fired = []
        sm.register_hook(PlanState.EXECUTING, PlanState.COMPLETED, lambda p: fired.append(p.id))

        executor._finalize_state(plan, PlanState.COMPLETED, reason="测试")

        assert fired == [plan.id]

    def test_terminal_state_preserved_on_invalid_transition(self):
        """终态保护（漏洞H修复）：计划已处于终态（COMPLETED）时，收尾非法转换
        保留原终态，不被降级覆盖为 FAILED（重复执行已完成计划的异常路径）"""
        sm = PlanStateMachine()
        executor = PlanExecutor(ToolRegistry(), state_machine=sm)
        plan = Plan(state=PlanState.COMPLETED)  # 已终态，COMPLETED -> FAILED 非法

        executor._finalize_state(plan, PlanState.FAILED, reason="异常")

        assert plan.state == PlanState.COMPLETED  # 终态保护：保留原状态
        assert sm.get_transition_history(plan_id=plan.id) == []  # 非法转换未记录

    def test_non_terminal_invalid_transition_still_falls_back(self):
        """非终态非法转换仍降级直接赋值（终态保护仅作用于终态，不破坏既有降级语义）"""
        sm = PlanStateMachine()
        executor = PlanExecutor(ToolRegistry(), state_machine=sm)
        plan = Plan(state=PlanState.INIT)  # INIT -> FAILED 状态机不支持，非终态

        executor._finalize_state(plan, PlanState.FAILED, reason="异常")

        assert plan.state == PlanState.FAILED  # 非终态 → 降级赋值


class TestFinalizeStateIntegration:
    """execute_plan 三条收尾路径的状态流转集成测试"""

    @pytest.mark.asyncio
    async def test_all_success_finalized_completed(self):
        """全成功计划：收尾经状态机转为 COMPLETED

        注：result 文案受 D1 缺陷影响（"部分任务失败"），此处仅断言 _finalize_state
        职责范围内的状态与转换历史，不依赖 D1 修复。
        """
        sm = PlanStateMachine()
        registry = ToolRegistry()
        registry.register("echo", lambda: "ok")
        executor = PlanExecutor(registry, state_machine=sm)

        plan = Plan(original_task="成功任务", state=PlanState.READY)
        plan.add_task(Task(id="t1", description="echo 任务"))

        result = await executor.execute_plan(plan)

        assert result.state == PlanState.COMPLETED
        history = sm.get_transition_history(plan_id=plan.id)
        assert history[-1]["to_state"] == "completed"

    @pytest.mark.asyncio
    async def test_partial_failure_finalized_completed(self):
        """部分失败（失败任务属终态）：计划完成但标记部分失败，仍经状态机转 COMPLETED"""
        sm = PlanStateMachine()
        registry = ToolRegistry()
        registry.register("echo", lambda: "ok")

        def fail():
            raise RuntimeError("boom")

        registry.register("fail", fail)
        executor = PlanExecutor(registry, state_machine=sm)

        plan = Plan(original_task="部分失败任务", state=PlanState.READY)
        plan.add_task(Task(id="t1", description="echo 任务", priority=1))
        plan.add_task(Task(id="t2", description="fail 任务", priority=1))

        result = await executor.execute_plan(plan)

        assert result.state == PlanState.COMPLETED
        assert "部分任务失败" in str(result.result)
        history = sm.get_transition_history(plan_id=plan.id)
        assert history[-1]["to_state"] == "completed"

    @pytest.mark.asyncio
    async def test_max_steps_exceeded_finalized_failed(self):
        """步骤数超限（残留 PENDING 任务，计划未完成）：经状态机转 FAILED"""
        sm = PlanStateMachine()
        registry = ToolRegistry()
        registry.register("echo", lambda: "ok")
        executor = PlanExecutor(registry, state_machine=sm)

        plan = Plan(original_task="超限任务", state=PlanState.READY)
        plan.add_task(Task(id="t1", description="echo 任务"))
        plan.add_task(Task(id="t2", description="echo 任务"))
        plan.add_task(Task(id="t3", description="echo 任务"))
        plan.max_steps = 2  # 仅够执行前 2 个任务，t3 残留 PENDING

        result = await executor.execute_plan(plan)

        assert result.state == PlanState.FAILED
        assert result.error is not None
        history = sm.get_transition_history(plan_id=plan.id)
        assert history[-1]["to_state"] == "failed"

    @pytest.mark.asyncio
    async def test_core_reexecute_completed_plan_preserves_terminal_state(self, tmp_path):
        """漏洞H 端到端：core.execute_plan 重复执行已完成计划 → COMPLETED 不被覆盖为 FAILED。

        触发链：transition(COMPLETED->EXECUTING) 非法抛 InvalidStateTransitionError →
        core 异常路径调 _finalize_state(FAILED) → 终态保护保留 COMPLETED。
        """
        from planning.core import PlanningCore

        registry = ToolRegistry()
        registry.register("echo", lambda: "ok")
        core = PlanningCore(
            llm_service=None,
            tool_registry=registry,
            config={
                "persist_dir": str(tmp_path),
                "planning": {"persist_db": str(tmp_path / "plans.db")},
            },
        )

        plan = await core.plan("echo 任务", {})
        result = await core.execute_plan(plan)
        assert result.state == PlanState.COMPLETED

        # 重复执行：状态转换异常路径不得覆盖终态
        result2 = await core.execute_plan(plan)
        assert result2.state == PlanState.COMPLETED
