"""D18 协作式取消：复杂并发场景稳定性专项测试

验证取消传播在以下并发场景下的稳定性（D18 主缺陷修复后）：
1. 多计划并发执行时取消单个计划，其余计划不受影响（per-plan 隔离）
2. 重复取消幂等（不抛异常，状态保持）
3. 链式任务执行中取消：后续任务不再被调度
4. 计划完成后取消：无异常（task.done 守卫）
5. 同步工具执行中取消：执行完当前调用后停止调度（降级语义）
"""
import asyncio
import time

import pytest

from planning.core import PlanningCore
from planning.models import Plan, PlanState, Task, TaskStatus


@pytest.fixture
def core() -> PlanningCore:
    """无 LLM 规则模式规划引擎"""
    c = PlanningCore()
    return c


def _plan_with_tasks(*descriptions, original_task="并发稳定性测试") -> Plan:
    plan = Plan(original_task=original_task, state=PlanState.READY)
    for i, desc in enumerate(descriptions):
        plan.add_task(Task(id=f"t{i+1}", description=desc))
    return plan


class TestConcurrentPlansIsolation:
    """多计划并发取消隔离"""

    @pytest.mark.asyncio
    async def test_cancel_one_plan_does_not_affect_other(self, core):
        """两个计划并发执行，取消 plan_a 后 plan_b 正常完成"""
        started_a, started_b = asyncio.Event(), asyncio.Event()

        async def slow_a(**kwargs):
            started_a.set()
            await asyncio.sleep(30)  # 被取消时中断
            return "a"

        async def slow_b(**kwargs):
            started_b.set()
            await asyncio.sleep(0.3)
            return "b"

        core.register_tool("slow_a", slow_a)
        core.register_tool("slow_b", slow_b)

        plan_a = _plan_with_tasks("slow_a 任务", original_task="计划A")
        plan_b = _plan_with_tasks("slow_b 任务", original_task="计划B")
        core._active_plans[plan_a.id] = plan_a
        core._active_plans[plan_b.id] = plan_b

        task_a = asyncio.create_task(core.execute_plan(plan_a))
        task_b = asyncio.create_task(core.execute_plan(plan_b))
        await asyncio.gather(started_a.wait(), started_b.wait())  # 两个工具均已开始

        assert core.cancel_plan(plan_a.id) is True

        result_a, result_b = await asyncio.gather(task_a, task_b)

        assert result_a.state == PlanState.CANCELLED          # 被取消的计划
        assert result_b.state == PlanState.COMPLETED          # 其余计划不受影响
        assert plan_a.get_task("t1").status != TaskStatus.COMPLETED


class TestCancelIdempotency:
    """重复取消幂等"""

    @pytest.mark.asyncio
    async def test_repeated_cancel_is_idempotent(self, core):
        """执行中重复取消多次：不抛异常，最终状态 CANCELLED"""
        started = asyncio.Event()

        async def slow(**kwargs):
            started.set()
            await asyncio.sleep(30)
            return "ok"

        core.register_tool("slow", slow)
        plan = _plan_with_tasks("slow 任务")
        core._active_plans[plan.id] = plan

        run_task = asyncio.create_task(core.execute_plan(plan))
        await started.wait()

        core.cancel_plan(plan.id)
        await asyncio.sleep(0.05)
        # 重复取消：状态机拒绝（返回 False），不抛异常
        assert core.cancel_plan(plan.id) is False
        assert core.cancel_plan(plan.id) is False

        result = await asyncio.wait_for(run_task, timeout=5.0)

        assert result.state == PlanState.CANCELLED


