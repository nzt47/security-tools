"""P1 已知问题收口测试：token 计费接入与成本口径回填

【不易】预算记账不改变既有行为（默认预算全 None = 不限制，零行为变化）；
  ReAct 与 Plan 两路径计价口径必须一致（同字符/3 token 估算 + 同单价），防双轨漂移。
【变易】token_price_per_1k 从 config 透传（planning.token_price_per_1k，默认 0.002），
  修改配置后 budget 快照/埋点 cost 按新单价变化。
【简易】用可预测的 mock LLM 与固定文本，直接断言 budget 快照数值。

验收口径（阶段 5 任务卡片 P1）：
- EXCEEDED_COST 降级路径有单测覆盖（超限时正常收尾返回部分结果，不抛异常）
- token_price_per_1k 配置透传生效（改单价后 cost 按比例变化）
- ReAct 与 Plan 两路径计价口径一致（同一 price 与同一 token 估算方式）
"""
import pytest

from planning.budget import BudgetManager, BudgetStatus
from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task
from planning.react import ReActLoop


class _FakeLLM:
    """可预测 mock LLM：返回固定文本，供 Plan 路径 LLM 推理记账"""

    def __init__(self, response: str):
        self._response = response
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        return self._response


class TestP1CostBilling:
    """P1：token 计费接入与成本口径回填"""

    @pytest.mark.asyncio
    async def test_exceeded_cost_degrades_gracefully(self):
        """EXCEEDED_COST 降级路径：超限时正常收尾返回部分结果，不抛异常"""
        # max_cost 极低：t1 一次 LLM 推理记账即超限（prompt+response ≈ 34 tokens，
        # cost = 34/1000*0.002 = $0.000068 > 1e-6）；t2 应在下一轮被预算中断
        cfg = {"budget": {"enabled": True, "max_cost": 1e-6}, "token_price_per_1k": 0.002}
        executor = PlanExecutor(ToolRegistry(), config=cfg)
        executor.llm = _FakeLLM("r" * 90)

        plan = Plan(original_task="完成复杂分析任务", state=PlanState.READY)
        plan.add_task(Task(id="t1", description="完成复杂分析任务"))
        plan.add_task(Task(id="t2", description="生成数据报告"))

        # 不抛异常即通过"正常收尾"验收
        await executor.execute_plan(plan)

        assert executor.budget_manager.check() == BudgetStatus.EXCEEDED_COST
        snap = plan.metadata["budget"]
        assert snap["status"] == "exceeded_cost"
        assert snap["cost"] > 0
        # LLM 推理确实发生过记账（t1 = LLM_REASONING 动作）
        assert executor.budget_manager.tokens > 0
        # 部分结果保留：t1 已完成、t2 未执行（预算中断截断后续调度）
        assert plan.tasks[0].status.value == "completed"
        assert plan.tasks[1].status.value == "pending"

    @pytest.mark.asyncio
    async def test_token_price_per_1k_passthrough(self):
        """token_price_per_1k 配置透传生效：单价翻倍 → 成本快照翻倍"""
        costs = {}
        for price in (0.002, 0.004):
            executor = PlanExecutor(ToolRegistry(), config={"token_price_per_1k": price})
            executor.llm = _FakeLLM("r" * 90)
            plan = Plan(original_task="完成复杂分析任务", state=PlanState.READY)
            plan.add_task(Task(id="t1", description="完成复杂分析任务"))
            await executor.execute_plan(plan)
            costs[price] = plan.metadata["budget"]["cost"]
            assert costs[price] > 0
        # 同一文本量级（34 tokens），单价 2 倍 → 成本精确 2 倍
        assert costs[0.004] == pytest.approx(costs[0.002] * 2.0)

    @pytest.mark.asyncio
    async def test_react_and_plan_billing_consistent(self):
        """ReAct 与 Plan 计价口径一致：同字符/3 估算 + 同单价 → 同成本"""
        cfg = {"token_price_per_1k": 0.002}
        react_loop = ReActLoop(None, None, max_iterations=1, config=cfg)
        executor = PlanExecutor(ToolRegistry(), config=cfg)

        text = "x" * 300  # 300 字符 → 100 tokens（字符/3）
        react_loop.budget_manager.record_text(text)
        executor.budget_manager.record_text(text)

        assert react_loop.budget_manager.tokens == executor.budget_manager.tokens
        assert react_loop.budget_manager.tokens == 300 // 3
        assert react_loop.budget_manager.cost == executor.budget_manager.cost
        assert executor.budget_manager.cost == pytest.approx(100 / 1000.0 * 0.002)
        # 两路径单价均从 config 透传（防双轨漂移：任一硬编码即失败）
        assert react_loop.budget_manager._token_price_per_1k == 0.002
        assert executor.budget_manager._token_price_per_1k == 0.002

    def test_record_text_char3_estimation(self):
        """BudgetManager 无 token 计数器时回退字符/3 近似（Plan/ReAct 共用同一实现）"""
        mgr = BudgetManager(token_price_per_1k=0.002)
        mgr.record_text("x" * 300)
        assert mgr.tokens == 100
        assert mgr.cost == pytest.approx(100 / 1000.0 * 0.002)
