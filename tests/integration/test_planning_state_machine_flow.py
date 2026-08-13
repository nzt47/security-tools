"""planning 模块状态机转换集成测试

通过 PlanningCore 完整执行流程验证状态机转换路径（不依赖 D1 修复，仅断言状态与转换历史）：

1. 完整成功流程：READY -> EXECUTING -> COMPLETED，转换历史完整、钩子触发
2. 部分失败流程：READY -> EXECUTING -> COMPLETED（FAILED 属终态，is_complete 为真）
3. 步骤超限流程：READY -> EXECUTING -> FAILED（残留 PENDING 任务）
4. 取消流程：EXECUTING -> CANCELLED（同步生效）
5. 非法取消：COMPLETED -> CANCELLED 被状态机拒绝，cancel_plan 返回 False
6. 非法执行：COMPLETED -> EXECUTING 被状态机拒绝，execute_plan 捕获并标记 FAILED
"""
import asyncio

import pytest

from planning.core import PlanningCore
from planning.executor import ToolRegistry
from planning.models import Plan, PlanState, Task


@pytest.fixture
def core() -> PlanningCore:
    """无 LLM 规则模式规划引擎 + echo 工具"""
    c = PlanningCore()
    c.register_tool("echo", lambda: "ok")
    return c


def _ready_plan(original_task="集成测试计划") -> Plan:
    plan = Plan(original_task=original_task, state=PlanState.READY)
    plan.add_task(Task(id="t1", description="echo 任务"))
    return plan


class TestFullFlowStateTransitions:
    """完整执行流程的状态机转换验证"""

    @pytest.mark.asyncio
    async def test_success_flow_transition_history(self, core):
        """成功流程：READY -> EXECUTING -> COMPLETED，历史完整"""
        plan = _ready_plan()

        result = await core.execute_plan(plan)

        assert result.state == PlanState.COMPLETED
        history = core.state_machine.get_transition_history(plan_id=plan.id)
        # 状态机记录：READY->EXECUTING（core 步骤1）+ EXECUTING->COMPLETED（executor 收尾）
        assert [h["from_state"] for h in history] == ["ready", "executing"]
        assert [h["to_state"] for h in history] == ["executing", "completed"]

    @pytest.mark.asyncio
    async def test_success_flow_fires_completion_hook(self, core):
        """成功流程：注册 (EXECUTING, COMPLETED) 钩子在收尾时触发"""
        fired = []
        core.state_machine.register_hook(
            PlanState.EXECUTING, PlanState.COMPLETED, lambda p: fired.append(p.id)
        )
        plan = _ready_plan()

        result = await core.execute_plan(plan)

        assert result.state == PlanState.COMPLETED
        assert fired == [plan.id]

    @pytest.mark.asyncio
    async def test_partial_failure_flow_completed(self, core):
        """部分失败流程：失败任务属终态，计划 COMPLETED"""
        def fail():
            raise RuntimeError("boom")

        core.register_tool("fail", fail)
        plan = _ready_plan()
        plan.add_task(Task(id="t2", description="fail 任务", priority=1))

        result = await core.execute_plan(plan)

        assert result.state == PlanState.COMPLETED
        history = core.state_machine.get_transition_history(plan_id=plan.id)
        assert history[-1]["to_state"] == "completed"

    @pytest.mark.asyncio
    async def test_max_steps_exceeded_flow_failed(self, core):
        """步骤超限流程：残留 PENDING 任务，计划 FAILED"""
        plan = _ready_plan()
        plan.add_task(Task(id="t2", description="echo 任务"))
        plan.add_task(Task(id="t3", description="echo 任务"))
        plan.max_steps = 2

        result = await core.execute_plan(plan)

        assert result.state == PlanState.FAILED
        assert result.error is not None
        history = core.state_machine.get_transition_history(plan_id=plan.id)
        assert history[-1]["to_state"] == "failed"


class TestCancelFlow:
    """取消流程的状态机转换验证"""

    @pytest.mark.asyncio
    async def test_cancel_executing_plan(self, core):
        """取消执行中计划：EXECUTING -> CANCELLED 同步生效"""
        plan = Plan(original_task="取消测试", state=PlanState.EXECUTING)
        core._active_plans[plan.id] = plan

        ok = core.cancel_plan(plan.id)

        assert ok is True
        assert plan.state == PlanState.CANCELLED
        history = core.state_machine.get_transition_history(plan_id=plan.id)
        assert history[-1]["from_state"] == "executing"
        assert history[-1]["to_state"] == "cancelled"

    def test_cancel_completed_plan_rejected(self, core):
        """取消已完成计划：COMPLETED -> CANCELLED 非法，返回 False 且状态不变"""
        plan = Plan(original_task="已结束", state=PlanState.COMPLETED)
        core._active_plans[plan.id] = plan

        ok = core.cancel_plan(plan.id)

        assert ok is False
        assert plan.state == PlanState.COMPLETED

    def test_cancel_unknown_plan(self, core):
        """取消不存在的计划：返回 False"""
        assert core.cancel_plan("nonexistent_plan") is False

    @pytest.mark.asyncio
    async def test_real_concurrent_cancel_preserves_cancelled(self, core):
        """真实 asyncio 并发：工具执行中取消 → 最终状态 CANCELLED

        完整工具调用与取消并发场景：slow_tool 运行中调用 core.cancel_plan
        （同步 transition EXECUTING->CANCELLED），工具完成后 executor 收尾
        不得覆盖取消状态（边界 #2 在真实时序下生效）；execute_plan 正常返回。
        """
        started = asyncio.Event()

        async def slow_tool(**kwargs):
            started.set()
            await asyncio.sleep(0.5)
            return "完成"

        core.register_tool("slow_tool", slow_tool)
        plan = Plan(original_task="并发取消", state=PlanState.READY)
        plan.add_task(Task(id="t1", description="slow_tool 任务"))
        core._active_plans[plan.id] = plan

        run_task = asyncio.create_task(core.execute_plan(plan))
        await started.wait()  # 工具已开始执行

        ok = core.cancel_plan(plan.id)  # 同步 transition EXECUTING->CANCELLED
        assert ok is True

        result = await asyncio.wait_for(run_task, timeout=5.0)

        assert result.state == PlanState.CANCELLED  # 收尾不覆盖取消状态
        history = core.state_machine.get_transition_history(plan_id=plan.id)
        assert history[-1]["to_state"] == "cancelled"


class TestInvalidExecution:
    """非法执行路径的状态机保护验证"""

    @pytest.mark.asyncio
    async def test_execute_terminal_plan_preserved(self, core):
        """执行终态计划（漏洞H修复）：COMPLETED -> EXECUTING 非法，
        终态保护保留原状态，不再被错误覆盖为 FAILED"""
        plan = Plan(original_task="已结束", state=PlanState.COMPLETED)

        result = await core.execute_plan(plan)

        assert result.state == PlanState.COMPLETED  # 终态保留，不被覆盖为 FAILED

    @pytest.mark.asyncio
    async def test_execute_plan_and_query_status(self, core):
        """执行后 get_plan_status 反映最终状态"""
        plan = _ready_plan()
        result = await core.execute_plan(plan)

        status = core.get_plan_status(plan.id)

        assert status is not None
        assert status["state"] == result.state.value
        assert status["total_tasks"] == 1
        assert status["completed_tasks"] == 1
