"""TASK-S5-01 四层分片存储单测（`agent/memory/layered_store.py`）

覆盖：五类分片落点 / 租户隔离正反例 / 偏好跨租户携带 / 策略层不可污染 / 缺租户字段强制
拒绝或显式降级 / 内容脱敏先于哈希 / §4.3 召回优先级 / TTL 到期 / 遗忘支撑（标记+物理删除）
/ 只读操作零落盘（运行时目录零污染）。
"""

import os

import pytest

from memory_layer_testkit import (  # noqa: F401  (autouse 夹具)
    FakeClock,
    make_store,
    memory_runtime,
    project_root,
    tenancy,
)

from agent.memory.layered_store import (
    DEFAULT_LAYERS_ROOT,
    LayeredMemoryStore,
    load_layer_metadata,
    redact_content,
)
from agent.memory.identity import subject_ref
from agent.memory.taxonomy import MemoryEntry, MemoryType
from agent.memory.tenancy import MemoryWriteRejected, WriteChannel, TenancyPolicy

ROOT_A = "C:/repos/alpha"
ROOT_B = "C:/repos/beta"


@pytest.fixture
def store(tmp_path):
    return make_store(tmp_path)


def _shard_of(store, entry):
    return entry is not None and os.path.exists(store.owner_shard_path(entry))


# ════════════════════════════════════════════════════════════
#  1. 分片落点（物理分库）
# ════════════════════════════════════════════════════════════


class TestSharding:
    async def test_fact_lands_in_tenant_shard(self, store, tmp_path):
        result = await store.write("项目使用 pytest", memory_type="fact",
                                   workspace_root=ROOT_A)
        assert result.persisted is True
        assert os.path.exists(result.shard_path)
        assert "%stenants%s" % (os.sep, os.sep) in result.shard_path
        assert result.shard_path.endswith("fact.db")

    async def test_preference_lands_in_subject_shard(self, store):
        result = await store.write("偏好 tabs 缩进", memory_type="preference",
                                   workspace_root=ROOT_A, subject_id="alice")
        assert result.shard_path.endswith("preference.db")
        assert "%ssubjects%s" % (os.sep, os.sep) in result.shard_path

    async def test_working_lands_in_tenant_shard(self, store):
        result = await store.write("当前任务上下文", memory_type="working",
                                   workspace_root=ROOT_A)
        assert result.shard_path.endswith("working.db")

    async def test_org_strategy_lands_in_org_shard(self, store):
        result = await store.write("规避 rm -rf", memory_type="strategy",
                                   workspace_root=ROOT_A, channel=WriteChannel.ORG)
        assert result.entry.tenant_id == "__org__"
        assert result.entry.is_org_level is True
        assert result.shard_path.endswith(os.path.join("org", "strategy.db"))

    async def test_tenant_local_strategy_lands_in_tenant_shard(self, store):
        result = await store.write("租户内规避规则", memory_type="strategy",
                                   workspace_root=ROOT_A,
                                   channel=WriteChannel.SYSTEM, org_level=False)
        assert result.entry.is_org_level is False
        assert result.shard_path.endswith("strategy.db")
        assert "%sorg%s" % (os.sep, os.sep) not in result.shard_path

    async def test_shard_paths_exposes_all_reachable_shards(self, store):
        paths = store.shard_paths(tenancy(ROOT_A))
        assert set(paths) == {
            "org_strategy", "tenant_working", "tenant_fact",
            "tenant_strategy", "subject_preference"}

    async def test_distinct_workspaces_get_distinct_shards(self, store):
        a = await store.write("A 的事实", memory_type="fact", workspace_root=ROOT_A)
        b = await store.write("B 的事实", memory_type="fact", workspace_root=ROOT_B)
        assert a.shard_path != b.shard_path
        assert a.entry.tenant_id != b.entry.tenant_id

    def test_default_root_is_outside_project_tree(self):
        """默认落点在项目树之外（§8 P7.2-18；避免记忆数据被误提交）"""
        assert not os.path.abspath(DEFAULT_LAYERS_ROOT).startswith(project_root())


# ════════════════════════════════════════════════════════════
#  2. 租户隔离正反例（P7.2-08 铁律）
# ════════════════════════════════════════════════════════════


