"""阶段 3（约束、预算与容错降级）专项测试

覆盖任务提示词"执行步骤 6"的 5 类机制正/反场景与评估标准 1-3：
1. 预算管理（D13）：steps/seconds 超限 → 正常收尾返回部分结果；
   tokens 超限 → 停止调用 LLM（mock 计数断言）；final_state/plan.metadata 预算可观测。
2. 任务级降级链（D14）：Task.fallback_actions 主工具失败 → fallback 成功；
   未注册跳过；全失败保留主错误；to_dict/from_dict 往返兼容。
3. 失败归因（D14）：failure_reason 四分类 + reflector lesson 被调用。
4. 重规划 Plan B（D14）：高优先级失败 → refine 修正任务集继续执行；
   无调整空间 / refine 失败 / replan_on_failure=false → 走中断路径。
5. 协作式取消（D18）：工具调用中被取消 → 计划 CANCELLED。
6. ask_user 恢复语义：resume_plan 后执行上下文含用户答案；
   等待超时以"用户未确认"结束；无等待问题返回明确提示。
"""

import asyncio
import json
import tempfile

import pytest
from unittest.mock import AsyncMock, MagicMock

from planning.budget import BudgetManager, BudgetStatus, PlanBudget
from planning.core import PlanningCore
from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task, TaskStatus
from planning.models.action import ActionResult
from planning.react import ReActLoop


# ────────────────────────────── D13：预算管理 ──────────────────────────────

class TestBudgetManager:
    """BudgetManager / PlanBudget 单元测试"""

    def test_from_config_nested_budget_priority(self):
        """嵌套 budget 段优先于直连兼容键"""
        budget = PlanBudget.from_config({
            "budget": {"max_steps": 5, "max_seconds": 9},
            "timeout_seconds": 3,
        })
        assert budget.max_steps == 5
        assert budget.max_seconds == 9

    def test_from_config_compat_flat_keys(self):
        """直连兼容键映射：timeout_seconds/token_budget/cost_budget"""
        budget = PlanBudget.from_config({
            "timeout_seconds": 3, "token_budget": 100, "cost_budget": 0.5,
        })
        assert budget.max_seconds == 3
        assert budget.max_tokens == 100
        assert budget.max_cost == 0.5

    def test_budget_disabled_when_no_dimension(self):
        """无任一限制维度时 enabled=False（向后兼容零行为变化）"""
        assert PlanBudget().enabled is False
        assert PlanBudget(max_steps=1).enabled is True

    def test_check_priority_steps_over_seconds(self):
        """维度判定优先级：steps 先于 seconds"""
        mgr = BudgetManager(PlanBudget(max_steps=1, max_seconds=999))
        mgr.record_step(1)
        assert mgr.check() == BudgetStatus.EXCEEDED_STEPS

    def test_record_tokens_updates_cost(self):
        """成本 = tokens/1000 × 单价"""
        mgr = BudgetManager(PlanBudget(), token_price_per_1k=0.002)
        mgr.record_tokens(1000)
        assert mgr.tokens == 1000
        assert mgr.cost == pytest.approx(0.002)

    def test_snapshot_fields(self):
        """snapshot 透出全部累计维度"""
        mgr = BudgetManager(PlanBudget(max_steps=3))
        mgr.record_step(1)
        mgr.record_tokens(500)
        snap = mgr.snapshot()
        assert snap["steps"] == 1
        assert snap["tokens"] == 500
        assert snap["cost"] == pytest.approx(0.001)
        assert "iterations" in snap and "elapsed_seconds" in snap


