"""TASK-S5-01 遗忘与 TTL 单测（`agent/memory/forgetting.py`，对齐 v7.2 §4.3 / §8 / §10）

覆盖：遗忘三触发各自的触发用例 / **先快照后删除** / 快照保留 30 天与清理 / TTL 到期降级为
候选 / 被遗忘权（记忆物理删除 + 审计标识符匿名化 + 审计链保留可验签）/ 墓碑快照 /
既有 full 快照的主体墓碑化 / 默认 dry-run（破坏性动作须显式开启）。
"""

import json
import os

import pytest

from memory_layer_testkit import (  # noqa: F401  (autouse 夹具)
    BrokenRegistry,
    FakeClock,
    FakeDescriptor,
    FakeRegistry,
    FakeTrace,
    FakeTraceStore,
    bound_audit_chain,
    make_engine,
    make_store,
    memory_runtime,
    workspace_hash,
)

from agent.memory.forgetting import (
    DEFAULT_SNAPSHOT_ROOT,
    FORGET_MIN_SAMPLES,
    FORGET_SUCCESS_RATE_RATIO,
    FORGET_WINDOW_DAYS,
    SNAPSHOT_RETENTION_DAYS,
    ForgetTrigger,
    ForgettingEngine,
    MemorySnapshotStore,
    SourceValidityChecker,
    TraceQualitySource,
)
from agent.memory.identity import ERASED_MARKER, subject_ref
from agent.memory.tenancy import WriteChannel

ROOT_A = "C:/repos/alpha"
ROOT_B = "C:/repos/beta"
CAP = "cp.fs.write"
DAY = 86400.0


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def store(tmp_path, clock):
    return make_store(tmp_path, clock=clock)


async def _seeded(store, *, content="关键字 alpha 的事实", **kwargs):
    kwargs.setdefault("memory_type", "fact")
    kwargs.setdefault("workspace_root", ROOT_A)
    return await store.write(content, **kwargs)


# ════════════════════════════════════════════════════════════
#  1. TTL 到期自动降级为遗忘候选（§4.3）
# ════════════════════════════════════════════════════════════


class TestTTLTrigger:
    async def test_expired_entry_becomes_candidate(self, tmp_path, clock, store):
        await _seeded(store, memory_type="working", ttl_seconds=60)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        clock.advance(61)
        candidates = await engine.apply_ttl()
        assert [c.trigger for c in candidates] == [ForgetTrigger.TTL_EXPIRED]
        assert candidates[0].reason == "ttl_expired"
        assert candidates[0].evidence["layer"] == "working"
        assert candidates[0].evidence["expired_seconds"] == pytest.approx(1.0)

    async def test_fresh_entry_is_not_a_candidate(self, tmp_path, clock, store):
        await _seeded(store, memory_type="working", ttl_seconds=600)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        clock.advance(10)
        assert await engine.apply_ttl() == []

    async def test_never_expiring_entry_is_not_a_candidate(self, tmp_path, clock, store):
        await _seeded(store, ttl_seconds=0)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        clock.advance(10 ** 9)
        assert await engine.apply_ttl() == []

    async def test_ttl_marking_is_persisted(self, tmp_path, clock, store):
        result = await _seeded(store, memory_type="working", ttl_seconds=60)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        clock.advance(61)
        await engine.apply_ttl()
        reopened = make_store(tmp_path, clock=clock)
        reloaded = await reopened.get(result.entry.id, workspace_root=ROOT_A,
                                      include_expired=True)
        assert reloaded.forget_candidate is True
        assert reloaded.forget_reason == "ttl_expired"

    async def test_ttl_marking_is_idempotent(self, tmp_path, clock, store):
        await _seeded(store, memory_type="working", ttl_seconds=60)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        clock.advance(61)
        first = await engine.apply_ttl()
        second = await engine.apply_ttl()
        assert len(first) == len(second) == 1

    async def test_expired_entry_still_recallable_when_requested(self, tmp_path, clock, store):
        await _seeded(store, memory_type="working", ttl_seconds=60)
        clock.advance(61)
        assert await store.recall("alpha", workspace_root=ROOT_A) == []
        assert len(await store.recall(
            "alpha", workspace_root=ROOT_A, include_expired=True)) == 1


# ════════════════════════════════════════════════════════════
#  2. 触发①：成功率 30 天 < 基线 × 0.7
# ════════════════════════════════════════════════════════════