class TestTenantIsolation:
    async def test_same_tenant_fact_is_recalled(self, store):
        await store.write("项目使用 pytest 进行测试", memory_type="fact",
                         workspace_root=ROOT_A, subject_id="alice")
        hits = await store.recall("pytest", workspace_root=ROOT_A, subject_id="alice")
        assert [h.content_redacted for h in hits] == ["项目使用 pytest 进行测试"]

    async def test_cross_tenant_fact_is_invisible(self, store):
        """★ 反例证据：跨租户不可见"""
        await store.write("A 租户的机密事实", memory_type="fact", workspace_root=ROOT_A)
        assert await store.recall("机密事实", workspace_root=ROOT_B) == []
        assert await store.recall("机密事实", workspace_root=ROOT_A) != []

    async def test_cross_tenant_get_returns_none(self, store):
        result = await store.write("A 的事实", memory_type="fact", workspace_root=ROOT_A)
        assert await store.get(result.entry.id, workspace_root=ROOT_A) is not None
        assert await store.get(result.entry.id, workspace_root=ROOT_B) is None

    async def test_cross_tenant_working_memory_is_invisible(self, store):
        await store.write("A 的工作记忆", memory_type="working", workspace_root=ROOT_A)
        assert await store.recall("工作记忆", workspace_root=ROOT_B) == []

    async def test_cross_tenant_tenant_local_strategy_is_invisible(self, store):
        await store.write("A 的租户策略", memory_type="strategy", workspace_root=ROOT_A,
                          channel=WriteChannel.SYSTEM, org_level=False)
        assert await store.recall("租户策略", workspace_root=ROOT_B) == []
        assert await store.recall("租户策略", workspace_root=ROOT_A) != []

    async def test_list_entries_is_tenant_scoped(self, store):
        await store.write("A 的事实", memory_type="fact", workspace_root=ROOT_A)
        await store.write("B 的事实", memory_type="fact", workspace_root=ROOT_B)
        a_entries = await store.list_entries(workspace_root=ROOT_A)
        assert [e.content_redacted for e in a_entries] == ["A 的事实"]

    async def test_all_entries_is_governance_view_across_tenants(self, store):
        await store.write("A 的事实", memory_type="fact", workspace_root=ROOT_A)
        await store.write("B 的事实", memory_type="fact", workspace_root=ROOT_B)
        assert len(await store.all_entries()) == 2


# ════════════════════════════════════════════════════════════
#  3. 偏好跨租户携带 + 主体隔离
# ════════════════════════════════════════════════════════════


class TestPreferenceCarry:
    async def test_preference_is_carried_to_another_tenant(self, store):
        """★ 正例证据：同一 subject 换工作区仍召回（跨租户携带）"""
        await store.write("偏好使用 tabs 缩进", memory_type="preference",
                          workspace_root=ROOT_A, subject_id="alice")
        other = await store.recall("tabs 缩进", workspace_root=ROOT_B, subject_id="alice")
        assert [e.content_redacted for e in other] == ["偏好使用 tabs 缩进"]

    async def test_preference_is_invisible_to_another_subject(self, store):
        """★ 反例证据：偏好不得泄漏给同一租户的其他用户"""
        await store.write("alice 的私人偏好", memory_type="preference",
                          workspace_root=ROOT_A, subject_id="alice")
        assert await store.recall("私人偏好", workspace_root=ROOT_A, subject_id="bob") == []
        assert await store.recall("私人偏好", workspace_root=ROOT_B, subject_id="bob") == []

    async def test_preference_without_subject_is_rejected(self, store):
        with pytest.raises(MemoryWriteRejected) as exc:
            await store.write("无主体偏好", memory_type="preference",
                              workspace_root=ROOT_A, subject_id="")
        assert exc.value.decision.reason == "missing_subject_id"

    async def test_preference_scope_is_always_global(self, store):
        result = await store.write("偏好", memory_type="preference",
                                   workspace_root=ROOT_A, subject_id="alice")
        assert result.entry.scope == "global"
        assert result.entry.tenant_id == tenancy(ROOT_A).tenant_id


# ════════════════════════════════════════════════════════════
#  4. 策略层不可污染（铁律）
# ════════════════════════════════════════════════════════════


