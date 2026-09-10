"""TASK-S2-01 统一 Trace 规格 + TraceContext 透传 单元测试

覆盖范围（对齐任务书 §四 验收清单）：
- UnifiedTrace 字段覆盖 §3.4（schema_version/parent_trace_id/workspace_id/tenancy/
  request/response/timing/cost/side_effects）
- 缺 workspace_id 持久化失败（strict）或显式降级（ring buffer）有测试
- 脱敏先于哈希（密钥注入用例断言原文不可恢复）
- TraceContext ContextVar 透传 + child() 自动 parent 串联
- append-only 台账（无 UPDATE 写路径；DELETE 仅 clear() 测试专用）
- 读取接口 list_by_capability / chain / task_summary / snapshot_stats
- trace.capability_id ↔ descriptors 台账 join
- 异步批量 writer：flush / 降级 / stop 优雅关闭
"""

import json
import os
import pathlib
import sqlite3
import time
from unittest.mock import patch

import pytest

from agent.observability import trace_v2
from agent.observability.trace_v2 import (
    ACTOR_AUTO,
    ACTOR_HUMAN,
    ACTOR_SUB_AGENT,
    SCHEMA_VERSION,
    STATUS_ERROR,
    STATUS_SUCCESS,
    Cost,
    MissingWorkspaceError,
    Request,
    Response,
    SideEffects,
    Tenancy,
    Timing,
    TraceContext,
    TraceFacade,
    UnifiedTrace,
    UnifiedTraceStore,
    capability_reference,
    capability_reference_for_trace,
    derive_workspace_id,
    generate_trace_id,
    hash_content,
    load_runtime_descriptors,
    redact,
    redact_then_hash,
)

# 占位密钥：sk-test- 前缀命中仓库密钥扫描白名单（.github/gitleaks-config.toml），
# 非真实密钥；用途＝验证「脱敏先于哈希 → 原文不可恢复」。
SECRET_VALUE = "sk-test-SUPERSECRET-0123456789abcdef"


def _module_source() -> str:
    """读取 trace_v2 源文件（直读磁盘，不走 inspect/linecache）。

    Why: inspect.getsource 依赖 code.co_firstlineno + linecache 行切片，长时进程/并发
    改写下会出现行号漂移（本仓库 K13/F1/F2 已记录的同类问题）；直读文件按文本定位
    函数体，与内存中的行号无关，确定性更高。
    """
    return pathlib.Path(trace_v2.__file__).read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    """按文本提取模块内某函数体（4 空格缩进的方法：从 def 到下一个同级 def）"""
    src = _module_source()
    start = src.index(f"def {name}(")
    nxt = src.find("\n    def ", start + 1)
    return src[start:nxt] if nxt != -1 else src[start:]


# ════════════════════════════════════════════════════════════
#  fixture
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_context_and_singleton():
    """每测试前后清理结构化 ContextVar + TraceFacade 单例（隔离）"""
    trace_v2._trace_context_var.set(None)
    TraceFacade.reset()
    yield
    trace_v2._trace_context_var.set(None)
    TraceFacade.reset()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "trace_v2_test.db")


@pytest.fixture
def store(db_path):
    s = UnifiedTraceStore(db_path)
    yield s
    s.stop(timeout=2.0)


@pytest.fixture
def facade(db_path):
    f = TraceFacade(db_path)
    yield f
    f.flush()
    f._store.stop(timeout=2.0)


def _make_trace(**kwargs) -> UnifiedTrace:
    defaults = dict(
        trace_id="t" * 16,
        task_id="task-1",
        capability_id="cp.builtin.read_file",
        actor=ACTOR_AUTO,
        tenancy=Tenancy(tenant_id="ws_abc", workspace_id="ws_abc"),
        request=Request(args_redacted={"path": "/tmp/x"}, args_hash="a" * 16,
                        idempotency_key="k1"),
        response=Response(status=STATUS_SUCCESS, output_redacted={"ok": True},
                          output_hash="b" * 16, error_code=""),
        timing=Timing(started_at=time.time(), finished_at=time.time(),
                      duration_ms=1.5),
        cost=Cost(input_tokens=1, output_tokens=2, total_tokens=3, cost_usd=0.01),
        side_effects=SideEffects(files_written=["/tmp/x"]),
        parent_trace_id="p" * 16,
    )
    defaults.update(kwargs)
    return UnifiedTrace(**defaults)


# ════════════════════════════════════════════════════════════
#  1. UnifiedTrace schema（§3.4 字段覆盖）
# ════════════════════════════════════════════════════════════


