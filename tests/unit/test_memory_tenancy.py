"""TASK-S5-01 租户隔离矩阵单测（`agent/memory/tenancy.py`，对齐 v7.2 P7.2-08）

**本文件是"三条铁律"的正反例证据来源**：

1. 事实/策略记忆**按租户隔离** —— 同租户可见、**跨租户不可见**（反例）；
2. 偏好记忆**跟随 subject 跨租户携带** —— 换租户仍可见，换 subject 不可见（反例）；
3. **个人偏好绝不污染企业策略记忆** —— 策略层个人写入被拒；org 级只读下发。
"""

import os

import pytest

from memory_layer_testkit import memory_runtime  # noqa: F401  (autouse 夹具)

from agent.memory.taxonomy import MemoryEntry, MemoryType, project_scope
from agent.memory.tenancy import (
    DEFAULT_TENANT_PLACEHOLDER,
    DEGRADED_TENANT,
    ISOLATION_MATRIX,
    MemoryWriteRejected,
    MissingTenancyError,
    ORG_TENANT,
    TenancyContext,
    TenancyPolicy,
    WriteChannel,
    WriteDisposition,
    build_tenancy_policy,
    isolation_matrix_rows,
    resolve_tenancy,
    tenancy_context,
    tenant_id_for_workspace,
)
from agent.observability.trace_v2 import TraceContext, derive_workspace_id

TENANT_A = "ws_tenantA"
TENANT_B = "ws_tenantB"


def ctx_a(**overrides):
    params = {
        "tenant_id": TENANT_A, "workspace_id": TENANT_A, "subject_id": "alice"}
    params.update(overrides)
    return TenancyContext(**params)


def ctx_b(**overrides):
    params = {
        "tenant_id": TENANT_B, "workspace_id": TENANT_B, "subject_id": "alice"}
    params.update(overrides)
    return TenancyContext(**params)


def entry(**overrides):
    params = {
        "id": "mem_1", "tenant_id": TENANT_A, "subject_id": "alice", "type": "fact",
        "content_redacted": "c", "content_hash": "", "scope": project_scope(TENANT_A),
    }
    params.update(overrides)
    return MemoryEntry(**params)


# ════════════════════════════════════════════════════════════
#  1. 隔离矩阵声明（P7.2-08 三条铁律）
# ════════════════════════════════════════════════════════════


class TestIsolationMatrix:
    def test_matrix_covers_all_four_layers(self):
        assert {r.memory_type for r in ISOLATION_MATRIX} == set(MemoryType)
        assert len(isolation_matrix_rows()) == 4

    def test_fact_carried_by_tenant_and_not_cross_tenant(self):
        row = next(r for r in ISOLATION_MATRIX if r.memory_type is MemoryType.FACT)
        assert row.carrier == "tenant_id"
        assert row.cross_tenant_visible is False
        assert row.subject_write_forbidden is False

    def test_preference_carried_by_subject_and_cross_tenant(self):
        row = next(r for r in ISOLATION_MATRIX if r.memory_type is MemoryType.PREFERENCE)
        assert row.carrier == "subject_id"
        assert row.cross_tenant_visible is True
        assert row.read_only is False

    def test_strategy_is_org_readonly_and_personal_write_forbidden(self):
        row = next(r for r in ISOLATION_MATRIX if r.memory_type is MemoryType.STRATEGY)
        assert row.subject_write_forbidden is True
        assert row.read_only is True
        assert WriteChannel.USER not in row.write_channels
        assert set(row.write_channels) == {WriteChannel.ORG, WriteChannel.SYSTEM}

    def test_preference_row_never_read_only(self):
        """偏好层不得被只读化（否则"偏好随 subject 携带"名存实亡）"""
        for row in ISOLATION_MATRIX:
            if row.memory_type is MemoryType.PREFERENCE:
                assert row.read_only is False


# ════════════════════════════════════════════════════════════
#  2. 租户上下文解析（tenant_id = workspace-hash）
# ════════════════════════════════════════════════════════════


