"""TASK-S2-03 events.v1 信封 + 统一事件出口 单元测试

覆盖范围（对齐任务书 §四 验收清单）：
- EventEnvelope 字段逐字对齐 §3.6（v/event_id/ts/correlation_id/actor/type/payload）
- event_id 幂等（内容派生 / 幂等键派生 / 重放不重复计数）
- 九事件 + §6.6 埋点事件枚举
- 单写者纪律（§5.5）/ 只读 store / 关闭后写入
- 载荷纪律：敏感键丢弃、超长截断、**度量字段不被误伤**
- 读取：按日/类型/关联 id 过滤、跨分片去重、坏行跳过
- 跨日归档（复用 `log_archiver`）
- 审计镜像（治理类事件 → S2-02 链式台账）
"""

import json
import os
import threading

import pytest

from agent.observability import events as ev
from agent.observability.events import (
    ALL_EVENT_TYPES,
    CORE_EVENT_TYPES,
    ENVELOPE_FIELDS,
    GOVERNANCE_EVENT_TYPES,
    METRIC_EVENT_TYPES,
    NINE_EVENT_TYPES,
    SCHEMA_NAME,
    SCHEMA_VERSION,
    EventEnvelope,
    EventEnvelopeError,
    EventStore,
    EventType,
    EventTypeError,
    ReadOnlyEventStoreError,
    SingleWriterViolationError,
    build_event_id,
    get_event_store,
    sanitize_payload,
)


@pytest.fixture(autouse=True)
def _isolated_events(tmp_path, monkeypatch):
    """每个测试独立事件目录 + 清空进程单例（互不污染）"""
    monkeypatch.setenv(ev.ENV_DIR, str(tmp_path / "events"))
    monkeypatch.delenv(ev.ENV_ENABLED, raising=False)
    monkeypatch.delenv(ev.ENV_AUDIT_MIRROR, raising=False)
    ev.reset_event_stores()
    yield
    ev.reset_event_stores()


def _active(tmp_path) -> str:
    return os.path.join(str(tmp_path / "events"), ev.ACTIVE_FILENAME)


# ════════════════════════════════════════════════════════════
#  1. 信封字段与序列化
# ════════════════════════════════════════════════════════════


class TestEnvelope:
    def test_dict_keys_are_exactly_spec_fields(self):
        env = EventEnvelope(type="tool.called", payload={"a": 1})
        assert tuple(env.to_dict().keys()) == ENVELOPE_FIELDS
        assert set(env.to_dict()) == {"v", "event_id", "ts", "correlation_id",
                                      "actor", "type", "payload"}

    def test_default_schema_version_and_name(self):
        env = EventEnvelope(type="cost")
        assert env.v == SCHEMA_VERSION == 1
        assert SCHEMA_NAME == "events.v1"
        assert env.type == "cost"

    def test_ts_is_iso_with_offset(self):
        env = EventEnvelope(type="cost")
        assert len(env.ts) >= 19 and env.ts[4] == "-" and env.ts[7] == "-"
        assert ("+" in env.ts[10:]) or ("-" in env.ts[10:]) or env.ts.endswith("Z")
        assert env.day == env.ts[:10]

    def test_actor_defaults_to_system(self):
        assert EventEnvelope(type="cost").actor == "system"
        assert EventEnvelope(type="cost", actor="human").actor == "human"

    def test_correlation_defaults_to_unlinked(self):
        assert EventEnvelope(type="cost").correlation_id == "unlinked"

    def test_empty_type_rejected(self):
        with pytest.raises(EventEnvelopeError):
            EventEnvelope(type="")

    def test_bad_type_shape_rejected(self):
        for bad in ("Tool.Called", "tool called", "tool..called", "tool.called!"):
            with pytest.raises(EventEnvelopeError):
                EventEnvelope(type=bad)

    def test_single_segment_and_dotted_types_accepted(self):
        assert EventEnvelope(type="approval").type == "approval"
        assert EventEnvelope(type="model.degraded").type == "model.degraded"
        assert EventEnvelope(type="task.closed").type == "task.closed"

    def test_payload_must_be_dict(self):
        with pytest.raises(EventEnvelopeError):
            EventEnvelope(type="cost", payload=[1, 2])

    def test_to_json_roundtrip(self):
        env = EventEnvelope(type="cost", payload={"n": 1}, actor="auto",
                            correlation_id="t1")
        restored = EventEnvelope.from_dict(json.loads(env.to_json()))
        assert restored.to_dict() == env.to_dict()

    def test_from_dict_tolerates_missing_fields(self):
        env = EventEnvelope.from_dict({"type": "escape", "payload": None})
        assert env.type == "escape" and env.payload == {}
        assert env.event_id and env.ts

    def test_enum_values_are_normalized(self):
        env = EventEnvelope(type=EventType.COST)
        assert env.type == "cost"
        assert ev.normalize_type(EventType.COST) == "cost"
        assert ev.normalize_type("cost") == "cost"
        assert ev.normalize_type(None) == ""


