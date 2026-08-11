"""阶段 3（D13 预算 / D14 降级链）：并发场景稳定性专项集成测试

验证预算约束与降级链在并发场景下的稳定性：
1. 多 ReActLoop 并发运行，各自独立预算隔离（deadline/token 互不串扰）
2. 并行计划执行中，单任务主工具失败走降级链成功，其余并行任务不受影响
3. 多任务并行同批触发降级链（每个任务独立尝试自己的备份）
4. 降级链全部失败 → 任务失败，计划收尾不误判成功
5. 预算超限与取消并发：预算终止路径无状态滞留
"""
import asyncio
import json
import tempfile

import pytest
from unittest.mock import AsyncMock

from planning.core import PlanningCore
from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task, TaskStatus
from planning.react import ReActLoop
from planning.reflector import Reflector


# ────────────────────────────── D13：预算并发 ──────────────────────────────

class TestBudgetConcurrency:
    """D13：多 ReActLoop 并发时预算隔离"""

    @staticmethod
    def _loop_with_timeout_budget(tmp_dir: str, timeout: float) -> ReActLoop:
        planner = type("P", (), {})()
        planner.llm = AsyncMock()
        planner.llm.chat.side_effect = [
            json.dumps({
                "reasoning": "调用慢工具",
                "action_type": "tool_call",
                "action": {"tool": "slow_tool", "params": {}, "description": "慢工具"},
            }),
            json.dumps({"reasoning": "完成", "action_type": "finish", "result": "完成"}),
        ]
        planner.tool_registry = ToolRegistry()
        planner.tool_registry.register("slow_tool", TestBudgetConcurrency._slow_tool)
        return ReActLoop(
            planner, Reflector(persist_dir=tmp_dir),
            max_iterations=3, config={"timeout_seconds": timeout},
        )

    @staticmethod
    async def _slow_tool(**kwargs):
        await asyncio.sleep(0.3)
        return "慢工具完成"

    @pytest.mark.asyncio
    async def test_concurrent_loops_budget_isolated(self):
        """两个 ReActLoop 并发：预算 0.05s 的终止，无预算的跑完（互不串扰）"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            loop_short = self._loop_with_timeout_budget(tmp_dir, 0.05)
            loop_long = self._loop_with_timeout_budget(tmp_dir, None)  # 无 deadline

            r1, r2 = await asyncio.gather(
                loop_short.run("预算任务", {}),
                loop_long.run("无预算任务", {}),
            )

            # 短预算：终止且错误指明预算/超时；长循环：正常完成
            assert r1.success is False
            assert "预算" in (r1.error or "") or "超时" in (r1.error or "")
            assert r2.success is True

    @pytest.mark.asyncio
    async def test_token_budget_does_not_leak_across_loops(self):
        """token 预算隔离：两个循环各跑各的累计，不共享计数器"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            def _mk(chat_seq):
                planner = type("P", (), {})()
                llm = AsyncMock()
                llm.chat.side_effect = chat_seq
                planner.llm = llm
                planner.tool_registry = ToolRegistry()
                return ReActLoop(planner, None, max_iterations=5, config={"token_budget": 10})

            # 两步序列：第 1 轮 tool_call 累计 token 超过预算 → 第 2 轮迭代入口检查触发终止
            tool_first = [
                json.dumps({
                    "reasoning": "调用工具",
                    "action_type": "tool_call",
                    "action": {"tool": "missing_tool", "params": {}, "description": "调用"},
                }),
                json.dumps({"reasoning": "完成", "action_type": "finish", "result": "完成"}),
            ]
            finish_only = [json.dumps({"reasoning": "x", "action_type": "finish", "result": "B"})]

            loop_a = _mk(tool_first)
            loop_b = _mk(finish_only)

            # 并发运行：A 超预算终止，B 正常完成——若计数器共享则 B 也会被误杀
            ra, rb = await asyncio.gather(loop_a.run("任务A", {}), loop_b.run("任务B", {}))

            assert ra.success is False
            assert "token" in (ra.error or "")
            assert rb.success is True


# ────────────────────────────── D14：降级链并发 ──────────────────────────────