class TestUnifiedTraceSchema:
    def test_to_dict_covers_all_spec_fields(self):
        """§3.4 全字段覆盖：trace_id/task_id/capability_id/actor/tenancy/request/
        response/timing/cost/side_effects/parent_trace_id/schema_version"""
        d = _make_trace().to_dict()
        expected = {
            "trace_id", "task_id", "capability_id", "actor",
            "tenant_id", "workspace_id",
            "args_redacted", "args_hash", "idempotency_key",
            "status", "output_redacted", "output_hash", "error_code",
            "started_at", "finished_at", "duration_ms",
            "input_tokens", "output_tokens", "total_tokens", "cost_usd",
            "files_written", "files_deleted", "external_calls", "notes",
            "parent_trace_id", "schema_version",
        }
        assert set(d.keys()) == expected

    def test_schema_version_default(self):
        assert _make_trace().schema_version == SCHEMA_VERSION
        assert _make_trace().to_dict()["schema_version"] == SCHEMA_VERSION

    def test_roundtrip_dict(self):
        original = _make_trace()
        restored = UnifiedTrace.from_dict(original.to_dict())
        assert restored.to_dict() == original.to_dict()

    def test_from_dict_defaults_for_missing_groups(self):
        t = UnifiedTrace.from_dict({"trace_id": "x" * 16})
        assert t.actor == ACTOR_AUTO
        assert t.tenancy.tenant_id == "default"
        assert t.tenancy.workspace_id == ""
        assert t.response.status == STATUS_SUCCESS
        assert t.schema_version == SCHEMA_VERSION
        assert t.side_effects.files_written == []

    def test_validate_passes_for_complete_trace(self):
        assert _make_trace().validate() == []

    def test_validate_flags_missing_workspace(self):
        issues = _make_trace(tenancy=Tenancy(tenant_id="t", workspace_id="")).validate()
        assert any("workspace_id" in i for i in issues)

    def test_validate_flags_illegal_actor(self):
        issues = _make_trace(actor="robot").validate()
        assert any("actor" in i for i in issues)

    def test_validate_flags_illegal_status(self):
        issues = _make_trace(
            response=Response(status="weird")).validate()
        assert any("status" in i for i in issues)

    def test_actor_constants(self):
        assert (ACTOR_HUMAN, ACTOR_AUTO, ACTOR_SUB_AGENT) == (
            "human", "auto", "sub_agent")

    def test_validate_flags_missing_trace_and_task_id(self):
        t = _make_trace(trace_id="", task_id="")
        issues = t.validate()
        assert any("trace_id" in i for i in issues)
        assert any("task_id" in i for i in issues)


# ════════════════════════════════════════════════════════════
#  2. 脱敏先于哈希
# ════════════════════════════════════════════════════════════


class TestRedactionBeforeHash:
    def test_redact_masks_sensitive_key(self):
        out = redact({"api_key": SECRET_VALUE, "path": "/tmp/a"})
        assert SECRET_VALUE not in json.dumps(out)
        assert out["path"] == "/tmp/a"

    def test_redact_masks_secret_in_plain_string(self):
        out = redact({"note": f"token={SECRET_VALUE}"})
        assert SECRET_VALUE not in json.dumps(out)

    def test_redact_nested(self):
        out = redact({"outer": {"token": SECRET_VALUE}, "list": [{"secret": SECRET_VALUE}]})
        assert SECRET_VALUE not in json.dumps(out)

    def test_redact_then_hash_hashes_redacted_not_original(self):
        """哈希绑定脱敏后内容（先哈希后脱敏会绑定原文）"""
        original = {"api_key": SECRET_VALUE}
        redacted, digest = redact_then_hash(original)
        assert digest == hash_content(redacted)
        # 脱敏确实改变了内容 ⇒ 脱敏后哈希 ≠ 原文哈希（证明 redact 先于 hash）
        assert redacted != original
        assert digest != hash_content(original)

    def test_secret_original_unrecoverable_in_persisted_record(self, store):
        """密钥注入用例：持久化内容仅 redacted + hash，原文不可恢复"""
        store.record(_make_trace())
        store.flush()
        trace = store.query(capability_id="cp.builtin.read_file")[0]
        dumped = json.dumps(trace.to_dict(), ensure_ascii=False)
        assert SECRET_VALUE not in dumped

    def test_facade_persists_only_redacted_args_and_output(self, facade):
        facade.start(task_id="tk", workspace_id="ws_1")
        facade.record("cp.builtin.web_post",
                      args={"api_key": SECRET_VALUE, "url": "https://x"},
                      output={"ok": True, "body": f"Bearer {SECRET_VALUE}"})
        facade.finish()
        assert facade.flush()
        [t] = facade.list_by_capability("cp.builtin.web_post")
        dumped = json.dumps(t.to_dict(), ensure_ascii=False)
        assert SECRET_VALUE not in dumped
        assert t.request.args_hash and t.response.output_hash

    def test_hash_deterministic_and_order_insensitive(self):
        assert hash_content({"a": 1, "b": 2}) == hash_content({"b": 2, "a": 1})

    def test_hash_differs_for_different_content(self):
        assert hash_content({"a": 1}) != hash_content({"a": 2})

    def test_hash_length_16_hex(self):
        h = hash_content("hello")
        assert len(h) == 16
        int(h, 16)

    def test_fallback_redact_when_filter_unavailable(self):
        """sensitive_data_filter 不可用 → 兜底掩码仍生效（不抛异常）"""
        with patch("agent.utils.sensitive_data_filter.filter_sensitive_data",
                   side_effect=RuntimeError("boom")):
            out = redact({"password": "p@ssw0rd", "note": f"{SECRET_VALUE}"})
        dumped = json.dumps(out)
        assert "p@ssw0rd" not in dumped
        assert SECRET_VALUE not in dumped

    def test_fallback_redact_text_patterns(self):
        out = trace_v2._fallback_redact_text(
            "key=" + SECRET_VALUE + " jwt=eyJabc.eyJdef.ghi")
        assert SECRET_VALUE not in out

    def test_redact_non_dict_passthrough_types(self):
        assert redact(42) == 42
        assert redact(None) is None