# ════════════════════════════════════════════════════════════
#  2. 事件类型枚举（§3.6 八 + P7.1-18 第九 + §6.6 埋点）
# ════════════════════════════════════════════════════════════


class TestEventTypes:
    def test_core_eight_events(self):
        assert CORE_EVENT_TYPES == (
            "tool.called", "digest.stage", "skill.generated", "approval.required",
            "healing.triggered", "metrics.delta", "policy.denied", "backup.health")

    def test_nine_events_include_model_degraded(self):
        assert len(NINE_EVENT_TYPES) == 9
        assert "model.degraded" in NINE_EVENT_TYPES
        assert NINE_EVENT_TYPES[:-1] == CORE_EVENT_TYPES

    def test_metric_events_cover_acr_utc_checklist(self):
        assert set(METRIC_EVENT_TYPES) == {
            "task.closed", "task.abandoned", "approval", "escape",
            "intervention", "cost"}

    def test_governance_events_cover_s8_01(self):
        # S8-01 数据生命周期治理埋点：**新增分组**，不改上面三组的冻结语义
        assert GOVERNANCE_EVENT_TYPES == ("retention.run",)

    def test_all_types_contains_everything(self):
        assert set(ALL_EVENT_TYPES) == (set(NINE_EVENT_TYPES)
                                       | set(METRIC_EVENT_TYPES)
                                       | set(GOVERNANCE_EVENT_TYPES))
        assert EventType.isin("cost") and EventType.isin("model.degraded")
        assert EventType.isin("retention.run")
        assert not EventType.isin("nope")
        assert set(EventType.values()) == set(ALL_EVENT_TYPES)
        assert EventType.COST == "cost"

    def test_audit_mirror_set_is_governance_only(self):
        # approval.required 不镜像：审批状态机已直接写链（S2-02），镜像会造成重复留痕
        assert ev.AUDIT_MIRROR_TYPES == frozenset({
            "policy.denied", "healing.triggered", "model.degraded", "escape"})
        assert "approval.required" not in ev.AUDIT_MIRROR_TYPES


# ════════════════════════════════════════════════════════════
#  3. event_id 幂等
# ════════════════════════════════════════════════════════════


