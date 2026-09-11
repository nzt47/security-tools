"""TASK-S4-04 容器 / 生命周期 接入真实委派 单元测试

覆盖：
- ``SubagentContainer.run_delegation`` 走真执行器（八要素 → task_file → 通道 → 三件套）
- **既有 ``execute()`` 行为不变**（升级走新增路径，不改占位骨架的对外语义）
- ``SubagentLifecycleManager.delegate``：分身旁挂契约⑦超时（TTL ≤ 任务时长）、
  执行后销毁、名称冲突自动消歧
- 已销毁容器上的委派被拒（不抛异常，返回 ``ok=False``）
"""

from __future__ import annotations

import json

import pytest

from agent.subagent.container import (
    ExecutionResult,
    SubagentConfig,
    SubagentContainer,
)
from agent.subagent.delegation import DelegationContext
from agent.subagent.executor import DelegationExecutor
from agent.subagent.lifecycle import SubagentLifecycleManager


class StubChannel:
    def __init__(self, outputs=None):
        self.outputs = list(outputs or [self._success()])
        self.invocations = []

    @staticmethod
    def _success():
        return json.dumps({
            "status": "done",
            "summary": "已完成",
            "artifacts": [{"path": "out/a.jsonl"}],
            "self_eval": {"verdict": "pass", "score": 0.9},
            "tool_calls": [{"name": "read_file"}],
        }, ensure_ascii=False)

    def __call__(self, invocation):
        from agent.subagent.channel import RawOutput

        self.invocations.append(invocation)
        text = self.outputs.pop(0) if self.outputs else self._success()
        return RawOutput(stdout=text, returncode=0)


def make_ctx(**overrides) -> DelegationContext:
    data = {
        "goal": "把 docs/zh 下的 12 篇设计稿抽取为可复现步骤序列",
        "constraints": ["只读仓库"],
        "prior_artifacts": [],
        "prohibitions": [],
        "artifact_format": "JSONL",
        "budget_tokens": 1000,
        "timeout_seconds": 30.0,
        "callback_url": "internal://x",
    }
    data.update(overrides)
    return DelegationContext(**data)


def make_executor(tmp_path) -> DelegationExecutor:
    return DelegationExecutor(channel=StubChannel(), workspace=str(tmp_path / "ws"))


@pytest.fixture()
def config():
    return SubagentConfig(name="code-helper", model_id="stub-model",
                          permissions=["read"])


# ════════════════════════════════════════════════════════════
#  容器
# ════════════════════════════════════════════════════════════


class TestContainerDelegation:
    def test_run_delegation_returns_outcome(self, tmp_path, config):
        container = SubagentContainer(config)
        outcome = container.run_delegation(
            make_ctx(), executor=make_executor(tmp_path), tools=["read_file"],
            authorized_capabilities=["read_file"])
        assert outcome.ok is True
        assert outcome.payload["summary"] == "已完成"

    def test_delegation_recorded_in_container_context(self, tmp_path, config):
        container = SubagentContainer(config)
        ctx = make_ctx()
        container.run_delegation(ctx, executor=make_executor(tmp_path),
                                 tools=["read_file"],
                                 authorized_capabilities=["read_file"])
        entries = [e for e in container.context if e.get("role") == "delegation"]
        assert len(entries) == 1
        assert entries[0]["delegation_id"] == ctx.delegation_id
        assert entries[0]["ok"] is True

    def test_destroyed_container_refuses_delegation(self, tmp_path, config):
        container = SubagentContainer(config)
        container._is_destroyed = True
        outcome = container.run_delegation(make_ctx(), executor=make_executor(tmp_path))
        assert outcome.ok is False
        assert outcome.error_code == "E_SUBAGENT_DESTROYED"

    def test_execute_behaviour_unchanged(self, config):
        """既有占位骨架 ``execute()`` 的对外语义不变（升级只走新增路径）"""
        container = SubagentContainer(config)
        result = container.execute("帮我写一段 Python 代码")
        assert isinstance(result, ExecutionResult)
        assert result.error is None
        assert "code-helper" in result.output
        assert result.trace_id
        assert len(container.context) == 2

    def test_execute_after_delegation_still_works(self, tmp_path, config):
        container = SubagentContainer(config)
        container.run_delegation(make_ctx(), executor=make_executor(tmp_path))
        result = container.execute("后续任务")
        assert result.error is None

    def test_transient_executor_used_when_llm_given(self, tmp_path, config):
        class StubLlm:
            def chat(self, messages, system_prompt=""):
                return json.dumps({
                    "status": "done", "summary": "由内部 LLM 完成",
                    "artifacts": [{"path": "a"}],
                    "self_eval": {"verdict": "pass", "score": 1.0},
                    "tool_calls": [],
                }, ensure_ascii=False)

        container = SubagentContainer(config)
        outcome = container.run_delegation(make_ctx(timeout_seconds=5), llm=StubLlm())
        assert outcome.ok is True
        assert outcome.payload["summary"] == "由内部 LLM 完成"