# ════════════════════════════════════════════════════════════
#  3. TraceContext（ContextVar 透传 + child 串联）
# ════════════════════════════════════════════════════════════


class TestTraceContext:
    def test_current_none_by_default(self):
        assert TraceContext.current() is None

    def test_enter_and_current(self):
        ctx = TraceContext(task_id="tk", tenant_id="ten", workspace_id="ws",
                           subject_id="u1", policy_version="p1")
        ctx.enter()
        assert TraceContext.current() is ctx
        assert TraceContext.current().workspace_id == "ws"

    def test_exit_restores_previous(self):
        outer = TraceContext(task_id="outer")
        token = outer.enter()
        inner = TraceContext(task_id="inner")
        inner.enter()
        TraceContext.exit(token)
        assert TraceContext.current() is None

    def test_task_id_defaults_to_trace_id(self):
        ctx = TraceContext()
        assert ctx.task_id == ctx.trace_id
        assert len(ctx.trace_id) == 16

    def test_child_new_trace_id_and_parent_linkage(self):
        parent = TraceContext(task_id="tk", workspace_id="ws")
        child = parent.child()
        assert child.trace_id != parent.trace_id
        assert child.parent_trace_id == parent.trace_id

    def test_child_inherits_context_fields(self):
        parent = TraceContext(task_id="tk", tenant_id="ten", workspace_id="ws",
                              subject_id="u1", policy_version="p9")
        child = parent.child()
        assert (child.task_id, child.tenant_id, child.workspace_id,
                child.subject_id, child.policy_version) == (
            "tk", "ten", "ws", "u1", "p9")

    def test_child_chain_links_grandchild(self):
        root = TraceContext(task_id="tk", workspace_id="ws")
        mid = root.child()
        leaf = mid.child()
        assert mid.parent_trace_id == root.trace_id
        assert leaf.parent_trace_id == mid.trace_id
        assert len({root.trace_id, mid.trace_id, leaf.trace_id}) == 3

    def test_to_dict(self):
        ctx = TraceContext(task_id="tk", workspace_id="ws", subject_id="u")
        d = ctx.to_dict()
        assert d["task_id"] == "tk"
        assert d["workspace_id"] == "ws"
        assert d["subject_id"] == "u"
        assert "parent_trace_id" in d

    def test_generate_trace_id_format_and_uniqueness(self):
        ids = {generate_trace_id() for _ in range(50)}
        assert len(ids) == 50
        assert all(len(i) == 16 for i in ids)

    def test_started_at_autofilled(self):
        assert TraceContext().started_at > 0


# ════════════════════════════════════════════════════════════
#  4. workspace_id 派生（P7.1-19）
# ════════════════════════════════════════════════════════════


class TestWorkspaceId:
    def test_deterministic(self):
        assert derive_workspace_id("/a/b/c") == derive_workspace_id("/a/b/c")

    def test_normalized_case_and_separators(self, tmp_path):
        """平台规范化（os.path.normcase）后恒等——跨平台安全。

        Why 不用 `C:/Repo/Demo` vs `c:\\repo\\demo` 直接断言：Linux 上
        normcase 为恒等且反斜杠非分隔符，两者本就是不同路径（CI ubuntu 首轮实测失败）。
        大小写不敏感语义单独由 test_windows_case_insensitive_paths 覆盖（skipif nt）。
        """
        p = str(tmp_path / "Repo" / "Demo")
        assert derive_workspace_id(p) == derive_workspace_id(os.path.normcase(p))

    def test_normalized_separator_variants(self, tmp_path):
        """同一路径的 / 与平台分隔符写法恒等（两种写法在本平台均指向同一路径）"""
        p = tmp_path / "Repo" / "Demo"
        mixed = str(p).replace(os.sep, "/") if os.sep != "/" else str(p)
        assert derive_workspace_id(str(p)) == derive_workspace_id(mixed)

    @pytest.mark.skipif(os.name != "nt",
                        reason="Windows 路径大小写不敏感语义（Linux 上 normcase 为恒等）")
    def test_windows_case_insensitive_paths(self):
        assert derive_workspace_id("C:/Repo/Demo") == derive_workspace_id("c:\\repo\\demo")

    def test_different_paths_differ(self):
        assert derive_workspace_id("/a/b") != derive_workspace_id("/a/c")

    def test_prefix_and_length(self):
        wid = derive_workspace_id("/a/b")
        assert wid.startswith("ws_")
        assert len(wid) == 19

    def test_empty_returns_empty(self):
        assert derive_workspace_id("") == ""
        assert derive_workspace_id(None) == ""