class TestSuccessRateTrigger:
    def _traces(self, successes, failures, capability=CAP):
        return [FakeTrace(capability, "success") for _ in range(successes)] + [
            FakeTrace(capability, "error") for _ in range(failures)]

    async def test_below_baseline_ratio_triggers(self, tmp_path, clock, store):
        traces = self._traces(10, 15)  # 观测 0.40 < 0.9 × 0.7 = 0.63
        engine, _ = make_engine(
            tmp_path, store=store, clock=clock, traces=traces,
            registry=FakeRegistry({CAP: FakeDescriptor(success_rate=0.9, sample_count=100)}))
        await _seeded(store, source_capability_id=CAP)
        candidates = await engine.scan(apply_ttl=False)
        assert [c.trigger for c in candidates] == [ForgetTrigger.SUCCESS_RATE]
        evidence = candidates[0].evidence
        assert evidence["observed_rate"] == pytest.approx(0.4)
        assert evidence["baseline"] == pytest.approx(0.9)
        assert evidence["threshold"] == pytest.approx(0.9 * FORGET_SUCCESS_RATE_RATIO)
        assert evidence["samples"] == 25

    async def test_above_baseline_ratio_does_not_trigger(self, tmp_path, clock, store):
        engine, _ = make_engine(
            tmp_path, store=store, clock=clock, traces=self._traces(24, 1),
            registry=FakeRegistry({CAP: FakeDescriptor(success_rate=0.9, sample_count=100)}))
        await _seeded(store, source_capability_id=CAP)
        assert await engine.scan(apply_ttl=False) == []

    async def test_exactly_at_threshold_does_not_trigger(self, tmp_path, clock, store):
        """边界：观测 == 基线×0.7 不触发（严格小于才触发）"""
        engine, _ = make_engine(
            tmp_path, store=store, clock=clock, traces=self._traces(7, 3),
            registry=FakeRegistry({CAP: FakeDescriptor(success_rate=1.0, sample_count=100)}))
        await _seeded(store, source_capability_id=CAP)
        # 观测 0.7，阈值 1.0×0.7 = 0.7 → 相等，不触发
        assert await engine.scan(apply_ttl=False) == []

    async def test_insufficient_samples_does_not_trigger(self, tmp_path, clock, store):
        """口径纪律：样本数未达门槛（默认 20）不判失败，避免小样本误杀"""
        engine, _ = make_engine(
            tmp_path, store=store, clock=clock, traces=self._traces(0, 5),
            registry=FakeRegistry({CAP: FakeDescriptor(success_rate=1.0, sample_count=100)}))
        await _seeded(store, source_capability_id=CAP)
        assert await engine.scan(apply_ttl=False) == []
        assert FORGET_MIN_SAMPLES == 20

    async def test_no_quality_baseline_does_not_trigger(self, tmp_path, clock, store):
        engine, _ = make_engine(
            tmp_path, store=store, clock=clock, traces=self._traces(0, 30),
            registry=FakeRegistry({CAP: FakeDescriptor(success_rate=0.0, sample_count=0)}))
        await _seeded(store, source_capability_id=CAP)
        assert await engine.scan(apply_ttl=False) == []

    async def test_entry_without_capability_is_skipped(self, tmp_path, clock, store):
        engine, _ = make_engine(
            tmp_path, store=store, clock=clock, traces=self._traces(0, 30),
            registry=FakeRegistry({CAP: FakeDescriptor(success_rate=0.9, sample_count=100)}))
        await _seeded(store)  # 无 source_capability_id
        assert await engine.scan(apply_ttl=False) == []

    async def test_explicit_baseline_provider_wins(self, tmp_path, clock, store):
        engine, _ = make_engine(
            tmp_path, store=store, clock=clock, traces=self._traces(10, 10),
            registry=FakeRegistry(), baseline_provider=lambda cid: 1.0)
        await _seeded(store, source_capability_id=CAP)
        assert len(await engine.scan(apply_ttl=False)) == 1

    async def test_ratio_is_configurable(self, tmp_path, clock, store):
        """阈值可调：放宽到 0.3 后同样的观测不再触发"""
        engine, _ = make_engine(
            tmp_path, store=store, clock=clock, traces=self._traces(10, 15),
            registry=FakeRegistry({CAP: FakeDescriptor(success_rate=0.9, sample_count=100)}),
            success_ratio=0.3)
        await _seeded(store, source_capability_id=CAP)
        assert await engine.scan(apply_ttl=False) == []

    async def test_window_days_passed_to_quality_source(self, tmp_path, clock, store):
        engine, fake = make_engine(
            tmp_path, store=store, clock=clock, traces=self._traces(1, 1),
            registry=FakeRegistry({CAP: FakeDescriptor(success_rate=0.9, sample_count=100)}))
        await _seeded(store, source_capability_id=CAP)
        await engine.scan(apply_ttl=False)
        assert fake.queries[0]["since"] == pytest.approx(clock() - FORGET_WINDOW_DAYS * DAY)

    async def test_defaults_match_section_4_3(self):
        assert FORGET_SUCCESS_RATE_RATIO == 0.7
        assert FORGET_WINDOW_DAYS == 30

    async def test_ledger_derived_proxy_is_not_used_as_baseline(
        self, tmp_path, clock, store, monkeypatch
    ):
        """**刻意不接线**：`digestion.gate.baseline_from_ledger` 不得被当作遗忘基线

        两条代码级理由（本用例把它们固化为回归护栏，防止后续会话"顺手接上"）：

        1. **它不是基线**：`baseline_from_ledger(capability_id, limit=500)` 取的是该能力
           *最近 500 条轨迹* 的成功率（`baseline_from_traces`，无时间窗，`source="s2-01_ledger"`）
           —— 与触发①的观测值（最近 30 天）是**同类量的不同窗口**。拿它当基线等于
           "近 30 天 vs 最近 500 次调用"：稳定流量下两窗几乎重合 ⇒ 比值恒 ≈1 ⇒ 触发①
           **静默失效**；流量波动时又随调用量漂移 ⇒ 同一劣化得到不同裁决。而 §4.3 的
           "基线"在本项目有确定含义：S5-02 冻结的 L2 Core-50 基线，指针为
           `ToolDescriptor.quality.regression_baseline_id`（P7.2-24）。
        2. **它没有租户作用域**：内部 `store.query(capability_id=..., limit=500)` 不带租户过滤，
           接上即**重新引入**修复 #9 刚消除的跨租户串扰（租户 A 的流量决定租户 B 的基线）。

        当前口径：基线只取 `descriptor.quality`（已记录、已认证的量）；**无基线 ⇒ 不触发**
        （失败安全）。未来正确接线是 `regression_baseline_id` → S5-02 冻结基线产物，
        经由 `baseline_provider` 注入缝，而不是台账代理量。
        """
        from agent.digestion import gate as gate_mod

        calls = []

        def _sentinel(capability_id, **kwargs):
            calls.append(capability_id)
            return {"success_rate": 0.99, "p99": 1.0, "sample_count": 999,
                    "source": "s2-01_ledger"}

        monkeypatch.setattr(gate_mod, "baseline_from_ledger", _sentinel, raising=False)

        engine, _ = make_engine(tmp_path, store=store, clock=clock,
                                traces=self._traces(0, 30),
                                registry=self._healthy_without_quality())
        await _seeded(store, source_capability_id=CAP)
        # 来源健康（不触发②）但 quality.sample_count=0 ⇒ 无基线 ⇒ 不触发①；
        # 且全程未咨询台账代理量
        assert engine.baseline_for(CAP) is None
        assert await engine.scan(apply_ttl=False) == []
        assert calls == []

    @staticmethod
    def _healthy_without_quality():
        """来源存在且未 deprecated，但无 quality 样本（`sample_count=0`）"""
        return FakeRegistry({CAP: FakeDescriptor(stage="native", success_rate=0.0,
                                                 sample_count=0)})