class TestResolveTenancy:
    def test_tenant_id_equals_workspace_hash(self):
        assert tenant_id_for_workspace("/repo/a") == derive_workspace_id("/repo/a")

    def test_workspace_root_derives_tenant_and_workspace(self):
        t = resolve_tenancy(workspace_root="/repo/a", use_current_context=False)
        assert t.tenant_id == t.workspace_id
        assert t.tenant_derived is True

    def test_same_root_is_deterministic(self):
        """同一 root 恒产同一 tenant（**跨平台**契约：确定性）"""
        first = resolve_tenancy(workspace_root="/repo/a", use_current_context=False)
        second = resolve_tenancy(workspace_root="/repo/a", use_current_context=False)
        assert first.tenant_id == second.tenant_id
        assert first.tenant_id == derive_workspace_id("/repo/a")

    @pytest.mark.skipif(
        os.name != "nt",
        reason=("路径大小写不敏感是 **Windows 专属语义**（`os.path.normcase` 在 nt 上 "
                "lower()，在 POSIX 上恒等）——与 test_trace_v2.py 的 "
                "test_windows_case_insensitive_paths 同一 gate，非跨平台契约"))
    def test_same_root_is_case_insensitive_on_windows(self):
        first = resolve_tenancy(workspace_root="/repo/A", use_current_context=False)
        second = resolve_tenancy(workspace_root="/repo/a", use_current_context=False)
        assert first.tenant_id == second.tenant_id

    def test_different_roots_yield_different_tenants(self):
        a = resolve_tenancy(workspace_root="/repo/a", use_current_context=False)
        b = resolve_tenancy(workspace_root="/repo/b", use_current_context=False)
        assert a.tenant_id != b.tenant_id

    def test_empty_context_has_no_tenant(self):
        t = resolve_tenancy(use_current_context=False)
        assert t.has_tenant is False
        assert t.has_workspace is False
        assert t.source == "none"

    def test_explicit_values_win(self):
        t = resolve_tenancy(tenant_id="acme", workspace_id="ws_x",
                            subject_id="bob", use_current_context=False)
        assert (t.tenant_id, t.workspace_id, t.subject_id) == ("acme", "ws_x", "bob")
        assert t.source == "explicit"

    def test_tenant_id_only_ws_prefix_backfills_workspace(self):
        t = resolve_tenancy(tenant_id="ws_only", use_current_context=False)
        assert t.workspace_id == "ws_only"

    def test_trace_context_is_used_when_available(self):
        carrier = TraceContext(tenant_id="default", workspace_id="ws_ctx",
                               subject_id="carol", trace_id="tr-1")
        t = resolve_tenancy(context=carrier, use_current_context=False)
        assert t.tenant_id == "ws_ctx"  # P7.2-08：tenant_id 取 workspace-hash
        assert t.tenant_derived is True
        assert t.subject_id == "carol"
        assert t.trace_id == "tr-1"

    def test_non_default_tenant_and_workspace_are_both_kept(self):
        carrier = TraceContext(tenant_id="acme", workspace_id="ws_branch",
                               subject_id="carol")
        t = resolve_tenancy(context=carrier, use_current_context=False)
        assert t.tenant_id == "acme"
        assert t.workspace_id == "ws_branch"
        assert t.tenant_derived is False

    def test_default_placeholder_is_not_a_tenant(self):
        carrier = TraceContext(tenant_id=DEFAULT_TENANT_PLACEHOLDER)
        t = resolve_tenancy(context=carrier, use_current_context=False)
        assert t.has_tenant is False

    def test_current_context_is_picked_up(self):
        t = None
        with tenancy_context(workspace_root="/repo/a", subject_id="dave") as carrier:
            t = resolve_tenancy()
            assert TraceContext.current() is not None
            assert carrier.subject_id == "dave"
        assert t is not None and t.has_tenant
        assert TraceContext.current() is None

    def test_as_dict_only_leaf_fields(self):
        assert set(ctx_a().as_dict()) == {
            "tenant_id", "workspace_id", "subject_id", "trace_id", "task_id", "source"}


# ════════════════════════════════════════════════════════════
#  3. 写入守卫（hard constraint 1：强制 tenancy）
# ════════════════════════════════════════════════════════════