# ════════════════════════════════════════════════════════════
#  5. UnifiedTraceStore：异步写入 + append-only + 读取接口
# ════════════════════════════════════════════════════════════


class TestStorePersistence:
    def test_record_and_flush_see_data(self, store):
        assert store.record(_make_trace()) is True
        assert store.flush()
        assert store.count() == 1

    def test_memory_db_shared_across_threads(self):
        s = UnifiedTraceStore(":memory:")
        try:
            s.record(_make_trace())
            assert s.flush()
            assert s.count() == 1
        finally:
            s.stop(timeout=2.0)

    def test_query_by_capability(self, store):
        store.record(_make_trace(capability_id="cp.a.b"))
        store.record(_make_trace(trace_id="u" * 16, capability_id="cp.c.d"))
        store.flush()
        assert len(store.query(capability_id="cp.a.b")) == 1
        assert len(store.list_by_capability("cp.c.d")) == 1
        assert store.list_by_capability("cp.none") == []

    def test_query_by_task_and_parent(self, store):
        store.record(_make_trace(trace_id="a" * 16, task_id="t1", parent_trace_id="r"))
        store.record(_make_trace(trace_id="b" * 16, task_id="t1", parent_trace_id="r"))
        store.record(_make_trace(trace_id="c" * 16, task_id="t2", parent_trace_id="z"))
        store.flush()
        assert len(store.query(task_id="t1")) == 2
        assert len(store.query(parent_id="r")) == 2

    def test_query_since_and_limit(self, store):
        now = time.time()
        store.record(_make_trace(trace_id="a" * 16, timing=Timing(started_at=now - 100)))
        store.record(_make_trace(trace_id="b" * 16, timing=Timing(started_at=now)))
        store.flush()
        assert len(store.query(since=now - 1)) == 1
        assert len(store.query(limit=1)) == 1

    def test_list_by_capability_limit_supports_s3_threshold(self, store):
        """S3 模式挖掘 ≥20 条同类轨迹的数据源可计量"""
        for i in range(25):
            store.record(_make_trace(trace_id=f"{i:016d}", capability_id="cp.mine.pattern"))
        store.flush()
        rows = store.list_by_capability("cp.mine.pattern", limit=100)
        assert len(rows) == 25
        assert len(store.list_by_capability("cp.mine.pattern", limit=20)) == 20

    def test_chain_returns_parent_then_children(self, store):
        root = _make_trace(trace_id="r" * 16, task_id="r" * 16, parent_trace_id="",
                           capability_id="", timing=Timing(started_at=1.0))
        c1 = _make_trace(trace_id="c" * 16, task_id="r" * 16, parent_trace_id="r" * 16,
                         timing=Timing(started_at=2.0))
        c2 = _make_trace(trace_id="d" * 16, task_id="r" * 16, parent_trace_id="r" * 16,
                         timing=Timing(started_at=3.0))
        for t in (root, c1, c2):
            store.record(t)
        store.flush()
        chained = [t.trace_id for t in store.chain("r" * 16)]
        assert chained == ["r" * 16, "c" * 16, "d" * 16]

    def test_chain_unknown_id_returns_empty(self, store):
        assert store.chain("nope") == []

    def test_task_summary_aggregates(self, store):
        store.record(_make_trace(trace_id="r" * 16, task_id="r" * 16, capability_id=""))
        store.record(_make_trace(trace_id="a" * 16, task_id="r" * 16,
                                 capability_id="cp.x.y", response=Response(status=STATUS_SUCCESS),
                                 cost=Cost(total_tokens=10, cost_usd=0.02)))
        store.record(_make_trace(trace_id="b" * 16, task_id="r" * 16,
                                 capability_id="cp.x.z", response=Response(status=STATUS_ERROR,
                                                                           error_code="E"),
                                 cost=Cost(total_tokens=5, cost_usd=0.01)))
        store.flush()
        s = store.task_summary("r" * 16)
        assert s["step_count"] == 2
        assert s["success_count"] == 1
        assert s["failed_count"] == 1
        assert s["success_rate"] == 0.5
        assert s["total_tokens"] == 15
        assert abs(s["total_cost_usd"] - 0.03) < 1e-9
        assert s["capabilities"] == ["cp.x.y", "cp.x.z"]
        assert s["workspace_id"] == "ws_abc"

    def test_task_summary_empty_task(self, store):
        s = store.task_summary("missing")
        assert s["step_count"] == 0 and s["success_rate"] == 0.0

    def test_snapshot_stats(self, store):
        store.record(_make_trace(capability_id="cp.a.b", actor=ACTOR_HUMAN))
        store.record(_make_trace(trace_id="z" * 16, capability_id="cp.a.b",
                                 response=Response(status=STATUS_ERROR)))
        store.flush()
        st = store.snapshot_stats()
        assert st["total"] == 2
        assert st["by_capability"]["cp.a.b"] == 2
        assert st["by_actor"][ACTOR_HUMAN] == 1
        assert st["success_rate"] == 0.5
        assert st["append_only"] is True

    def test_flush_empty_is_true(self, store):
        assert store.flush(timeout=0.5) is True

    def test_stop_idempotent_and_flushes_residual(self, store):
        store.record(_make_trace())
        assert store.stop(timeout=3.0) is True
        assert store.stop(timeout=1.0) is True
        assert not store._writer_thread.is_alive()


