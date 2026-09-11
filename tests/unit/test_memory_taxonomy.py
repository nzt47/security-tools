"""TASK-S5-01 四层记忆模型单测（`agent/memory/taxonomy.py`，对齐 v7.2 §3.11 / §4.3 / P7.2-10）

覆盖：四层枚举 / 层策略与隔离维度 / §3.11 字段完整性 / TTL 与到期 / 序列化往返 /
审计叶子（不含内容与原始标识符）/ 召回优先级排序（§4.3 + P7.2-10）。
"""

import time

import pytest

from memory_layer_testkit import memory_runtime  # noqa: F401  (autouse 夹具)

from agent.memory.taxonomy import (
    GLOBAL_SCOPE,
    LAYER_POLICY,
    MEMORY_SCHEMA_VERSION,
    MemoryEntry,
    MemoryEntryError,
    MemoryType,
    PROJECT_SCOPE_PREFIX,
    coerce_memory_type,
    default_scope_for,
    expiry_for,
    is_project_scope,
    new_memory_id,
    project_scope,
    recall_priority_key,
    scope_kind,
    scope_workspace_id,
    sort_for_recall,
    ttl_seconds_for,
)

#: §3.11 MemoryEntry 的字段集合（逐字对齐，改动即破坏契约）
SECTION_3_11_FIELDS = {
    "id", "tenant_id", "subject_id", "type", "content_redacted", "content_hash",
    "scope", "source_task_id", "confidence", "created_at", "last_hit_at",
    "ttl_expires_at", "forget_candidate", "schema_version",
}


def _entry(**overrides):
    params = {
        "id": "mem_fixed",
        "tenant_id": "ws_tenantA",
        "subject_id": "alice",
        "type": "fact",
        "content_redacted": "项目使用 pytest",
        "content_hash": "",
        "scope": project_scope("ws_tenantA"),
        "created_at": 1_000_000.0,
    }
    params.update(overrides)
    return MemoryEntry(**params)


# ════════════════════════════════════════════════════════════
#  1. 四层枚举与层策略
# ════════════════════════════════════════════════════════════


class TestFourLayers:
    def test_four_layers_exact_values(self):
        assert [m.value for m in MemoryType] == [
            "working", "fact", "preference", "strategy"]

    def test_coerce_accepts_str_and_enum(self):
        assert coerce_memory_type("fact") is MemoryType.FACT
        assert coerce_memory_type(" FACT ") is MemoryType.FACT
        assert coerce_memory_type(MemoryType.STRATEGY) is MemoryType.STRATEGY

    def test_coerce_unknown_raises(self):
        with pytest.raises(MemoryEntryError):
            coerce_memory_type("episodic")

    def test_layer_policy_covers_all_layers(self):
        assert set(LAYER_POLICY) == set(MemoryType)

    def test_fact_and_strategy_are_tenant_isolated(self):
        assert LAYER_POLICY[MemoryType.FACT].tenant_isolated is True
        assert LAYER_POLICY[MemoryType.STRATEGY].tenant_isolated is True
        assert LAYER_POLICY[MemoryType.WORKING].tenant_isolated is True

    def test_preference_is_subject_carried_across_tenants(self):
        policy = LAYER_POLICY[MemoryType.PREFERENCE]
        assert policy.carried_across_tenants is True
        assert policy.tenant_isolated is False
        assert policy.scope_kind == "global"

    def test_strategy_layer_forbids_subject_write(self):
        policy = LAYER_POLICY[MemoryType.STRATEGY]
        assert policy.subject_write_forbidden is True
        assert policy.org_level_default is True

    def test_injection_rank_follows_p7_2_10(self):
        ranks = {m: LAYER_POLICY[m].injection_rank for m in MemoryType}
        assert ranks[MemoryType.STRATEGY] < ranks[MemoryType.FACT]
        assert ranks[MemoryType.FACT] < ranks[MemoryType.PREFERENCE]
        assert ranks[MemoryType.PREFERENCE] < ranks[MemoryType.WORKING]

    def test_working_ttl_shorter_than_fact_and_strategy(self):
        working = LAYER_POLICY[MemoryType.WORKING].default_ttl_seconds
        fact = LAYER_POLICY[MemoryType.FACT].default_ttl_seconds
        strategy = LAYER_POLICY[MemoryType.STRATEGY].default_ttl_seconds
        assert working < fact
        assert working < strategy
        assert fact >= 180 * 86400


# ════════════════════════════════════════════════════════════
#  2. 作用域
# ════════════════════════════════════════════════════════════