# ════════════════════════════════════════════════════════════
#  生命周期
# ════════════════════════════════════════════════════════════


class TestLifecycleDelegate:
    def test_delegate_creates_runs_and_destroys(self, tmp_path, config):
        manager = SubagentLifecycleManager()
        outcome = manager.delegate(config, make_ctx(), executor=make_executor(tmp_path),
                                   tools=["read_file"],
                                   authorized_capabilities=["read_file"])
        assert outcome.ok is True
        assert manager.count() == 0            # 默认 destroy_after=True
        assert manager.get_stats()["total_created"] == 1
        assert manager.get_stats()["total_destroyed"] == 1

    def test_delegate_keeps_container_when_asked(self, tmp_path, config):
        manager = SubagentLifecycleManager()
        manager.delegate(config, make_ctx(), executor=make_executor(tmp_path),
                         destroy_after=False)
        assert manager.count() == 1

    def test_container_ttl_taken_from_contract_timeout(self, tmp_path, config):
        """§5.9 对齐：分身存活期 = 契约⑦任务时长，而不是「永久」"""
        captured = {}

        class SpyExecutor:
            def execute(self, ctx, **kwargs):
                captured["ttl"] = config.ttl_seconds
                from agent.subagent.executor import ExecutionOutcome

                return ExecutionOutcome(delegation_id=ctx.delegation_id, ok=True)

        manager = SubagentLifecycleManager()
        manager.delegate(config, make_ctx(timeout_seconds=42.0), executor=SpyExecutor(),
                         destroy_after=False)
        assert config.ttl_seconds == 42
        assert captured["ttl"] == 42

    def test_existing_ttl_not_overwritten(self, tmp_path):
        manager = SubagentLifecycleManager()
        config = SubagentConfig(name="c", model_id="m", ttl_seconds=7)

        class SpyExecutor:
            def execute(self, ctx, **kwargs):
                from agent.subagent.executor import ExecutionOutcome

                return ExecutionOutcome(delegation_id=ctx.delegation_id, ok=True)

        manager.delegate(config, make_ctx(timeout_seconds=999), executor=SpyExecutor(),
                         destroy_after=False)
        assert config.ttl_seconds == 7

    def test_name_conflict_disambiguated(self, tmp_path, config):
        """并行委派复用同一配置模板时不得撞 ``create()`` 的唯一性检查"""
        manager = SubagentLifecycleManager()
        existing = manager.create(config)
        manager.delegate(config, make_ctx(), executor=make_executor(tmp_path),
                         destroy_after=False)
        assert manager.count() == 2
        assert manager.get(existing.config.name) is existing

    def test_container_destroyed_even_when_executor_raises(self, tmp_path, config):
        class BoomExecutor:
            def execute(self, ctx, **kwargs):
                raise RuntimeError("executor blew up")

        manager = SubagentLifecycleManager()
        with pytest.raises(RuntimeError):
            manager.delegate(config, make_ctx(), executor=BoomExecutor())
        assert manager.count() == 0

    def test_full_pipeline_via_lifecycle(self, tmp_path, config):
        """端到端：生命周期委托 → 执行器 → 通道 → Trace → 三件套 → 计费"""
        from agent.observability.trace_v2 import TraceFacade

        facade = TraceFacade(db_path=str(tmp_path / "trace.db"))
        try:
            executor = DelegationExecutor(channel=StubChannel(),
                                          workspace=str(tmp_path / "ws"), trace=facade)
            manager = SubagentLifecycleManager()
            outcome = manager.delegate(config, make_ctx(), executor=executor,
                                       tools=["read_file"],
                                       authorized_capabilities=["read_file"])
            assert outcome.trace["actor"] == "sub_agent"
            assert outcome.triad.is_complete is True
            assert outcome.cost.counted is True
            assert executor.cost_ledger.totals()["counted_delegations"] == 1
        finally:
            facade.flush(timeout=2.0)
            facade._store.stop(timeout=2.0)

    def test_without_orchestration_trace_triad_is_incomplete(self, tmp_path, config):
        """**没有编排 Trace 就没有「轨迹」这一件**——三件套不齐全、不计成本

        这是 §3.9 的诚实语义：拿不到轨迹时不能把「无轨迹」当成「有轨迹」凑数。
        """
        manager = SubagentLifecycleManager()
        executor = make_executor(tmp_path)
        outcome = manager.delegate(config, make_ctx(), executor=executor,
                                   tools=["read_file"],
                                   authorized_capabilities=["read_file"])
        assert outcome.ok is True                     # 委派本身成功
        assert outcome.trace is None
        assert outcome.triad.is_complete is False     # 但回收三件套不齐全
        assert outcome.cost.counted is False
        assert outcome.cost.wasted is True
        assert executor.cost_ledger.totals()["counted_delegations"] == 0