class TestWorkspaceInvariant:
    """P7.1-19：缺 workspace_id 的持久化记录写入失败或显式降级"""

    def test_missing_workspace_degrades_explicitly(self, store):
        t = _make_trace(tenancy=Tenancy(tenant_id="t", workspace_id=""))
        assert store.record(t) is False          # 显式降级（未入队）
        assert store.snapshot_stats()["workspace_degraded"] == 1
        assert store.count() == 1                # 仍可见于 ring buffer（不丢数据）

    def test_missing_workspace_strict_raises(self, store):
        t = _make_trace(tenancy=Tenancy(tenant_id="t", workspace_id=""))
        with pytest.raises(MissingWorkspaceError):
            store.record(t, strict=True)

    def test_store_strict_default_raises(self, db_path):
        s = UnifiedTraceStore(db_path, strict=True)
        try:
            with pytest.raises(MissingWorkspaceError):
                s.record(_make_trace(tenancy=Tenancy(workspace_id="")))
        finally:
            s.stop(timeout=2.0)

    def test_present_workspace_enqueues(self, store):
        assert store.record(_make_trace()) is True
        assert store.snapshot_stats()["workspace_degraded"] == 0

    def test_facade_record_strict_propagates(self, facade):
        facade.start(task_id="tk", workspace_id="")
        with pytest.raises(MissingWorkspaceError):
            facade.record("cp.a.b", args={}, strict=True)

    def test_sqlite_failure_degrades_to_ring_buffer(self, store, tmp_path):
        with patch.object(store, "_get_conn",
                          side_effect=sqlite3.OperationalError("disk full")):
            store._write_to_db([_make_trace()])
        assert store._degraded is True
        assert store.count() == 1


# ════════════════════════════════════════════════════════════
#  6. append-only
# ════════════════════════════════════════════════════════════


class TestAppendOnly:
    def test_no_update_write_path_in_source(self):
        src = _module_source()
        assert "UPDATE unified_traces" not in src
        assert "update unified_traces" not in src.lower()

    def test_only_delete_is_in_clear(self):
        src = _module_source()
        occurrences = [ln.strip() for ln in src.splitlines()
                       if "DELETE FROM unified_traces" in ln]
        assert len(occurrences) == 1

    def test_writer_statement_is_insert_only(self):
        body = _function_source("_write_to_db")
        assert "INSERT INTO unified_traces" in body
        assert "UPDATE" not in body.upper()
        assert "DELETE" not in body.upper()

    def test_row_values_align_with_columns(self, store):
        """列名与取值元组顺序一致（防 schema 漂移）"""
        assert len(store._COLUMNS) == len(store._row_values(_make_trace()))

    def test_records_are_immutable_after_write(self, store):
        store.record(_make_trace())
        store.flush()
        before = store.query()[0].to_dict()
        # 再查一次内容不变（无就地改写路径）
        after = store.query()[0].to_dict()
        assert before == after

    def test_clear_is_explicit_test_helper(self, store):
        store.record(_make_trace())
        store.flush()
        assert store.count() == 1
        store.clear()
        assert store.count() == 0


# ════════════════════════════════════════════════════════════
#  7. TraceFacade：start / record / finish / 读取
# ════════════════════════════════════════════════════════════


