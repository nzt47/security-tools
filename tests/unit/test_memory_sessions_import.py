# -*- coding: utf-8 -*-
"""外部会话「全话」迁移：读取器 / 幂等 / 上限 / 脱敏 / 路由契约。

所有用例都在 tmp 里造源与落点，绝不碰生产 data/messages.jsonl：
- dsh：zstd 压缩的 JSONL 事件流（只挑真人输入 + assistant 文本块）
- claude：projects/<p>/<sid>.jsonl（复用既有读取器）
- trae：User/globalStorage/state.vscdb（sqlite ItemTable）
"""

from __future__ import annotations

import json
import os
import sqlite3
import time

import pytest
import zstandard
from flask import Flask

from agent.api_envelope import ENVELOPE_HEADER, ENVELOPE_VERSION
from agent.memory_import import transcript
from agent.memory_import.readers import dsh as dsh_reader
from agent.memory_import.readers import trae as trae_reader
from agent.server_routes import routes_memory_sessions as routes

TOKEN = "test-token-memory-sessions"


def _zstd_write(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"
    path.write_bytes(zstandard.ZstdCompressor().compress(raw.encode("utf-8")))


def _session_event(sid, origin="top"):
    return {"type": "session", "id": sid, "createdAt": 1700000000000,
            "cwd": "C:/work", "origin": origin, "agentPreset": "cordis"}


def _user_event(text, kind="user", ms=1700000001000):
    return {"type": "user/message", "time": ms,
            "data": {"role": "user", "source": {"kind": kind},
                     "content": [{"type": "text", "text": text}]}}


def _asst_event(text, ms=1700000002000):
    return {"type": "assistant/message", "time": ms,
            "data": {"message": {"role": "assistant",
                                 "content": [{"type": "text", "text": text},
                                             {"type": "tool-call", "name": "read"}]}}}


@pytest.fixture()
def dsh_root(tmp_path):
    root = tmp_path / "sessions"
    _zstd_write(root / "proj-a" / "sess-1" / "session.jsonl.zstd", [
        _session_event("sess-1"),
        _user_event("你好，帮我看看仓库"),
        _user_event("系统注入的快照", kind="plugin"),
        _asst_event("我先读一下目录"),
    ])
    _zstd_write(root / "proj-a" / "sess-2" / "session.jsonl.zstd", [
        _session_event("sess-2"),
        _user_event("另一个会话"),
    ])
    _zstd_write(root / "proj-b" / "sub-1" / "session.jsonl.zstd", [
        _session_event("sub-1", origin="subagent"),
        _user_event("子代理输入"),
        _asst_event("子代理输出"),
    ])
    # 排序按 mtime 新 -> 旧（小样本取最近 N 个）：显式钉住，避免文件系统抖动
    now = time.time()
    os.utime(root / "proj-a" / "sess-1" / "session.jsonl.zstd", (now, now))
    os.utime(root / "proj-a" / "sess-2" / "session.jsonl.zstd", (now - 10, now - 10))
    os.utime(root / "proj-b" / "sub-1" / "session.jsonl.zstd", (now - 20, now - 20))
    return root


def _paths(tmp_path):
    return {
        "messages_path": tmp_path / "messages.jsonl",
        "ledger_path": tmp_path / "ledger.json",
        "report_dir": tmp_path / "reports",
    }


def test_dsh_reader_filters_injected_and_keeps_text(dsh_root):
    path = dsh_root / "proj-a" / "sess-1" / "session.jsonl.zstd"
    session, stats = dsh_reader.read_session(path)
    assert session.session_id == "sess-1"
    assert [m.role for m in session.messages] == ["user", "assistant"]
    assert session.messages[0].content == "你好，帮我看看仓库"
    assert session.messages[0].ts  # 毫秒 epoch -> ISO
    assert stats["injected_user_messages"] == 1


def test_dry_run_reports_counts_and_writes_nothing(tmp_path, dsh_root):
    p = _paths(tmp_path)
    result = transcript.import_sessions(
        source="dsh", dry_run=True, dsh_root=dsh_root, limit_sessions=2, **p)
    assert result["sessions"]["seen"] == 3
    assert result["sessions"]["scanned"] == 2
    assert result["messages"]["imported"] == 3
    assert result["has_more"] is True
    assert not p["messages_path"].exists()
    assert not p["ledger_path"].exists()  # dry-run 不写台账
    report = json.loads(open(result["report_path"], encoding="utf-8").read())
    # 报告只有计数：不出现正文
    assert "你好，帮我看看仓库" not in json.dumps(report, ensure_ascii=False)


def test_execute_writes_rows_and_is_idempotent(tmp_path, dsh_root):
    p = _paths(tmp_path)
    first = transcript.import_sessions(source="dsh", dry_run=False, confirm=True,
                                       dsh_root=dsh_root, **p)
    assert first["messages"]["imported"] == 5
    rows = [json.loads(line) for line in
            p["messages_path"].read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 5
    assert all(row["session_id"] for row in rows)
    assert all(row["import_key"].startswith("import:dsh:") for row in rows)
    assert all(row["source"] == "dsh" and row["imported_at"] for row in rows)
    assert sorted({row["session_id"] for row in rows}) == ["sess-1", "sess-2", "sub-1"]

    second = transcript.import_sessions(source="dsh", dry_run=False, confirm=True,
                                        dsh_root=dsh_root, **p)
    assert second["messages"]["imported"] == 0
    assert second["sessions"]["skipped_unchanged"] == 3
    assert len(p["messages_path"].read_text(encoding="utf-8").splitlines()) == 5


def test_execute_requires_confirm(tmp_path, dsh_root):
    from agent.memory_import.models import ConfirmationRequired

    with pytest.raises(ConfirmationRequired):
        transcript.import_sessions(source="dsh", dry_run=False, confirm=False,
                                   dsh_root=dsh_root, **_paths(tmp_path))


def test_redaction_masks_secret_before_write(tmp_path):
    root = tmp_path / "sessions"
    _zstd_write(root / "p" / "s1" / "session.jsonl.zstd", [
        _session_event("s1"),
        _user_event("我的 key 是 sk-abcdefghijklmnopqrstuvwxyz 请记住"),
    ])
    p = _paths(tmp_path)
    result = transcript.import_sessions(source="dsh", dry_run=False, confirm=True,
                                        dsh_root=root, **p)
    assert result["messages"]["imported"] == 1
    body = p["messages_path"].read_text(encoding="utf-8")
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in body
    assert "<redacted:openai_sk>" in body
    assert result["redactions"].get("openai_sk") == 1


def test_message_cap_truncates_long_text(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    _zstd_write(root / "p" / "s1" / "session.jsonl.zstd", [
        _session_event("s1"),
        _user_event("x" * (dsh_reader.MAX_MESSAGE_CHARS + 50)),
    ])
    p = _paths(tmp_path)
    result = transcript.import_sessions(source="dsh", dry_run=False, confirm=True,
                                        dsh_root=root, **p)
    assert result["messages"]["truncated"] == 1
    row = json.loads(p["messages_path"].read_text(encoding="utf-8").splitlines()[0])
    assert len(row["content"]) == dsh_reader.MAX_MESSAGE_CHARS


def test_include_subagents_false_filters(tmp_path, dsh_root):
    p = _paths(tmp_path)
    result = transcript.import_sessions(source="dsh", dry_run=True,
                                        dsh_root=dsh_root,
                                        include_subagents=False, **p)
    assert result["sessions"]["seen"] == 2  # sub-1 被过滤


def test_claude_transcript_import(tmp_path):
    base = tmp_path / ".claude"
    file = base / "projects" / "proj" / "claude-sess.jsonl"
    file.parent.mkdir(parents=True)
    lines = [
        {"type": "user", "sessionId": "claude-sess", "timestamp": "2026-01-01T00:00:00Z",
         "message": {"role": "user", "content": [{"type": "text", "text": "claude 提问"}]}},
        {"type": "assistant", "sessionId": "claude-sess", "timestamp": "2026-01-01T00:00:05Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "claude 回答"}]}},
    ]
    file.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                    encoding="utf-8")
    p = _paths(tmp_path)
    result = transcript.import_sessions(source="claude", dry_run=False, confirm=True,
                                        claude_dir=base, **p)
    assert result["messages"]["imported"] == 2
    rows = [json.loads(x) for x in p["messages_path"].read_text(encoding="utf-8").splitlines()]
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert {r["session_id"] for r in rows} == {"claude-sess"}


def _trae_fixture(tmp_path):
    root = tmp_path / "appdata" / "Trae CN"
    db = root / "User" / "globalStorage" / "state.vscdb"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(str(db))
    con.execute("create table ItemTable (key text primary key, value blob)")
    payload = {"response": {"resp": {"result": [
        {"id": "20260101120000abc", "text": "trae 的一个提问"},
    ]}}}
    con.execute("insert into ItemTable values (?, ?)",
                (trae_reader.QUERY_KEY_PREFIX + "k1", json.dumps(payload).encode("utf-8")))
    con.commit()
    con.close()
    return tmp_path / "appdata"


def test_trae_prompt_trace_import(tmp_path):
    appdata = _trae_fixture(tmp_path)
    p = _paths(tmp_path)
    result = transcript.import_sessions(source="trae", dry_run=False, confirm=True,
                                        appdata=appdata, **p)
    assert result["messages"]["imported"] == 1
    row = json.loads(p["messages_path"].read_text(encoding="utf-8").splitlines()[0])
    assert row["role"] == "user"
    assert row["session_id"].startswith("trae-prompts-")
    # 重跑：Trae 走聚合指纹，不应重扫/重写
    again = transcript.import_sessions(source="trae", dry_run=False, confirm=True,
                                       appdata=appdata, **p)
    assert again["messages"]["imported"] == 0
    assert again["sessions"]["skipped_unchanged"] == 1


def test_unknown_source_rejected(tmp_path):
    from agent.memory_import.models import MemoryImportError

    with pytest.raises(MemoryImportError):
        transcript.import_sessions(source="nope", dry_run=True, **_paths(tmp_path))


# ── 路由契约 ──────────────────────────────────────────────

@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FLASK_API_TOKEN", TOKEN)
    app = Flask(__name__)
    routes.register_routes(app, None)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture()
def auth():
    return {"X-API-Token": TOKEN}


def test_route_requires_token(client):
    assert client.post("/api/memory/import/sessions", json={"source": "dsh"}).status_code == 401


def test_route_sources_lists_three(client, auth):
    resp = client.get("/api/memory/import/sessions/sources", headers=auth)
    assert resp.status_code == 200
    assert resp.headers.get(ENVELOPE_HEADER) == ENVELOPE_VERSION
    ids = [s["id"] for s in resp.get_json()["data"]["sources"]]
    assert ids == ["dsh", "claude", "trae"]


def test_route_dry_run_forwards_and_wraps(client, auth, monkeypatch):
    calls = {}

    def fake(**kwargs):
        calls.update(kwargs)
        return {"source": "dsh", "dry_run": True, "sessions": {}, "messages": {}}

    monkeypatch.setattr(routes.transcript, "import_sessions", fake)
    resp = client.post("/api/memory/import/sessions",
                       json={"source": "dsh", "dry_run": True, "limit_sessions": 2},
                       headers=auth)
    assert resp.status_code == 200
    assert calls["source"] == "dsh" and calls["dry_run"] is True
    assert calls["limit_sessions"] == 2
    assert resp.get_json()["data"]["source"] == "dsh"


def test_route_execute_needs_confirm(client, auth):
    resp = client.post("/api/memory/import/sessions",
                       json={"source": "dsh", "dry_run": False}, headers=auth)
    assert resp.status_code == 400
    assert resp.get_json()["type"].endswith("confirmation-required")


def test_route_latest_empty_is_null(client, auth, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.transcript, "latest_report", lambda source: None)
    resp = client.get("/api/memory/import/sessions/latest?source=dsh", headers=auth)
    assert resp.status_code == 200
    assert resp.get_json()["data"]["report"] is None

def test_legacy_pool_reports_session_counts_without_moving_legacy(tmp_path, monkeypatch):
    """导入只增会话面读数；未分会话冻结池必须保持原值（L5：不折算）。"""
    from agent import legacy_pool

    path = tmp_path / "messages.jsonl"
    rows = [
        {"role": "user", "content": "旧消息一"},
        {"role": "user", "content": "旧消息二"},
        {"role": "user", "content": "导入消息", "session_id": "s1",
         "source": "dsh", "import_key": "import:dsh:s1:1"},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")
    monkeypatch.setattr(legacy_pool, "LEGACY_POOL_PATH", path)
    legacy_pool.reset_cache()
    snap = legacy_pool.snapshot(refresh=True)
    assert snap["messages_total"] == 3
    assert snap["legacy_messages"] == 2
    assert snap["session_messages"] == 1
    assert snap["sessions"] == 1
    assert snap["imported_messages"] == 1
