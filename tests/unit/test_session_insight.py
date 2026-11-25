"""E1-4c 会话派生视图（outline / stats / export）契约测试 + 路由形状。"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from agent.context import session_insight as I

MSGS = [
    {"role": "user", "content": "帮我看检查点功能", "timestamp": "2026-11-25T10:00:00"},
    {"role": "assistant", "content": "好的，我先只读勘察。",
     "timestamp": "2026-11-25T10:00:05",
     "steps": [{"title": "工具调用：read_file", "detail": "参数: {}", "status": "done"},
               {"title": "思考过程", "detail": "…", "status": "done"}]},
    {"role": "user", "content": "继续，改一下导出", "timestamp": "2026-11-25T10:01:00"},
    {"role": "assistant", "content": "改好了。", "timestamp": "2026-11-25T10:01:10",
     "tool_steps": [{"tool_call": {"name": "edit"}}],
     "tool_calls": [{"name": "present"}]},
]


# ── 纯函数 ──────────────────────────────────────────────────


def test_tool_names_三种来源都要认():
    names = I.tool_names(MSGS[1])
    assert names == ["read_file"]
    names2 = I.tool_names(MSGS[3])
    assert names2 == ["edit", "present"]


def test_tool_names_去重():
    msg = {"steps": [{"title": "工具调用：edit"}, {"title": "工具调用：edit"}]}
    assert I.tool_names(msg) == ["edit"]


def test_rounds_按_user_切分():
    rows = I.rounds(MSGS)
    assert len(rows) == 2
    assert rows[0]["user"].startswith("帮我看检查点")
    assert rows[0]["tools"] == ["read_file"]
    assert rows[1]["tools"] == ["edit", "present"]
    assert rows[1]["assistant"] == "改好了。"


def test_rounds_不以_user_开头时补空轮():
    rows = I.rounds([{"role": "assistant", "content": "历史导入"}])
    assert len(rows) == 1 and rows[0]["user"] == "" and rows[0]["assistant"] == "历史导入"


def test_rounds_轮次重新连续编号():
    rows = I.rounds(MSGS)
    assert [r["round"] for r in rows] == [1, 2]


def test_outline_结论被压平截断():
    doc = I.outline(MSGS)
    assert doc["round_count"] == 2
    assert doc["rounds"][0]["conclusion"] == "好的，我先只读勘察。"
    assert len(doc["rounds"][1]["user"]) <= I.MAX_OUTLINE_CHARS


def test_stats_每轮与汇总():
    doc = I.stats(MSGS)
    assert doc["round_count"] == 2
    assert doc["totals"]["tools"] == 3
    assert doc["totals"]["tokens_in"] > 0 and doc["totals"]["tokens_out"] > 0
    assert doc["rounds"][1]["tool_names"] == ["edit", "present"]


def test_export_filename_按_sha256_派生():
    name = I.export_filename("sess_20261125_101010_deadbeef", "md")
    assert name.startswith("session_") and name.endswith(".md")
    assert "sess_" not in name and len(name) == len("session_") + 12 + 3


def test_to_markdown_含轮次与工具与截断标记():
    meta = {"id": "s1", "title": "检查点", "created_at": "2026-11-25T10:00:00"}
    text = I.to_markdown(meta, MSGS)
    assert "# 检查点" in text
    assert "## 第 1 轮" in text and "## 第 2 轮" in text
    assert "工具**：read_file" in text
    long_msg = [{"role": "user", "content": "长" * (I.MAX_EXPORT_CHARS + 10)}]
    assert "（已截断）" in I.to_markdown({"id": "s2"}, long_msg)


def test_to_zip_三个文件都在():
    buf = I.to_zip({"id": "s1", "title": "检查点"}, MSGS)
    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        names = set(zf.namelist())
        assert names == {"manifest.json", "transcript.md", "messages.jsonl"}
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["schema"] == "yunshu/session-export/v1"
        assert manifest["messages"] == len(MSGS)
        raw = zf.read("messages.jsonl").decode("utf-8")
        assert "帮我看检查点功能" in raw


# ── 路由 ────────────────────────────────────────────────────


class _Mgr:
    def __init__(self, meta, messages):
        self.meta = meta
        self.messages = messages

    def get_session_metadata(self, sid):
        return dict(self.meta) if self.meta else None

    def get_messages(self, sid, limit=50):
        return list(self.messages)


def _client(monkeypatch, meta, messages):
    import sys
    import types

    from flask import Flask

    fake = types.ModuleType("app_server")
    fake._session_mgr = _Mgr(meta, messages)
    monkeypatch.setitem(sys.modules, "app_server", fake)

    from agent.server_routes.routes_session_insight import register_routes

    app = Flask("session_insight")
    app.config.update(TESTING=True)
    register_routes(app)
    return app.test_client()


def test_路由_outline(monkeypatch):
    client = _client(monkeypatch, {"id": "s1", "title": "检查点"}, MSGS)
    data = client.get("/api/sessions/s1/outline").get_json()
    assert data["ok"] is True and data["round_count"] == 2
    assert data["rounds"][0]["tools"] == ["read_file"]


def test_路由_stats(monkeypatch):
    client = _client(monkeypatch, {"id": "s1", "message_count": 4}, MSGS)
    data = client.get("/api/sessions/s1/stats").get_json()
    assert data["ok"] is True and data["totals"]["rounds"] == 2


def test_路由_export_md_带派生文件名(monkeypatch):
    client = _client(monkeypatch, {"id": "s1", "title": "检查点"}, MSGS)
    resp = client.get("/api/sessions/s1/export?format=md")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["Content-Disposition"]
    assert "session_" in resp.headers["Content-Disposition"]
    assert "# 检查点" in resp.get_data(as_text=True)


def test_路由_export_zip(monkeypatch):
    client = _client(monkeypatch, {"id": "s1"}, MSGS)
    resp = client.get("/api/sessions/s1/export?format=zip")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
        assert "transcript.md" in zf.namelist()


def test_路由_export_非法格式_400(monkeypatch):
    client = _client(monkeypatch, {"id": "s1"}, MSGS)
    assert client.get("/api/sessions/s1/export?format=pdf").status_code == 400


def test_路由_会话不存在_404(monkeypatch):
    client = _client(monkeypatch, None, [])
    assert client.get("/api/sessions/nope/outline").status_code == 404
    assert client.get("/api/sessions/nope/stats").status_code == 404
    assert client.get("/api/sessions/nope/export").status_code == 404