class TestIdempotency:
    def test_same_content_same_id(self):
        a = build_event_id("cost", "auto", "t1", {"x": 1})
        b = build_event_id("cost", "auto", "t1", {"x": 1})
        assert a == b and a.startswith("ev_") and len(a) == 35

    def test_key_order_irrelevant(self):
        a = build_event_id("cost", "auto", "t1", {"x": 1, "y": 2})
        b = build_event_id("cost", "auto", "t1", {"y": 2, "x": 1})
        assert a == b

    def test_content_change_changes_id(self):
        a = build_event_id("cost", "auto", "t1", {"x": 1})
        b = build_event_id("cost", "auto", "t1", {"x": 2})
        assert a != b

    def test_actor_and_correlation_participate(self):
        base = build_event_id("cost", "auto", "t1", {"x": 1})
        assert build_event_id("cost", "human", "t1", {"x": 1}) != base
        assert build_event_id("cost", "auto", "t2", {"x": 1}) != base

    def test_explicit_key_ignores_payload(self):
        a = build_event_id("cost", "auto", "t1", {"x": 1}, idempotency_key="k")
        b = build_event_id("cost", "auto", "t1", {"x": 999}, idempotency_key="k")
        assert a == b

    def test_replay_not_written_twice(self, tmp_path):
        store = EventStore(_active(tmp_path))
        assert store.emit("cost", {"n": 1}, correlation_id="t1") is not None
        assert store.emit("cost", {"n": 1}, correlation_id="t1") is None
        assert store.stats()["duplicate_count"] == 1
        assert len(ev.read_events(directory=str(tmp_path / "events"))) == 1

    def test_explicit_key_allows_distinct_payloads_once(self, tmp_path):
        store = EventStore(_active(tmp_path))
        assert store.emit("cost", {"n": 1}, idempotency_key="llm:1") is not None
        assert store.emit("cost", {"n": 2}, idempotency_key="llm:1") is None
        assert store.emit("cost", {"n": 3}, idempotency_key="llm:2") is not None

    def test_index_survives_restart(self, tmp_path):
        path = _active(tmp_path)
        first = EventStore(path)
        first.emit("cost", {"n": 1}, correlation_id="t1")
        first.close()                      # 模拟进程重启（释放单写者登记）
        fresh = EventStore(path)
        assert fresh.emit("cost", {"n": 1}, correlation_id="t1") is None
        assert fresh.stats()["duplicate_count"] == 1

    def test_reader_dedupes_across_shards(self, tmp_path):
        base = str(tmp_path / "events")
        os.makedirs(base, exist_ok=True)
        env = EventEnvelope(type="cost", payload={"n": 1}, correlation_id="t1")
        for name in ("events-2026-09-01.jsonl", ev.ACTIVE_FILENAME):
            with open(os.path.join(base, name), "a", encoding="utf-8") as fh:
                fh.write(env.to_json() + "\n")
        assert len(ev.read_events(directory=base, dedupe=True)) == 1
        assert len(ev.read_events(directory=base, dedupe=False)) == 2


# ════════════════════════════════════════════════════════════
#  4. 载荷纪律（sanitize）
# ════════════════════════════════════════════════════════════


class TestPayloadDiscipline:
    def test_secret_keys_dropped(self):
        out = sanitize_payload({"password": "p", "api_key": "k", "secret": "s",
                                "access_token": "t", "authorization": "a",
                                "cookie": "c", "private_key": "pk",
                                "api_key_value": "v"})
        assert all(str(v).startswith("[已丢弃") for v in out.values())

    def test_metric_fields_never_mangled(self):
        payload = {"tokens_in": 10, "tokens_out": 5, "billable_tokens_in": 10,
                   "token_count": 15, "billable_tokens_total": 15,
                   "cost_normalized_cents": 1.5, "cache_hit": True}
        assert sanitize_payload(payload) == payload

    def test_hex_digest_not_masked(self):
        digest = "a" * 64
        assert sanitize_payload({"sha256": digest})["sha256"] == digest

    def test_long_string_truncated(self):
        out = sanitize_payload({"reason": "x" * 1000})
        assert len(out["reason"]) == ev.MAX_STR_LEN + 1
        assert out["reason"].endswith("…")

    def test_nested_and_sequence_payloads(self):
        out = sanitize_payload({"a": [{"secret": "s", "k": 1}], "b": {"password": "p"}})
        assert out["a"][0]["secret"].startswith("[已丢弃")
        assert out["a"][0]["k"] == 1
        assert out["b"]["password"].startswith("[已丢弃")

    def test_envelope_sanitizes_on_construction(self):
        env = EventEnvelope(type="cost", payload={"api_key": "sk-test-x", "n": 2})
        assert env.payload["api_key"].startswith("[已丢弃")
        assert env.payload["n"] == 2


