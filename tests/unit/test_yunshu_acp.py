"""E4-5 第五批：ACP stdio server（scripts/yunshu_acp.py）契约测试。

判据：initialize/session/new/session/prompt/session/cancel 的最小集语义；
坏 JSON / 未知方法不带走连接；后端失败只回脱敏短句；未启用时退出码 2。
"""
from __future__ import annotations

import importlib.util
import io
import json
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "yunshu_acp.py"


def _load():
    spec = importlib.util.spec_from_file_location("yunshu_acp_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def acp():
    return _load()


class _FakeTransport:
    def __init__(self, answer="好的", session_id="sess_acp1", boom=False):
        self.answer = answer
        self.session_id = session_id
        self.boom = boom
        self.titles = []
        self.chats = []

    def create_session(self, title):
        self.titles.append(title)
        return self.session_id

    def chat(self, session_id, text):
        self.chats.append((session_id, text))
        if self.boom:
            raise RuntimeError("HTTP 500: 后端出错")
        return self.answer


def _run(acp, lines, transport=None):
    out = io.StringIO()
    acp.serve(io.StringIO("\n".join(lines) + "\n"), out, transport or _FakeTransport())
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def _req(msg_id, method, params=None):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return json.dumps(body, ensure_ascii=False)


class TestInitialize:
    def test_回能力与版本(self, acp):
        out = _run(acp, [_req(1, "initialize", {"protocolVersion": 1, "clientCapabilities": {}})])
        result = out[0]["result"]
        assert result["protocolVersion"] == 1
        caps = result["agentCapabilities"]
        assert caps["loadSession"] is False
        assert caps["promptCapabilities"] == {"image": False, "audio": False, "embeddedContext": False}
        assert result["authMethods"] == []
        assert result["agentInfo"]["name"] == "yunshu"

    def test_不支持的版本回_1(self, acp):
        out = _run(acp, [_req(1, "initialize", {"protocolVersion": 9})])
        assert out[0]["result"]["protocolVersion"] == 1


class TestSessionNew:
    def test_建会话_标题带目录名(self, acp):
        transport = _FakeTransport(session_id="sess_abc")
        out = _run(acp, [_req(2, "session/new", {"cwd": "/home/me/proj", "mcpServers": []})], transport)
        assert out[0]["result"] == {"sessionId": "sess_abc"}
        assert transport.titles and "proj" in transport.titles[0]

    def test_没有_cwd_也有标题(self, acp):
        transport = _FakeTransport()
        _run(acp, [_req(2, "session/new", {})], transport)
        assert transport.titles[0].startswith("ACP")


class TestPrompt:
    def test_先发_update_再回_end_turn(self, acp):
        transport = _FakeTransport(answer="第一段")
        out = _run(acp, [
            _req(2, "session/new", {"cwd": "/p"}),
            _req(3, "session/prompt", {"sessionId": "sess_acp1", "prompt": [{"type": "text", "text": "你好"}]}),
        ], transport)
        updates = [m for m in out if m.get("method") == "session/update"]
        assert len(updates) == 1
        update = updates[0]["params"]
        assert update["sessionId"] == "sess_acp1"
        assert update["update"]["sessionUpdate"] == "agent_message_chunk"
        assert update["update"]["content"] == {"type": "text", "text": "第一段"}
        final = [m for m in out if m.get("id") == 3][0]
        assert final["result"] == {"stopReason": "end_turn"}
        assert transport.chats == [("sess_acp1", "你好")]

    def test_多个_text_块拼接_非_text_忽略(self, acp):
        transport = _FakeTransport()
        _run(acp, [_req(3, "session/prompt", {"sessionId": "s1", "prompt": [
            {"type": "text", "text": "上半"},
            {"type": "image", "data": "x"},
            {"type": "text", "text": "下半"},
        ]})], transport)
        assert transport.chats[0][1] == "上半\n下半"

    def test_没有_text_回_invalid_params(self, acp):
        out = _run(acp, [_req(3, "session/prompt", {"sessionId": "s1", "prompt": [{"type": "image"}]})])
        assert out[0]["error"]["code"] == acp.INVALID_PARAMS

    def test_缺_sessionId_回_invalid_params(self, acp):
        out = _run(acp, [_req(3, "session/prompt", {"prompt": [{"type": "text", "text": "x"}]})])
        assert out[0]["error"]["code"] == acp.INVALID_PARAMS

    def test_长回答分块_同一_messageId(self, acp, monkeypatch):
        monkeypatch.setattr(acp, "MAX_CHUNK_CHARS", 3)
        transport = _FakeTransport(answer="abcdefgh")
        out = _run(acp, [_req(3, "session/prompt", {"sessionId": "s1",
                                                   "prompt": [{"type": "text", "text": "q"}]})], transport)
        updates = [m["params"]["update"] for m in out if m.get("method") == "session/update"]
        assert "".join(u["content"]["text"] for u in updates) == "abcdefgh"
        assert len({u["messageId"] for u in updates}) == 1

    def test_后端失败_脱敏且连接不断(self, acp):
        transport = _FakeTransport(boom=True)
        out = _run(acp, [
            _req(3, "session/prompt", {"sessionId": "s1", "prompt": [{"type": "text", "text": "q"}]}),
            _req(4, "initialize", {"protocolVersion": 1}),
        ], transport)
        assert out[0]["error"]["code"] == acp.INTERNAL_ERROR
        assert "Traceback" not in json.dumps(out, ensure_ascii=False)
        assert out[1]["result"]["protocolVersion"] == 1, "一条失败不该带走连接"


class TestCancel:
    def test_取消后再发_prompt_回_cancelled_且不调后端(self, acp):
        transport = _FakeTransport()
        lines = [
            _req(2, "session/new", {"cwd": "/p"}),
            json.dumps({"jsonrpc": "2.0", "method": "session/cancel",
                        "params": {"sessionId": "sess_acp1"}}),
            _req(3, "session/prompt", {"sessionId": "sess_acp1",
                                       "prompt": [{"type": "text", "text": "q"}]}),
        ]
        out = _run(acp, lines, transport)
        assert out[-1]["result"] == {"stopReason": "cancelled"}
        assert transport.chats == []


class TestProtocolRobustness:
    def test_未知方法回_method_not_found(self, acp):
        out = _run(acp, [_req(5, "session/load", {"sessionId": "s1"})])
        assert out[0]["error"]["code"] == acp.METHOD_NOT_FOUND

    def test_坏_json_回_parse_error_且继续(self, acp):
        out = _run(acp, ["{not json", _req(6, "initialize", {"protocolVersion": 1})])
        assert out[0]["error"]["code"] == acp.PARSE_ERROR
        assert out[0]["id"] is None
        assert out[1]["result"]["protocolVersion"] == 1

    def test_非对象_json_回_invalid_request(self, acp):
        out = _run(acp, ["[1, 2, 3]"])
        assert out[0]["error"]["code"] == acp.INVALID_REQUEST

    def test_通知不产生响应(self, acp):
        out = _run(acp, [json.dumps({"jsonrpc": "2.0", "method": "session/cancel",
                                     "params": {"sessionId": "s1"}})])
        assert out == []

    def test_空行忽略(self, acp):
        out = _run(acp, ["", "   ", _req(7, "initialize", {"protocolVersion": 1})])
        assert len(out) == 1


class TestRestTransport:
    def test_建会话与对话走既有_REST(self, acp, monkeypatch):
        calls = []

        class _FakeCli:
            def token_from_env(self):
                return "tok"

            def request_json(self, method, path, payload=None, token="", timeout=30.0):
                calls.append((method, path, payload, token))
                if path == "/api/sessions":
                    return {"id": "sess_rest1"}
                return {"Yunshu": "REST 回答"}

        transport = acp.RestTransport()
        monkeypatch.setattr(transport, "_module", lambda: _FakeCli())
        session_id = transport.create_session("ACP · 会话")
        answer = transport.chat(session_id, "问题")
        assert session_id == "sess_rest1" and answer == "REST 回答"
        assert calls[0][:2] == ("POST", "/api/sessions")
        assert calls[0][2] == {"title": "ACP · 会话"}
        assert calls[1][:2] == ("POST", "/api/chat")
        assert calls[1][2] == {"message": "问题", "session_id": "sess_rest1"}
        assert all(c[3] == "tok" for c in calls)

    def test_响应缺_Yunshu_回空串(self, acp, monkeypatch):
        class _FakeCli:
            def token_from_env(self):
                return ""

            def request_json(self, *a, **k):
                return {"response": "别名"}

        transport = acp.RestTransport()
        monkeypatch.setattr(transport, "_module", lambda: _FakeCli())
        assert transport.chat("s1", "q") == "别名"


class TestGate:
    def test_未启用_退出码_2(self, acp, monkeypatch, capsys):
        monkeypatch.delenv(acp.ENABLED_ENV, raising=False)
        assert acp.main([]) == 2
        err = capsys.readouterr().err
        assert "未启用" in err

    def test_启用后_能说协议(self, acp, monkeypatch):
        monkeypatch.setenv(acp.ENABLED_ENV, "1")
        stdin = io.StringIO(_req(1, "initialize", {"protocolVersion": 1}) + "\n")
        stdout = io.StringIO()
        monkeypatch.setattr(acp.sys, "stdin", stdin)
        monkeypatch.setattr(acp.sys, "stdout", stdout)
        assert acp.main([]) == 0
        first = json.loads(stdout.getvalue().splitlines()[0])
        assert first["result"]["agentInfo"]["name"] == "yunshu"
