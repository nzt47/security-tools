"""TASK-S2-02 统一审计门面单测（`agent/audit/facade.py`）

覆盖：audit.record 语义 / actor 解析与来源标注 / 脱敏先于入链 / UI 与 Agent 同表 /
best-effort 与严格模式 / Trace 关键事件接线 / 单例与懒加载 / 状态快照。
"""
from __future__ import annotations

import json
import os

import pytest

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain, reset_audit_chains
from agent.audit.facade import (
    AuditFacade,
    get_audit,
    get_ui_context,
    record,
    redact_payload,
    reset_ui_actor,
    set_ui_actor,
)


@pytest.fixture
def chain(tmp_path):
    reset_audit_chains()
    c = AuditChain(str(tmp_path / "audit_chain.db"),
                   roots_path=str(tmp_path / "roots.jsonl"),
                   signing_key_path=str(tmp_path / "k.pem"), auto_seal=False)
    yield c
    c.close(timeout=2.0)
    reset_audit_chains()


@pytest.fixture
def facade(chain):
    return AuditFacade(chain=chain, enabled=True, db_path=chain.db_path)


@pytest.fixture
def bound_facade(chain):
    """把进程级门面临时绑定到测试台账（Agent 侧接线走的就是它）"""
    previous = facade_mod.audit.bind(chain)
    old_enabled = facade_mod.audit.enabled
    facade_mod.audit.enabled = True
    facade_mod.audit.reset_counters()
    yield facade_mod.audit
    facade_mod.audit.bind(previous)
    facade_mod.audit.enabled = old_enabled


# ════════════════════════════════════════════════════════════
#  1. 基本写入与来源
# ════════════════════════════════════════════════════════════


class TestRecordBasics:
    def test_record_returns_entry_in_chain(self, facade, chain):
        e = facade.record("skill.delete", actor="alice", subject="skill:a",
                          payload={"k": 1})
        assert e is not None and e.seq == 1
        assert chain.get(e.seq).action == "skill.delete"

    def test_record_marks_actor_source_explicit(self, facade):
        e = facade.record("act", actor="alice")
        assert e.actor == "alice"
        assert e.payload["actor_source"] == "explicit"

    def test_record_without_actor_falls_back_to_system(self, facade):
        e = facade.record("act")
        assert e.actor == "system"
        assert e.payload["actor_source"] == "default_system"

    def test_record_ui_source(self, facade):
        e = facade.record_ui("skill.delete", actor="admin", subject="skill:a")
        assert e.source == "ui"

    def test_record_agent_source(self, facade):
        e = facade.record_agent("approval.approve", actor="reviewer")
        assert e.source == "agent"

    def test_invalid_source_coerced_to_agent(self, facade):
        e = facade.record("act", actor="a", source="not-a-source")
        assert e.source == "agent"

    def test_record_uses_ui_context_actor(self, facade):
        tokens = set_ui_actor("ui-user-1", endpoint="skills.delete")
        try:
            e = facade.record("act", source="ui")
            assert e.actor == "ui-user-1"
            assert e.payload["actor_source"] == "ui_request_context"
            assert get_ui_context()["endpoint"] == "skills.delete"
        finally:
            reset_ui_actor(tokens)

    def test_record_uses_trace_context_subject(self, facade):
        from agent.observability.trace_v2 import TraceContext
        ctx = TraceContext(trace_id="tid-1", task_id="task-1", workspace_id="ws-1",
                           subject_id="operator-x")
        token = ctx.enter()
        try:
            e = facade.record("act")
            assert e.actor == "operator-x"
            assert e.payload["actor_source"] == "agent_trace_subject"
            assert e.trace_id == "tid-1"
            assert e.workspace_id == "ws-1"
        finally:
            TraceContext.exit(token)

    def test_explicit_actor_beats_context(self, facade):
        tokens = set_ui_actor("ui-user")
        try:
            e = facade.record("act", actor="explicit-user")
            assert e.actor == "explicit-user"
        finally:
            reset_ui_actor(tokens)

    def test_status_recorded_in_payload(self, facade):
        e = facade.record("act", actor="a", status="denied")
        assert e.payload["status"] == "denied"

    def test_extra_fields_merged_at_top_level(self, facade):
        e = facade.record("act", actor="a", payload={"p": 1},
                          extra={"endpoint": "x", "duration_ms": 3})
        assert e.payload["endpoint"] == "x" and e.payload["duration_ms"] == 3
        assert e.payload["payload"] == {"p": 1}

    def test_non_dict_payload_wrapped(self, facade):
        e = facade.record("act", actor="a", payload=["a", "b"])
        assert e.payload["payload"] == {"value": ["a", "b"]}

    def test_empty_subject_and_payload_ok(self, facade):
        e = facade.record("act", actor="a")
        assert e.subject == "" and isinstance(e.payload, dict)