class TestScope:
    def test_project_scope_format(self):
        assert project_scope("ws_abc") == PROJECT_SCOPE_PREFIX + "ws_abc"
        assert is_project_scope("project:ws_abc") is True
        assert is_project_scope(GLOBAL_SCOPE) is False

    def test_project_scope_requires_workspace(self):
        with pytest.raises(MemoryEntryError):
            project_scope("   ")

    def test_scope_workspace_id_roundtrip(self):
        assert scope_workspace_id(project_scope("ws_abc")) == "ws_abc"
        assert scope_workspace_id(GLOBAL_SCOPE) is None

    def test_scope_kind_classification(self):
        assert scope_kind(project_scope("ws_abc")) == "project"
        assert scope_kind(GLOBAL_SCOPE) == "global"
        assert scope_kind("") == "global"
        assert scope_kind("org:weird") == "global"

    def test_default_scope_preference_is_always_global(self):
        assert default_scope_for(MemoryType.PREFERENCE, "ws_abc") == GLOBAL_SCOPE

    def test_default_scope_fact_needs_workspace(self):
        assert default_scope_for(MemoryType.FACT, "ws_abc") == "project:ws_abc"
        assert default_scope_for(MemoryType.FACT, "") == GLOBAL_SCOPE


# ════════════════════════════════════════════════════════════
#  3. §3.11 条目模型
# ════════════════════════════════════════════════════════════


class TestMemoryEntryModel:
    def test_section_3_11_fields_all_present(self):
        data = _entry().to_dict()
        assert SECTION_3_11_FIELDS <= set(data)

    def test_schema_version_default(self):
        assert _entry().schema_version == MEMORY_SCHEMA_VERSION == 1

    def test_minimal_section_3_11_construction_is_valid(self):
        """按 §3.11 最小集（不含云枢扩展字段）构造必须合法"""
        entry = MemoryEntry(
            id="mem_min", tenant_id="ws_a", subject_id="u1", type="preference",
            content_redacted="偏好 tabs", content_hash="", scope="global",
        )
        assert entry.type is MemoryType.PREFERENCE
        assert entry.content_hash  # 自动补齐
        assert entry.ttl_expires_at is None

    def test_content_hash_derived_from_redacted_content(self):
        from agent.observability.trace_v2 import hash_content

        entry = _entry(content_redacted="已脱敏文本")
        assert entry.content_hash == hash_content("已脱敏文本")

    def test_content_hash_is_not_of_raw_content(self):
        """§3.4：脱敏发生在哈希之前 —— 哈希必须源自脱敏文本"""
        from agent.observability.trace_v2 import hash_content

        entry = _entry(content_redacted="手机号 ********")
        assert entry.content_hash != hash_content("手机号 13812345678")

    def test_unknown_type_raises(self):
        with pytest.raises(MemoryEntryError):
            _entry(type="episodic")

    def test_malformed_project_scope_raises(self):
        with pytest.raises(MemoryEntryError):
            _entry(scope="project:")

    def test_empty_scope_falls_back_to_layer_default(self):
        entry = _entry(scope="", type="preference", tenant_id="ws_a")
        assert entry.scope == GLOBAL_SCOPE
        entry2 = _entry(scope="", type="fact", tenant_id="ws_a")
        assert entry2.scope == "project:ws_a"

    def test_confidence_clamped_to_unit_interval(self):
        assert _entry(confidence=5).confidence == 1.0
        assert _entry(confidence=-1).confidence == 0.0
        assert _entry(confidence="bogus").confidence == 0.5

    def test_auto_id_when_missing(self):
        entry = _entry(id="")
        assert entry.id.startswith("mem_")
        assert len(entry.id) == len("mem_") + 16

    def test_roundtrip_to_from_dict(self):
        original = _entry(
            type="strategy", org_level=True, confidence=0.42,
            source_capability_id="cp.fs.write", source_task_id="task-1",
            ttl_expires_at=2_000_000.0, forget_candidate=True,
            forget_reason="ttl_expired", hit_count=3, extra={"k": "v"},
        )
        restored = MemoryEntry.from_dict(original.to_dict())
        assert restored.to_dict() == original.to_dict()

    def test_from_dict_keeps_unknown_keys_in_extra(self):
        restored = MemoryEntry.from_dict({
            "id": "mem_x", "tenant_id": "ws_a", "subject_id": "u", "type": "fact",
            "content_redacted": "c", "content_hash": "", "scope": "project:ws_a",
            "future_field": "keep-me",
        })
        assert restored.extra["future_field"] == "keep-me"

    def test_isolation_properties_per_layer(self):
        assert _entry(type="fact").tenant_isolated is True
        assert _entry(type="fact").carried_across_tenants is False
        assert _entry(type="preference", scope="global").carried_across_tenants is True

    def test_to_audit_leaf_excludes_content_and_raw_subject(self):
        """审计叶子**不得**含内容与原始 subject_id（§8 被遗忘权可执行的前提）"""
        entry = _entry(content_redacted="机密：alice 的偏好", subject_id="alice")
        leaf = entry.to_audit_leaf()
        blob = repr(leaf)
        assert "机密" not in blob
        assert "alice" not in blob
        assert leaf["content_hash"] == entry.content_hash
        assert leaf["memory_id"] == entry.id
        assert "content_redacted" not in leaf
        assert "subject_id" not in leaf