# ════════════════════════════════════════════════════════════
#  5. 出口（EventStore）纪律
# ════════════════════════════════════════════════════════════


class TestEventStore:
    def test_single_writer_violation(self, tmp_path):
        path = _active(tmp_path)
        first = EventStore(path)
        with pytest.raises(SingleWriterViolationError):
            EventStore(path)
        first.close()

    def test_reader_does_not_hold_writer_slot(self, tmp_path):
        path = _active(tmp_path)
        writer = EventStore(path)
        reader = EventStore.reader(path)
        assert isinstance(reader, EventStore)
        writer.close()

    def test_reader_append_rejected(self, tmp_path):
        reader = EventStore.reader(_active(tmp_path))
        with pytest.raises(ReadOnlyEventStoreError):
            reader.append(EventEnvelope(type="cost"))

    def test_disabled_store_writes_nothing(self, tmp_path):
        store = EventStore(_active(tmp_path), enabled=False)
        assert store.emit("cost", {"n": 1}) is None
        assert not os.path.exists(_active(tmp_path))

    def test_strict_unknown_type_raises(self, tmp_path):
        store = EventStore(_active(tmp_path), strict=True)
        with pytest.raises(EventTypeError):
            store.append(EventEnvelope(type="custom.thing"))

    def test_non_strict_unknown_type_counted(self, tmp_path):
        store = EventStore(_active(tmp_path))
        assert store.emit("custom.thing", {"n": 1}) is not None
        assert store.stats()["unknown_type_count"] == 1

    def test_closed_store_rejects_append(self, tmp_path):
        store = EventStore(_active(tmp_path))
        store.close()
        with pytest.raises(ev.EventError):
            store.append(EventEnvelope(type="cost"))

    def test_concurrent_appends_unique_ids(self, tmp_path):
        store = EventStore(_active(tmp_path))
        errors = []

        def worker(idx):
            try:
                for j in range(20):
                    store.emit("cost", {"i": idx, "j": j},
                               correlation_id=f"w{idx}",
                               idempotency_key=f"{idx}:{j}")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        rows = ev.read_events(directory=str(tmp_path / "events"))
        assert len(rows) == 120
        assert len({r.event_id for r in rows}) == 120

    def test_intervention_tally_per_correlation(self, tmp_path):
        store = EventStore(_active(tmp_path))
        store.emit("intervention", {"kind": "approve"}, correlation_id="t1")
        store.emit("intervention", {"kind": "escape"}, correlation_id="t1")
        store.emit("intervention", {"kind": "view"}, correlation_id="t2")
        assert store.interventions_for("t1") == ["approve", "escape"]
        assert store.clear_interventions("t1") == ["approve", "escape"]
        assert store.interventions_for("t1") == []


# ════════════════════════════════════════════════════════════
#  6. 读取
# ════════════════════════════════════════════════════════════