# ════════════════════════════════════════════════════════════
#  2. 脱敏先于入链（密钥原文不可恢复）
# ════════════════════════════════════════════════════════════


class TestRedaction:
    def test_secret_value_not_persisted(self, facade, chain):
        secret = "sk-test-S2-02-SHOULD-NOT-LEAK"
        e = facade.record("config.write", actor="admin",
                          payload={"api_key": secret, "model": "deepseek-chat"})
        raw = json.dumps(chain.get(e.seq).payload, ensure_ascii=False)
        assert secret not in raw
        assert chain.get(e.seq).payload["payload"]["api_key"] == "********"

    def test_nested_secret_not_persisted(self, facade, chain):
        secret = "sk-test-NESTED-SECRET"
        e = facade.record("act", actor="a",
                          payload={"llm": {"auth": {"token": secret}}})
        raw = json.dumps(chain.get(e.seq).payload, ensure_ascii=False)
        assert secret not in raw

    def test_extra_fields_are_redacted_too(self, facade, chain):
        secret = "sk-test-EXTRA-SECRET"
        e = facade.record("act", actor="a", extra={"password": secret})
        assert secret not in json.dumps(chain.get(e.seq).payload, ensure_ascii=False)

    def test_redact_payload_handles_list_and_scalar(self):
        assert redact_payload([{"token": "x"}]) == [{"token": "********"}]
        assert redact_payload("plain") == "plain"

    def test_payload_hash_binds_redacted_form(self, facade, chain):
        e = facade.record("act", actor="a", payload={"api_key": "sk-test-HASH-BIND"})
        got = chain.get(e.seq)
        assert got.recompute_payload_hash() == got.payload_hash

    # ── 依赖倒置注入点与兜底脱敏（CI 循环依赖修复引入的分支） ──

    def test_minimal_redact_masks_text_level_secrets(self):
        """未注册脱敏器时的兜底：文本级密钥形态（sk-/ghp_/AKIA/JWT/Bearer）"""
        from agent.audit.facade import _minimal_redact
        assert "sk-test-TEXTLEVEL-LEAK" not in _minimal_redact(
            "key=sk-test-TEXTLEVEL-LEAK;")
        assert "ghp_" not in _minimal_redact("tok ghp_" + "a" * 24)
        assert "AKIA" not in _minimal_redact("AKIAABCDEFGHIJKLMNOP")
        assert _minimal_redact("Bearer abc.def.ghi") == "Bearer ********"
        assert _minimal_redact(123) == 123          # 非字符串原样返回

    def test_redact_payload_falls_back_when_sanitizer_absent(self, monkeypatch):
        """脱敏器未注册（observability 未导入）→ 内置兜底仍不落原文"""
        monkeypatch.setattr(facade_mod, "_PAYLOAD_SANITIZER", None)
        out = redact_payload({"api_key": "sk-test-FALLBACK", "note": "Bearer xyz"})
        assert out["api_key"] == "********"
        assert out["note"] == "Bearer ********"

    def test_redact_payload_falls_back_when_sanitizer_raises(self, monkeypatch):
        """脱敏器异常 → 兜底（绝不返回原文）"""
        def _boom(_payload):
            raise RuntimeError("sanitizer down")

        monkeypatch.setattr(facade_mod, "_PAYLOAD_SANITIZER", _boom)
        assert redact_payload({"password": "p"}) == {"password": "********"}

    def test_injection_points_roundtrip(self):
        """注入点可注册/注销（依赖倒置接口契约）——**用例结束必须还原真实注册**"""
        from agent.audit import facade as fm
        prev_sanitizer = fm._PAYLOAD_SANITIZER
        prev_provider = fm._TRACE_CONTEXT_PROVIDER
        try:
            fm.set_payload_sanitizer(lambda p: "S")
            assert fm.redact_payload({"a": 1}) == "S"
            fm.set_payload_sanitizer(None)
            assert fm.redact_payload({"a": 1}) == {"a": 1}

            fm.set_trace_context_provider(lambda: {"trace_id": "t"})
            assert fm._trace_context_leaf() == {"trace_id": "t"}
            fm.set_trace_context_provider(
                lambda: (_ for _ in ()).throw(RuntimeError("provider down")))
            assert fm._trace_context_leaf() == {}      # 提供者异常 → 无上下文
        finally:
            fm.set_payload_sanitizer(prev_sanitizer)
            fm.set_trace_context_provider(prev_provider)
        # 还原后：真实脱敏器/上下文提供者仍生效（不污染同进程后续用例）
        assert fm.redact_payload({"api_key": "sk-test-RESTORED"})["api_key"] == "********"

    def test_ui_actor_reset_tolerates_bad_token(self):
        """reset_ui_actor 容错：非法 token 走显式清空分支"""
        reset_ui_actor(("not-a-token", "not-a-token"))
        assert get_ui_context() == {}