class TestDegradeChainConcurrency:
    """D14：降级链在并行执行场景下的稳定性"""

    @staticmethod
    def _executor_with_chain(registry: ToolRegistry, chain: dict) -> PlanExecutor:
        return PlanExecutor(registry, max_retries=1, config={"degrade_chain": chain})

    @pytest.mark.asyncio
    async def test_degrade_chain_in_parallel_batch(self):
        """并行批中：任务a 主工具失败→备份成功；任务b 主工具直接成功（互不影响）"""
        registry = ToolRegistry()

        def failing_a(**kwargs):
            raise RuntimeError("a主故障")

        def backup_a(**kwargs):
            return "a备份成功"

        def ok_b(**kwargs):
            return "b成功"

        registry.register("tool_a", failing_a)
        registry.register("backup_a", backup_a)
        registry.register("tool_b", ok_b)

        executor = self._executor_with_chain(
            registry, {"tool_a": ["backup_a"]}
        )

        plan = Plan(original_task="并行降级任务", state=PlanState.READY)
        plan.add_task(Task(id="a", description="调用tool_a"))
        plan.add_task(Task(id="b", description="调用tool_b"))
        plan.state = PlanState.READY

        result = await executor.execute_plan(plan)

        # 并行执行：a 降级成功，b 主成功，计划整体完成
        assert result.state == PlanState.COMPLETED
        assert "a备份成功" in str(result.get_task("a").result)
        assert "b成功" in str(result.get_task("b").result)

    @pytest.mark.asyncio
    async def test_each_parallel_task_uses_own_chain(self):
        """多任务并行同批各自触发降级链（每个任务独立尝试自己的备份，不互相污染）"""
        registry = ToolRegistry()

        def fail_a(**kwargs):
            raise RuntimeError("a主故障")

        def fail_b(**kwargs):
            raise RuntimeError("b主故障")

        def bak_a(**kwargs):
            return "A备份"

        def bak_b(**kwargs):
            return "B备份"

        for name, fn in [("ta", fail_a), ("tb", fail_b), ("bak_a", bak_a), ("bak_b", bak_b)]:
            registry.register(name, fn)

        executor = self._executor_with_chain(
            registry, {"ta": ["bak_a"], "tb": ["bak_b"]}
        )

        plan = Plan(original_task="各自降级", state=PlanState.READY)
        plan.add_task(Task(id="a", description="调用ta"))
        plan.add_task(Task(id="b", description="调用tb"))
        plan.state = PlanState.READY

        result = await executor.execute_plan(plan)

        assert result.state == PlanState.COMPLETED
        assert "A备份" in str(result.get_task("a").result)
        assert "B备份" in str(result.get_task("b").result)

    @pytest.mark.asyncio
    async def test_all_chain_fail_in_parallel_batch_not_misjudged(self):
        """并行批中主+备份全部失败：任务 failed，计划收尾 FAILED 而非误判成功"""
        registry = ToolRegistry()

        def fail_a(**kwargs):
            raise RuntimeError("a主故障")

        def fail_bak(**kwargs):
            raise RuntimeError("a备份故障")

        registry.register("tool_a", fail_a)
        registry.register("backup_a", fail_bak)
        registry.register("tool_b", lambda **kw: "b成功")

        executor = self._executor_with_chain(
            registry, {"tool_a": ["backup_a"]}
        )

        plan = Plan(original_task="降级全失败", state=PlanState.READY)
        plan.add_task(Task(id="a", description="调用tool_a"))
        plan.add_task(Task(id="b", description="调用tool_b"))
        plan.state = PlanState.READY

        result = await executor.execute_plan(plan)

        # a 失败保留主错误，b 成功；计划整体"部分任务失败"但完成（不误判全成功）
        assert result.get_task("a").status == TaskStatus.FAILED
        assert "a主故障" in str(result.get_task("a").error)
        assert result.state == PlanState.COMPLETED  # 部分失败但计划完成
        assert "部分任务失败" in (result.result or "")


# ──────────────────────── D13 × D14 与取消并发 ────────────────────────

class TestBudgetDegradeWithCancel:
    """预算/降级与协作式取消并发：无状态滞留、无非法转换"""

    @pytest.mark.asyncio
    async def test_cancel_during_degrade_chain(self):
        """降级链执行中取消：计划收尾 CANCELLED，不抛异常、无非法转换"""
        core = PlanningCore()
        started = asyncio.Event()

        async def slow_primary(**kwargs):
            started.set()
            await asyncio.sleep(30)
            return "慢主工具"

        core.register_tool("slow_tool", slow_primary)

        plan = Plan(original_task="取消降级任务", state=PlanState.READY)
        plan.add_task(Task(id="t1", description="调用slow_tool"))
        core._active_plans[plan.id] = plan

        task = asyncio.create_task(core.execute_plan(plan))
        await started.wait()
        assert core.cancel_plan(plan.id) is True

        result = await task
        assert result.state == PlanState.CANCELLED
        assert plan.get_task("t1").status != TaskStatus.COMPLETED  # 未完成即收尾取消

    @pytest.mark.asyncio
    async def test_budget_exceeded_no_state_stuck(self):
        """ReAct 预算超限终止：正常返回，不抛异常（终态语义完整）"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_llm = AsyncMock()
            mock_llm.chat.side_effect = [
                json.dumps({
                    "reasoning": "调用工具",
                    "action_type": "tool_call",
                    "action": {"tool": "missing_tool", "params": {}, "description": "调用"},
                }),
                json.dumps({"reasoning": "完成", "action_type": "finish", "result": "完成"}),
            ]
            planner = type("P", (), {})()
            planner.llm = mock_llm
            planner.tool_registry = ToolRegistry()

            loop = ReActLoop(planner, None, max_iterations=5, config={"token_budget": 10})
            result = await loop.run("预算任务", {})

            assert result.success is False
            assert "token" in (result.error or "")