# ════════════════════════════════════════════════════════════
#  3. 触发②：来源失效（deprecated / 来源摘除）
# ════════════════════════════════════════════════════════════


class TestSourceInvalidatedTrigger:
    async def test_deprecated_source_triggers(self, tmp_path, clock, store):
        engine, _ = make_engine(
            tmp_path, store=store, clock=clock,
            registry=FakeRegistry({CAP: FakeDescriptor(stage="deprecated")}))
        await _seeded(store, source_capability_id=CAP)
        candidates = await engine.scan(apply_ttl=False)
        assert [c.trigger for c in candidates] == [ForgetTrigger.SOURCE_INVALIDATED]
        assert candidates[0].reason == "source_deprecated"
        assert candidates[0].evidence["deprecated"] is True

    async def test_removed_source_triggers(self, tmp_path, clock, store):
        engine, _ = make_engine(tmp_path, store=store, clock=clock,
                                registry=FakeRegistry({}))
        await _seeded(store, source_capability_id=CAP)
        candidates = await engine.scan(apply_ttl=False)
        assert candidates[0].reason == "source_removed"
        assert candidates[0].evidence["exists"] is False

    async def test_healthy_source_does_not_trigger(self, tmp_path, clock, store):
        engine, _ = make_engine(
            tmp_path, store=store, clock=clock,
            registry=FakeRegistry({CAP: FakeDescriptor(stage="internalized")}))
        await _seeded(store, source_capability_id=CAP)
        assert await engine.scan(apply_ttl=False) == []

    async def test_alias_merge_is_not_treated_as_removal(self, tmp_path, clock, store):
        """S1-01 三路投票去重会把别名归并到 canonical，改名不等于摘除"""
        registry = FakeRegistry(
            {CAP + ".v2": FakeDescriptor(stage="native")}, aliases={CAP: CAP + ".v2"})
        engine, _ = make_engine(tmp_path, store=store, clock=clock, registry=registry)
        await _seeded(store, source_capability_id=CAP)
        assert await engine.scan(apply_ttl=False) == []

    async def test_alias_to_deprecated_canonical_triggers(self, tmp_path, clock, store):
        registry = FakeRegistry(
            {CAP + ".v2": FakeDescriptor(stage="deprecated")}, aliases={CAP: CAP + ".v2"})
        engine, _ = make_engine(tmp_path, store=store, clock=clock, registry=registry)
        await _seeded(store, source_capability_id=CAP)
        candidates = await engine.scan(apply_ttl=False)
        assert candidates[0].reason == "source_deprecated"
        assert candidates[0].evidence["aliased_to"] == CAP + ".v2"

    async def test_broken_registry_does_not_mark_invalid(self, tmp_path, clock, store):
        """数据源不可用时**不判定失效**，避免把记忆误删"""
        engine, _ = make_engine(tmp_path, store=store, clock=clock,
                                registry=BrokenRegistry())
        await _seeded(store, source_capability_id=CAP)
        assert await engine.scan(apply_ttl=False) == []

    async def test_checker_without_capability_id(self):
        checker = SourceValidityChecker(registry=FakeRegistry({}))
        assert checker.check("").invalid is False


# ════════════════════════════════════════════════════════════
#  4. 扫描汇总与触发过滤
# ════════════════════════════════════════════════════════════


class TestScan:
    async def test_scan_collects_all_triggers(self, tmp_path, clock, store):
        traces = [FakeTrace(CAP, "error") for _ in range(30)]
        engine, _ = make_engine(
            tmp_path, store=store, clock=clock, traces=traces,
            registry=FakeRegistry({CAP: FakeDescriptor(success_rate=0.9, sample_count=100)}))
        await _seeded(store, memory_type="working", ttl_seconds=60, content="过期工作记忆")
        await _seeded(store, source_capability_id=CAP, content="关键字 beta 的事实")
        clock.advance(61)
        triggers = {c.trigger for c in await engine.scan()}
        assert triggers == {ForgetTrigger.TTL_EXPIRED, ForgetTrigger.SUCCESS_RATE}

    async def test_scan_can_be_restricted_to_one_trigger(self, tmp_path, clock, store):
        traces = [FakeTrace(CAP, "error") for _ in range(30)]
        engine, _ = make_engine(
            tmp_path, store=store, clock=clock, traces=traces,
            registry=FakeRegistry({CAP: FakeDescriptor(success_rate=0.9, sample_count=100)}))
        await _seeded(store, memory_type="working", ttl_seconds=60, content="过期工作记忆")
        await _seeded(store, source_capability_id=CAP, content="关键字 beta 的事实")
        clock.advance(61)
        candidates = await engine.scan(triggers=[ForgetTrigger.SUCCESS_RATE])
        assert {c.trigger for c in candidates} == {ForgetTrigger.SUCCESS_RATE}

    async def test_scan_applies_ttl_marker_even_when_not_executing(self, tmp_path, clock, store):
        result = await _seeded(store, memory_type="working", ttl_seconds=60)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        clock.advance(61)
        await engine.scan()
        reopened = make_store(tmp_path, clock=clock)
        reloaded = await reopened.get(result.entry.id, workspace_root=ROOT_A,
                                      include_expired=True)
        assert reloaded.forget_candidate is True

    async def test_scan_of_empty_store_is_empty(self, tmp_path, clock, store):
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        assert await engine.scan() == []