# ════════════════════════════════════════════════════════════
#  3. 开关 / best-effort / 严格模式
# ════════════════════════════════════════════════════════════


class _BrokenChain:
    """台账桩：append 必抛（模拟链不可用）"""

    closed = False

    def append(self, *a, **kw):
        raise RuntimeError("chain down")

    def stats(self, **kw):
        return {"total": 0}


class TestSwitchesAndFailure:
    def test_disabled_returns_none_and_creates_nothing(self, tmp_path):
        f = AuditFacade(db_path=str(tmp_path / "never.db"), enabled=False)
        assert f.chain is None
        assert f.record("act", actor="a") is None
        assert not os.path.exists(str(tmp_path / "never.db"))

    def test_enable_switch_after_construction(self, tmp_path, chain):
        f = AuditFacade(db_path=chain.db_path, enabled=False)
        assert f.record("act", actor="a") is None
        f.enabled = True
        f.bind(chain)
        assert f.record("act", actor="a") is not None

    def test_failure_is_swallowed_and_counted(self):
        f = AuditFacade(chain=_BrokenChain(), enabled=True)
        assert f.record("act", actor="a") is None
        snap = f.snapshot()
        assert snap["failure_count"] == 1
        assert "chain down" in snap["last_error"]
        assert snap["success_count"] == 0

    def test_strict_mode_raises(self):
        f = AuditFacade(chain=_BrokenChain(), enabled=True, strict=True)
        with pytest.raises(RuntimeError):
            f.record("act", actor="a")

    def test_success_counter_increments(self, facade):
        facade.reset_counters()
        facade.record("act", actor="a")
        facade.record("act", actor="a")
        assert facade.snapshot()["success_count"] == 2

    def test_snapshot_reports_chain_stats(self, facade, chain):
        facade.record("act", actor="a")
        chain.flush()
        snap = facade.snapshot()
        assert snap["enabled"] is True
        assert snap["chain"]["total"] == 1
        assert snap["db_path"] == chain.db_path

    def test_default_dual_write_enabled(self):
        assert AuditFacade().dual_write is True

    def test_disabled_env_switch(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUDIT_CHAIN_ENABLED", "0")
        f = AuditFacade(db_path=str(tmp_path / "x.db"))
        assert f.enabled is False


# ════════════════════════════════════════════════════════════
#  4. Trace 关键事件接线（含 S2-01 遗留 #3）
# ════════════════════════════════════════════════════════════


class TestTraceEvents:
    def test_record_trace_event_from_dict(self, facade):
        trace = {
            "trace_id": "tid-9", "task_id": "task-9", "capability_id": "cp.builtin.read_file",
            "actor": "auto", "tenancy": {"workspace_id": "ws-9"},
            "response": {"status": "error", "error_code": "ToolError"},
            "timing": {"duration_ms": 12.5},
            "cost": {"total_tokens": 30, "cost_usd": 0.001},
        }
        e = facade.record_trace_event(trace, event="trace.tool.error")
        assert e.action == "trace.tool.error"
        assert e.subject == "trace:tid-9"
        assert e.trace_id == "tid-9" and e.workspace_id == "ws-9"
        assert e.payload["payload"]["capability_id"] == "cp.builtin.read_file"
        assert e.payload["payload"]["error_code"] == "ToolError"

    def test_record_trace_event_accepts_object(self, facade):
        class _T:
            def to_dict(self):
                return {"trace_id": "t1", "actor": "human", "capability_id": "c",
                        "tenancy": {}, "response": {"status": "success"},
                        "timing": {}, "cost": {}}

        e = facade.record_trace_event(_T())
        assert e.action == "trace.closed"
        assert e.actor == "human"

    def test_record_trace_event_unserializable_returns_none(self, facade):
        class _Bad:
            def to_dict(self):
                raise ValueError("live object")

        assert facade.record_trace_event(_Bad()) is None

    def test_record_redact_event_records_no_values(self, facade, chain):
        e = facade.record_redact_event(field_count=3, kinds=["api_key", "token"],
                                       trace_id="tid-r")
        assert e.action == "trace.redact"
        assert e.payload["payload"]["field_count"] == 3
        assert e.payload["payload"]["kinds"] == ["api_key", "token"]
        assert e.payload["payload"]["values_recorded"] is False
        assert "sk-" not in json.dumps(chain.get(e.seq).payload, ensure_ascii=False)

    def test_record_redact_event_dedups_kinds(self, facade):
        e = facade.record_redact_event(field_count=2, kinds=["b", "a", "b"])
        assert e.payload["payload"]["kinds"] == ["a", "b"]


# ════════════════════════════════════════════════════════════
#  5. 读 / 验签 / 单例
# ════════════════════════════════════════════════════════════


class TestReadsAndSingleton:
    def test_recent_returns_tail_in_seq_order(self, facade):
        for i in range(5):
            facade.record(f"act{i}", actor="a")
        got = facade.recent(limit=2)
        assert [e.action for e in got] == ["act3", "act4"]

    def test_recent_with_filter(self, facade):
        facade.record_ui("skill.delete", actor="admin")
        facade.record_agent("approval.approve", actor="reviewer")
        assert len(facade.recent(limit=10, source="ui")) == 1

    def test_verify_passthrough(self, facade):
        facade.record("act", actor="a")
        v = facade.verify()
        assert v.ok is True

    def test_verify_when_disabled_reports_disabled(self, tmp_path):
        f = AuditFacade(db_path=str(tmp_path / "x.db"), enabled=False)
        v = f.verify()
        assert v.ok is True and v.reason == "disabled"

    def test_daily_merkle_root_delegation(self, facade):
        facade.record("act", actor="a")
        empty_day = facade.daily_merkle_root("2026-09-09")
        assert empty_day is not None and empty_day.leaf_count == 0   # 空日根留证
        root_today = facade.daily_merkle_root()
        assert root_today.leaf_count >= 1

    def test_module_level_record_uses_process_facade(self, bound_facade, chain):
        e = record("skill.delete", actor="admin", subject="skill:a", source="ui")
        assert e is not None
        assert chain.get(e.seq).source == "ui"

    def test_get_audit_is_process_facade(self):
        assert get_audit() is facade_mod.audit

    def test_lazy_chain_creation_on_first_record(self, tmp_path):
        db = str(tmp_path / "lazy.db")
        f = AuditFacade(db_path=db, roots_path=str(tmp_path / "r.jsonl"),
                        signing_key_path=str(tmp_path / "k.pem"))
        assert not os.path.exists(db)          # 构造期零副作用（不建库、不起线程）
        f.record("act", actor="a")
        assert os.path.exists(db)              # 首次写入才懒加载台账
        f.close()
        reset_audit_chains()

    def test_chain_property_rebuilds_after_close(self, tmp_path):
        reset_audit_chains()
        db = str(tmp_path / "rebuild.db")
        f = AuditFacade(db_path=db, roots_path=str(tmp_path / "r.jsonl"),
                        signing_key_path=str(tmp_path / "k.pem"))
        first = f.chain
        first.close()
        reset_audit_chains()
        second = f.chain
        assert second is not first and not second.closed
        f.close()
        reset_audit_chains()

    def test_bind_returns_previous_chain(self, chain, tmp_path):
        other = AuditChain(str(tmp_path / "other.db"),
                           roots_path=str(tmp_path / "r2.jsonl"),
                           signing_key_path=str(tmp_path / "k2.pem"), auto_seal=False)
        f = AuditFacade(chain=chain, enabled=True)
        assert f.bind(other) is chain
        assert f.chain is other
        other.close()

    def test_ui_agent_same_table(self, bound_facade, chain):
        """P7.2-24：UI 与 Agent 落同一张 audit_chain 表、同一条 seq 链"""
        a = record("approval.approve", actor="reviewer", source="agent")
        u = record("skill.delete", actor="admin", source="ui")
        chain.flush()
        rows = chain.entries()
        assert {r.source for r in rows} == {"agent", "ui"}
        assert [r.seq for r in rows] == [a.seq, u.seq]
        assert rows[1].prev_hash == rows[0].self_hash
        assert chain.verify_chain().ok is True