class TestStrategyLayerProtection:
    async def test_personal_write_to_strategy_layer_is_rejected(self, store):
        """★ 反例证据：个人写入不得污染企业策略记忆"""
        with pytest.raises(MemoryWriteRejected) as exc:
            await store.write("个人偏好试图写入策略层", memory_type="strategy",
                              workspace_root=ROOT_A, subject_id="alice")
        assert exc.value.decision.reason == "strategy_layer_readonly"
        assert await store.all_entries() == []

    async def test_preference_cannot_be_org_level(self, store):
        with pytest.raises(MemoryWriteRejected) as exc:
            await store.write("x", memory_type="preference", workspace_root=ROOT_A,
                              org_level=True)
        assert exc.value.decision.reason == "preference_cannot_be_org_level"

    async def test_org_strategy_is_readonly_for_personal_channel(self, store):
        result = await store.write("规避 rm -rf", memory_type="strategy",
                                   workspace_root=ROOT_A, channel=WriteChannel.ORG)
        with pytest.raises(MemoryWriteRejected):
            await store.update_entry(result.entry, channel=WriteChannel.USER)

    async def test_org_strategy_is_delivered_read_only_to_other_tenant(self, store):
        await store.write("规避 rm -rf 的企业规则", memory_type="strategy",
                          workspace_root=ROOT_A, channel=WriteChannel.ORG)
        hits = await store.recall("企业规则", workspace_root=ROOT_B, subject_id="bob")
        assert [e.is_org_level for e in hits] == [True]

    async def test_org_strategy_is_updateable_by_governance_channel(self, store):
        result = await store.write("初版企业规则", memory_type="strategy",
                                   workspace_root=ROOT_A, channel=WriteChannel.ORG)
        result.entry.content_redacted = "修订版企业规则"
        result.entry.content_hash = ""
        assert await store.update_entry(result.entry, channel=WriteChannel.ORG) is True


# ════════════════════════════════════════════════════════════
#  5. 强制 tenancy（拒绝 or 显式降级，绝不静默落全局）
# ════════════════════════════════════════════════════════════


class TestEnforcedTenancy:
    async def test_missing_tenant_is_rejected(self, store):
        with pytest.raises(MemoryWriteRejected) as exc:
            await store.write("无租户事实", memory_type="fact")
        assert exc.value.decision.reason == "missing_tenant_id"
        assert await store.all_entries() == []

    async def test_missing_workspace_is_rejected_for_project_layers(self, store):
        """只给 tenant_id 不给 workspace：拒绝，而不是静默降为 global 作用域"""
        with pytest.raises(MemoryWriteRejected) as exc:
            await store.write("无工作区事实", memory_type="fact", tenant_id="acme")
        assert exc.value.decision.reason == "missing_workspace_id"

    async def test_explicit_global_scope_is_allowed_for_tenant_wide_fact(self, store):
        result = await store.write("租户级事实", memory_type="fact",
                                   tenant_id="acme", scope="global")
        assert result.entry.scope == "global"
        assert result.persisted is True

    async def test_scope_workspace_mismatch_is_rejected(self, store):
        with pytest.raises(MemoryWriteRejected) as exc:
            await store.write("越界事实", memory_type="fact", workspace_root=ROOT_A,
                              scope="project:ws_someone_else")
        assert exc.value.decision.reason == "scope_workspace_mismatch"

    async def test_degrade_mode_quarantines_entry(self, tmp_path, store):
        """显式降级：落 __unscoped__ 隔离区并标注 degraded，默认召回不含"""
        policy = TenancyPolicy(allow_missing_tenancy=True)
        degraded_store = LayeredMemoryStore(
            root=str(tmp_path / "layers"), tenant_policy=policy, audit=False)
        result = await degraded_store.write("无租户上下文的事实", memory_type="fact")
        assert result.degraded is True
        assert result.entry.degraded is True
        assert result.entry.degradation_reason
        assert result.shard_path.endswith(os.path.join("degraded", "unscoped.db"))
        assert await degraded_store.recall(
            "无租户", workspace_root=ROOT_A) == []
        quarantined = await degraded_store.recall(
            "无租户", workspace_root=ROOT_A, include_degraded=True)
        assert [e.degraded for e in quarantined] == [True]

    async def test_rejected_write_creates_no_shard(self, tmp_path):
        fresh = LayeredMemoryStore(root=str(tmp_path / "fresh"), audit=False)
        with pytest.raises(MemoryWriteRejected):
            await fresh.write("无租户事实", memory_type="fact")
        # 拒绝路径不得创建任何落盘结构
        assert not os.path.exists(str(tmp_path / "fresh"))