# ════════════════════════════════════════════════════════════
#  4b. 触发①的**租户作用域**（修复 #9：破坏性决策不得跨租户串扰）
# ════════════════════════════════════════════════════════════


class TestSuccessRateTenantScoping:
    """租户 A 的劣化**不得**触发租户 B 的记忆被遗忘（P7.2-08 延伸到治理决策）"""

    def _traces(self, successes, failures, tenant_id):
        return (
            [FakeTrace(CAP, "success", tenant_id=tenant_id) for _ in range(successes)]
            + [FakeTrace(CAP, "error", tenant_id=tenant_id) for _ in range(failures)]
        )

    def _registry(self):
        return FakeRegistry({CAP: FakeDescriptor(success_rate=0.9, sample_count=100)})

    async def test_other_tenant_failures_do_not_trigger_this_tenant(self, tmp_path, clock):
        """租户 A 全失败、租户 B 全成功 ⇒ B 的记忆不得被判定劣化"""
        store = make_store(tmp_path, clock=clock)
        traces = (self._traces(0, 30, workspace_hash(ROOT_A))
                  + self._traces(30, 0, workspace_hash(ROOT_B)))
        engine, _ = make_engine(tmp_path, store=store, clock=clock, traces=traces,
                                registry=self._registry())
        await store.write("B 租户的事实", memory_type="fact", workspace_root=ROOT_B,
                          source_capability_id=CAP)
        assert await engine.scan(apply_ttl=False) == []

    async def test_own_tenant_failures_do_trigger(self, tmp_path, clock):
        """同一组轨迹下换成租户 A 的条目 ⇒ 正常触发（证明上一条不是"功能失效"）"""
        store = make_store(tmp_path, clock=clock)
        traces = (self._traces(0, 30, workspace_hash(ROOT_A))
                  + self._traces(30, 0, workspace_hash(ROOT_B)))
        engine, _ = make_engine(tmp_path, store=store, clock=clock, traces=traces,
                                registry=self._registry())
        await store.write("A 租户的事实", memory_type="fact", workspace_root=ROOT_A,
                          source_capability_id=CAP)
        candidates = await engine.scan(apply_ttl=False)
        assert [c.trigger for c in candidates] == [ForgetTrigger.SUCCESS_RATE]
        assert candidates[0].evidence["sample_scope"] == workspace_hash(ROOT_A)
        assert candidates[0].evidence["samples"] == 30

    async def test_adapter_scopes_sample_and_reports_unattributed(self, tmp_path, clock):
        """适配器侧：作用域过滤生效，且无法归属租户的轨迹被排除并计数"""
        from memory_layer_testkit import FakeTraceStore
        from agent.memory.forgetting import TraceQualitySource

        ten_a = workspace_hash(ROOT_A)
        fake = FakeTraceStore(
            self._traces(1, 0, ten_a)
            + self._traces(0, 5, ten_a)
            + self._traces(5, 0, workspace_hash(ROOT_B))
            + self._traces(5, 0, "")  # 不可归属（S2-01 缺 workspace 的降级记录）
        )
        source = TraceQualitySource(trace_store=fake)
        sample = source.success_rate(CAP, now=1_000_000.0, tenant_id=ten_a)
        assert sample.scope == ten_a
        assert (sample.samples, sample.successes) == (6, 1)
        assert sample.unattributed == 5

    async def test_unscoped_sample_includes_all_tenants(self, tmp_path, clock):
        """不给作用域 ⇒ org 级聚合（org 级策略记忆的判定口径）"""
        from memory_layer_testkit import FakeTraceStore
        from agent.memory.forgetting import TraceQualitySource

        fake = FakeTraceStore(
            self._traces(1, 1, workspace_hash(ROOT_A))
            + self._traces(2, 0, workspace_hash(ROOT_B))
        )
        sample = TraceQualitySource(trace_store=fake).success_rate(CAP)
        assert sample.scope == ""
        assert sample.samples == 4

    async def test_org_level_strategy_uses_org_wide_sample(self, tmp_path, clock):
        """org 级策略记忆 ⇒ 不对应任何工作区轨迹，用 org 级聚合样本判定"""
        store = make_store(tmp_path, clock=clock)
        traces = self._traces(0, 30, workspace_hash(ROOT_A))
        engine, _ = make_engine(tmp_path, store=store, clock=clock, traces=traces,
                                registry=self._registry())
        result = await store.write("企业规避规则", memory_type="strategy",
                                   workspace_root=ROOT_A, channel=WriteChannel.ORG,
                                   source_capability_id=CAP)
        assert result.entry.is_org_level is True
        candidates = await engine.scan(apply_ttl=False)
        assert [c.trigger for c in candidates] == [ForgetTrigger.SUCCESS_RATE]
        assert candidates[0].evidence["sample_scope"] == ""

    async def test_preference_layer_scope_helper(self):
        """sample_scope_for：租户隔离层取本租户；org 级取空；无租户取空"""
        from agent.memory.tenancy import sample_scope_for
        from memory_layer_testkit import make_entry

        assert sample_scope_for(make_entry(type="fact", tenant_id="ws_a")) == "ws_a"
        assert sample_scope_for(make_entry(type="working", tenant_id="ws_a")) == "ws_a"
        assert sample_scope_for(
            make_entry(type="strategy", tenant_id="__org__", org_level=True)) == ""
        assert sample_scope_for(make_entry(type="fact", tenant_id="")) == ""
        assert sample_scope_for(None) == ""

    async def test_fake_trace_defaults_to_root_a_tenant(self):
        """替身默认归属 ROOT_A：保证既有触发①用例仍打在"本租户"样本上"""
        assert FakeTrace(CAP).tenancy.tenant_id == workspace_hash(ROOT_A)
        assert workspace_hash(ROOT_A) != workspace_hash(ROOT_B)

    async def test_evaluate_success_rate_without_precomputed_sample(
        self, tmp_path, clock
    ):
        """无预置样本路径（`sample=None`）也必须走通并自带租户作用域

        Why: `scan()` 总是预取样本并传入 `sample=`，故该分支不会被扫描路径覆盖；
        曾因此在改名重构后留下 `NameError` 隐患（由 mypy 拦下，见验收报告 #11）。
        """
        store = make_store(tmp_path, clock=clock)
        traces = (self._traces(30, 0, workspace_hash(ROOT_B))
                  + self._traces(0, 30, workspace_hash(ROOT_A)))
        engine, fake = make_engine(tmp_path, store=store, clock=clock, traces=traces,
                                   registry=self._registry())
        result = await store.write("A 租户的事实", memory_type="fact",
                                   workspace_root=ROOT_A, source_capability_id=CAP)
        candidate = engine.evaluate_success_rate(result.entry, now=clock())
        assert candidate is not None
        assert candidate.evidence["sample_scope"] == workspace_hash(ROOT_A)
        assert candidate.evidence["samples"] == 30
        assert fake.queries, "必须实际查询数据源（而非静默返回空样本）"