class TestWriteGuard:
    def setup_method(self):
        self.policy = TenancyPolicy()

    def test_fact_write_accepted_with_project_scope(self):
        decision = self.policy.decide_write("fact", ctx_a())
        assert decision.disposition is WriteDisposition.ACCEPT
        assert decision.scope == project_scope(TENANT_A)
        assert decision.tenant_id == TENANT_A

    def test_missing_tenant_is_rejected_by_default(self):
        decision = self.policy.decide_write(
            "fact", TenancyContext(workspace_id="", tenant_id="", subject_id="alice"))
        assert decision.disposition is WriteDisposition.REJECT
        assert decision.reason == "missing_tenant_id"

    def test_missing_tenancy_can_be_explicitly_degraded(self):
        policy = TenancyPolicy(allow_missing_tenancy=True)
        decision = policy.decide_write(
            "fact", TenancyContext(tenant_id="", subject_id="alice"))
        assert decision.disposition is WriteDisposition.DEGRADE
        assert decision.degraded is True
        assert decision.tenant_id == DEGRADED_TENANT
        assert decision.warnings  # 显式标注，绝不静默

    def test_degrade_flag_defaults_off(self):
        assert build_tenancy_policy().allow_missing_tenancy is False

    def test_degrade_flag_reads_env(self, monkeypatch):
        monkeypatch.setenv("MEMORY_TENANCY_ALLOW_DEGRADE", "1")
        assert build_tenancy_policy().allow_missing_tenancy is True

    def test_missing_workspace_rejected_for_project_scope(self):
        decision = self.policy.decide_write(
            "fact", TenancyContext(tenant_id="acme", workspace_id="", subject_id="u"))
        assert decision.disposition is WriteDisposition.REJECT
        assert decision.reason == "missing_workspace_id"

    def test_scope_workspace_mismatch_rejected(self):
        """越界守卫：不得把事实写进别人的工作区作用域"""
        decision = self.policy.decide_write(
            "fact", ctx_a(), scope=project_scope("ws_someone_else"))
        assert decision.disposition is WriteDisposition.REJECT
        assert decision.reason == "scope_workspace_mismatch"

    def test_preference_accepted_with_subject_only(self):
        decision = self.policy.decide_write("preference", ctx_a())
        assert decision.disposition is WriteDisposition.ACCEPT
        assert decision.scope == "global"
        assert decision.reason == "subject_carried"

    def test_preference_without_subject_rejected(self):
        decision = self.policy.decide_write("preference", ctx_a(subject_id=""))
        assert decision.disposition is WriteDisposition.REJECT
        assert decision.reason == "missing_subject_id"

    def test_preference_cannot_be_org_level(self):
        decision = self.policy.decide_write("preference", ctx_a(), org_level=True)
        assert decision.disposition is WriteDisposition.REJECT
        assert decision.reason == "preference_cannot_be_org_level"

    def test_strategy_personal_write_is_rejected(self):
        """铁律：个人偏好/个人写入绝不进策略层"""
        decision = self.policy.decide_write("strategy", ctx_a())
        assert decision.disposition is WriteDisposition.REJECT
        assert decision.reason == "strategy_layer_readonly"

    def test_strategy_personal_write_rejected_even_without_tenant(self):
        decision = self.policy.decide_write(
            "strategy", TenancyContext(tenant_id="", subject_id="alice"))
        assert decision.disposition is WriteDisposition.REJECT
        assert decision.reason == "strategy_layer_readonly"

    def test_strategy_org_channel_is_org_level_delivery(self):
        decision = self.policy.decide_write("strategy", ctx_a(), channel=WriteChannel.ORG)
        assert decision.disposition is WriteDisposition.ACCEPT
        assert decision.org_level is True
        assert decision.tenant_id == ORG_TENANT
        assert decision.scope == "global"

    def test_strategy_system_channel_can_be_tenant_local(self):
        decision = self.policy.decide_write(
            "strategy", ctx_a(), channel=WriteChannel.SYSTEM, org_level=False)
        assert decision.disposition is WriteDisposition.ACCEPT
        assert decision.org_level is False
        assert decision.tenant_id == TENANT_A
        assert decision.scope == project_scope(TENANT_A)

    def test_unknown_channel_falls_back_to_user(self):
        decision = self.policy.decide_write("strategy", ctx_a(), channel="nonsense")
        assert decision.disposition is WriteDisposition.REJECT

    def test_decision_as_dict_is_json_friendly(self):
        payload = self.policy.decide_write("fact", ctx_a()).as_dict()
        assert payload["disposition"] == "accept"
        assert payload["type"] == "fact"


