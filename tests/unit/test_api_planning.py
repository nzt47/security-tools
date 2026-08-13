"""API 层重规划触发条件专项测试

对应 API 链路：app_server → DigitalLife（Orchestrator + LifecycleManager 混入）→ planning 引擎。
覆盖重规划触发路径上的三个环节：

1. 规划模式前置判定（TaskDispatcher._needs_planning）：复杂关键词/动作关键词命中
   → 进入规划路径；简单消息 / 规划禁用 / planner 缺失 → 不进入（重规划无从谈起）。
2. 重规划配置下发（LifecycleManager 初始化语义）：planning 段（replan_on_failure /
   ask_user_timeout_seconds / budget）传给 PlanningCore → executor/ReActLoop 生效，
   高优先级失败时重规划才可用。
3. 重规划触发决策（executor._replan_on_failure）：高优先级失败 + refine 有调整空间
   → 触发并继续；无调整空间 / 低优先级 → 不触发（中断路径 / 仅标记失败）。

运行：python -m pytest tests/unit/test_api_planning.py -q
"""

import asyncio
import json
import tempfile
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent.orchestrator.task_dispatcher import TaskDispatcher
from planning.core import PlanningCore
from planning.decomposer import TaskDecomposer
from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task, TaskStatus
from agent.error_handler import RecoverableError


class _MockLLM:
    """构造用 mock LLM：任何调用返回空 JSON（不触发 decompose/refine 路径）"""

    async def chat(self, messages):
        return "{}"


# ─────────────────────── 环节 1：规划模式前置判定 ───────────────────────

class TestPlanningModeGate:
    """API 层是否进入规划（重规划前提）由 _needs_planning 判定"""

    @pytest.fixture
    def dispatcher(self):
        """轻量 stub 绑定 TaskDispatcher._needs_planning（模拟 DigitalLife 混入）"""
        stub = types.SimpleNamespace(_planner=object(), _planning_enabled=True)
        stub._needs_planning = types.MethodType(TaskDispatcher._needs_planning, stub)
        return stub

    def test_complex_task_triggers_planning(self, dispatcher):
        """复杂关键词命中（帮我完成/流程）→ 进入规划模式"""
        assert dispatcher._needs_planning("帮我完成一个系统流程并生成报告") is True

    def test_action_keywords_triggers_planning(self, dispatcher):
        """动作关键词 ≥2（分析+生成）→ 进入规划模式"""
        assert dispatcher._needs_planning("帮我分析一下再生成报告") is True

    def test_simple_chat_skips_planning(self, dispatcher):
        """简单聊天不进入规划模式"""
        assert dispatcher._needs_planning("你好") is False

    def test_planning_disabled_or_planner_missing(self):
        """规划禁用 / planner 缺失时即使复杂消息也不进入规划"""
        cases = [
            dict(_planner=object(), _planning_enabled=False),
            dict(_planner=None, _planning_enabled=True),
            dict(_planner=None, _planning_enabled=False),
        ]
        for attrs in cases:
            stub = types.SimpleNamespace(**attrs)
            stub._needs_planning = types.MethodType(TaskDispatcher._needs_planning, stub)
            assert stub._needs_planning("帮我完成一个系统流程") is False


# ─────────────────────── 环节 2：重规划配置下发 ───────────────────────

class TestReplanConfigPropagation:
    """planning 段经 API 层初始化下发（LifecycleManager 传 config 给 PlanningCore）"""

    def test_planning_cfg_propagates_to_executor(self):
        with tempfile.TemporaryDirectory() as tmp:
            planning_cfg = {
                "enabled": True,
                "replan_on_failure": True,
                "ask_user_timeout_seconds": 300,
                "budget": {
                    "enabled": True, "max_seconds": 30, "token_price_per_1k": 0.002,
                },
                "persist_dir": tmp,
                "persist_db": str(Path(tmp) / "plans.db"),
            }
            core = PlanningCore(llm_service=_MockLLM(), config=planning_cfg)
            assert core.executor.replan_on_failure is True, "replan_on_failure 未下发"
            assert core.ask_user_timeout == 300, "ask_user_timeout 未下发"
            ex_b = core.executor.budget_manager.budget
            assert ex_b.enabled is True and ex_b.max_seconds == 30, "executor 预算未下发"
            rx_b = core.react_loop.budget_manager.budget
            assert rx_b.enabled is True and rx_b.max_seconds == 30, "react 预算未下发"


# ─────────────────────── 环节 3：重规划触发决策 ───────────────────────

class TestReplanTriggerDecision:
    """executor 重规划触发条件：优先级 + refine 调整空间"""

    def _make_executor(self, adjustments_json: str, task_priority: int):
        registry = ToolRegistry()

        def failing(**kwargs):
            raise RecoverableError(f"工具执行失败: {kwargs}")

        def ok_tool(**kwargs):
            return "收尾成功"

        registry.register("primary_tool", failing)
        registry.register("backup_tool", failing)
        registry.register("ok_tool", ok_tool)

        llm = AsyncMock()
        llm.chat.side_effect = [adjustments_json]
        executor = PlanExecutor(
            registry,
            max_retries=1,
            config={
                "degrade_chain": {"primary_tool": ["backup_tool"]},
                "replan_on_failure": True,
            },
            decomposer=TaskDecomposer(llm_service=llm),
            reflector=AsyncMock(),
        )
        plan = Plan(original_task="重规划触发条件模拟", state=PlanState.READY)
        task_a = Task(
            id="a", description="调用 primary_tool 执行主步骤",
            priority=task_priority, fallback_actions=["backup_tool"],
        )
        plan.add_task(task_a)
        plan.add_task(Task(id="b", description="调用 ok_tool 完成收尾", priority=1))
        return executor, plan, task_a, llm

    def test_high_priority_with_adjustment_replans(self):
        """高优先级(5) + refine 有调整空间 → 触发重规划并继续执行"""
        adj = json.dumps({"adjustments": [{"task_id": "a", "action": "remove"}]})
        executor, plan, task_a, _ = self._make_executor(adj, 5)
        asyncio.run(executor.execute_plan(plan))

        assert task_a.status == TaskStatus.FAILED
        assert "replanned" in plan.metadata, f"应触发重规划: {plan.metadata}"
        assert plan.metadata["replanned"]["failed_task"] == "a"
        assert plan.get_task("b").status == TaskStatus.COMPLETED, "重规划后计划应继续执行"
        assert plan.state == PlanState.COMPLETED

    def test_no_adjustment_space_breaks(self):
        """高优先级(5) + refine 无调整空间 → 不触发，走中断路径"""
        adj = json.dumps({"adjustments": [], "reasoning": "无调整空间"})
        executor, plan, task_a, _ = self._make_executor(adj, 5)
        asyncio.run(executor.execute_plan(plan))

        assert "replanned" not in plan.metadata, f"无调整空间不应触发重规划: {plan.metadata}"
        assert plan.get_task("b").status == TaskStatus.PENDING, "中断路径后续任务不执行"
        assert plan.state == PlanState.FAILED

    def test_low_priority_never_replans(self):
        """低优先级(3 < 4) → 即使有调整空间也不触发重规划"""
        adj = json.dumps({"adjustments": [{"task_id": "a", "action": "remove"}]})
        executor, plan, task_a, llm = self._make_executor(adj, 3)
        asyncio.run(executor.execute_plan(plan))

        assert llm.chat.call_count == 0, "低优先级不应调用 refine"
        assert "replanned" not in plan.metadata
        assert plan.get_task("b").status == TaskStatus.COMPLETED, "低优先级失败不中断后续任务"