class TestFacade:
    def test_start_sets_context(self, facade):
        tid = facade.start(task_id="tk", tenant_id="ten", workspace_id="ws",
                           subject_id="u", policy_version="v1")
        ctx = TraceContext.current()
        assert ctx is not None
        assert ctx.trace_id == tid
        assert (ctx.task_id, ctx.tenant_id, ctx.workspace_id, ctx.subject_id,
                ctx.policy_version) == ("tk", "ten", "ws", "u", "v1")

    def test_record_creates_child_of_task_trace(self, facade):
        root = facade.start(task_id="tk", workspace_id="ws")
        t = facade.record("cp.builtin.read_file", args={"p": 1}, output={"ok": True})
        assert t.parent_trace_id == root
        assert t.task_id == "tk"
        assert t.tenancy.workspace_id == "ws"
        assert t.trace_id != root

    def test_record_without_context_has_no_parent(self, facade):
        t = facade.record("cp.builtin.read_file", args={}, output={"ok": True})
        assert t.parent_trace_id == ""
        assert t.task_id == t.trace_id

    def test_record_explicit_trace_id(self, facade):
        facade.start(task_id="tk", workspace_id="ws")
        t = facade.record("cp.a.b", output={"ok": True}, trace_id="explicit12345678")
        assert t.trace_id == "explicit12345678"
        assert t.parent_trace_id == ""

    def test_status_inference_from_output_ok_false(self, facade):
        facade.start(task_id="tk", workspace_id="ws")
        t = facade.record("cp.a.b", output={"ok": False, "error": "boom"})
        assert t.response.status == STATUS_ERROR
        assert t.response.error_code == "boom"

    def test_status_explicit_overrides(self, facade):
        facade.start(task_id="tk", workspace_id="ws")
        t = facade.record("cp.a.b", output={"ok": False}, status=STATUS_SUCCESS,
                          error_code="")
        assert t.response.status == STATUS_SUCCESS

    def test_actor_and_cost_recorded(self, facade):
        facade.start(task_id="tk", workspace_id="ws")
        t = facade.record("cp.a.b", output={"ok": True}, actor=ACTOR_SUB_AGENT,
                          input_tokens=10, output_tokens=5, cost_usd=0.5)
        assert t.actor == ACTOR_SUB_AGENT
        assert (t.cost.input_tokens, t.cost.output_tokens, t.cost.total_tokens) == (10, 5, 15)
        assert t.cost.cost_usd == 0.5

    def test_side_effects_recorded(self, facade):
        facade.start(task_id="tk", workspace_id="ws")
        se = SideEffects(files_written=["a.txt"], external_calls=["mcp:x"])
        t = facade.record("cp.a.b", output={"ok": True}, side_effects=se)
        assert t.side_effects.files_written == ["a.txt"]
        assert t.side_effects.external_calls == ["mcp:x"]

    def test_finish_writes_task_level_trace_and_clears_context(self, facade):
        root = facade.start(task_id="tk", workspace_id="ws")
        facade.record("cp.a.b", output={"ok": True})
        task = facade.finish(status=STATUS_SUCCESS)
        assert task is not None
        assert task.trace_id == root
        assert task.task_id == "tk"
        assert task.capability_id == ""
        assert TraceContext.current() is None

    def test_finish_without_context_returns_none(self, facade):
        assert facade.finish() is None

    def test_finish_records_error_status(self, facade):
        root = facade.start(task_id="tk", workspace_id="ws")
        task = facade.finish(status=STATUS_ERROR, error_code="ValueError")
        assert task.response.status == STATUS_ERROR
        assert task.response.error_code == "ValueError"

    def test_end_to_end_task_chain(self, facade):
        """端到端：task 主 Trace + tool 子 Trace 可串联且全程含 workspace_id"""
        root = facade.start(task_id="fix-test", workspace_id="ws_repo",
                            tenant_id="ws_repo")
        facade.record("cp.builtin.read_file", args={"path": "t.py"}, output={"ok": True})
        facade.record("cp.builtin.shell_execute", args={"cmd": "pytest"},
                      output={"ok": False, "error": "1 failed"})
        facade.finish(status=STATUS_ERROR, error_code="ToolError")
        assert facade.flush()

        chain = facade.chain(root)
        assert [t.trace_id for t in chain][0] == root
        assert len(chain) == 3
        assert all(t.tenancy.workspace_id == "ws_repo" for t in chain)
        assert sum(1 for t in chain if t.parent_trace_id == root) == 2

        summary = facade.task_summary("fix-test")
        assert summary["step_count"] == 2
        assert summary["success_rate"] == 0.5
        assert summary["workspace_id"] == "ws_repo"

    def test_query_delegates(self, facade):
        root = facade.start(task_id="tk", workspace_id="ws")
        facade.record("cp.a.b", output={"ok": True})
        facade.finish()
        assert facade.flush()
        assert len(facade.query(task_id="tk")) == 2
        assert len(facade.list_by_capability("cp.a.b")) == 1

    def test_write_stats_outputs_json(self, facade, tmp_path):
        facade.start(task_id="tk", workspace_id="ws")
        facade.record("cp.a.b", output={"ok": True})
        facade.finish()
        assert facade.flush()
        target = tmp_path / "trace_stats.json"
        stats = facade.write_stats(str(target))
        assert stats["total"] == 2
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded["schema_version"] == SCHEMA_VERSION
        assert loaded["append_only"] is True

    def test_instance_singleton_and_reset(self, db_path, monkeypatch):
        f1 = TraceFacade.instance(db_path=db_path)
        f2 = TraceFacade.instance()
        assert f1 is f2
        TraceFacade.reset()
        assert TraceFacade.instance(db_path=db_path) is not f1
        assert f1._store._stopped is True


