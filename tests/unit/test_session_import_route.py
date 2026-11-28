"""E4-5 会话导入路由：POST /api/sessions/import（默认关 + 写路径二次闸）。"""
from __future__ import annotations

import io
import json
import sys
import types

import pytest
from flask import Flask

from agent import session_import as _si
from agent.server_routes import routes_session_import as mod


def _json(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


SAMPLE = [{
    "id": "c1", "title": "导入的会话", "inserted_at": "2026-11-20T10:00:00Z",
    "messages": [
        {"role": "user", "content": "第一问", "created_at": "2026-11-20T10:00:00Z"},
        {"role": "assistant", "content": "第一答", "created_at": "2026-11-20T10:00:05Z"},
    ],
}]


class _FakeMgr:
    """最小会话管理器：记录建了哪些会话、写了哪些消息。"""

    def __init__(self, boom=False):
        self.sessions = []
        self.messages = {}
        self.meta = {}
        self.current = "sess_orig"
        self.calls = 0
        self.boom = boom

    def get_current_id(self):
        return self.current

    def set_current(self, session_id):
        self.current = session_id
        return True

    def create_session(self, title=""):
        self.calls += 1
        if self.boom:
            raise RuntimeError("SECRET-BOOM")
        session_id = "sess_new%d" % (len(self.sessions) + 1)
        self.sessions.append({"id": session_id, "title": title})
        self.messages[session_id] = []
        self.current = session_id
        return {"id": session_id, "title": title}

    def add_message(self, session_id, role, content, timestamp=None):
        self.calls += 1
        self.messages[session_id].append(
            {"role": role, "content": content, "timestamp": timestamp})
        return {"role": role}

    def update_session_metadata(self, session_id, **fields):
        self.meta.setdefault(session_id, {}).update(fields)
        return True


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    def _make(mgr=None):
        fake = mgr or _FakeMgr()
        monkeypatch.setitem(sys.modules, "app_server",
                           types.SimpleNamespace(_session_mgr=fake))
        monkeypatch.setattr(mod, "_data_dir", lambda: tmp_path)
        app = Flask(__name__)
        app.config.update(TESTING=True)
        mod.register_routes(app)
        return app.test_client(), fake

    return _make


def _upload(client, payload, filename="export.json", **form):
    data = {"file": (io.BytesIO(payload), filename)}
    data.update(form)
    return client.post("/api/sessions/import", data=data,
                       content_type="multipart/form-data")


class TestDisabledByDefault:
    def test_默认关_回_disabled_且不读文件(self, make_client, monkeypatch):
        monkeypatch.delenv("CP_SESSION_IMPORT_ENABLED", raising=False)
        client, mgr = make_client()

        def _boom(*a, **k):
            raise AssertionError("开关关时不应解析上传内容")

        monkeypatch.setattr(_si, "parse_upload", _boom)
        body = _upload(client, _json(SAMPLE)).get_json()
        assert body["ok"] is False and body["code"] == "disabled"
        assert body["write_enabled"] is False
        assert mgr.calls == 0


class TestPreview:
    def test_预览只回计数_不建会话(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_SESSION_IMPORT_ENABLED", "1")
        client, mgr = make_client()
        body = _upload(client, _json(SAMPLE)).get_json()
        assert body["ok"] is True and body["write_enabled"] is False
        assert body["format"] == "deepseek.export.v1"
        assert body["session_count"] == 1 and body["duplicates"] == 0
        assert body["sessions"][0]["messages"] == 2
        assert "第一问" not in json.dumps(body, ensure_ascii=False)
        assert mgr.calls == 0 and mgr.sessions == []

    def test_预览标记重复(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_SESSION_IMPORT_ENABLED", "1")
        monkeypatch.setenv("CP_SESSION_IMPORT_WRITE_ENABLED", "1")
        client, _mgr = make_client()
        assert _upload(client, _json(SAMPLE), mode="apply", confirm="1").get_json()["ok"] is True
        body = _upload(client, _json(SAMPLE)).get_json()
        assert body["duplicates"] == 1 and body["sessions"][0]["duplicate"] is True

    def test_缺文件_400(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_SESSION_IMPORT_ENABLED", "1")
        client, _mgr = make_client()
        r = client.post("/api/sessions/import", data={},
                        content_type="multipart/form-data")
        assert r.status_code == 400 and r.get_json()["code"] == "missing_file"

    def test_坏_模式_400(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_SESSION_IMPORT_ENABLED", "1")
        client, _mgr = make_client()
        r = _upload(client, _json(SAMPLE), mode="write")
        assert r.status_code == 400 and r.get_json()["code"] == "bad_mode"

    def test_格式不支持_400(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_SESSION_IMPORT_ENABLED", "1")
        client, _mgr = make_client()
        r = _upload(client, _json({"foo": "bar"}))
        assert r.status_code == 400 and r.get_json()["code"] == "unsupported_format"

    def test_超体积_413(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_SESSION_IMPORT_ENABLED", "1")
        monkeypatch.setattr(_si, "MAX_UPLOAD_BYTES", 8)
        client, _mgr = make_client()
        r = _upload(client, b"0123456789")
        assert r.status_code == 413 and r.get_json()["code"] == "too_large"

    def test_后缀拒收_400(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_SESSION_IMPORT_ENABLED", "1")
        client, _mgr = make_client()
        r = _upload(client, _json(SAMPLE), filename="export.exe")
        assert r.status_code == 400 and r.get_json()["code"] == "unsupported_format"


class TestApplyGates:
    def test_写开关关_403(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_SESSION_IMPORT_ENABLED", "1")
        monkeypatch.delenv("CP_SESSION_IMPORT_WRITE_ENABLED", raising=False)
        client, mgr = make_client()
        r = _upload(client, _json(SAMPLE), mode="apply", confirm="1")
        assert r.status_code == 403 and r.get_json()["code"] == "write_disabled"
        assert mgr.calls == 0

    def test_无_confirm_400(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_SESSION_IMPORT_ENABLED", "1")
        monkeypatch.setenv("CP_SESSION_IMPORT_WRITE_ENABLED", "1")
        client, mgr = make_client()
        r = _upload(client, _json(SAMPLE), mode="apply")
        assert r.status_code == 400 and r.get_json()["code"] == "confirmation_required"
        assert mgr.calls == 0

    def test_并发导入_409(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_SESSION_IMPORT_ENABLED", "1")
        monkeypatch.setenv("CP_SESSION_IMPORT_WRITE_ENABLED", "1")
        client, _mgr = make_client()
        assert mod._RUN_LOCK.acquire(blocking=False) is True
        try:
            r = _upload(client, _json(SAMPLE), mode="apply", confirm="1")
        finally:
            mod._RUN_LOCK.release()
        assert r.status_code == 409 and r.get_json()["code"] == "busy"


class TestApply:
    def test_写入会话与消息_保留时间戳_恢复当前会话(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_SESSION_IMPORT_ENABLED", "1")
        monkeypatch.setenv("CP_SESSION_IMPORT_WRITE_ENABLED", "1")
        client, mgr = make_client()
        body = _upload(client, _json(SAMPLE), mode="apply", confirm="1").get_json()
        assert body["ok"] is True and body["created_count"] == 1
        session = body["created"][0]
        assert session["title"] == "导入的会话" and session["messages"] == 2
        rows = mgr.messages[session["id"]]
        assert [r["role"] for r in rows] == ["user", "assistant"]
        assert rows[0]["timestamp"] == "2026-11-20T10:00:00Z"
        assert rows[1]["timestamp"] == "2026-11-20T10:00:05Z"
        assert mgr.meta[session["id"]]["imported"]["format"] == "deepseek.export.v1"
        assert mgr.meta[session["id"]]["imported"]["messages"] == 2
        assert mgr.current == "sess_orig", "导入不应把当前会话切走"

    def test_同一份导出再导一次_全部跳过(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_SESSION_IMPORT_ENABLED", "1")
        monkeypatch.setenv("CP_SESSION_IMPORT_WRITE_ENABLED", "1")
        client, mgr = make_client()
        first = _upload(client, _json(SAMPLE), mode="apply", confirm="1").get_json()
        second = _upload(client, _json(SAMPLE), mode="apply", confirm="1").get_json()
        assert first["created_count"] == 1
        assert second["created_count"] == 0 and second["skipped_duplicates"] == 1
        assert len(mgr.sessions) == 1

    def test_改名后再导仍算重复(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_SESSION_IMPORT_ENABLED", "1")
        monkeypatch.setenv("CP_SESSION_IMPORT_WRITE_ENABLED", "1")
        client, mgr = make_client()
        _upload(client, _json(SAMPLE), mode="apply", confirm="1")
        renamed = json.loads(json.dumps(SAMPLE))
        renamed[0]["title"] = "我改过的名字"
        second = _upload(client, _json(renamed), mode="apply", confirm="1").get_json()
        assert second["created_count"] == 0 and second["skipped_duplicates"] == 1
        assert len(mgr.sessions) == 1

    def test_写失败_500_不回异常原文且恢复当前会话(self, make_client, monkeypatch):
        monkeypatch.setenv("CP_SESSION_IMPORT_ENABLED", "1")
        monkeypatch.setenv("CP_SESSION_IMPORT_WRITE_ENABLED", "1")
        client, mgr = make_client(_FakeMgr(boom=True))
        r = _upload(client, _json(SAMPLE), mode="apply", confirm="1")
        assert r.status_code == 500 and r.get_json()["code"] == "import_failed"
        assert "SECRET-BOOM" not in json.dumps(r.get_json(), ensure_ascii=False)
        assert mgr.current == "sess_orig"

class TestRealSessionManager:
    """与**真实** SessionManager 的集成（不桩会话库）：建会话/写消息/元数据/当前会话恢复。"""

    def test_真实会话库_建会话与消息落盘(self, monkeypatch, tmp_path):
        from agent.session_manager import SessionManager

        mgr = SessionManager(sessions_dir=str(tmp_path / "sessions"))
        # 先建一个"原当前会话"：导入必须把它恢复回去（生产里总有当前会话）
        original = mgr.create_session(title="原会话")["id"]
        mgr.set_current(original)
        monkeypatch.setenv("CP_SESSION_IMPORT_ENABLED", "1")
        monkeypatch.setenv("CP_SESSION_IMPORT_WRITE_ENABLED", "1")
        monkeypatch.setitem(sys.modules, "app_server",
                            types.SimpleNamespace(_session_mgr=mgr))
        monkeypatch.setattr(mod, "_data_dir", lambda: tmp_path / "import")
        app = Flask(__name__)
        app.config.update(TESTING=True)
        mod.register_routes(app)
        client = app.test_client()

        body = _upload(client, _json(SAMPLE), mode="apply", confirm="1").get_json()
        assert body["ok"] is True and body["created_count"] == 1
        session_id = body["created"][0]["id"]

        messages_path = tmp_path / "sessions" / session_id / "messages.jsonl"
        rows = [json.loads(line) for line in
                messages_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert [(row["role"], row["content"]) for row in rows] == [
            ("user", "第一问"), ("assistant", "第一答")]
        assert rows[0]["timestamp"] == "2026-11-20T10:00:00Z"
        meta = json.loads((tmp_path / "sessions" / session_id / "meta.json")
                          .read_text(encoding="utf-8"))
        assert meta["title"] == "导入的会话"
        assert meta["imported"]["messages"] == 2
        index = json.loads((tmp_path / "sessions" / "sessions.json").read_text(encoding="utf-8"))
        imported_row = [row for row in index if row["id"] == session_id][0]
        assert imported_row["message_count"] == 2
        assert mgr.get_current_id() == original, "导入后必须恢复原来的当前会话"