class TestReActBudget:
    """ReActLoop 预算：超限后不再发起 LLM 调用（评估标准 1）"""

    @staticmethod
    def _loop(token_budget: int = 10, **extra) -> ReActLoop:
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = [
            json.dumps({
                "reasoning": "调用工具",
                "action_type": "tool_call",
                "action": {"tool": "missing_tool", "params": {}, "description": "调用"},
            }),
            json.dumps({"reasoning": "完成", "action_type": "finish", "result": "完成"}),
        ]
        planner = MagicMock()
        planner.llm = mock_llm
        planner.tool_registry = ToolRegistry()
        loop = ReActLoop(
            planner, None, max_iterations=5,
            config={"token_budget": token_budget, **extra},
        )
        return loop, mock_llm

    @pytest.mark.asyncio
    async def test_token_exceeded_stops_llm_calls(self):
        """token 超限后不再发起后续 LLM 调用（mock 计数断言）"""
        loop, mock_llm = self._loop(token_budget=10)
        result = await loop.run("测试任务", {})

        assert result.success is False
        assert "token" in result.error
        assert mock_llm.chat.call_count == 1  # 仅第一次思考记账即超限，第二次迭代入口拦截

    @pytest.mark.asyncio
    async def test_react_result_final_state_carries_budget(self):
        """预算信息写入 ReActResult.final_state（可观测）"""
        loop, _ = self._loop(token_budget=10)
        result = await loop.run("测试任务", {})

        assert "budget" in result.final_state
        assert result.final_state["budget"]["tokens"] > 10
        assert result.token_used > 10

    @pytest.mark.asyncio
    async def test_no_budget_final_state_empty_backward_compat(self):
        """默认无预算：final_state 保持空 dict（向后兼容）"""
        planner = MagicMock()
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = json.dumps(
            {"reasoning": "x", "action_type": "finish", "result": "OK"}
        )
        planner.llm = mock_llm
        planner.tool_registry = ToolRegistry()
        loop = ReActLoop(planner, None, max_iterations=3, config={})
        result = await loop.run("简单任务", {})

        assert result.success is True
        assert result.final_state == {}