# ════════════════════════════════════════════════════════════
#  6. 内容脱敏（§3.4 脱敏先于哈希）
# ════════════════════════════════════════════════════════════


class TestContentRedaction:
    def test_redact_content_is_importable_and_stable(self):
        assert redact_content("普通文本") == "普通文本"

    async def test_sensitive_content_is_redacted_before_storage(self, store):
        result = await store.write("用户手机号 13812345678 已记录", memory_type="fact",
                                   workspace_root=ROOT_A)
        stored = await store.get(result.entry.id, workspace_root=ROOT_A)
        assert "13812345678" not in stored.content_redacted
        assert "13812345678" not in result.entry.content_redacted

    async def test_content_hash_matches_redacted_text(self, store):
        from agent.observability.trace_v2 import hash_content

        result = await store.write("普通事实", memory_type="fact", workspace_root=ROOT_A)
        assert result.entry.content_hash == hash_content(result.entry.content_redacted)

    async def test_non_string_content_is_serialised(self, store):
        result = await store.write({"k": "v"}, memory_type="fact", workspace_root=ROOT_A)
        assert '"k"' in result.entry.content_redacted


# ════════════════════════════════════════════════════════════
#  7. 召回：优先级 / 过滤 / 限额
# ════════════════════════════════════════════════════════════


class TestRecallPriority:
    async def test_project_fact_outranks_global_preference(self, store):
        """§4.3：project 事实 > global 偏好"""
        await store.write("共用关键字 alpha 的偏好", memory_type="preference",
                          workspace_root=ROOT_A, subject_id="alice")
        await store.write("共用关键字 alpha 的事实", memory_type="fact",
                          workspace_root=ROOT_A, subject_id="alice")
        hits = await store.recall("alpha", workspace_root=ROOT_A, subject_id="alice")
        assert [e.type for e in hits] == [MemoryType.FACT, MemoryType.PREFERENCE]

    async def test_org_strategy_ranks_first(self, store):
        await store.write("关键字 beta 的事实", memory_type="fact", workspace_root=ROOT_A)
        await store.write("关键字 beta 的企业策略", memory_type="strategy",
                          workspace_root=ROOT_A, channel=WriteChannel.ORG)
        hits = await store.recall("beta", workspace_root=ROOT_A)
        assert hits[0].type is MemoryType.STRATEGY

    async def test_newer_wins_within_same_layer(self, store):
        await store.write("关键字 gamma 旧", memory_type="fact", workspace_root=ROOT_A,
                          entry_id="mem_old")
        await store.write("关键字 gamma 新", memory_type="fact", workspace_root=ROOT_A,
                          entry_id="mem_new")
        hits = await store.recall("gamma", workspace_root=ROOT_A)
        assert hits[0].content_redacted.endswith("新")

    async def test_recall_limit(self, store):
        for index in range(4):
            await store.write("关键字 delta %d" % index, memory_type="fact",
                              workspace_root=ROOT_A, entry_id="mem_%d" % index)
        assert len(await store.recall("delta", workspace_root=ROOT_A, limit=2)) == 2

    async def test_memory_types_filter(self, store):
        await store.write("关键字 epsilon 事实", memory_type="fact", workspace_root=ROOT_A)
        await store.write("关键字 epsilon 偏好", memory_type="preference",
                          workspace_root=ROOT_A, subject_id="alice")
        hits = await store.recall("epsilon", workspace_root=ROOT_A, subject_id="alice",
                                  memory_types=["preference"])
        assert [e.type for e in hits] == [MemoryType.PREFERENCE]

    async def test_empty_query_lists_recent(self, store):
        await store.write("任意事实", memory_type="fact", workspace_root=ROOT_A)
        assert len(await store.recall("", workspace_root=ROOT_A)) == 1

    async def test_record_hits_updates_last_hit(self, store):
        result = await store.write("关键字 zeta 事实", memory_type="fact",
                                   workspace_root=ROOT_A)
        await store.recall("zeta", workspace_root=ROOT_A, record_hits=True)
        refreshed = await store.get(result.entry.id, workspace_root=ROOT_A)
        assert refreshed.hit_count == 1
        assert refreshed.last_hit_at > 0

    async def test_write_upsert_is_idempotent_by_id(self, store):
        await store.write("初版", memory_type="fact", workspace_root=ROOT_A,
                          entry_id="mem_same")
        await store.write("改版", memory_type="fact", workspace_root=ROOT_A,
                          entry_id="mem_same")
        entries = await store.list_entries(workspace_root=ROOT_A)
        assert len(entries) == 1
        assert entries[0].content_redacted == "改版"

    async def test_read_only_operations_leave_no_files(self, tmp_path):
        """零运行时目录污染：只读召回不得创建分片文件"""
        fresh = LayeredMemoryStore(root=str(tmp_path / "never"), audit=False)
        assert await fresh.recall("anything", workspace_root=ROOT_A) == []
        assert await fresh.list_entries(workspace_root=ROOT_A) == []
        assert await fresh.get("mem_missing", workspace_root=ROOT_A) is None
        assert not os.path.exists(str(tmp_path / "never"))


