"""server_routes/routes_config.py 集成测试

覆盖范围:
    - 辅助函数: _trace_id / _get_current_session_id
    - LLM 配置: GET/POST /api/config (含 anthropic/openai 依赖检查)
    - 网络配置: GET/POST /api/network-config, reset, export, import, apply
    - LLM 实例管理: CRUD + default + test(含 urllib mock)
    - MCP 服务管理: CRUD + enable
    - 搜索引擎实例管理: CRUD + default + test(含 503/分支)
    - 配置变更日志: GET /api/config/logs

边缘情况覆盖:
    - api_llm_instance_test: stderr.reconfigure + urllib mock + key_preview 条件格式
    - api_apply_network_config: google_cx 特殊命名(不加 _api_key 后缀)
    - api_search_instance_update: api_key 三路分支(新key/掩码/空)
    - api_search_instance_delete: 延迟导入 sync_web_search_engines
    - api_search_instance_set_default: custom/builtin 分支 + ValueError
    - api_search_instance_test: web_search=None 返回 503(非 500)
    - api_llm_instance_set_default: 失败返回 500(非 404)
    - api_config POST: base_url 回退链 + 条件清空
"""

import json
import urllib.error
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from flask import Flask

from agent.server_routes.routes_config import (
    BUILTIN_ENGINES,
    _get_current_session_id,
    _trace_id,
    register_routes,
    validate_search_instance,
)


# ──────────────────────────────────────────────
# 装饰器 no-op patcher
# ──────────────────────────────────────────────

_NOOP_DECORATOR = lambda f: f
_NOOP_DECORATOR_FACTORY = lambda *a, **kw: (lambda f: f)


# ──────────────────────────────────────────────
# Fixture: Flask app + mock state
# ──────────────────────────────────────────────


@pytest.fixture
def mock_state():
    """构造 mock state 对象,包含所有 register_routes 需要的属性"""
    state = MagicMock()
    state.Yunshu = MagicMock()
    state.session_mgr = MagicMock()
    state.network_config_mgr = MagicMock()
    state.search_engine = MagicMock()
    state.chat_history = MagicMock()

    # session_mgr 默认行为: 有当前会话
    state.session_mgr.get_current_id.return_value = "session_001"
    state.session_mgr.create_session.return_value = {"id": "new_session_001"}

    return state


@pytest.fixture
def config_client(mock_state):
    """Flask test client,装饰器全部 patch 为 no-op

    返回 (client, mock_state) 元组,测试中可通过 mock_state.xxx 调整 mock 行为
    """
    app = Flask(__name__)
    app.testing = True

    with patch("agent.server_routes.routes_config.require_token", _NOOP_DECORATOR), \
         patch("agent.server_routes.routes_config.log_request", _NOOP_DECORATOR_FACTORY), \
         patch("agent.server_routes.routes_config.trace_route", _NOOP_DECORATOR_FACTORY):
        register_routes(app, mock_state)

    client = app.test_client()
    return client, mock_state


@pytest.fixture
def client(config_client):
    """便捷 fixture: 只返回 client"""
    return config_client[0]


@pytest.fixture
def state(config_client):
    """便捷 fixture: 只返回 mock_state"""
    return config_client[1]


@pytest.fixture
def no_search_client_and_state(mock_state):
    """web_search=None 的 client + state(用于测试 503/跳过搜索引擎场景)

    注意: web_search 在 register_routes 时捕获,故必须在注册前设为 None
    """
    mock_state.search_engine = None
    app = Flask(__name__)
    app.testing = True
    with patch("agent.server_routes.routes_config.require_token", _NOOP_DECORATOR), \
         patch("agent.server_routes.routes_config.log_request", _NOOP_DECORATOR_FACTORY), \
         patch("agent.server_routes.routes_config.trace_route", _NOOP_DECORATOR_FACTORY):
        register_routes(app, mock_state)
    return app.test_client(), mock_state


@pytest.fixture
def no_search_client(no_search_client_and_state):
    return no_search_client_and_state[0]


@pytest.fixture
def no_search_state(no_search_client_and_state):
    return no_search_client_and_state[1]


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────


def post_json(client, path, payload):
    """POST JSON 请求便捷函数"""
    return client.post(path, data=json.dumps(payload), content_type="application/json")


def put_json(client, path, payload):
    """PUT JSON 请求便捷函数"""
    return client.put(path, data=json.dumps(payload), content_type="application/json")


# ──────────────────────────────────────────────
# 1. 辅助函数测试
# ──────────────────────────────────────────────