# ════════════════════════════════════════════════════════════
#  5. 先快照、后删除
# ════════════════════════════════════════════════════════════


class TestForgetSnapshotFirst:
    async def test_snapshot_is_written_before_deletion(self, tmp_path, clock, store):
        result = await _seeded(store, content="关键字 gamma 的事实")
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        candidates = await engine.scan(apply_ttl=False)
        clock.advance(120)
        await store.mark_forget_candidate(result.entry, "manual")
        batch = await engine.forget(
            [c for c in candidates] or [], trigger="manual", reason="unit") if candidates else None
        assert batch is None or batch.snapshot_id == ""  # 无候选 → 无快照

        batch = await engine.forget(
            [_candidate(result.entry)], trigger=ForgetTrigger.SUCCESS_RATE, reason="unit")
        snapshot = engine.snapshots.get(batch.snapshot_id)
        assert snapshot is not None
        assert os.path.exists(snapshot.path)
        payload = engine.snapshots.read(batch.snapshot_id)
        assert payload["count"] == 1
        assert payload["entries"][0]["content_redacted"] == "关键字 gamma 的事实"
        assert batch.deleted_ids == [result.entry.id]

    async def test_deletion_is_physical(self, tmp_path, clock, store):
        result = await _seeded(store)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        await engine.forget([_candidate(result.entry)], trigger="manual")
        reopened = make_store(tmp_path, clock=clock)
        assert await reopened.get(result.entry.id, workspace_root=ROOT_A,
                                  include_expired=True) is None
        assert await reopened.all_entries() == []

    async def test_dry_run_deletes_nothing(self, tmp_path, clock, store):
        result = await _seeded(store)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        batch = await engine.forget([_candidate(result.entry)], trigger="manual", dry_run=True)
        assert batch.dry_run is True
        assert batch.snapshot_id == ""
        assert batch.deleted_ids == []
        assert engine.snapshots.list_snapshots() == []
        assert len(await store.all_entries()) == 1

    async def test_empty_candidate_list_is_a_no_op(self, tmp_path, clock, store):
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        batch = await engine.forget([], trigger="manual")
        assert batch.deleted_count == 0
        assert engine.snapshots.list_snapshots() == []

    async def test_failures_are_reported_not_swallowed(self, tmp_path, clock, store, monkeypatch):
        result = await _seeded(store)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)

        async def _boom(entry, *, force=True):
            return False

        monkeypatch.setattr(store, "delete_entry", _boom)
        batch = await engine.forget([_candidate(result.entry)], trigger="manual")
        assert batch.failed_ids == [result.entry.id]
        assert batch.deleted_ids == []

    async def test_run_is_dry_run_by_default(self, tmp_path, clock, store):
        result = await _seeded(store, memory_type="working", ttl_seconds=60)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        clock.advance(61)
        report = await engine.run()
        assert report.dry_run is True
        assert report.candidates_by_trigger() == {"ttl_expired": 1}
        assert engine.snapshots.list_snapshots() == []
        assert len(await store.all_entries()) == 1
        assert result.entry.id

    async def test_run_with_execute_deletes_and_snapshots(self, tmp_path, clock, store):
        await _seeded(store, memory_type="working", ttl_seconds=60)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        clock.advance(61)
        report = await engine.run(execute=True)
        assert report.dry_run is False
        assert report.batches[0].deleted_count == 1
        assert len(engine.snapshots.list_snapshots()) == 1
        assert await store.all_entries() == []

    async def test_run_prunes_expired_snapshots_only_when_executing(self, tmp_path, clock, store):
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        engine.snapshots.capture([], trigger="manual", now=clock())
        clock.advance((SNAPSHOT_RETENTION_DAYS + 1) * DAY)
        report = await engine.run(execute=False)
        assert report.pruned_snapshots == 0
        report = await engine.run(execute=True)
        assert report.pruned_snapshots == 1