# ════════════════════════════════════════════════════════════
#  8. trace.capability_id ↔ descriptors 台账 join（S1-01 遗留 #3）
# ════════════════════════════════════════════════════════════


class TestCapabilityJoin:
    def test_load_runtime_descriptors_registers_builtin(self, tmp_path):
        from agent.descriptors.registry import DescriptorRegistry
        reg = DescriptorRegistry(path=str(tmp_path / "d.json"), autosave=False)
        reg, summary = load_runtime_descriptors(
            reg, builtin_entries=[{"name": "read_file", "description": "读文件"}])
        assert summary["builtin"]["registered"] == 1
        assert reg.get("cp.builtin.read_file") is not None

    def test_load_runtime_descriptors_registers_mcp(self, tmp_path):
        from agent.descriptors.registry import DescriptorRegistry
        reg = DescriptorRegistry(path=str(tmp_path / "d.json"), autosave=False)
        _, summary = load_runtime_descriptors(
            reg, builtin_entries=[],
            mcp_tools=[{"name": "read_file", "description": "MCP 读文件",
                        "inputSchema": {"type": "object",
                                        "properties": {"path": {"type": "string"}}}}],
            mcp_server="filesystem")
        assert summary["mcp"]["registered"] == 1
        assert reg.get("cp.filesystem.read_file") is not None

    def test_trace_capability_id_joins_descriptor_ledger(self, tmp_path, facade):
        """真实登记后 join 通过：trace.capability_id → registry.get() 非 None"""
        from agent.descriptors.registry import DescriptorRegistry
        reg = DescriptorRegistry(path=str(tmp_path / "d.json"), autosave=False)
        reg, _ = load_runtime_descriptors(
            reg, builtin_entries=[{"name": "read_file", "description": "读文件"}])
        facade.start(task_id="tk", workspace_id="ws")
        facade.record("cp.builtin.read_file", args={"path": "x"}, output={"ok": True})
        facade.finish()
        assert facade.flush()
        [trace] = facade.list_by_capability("cp.builtin.read_file")
        descriptor = reg.get(trace.capability_id)
        assert descriptor is not None
        assert descriptor.capability_id == "cp.builtin.read_file"
        assert descriptor.origin.source_type.value == "builtin"

    def test_unregistered_capability_does_not_join(self, tmp_path, facade):
        from agent.descriptors.registry import DescriptorRegistry
        reg = DescriptorRegistry(path=str(tmp_path / "d.json"), autosave=False)
        facade.start(task_id="tk", workspace_id="ws")
        facade.record("plain_tool_name", output={"ok": True})
        facade.finish()
        assert facade.flush()
        [trace] = facade.list_by_capability("plain_tool_name")
        assert reg.get(trace.capability_id) is None

    def test_empty_runtime_entries_is_advisory(self, tmp_path):
        from agent.descriptors.registry import DescriptorRegistry
        reg = DescriptorRegistry(path=str(tmp_path / "d.json"), autosave=False)
        reg2, summary = load_runtime_descriptors(reg, builtin_entries=[])
        assert reg2 is reg
        assert summary == {}


# ════════════════════════════════════════════════════════════
#  8b. capability 引用（§3.4：borrowed/opaque 须带 origin/provenance）
# ════════════════════════════════════════════════════════════


