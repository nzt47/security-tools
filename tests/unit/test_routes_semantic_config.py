"""routes_config.register_routes 语义层配置路由单元测试

覆盖 GET/POST /api/orchestrator/semantic-config：
- GET：返回合并配置 + API override
- POST：类型/范围校验（400）、成功热更（200）
所有外部副作用（Orchestrator 类成员/SQLite 持久化）均 mock，不触达真实状态。
"""
from types import SimpleNamespace
from unittest import mock

import pytest
from flask import Flask

from agent.server_routes.routes_config import register_routes


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    state = SimpleNamespace(
        Yunshu=mock.MagicMock(),
        session_mgr=mock.MagicMock(),
        network_config_mgr=mock.MagicMock(),
        search_engine=mock.MagicMock(),
        chat_history=[],
    )
    with mock.patch("agent.server_routes.routes_config.require_token", lambda f: f), \
         mock.patch("agent.server_routes.routes_config.log_request", lambda **kw: lambda f: f), \
         mock.patch("agent.server_routes.routes_config.trace_route", lambda *a, **kw: lambda f: f):
        register_routes(app, state)
    return app.test_client()


class TestSemanticConfigGet:
    """GET /api/orchestrator/semantic-config"""

    def test_get_returns_merged_config(self, client):
        with mock.patch("agent.orchestrator.orchestrator.Orchestrator._load_semantic_layer_config",
                        return_value={"min_score": 0.5, "top_k": 8}) as m_load, \
             mock.patch("agent.orchestrator.orchestrator.Orchestrator._SEM_API_OVERRIDE",
                        {"min_score": 0.6}):
            resp = client.get("/api/orchestrator/semantic-config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["config"] == {"min_score": 0.5, "top_k": 8}
        assert data["api_override"] == {"min_score": 0.6}
        m_load.assert_called_once()

    def test_get_failure_returns_500(self, client):
        with mock.patch("agent.orchestrator.orchestrator.Orchestrator._load_semantic_layer_config",
                        side_effect=RuntimeError("加载失败")):
            resp = client.get("/api/orchestrator/semantic-config")
        assert resp.status_code == 500
        assert resp.get_json()["ok"] is False


class TestSemanticConfigUpdate:
    """POST /api/orchestrator/semantic-config"""

    def test_update_success(self, client):
        with mock.patch("agent.orchestrator.orchestrator.Orchestrator._SEM_API_OVERRIDE", {}), \
             mock.patch("agent.orchestrator.orchestrator.Orchestrator._SEM_DEFAULTS",
                        {"min_score": 0.5, "top_k": 5}), \
             mock.patch("agent.orchestrator.orchestrator.Orchestrator._clear_semantic_config_cache") as m_clear, \
             mock.patch("agent.orchestrator.orchestrator.Orchestrator._save_semantic_override_to_db") as m_save, \
             mock.patch("agent.orchestrator.orchestrator.Orchestrator._load_semantic_layer_config",
                        return_value={"min_score": 0.5, "top_k": 8}):
            resp = client.post("/api/orchestrator/semantic-config",
                               json={"min_score": 0.5, "top_k": 8})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["overrides"] == {"min_score": 0.5, "top_k": 8}
        m_clear.assert_called_once()
        m_save.assert_called_once_with({"min_score": 0.5, "top_k": 8})

    def test_update_type_error_returns_400(self, client):
        resp = client.post("/api/orchestrator/semantic-config", json={"min_score": "abc"})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_update_range_error_returns_400(self, client):
        resp = client.post("/api/orchestrator/semantic-config", json={"min_score": 1.5})
        assert resp.status_code == 400

    def test_update_unknown_key_returns_400(self, client):
        resp = client.post("/api/orchestrator/semantic-config", json={"unknown_key": 1})
        assert resp.status_code == 400

    def test_update_invalid_fusion_mode_returns_400(self, client):
        resp = client.post("/api/orchestrator/semantic-config", json={"fusion_mode": "bad"})
        assert resp.status_code == 400