# ════════════════════════════════════════════════════════════
#  6. 快照存储（保留 30 天 / 完整性验签 / 墓碑模式）
# ════════════════════════════════════════════════════════════


class TestSnapshotStore:
    def test_default_retention_is_30_days(self, tmp_path, clock):
        snapshots = MemorySnapshotStore(root=str(tmp_path / "s"), clock=clock)
        assert snapshots.retention_days == float(SNAPSHOT_RETENTION_DAYS) == 30.0

    def test_default_root_is_outside_project_tree(self):
        assert ".cloudpivot" in DEFAULT_SNAPSHOT_ROOT

    def test_retention_days_is_configurable(self, tmp_path, clock, monkeypatch):
        monkeypatch.setenv("MEMORY_SNAPSHOT_RETENTION_DAYS", "7")
        snapshots = MemorySnapshotStore(root=str(tmp_path / "s"), clock=clock)
        assert snapshots.retention_days == 7.0

    def test_invalid_retention_falls_back(self, tmp_path, clock, monkeypatch):
        monkeypatch.setenv("MEMORY_SNAPSHOT_RETENTION_DAYS", "bogus")
        snapshots = MemorySnapshotStore(root=str(tmp_path / "s"), clock=clock)
        assert snapshots.retention_days == float(SNAPSHOT_RETENTION_DAYS)

    async def test_expiry_is_created_at_plus_retention(self, tmp_path, clock, store):
        from memory_layer_testkit import make_entry

        snapshots = MemorySnapshotStore(root=str(tmp_path / "s"), clock=clock)
        record = snapshots.capture([make_entry()], trigger="manual")
        assert record.expires_at == clock() + 30 * DAY

    async def test_prune_keeps_until_expiry_then_removes(self, tmp_path, clock, store):
        from memory_layer_testkit import make_entry

        snapshots = MemorySnapshotStore(root=str(tmp_path / "s"), clock=clock)
        record = snapshots.capture([make_entry()], trigger="manual")
        clock.advance(29 * DAY)
        assert snapshots.prune() == 0
        assert os.path.exists(record.path)
        assert len(snapshots.list_snapshots()) == 1
        clock.advance(2 * DAY)
        assert snapshots.prune() == 1
        assert not os.path.exists(record.path)
        assert snapshots.list_snapshots() == []

    async def test_verify_detects_tampering(self, tmp_path, clock, store):
        from memory_layer_testkit import make_entry

        snapshots = MemorySnapshotStore(root=str(tmp_path / "s"), clock=clock)
        record = snapshots.capture([make_entry()], trigger="manual")
        assert snapshots.verify(record.snapshot_id) is True
        with open(record.path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        payload["entries"][0]["content_redacted"] = "被篡改"
        with open(record.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        assert snapshots.verify(record.snapshot_id) is False

    async def test_hash_only_mode_is_a_tombstone(self, tmp_path, clock, store):
        from memory_layer_testkit import make_entry

        snapshots = MemorySnapshotStore(root=str(tmp_path / "s"), clock=clock)
        entry = make_entry(subject_id="alice", content_redacted="机密内容")
        record = snapshots.capture([entry], trigger="deletion_right", mode="hash_only")
        payload = snapshots.read(record.snapshot_id)
        stored = payload["entries"][0]
        assert stored["content_redacted"] == ""
        assert stored["subject_id"] == subject_ref("alice")
        assert "alice" not in json.dumps(payload, ensure_ascii=False)
        assert "机密内容" not in json.dumps(payload, ensure_ascii=False)
        assert stored["content_hash"] == entry.content_hash
        assert stored["tombstoned"] is True

    def test_missing_snapshot_reads_as_none(self, tmp_path, clock):
        snapshots = MemorySnapshotStore(root=str(tmp_path / "s"), clock=clock)
        assert snapshots.read("snap_missing") is None
        assert snapshots.verify("snap_missing") is False
        assert snapshots.get("snap_missing") is None


# ════════════════════════════════════════════════════════════
#  7. 触发③：被遗忘权（§8 记忆物理删除 + 审计标识符匿名化 + 链保留）
# ════════════════════════════════════════════════════════════


class TestRightToBeForgotten:
    async def test_erasure_deletes_subject_entries_across_tenants(self, tmp_path, clock):
        store = make_store(tmp_path, clock=clock)
        await store.write("alice 偏好 tabs", memory_type="preference",
                          workspace_root=ROOT_A, subject_id="alice")
        await store.write("alice 的事实", memory_type="fact",
                          workspace_root=ROOT_B, subject_id="alice")
        await store.write("bob 的事实", memory_type="fact",
                          workspace_root=ROOT_A, subject_id="bob")
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        result = await engine.erase_subject("alice")
        assert result.deleted_count == 2
        assert result.subject_ref == subject_ref("alice")
        remaining = await store.all_entries()
        assert [e.subject_id for e in remaining] == ["bob"]

    async def test_erasure_uses_hash_only_snapshot(self, tmp_path, clock):
        store = make_store(tmp_path, clock=clock)
        await store.write("alice 的机密偏好", memory_type="preference",
                          workspace_root=ROOT_A, subject_id="alice")
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        result = await engine.erase_subject("alice")
        payload = engine.snapshots.read(result.snapshot_id)
        assert payload["mode"] == "hash_only"
        blob = json.dumps(payload, ensure_ascii=False)
        assert "机密偏好" not in blob
        assert "alice" not in blob

    async def test_erasure_is_not_recallable_afterwards(self, tmp_path, clock):
        store = make_store(tmp_path, clock=clock)
        await store.write("关键字 omega 的偏好", memory_type="preference",
                          workspace_root=ROOT_A, subject_id="alice")
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        await engine.erase_subject("alice")
        assert await store.recall("omega", workspace_root=ROOT_A, subject_id="alice") == []

    async def test_erasure_dry_run_changes_nothing(self, tmp_path, clock):
        store = make_store(tmp_path, clock=clock)
        await store.write("alice 的偏好", memory_type="preference",
                          workspace_root=ROOT_A, subject_id="alice")
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        result = await engine.erase_subject("alice", dry_run=True)
        assert result.dry_run is True
        assert result.salts_shredded is False
        assert len(await store.all_entries()) == 1
        assert engine.snapshots.list_snapshots() == []

    async def test_erasure_of_unknown_subject_is_safe(self, tmp_path, clock):
        store = make_store(tmp_path, clock=clock)
        await store.write("bob 的事实", memory_type="fact",
                          workspace_root=ROOT_A, subject_id="bob")
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        result = await engine.erase_subject("nobody")
        assert result.deleted_count == 0
        assert len(await store.all_entries()) == 1

    async def test_erasure_with_empty_subject_is_noop(self, tmp_path, clock):
        store = make_store(tmp_path, clock=clock)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        result = await engine.erase_subject("")
        assert result.deleted_count == 0
        assert "subject_id 为空" in result.chain_detail

    async def test_erasure_redacts_prior_full_snapshots(self, tmp_path, clock):
        """删除权必须穿透既有 full 快照，否则 30 天保留期会抵消"物理删除" """
        store = make_store(tmp_path, clock=clock)
        result = await store.write("alice 的历史事实", memory_type="fact",
                                   workspace_root=ROOT_A, subject_id="alice")
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        # 先制造一份 full 快照（模拟触发①②留下的可回滚快照）
        full = engine.snapshots.capture([result.entry], trigger="source_invalidated",
                                        mode="full")
        assert "alice 的历史事实" in json.dumps(
            engine.snapshots.read(full.snapshot_id), ensure_ascii=False)
        erased = await engine.erase_subject("alice")
        assert erased.snapshots_redacted == 1
        blob = json.dumps(engine.snapshots.read(full.snapshot_id), ensure_ascii=False)
        assert "alice 的历史事实" not in blob
        assert '"alice"' not in blob
        assert engine.snapshots.verify(full.snapshot_id) is True

    async def test_salt_shredding_is_irreversible(self, tmp_path, clock):
        store = make_store(tmp_path, clock=clock)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        before = engine.pseudonymizer.pseudonym("alice")
        assert before.startswith("anon-")
        assert engine.pseudonymizer.has_salt("alice") is True
        await engine.erase_subject("alice")
        assert engine.pseudonymizer.has_salt("alice") is False
        assert engine.pseudonymizer.is_shredded("alice") is True
        assert engine.pseudonymizer.pseudonym("alice") == ERASED_MARKER

    async def test_pseudonym_is_stable_before_erasure(self, tmp_path, clock):
        store = make_store(tmp_path, clock=clock)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        assert engine.pseudonymizer.pseudonym("alice") == engine.pseudonymizer.pseudonym("alice")
        assert engine.pseudonymizer.pseudonym("alice") != engine.pseudonymizer.pseudonym("bob")

    async def test_pseudonym_persists_across_instances(self, tmp_path, clock):
        from agent.memory.identity import SubjectPseudonymizer

        store = make_store(tmp_path, clock=clock)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        expected = engine.pseudonymizer.pseudonym("alice")
        reopened = SubjectPseudonymizer(root=str(tmp_path / "layers" / "identity"))
        assert reopened.pseudonym("alice") == expected

    async def test_erasure_records_audit_and_keeps_chain_verifiable(
        self, tmp_path, clock, bound_audit_chain
    ):
        """★ 核心验收：删的是记忆，不是证据 —— 链仍可验签且无原始标识符残留"""
        store = make_store(tmp_path, clock=clock, audit=True)
        await store.write("alice 的偏好内容", memory_type="preference",
                          workspace_root=ROOT_A, subject_id="alice")
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        result = await engine.erase_subject("alice")

        assert result.deleted_count == 1
        assert result.audit_recorded is True
        assert result.chain_verified is True           # 链未断
        assert result.chain_checked > 0
        assert result.residual_identifier_hits == []   # 无原始标识符残留
        assert result.anonymized is True
        assert result.pseudonym_before.startswith("anon-")
        assert result.pseudonym_after == ERASED_MARKER
        assert result.chain_pseudonym_present is True  # 旧伪名仍在链上（证据保留）

        actions = [e.action for e in bound_audit_chain.entries()]
        assert "memory.erase" in actions

    async def test_audit_payload_never_carries_content_or_raw_subject(
        self, tmp_path, clock, bound_audit_chain
    ):
        store = make_store(tmp_path, clock=clock, audit=True)
        await store.write("alice 的机密偏好 13812345678", memory_type="preference",
                          workspace_root=ROOT_A, subject_id="alice")
        blob = json.dumps(
            [{"a": e.actor, "s": e.subject, "p": e.payload}
             for e in bound_audit_chain.entries()], ensure_ascii=False)
        assert "alice" not in blob
        assert "机密偏好" not in blob

    async def test_store_degrades_are_audited(self, tmp_path, clock, bound_audit_chain):
        from agent.memory.tenancy import TenancyPolicy

        store = make_store(tmp_path, clock=clock, audit=True,
                           tenant_policy=TenancyPolicy(allow_missing_tenancy=True))
        await store.write("无租户上下文的事实", memory_type="fact")
        actions = [e.action for e in bound_audit_chain.entries()]
        assert "memory.write.degraded" in actions

    async def test_rejected_write_is_audited(self, tmp_path, clock, bound_audit_chain):
        from agent.memory.tenancy import MemoryWriteRejected

        store = make_store(tmp_path, clock=clock, audit=True)
        with pytest.raises(MemoryWriteRejected):
            await store.write("个人偏好别进策略层", memory_type="strategy",
                              workspace_root=ROOT_A, subject_id="alice")
        actions = [e.action for e in bound_audit_chain.entries()]
        assert "memory.write.rejected" in actions

    async def test_chain_scan_covers_whole_chain_not_a_tail(
        self, tmp_path, clock, bound_audit_chain
    ):
        """修复 #10：残留扫描覆盖**整条链**并如实报出扫描条数（不是只扫最近 N 条）"""
        store = make_store(tmp_path, clock=clock, audit=True)
        engine, _ = make_engine(tmp_path, store=store, clock=clock)
        subject = "alice"
        # 先制造一批与本主体无关的链记录，把目标记录"推离"尾部
        for index in range(60):
            await store.write("无关事实 %d" % index, memory_type="fact",
                              workspace_root=ROOT_A, subject_id="bob")
        await store.write("alice 的偏好", memory_type="preference",
                          workspace_root=ROOT_A, subject_id=subject)
        total = bound_audit_chain.count()
        assert total > 60

        result = await engine.erase_subject(subject)
        assert result.chain_verified is True
        # 全链，而非尾部截断；扫描含擦除自身追加的 memory.erase 记录 ⇒ 等于扫描时的链长
        assert result.chain_scanned == total + 1
        assert result.chain_scanned == bound_audit_chain.count()
        assert result.residual_identifier_hits == []
        assert result.anonymized is True

    async def test_chain_scan_limit_is_a_visible_tradeoff(
        self, tmp_path, clock, bound_audit_chain
    ):
        """扫描上限是**可见**的取舍：设上限后扫描条数如实下降（不谎报全链）"""
        store = make_store(tmp_path, clock=clock, audit=True)
        engine, _ = make_engine(tmp_path, store=store, clock=clock, audit_scan_limit=2)
        for index in range(6):
            await store.write("事实 %d" % index, memory_type="fact",
                              workspace_root=ROOT_A, subject_id="bob")
        result = await engine.erase_subject("bob")
        assert result.chain_scanned == 2
        assert bound_audit_chain.count() > 2


# ════════════════════════════════════════════════════════════
#  8. 数据源适配器
# ════════════════════════════════════════════════════════════


class TestQualitySource:
    def test_computes_rate_from_ledger(self):
        from memory_layer_testkit import FakeTraceStore

        fake = FakeTraceStore([FakeTrace(CAP, "success"), FakeTrace(CAP, "error")])
        source = TraceQualitySource(trace_store=fake)
        sample = source.success_rate(CAP, now=1_000_000.0)
        assert (sample.samples, sample.successes) == (2, 1)
        assert sample.rate == pytest.approx(0.5)

    def test_empty_ledger_yields_none_rate(self):
        from memory_layer_testkit import FakeTraceStore

        source = TraceQualitySource(trace_store=FakeTraceStore([]))
        sample = source.success_rate(CAP)
        assert sample.rate is None
        assert sample.samples == 0

    def test_unavailable_ledger_yields_none_rate(self):
        class _Broken:
            def query(self, **_kwargs):
                raise RuntimeError("ledger down")

        source = TraceQualitySource(trace_store=_Broken())
        assert source.success_rate(CAP).rate is None

    def test_close_only_stops_owned_store(self):
        from memory_layer_testkit import FakeTraceStore

        fake = FakeTraceStore([])
        source = TraceQualitySource(trace_store=fake)
        source.close()
        assert fake.stopped is False  # 外部注入的台账不由本模块停止

    def test_ignores_traces_of_other_capabilities(self):
        from memory_layer_testkit import FakeTraceStore

        fake = FakeTraceStore([FakeTrace("cp.other", "success"), FakeTrace(CAP, "error")])
        sample = TraceQualitySource(trace_store=fake).success_rate(CAP)
        assert (sample.samples, sample.successes) == (1, 0)


class TestSourceChecker:
    def test_deprecated_and_removed_classification(self):
        checker = SourceValidityChecker(
            registry=FakeRegistry({"cp.a": FakeDescriptor(stage="deprecated")}))
        assert checker.check("cp.a").invalid is True
        assert checker.check("cp.a").deprecated is True
        assert checker.check("cp.missing").invalid is True
        assert checker.check("cp.missing").exists is False

    def test_healthy_is_valid(self):
        checker = SourceValidityChecker(
            registry=FakeRegistry({"cp.a": FakeDescriptor(stage="native")}))
        status = checker.check("cp.a")
        assert status.invalid is False
        assert status.stage == "native"

    def test_status_as_dict(self):
        checker = SourceValidityChecker(registry=FakeRegistry({}))
        assert checker.check("cp.x").as_dict()["invalid"] is True


def _candidate(entry):
    from agent.memory.forgetting import ForgetCandidate

    return ForgetCandidate(entry=entry, trigger=ForgetTrigger.SUCCESS_RATE, reason="unit")