class TestSchedulingOnCancel:
    """取消后的任务调度行为"""

    @pytest.mark.asyncio
    async def test_cancel_stops_scheduling_remaining_tasks(self, core):
        """链式任务执行中取消：后续任务保持 PENDING 不再调度"""
        started = asyncio.Event()

        async def slow(**kwargs):
            started.set()
            await asyncio.sleep(0.5)
            return "ok"

        async def quick(**kwargs):
            return "q"

        core.register_tool("slow", slow)
        core.register_tool("quick", quick)

        plan = _plan_with_tasks("quick 任务", "slow 任务", "quick 任务")
        # D5 并行语义：同层无依赖任务并行执行，取消只能阻止"尚未开始"的任务。
        # 因此"后续任务"需显式依赖链（t3 依赖 t2），取消后才保持 PENDING 不被调度。
        plan.get_task("t3").dependencies = ["t2"]
        core._active_plans[plan.id] = plan

        run_task = asyncio.create_task(core.execute_plan(plan))
        await started.wait()  # t2 (slow) 已开始执行

        core.cancel_plan(plan.id)

        result = await asyncio.wait_for(run_task, timeout=5.0)

        assert result.state == PlanState.CANCELLED
        assert plan.get_task("t3").status == TaskStatus.PENDING  # 后续任务不再调度

    @pytest.mark.asyncio
    async def test_cancel_after_completion_no_error(self, core):
        """计划完成后取消：不抛异常（task.done 守卫）"""
        async def quick(**kwargs):
            return "q"

        core.register_tool("quick", quick)
        plan = _plan_with_tasks("quick 任务")
        core._active_plans[plan.id] = plan

        result = await core.execute_plan(plan)
        assert result.state == PlanState.COMPLETED

        # 完成后取消：低层 executor.cancel_plan 幂等不抛（状态覆盖为已知残余行为）
        await core.executor.cancel_plan(plan)

    @pytest.mark.asyncio
    async def test_cancel_flag_before_execution_stops_all_tasks(self, core):
        """取消标志预置（同步工具阻塞期间取消请求排队的等价形态）→ 不执行任何任务

        注：asyncio 单线程模型中同步阻塞期间无法并发运行取消协程，
        同步工具执行中取消不可达（本质限制）；预置标志验证降级语义的确定性形态。
        """
        async def quick(**kwargs):
            return "q"

        core.register_tool("quick", quick)
        plan = _plan_with_tasks("quick 任务", "quick 任务")

        await core.executor.cancel_plan(plan)  # 预置取消标志（等价于取消请求已排队）

        result = await core.execute_plan(plan)

        assert result.state == PlanState.CANCELLED
        assert plan.get_task("t1").status == TaskStatus.PENDING  # 无任务被调度
        assert plan.get_task("t2").status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_cancel_after_sync_task_during_async_task(self, core):
        """同步任务完成后、异步任务执行中取消：同步结果保留，后续任务不再调度"""
        started = asyncio.Event()

        def sync_quick():
            time.sleep(0.05)  # 同步快任务（完成后让出事件循环）
            return "sync done"

        async def slow(**kwargs):
            started.set()
            await asyncio.sleep(30)
            return "ok"

        async def quick(**kwargs):
            return "q"

        core.register_tool("sync_quick", sync_quick)
        core.register_tool("slow", slow)
        core.register_tool("quick", quick)

        plan = _plan_with_tasks("sync_quick 任务", "slow 任务", "quick 任务")
        # 同 test_cancel_stops_scheduling_remaining_tasks：t3 依赖 t1/t2，
        # 保证取消发生在 t3 调度之前（D5 并行语义下无依赖任务同批并行）。
        plan.get_task("t3").dependencies = ["t1", "t2"]
        core._active_plans[plan.id] = plan

        run_task = asyncio.create_task(core.execute_plan(plan))
        await started.wait()  # t2 (slow 异步) 已开始，t1 (sync_quick) 已完成

        core.cancel_plan(plan.id)

        result = await asyncio.wait_for(run_task, timeout=5.0)

        assert result.state == PlanState.CANCELLED
        assert plan.get_task("t1").status == TaskStatus.COMPLETED  # 已完成的同步任务保留
        assert plan.get_task("t3").status == TaskStatus.PENDING     # 后续任务不再调度
