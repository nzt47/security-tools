"""cancel_plan 与 _finalize_state 并发场景单元测试（边界 #2/#3，关联 D18）

覆盖：
1. 边界 #2 现状锁定：_finalize_state 在 CANCELLED 状态下调用不抛异常（降级覆盖取消状态）
2. 边界 #3 修复验证：取消竞态把最终状态改为 CANCELLED 时，core.execute_plan 不抛 AssertionError
3. 合法取消：EXECUTING 状态经状态机同步转 CANCELLED
4. 边界 #3 源头锁定：executor.cancel_plan 直接赋值绕过状态机（无状态机校验）
"""
import pytest

from planning.core import PlanningCore
from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task
from planning.state_machine import PlanStateMachine


def _ready_plan() -> Plan:
    plan = Plan(original_task="取消竞态测试", state=PlanState.READY)
    plan.add_task(Task(id="t1", description="echo 任务"))
    return plan


@pytest.fixture
def core() -> PlanningCore:
    """无 LLM 规则模式规划引擎 + echo 工具"""
    c = PlanningCore()
    c.register_tool("echo", lambda: "ok")
    return c


class TestFinalizeStateOnCancelled:
    """边界 #2：_finalize_state 与取消状态交互"""

    def test_finalize_state_on_cancelled_plan_no_raise(self):
        """CANCELLED 状态下 _finalize_state 不抛异常且保留取消状态

        边界 #2 修复后：CANCELLED->COMPLETED 非法转换不覆盖取消状态，
        取消优先于收尾（此前为降级直接赋值覆盖 CANCELLED）。
        """
        sm = PlanStateMachine()
        executor = PlanExecutor(ToolRegistry(), state_machine=sm)
        plan = Plan(state=PlanState.CANCELLED)

        executor._finalize_state(plan, PlanState.COMPLETED, reason="收尾")

        assert plan.state == PlanState.CANCELLED  # 取消状态不被收尾覆盖


class TestExecutePlanCancelRace:
    """边界 #3：取消竞态下的断言保护"""

    @pytest.mark.asyncio
    async def test_cancelled_final_state_logs_not_raises(self, core, monkeypatch):
        """取消竞态把最终状态改为 CANCELLED 时，execute_plan 不抛 AssertionError

        模拟：executor.execute_plan 正常收尾（COMPLETED）后，executor.cancel_plan
        异步 task（直接赋值）把状态改为 CANCELLED，core 步骤 4 应记录日志而非抛错。
        """
        plan = _ready_plan()
        real_execute = core.executor.execute_plan

        async def fake_execute(p):
            result = await real_execute(p)
            result.state = PlanState.CANCELLED  # 模拟 cancel_plan 竞态 task
            return result

        monkeypatch.setattr(core.executor, "execute_plan", fake_execute)

        result = await core.execute_plan(plan)  # 修复前抛 AssertionError，修复后正常返回

        assert result.state == PlanState.CANCELLED

    @pytest.mark.asyncio
    async def test_executor_cancel_plan_bypasses_state_machine(self):
        """executor.cancel_plan 直接赋值绕过状态机（边界 #3 源头，D18 范畴）

        现状：对已终态（COMPLETED）计划仍可强制改为 CANCELLED，无状态机校验。
        """
        core = PlanningCore()
        plan = Plan(state=PlanState.COMPLETED)

        result = await core.executor.cancel_plan(plan)

        assert result.state == PlanState.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_executing_plan_sync_transition(self, core):
        """合法取消：EXECUTING 经状态机同步转 CANCELLED"""
        plan = Plan(state=PlanState.EXECUTING)
        core._active_plans[plan.id] = plan

        ok = core.cancel_plan(plan.id)

        assert ok is True
        assert plan.state == PlanState.CANCELLED
        history = core.state_machine.get_transition_history(plan_id=plan.id)
        assert history[-1]["from_state"] == "executing"
        assert history[-1]["to_state"] == "cancelled"
