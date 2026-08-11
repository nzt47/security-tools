"""D11 复现测试：无规划验证机制

缺陷（P1）：依赖图不校验（悬空依赖→任务永不可执行→计划卡死）；工具可用性不预检；
循环依赖不检测。

预期失败：悬空依赖计划应在执行前被验证器拦截并给出明确的依赖错误
→ 当前执行期才卡死并返回泛化错误"计划执行超时或异常终止" → 断言失败即复现成功。
"""
import pytest

from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task


class TestDefectD11:
    """D11：悬空依赖应在执行前被验证器拦截"""

    @pytest.mark.asyncio
    async def test_dangling_dependency_detected_by_validator(self):
        registry = ToolRegistry()
        registry.register("ta", lambda: "ok")
        executor = PlanExecutor(registry)

        plan = Plan(original_task="悬空依赖任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="调用ta"))
        plan.add_task(Task(id="b", description="调用ta", dependencies=["nonexistent_id"]))
        plan.state = PlanState.READY

        result = await executor.execute_plan(plan)

        # 目标行为：验证器应在执行前拦截悬空依赖，错误指明"依赖不存在"
        assert result.state == PlanState.FAILED
        assert "依赖" in (result.error or "")

    @pytest.mark.asyncio
    async def test_unregistered_tool_detected_by_validator(self):
        """能力基线规格: 执行前应预检工具可用性，引用未注册工具的任务被拦截（D11 已实现）"""
        registry = ToolRegistry()  # 未注册任何工具
        executor = PlanExecutor(registry)

        plan = Plan(original_task="未注册工具任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="调用一个不存在的工具"))
        plan.state = PlanState.READY

        result = await executor.execute_plan(plan)

        # 目标行为：执行前拦截并指明工具不可用
        assert result.state == PlanState.FAILED
        assert "工具" in (result.error or "")
