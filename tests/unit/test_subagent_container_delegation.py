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


# ════════════════════════════════════════════════════════════
#  批量委派（delegate 的批量版：建分身 → execute_many → 统一回收）
# ════════════════════════════════════════════════════════════


def make_batch_executor(tmp_path, n: int) -> DelegationExecutor:
    """批量用执行器：桩通道预置 n 条成功输出（避免多线程争抢同一个输出列表）"""
    channel = StubChannel([StubChannel._success() for _ in range(max(1, n))])
    return DelegationExecutor(channel=channel, workspace=str(tmp_path / "ws"))


def batch_specs(n: int, **ctx_overrides):
    return [
        (SubagentConfig(name=f"batch-{i}", model_id="stub-model"), 
         make_ctx(delegation_id=f"dlg-{i}", **ctx_overrides))
        for i in range(n)
    ]


class TestDelegateMany:
    def test_批量建分身执行回收_结果等长同序(self, tmp_path):
        manager = SubagentLifecycleManager()
        specs = batch_specs(3)
        outcomes = manager.delegate_many(
            specs, executor=make_batch_executor(tmp_path, 3), max_concurrency=2,
            tools=["read_file"], authorized_capabilities=["read_file"])

        assert [o.delegation_id for o in outcomes] == ["dlg-0", "dlg-1", "dlg-2"]
        assert all(o.ok for o in outcomes)
        assert manager.count() == 0                      # 默认 destroy_after=True
        assert manager.get_stats()["total_created"] == 3
        assert manager.get_stats()["total_destroyed"] == 3

    def test_同一模板复用_各任务TTL取自己的契约且模板不被改写(self, tmp_path, config):
        """批量**复制**模板后逐任务适配：TTL 不互相覆盖，调用方模板保持原样"""
        manager = SubagentLifecycleManager()
        specs = [(config, make_ctx(delegation_id="d0", timeout_seconds=5.0)),
                 (config, make_ctx(delegation_id="d1", timeout_seconds=9.0))]
        outcomes = manager.delegate_many(
            specs, executor=make_batch_executor(tmp_path, 2), destroy_after=False)

        assert len(outcomes) == 2
        names = {c.config.name for c in manager.list()}
        assert names == {"code-helper", "code-helper-d1"}, "同名复制模板未自动消歧"
        ttl_by_name = {c.config.name: c.config.ttl_seconds for c in manager.list()}
        assert ttl_by_name["code-helper"] == 5
        assert ttl_by_name["code-helper-d1"] == 9, "各任务 TTL 未取自己的契约⑦"
        assert config.ttl_seconds == 0, "批量不得就地改写调用方传入的配置模板"

    def test_容量上限_只让建不出分身的那条失败(self, tmp_path):
        """``max_subagents`` 是硬上限：超限任务就地失败（``E_SUBAGENT_UNAVAILABLE``），
        其余照跑——这是批量与单发的差别（单发 ``delegate`` 是失败即抛）。"""
        from agent.subagent.lifecycle import SUB_REASON_SUBAGENT_UNAVAILABLE

        manager = SubagentLifecycleManager(max_subagents=1)
        outcomes = manager.delegate_many(
            batch_specs(2), executor=make_batch_executor(tmp_path, 1), max_concurrency=2,
            tools=["read_file"], authorized_capabilities=["read_file"])

        assert len(outcomes) == 2
        assert [o.ok for o in outcomes] == [True, False]
        assert outcomes[1].error_code == "E_SUBAGENT_UNAVAILABLE"
        assert outcomes[1].sub_reason == SUB_REASON_SUBAGENT_UNAVAILABLE
        assert "上限" in outcomes[1].error
        assert manager.count() == 0, "已建的分身必须照常回收"

    def test_逐任务工具集经工厂下传(self, tmp_path):
        """``tools_for`` / ``authorized_for`` 让一批任务各持自己的授权集

        桩通道声称调用了 ``read_file``：授权含它的那条通过，只授权 ``grep`` 的那条
        被工具裁剪闸门判失败（``E_TOOL_NOT_AUTHORIZED``）——若工厂未逐任务生效，
        整批单值会让两条都放行。
        """
        from agent.subagent.toolset import E_TOOL_NOT_AUTHORIZED

        manager = SubagentLifecycleManager()
        granted = {"dlg-0": ("read_file",), "dlg-1": ("grep",)}

        def _for(ctx):
            return granted[ctx.delegation_id]

        outcomes = manager.delegate_many(
            batch_specs(2), executor=make_batch_executor(tmp_path, 2),
            max_concurrency=2, tools=("read_file", "grep"),
            tools_for=_for, authorized_for=_for)

        by_id = {o.delegation_id: o for o in outcomes}
        assert set(by_id["dlg-0"].toolset["tools"]) == {"read_file"}
        assert by_id["dlg-0"].ok is True
        assert set(by_id["dlg-1"].toolset["tools"]) == {"grep"}
        assert by_id["dlg-1"].ok is False
        assert by_id["dlg-1"].error_code == E_TOOL_NOT_AUTHORIZED

    def test_空批次返回空列表(self, tmp_path):
        manager = SubagentLifecycleManager()
        assert manager.delegate_many([], executor=make_batch_executor(tmp_path, 1)) == []

    def test_未配置执行通道时不假装成功且不留孤儿(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CP_SUBAGENT_AGENT_CLI", raising=False)
        manager = SubagentLifecycleManager()
        outcomes = manager.delegate_many(batch_specs(1))

        assert len(outcomes) == 1
        assert outcomes[0].ok is False          # 通道未配置 ⇒ 显式失败
        assert manager.count() == 0, "失败路径也把分身回收掉"