class TestReading:
    def _seed(self, store):
        store.emit("task.closed", {"intent": "fix"}, correlation_id="t1", ts="2026-09-09T10:00:00.000+08:00")
        store.emit("cost", {"n": 1}, correlation_id="t1", ts="2026-09-09T11:00:00.000+08:00")
        store.emit("cost", {"n": 2}, correlation_id="t2", ts="2026-09-10T11:00:00.000+08:00")

    def test_filter_by_day(self, tmp_path):
        store = EventStore(_active(tmp_path))
        self._seed(store)
        rows = ev.read_events(day="2026-09-09", directory=str(tmp_path / "events"))
        assert [r.type for r in rows] == ["task.closed", "cost"]

    def test_filter_by_type_and_correlation(self, tmp_path):
        store = EventStore(_active(tmp_path))
        self._seed(store)
        base = str(tmp_path / "events")
        assert len(ev.read_events(types=["cost"], directory=base)) == 2
        assert len(ev.read_events(correlation_id="t2", directory=base)) == 1

    def test_window_bounds_inclusive(self, tmp_path):
        store = EventStore(_active(tmp_path))
        self._seed(store)
        base = str(tmp_path / "events")
        rows = ev.read_events(since="2026-09-10T00:00", directory=base)
        assert len(rows) == 1 and rows[0].payload["n"] == 2

    def test_corrupt_lines_skipped(self, tmp_path):
        store = EventStore(_active(tmp_path))
        store.emit("cost", {"n": 1})
        with open(_active(tmp_path), "a", encoding="utf-8") as fh:
            fh.write("{not json}\n\n")
        assert len(ev.read_events(directory=str(tmp_path / "events"))) == 1

    def test_group_by_day(self, tmp_path):
        store = EventStore(_active(tmp_path))
        self._seed(store)
        buckets = ev.group_by_day(ev.read_events(directory=str(tmp_path / "events")))
        assert sorted(buckets) == ["2026-09-09", "2026-09-10"]

    def test_event_files_lists_shards(self, tmp_path):
        base = tmp_path / "events"
        base.mkdir(parents=True, exist_ok=True)
        (base / ev.ACTIVE_FILENAME).write_text("", encoding="utf-8")
        (base / "events-2026-09-01.jsonl").write_text("", encoding="utf-8")
        assert len(ev.event_files(str(base))) == 2

    def test_shift_day_and_event_day(self):
        assert ev.shift_day("2026-09-10", -1) == "2026-09-09"
        assert ev.shift_day("bad", 1) == "bad"
        assert ev.event_day("2026-09-10T00:00:00+08:00") == "2026-09-10"
        assert ev.event_day("") == ""


# ════════════════════════════════════════════════════════════
#  7. 跨日归档 + 审计镜像
# ════════════════════════════════════════════════════════════


class TestArchiveAndMirror:
    def test_archive_moves_old_lines_to_shard(self, tmp_path):
        base = tmp_path / "events"
        base.mkdir(parents=True, exist_ok=True)
        store = EventStore(str(base / ev.ACTIVE_FILENAME))
        store.emit("cost", {"n": 1}, ts="2020-01-01T00:00:00+08:00")
        assert store.stats()["archived_total"] >= 1
        assert (base / "events-2020-01-01.jsonl").exists()
        rows = ev.read_events(directory=str(base))
        assert len(rows) == 1  # 归档后仍可读

    def test_archive_disabled_keeps_single_file(self, tmp_path):
        base = tmp_path / "events"
        base.mkdir(parents=True, exist_ok=True)
        store = EventStore(str(base / ev.ACTIVE_FILENAME), archive=False)
        store.emit("cost", {"n": 1}, ts="2020-01-01T00:00:00+08:00")
        assert store.stats()["archived_total"] == 0
        assert not (base / "events-2020-01-01.jsonl").exists()

    def test_escape_mirrored_to_audit_chain(self, tmp_path, monkeypatch):
        calls = []

        class _FakeAudit:
            def record(self, action, **kwargs):
                calls.append((action, kwargs))
                return object()

        import agent.audit as audit_pkg
        monkeypatch.setattr(audit_pkg, "audit", _FakeAudit(), raising=False)
        store = EventStore(_active(tmp_path), audit_mirror=True)
        store.emit("escape", {"path": "data/scheduled_tasks.json"}, correlation_id="t1")
        store.emit("cost", {"n": 1}, correlation_id="t1")
        assert [c[0] for c in calls] == ["escape"]
        assert calls[0][1]["extra"]["event_id"].startswith("ev_")

    def test_audit_mirror_can_be_disabled(self, tmp_path, monkeypatch):
        calls = []

        class _FakeAudit:
            def record(self, action, **kwargs):
                calls.append(action)

        import agent.audit as audit_pkg
        monkeypatch.setattr(audit_pkg, "audit", _FakeAudit(), raising=False)
        store = EventStore(_active(tmp_path), audit_mirror=False)
        store.emit("escape", {"path": "x"}, correlation_id="t1")
        assert calls == []

    def test_singleton_and_reader_singleton(self, tmp_path):
        assert get_event_store() is get_event_store()
        assert ev.get_event_reader() is ev.get_event_reader()