class TestExecutorBudget:
    """PlanExecutor 预算：每任务执行前检查，超限正常收尾返回部分结果"""

    @pytest.mark.asyncio
    async def test_max_steps_normal_wrap_up_partial(self):
        """steps 超限：已完成任务保留，未执行任务残留，正常收尾不抛错"""
        registry = ToolRegistry()

        def ok_tool(**kwargs):
            return "成功"

        registry.register("ok_tool", ok_tool)

        executor = PlanExecutor(registry, max_retries=1, config={"budget": {"max_steps": 1}})
        plan = Plan(original_task="预算任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="ok_tool 执行"))
        plan.add_task(Task(id="b", description="ok_tool 执行"))
        result = await executor.execute_plan(plan)

        assert result.metadata["budget"]["status"] == "exceeded_steps"
        assert result.get_task("a").status == TaskStatus.COMPLETED
        assert result.get_task("b").status == TaskStatus.PENDING
        assert result.state == PlanState.FAILED  # 部分任务未执行

    @pytest.mark.asyncio
    async def test_max_seconds_normal_wrap_up_partial(self):
        """seconds 超限：慢任务执行后收尾，返回部分结果"""
        registry = ToolRegistry()

        async def slow_tool(**kwargs):
            await asyncio.sleep(0.7)
            return "慢完成"

        def fast_tool(**kwargs):
            return "快完成"

        registry.register("slow_tool", slow_tool)
        registry.register("fast_tool", fast_tool)

        executor = PlanExecutor(registry, max_retries=1, config={"budget": {"max_seconds": 0.5}})
        plan = Plan(original_task="时间预算任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="slow_tool 执行"))
        plan.add_task(Task(id="b", description="fast_tool 执行"))
        result = await executor.execute_plan(plan)

        assert result.metadata["budget"]["status"] == "exceeded_seconds"
        assert result.get_task("a").status == TaskStatus.COMPLETED
        assert result.get_task("b").status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_no_budget_plan_completes_normally(self):
        """默认无预算配置 → 计划完整执行，不受预算逻辑干扰（向后兼容）"""
        registry = ToolRegistry()

        def ok_tool(**kwargs):
            return "成功"

        registry.register("ok_tool", ok_tool)

        executor = PlanExecutor(registry, max_retries=1, config={})
        plan = Plan(original_task="兼容任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="ok_tool 执行"))
        result = await executor.execute_plan(plan)

        assert result.state == PlanState.COMPLETED
        assert result.get_task("a").status == TaskStatus.COMPLETED


# ────────────────────────────── D14：任务级降级链 ──────────────────────────────

class TestTaskFallbackChain:
    """Task.fallback_actions 任务级降级链"""

    @staticmethod
    def _failing(**kwargs):
        raise RuntimeError("主工具故障")

    @pytest.mark.asyncio
    async def test_fallback_success_after_primary_failure(self):
        """主工具失败（重试耗尽）→ fallback 工具成功（评估标准 1 反例）"""
        registry = ToolRegistry()

        def backup(**kwargs):
            return "备份工具成功"

        registry.register("primary_tool", self._failing)
        registry.register("backup_tool", backup)

        executor = PlanExecutor(registry, max_retries=1, config={})
        plan = Plan(original_task="降级链任务", state=PlanState.READY)
        plan.add_task(Task(
            id="a", description="primary_tool 执行",
            fallback_actions=["backup_tool"],
        ))
        result = await executor.execute_plan(plan)

        task_a = result.get_task("a")
        assert task_a.status == TaskStatus.COMPLETED
        assert "备份工具成功" in str(task_a.result)

    @pytest.mark.asyncio
    async def test_fallback_unregistered_skipped(self):
        """fallback 工具未注册 → 跳过；主失败则任务失败（保留主错误）"""
        registry = ToolRegistry()
        registry.register("primary_tool", self._failing)

        executor = PlanExecutor(registry, max_retries=1, config={})
        plan = Plan(original_task="降级链任务", state=PlanState.READY)
        plan.add_task(Task(
            id="a", description="primary_tool 执行",
            fallback_actions=["not_registered"],
        ))
        result = await executor.execute_plan(plan)

        task_a = result.get_task("a")
        assert task_a.status == TaskStatus.FAILED
        assert "主工具故障" in str(task_a.error)

    @pytest.mark.asyncio
    async def test_all_fallbacks_fail_keeps_primary_error(self):
        """全部 fallback 失败 → 任务失败，错误保留主工具根因"""
        registry = ToolRegistry()

        def b1(**kwargs):
            raise RuntimeError("备份1故障")

        def b2(**kwargs):
            raise RuntimeError("备份2故障")

        registry.register("primary_tool", self._failing)
        registry.register("b1", b1)
        registry.register("b2", b2)

        executor = PlanExecutor(registry, max_retries=1, config={})
        plan = Plan(original_task="降级链任务", state=PlanState.READY)
        plan.add_task(Task(
            id="a", description="primary_tool 执行",
            fallback_actions=["b1", "b2"],
        ))
        result = await executor.execute_plan(plan)

        task_a = result.get_task("a")
        assert task_a.status == TaskStatus.FAILED
        assert "主工具故障" in str(task_a.error)

    def test_to_dict_from_dict_roundtrip(self):
        """fallback_actions 序列化往返兼容；缺省为空列表"""
        task = Task(id="x", description="d", fallback_actions=["b1", "b2"])
        data = task.to_dict()
        assert data["fallback_actions"] == ["b1", "b2"]

        restored = Task.from_dict(data)
        assert restored.fallback_actions == ["b1", "b2"]

        # 旧数据无 fallback_actions 键 → 缺省空列表（向后兼容）
        legacy = {"id": "y", "description": "d", "status": "pending"}
        assert Task.from_dict(legacy).fallback_actions == []


# ────────────────────────────── D14：失败归因 ──────────────────────────────

class TestFailureAttribution:
    """失败归因：failure_reason 四分类 + reflector lesson"""

    def test_classify_four_categories(self):
        """四分类：工具缺失 / 超时 / LLM 错误 / 逻辑错误"""
        assert (PlanExecutor._classify_failure(Task(), ActionResult.failure_result("工具不存在: x"))
                == "工具缺失")
        assert (PlanExecutor._classify_failure(Task(), ActionResult.failure_result("工具调用超时"))
                == "超时")
        assert (PlanExecutor._classify_failure(Task(), ActionResult.failure_result("LLM推理失败: 格式错误"))
                == "LLM错误")
        assert (PlanExecutor._classify_failure(Task(), ActionResult.failure_result("未知逻辑错误"))
                == "逻辑错误")

    @pytest.mark.asyncio
    async def test_failure_reason_written_and_lesson_recorded(self):
        """任务失败 → metadata 写入 failure_reason，reflector.learn_from_experience 被调用"""
        registry = ToolRegistry()

        def failing(**kwargs):
            raise RuntimeError("工具不存在: xyz")

        registry.register("primary_tool", failing)

        reflector = AsyncMock()
        executor = PlanExecutor(registry, max_retries=1, config={}, reflector=reflector)
        plan = Plan(original_task="归因任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="primary_tool 执行"))
        await executor.execute_plan(plan)

        task_a = plan.get_task("a")
        assert task_a.metadata["failure_reason"] == "工具缺失"
        assert reflector.learn_from_experience.await_count == 1

    @pytest.mark.asyncio
    async def test_success_task_no_attribution(self):
        """任务成功 → 不调用 reflector（失败归因仅失败路径触发）"""
        registry = ToolRegistry()

        def ok_tool(**kwargs):
            return "成功"

        registry.register("ok_tool", ok_tool)

        reflector = AsyncMock()
        executor = PlanExecutor(registry, max_retries=1, config={}, reflector=reflector)
        plan = Plan(original_task="成功任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="ok_tool 执行"))
        await executor.execute_plan(plan)

        assert reflector.learn_from_experience.await_count == 0


# ────────────────────────────── D14：重规划 Plan B ──────────────────────────────

class FakeDecomposer:
    """可编程的假分解器：控制 refine 是否调整计划 / 是否抛异常"""

    def __init__(self, mutate: bool = True, raise_on_refine: bool = False):
        self.refine_calls = 0
        self.mutate = mutate
        self.raise_on_refine = raise_on_refine

    async def refine(self, plan: Plan, feedback: str):
        self.refine_calls += 1
        if self.raise_on_refine:
            raise RuntimeError("refine 失败")
        if self.mutate:
            # 模拟"修正计划"：移除已失败的高优先级任务，任务集变化
            plan.tasks = [t for t in plan.tasks if t.id != "a"]


class TestReplanOnFailure:
    """高优先级任务失败 → 重规划修正而非中断"""

    @staticmethod
    def _build_plan() -> Plan:
        plan = Plan(original_task="重规划任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="primary_tool 执行", priority=4))
        plan.add_task(Task(id="b", description="ok_tool 执行", priority=3))
        return plan

    @staticmethod
    def _registry() -> ToolRegistry:
        registry = ToolRegistry()

        def failing(**kwargs):
            raise RuntimeError("主工具故障")

        def ok_tool(**kwargs):
            return "成功"

        registry.register("primary_tool", failing)
        registry.register("ok_tool", ok_tool)
        return registry

    @pytest.mark.asyncio
    async def test_replan_corrects_plan_and_continues(self):
        """高优先级失败 → refine 修正任务集 → 后续任务继续执行，计划完成而非中断"""
        decomposer = FakeDecomposer(mutate=True)
        executor = PlanExecutor(
            self._registry(), max_retries=1, config={}, decomposer=decomposer,
        )
        plan = self._build_plan()
        result = await executor.execute_plan(plan)

        assert decomposer.refine_calls == 1
        assert result.metadata["replanned"]["failed_task"] == "a"
        assert result.get_task("b").status == TaskStatus.COMPLETED
        assert result.state == PlanState.COMPLETED

    @pytest.mark.asyncio
    async def test_replan_no_adjustment_interrupts(self):
        """refine 无调整空间（任务集未变）→ 走中断路径"""
        decomposer = FakeDecomposer(mutate=False)
        executor = PlanExecutor(
            self._registry(), max_retries=1, config={}, decomposer=decomposer,
        )
        plan = self._build_plan()
        result = await executor.execute_plan(plan)

        assert decomposer.refine_calls == 1
        assert "replanned" not in result.metadata
        assert result.state == PlanState.FAILED
        assert result.get_task("b").status == TaskStatus.PENDING  # 中断后未执行

    @pytest.mark.asyncio
    async def test_replan_refine_failure_interrupts(self):
        """refine 抛异常 → 降级走中断路径（计划标记失败）"""
        decomposer = FakeDecomposer(raise_on_refine=True)
        executor = PlanExecutor(
            self._registry(), max_retries=1, config={}, decomposer=decomposer,
        )
        plan = self._build_plan()
        result = await executor.execute_plan(plan)

        assert decomposer.refine_calls == 1
        assert result.state == PlanState.FAILED

    @pytest.mark.asyncio
    async def test_replan_disabled_by_config(self):
        """replan_on_failure=false → 不调用 refine，直接中断（回滚开关）"""
        decomposer = FakeDecomposer(mutate=True)
        executor = PlanExecutor(
            self._registry(), max_retries=1,
            config={"replan_on_failure": False}, decomposer=decomposer,
        )
        plan = self._build_plan()
        result = await executor.execute_plan(plan)

        assert decomposer.refine_calls == 0
        assert result.state == PlanState.FAILED


# ────────────────────────────── D18：协作式取消 ──────────────────────────────

class TestCollaborativeCancel:
    """工具调用中被取消 → 计划 CANCELLED"""

    @pytest.mark.asyncio
    async def test_cancel_during_tool_call(self):
        registry = ToolRegistry()
        started = asyncio.Event()

        async def slow_tool(**kwargs):
            started.set()
            await asyncio.sleep(30)
            return "太慢"

        registry.register("slow_tool", slow_tool)

        executor = PlanExecutor(registry, max_retries=1, config={"tool_timeout": 30})
        plan = Plan(original_task="取消任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="slow_tool 执行"))

        task = asyncio.create_task(executor.execute_plan(plan))
        await started.wait()
        await executor.cancel_plan(plan)
        await task

        assert plan.state == PlanState.CANCELLED
        assert plan.get_task("a").status != TaskStatus.COMPLETED  # 未完成即收尾取消


# ────────────────────────────── ask_user 恢复语义 ──────────────────────────────

class TestAskUserResume:
    """resume_plan 恢复 + 超时 + 无等待问题"""

    @staticmethod
    def _core(tmp_dir: str, ask_user_timeout: float = 300):
        llm = AsyncMock()
        core = PlanningCore(llm_service=llm, config={
            "planning": {"storage": {"enabled": False}, "persist_dir": tmp_dir},
            "reflector": {"persist_dir": tmp_dir},
            "ask_user_timeout_seconds": ask_user_timeout,
        })
        return core, llm

    def test_ask_user_registers_pending_question(self):
        """ask_user 登记等待中的问题，get_pending_question 可查"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            core, _ = self._core(tmp_dir)
            assert core.ask_user("plan_1", "是否继续?") is True
            pending = core.get_pending_question("plan_1")
            assert pending is not None
            assert pending["question"] == "是否继续?"
            assert pending["timed_out"] is False

    def test_ask_user_empty_question_rejected(self):
        """空问题拒绝登记"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            core, _ = self._core(tmp_dir)
            assert core.ask_user("plan_1", "") is False

    @pytest.mark.asyncio
    async def test_resume_plan_writes_user_answer_to_context(self):
        """恢复后执行上下文包含用户答案（评估标准 3）"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            core, llm = self._core(tmp_dir)
            llm.chat.return_value = json.dumps(
                {"reasoning": "根据用户答案继续", "action_type": "finish", "result": "完成"}
            )
            core.ask_user("plan_1", "需要确认?", task="继续任务", context={"history": "x"})
            chat_result = await core.resume_plan("plan_1", "确认继续")

            assert chat_result.used_planning is True
            # 用户答案写入执行上下文 → 进入 ReAct 提示词（可断言 context 值）
            prompt = llm.chat.await_args.args[0][0]["content"]
            assert "user_answer" in prompt
            assert "确认继续" in prompt

    @pytest.mark.asyncio
    async def test_resume_plan_timeout_ends_unconfirmed(self):
        """等待超时 → 自动放弃，以"用户未确认"结束"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            core, _ = self._core(tmp_dir, ask_user_timeout=-1)
            core.ask_user("plan_1", "问题")
            chat_result = await core.resume_plan("plan_1", "答案")
            assert "用户未确认" in chat_result.response

    @pytest.mark.asyncio
    async def test_resume_plan_no_pending_question(self):
        """无等待中的问题 → 明确提示"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            core, _ = self._core(tmp_dir)
            chat_result = await core.resume_plan("plan_1", "答案")
            assert "没有等待中的问题" in chat_result.response

    @pytest.mark.asyncio
    async def test_chat_ask_user_returns_pending_plan_id(self):
        """ReAct 返回"等待用户输入" → ChatResult 挂起 + ask_user 已登记（可恢复）"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            core, llm = self._core(tmp_dir)
            llm.chat.return_value = json.dumps({
                "reasoning": "需要确认",
                "action_type": "ask_user",
                "action": {"tool": "", "params": {}, "description": "询问"},
                "result": "需要用户确认",
            })
            context = {"session_id": "sess_1"}
            chat_result = await core.chat("帮我完成复杂的任务流程", context)

            assert chat_result.pending_plan_id == "sess_1"
            assert core.get_pending_question("sess_1") is not None
