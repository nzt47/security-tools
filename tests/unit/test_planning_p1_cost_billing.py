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
import json
import pytest

from planning.budget import BudgetManager, BudgetStatus, PlanBudget
from planning.core import PlanningCore
from planning.decomposer import TaskDecomposer
from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task
from planning.react import ReActLoop
from planning.reflector import Reflector


class _FakeLLM:
    """可预测 mock LLM：返回固定文本，供 Plan 路径 LLM 推理记账"""

    def __init__(self, response: str):
        self._response = response
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        return self._response


class _SeqLLM:
    """序列 mock LLM：按调用顺序依次返回，超界回退最后一个（供多阶段记账测试）"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, messages):
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]


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

    # ── TD-4 扩展：decomposer/reflector 记账生效性 ──

    @pytest.mark.asyncio
    async def test_decompose_billing_included(self):
        """TD-4 R-1：LLM 分解成本计入注入的 budget_manager（非法 JSON 触发重试+规则回退，
        但 LLM 调用已发生并记账——验证记账点不在解析成功路径上）"""
        mgr = BudgetManager(token_price_per_1k=0.002)
        decomposer = TaskDecomposer(
            llm_service=_FakeLLM("这不是合法JSON"),
            config={"max_subtasks": 3},
            budget_manager=mgr,
        )
        plan = await decomposer.decompose("帮我创建文件并发送邮件", {})
        assert plan is not None
        assert mgr.tokens > 0
        assert mgr.cost > 0

    @pytest.mark.asyncio
    async def test_plan_reflect_billing_included(self):
        """TD-4 R-3/R-11：计划路径 plan_reflect 记账后 core 刷新快照，成本进入 metrics"""
        cfg = {"budget": {"enabled": True}, "token_price_per_1k": 0.002}
        llm = _SeqLLM([
            "这不是JSON",  # decompose：触发规则回退，但 LLM 调用已记账
            json.dumps({"overall_score": 8.0, "effectiveness": "执行成功",
                        "lessons": ["保持"], "improvements": []}),  # plan_reflect
        ])
        core = PlanningCore(llm_service=llm, tool_registry=ToolRegistry(), config=cfg)
        plan = await core.plan("帮我分析整个系统流程", {})
        plan = await core.execute_plan(plan)

        # 刷新后快照与实例一致（plan_reflect 记账被覆盖进 metadata）
        assert plan.metadata["budget"]["cost"] > 0
        assert plan.metadata["budget"]["cost"] == pytest.approx(core.executor.budget_manager.cost)
        # 反射成本进入 metrics（_record_plan_result 读取刷新后快照）
        metrics = core.get_planning_metrics()
        assert metrics["cost_total"] > 0

    @pytest.mark.asyncio
    async def test_react_step_reflect_billing(self, tmp_path):
        """TD-4 R-4：ReAct 路径 step_reflect 记账记入 react 实例，进入 react_result.cost"""
        llm = _SeqLLM([
            json.dumps({"reasoning": "先搜索", "action_type": "tool_call",
                        "action": {"tool": "search", "params": {"query": "t"}, "description": "搜索"},
                        "confidence": 0.9}),
            json.dumps({"assessment": "需要调整", "confidence": 0.8,
                        "adjustments": ["调整建议A"], "next_steps": []}),  # step_reflect
            json.dumps({"reasoning": "完成", "action_type": "finish",
                        "result": "完成", "confidence": 0.9}),
        ])
        planner = type("P", (), {})()
        planner.llm = llm
        planner.tool_registry = ToolRegistry()
        planner.tool_registry.register("search", lambda query: "结果")

        reflector = Reflector(llm_service=llm, config={},
                              persist_dir=str(tmp_path / "reflection"))
        loop = ReActLoop(planner, reflector, max_iterations=5,
                         config={"token_price_per_1k": 0.002})
        result = await loop.run("搜索测试", {"user": "u"})

        assert result.success
        assert llm.calls >= 3  # 思考1 + step_reflect + 思考2 均发生
        # step_reflect 记账记入 react 实例 → 成本含反思分量
        assert result.cost > 0
        assert loop.budget_manager.tokens > 0

    @pytest.mark.asyncio
    async def test_exceeded_cost_with_decompose(self):
        """TD-4 R-9：预算 max_cost 含分解成本——分解记账后 check 即超限"""
        mgr = BudgetManager(PlanBudget(max_cost=1e-6), token_price_per_1k=0.002)
        decomposer = TaskDecomposer(llm_service=_FakeLLM("bad"), config={}, budget_manager=mgr)
        await decomposer.decompose("复杂分析任务", {})
        assert mgr.check() == BudgetStatus.EXCEEDED_COST

    @pytest.mark.asyncio
    async def test_refresh_preserves_status(self):
        """TD-4 R-10：core 刷新快照保留循环内写入的超限 status（不覆盖丢失）"""
        cfg = {"budget": {"enabled": True, "max_cost": 1e-6}, "token_price_per_1k": 0.002}
        executor = PlanExecutor(ToolRegistry(), config=cfg)
        executor.llm = _FakeLLM("r" * 90)
        plan = Plan(original_task="完成复杂分析任务", state=PlanState.READY)
        plan.add_task(Task(id="t1", description="完成复杂分析任务"))
        plan.add_task(Task(id="t2", description="生成数据报告"))
        await executor.execute_plan(plan)  # t1 记账后循环顶部 check 超限 → 写入 status
        assert plan.metadata["budget"]["status"] == "exceeded_cost"

        # 模拟 core 刷新块（TD-4 收口：快照刷新 + status 保留）
        _prev = (plan.metadata or {}).get("budget") or {}
        _prev_status = _prev.get("status")
        plan.metadata["budget"] = executor.budget_manager.snapshot()
        if _prev_status:
            plan.metadata["budget"]["status"] = _prev_status

        assert plan.metadata["budget"]["status"] == "exceeded_cost"
        assert plan.metadata["budget"]["cost"] > 0