class TestMemoryEntryLifecycle:
    def test_record_hit_updates_last_hit_and_count(self):
        entry = _entry()
        entry.record_hit(now=1_500_000.0)
        assert entry.last_hit_at == 1_500_000.0
        assert entry.hit_count == 1

    def test_mark_and_clear_forget_candidate(self):
        entry = _entry()
        entry.mark_forget_candidate("success_rate_below_baseline")
        assert entry.forget_candidate is True
        assert entry.forget_reason == "success_rate_below_baseline"
        entry.clear_forget_candidate()
        assert entry.forget_candidate is False
        assert entry.forget_reason == ""

    def test_is_expired_respects_injected_now(self):
        entry = _entry(ttl_expires_at=1_000_100.0)
        assert entry.is_expired(now=1_000_099.0) is False
        assert entry.is_expired(now=1_000_100.0) is True

    def test_never_expires_when_ttl_is_none(self):
        assert _entry(ttl_expires_at=None).is_expired(now=time.time() + 10**9) is False

    def test_age_seconds_uses_injected_now(self):
        assert _entry(created_at=1_000_000.0).age_seconds(now=1_000_060.0) == 60.0

    def test_with_updates_returns_new_object(self):
        entry = _entry(confidence=0.8)
        other = entry.with_updates(confidence=0.1)
        assert other is not entry
        assert entry.confidence == 0.8
        assert other.confidence == 0.1


# ════════════════════════════════════════════════════════════
#  4. TTL 配置（可调 + 非法值回退）
# ════════════════════════════════════════════════════════════


class TestTTLPolicy:
    def test_defaults_per_layer(self):
        assert ttl_seconds_for("working") == 8 * 3600.0
        assert ttl_seconds_for("fact") == 180 * 86400.0
        assert ttl_seconds_for("preference") == 365 * 86400.0
        assert ttl_seconds_for("strategy") == 365 * 86400.0

    def test_explicit_override_wins(self, monkeypatch):
        monkeypatch.setenv("MEMORY_TTL_FACT", "12345")
        assert ttl_seconds_for("fact", override=42.0) == 42.0

    def test_env_override_applies(self, monkeypatch):
        monkeypatch.setenv("MEMORY_TTL_WORKING", "60")
        assert ttl_seconds_for("working") == 60.0

    def test_env_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("MEMORY_TTL_WORKING", "not-a-number")
        assert ttl_seconds_for("working") == 8 * 3600.0

    def test_env_non_positive_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("MEMORY_TTL_WORKING", "0")
        assert ttl_seconds_for("working") == 8 * 3600.0
        monkeypatch.setenv("MEMORY_TTL_WORKING", "-5")
        assert ttl_seconds_for("working") == 8 * 3600.0

    def test_override_zero_means_never_expire(self, monkeypatch):
        assert ttl_seconds_for("fact", override=0) is None

    def test_expiry_for_uses_created_at(self):
        assert expiry_for("fact", created_at=1_000_000.0) == 1_000_000.0 + 180 * 86400.0
        assert expiry_for("fact", ttl_seconds=0, created_at=1_000_000.0) is None


# ════════════════════════════════════════════════════════════
#  5. 召回优先级（§4.3 + P7.2-10）
# ════════════════════════════════════════════════════════════


class TestRecallPriority:
    def test_strategy_fact_preference_working_order(self):
        entries = [
            _entry(id="working", type="working", scope="project:ws_a"),
            _entry(id="pref", type="preference", scope="global"),
            _entry(id="fact", type="fact", scope="project:ws_a"),
            _entry(id="strat", type="strategy", scope="project:ws_a"),
        ]
        assert [e.id for e in sort_for_recall(entries)] == [
            "strat", "fact", "pref", "working"]

    def test_project_fact_beats_global_preference(self):
        """§4.3 原文口径：project 事实 > global 偏好"""
        fact = _entry(id="fact", type="fact", scope="project:ws_a")
        pref = _entry(id="pref", type="preference", scope="global")
        assert recall_priority_key(fact) < recall_priority_key(pref)

    def test_project_beats_global_within_same_layer(self):
        project = _entry(id="p", type="fact", scope="project:ws_a")
        glob = _entry(id="g", type="fact", scope="global")
        assert recall_priority_key(project) < recall_priority_key(glob)

    def test_newer_wins_at_same_level(self):
        older = _entry(id="older", created_at=1_000.0)
        newer = _entry(id="newer", created_at=2_000.0)
        assert [e.id for e in sort_for_recall([older, newer])] == ["newer", "older"]

    def test_same_timestamp_is_deterministic_by_id(self):
        a = _entry(id="mem_a", created_at=1_000.0)
        b = _entry(id="mem_b", created_at=1_000.0)
        assert [e.id for e in sort_for_recall([b, a])] == ["mem_a", "mem_b"]
        assert [e.id for e in sort_for_recall([a, b])] == ["mem_a", "mem_b"]

    def test_sort_for_recall_does_not_mutate_input(self):
        older = _entry(id="older", created_at=1_000.0)
        newer = _entry(id="newer", created_at=2_000.0)
        source = [older, newer]
        sort_for_recall(source)
        assert [e.id for e in source] == ["older", "newer"]

    def test_entry_method_matches_function(self):
        entry = _entry()
        assert entry.recall_priority_key() == recall_priority_key(entry)

    def test_new_memory_id_is_unique_and_prefixed(self):
        ids = {new_memory_id() for _ in range(50)}
        assert len(ids) == 50
        assert all(i.startswith("mem_") for i in ids)