# ════════════════════════════════════════════════════════════
#  8. TTL
# ════════════════════════════════════════════════════════════


class TestTTL:
    async def test_expired_entry_is_not_recalled_by_default(self, tmp_path):
        clock = FakeClock()
        store = make_store(tmp_path, clock=clock)
        await store.write("关键字 theta 工作记忆", memory_type="working",
                          workspace_root=ROOT_A, ttl_seconds=60)
        assert len(await store.recall("theta", workspace_root=ROOT_A)) == 1
        clock.advance(61)
        assert await store.recall("theta", workspace_root=ROOT_A) == []
        still_there = await store.recall(
            "theta", workspace_root=ROOT_A, include_expired=True)
        assert [e.is_expired(now=clock()) for e in still_there] == [True]

    async def test_never_expiring_entry_has_no_ttl(self, tmp_path):
        clock = FakeClock()
        store = make_store(tmp_path, clock=clock)
        result = await store.write("永不过期", memory_type="fact", workspace_root=ROOT_A,
                                   ttl_seconds=0)
        assert result.entry.ttl_expires_at is None
        clock.advance(10 ** 9)
        assert len(await store.recall("永不过期", workspace_root=ROOT_A)) == 1

    async def test_working_default_ttl_is_shorter_than_fact(self, tmp_path):
        clock = FakeClock()
        store = make_store(tmp_path, clock=clock)
        working = await store.write("w", memory_type="working", workspace_root=ROOT_A)
        fact = await store.write("f", memory_type="fact", workspace_root=ROOT_A)
        assert working.entry.ttl_expires_at < fact.entry.ttl_expires_at

    async def test_ttl_uses_injected_created_at(self, tmp_path):
        clock = FakeClock()
        store = make_store(tmp_path, clock=clock)
        result = await store.write("t", memory_type="fact", workspace_root=ROOT_A,
                                   ttl_seconds=100)
        assert result.entry.created_at == clock()
        assert result.entry.ttl_expires_at == clock() + 100


# ════════════════════════════════════════════════════════════
#  9. 遗忘支撑（标记持久化 / 物理删除 / 统计）
# ════════════════════════════════════════════════════════════


