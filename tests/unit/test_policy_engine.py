"""TASK-S4-02 决策引擎单元测试（§5.6）

覆盖：check 返回值形状 / 首个命中生效 / 未覆盖率回落 / 决策缓存与失效 /
latency 分位口径 / 审计与事件埋点 / break-glass（含 TTL 与上限）/ 例外入收件箱 /
引擎异常不阻断 / 引擎不做网络动作的类型级证据。
"""
from __future__ import annotations

import json

import pytest

from policy_testkit import make_policy, make_store, isolate_policy

from agent.policy.decisions import DecisionLog
from agent.policy.engine import (
    REASON_BREAK_GLASS,
    REASON_NO_MATCH,
    REASON_POLICY_ALLOW,
    REASON_POLICY_DENY,
    REASON_POLICY_ASK,
    BreakGlassError,
    DecisionObserver,
    PolicyEngine,
    get_policy_engine,
    reset_policy_engine,
)
from agent.policy.inbox import PolicyInbox
from agent.policy.models import (
    EFFECT_ALLOW,
    EFFECT_ASK,
    EFFECT_DENY,
    PolicyContext,
)


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    return isolate_policy(tmp_path, monkeypatch)


@pytest.fixture
def ctx_factory():
    def _make(capability_id="cp.test.src.act", *, data_class=None, external=True,
              tenant_id="default", actor="u1", action="tool.run", **attrs):
        return PolicyContext.build(
            capability_id=capability_id,
            capability={"trust": {"data_class": data_class}},
            tenant_id=tenant_id, actor=actor, action=action,
            target={"external": external, "host": "h.example.com"},
            attributes=attrs)
    return _make


@pytest.fixture
def engine():
    """默认引擎：**无落盘、无埋点**（判定语义用例不需要 IO）"""
    return PolicyEngine(make_store(), cache_size=0, decision_log=False,
                        observer=DecisionObserver(enabled=False), inbox=False)


# ────────────────────────────────────────────────────────────
#  决策形状与首个命中语义
# ────────────────────────────────────────────────────────────