class TestHelpers:
    """_trace_id / _get_current_session_id"""

    def test_trace_id_returns_16_char_hex(self):
        tid = _trace_id()
        assert len(tid) == 16
        int(tid, 16)  # 应为合法 hex

    def test_trace_id_unique(self):
        ids = {_trace_id() for _ in range(20)}
        assert len(ids) == 20

    def test_get_current_session_id_existing(self):
        """有当前会话时直接返回"""
        mgr = MagicMock()
        mgr.get_current_id.return_value = "existing_session"
        assert _get_current_session_id(mgr) == "existing_session"
        mgr.create_session.assert_not_called()

    def test_get_current_session_id_creates_new(self):
        """无当前会话时创建新会话"""
        mgr = MagicMock()
        mgr.get_current_id.return_value = None
        mgr.create_session.return_value = {"id": "new_session"}
        assert _get_current_session_id(mgr) == "new_session"
        mgr.create_session.assert_called_once_with("新会话")


# ──────────────────────────────────────────────
# 2. LLM 配置路由
# ──────────────────────────────────────────────


class TestLlmConfig:
    """GET/POST /api/config"""

    def test_get_config(self, client, state):
        """GET 返回 Yunshu.get_config()"""
        state.Yunshu.get_config.return_value = {"provider": "openai", "model": "gpt-4"}
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["provider"] == "openai"

    def test_post_config_anthropic_missing_lib(self, client, state):
        """provider=anthropic 且缺少 anthropic 库 → ok:False"""
        with patch.dict("sys.modules", {"anthropic": None}):
            resp = post_json(client, "/api/config", {"provider": "anthropic"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False
        assert "anthropic" in data["error"]

    def test_post_config_openai_missing_lib(self, client, state):
        """provider=openai 且缺少 openai 库 → ok:False"""
        with patch.dict("sys.modules", {"openai": None}):
            resp = post_json(client, "/api/config", {"provider": "openai"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False
        assert "openai" in data["error"]

    def test_post_config_deepseek_missing_lib(self, client, state):
        """provider=deepseek 且缺少 openai 库 → ok:False"""
        with patch.dict("sys.modules", {"openai": None}):
            resp = post_json(client, "/api/config", {"provider": "deepseek"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False
        assert "openai" in data["error"]

    def test_post_config_success_clears_history(self, client, state):
        """配置成功时清空 messages 和 chat_history"""
        state.Yunshu.configure_llm.return_value = {"ok": True}
        resp = post_json(client, "/api/config", {
            "provider": "openai", "api_key": "sk-xxx", "model": "gpt-4"
        })
        assert resp.status_code == 200
        state.session_mgr.clear_messages.assert_called_once()
        state.chat_history.clear.assert_called_once()

    def test_post_config_fail_no_clear(self, client, state):
        """配置失败时不清空"""
        state.Yunshu.configure_llm.return_value = {"ok": False, "error": "bad key"}
        resp = post_json(client, "/api/config", {"provider": "openai"})
        assert resp.status_code == 200
        state.session_mgr.clear_messages.assert_not_called()
        state.chat_history.clear.assert_not_called()

    def test_post_config_base_url_fallback(self, client, state):
        """base_url 缺失时回退到 api_endpoint"""
        state.Yunshu.configure_llm.return_value = {"ok": True}
        post_json(client, "/api/config", {
            "provider": "openai", "api_endpoint": "https://api.example.com"
        })
        call_kwargs = state.Yunshu.configure_llm.call_args[1]
        assert call_kwargs["base_url"] == "https://api.example.com"

    def test_post_config_base_url_empty_when_neither(self, client, state):
        """base_url 和 api_endpoint 都缺失时为空字符串"""
        state.Yunshu.configure_llm.return_value = {"ok": True}
        post_json(client, "/api/config", {"provider": "local"})
        call_kwargs = state.Yunshu.configure_llm.call_args[1]
        assert call_kwargs["base_url"] == ""

    def test_post_config_no_json_body(self, client, state):
        """空 JSON body 时不报错(provider 默认空字符串)"""
        state.Yunshu.configure_llm.return_value = {"ok": True}
        resp = client.post("/api/config", data="{}", content_type="application/json")
        assert resp.status_code == 200


# ──────────────────────────────────────────────
# 3. 网络配置路由
# ──────────────────────────────────────────────


class TestNetworkConfig:
    """GET/POST /api/network-config, reset, export, import, apply"""

    def test_network_config_get(self, client, state):
        state.network_config_mgr.get_all.return_value = {"llm": {"provider": "openai"}}
        resp = client.get("/api/network-config")
        assert resp.status_code == 200
        assert resp.get_json()["llm"]["provider"] == "openai"

    def test_network_config_update_success(self, client, state):
        state.network_config_mgr.update.return_value = {"updated": True}
        resp = post_json(client, "/api/network-config", {"llm": {"timeout": 60}})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["config"] == {"updated": True}
        state.network_config_mgr.apply_to_app.assert_called_once()

    def test_network_config_update_exception(self, client, state):
        state.network_config_mgr.update.side_effect = RuntimeError("db error")
        resp = post_json(client, "/api/network-config", {"llm": {}})
        assert resp.status_code == 500
        assert resp.get_json()["ok"] is False

    def test_network_config_reset(self, client, state):
        state.network_config_mgr.reset.return_value = {"reset": True}
        resp = client.post("/api/network-config/reset")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_network_config_export_success(self, client, state):
        state.network_config_mgr.export_config.return_value = '{"exported": true}'
        resp = client.get("/api/network-config/export")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["config_json"] == '{"exported": true}'

    def test_network_config_export_exception(self, client, state):
        state.network_config_mgr.export_config.side_effect = RuntimeError("fail")
        resp = client.get("/api/network-config/export")
        assert resp.status_code == 500

    def test_network_config_import_missing_json(self, client, state):
        resp = post_json(client, "/api/network-config/import", {})
        assert resp.status_code == 400
        assert "config_json" in resp.get_json()["error"]

    def test_network_config_import_value_error(self, client, state):
        state.network_config_mgr.import_config.side_effect = ValueError("bad format")
        resp = post_json(client, "/api/network-config/import", {"config_json": "invalid"})
        assert resp.status_code == 400
        assert "bad format" in resp.get_json()["error"]

    def test_network_config_import_success(self, client, state):
        state.network_config_mgr.import_config.return_value = {"imported": True}
        resp = post_json(client, "/api/network-config/import", {"config_json": '{"v":1}'})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        state.network_config_mgr.apply_to_app.assert_called_once()

    def test_network_config_import_exception(self, client, state):
        state.network_config_mgr.import_config.side_effect = RuntimeError("io error")
        resp = post_json(client, "/api/network-config/import", {"config_json": "x"})
        assert resp.status_code == 500

    def test_apply_network_config_success(self, client, state):
        state.network_config_mgr.get_raw_config.return_value = {
            "search": {"engine_priority": ["duckduckgo"], "timeout": 30},
            "search_api_keys": {},
        }
        state.network_config_mgr.get_search_engines.return_value = []
        resp = client.post("/api/apply-network-config")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        state.search_engine.update_config.assert_called_once()

    def test_apply_network_config_with_api_keys(self, client, state):
        """google_cx 特殊命名:不加 _api_key 后缀"""
        state.network_config_mgr.get_raw_config.return_value = {
            "search": {},
            "search_api_keys": {
                "tavily": "tv-key",
                "google_cx": "cx-value",
                "bing": "bg-key",
            },
        }
        state.network_config_mgr.get_search_engines.return_value = []
        resp = client.post("/api/apply-network-config")
        assert resp.status_code == 200
        update_config = state.search_engine.update_config.call_args[0][0]
        # tavily → tavily_api_key
        assert update_config["tavily_api_key"] == "tv-key"
        # google_cx → google_cx (不加后缀)
        assert update_config["google_cx"] == "cx-value"
        # bing → bing_api_key
        assert update_config["bing_api_key"] == "bg-key"

    def test_apply_network_config_no_web_search(self, no_search_client, no_search_state):
        """web_search=None 时跳过 update_config"""
        no_search_state.network_config_mgr.get_raw_config.return_value = {"search": {}, "search_api_keys": {}}
        no_search_state.network_config_mgr.get_search_engines.return_value = []
        resp = no_search_client.post("/api/apply-network-config")
        assert resp.status_code == 200

    def test_apply_network_config_exception(self, client, state):
        state.network_config_mgr.apply_to_app.side_effect = RuntimeError("crash")
        resp = client.post("/api/apply-network-config")
        assert resp.status_code == 500


# ──────────────────────────────────────────────
# 4. LLM 实例管理路由
# ──────────────────────────────────────────────


class TestLlmInstances:
    """CRUD /api/llm/instances + default + test"""

    def test_llm_instances_list_success(self, client, state):
        state.network_config_mgr.get_llm_instances.return_value = [{"id": "i1"}]
        resp = client.get("/api/llm/instances")
        assert resp.status_code == 200
        assert resp.get_json()["instances"] == [{"id": "i1"}]

    def test_llm_instances_list_exception(self, client, state):
        state.network_config_mgr.get_llm_instances.side_effect = RuntimeError("fail")
        resp = client.get("/api/llm/instances")
        assert resp.status_code == 500

    def test_llm_instance_get_found(self, client, state):
        state.network_config_mgr.get_llm_instance.return_value = {"id": "i1"}
        resp = client.get("/api/llm/instances/i1")
        assert resp.status_code == 200

    def test_llm_instance_get_not_found(self, client, state):
        state.network_config_mgr.get_llm_instance.return_value = None
        resp = client.get("/api/llm/instances/missing")
        assert resp.status_code == 404

    def test_llm_instance_get_exception(self, client, state):
        state.network_config_mgr.get_llm_instance.side_effect = RuntimeError("fail")
        resp = client.get("/api/llm/instances/i1")
        assert resp.status_code == 500

    def test_llm_instance_add_validation_error(self, client, state):
        state.network_config_mgr.validate_llm_instance.return_value = ["name is required"]
        resp = post_json(client, "/api/llm/instances", {"instance": {}})
        assert resp.status_code == 400
        assert "name is required" in resp.get_json()["errors"]

    def test_llm_instance_add_value_error(self, client, state):
        state.network_config_mgr.validate_llm_instance.return_value = []
        state.network_config_mgr.add_llm_instance.side_effect = ValueError("dup id")
        resp = post_json(client, "/api/llm/instances", {"instance": {"name": "test"}})
        assert resp.status_code == 400

    def test_llm_instance_add_success(self, client, state):
        state.network_config_mgr.validate_llm_instance.return_value = []
        state.network_config_mgr.add_llm_instance.return_value = {"id": "new1", "name": "test"}
        resp = post_json(client, "/api/llm/instances", {"instance": {"name": "test"}})
        assert resp.status_code == 200
        assert resp.get_json()["instance"]["id"] == "new1"
        state.network_config_mgr.apply_to_app.assert_called_once()

    def test_llm_instance_add_exception(self, client, state):
        state.network_config_mgr.validate_llm_instance.return_value = []
        state.network_config_mgr.add_llm_instance.side_effect = RuntimeError("db")
        resp = post_json(client, "/api/llm/instances", {"instance": {}})
        assert resp.status_code == 500

    def test_llm_instance_update_found(self, client, state):
        state.network_config_mgr.update_llm_instance.return_value = {"id": "i1", "name": "updated"}
        resp = put_json(client, "/api/llm/instances/i1", {"updates": {"name": "updated"}})
        assert resp.status_code == 200
        state.network_config_mgr.apply_to_app.assert_called_once()

    def test_llm_instance_update_not_found(self, client, state):
        state.network_config_mgr.update_llm_instance.return_value = None
        resp = put_json(client, "/api/llm/instances/missing", {"updates": {}})
        assert resp.status_code == 404

    def test_llm_instance_update_value_error(self, client, state):
        state.network_config_mgr.update_llm_instance.side_effect = ValueError("bad")
        resp = put_json(client, "/api/llm/instances/i1", {"updates": {}})
        assert resp.status_code == 400

    def test_llm_instance_update_exception(self, client, state):
        state.network_config_mgr.update_llm_instance.side_effect = RuntimeError("fail")
        resp = put_json(client, "/api/llm/instances/i1", {"updates": {}})
        assert resp.status_code == 500

    def test_llm_instance_delete_success(self, client, state):
        state.network_config_mgr.delete_llm_instance.return_value = True
        resp = client.delete("/api/llm/instances/i1")
        assert resp.status_code == 200
        state.network_config_mgr.apply_to_app.assert_called_once()

    def test_llm_instance_delete_not_found(self, client, state):
        state.network_config_mgr.delete_llm_instance.return_value = False
        resp = client.delete("/api/llm/instances/missing")
        assert resp.status_code == 404

    def test_llm_instance_delete_exception(self, client, state):
        state.network_config_mgr.delete_llm_instance.side_effect = RuntimeError("fail")
        resp = client.delete("/api/llm/instances/i1")
        assert resp.status_code == 500

    def test_llm_instance_set_default_success(self, client, state):
        state.network_config_mgr.set_default_llm_instance.return_value = True
        resp = client.post("/api/llm/instances/i1/default")
        assert resp.status_code == 200
        state.network_config_mgr.apply_to_app.assert_called_once()

    def test_llm_instance_set_default_fail(self, client, state):
        """失败返回 500(非 404)"""
        state.network_config_mgr.set_default_llm_instance.return_value = False
        resp = client.post("/api/llm/instances/missing/default")
        assert resp.status_code == 500

    def test_llm_instance_set_default_exception(self, client, state):
        state.network_config_mgr.set_default_llm_instance.side_effect = RuntimeError("fail")
        resp = client.post("/api/llm/instances/i1/default")
        assert resp.status_code == 500


class TestLlmInstanceTest:
    """POST /api/llm/instances/<id>/test — 最复杂路由

    边缘情况:
    - stderr.reconfigure 在 try 块外
    - urllib.request.urlopen 需要 mock
    - key_preview 条件格式(len > 16 截取)
    - HTTPError 特定异常处理
    """

    def test_llm_instance_test_not_found(self, client, state):
        """实例不存在 → 404"""
        state.network_config_mgr.get_raw_config.return_value = {"llm_instances": []}
        resp = client.post("/api/llm/instances/missing/test")
        assert resp.status_code == 404

    def test_llm_instance_test_no_api_key(self, client, state):
        """api_key 为空 → ok:False(200)"""
        state.network_config_mgr.get_raw_config.return_value = {
            "llm_instances": [{"id": "i1", "provider": "openai", "api_key": "", "model": "gpt-4", "api_endpoint": "https://api.openai.com"}]
        }
        resp = client.post("/api/llm/instances/i1/test")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False
        assert "API Key" in data["error"]

    @patch("urllib.request.urlopen")
    def test_llm_instance_test_success(self, mock_urlopen, client, state):
        """成功测试连通性"""
        state.network_config_mgr.get_raw_config.return_value = {
            "llm_instances": [{
                "id": "i1", "provider": "openai", "api_key": "sk-1234567890abcdef",
                "model": "gpt-4", "api_endpoint": "https://api.openai.com"
            }]
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "OK"}}]
        }).encode()
        mock_urlopen.return_value = mock_resp

        resp = client.post("/api/llm/instances/i1/test")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["response"] == "OK"
        assert data["provider"] == "openai"
        assert data["model"] == "gpt-4"

    @patch("urllib.request.urlopen")
    def test_llm_instance_test_http_error(self, mock_urlopen, client, state):
        """HTTPError → 500 + debug info"""
        state.network_config_mgr.get_raw_config.return_value = {
            "llm_instances": [{
                "id": "i1", "provider": "openai", "api_key": "sk-1234567890abcdef",
                "model": "gpt-4", "api_endpoint": "https://api.openai.com"
            }]
        }
        mock_http_error = urllib.error.HTTPError(
            url="https://api.openai.com/v1/chat/completions",
            code=401, msg="Unauthorized",
            hdrs=None, fp=None,
        )
        mock_urlopen.side_effect = mock_http_error

        resp = client.post("/api/llm/instances/i1/test")
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["ok"] is False
        assert "HTTP 401" in data["error"]
        assert "key_preview" in data["debug"]

    @patch("urllib.request.urlopen")
    def test_llm_instance_test_exception(self, mock_urlopen, client, state):
        """其他异常 → 500 + debug info"""
        state.network_config_mgr.get_raw_config.return_value = {
            "llm_instances": [{
                "id": "i1", "provider": "openai", "api_key": "sk-1234567890abcdef",
                "model": "gpt-4", "api_endpoint": "https://api.openai.com"
            }]
        }
        mock_urlopen.side_effect = ConnectionError("timeout")

        resp = client.post("/api/llm/instances/i1/test")
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["ok"] is False
        assert "timeout" in data["error"]

    @patch("urllib.request.urlopen")
    def test_llm_instance_test_key_preview_short(self, mock_urlopen, client, state):
        """api_key <= 16 字符时 key_preview 为完整 key"""
        state.network_config_mgr.get_raw_config.return_value = {
            "llm_instances": [{
                "id": "i1", "provider": "openai", "api_key": "shortkey123456",  # 13 chars
                "model": "gpt-4", "api_endpoint": "https://api.openai.com"
            }]
        }
        mock_urlopen.side_effect = ConnectionError("fail")
        resp = client.post("/api/llm/instances/i1/test")
        data = resp.get_json()
        assert data["debug"]["key_preview"] == "shortkey123456"

    @patch("urllib.request.urlopen")
    def test_llm_instance_test_key_preview_long(self, mock_urlopen, client, state):
        """api_key > 16 字符时 key_preview 截取前10+后6"""
        state.network_config_mgr.get_raw_config.return_value = {
            "llm_instances": [{
                "id": "i1", "provider": "openai", "api_key": "sk-1234567890abcdefGHIJKL",
                "model": "gpt-4", "api_endpoint": "https://api.openai.com"
            }]
        }
        mock_urlopen.side_effect = ConnectionError("fail")
        resp = client.post("/api/llm/instances/i1/test")
        data = resp.get_json()
        preview = data["debug"]["key_preview"]
        assert preview == "sk-1234567...GHIJKL"


# ──────────────────────────────────────────────
# 5. MCP 服务管理路由
# ──────────────────────────────────────────────


class TestMcpServices:
    """CRUD /api/mcp/services + enable"""

    def test_mcp_services_list_success(self, client, state):
        state.network_config_mgr.get_mcp_services.return_value = [{"id": "s1"}]
        resp = client.get("/api/mcp/services")
        assert resp.status_code == 200
        assert resp.get_json()["services"] == [{"id": "s1"}]

    def test_mcp_services_list_exception(self, client, state):
        state.network_config_mgr.get_mcp_services.side_effect = RuntimeError("fail")
        resp = client.get("/api/mcp/services")
        assert resp.status_code == 500

    def test_mcp_service_get_found(self, client, state):
        state.network_config_mgr.get_mcp_service.return_value = {"id": "s1"}
        resp = client.get("/api/mcp/services/s1")
        assert resp.status_code == 200

    def test_mcp_service_get_not_found(self, client, state):
        state.network_config_mgr.get_mcp_service.return_value = None
        resp = client.get("/api/mcp/services/missing")
        assert resp.status_code == 404

    def test_mcp_service_get_exception(self, client, state):
        state.network_config_mgr.get_mcp_service.side_effect = RuntimeError("fail")
        resp = client.get("/api/mcp/services/s1")
        assert resp.status_code == 500

    def test_mcp_service_add_validation_error(self, client, state):
        state.network_config_mgr.validate_mcp_service.return_value = ["name required"]
        resp = post_json(client, "/api/mcp/services", {"service": {}})
        assert resp.status_code == 400
        assert "name required" in resp.get_json()["errors"]

    def test_mcp_service_add_value_error(self, client, state):
        state.network_config_mgr.validate_mcp_service.return_value = []
        state.network_config_mgr.add_mcp_service.side_effect = ValueError("dup")
        resp = post_json(client, "/api/mcp/services", {"service": {}})
        assert resp.status_code == 400

    def test_mcp_service_add_success(self, client, state):
        state.network_config_mgr.validate_mcp_service.return_value = []
        state.network_config_mgr.add_mcp_service.return_value = {"id": "new1"}
        resp = post_json(client, "/api/mcp/services", {"service": {"name": "test"}})
        assert resp.status_code == 200

    def test_mcp_service_add_exception(self, client, state):
        state.network_config_mgr.validate_mcp_service.return_value = []
        state.network_config_mgr.add_mcp_service.side_effect = RuntimeError("db")
        resp = post_json(client, "/api/mcp/services", {"service": {}})
        assert resp.status_code == 500

    def test_mcp_service_update_found(self, client, state):
        state.network_config_mgr.update_mcp_service.return_value = {"id": "s1"}
        resp = put_json(client, "/api/mcp/services/s1", {"updates": {"name": "updated"}})
        assert resp.status_code == 200

    def test_mcp_service_update_not_found(self, client, state):
        state.network_config_mgr.update_mcp_service.return_value = None
        resp = put_json(client, "/api/mcp/services/missing", {"updates": {}})
        assert resp.status_code == 404

    def test_mcp_service_update_value_error(self, client, state):
        state.network_config_mgr.update_mcp_service.side_effect = ValueError("bad")
        resp = put_json(client, "/api/mcp/services/s1", {"updates": {}})
        assert resp.status_code == 400

    def test_mcp_service_update_exception(self, client, state):
        state.network_config_mgr.update_mcp_service.side_effect = RuntimeError("fail")
        resp = put_json(client, "/api/mcp/services/s1", {"updates": {}})
        assert resp.status_code == 500

    def test_mcp_service_delete_success(self, client, state):
        state.network_config_mgr.delete_mcp_service.return_value = True
        resp = client.delete("/api/mcp/services/s1")
        assert resp.status_code == 200

    def test_mcp_service_delete_not_found(self, client, state):
        state.network_config_mgr.delete_mcp_service.return_value = False
        resp = client.delete("/api/mcp/services/missing")
        assert resp.status_code == 404

    def test_mcp_service_delete_exception(self, client, state):
        state.network_config_mgr.delete_mcp_service.side_effect = RuntimeError("fail")
        resp = client.delete("/api/mcp/services/s1")
        assert resp.status_code == 500

    def test_mcp_enable_success(self, client, state):
        state.network_config_mgr.get_raw_config.return_value = {"mcp": {"enabled": False}}
        resp = post_json(client, "/api/mcp/enable", {"enabled": True})
        assert resp.status_code == 200
        assert resp.get_json()["enabled"] is True
        state.network_config_mgr.update.assert_called_once()

    def test_mcp_enable_exception(self, client, state):
        state.network_config_mgr.get_raw_config.side_effect = RuntimeError("fail")
        resp = post_json(client, "/api/mcp/enable", {"enabled": True})
        assert resp.status_code == 500


# ──────────────────────────────────────────────
# 6. 搜索引擎实例管理路由
# ──────────────────────────────────────────────


class TestSearchInstances:
    """CRUD /api/search/instances + default + test"""

    def test_search_instances_list_success(self, client, state):
        state.network_config_mgr.get_all.return_value = {"search_instances": [{"id": "s1"}]}
        resp = client.get("/api/search/instances")
        assert resp.status_code == 200
        assert resp.get_json()["instances"] == [{"id": "s1"}]

    def test_search_instances_list_exception(self, client, state):
        state.network_config_mgr.get_all.side_effect = RuntimeError("fail")
        resp = client.get("/api/search/instances")
        assert resp.status_code == 500

    def test_search_instance_add_validation_error(self, client, state):
        """校验失败 → 400"""
        resp = post_json(client, "/api/search/instances", {"instance": {"engine_type": "unknown"}})
        assert resp.status_code == 400
        assert "errors" in resp.get_json()

    def test_search_instance_add_success(self, client, state):
        """正常添加(含加密保存 API Key)"""
        state.network_config_mgr.get_raw_config.return_value = {"search_instances": []}
        resp = post_json(client, "/api/search/instances", {
            "instance": {"name": "test", "engine_type": "duckduckgo", "api_key": "key123"}
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "id" in data["instance"]
        # 加密保存被调用
        state.network_config_mgr._save_secure.assert_called_once()

    def test_search_instance_add_no_api_key(self, client, state):
        """无 api_key → 跳过加密"""
        state.network_config_mgr.get_raw_config.return_value = {"search_instances": []}
        resp = post_json(client, "/api/search/instances", {
            "instance": {"name": "test", "engine_type": "duckduckgo"}
        })
        assert resp.status_code == 200
        state.network_config_mgr._save_secure.assert_not_called()

    def test_search_instance_add_masked_api_key(self, client, state):
        """api_key='***' → 跳过加密"""
        state.network_config_mgr.get_raw_config.return_value = {"search_instances": []}
        resp = post_json(client, "/api/search/instances", {
            "instance": {"name": "test", "engine_type": "duckduckgo", "api_key": "***"}
        })
        assert resp.status_code == 200
        state.network_config_mgr._save_secure.assert_not_called()

    def test_search_instance_add_custom_engine(self, client, state):
        """自定义引擎需 api_endpoint"""
        state.network_config_mgr.get_raw_config.return_value = {"search_instances": []}
        resp = post_json(client, "/api/search/instances", {
            "instance": {"name": "custom", "engine_type": "custom", "api_endpoint": "https://api.custom.com"}
        })
        assert resp.status_code == 200

    def test_search_instance_add_exception(self, client, state):
        state.network_config_mgr.get_raw_config.side_effect = RuntimeError("fail")
        resp = post_json(client, "/api/search/instances", {
            "instance": {"name": "test", "engine_type": "duckduckgo"}
        })
        assert resp.status_code == 500

    def test_search_instance_update_found_with_new_key(self, client, state):
        """更新实例 + 新 api_key → 加密保存"""
        state.network_config_mgr.get_raw_config.return_value = {
            "search_instances": [{"id": "s1", "name": "old", "engine_type": "duckduckgo"}]
        }
        resp = put_json(client, "/api/search/instances/s1", {
            "updates": {"name": "new", "api_key": "new_key_123"}
        })
        assert resp.status_code == 200
        state.network_config_mgr._save_secure.assert_called_once()

    def test_search_instance_update_found_masked_key(self, client, state):
        """更新实例 + api_key='***' → 从 updates 移除(不覆盖)"""
        state.network_config_mgr.get_raw_config.return_value = {
            "search_instances": [{"id": "s1", "name": "old", "engine_type": "duckduckgo"}]
        }
        resp = put_json(client, "/api/search/instances/s1", {
            "updates": {"name": "new", "api_key": "***"}
        })
        assert resp.status_code == 200
        state.network_config_mgr._save_secure.assert_not_called()

    def test_search_instance_update_found_no_key(self, client, state):
        """更新实例 + 无 api_key → 不触发加密"""
        state.network_config_mgr.get_raw_config.return_value = {
            "search_instances": [{"id": "s1", "name": "old", "engine_type": "duckduckgo"}]
        }
        resp = put_json(client, "/api/search/instances/s1", {"updates": {"name": "new"}})
        assert resp.status_code == 200
        state.network_config_mgr._save_secure.assert_not_called()

    def test_search_instance_update_not_found(self, client, state):
        state.network_config_mgr.get_raw_config.return_value = {"search_instances": []}
        resp = put_json(client, "/api/search/instances/missing", {"updates": {}})
        assert resp.status_code == 404

    def test_search_instance_update_exception(self, client, state):
        state.network_config_mgr.get_raw_config.side_effect = RuntimeError("fail")
        resp = put_json(client, "/api/search/instances/s1", {"updates": {}})
        assert resp.status_code == 500

    def test_search_instance_delete_success(self, client, state):
        """成功删除 + 从搜索引擎移除"""
        state.network_config_mgr.get_raw_config.return_value = {
            "search_instances": [{"id": "s1"}, {"id": "s2"}]
        }
        with patch("agent.tools.sync_web_search_engines") as mock_sync:
            resp = client.delete("/api/search/instances/s1")
        assert resp.status_code == 200
        state.network_config_mgr._save.assert_called_once()
        state.network_config_mgr._save_secure.assert_called_once()
        state.search_engine.remove_engine.assert_called_once_with("s1")
        mock_sync.assert_called_once()

    def test_search_instance_delete_not_found(self, client, state):
        state.network_config_mgr.get_raw_config.return_value = {"search_instances": [{"id": "s2"}]}
        resp = client.delete("/api/search/instances/missing")
        assert resp.status_code == 404

    def test_search_instance_delete_exception(self, client, state):
        state.network_config_mgr.get_raw_config.side_effect = RuntimeError("fail")
        resp = client.delete("/api/search/instances/s1")
        assert resp.status_code == 500

    def test_search_instance_set_default_custom(self, client, state):
        """custom 引擎:用 instance_id"""
        state.network_config_mgr.get_raw_config.return_value = {
            "search_instances": [{"id": "s1", "engine_type": "custom"}]
        }
        resp = client.post("/api/search/instances/s1/default")
        assert resp.status_code == 200
        state.search_engine.set_default_engine.assert_called_once_with("s1")

    def test_search_instance_set_default_builtin(self, client, state):
        """内置引擎:用 engine_type"""
        state.network_config_mgr.get_raw_config.return_value = {
            "search_instances": [{"id": "s1", "engine_type": "duckduckgo"}]
        }
        resp = client.post("/api/search/instances/s1/default")
        assert resp.status_code == 200
        state.search_engine.set_default_engine.assert_called_once_with("duckduckgo")

    def test_search_instance_set_default_not_found(self, client, state):
        state.network_config_mgr.get_raw_config.return_value = {"search_instances": []}
        resp = client.post("/api/search/instances/missing/default")
        assert resp.status_code == 404

    def test_search_instance_set_default_value_error(self, client, state):
        """set_default_engine 抛 ValueError → 500"""
        state.network_config_mgr.get_raw_config.return_value = {
            "search_instances": [{"id": "s1", "engine_type": "custom"}]
        }
        state.search_engine.set_default_engine.side_effect = ValueError("not registered")
        resp = client.post("/api/search/instances/s1/default")
        assert resp.status_code == 500

    def test_search_instance_set_default_no_web_search(self, no_search_client, no_search_state):
        """web_search=None 时跳过 set_default_engine"""
        no_search_state.network_config_mgr.get_raw_config.return_value = {
            "search_instances": [{"id": "s1", "engine_type": "duckduckgo"}]
        }
        resp = no_search_client.post("/api/search/instances/s1/default")
        assert resp.status_code == 200

    def test_search_instance_test_not_found(self, client, state):
        state.network_config_mgr.get_raw_config.return_value = {"search_instances": []}
        resp = client.post("/api/search/instances/missing/test")
        assert resp.status_code == 404

    def test_search_instance_test_no_web_search(self, no_search_client, no_search_state):
        """web_search=None → 503(非 500)"""
        no_search_state.network_config_mgr.get_raw_config.return_value = {
            "search_instances": [{"id": "s1", "engine_type": "duckduckgo"}]
        }
        resp = no_search_client.post("/api/search/instances/s1/test")
        assert resp.status_code == 503

    def test_search_instance_test_custom(self, client, state):
        """custom 引擎:调用 _search_custom"""
        state.network_config_mgr.get_raw_config.return_value = {
            "search_instances": [{"id": "s1", "engine_type": "custom"}]
        }
        state.search_engine._search_custom.return_value = {
            "ok": True, "results": [{"title": "test"}], "total_estimate": 1, "engine": "custom"
        }
        resp = client.post("/api/search/instances/s1/test")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["total"] == 1
        state.search_engine._search_custom.assert_called_once()

    def test_search_instance_test_builtin(self, client, state):
        """内置引擎:调用 search"""
        state.network_config_mgr.get_raw_config.return_value = {
            "search_instances": [{"id": "s1", "engine_type": "duckduckgo"}]
        }
        state.search_engine.search.return_value = {
            "ok": True, "results": [{"title": "r1"}, {"title": "r2"}, {"title": "r3"}],
            "total_estimate": 3, "engine": "duckduckgo"
        }
        resp = client.post("/api/search/instances/s1/test")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        # results 截取前 2 个
        assert len(data["results"]) == 2

    def test_search_instance_test_exception(self, client, state):
        state.network_config_mgr.get_raw_config.side_effect = RuntimeError("fail")
        resp = client.post("/api/search/instances/s1/test")
        assert resp.status_code == 500


# ──────────────────────────────────────────────
# 7. 配置变更日志路由
# ──────────────────────────────────────────────


class TestConfigLogs:
    """GET /api/config/logs"""

    def test_config_logs_default_limit(self, client, state):
        state.network_config_mgr.get_change_log.return_value = [{"action": "add"}]
        resp = client.get("/api/config/logs")
        assert resp.status_code == 200
        assert resp.get_json()["logs"] == [{"action": "add"}]
        state.network_config_mgr.get_change_log.assert_called_once_with(20)

    def test_config_logs_custom_limit(self, client, state):
        state.network_config_mgr.get_change_log.return_value = []
        resp = client.get("/api/config/logs?limit=5")
        assert resp.status_code == 200
        state.network_config_mgr.get_change_log.assert_called_once_with(5)

    def test_config_logs_exception(self, client, state):
        state.network_config_mgr.get_change_log.side_effect = RuntimeError("fail")
        resp = client.get("/api/config/logs")
        assert resp.status_code == 500