class TestForgettingSupport:
    async def test_mark_forget_candidate_is_persisted(self, tmp_path):
        store = make_store(tmp_path)
        result = await store.write("关键字 iota 事实", memory_type="fact",
                                   workspace_root=ROOT_A)
        assert await store.mark_forget_candidate(result.entry, "ttl_expired") is True
        reopened = make_store(tmp_path)
        reloaded = await reopened.get(result.entry.id, workspace_root=ROOT_A)
        assert reloaded.forget_candidate is True
        assert reloaded.forget_reason == "ttl_expired"

    async def test_clear_forget_candidate_is_persisted(self, tmp_path):
        store = make_store(tmp_path)
        result = await store.write("关键字 kappa 事实", memory_type="fact",
                                   workspace_root=ROOT_A)
        await store.mark_forget_candidate(result.entry, "source_deprecated")
        await store.clear_forget_candidate(result.entry)
        reopened = make_store(tmp_path)
        reloaded = await reopened.get(result.entry.id, workspace_root=ROOT_A)
        assert reloaded.forget_candidate is False
        assert reloaded.forget_reason == ""

    async def test_delete_entry_physically_removes_row(self, tmp_path):
        store = make_store(tmp_path)
        result = await store.write("关键字 lambda 事实", memory_type="fact",
                                   workspace_root=ROOT_A)
        assert await store.delete_entry(result.entry) is True
        reopened = make_store(tmp_path)
        assert await reopened.get(result.entry.id, workspace_root=ROOT_A) is None
        assert await reopened.all_entries() == []

    async def test_delete_missing_entry_returns_false(self, tmp_path):
        store = make_store(tmp_path)
        await store.write("x", memory_type="fact", workspace_root=ROOT_A)
        ghost = MemoryEntry(id="mem_ghost", tenant_id="ws_x", subject_id="",
                            type="fact", content_redacted="", content_hash="",
                            scope="global")
        assert await store.delete_entry(ghost) is False

    async def test_forget_candidates_can_be_excluded_from_recall(self, store):
        result = await store.write("关键字 mu 事实", memory_type="fact",
                                   workspace_root=ROOT_A)
        await store.mark_forget_candidate(result.entry, "ttl_expired")
        assert len(await store.recall(
            "mu", workspace_root=ROOT_A, include_forget_candidates=False)) == 0
        assert len(await store.recall("mu", workspace_root=ROOT_A)) == 1

    async def test_stats_reports_shards_and_entries(self, store):
        await store.write("关键字 nu 事实", memory_type="fact", workspace_root=ROOT_A)
        await store.write("关键字 nu 偏好", memory_type="preference",
                          workspace_root=ROOT_A, subject_id="alice")
        stats = store.stats()
        assert stats["shard_count"] >= 2
        assert stats["entry_count"] == 2
        assert stats["audit_enabled"] is False
        assert "identity" in stats

    async def test_iter_shard_files_excludes_identity_dir(self, store):
        await store.write("关键字 xi 事实", memory_type="fact", workspace_root=ROOT_A)
        files = store.iter_shard_files()
        assert files
        assert all("identity" not in path for path in files)


# ════════════════════════════════════════════════════════════
#  10. 审计叶子与元数据装载
# ════════════════════════════════════════════════════════════


class TestMetadataBridge:
    async def test_stored_metadata_round_trips_entry(self, store):
        result = await store.write("关键字 omicron 事实", memory_type="fact",
                                   workspace_root=ROOT_A, confidence=0.7,
                                   source_capability_id="cp.fs.write",
                                   source_task_id="task-9", extra={"origin": "unit"})
        reopened = load_layer_metadata(result.entry.to_persist_metadata())
        assert reopened.to_dict() == result.entry.to_dict()
        assert reopened.extra == {"origin": "unit"}
        assert reopened.source_capability_id == "cp.fs.write"

    def test_load_layer_metadata_tolerates_non_layered_payloads(self):
        assert load_layer_metadata(None) is None
        assert load_layer_metadata("") is None
        assert load_layer_metadata("not json") is None
        assert load_layer_metadata({"key": "legacy"}) is None
        assert load_layer_metadata({"id": "x", "type": "nope"}) is None

    def test_load_layer_metadata_parses_json_text_column(self):
        entry = MemoryEntry(id="mem_t", tenant_id="ws_a", subject_id="u", type="fact",
                            content_redacted="c", content_hash="", scope="project:ws_a")
        import json as _json

        loaded = load_layer_metadata(_json.dumps(entry.to_dict()))
        assert loaded.id == "mem_t"

    async def test_legacy_rows_are_ignored_by_layered_recall(self, store, tmp_path):
        """既有 LongTermMemory 的普通条目（非分层）不得被分层召回纳入"""
        from agent.memory.long_term_memory import LongTermMemory

        legacy = LongTermMemory(db_path=str(tmp_path / "legacy.db"))
        await legacy.save(key="legacy-1", content="关键字 pi 的旧记忆", importance=3)
        assert await store.recall("pi", workspace_root=ROOT_A) == []

    async def test_subject_ref_is_pseudonymous(self):
        ref = subject_ref("alice")
        assert ref.startswith("subj-")
        assert "alice" not in ref