# ════════════════════════════════════════════════════════════
#  4. 读取可见性矩阵（正例 + 反例）
# ════════════════════════════════════════════════════════════


class TestVisibilityMatrix:
    def setup_method(self):
        self.policy = TenancyPolicy()

    # ── 铁律 1：事实/策略按租户隔离 ──

    def test_same_tenant_fact_is_visible(self):
        assert self.policy.is_visible(entry(), ctx_a()) is True

    def test_cross_tenant_fact_is_invisible(self):
        """★ 反例：跨租户不可见"""
        assert self.policy.is_visible(entry(), ctx_b()) is False

    def test_cross_tenant_tenant_local_strategy_is_invisible(self):
        target = entry(type="strategy", org_level=False)
        assert self.policy.is_visible(target, ctx_a()) is True
        assert self.policy.is_visible(target, ctx_b()) is False

    def test_cross_tenant_working_memory_is_invisible(self):
        target = entry(type="working")
        assert self.policy.is_visible(target, ctx_b()) is False

    def test_context_without_tenant_sees_nothing_tenant_scoped(self):
        empty = TenancyContext(tenant_id="", subject_id="alice")
        assert self.policy.is_visible(entry(), empty) is False

    def test_project_scope_hash_must_match_context_workspace(self):
        target = entry(scope=project_scope(TENANT_A))
        mismatch = TenancyContext(
            tenant_id=TENANT_A, workspace_id="ws_other", subject_id="alice")
        assert self.policy.is_visible(target, mismatch) is False

    def test_context_without_workspace_cannot_read_project_scoped_fact(self):
        target = entry(scope=project_scope(TENANT_A))
        assert self.policy.is_visible(
            target, TenancyContext(tenant_id=TENANT_A, workspace_id="")) is False

    def test_global_scoped_fact_needs_only_tenant(self):
        target = entry(scope="global")
        assert self.policy.is_visible(
            target, TenancyContext(tenant_id=TENANT_A, workspace_id="")) is True

    # ── 铁律 2：偏好随 subject 跨租户携带 ──

    def test_preference_is_carried_across_tenants(self):
        """★ 正例：换租户同 subject 仍可见（携带验证）"""
        target = entry(type="preference", scope="global")
        assert self.policy.is_visible(target, ctx_a()) is True
        assert self.policy.is_visible(target, ctx_b()) is True

    def test_preference_is_invisible_to_other_subject(self):
        """★ 反例：偏好不得泄漏给同租户的其他用户"""
        target = entry(type="preference", scope="global", subject_id="alice")
        assert self.policy.is_visible(target, ctx_a(subject_id="bob")) is False

    def test_preference_invisible_without_subject_context(self):
        target = entry(type="preference", scope="global")
        assert self.policy.is_visible(target, ctx_a(subject_id="")) is False

    def test_preference_with_empty_subject_is_never_visible(self):
        target = entry(type="preference", scope="global", subject_id="")
        assert self.policy.is_visible(target, ctx_a()) is False

    def test_preference_needs_no_tenant(self):
        target = entry(type="preference", scope="global")
        assert self.policy.is_visible(target, TenancyContext(subject_id="alice")) is True

    # ── 铁律 3：企业策略 org 级只读下发 ──

    def test_org_level_strategy_is_visible_to_other_tenant(self):
        target = entry(type="strategy", tenant_id=ORG_TENANT, scope="global", org_level=True)
        assert self.policy.is_visible(target, ctx_b()) is True

    def test_org_level_strategy_read_only_for_personal_channel(self):
        target = entry(type="strategy", tenant_id=ORG_TENANT, scope="global", org_level=True)
        assert self.policy.is_readonly(target, ctx_a(), channel=WriteChannel.USER) is True
        with pytest.raises(MemoryWriteRejected):
            self.policy.require_writable(target, ctx_a(), channel=WriteChannel.USER)

    def test_org_level_strategy_writable_through_governance_channels(self):
        """org/system 治理通道可更新自己下发的策略记忆（否则遗忘/下发无法执行）"""
        target = entry(type="strategy", tenant_id=ORG_TENANT, scope="global", org_level=True)
        assert self.policy.is_readonly(target, ctx_a(), channel=WriteChannel.ORG) is False
        assert self.policy.is_readonly(target, ctx_a(), channel=WriteChannel.SYSTEM) is False
        self.policy.require_writable(target, ctx_a(), channel=WriteChannel.ORG)  # 不抛

    def test_tenant_local_strategy_read_only_for_personal_channel(self):
        target = entry(type="strategy", org_level=False)
        assert self.policy.is_readonly(target, ctx_a(), channel=WriteChannel.USER) is True
        assert self.policy.is_readonly(target, ctx_a(), channel=WriteChannel.SYSTEM) is False

    def test_normal_fact_is_writable(self):
        assert self.policy.is_readonly(entry(), ctx_a()) is False

    # ── 降级 / TTL / 候选闸门 ──

    def test_degraded_entry_is_quarantined_by_default(self):
        target = entry(tenant_id=DEGRADED_TENANT, scope="global", degraded=True)
        assert self.policy.is_visible(target, ctx_a()) is False
        assert self.policy.is_visible(
            target, ctx_a(), include_degraded=True) is True

    def test_expired_entry_excluded_unless_requested(self):
        target = entry(ttl_expires_at=10.0)
        assert self.policy.is_visible(target, ctx_a(), now=100.0) is False
        assert self.policy.is_visible(
            target, ctx_a(), now=100.0, include_expired=True) is True

    def test_forget_candidate_gate(self):
        target = entry()
        target.mark_forget_candidate("ttl_expired")
        assert self.policy.is_visible(target, ctx_a()) is True
        assert self.policy.is_visible(
            target, ctx_a(), include_forget_candidates=False) is False

    def test_none_entry_is_not_visible(self):
        assert self.policy.is_visible(None, ctx_a()) is False