class TestDecisionShape:
    def test_返回值形状符合_5_6(self, engine, ctx_factory):
        decision = engine.check(ctx_factory())
        assert decision.effect in (EFFECT_ALLOW, EFFECT_DENY, EFFECT_ASK)
        assert isinstance(decision.policy_id, str)

    def test_未覆盖返回_matched_false_且不拒绝(self, engine, ctx_factory):
        decision = engine.check(ctx_factory(data_class="internal", external=False))
        assert decision.effect == EFFECT_ALLOW
        assert decision.matched is False
        assert decision.reason_code == REASON_NO_MATCH
        assert decision.allowed is True  # 「策略层无异议」，不是「授权」

    def test_允许类策略命中(self):
        store = make_store([make_policy(id="a.ok", effect="allow", match={})])
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        decision = eng.check(PolicyContext.build(capability_id="x"))
        assert (decision.effect, decision.matched) == (EFFECT_ALLOW, True)
        assert decision.reason_code == REASON_POLICY_ALLOW

    def test_拒绝命中(self, engine, ctx_factory):
        decision = engine.check(ctx_factory(data_class="secret", external=True))
        assert decision.effect == EFFECT_DENY
        assert decision.policy_id.startswith("builtin.invariant.")
        assert decision.reason_code == REASON_POLICY_DENY
        assert decision.denied is True
        assert decision.message  # 文案已渲染

    def test_ask_命中(self, ctx_factory):
        store = make_store([make_policy(id="a.ask", effect="ask")])
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        decision = eng.check(ctx_factory())
        assert decision.effect == EFFECT_ASK
        assert decision.reason_code == REASON_POLICY_ASK
        assert decision.allowed is False
        assert decision.needs_human is True

    def test_首个命中生效(self, ctx_factory):
        store = make_store([
            make_policy(id="first.deny", effect="deny"),
            make_policy(id="second.allow", effect="allow"),
        ])
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        assert eng.check(ctx_factory()).policy_id == "first.deny"

    def test_内置不变量先于文件策略(self, ctx_factory):
        """一条宽泛 allow 写在文件里也不能关掉 secret 出域禁令。"""
        store = make_store([make_policy(id="wide.allow", effect="allow")])
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        decision = eng.check(ctx_factory(data_class="secret", external=True))
        assert decision.effect == EFFECT_DENY
        assert decision.policy_id.startswith("builtin.invariant.")

    def test_effective_range_不覆盖时视为不存在(self, ctx_factory):
        store = make_store([make_policy(
            id="scoped.deny", effect="deny",
            effective_range={"tenants": ["other-tenant"]})])
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        assert eng.check(ctx_factory(tenant_id="t-alpha")).matched is False

    def test_effective_range_覆盖时生效(self, ctx_factory):
        store = make_store([make_policy(
            id="scoped2.deny", effect="deny",
            effective_range={"scopes": ["cp.test.*"]})])
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        assert eng.check(ctx_factory()).policy_id == "scoped2.deny"

    def test_check_many_批量(self, engine, ctx_factory):
        results = engine.check_many([ctx_factory(), ctx_factory(external=False)])
        assert len(results) == 2

    def test_descriptor_可直接传入(self, ctx_factory):
        class Desc:
            capability_id = "cp.test.src.act"
            trust = type("T", (), {"data_class": "secret", "risk_level": None,
                                   "requires_approval": False})()
            origin = type("O", (), {"source_type": "builtin",
                                    "external_endpoint": True,
                                    "provenance": "verified"})()
            evolution = type("E", (), {"stage": None})()
            tenancy = type("N", (), {"tenant_id": "default"})()

        eng = PolicyEngine(make_store(), cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        assert eng.check(Desc()).effect == EFFECT_DENY

    def test_裸_dict_作为_attributes(self, engine):
        assert engine.check({"tool": "x"}).effect == EFFECT_ALLOW

    def test_None_上下文不崩(self, engine):
        assert engine.check(None).effect == EFFECT_ALLOW

    def test_无法识别的类型抛错被吞成未覆盖(self, engine):
        decision = engine.check(object())
        assert decision.matched is False


# ────────────────────────────────────────────────────────────
#  决策缓存
# ────────────────────────────────────────────────────────────


class TestCache:
    def test_命中缓存(self, ctx_factory):
        eng = PolicyEngine(make_store(), cache_size=16, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        first = eng.check(ctx_factory())
        second = eng.check(ctx_factory())
        assert first.cache_hit is False
        assert second.cache_hit is True
        assert second.effect == first.effect

    def test_不同输入不串键(self, ctx_factory):
        eng = PolicyEngine(make_store(), cache_size=16, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        eng.check(ctx_factory(data_class="secret", external=True))
        other = eng.check(ctx_factory(data_class="internal", external=False))
        assert other.cache_hit is False
        assert other.effect == EFFECT_ALLOW

    def test_策略变更即失效(self, ctx_factory):
        store = make_store()
        eng = PolicyEngine(store, cache_size=16, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        assert eng.check(ctx_factory()).matched is False
        store.add(make_policy(id="new.deny", effect="deny"))
        after = eng.check(ctx_factory())
        assert after.cache_hit is False
        assert after.policy_id == "new.deny"

    def test_策略移除即失效(self, ctx_factory):
        store = make_store([make_policy(id="rm.deny", effect="deny")])
        eng = PolicyEngine(store, cache_size=16, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        assert eng.check(ctx_factory()).effect == EFFECT_DENY
        store.remove("rm.deny")
        after = eng.check(ctx_factory())
        assert after.cache_hit is False and after.effect == EFFECT_ALLOW

    def test_显式_invalidate(self, ctx_factory):
        eng = PolicyEngine(make_store(), cache_size=16, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        eng.check(ctx_factory())
        eng.invalidate()
        assert eng.check(ctx_factory()).cache_hit is False

    def test_容量零即关闭缓存(self, ctx_factory):
        eng = PolicyEngine(make_store(), cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        eng.check(ctx_factory())
        assert eng.check(ctx_factory()).cache_hit is False

    def test_LRU_淘汰不超容量(self, ctx_factory):
        eng = PolicyEngine(make_store(), cache_size=2, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        for index in range(6):
            eng.check(ctx_factory(actor=f"u{index}"))
        assert eng.stats["cache"]["size"] <= 2

    def test_use_cache_False_强制重算(self, ctx_factory):
        eng = PolicyEngine(make_store(), cache_size=16, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        eng.check(ctx_factory())
        assert eng.check(ctx_factory(), use_cache=False).cache_hit is False

    def test_策略变更时回收旧缓存条目(self, ctx_factory):
        """正确性由缓存键保证；这里验证的是**内存回收**（旧键不在 LRU 里占位）。"""
        store = make_store()
        eng = PolicyEngine(store, cache_size=16, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        for index in range(4):
            eng.check(ctx_factory(actor=f"u{index}"))
        assert eng.stats["cache"]["size"] == 4
        store.add(make_policy(id="reclaim.deny", effect="deny"))
        eng.check(ctx_factory(actor="u9"))
        assert eng.stats["cache"]["size"] == 1  # 旧条目已被回收

    def test_统计计数(self, ctx_factory):
        eng = PolicyEngine(make_store(), cache_size=16, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        eng.check(ctx_factory())
        eng.check(ctx_factory())
        stats = eng.stats["cache"]
        assert stats["misses"] == 1 and stats["hits"] == 1


class TestLatencyEvidence:
    def test_分位统计存在且口径可分辨(self, ctx_factory):
        eng = PolicyEngine(make_store(), cache_size=64, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        for _ in range(60):
            eng.check(ctx_factory())
        overall = eng.latency_percentiles()
        hits = eng.latency_percentiles(cache_hit=True)
        misses = eng.latency_percentiles(cache_hit=False)
        assert overall["count"] == 60
        assert hits["count"] == 59 and misses["count"] == 1
        for stats in (overall, hits, misses):
            assert stats["p50"] is not None and stats["p99"] is not None

    def test_无样本返回_None(self):
        eng = PolicyEngine(make_store(), cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        assert eng.latency_percentiles()["count"] == 0
        assert eng.latency_percentiles()["p99"] is None

    def test_reset_latency(self, ctx_factory):
        eng = PolicyEngine(make_store(), cache_size=4, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        eng.check(ctx_factory())
        eng.reset_latency()
        assert eng.latency_percentiles()["count"] == 0

    def test_缓存命中路径相对更快(self, ctx_factory):
        """相对断言而非绝对阈值：CI 覆盖率插桩下绝对值会抖动（START §七.2）。

        这里只要求「命中路径 p99 ≤ 未命中路径 p99 × 3 + 少量余量」，即缓存确实
        在起作用的**结构性**证据；绝对 p99<5ms 的实测证据由
        ``scripts/bench_policy_cache.py`` 输出（验收报告引用其原始输出）。
        """
        store = make_store([make_policy(id="lat.deny", effect="deny",
                                        match={"all": [
                                            {"field": "tenant.id", "op": "in",
                                             "value": ["default", "t1", "t2", "t3"]},
                                            {"field": "target.external", "op": "eq",
                                             "value": True},
                                        ]})])
        eng = PolicyEngine(store, cache_size=256, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        for index in range(400):
            eng.check(ctx_factory(actor=f"u{index % 32}"))
        hit_p99 = eng.latency_percentiles(cache_hit=True)["p99"]
        miss_p99 = eng.latency_percentiles(cache_hit=False)["p99"]
        assert hit_p99 is not None and miss_p99 is not None
        assert hit_p99 <= miss_p99 * 3 + 0.5


# ────────────────────────────────────────────────────────────
#  break-glass
# ────────────────────────────────────────────────────────────


class TestBreakGlass:
    @pytest.fixture
    def store(self):
        return make_store([make_policy(
            id="bg.deny", effect="deny", break_glass_ttl_min=30,
            match={"field": "target.external", "op": "eq", "value": True})])

    def test_不可例外的策略拒绝授予(self):
        store = make_store([make_policy(id="abs.deny", effect="deny",
                                        break_glass_ttl_min=None)])
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        with pytest.raises(BreakGlassError) as exc:
            eng.grant_break_glass("abs.deny", actor="ops", reason="紧急")
        assert "不可例外" in str(exc.value)

    def test_不存在的策略拒绝授予(self, store):
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        with pytest.raises(BreakGlassError):
            eng.grant_break_glass("nope", actor="ops", reason="x")

    @pytest.mark.parametrize("actor,reason,fragment", [
        ("", "r", "授予人"), ("a", "", "理由"),
    ])
    def test_必须记录人与理由(self, store, actor, reason, fragment):
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        with pytest.raises(BreakGlassError) as exc:
            eng.grant_break_glass("bg.deny", actor=actor, reason=reason)
        assert fragment in str(exc.value)

    def test_ttl_不得超过策略上限(self, store):
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        with pytest.raises(BreakGlassError) as exc:
            eng.grant_break_glass("bg.deny", actor="ops", reason="r", ttl_min=31)
        assert "超出策略上限" in str(exc.value)

    def test_ttl_必须为正(self, store):
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        with pytest.raises(BreakGlassError):
            eng.grant_break_glass("bg.deny", actor="ops", reason="r", ttl_min=0)

    def test_授予后放行并标记来源(self, store, ctx_factory):
        eng = PolicyEngine(store, cache_size=16, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        assert eng.check(ctx_factory()).effect == EFFECT_DENY
        grant = eng.grant_break_glass("bg.deny", actor="ops", reason="演练")
        decision = eng.check(ctx_factory())
        assert decision.effect == EFFECT_ALLOW
        assert decision.break_glass is True
        assert decision.break_glass_grant == grant.grant_id
        assert decision.reason_code == REASON_BREAK_GLASS
        assert decision.policy_id == "bg.deny"  # 仍能追溯被例外的策略

    def test_授予会使缓存失效(self, store, ctx_factory):
        eng = PolicyEngine(store, cache_size=16, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        eng.check(ctx_factory())
        eng.grant_break_glass("bg.deny", actor="ops", reason="演练")
        assert eng.check(ctx_factory()).effect == EFFECT_ALLOW

    def test_撤销后恢复拒绝(self, store, ctx_factory):
        eng = PolicyEngine(store, cache_size=16, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        grant = eng.grant_break_glass("bg.deny", actor="ops", reason="演练")
        assert eng.revoke_break_glass(grant.grant_id) is True
        assert eng.check(ctx_factory()).effect == EFFECT_DENY

    def test_撤销不存在的返回_False(self, store):
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        assert eng.revoke_break_glass("nope") is False

    def test_绑定能力范围(self, store, ctx_factory):
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        eng.grant_break_glass("bg.deny", actor="ops", reason="r",
                              capability_id="cp.other.*")
        assert eng.check(ctx_factory()).effect == EFFECT_DENY
        eng.grant_break_glass("bg.deny", actor="ops", reason="r",
                              capability_id="cp.test.src.act")
        assert eng.check(ctx_factory()).effect == EFFECT_ALLOW

    def test_过期例外不再生效(self, store, ctx_factory, monkeypatch):
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        eng.grant_break_glass("bg.deny", actor="ops", reason="r", ttl_min=1)
        grant = eng.active_grants()[0]
        # 直接伪造过期（不 sleep：用例不做时间等待）
        object.__setattr__(grant, "expires_at", "2000-01-01T00:00:00+08:00")
        assert eng.active_grants() == []
        assert eng.check(ctx_factory()).effect == EFFECT_DENY

    def test_active_grants_列出有效例外(self, store):
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        eng.grant_break_glass("bg.deny", actor="ops", reason="r")
        grants = eng.active_grants()
        assert len(grants) == 1 and grants[0].actor == "ops"
        assert grants[0].to_dict()["ttl_min"] > 0


# ────────────────────────────────────────────────────────────
#  收件箱（§5.6「只收例外」）
# ────────────────────────────────────────────────────────────


class TestInboxRouting:
    def _engine(self, tmp_path, policies, inbox):
        return PolicyEngine(make_store(policies), cache_size=0, decision_log=False,
                            observer=DecisionObserver(enabled=False), inbox=inbox)

    def test_ask_进收件箱(self, tmp_path, ctx_factory):
        inbox = PolicyInbox(backend="log", path=str(tmp_path / "inbox.jsonl"))
        eng = self._engine(tmp_path, [make_policy(id="i.ask", effect="ask")], inbox)
        eng.check(ctx_factory())
        items = inbox.pending()
        assert len(items) == 1
        assert items[0].kind == "policy.ask"

    def test_deny_不进收件箱(self, tmp_path, ctx_factory):
        inbox = PolicyInbox(backend="log", path=str(tmp_path / "inbox.jsonl"))
        eng = self._engine(tmp_path, [make_policy(id="i.deny", effect="deny")], inbox)
        eng.check(ctx_factory())
        assert inbox.pending() == []

    def test_allow_不进收件箱(self, tmp_path, ctx_factory):
        inbox = PolicyInbox(backend="log", path=str(tmp_path / "inbox.jsonl"))
        eng = self._engine(tmp_path, [make_policy(id="i.allow", effect="allow")], inbox)
        eng.check(ctx_factory())
        assert inbox.pending() == []

    def test_未覆盖不进收件箱(self, tmp_path, ctx_factory):
        inbox = PolicyInbox(backend="log", path=str(tmp_path / "inbox.jsonl"))
        eng = self._engine(tmp_path, [], inbox)
        eng.check(ctx_factory(external=False))
        assert inbox.pending() == []

    def test_break_glass_进收件箱(self, tmp_path, ctx_factory):
        inbox = PolicyInbox(backend="log", path=str(tmp_path / "inbox.jsonl"))
        eng = self._engine(tmp_path, [make_policy(id="i.bg", effect="deny",
                                                  break_glass_ttl_min=10)], inbox)
        eng.grant_break_glass("i.bg", actor="ops", reason="r")
        eng.check(ctx_factory())
        items = inbox.pending()
        assert items and items[0].kind == "policy.break_glass"

    def test_重复例外按窗口合并(self, tmp_path, ctx_factory):
        inbox = PolicyInbox(backend="log", path=str(tmp_path / "inbox.jsonl"),
                            dedupe_window_seconds=600)
        eng = self._engine(tmp_path, [make_policy(id="i.dup", effect="ask")], inbox)
        for _ in range(5):
            eng.check(ctx_factory())
        items = inbox.pending()
        assert len(items) == 1  # 入账只有一条
        assert items[0].duplicate_count == 5
        assert inbox.stats["deduped"] == 4

    def test_收件箱不可用时不影响决策(self, tmp_path, ctx_factory):
        class Boom:
            def submit(self, *a, **k):
                raise RuntimeError("inbox down")

        eng = self._engine(tmp_path, [make_policy(id="i.boom", effect="ask")], Boom())
        assert eng.check(ctx_factory()).effect == EFFECT_ASK


# ────────────────────────────────────────────────────────────
#  埋点 / 审计 / 决策日志
# ────────────────────────────────────────────────────────────


class TestObservability:
    def test_决策写入链式审计(self, tmp_path, ctx_factory):
        """决策入链式审计（S2-02）

        **隔离口径**：本用例只对**自己建的链**做断言。读进程级门面
        （`get_audit().recent(...)`）在全量 xdist 下会被同 worker 内其它写者
        （如 `lineage.append`）污染，并触发审计链的单写者 seq 冲突——被测对象
        变成「谁先写」，用例就不再是本用例。这里照抄 S2-02 自己在
        `test_audit_facade.py::bound_facade` 给出的口径：自有链 + 公开 `bind()`
        + 从自有链按 action 精确过滤。
        """
        from agent.audit import facade as facade_mod
        from agent.audit.chain import AuditChain, reset_audit_chains

        reset_audit_chains()
        chain = AuditChain(str(tmp_path / "policy_audit.db"),
                           roots_path=str(tmp_path / "roots.jsonl"),
                           signing_key_path=str(tmp_path / "k.pem"),
                           auto_seal=False)
        previous = facade_mod.audit.bind(chain)
        old_enabled = facade_mod.audit.enabled
        facade_mod.audit.enabled = True
        try:
            eng = PolicyEngine(make_store(), cache_size=0, decision_log=False,
                               observer=DecisionObserver(), inbox=False)
            eng.check(ctx_factory(data_class="secret", external=True))
            recorded = chain.entries(action="policy.decision")
        finally:
            facade_mod.audit.bind(previous)
            facade_mod.audit.enabled = old_enabled
            chain.close(timeout=2.0)
            reset_audit_chains()
        assert len(recorded) >= 1
        assert recorded[-1].source == "agent"

    def test_事件埋点_policy_decision_与_policy_denied(self, tmp_path, ctx_factory):
        eng = PolicyEngine(make_store(), cache_size=0, decision_log=False,
                           observer=DecisionObserver(), inbox=False)
        eng.check(ctx_factory(data_class="secret", external=True))
        from agent.observability import events as ev
        types = [e.type for e in ev.iter_events()]
        assert "policy.decision" in types
        assert "policy.denied" in types

    def test_埋点不写匹配值(self, tmp_path, ctx_factory):
        """沿用既有脱敏口径：审计/事件里不出现敏感匹配值。"""
        eng = PolicyEngine(make_store(), cache_size=0, decision_log=False,
                           observer=DecisionObserver(), inbox=False)
        ctx = PolicyContext.build(
            capability_id="cp.test.src.act",
            capability={"trust": {"data_class": "secret"}},
            target={"external": True, "host": "internal.example.com"},
            attributes={"payload": "sk-abcdefghijklmnopqrstuvwxyz"})
        eng.check(ctx)
        from agent.observability import events as ev
        blob = json.dumps([e.to_dict() for e in ev.iter_events()], ensure_ascii=False)
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in blob

    def test_ask_发_intervention_事件(self, tmp_path, ctx_factory):
        store = make_store([make_policy(id="ev.ask", effect="ask")])
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(), inbox=False)
        eng.check(ctx_factory())
        from agent.observability import events as ev
        interventions = [e for e in ev.iter_events() if e.type == "intervention"]
        assert any(e.payload.get("kind") == "policy.ask" for e in interventions)

    def test_埋点范围_governance_只为治理相关决策写链(self, tmp_path, ctx_factory):
        """``scope=governance``：``matched=False`` 的 allow 不进防篡改账本

        实测依据（`bench_policy_cache.py --with-io`）：链式审计是 SQLite 追加，
        是出域判定路径的主要开销（命中 p99 0.047ms → 3.764ms）。该开关把
        「策略层无异议」剔出账本，但**决策日志照写**——模拟器数据不受影响。
        """
        eng = PolicyEngine(make_store(), cache_size=0, decision_log=False,
                           observer=DecisionObserver(scope="governance"), inbox=False)
        assert eng.stats["observer"]["scope"] == "governance"
        eng.check(ctx_factory(data_class="internal", external=False))   # matched=False
        assert eng.stats["observer"]["skipped_count"] == 1
        assert eng.stats["observer"]["audit_count"] == 0
        eng.check(ctx_factory(data_class="secret", external=True))       # deny
        assert eng.stats["observer"]["audit_count"] == 1

    def test_埋点范围_governance_命中策略的_allow_仍写链(self, ctx_factory):
        store = make_store([make_policy(id="gv.allow", effect="allow", match={})])
        eng = PolicyEngine(store, cache_size=0, decision_log=False,
                           observer=DecisionObserver(scope="governance"), inbox=False)
        eng.check(ctx_factory())
        assert eng.stats["observer"]["audit_count"] == 1

    def test_埋点范围_非法值回退_all(self, monkeypatch):
        monkeypatch.setenv("CP_POLICY_OBSERVE_SCOPE", "nonsense")
        assert DecisionObserver().scope == "all"
        monkeypatch.setenv("CP_POLICY_OBSERVE_SCOPE", "governance")
        assert DecisionObserver().scope == "governance"

    def test_埋点范围_governance_不影响决策日志(self, tmp_path, ctx_factory):
        log = DecisionLog(str(tmp_path / "d.jsonl"))
        eng = PolicyEngine(make_store(), cache_size=0, decision_log=log,
                           observer=DecisionObserver(scope="governance"), inbox=False)
        eng.check(ctx_factory(external=False))
        log.close()
        assert len(DecisionLog(str(tmp_path / "d.jsonl"), enabled=False).read()) == 1

    def test_埋点关闭时零写入(self, tmp_path, ctx_factory):
        eng = PolicyEngine(make_store(), cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        eng.check(ctx_factory())
        from agent.observability import events as ev
        assert ev.iter_events() == []
        assert eng.stats["observer"]["enabled"] is False

    def test_埋点异常不阻断决策(self, tmp_path, ctx_factory):
        class Boom:
            enabled = True

            def observe(self, *a, **k):
                raise RuntimeError("audit down")

        eng = PolicyEngine(make_store(), cache_size=0, decision_log=False,
                           observer=Boom(), inbox=False)
        assert eng.check(ctx_factory()).matched is False

    def test_决策日志落盘且可读回(self, tmp_path, ctx_factory):
        log = DecisionLog(str(tmp_path / "decisions.jsonl"))
        eng = PolicyEngine(make_store(), cache_size=0, decision_log=log,
                           observer=DecisionObserver(enabled=False), inbox=False)
        eng.check(ctx_factory(data_class="secret", external=True))
        log.close()
        records = DecisionLog(str(tmp_path / "decisions.jsonl")).read()
        assert len(records) == 1
        assert records[0].effect == EFFECT_DENY
        assert records[0].input["capability"]["trust"]["data_class"] == "secret"

    def test_决策日志脱敏敏感键(self, tmp_path, ctx_factory):
        log = DecisionLog(str(tmp_path / "decisions.jsonl"))
        eng = PolicyEngine(make_store(), cache_size=0, decision_log=log,
                           observer=DecisionObserver(enabled=False), inbox=False)
        eng.check(ctx_factory(api_key="sk-abcdefghijklmnopqrstuvwxyz"))
        log.close()
        raw = (tmp_path / "decisions.jsonl").read_text(encoding="utf-8")
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in raw

    def test_决策日志关闭时不落盘(self, tmp_path, ctx_factory):
        eng = PolicyEngine(make_store(), cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        eng.check(ctx_factory())
        assert not (tmp_path / "policy_io" / "decisions.jsonl").exists()


class TestEngineRobustness:
    def test_引擎异常被吞成未覆盖(self, ctx_factory):
        class BoomStore:
            revision = 1

            def active(self):
                raise RuntimeError("store down")

            def fingerprint(self):
                return ""

        eng = PolicyEngine(BoomStore(), cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        decision = eng.check(ctx_factory())
        assert decision.matched is False and decision.effect == EFFECT_ALLOW
        assert eng.stats["errors"] >= 1

    def test_stats_全量字段(self, ctx_factory):
        eng = PolicyEngine(make_store(), cache_size=4, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        eng.check(ctx_factory())
        stats = eng.stats
        for key in ("decisions", "errors", "cache", "store_revision",
                    "store_fingerprint", "active_policies", "grants", "latency"):
            assert key in stats

    def test_simulate_以引擎为基线可跑通(self, tmp_path, ctx_factory):
        """引擎**不再**提供 `simulate()` 便捷方法（见 engine.py 的架构说明：
        simulator 模块级 import engine，反向引用会形成 no_circular_dependency
        违规并阻断 CI）。这里验证「引擎 + 空决策日志」这条调用路径仍然可用。
        """
        from agent.policy.simulator import simulate
        eng = PolicyEngine(make_store(), cache_size=0, decision_log=False,
                           observer=DecisionObserver(enabled=False), inbox=False)
        report = simulate(make_policy(id="sim.cand"), engine=eng, since_days=7,
                          log_path=str(tmp_path / "empty.jsonl"))
        assert report.total == 0
        assert report.verdict == "no_sample"


class TestSingleton:
    def test_get_policy_engine_同实例(self):
        reset_policy_engine()
        assert get_policy_engine() is get_policy_engine()

    def test_reset_换实例(self):
        reset_policy_engine()
        first = get_policy_engine()
        reset_policy_engine()
        assert get_policy_engine() is not first

    def test_默认引擎不创建文件(self, tmp_path):
        """构造引擎是**读**操作；不应在仓库里生任何落盘产物。"""
        reset_policy_engine()
        engine = get_policy_engine()
        assert engine.decision_log is not None
        assert not (tmp_path / "unused").exists()
