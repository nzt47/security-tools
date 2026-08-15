"""D1 复现测试：全成功计划被误判为"部分任务失败"

缺陷（P0 正确性）：executor.execute_plan() 在 plan 仍处于 EXECUTING 状态时调用
Plan.is_success()（要求 state == COMPLETED），导致"全部成功"分支永不触发，
全成功计划被标记为 result="计划执行完成,但部分任务失败"，污染执行记录与统计。

同类短路（C1，D1 下游表现）：executor 直接改 plan.state 绕过状态机，
导致 core.execute_plan() 步骤4 的 `if plan.state == PlanState.EXECUTING` 永不成立（死代码），
(EXECUTING, COMPLETED) 状态机钩子永不触发、转换历史缺失。

预期失败（阶段 0 复现）：当前代码 result 含"部分任务失败" → 断言失败即复现成功。
修复后（阶段 1）：executor 先基于任务状态计算 all_completed/any_failed 再设置 COMPLETED 与
result，全成功计划 result="所有任务执行成功"；is_success() 对外语义不变（state==COMPLETED）。
"""
import pytest
from planning.core import PlanningCore
from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task


class TestDefectD1:
    """D1：全成功计划不应被标记为部分失败"""

    @pytest.mark.asyncio
    async def test_full_success_plan_not_marked_partial_failure(self):
        registry = ToolRegistry()
        registry.register("ta", lambda: "ok")
        executor = PlanExecutor(registry)

        plan = Plan(original_task="全部成功任务", state=PlanState.READY)
        plan.add_task(Task(id="t1", description="调用ta"))
        plan.state = PlanState.READY

        result = await executor.execute_plan(plan)

        # 目标行为：全成功计划应进入 COMPLETED 且 result 为成功语义
        assert result.is_success() is True
        assert "部分任务失败" not in str(result.result)
        assert str(result.result) == "所有任务执行成功"

    @pytest.mark.asyncio
    async def test_final_transition_goes_through_state_machine(self, tmp_path):
        """C1：最终状态转换应经状态机触发 (EXECUTING, COMPLETED) 钩子

        目标行为：计划执行完成后，core 步骤4 的最终状态判断应生效，
        通过 state_machine.transition 触发钩子并记录转换历史。
        当前：executor 直接改 state，步骤4 死代码，钩子永不触发。
        """
        # P2：reflector 隔离于 tmp_path，不依赖宿主 data/reflection
        core = PlanningCore(config={"reflector": {"persist_dir": str(tmp_path)}})
        core.tool_registry.register("echo", lambda: "ok")

        plan = Plan(original_task="状态机钩子测试", state=PlanState.READY)
        plan.add_task(Task(id="a", description="echo 任务"))
        plan.state = PlanState.READY

        fired_hooks = []
        core.state_machine.register_hook(
            PlanState.EXECUTING, PlanState.COMPLETED,
            lambda p: fired_hooks.append(p.id)
        )

        result = await core.execute_plan(plan)

        assert result.state == PlanState.COMPLETED
        # 目标行为：EXECUTING->COMPLETED 应经状态机转换并触发钩子
        assert result.id in fired_hooks, "目标: 最终状态转换应经状态机触发钩子"