class TestCapabilityReference:
    @pytest.fixture
    def reg(self, tmp_path):
        from agent.descriptors.registry import DescriptorRegistry
        registry = DescriptorRegistry(path=str(tmp_path / "d.json"), autosave=False)
        registry, _ = load_runtime_descriptors(
            registry, builtin_entries=[{"name": "read_file", "description": "读文件"}])
        registry, _ = load_runtime_descriptors(
            registry, builtin_entries=[],
            mcp_tools=[{"name": "remote_read", "description": "远端读",
                        "inputSchema": {"type": "object",
                                        "properties": {"p": {"type": "string"}}}}],
            mcp_server="filesystem")
        return registry

    def test_builtin_reference_carries_provenance(self, reg):
        ref = capability_reference("cp.builtin.read_file", reg)
        assert ref["joined"] is True
        assert ref["source_type"] == "builtin"
        assert ref["provenance"] == "verified"
        assert ref["stage"] is None
        assert ref["external_endpoint"] is False

    def test_borrowed_opaque_reference_carries_origin_and_trace_policy(self, reg):
        """borrowed/opaque（MCP 外来）能力必须带 origin/provenance + trace_policy"""
        ref = capability_reference("cp.filesystem.remote_read", reg)
        assert ref["joined"] is True
        assert ref["source_type"] == "mcp"
        assert ref["source_id"] == "filesystem"
        assert ref["provenance"] == "declared"          # 外来无签名 → declared
        assert ref["stage"] == "borrowed"               # 外部上游接入即借
        assert ref["trace_policy"]                       # borrowed 必记完整轨迹
        assert "S2-ledger-pending" in ref["trace_policy"]

    def test_unregistered_reference_is_honest(self, reg):
        ref = capability_reference("cp.unknown.thing", reg)
        assert ref["joined"] is False
        assert ref["provenance"] is None                 # 不伪造 provenance
        assert ref["source_type"] is None

    def test_empty_capability_id(self, reg):
        assert capability_reference("", reg)["joined"] is False

    def test_registry_failure_is_advisory(self):
        class _Boom:
            def get(self, _cid):
                raise RuntimeError("registry down")

        ref = capability_reference("cp.x.y", _Boom())
        assert ref["joined"] is False

    def test_reference_for_trace(self, reg):
        trace = _make_trace(capability_id="cp.builtin.read_file")
        ref = capability_reference_for_trace(trace, reg)
        assert ref["joined"] is True
        assert ref["capability_id"] == "cp.builtin.read_file"

    def test_reference_defaults_to_real_ledger(self):
        """registry 缺省时懒加载真实台账（data/descriptors.json，只读）"""
        ref = capability_reference("cp.skill.global-core-principles")
        assert ref["capability_id"] == "cp.skill.global-core-principles"
        # 存量回填资产（S1-02）应能 join；即便台账缺失也只返回 joined=False（advisory）
        assert isinstance(ref["joined"], bool)


# ════════════════════════════════════════════════════════════
#  9. 降级/兜底路径（守【不易】：失败绝不阻断主路径）
# ════════════════════════════════════════════════════════════


class TestFallbackPaths:
    def test_exit_cross_context_falls_back_to_none(self):
        import contextvars as _cv
        ctx = TraceContext(task_id="t")
        other = _cv.copy_context()
        token = other.run(ctx.enter)
        # 在主 context reset 其他 context 的 token → 异常 → 降级为 None（不抛）
        TraceContext.exit(token)
        assert TraceContext.current() is None

    def test_store_init_db_failure_degrades(self, db_path):
        with patch.object(UnifiedTraceStore, "_init_db",
                          side_effect=sqlite3.OperationalError("nope")):
            s = UnifiedTraceStore(db_path)
        try:
            assert s._degraded is True
            assert s.record(_make_trace()) is True
            assert s.flush()
            assert s.count() == 1        # ring buffer 降级仍可见
        finally:
            s.stop(timeout=2.0)

    def test_flush_timeout_returns_false(self, store):
        with store._count_lock:
            store._enqueue_count += 1    # 构造不可追平的入队计数
        assert store.flush(timeout=0.05) is False

    def test_stop_flushes_residual_queue(self, store):
        """writer 线程已退出时，队列残留被 stop 兜底写入（不丢数据）"""
        store._stopped = True
        store._writer_thread.join(timeout=2.0)
        store._stopped = False
        store._queue.put(_make_trace())
        assert store.stop(timeout=2.0) is True
        assert store._queue.empty()
        assert store.count() == 1

    def test_iter_persisted_skips_corrupt_payload(self, store):
        store.record(_make_trace())
        store.flush()
        conn = store._get_conn()
        conn.execute(
            "INSERT INTO unified_traces "
            "(trace_id, task_id, capability_id, started_at, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            ("bad", "bad", "bad", 1.0, "{not-json"))
        conn.commit()
        assert store.count() == 1        # 损坏行被跳过，不抛异常

    def test_clear_on_degraded_store(self, store):
        store._degraded = True
        store._fallback_ring_buffer.append(_make_trace())
        store.clear()
        assert store.count() == 0

    def test_list_builtin_entries_shape(self):
        assert isinstance(trace_v2._list_builtin_entries(), list)

    def test_list_builtin_entries_failure_returns_empty(self):
        with patch("agent.tools.list_tools", side_effect=RuntimeError("boom")):
            assert trace_v2._list_builtin_entries() == []

    def test_load_runtime_descriptors_defaults_to_runtime_builtins(self, tmp_path):
        from agent.descriptors.registry import DescriptorRegistry
        reg = DescriptorRegistry(path=str(tmp_path / "d.json"), autosave=False)
        reg2, summary = load_runtime_descriptors(reg)
        assert reg2 is reg
        assert set(summary) <= {"builtin", "mcp"}

    def test_facade_write_stats_swallows_oserror(self, facade, tmp_path):
        stats = facade.write_stats(str(tmp_path / "no_such_dir" / "\0bad" / "s.json"))
        assert stats["total"] == 0