# ════════════════════════════════════════════════════════════
#  5. 召回（可见性 + §4.3 优先级）
# ════════════════════════════════════════════════════════════


class TestRecall:
    def setup_method(self):
        self.policy = TenancyPolicy()

    def _pool(self):
        return [
            entry(id="factA", type="fact", scope=project_scope(TENANT_A), created_at=1.0),
            entry(id="factB", type="fact", tenant_id=TENANT_B,
                  scope=project_scope(TENANT_B), created_at=9.0),
            entry(id="prefA", type="preference", scope="global", created_at=5.0),
            entry(id="stratOrg", type="strategy", tenant_id=ORG_TENANT,
                  scope="global", org_level=True, created_at=1.0),
        ]

    def test_recall_filters_cross_tenant_and_orders_by_priority(self):
        hits = self.policy.recall(self._pool(), ctx_a())
        assert [e.id for e in hits] == ["stratOrg", "factA", "prefA"]

    def test_recall_never_leaks_other_tenant_fact(self):
        assert "factB" not in [e.id for e in self.policy.recall(self._pool(), ctx_a())]

    def test_recall_limit(self):
        hits = self.policy.recall(self._pool(), ctx_a(), limit=1)
        assert [e.id for e in hits] == ["stratOrg"]

    def test_recall_accepts_empty_input(self):
        assert self.policy.recall([], ctx_a()) == []
        assert self.policy.visible_entries(None, ctx_a()) == []

    def test_require_writable_raises_and_carries_decision(self):
        target = entry(type="strategy", tenant_id=ORG_TENANT, scope="global", org_level=True)
        with pytest.raises(MemoryWriteRejected) as exc:
            self.policy.require_writable(target, ctx_a())
        assert exc.value.decision.memory_type is MemoryType.STRATEGY

    def test_missing_tenancy_error_is_tenancy_error_with_code(self):
        err = MissingTenancyError("tenant_id", "fact")
        assert err.code == "MISSING_TENANT_ID"
        assert "P7.2-08" in str(err)
        assert isinstance(err, ValueError)
