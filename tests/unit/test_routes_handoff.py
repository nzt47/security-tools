"""routes_sessions.register_handoff_routes 单元测试

覆盖 POST /api/handoff：
- 成功：mock generate_handoff 返回交接结果（200）
- ValueError → 400；其他异常 → 500
generate_handoff（LLM 压缩）与 state 依赖均 mock，不触达真实会话/LLM。
"""
from types import SimpleNamespace
from unittest import mock

import pytest
from flask import Flask

from agent.server_routes.routes_sessions import register_handoff_routes


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    state = SimpleNamespace(
        session_mgr=mock.MagicMock(),
        Yunshu=mock.MagicMock(_llm=mock.MagicMock()),
    )
    with mock.patch("agent.server_routes.routes_sessions.require_token", lambda f: f), \
         mock.patch("agent.server_routes.routes_sessions.log_request", lambda **kw: lambda f: f), \
         mock.patch("agent.server_routes.routes_sessions.trace_route", lambda *a, **kw: lambda f: f):
        register_handoff_routes(app, state)
    return app.test_client()


class TestHandoff:
    """POST /api/handoff"""

    def test_handoff_success(self, client):
        expected = {"ok": True, "path": "/tmp/handoff_xxx.md"}
        with mock.patch("agent.handoff.handoff_generator.generate_handoff",
                        return_value=expected) as m_gen:
            resp = client.post("/api/handoff", json={"session_id": "s1", "intent": "code review"})
        assert resp.status_code == 200
        assert resp.get_json() == expected
        m_gen.assert_called_once()
        _, kwargs = m_gen.call_args
        assert kwargs["session_id"] == "s1"
        assert kwargs["intent"] == "code review"

    def test_handoff_value_error_returns_400(self, client):
        with mock.patch("agent.handoff.handoff_generator.generate_handoff",
                        side_effect=ValueError("session_mgr 未初始化")):
            resp = client.post("/api/handoff", json={})
        assert resp.status_code == 400
        assert "session_mgr" in resp.get_json()["error"]

    def test_handoff_generic_error_returns_500(self, client):
        with mock.patch("agent.handoff.handoff_generator.generate_handoff",
                        side_effect=RuntimeError("LLM 调用失败")):
            resp = client.post("/api/handoff", json={})
        assert resp.status_code == 500
        assert "LLM" in resp.get_json()["error"]
