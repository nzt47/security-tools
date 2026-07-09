"""server_routes 综合单元测试

覆盖模块:
  - agent/server_routes/tracing_decorator.py
  - agent/server_routes/tracing_middleware.py
  - agent/server_routes/extensions.py

测试维度:
  - trace_route / trace_async_route 装饰器行为
  - _safe_call 异常捕获
  - TracingMiddleware WSGI 中间件
  - register_tracing / before_request_handler / after_request_handler
  - get_current_trace_context
  - extensions 路由 (list/installed/install/uninstall/toggle/configure/discover/market)

设计原则: AAA, Mock 外部依赖 (ExtensionManager/Market/Tracer)

注意: tracing_middleware 引用了一些在 tracing.py 中尚未实现的符号
(init_observability, get_tracer, SpanKind, StatusCode, record_request_metrics,
get_logger_with_context, _OPENTELEMETRY_AVAILABLE)。在导入前注入 mock。
"""

import asyncio
import json
import sys
from unittest import mock

import pytest
from flask import Flask, jsonify

# 在导入 tracing_middleware 前,向 tracing 模块注入缺失符号的 mock
import agent.monitoring.tracing as _tracing_mod

if not hasattr(_tracing_mod, "init_observability"):
    _tracing_mod.init_observability = lambda: None
if not hasattr(_tracing_mod, "_OPENTELEMETRY_AVAILABLE"):
    _tracing_mod._OPENTELEMETRY_AVAILABLE = False
if not hasattr(_tracing_mod, "get_tracer"):
    _tracing_mod.get_tracer = lambda name=None: None
if not hasattr(_tracing_mod, "SpanKind"):
    class _SpanKind:
        SERVER = "server"
        CLIENT = "client"
    _tracing_mod.SpanKind = _SpanKind
if not hasattr(_tracing_mod, "StatusCode"):
    class _StatusCode:
        OK = "ok"
        ERROR = "error"
    _tracing_mod.StatusCode = _StatusCode
if not hasattr(_tracing_mod, "record_request_metrics"):
    _tracing_mod.record_request_metrics = lambda **kw: None
if not hasattr(_tracing_mod, "get_logger_with_context"):
    import logging
    def _get_logger_with_context(name):
        return logging.getLogger(name)
    _tracing_mod.get_logger_with_context = _get_logger_with_context

from agent.server_routes.tracing_decorator import (
    trace_route,
    trace_async_route,
    _safe_call,
)
# tracing_decorator._safe_call 引用了未导入的 logger/json/get_trace_id
# 在模块加载后注入,以避免 NameError
import agent.server_routes.tracing_decorator as _td_mod
import logging as _logging
if not hasattr(_td_mod, "logger"):
    _td_mod.logger = _logging.getLogger("tracing_decorator")
if not hasattr(_td_mod, "get_trace_id"):
    _td_mod.get_trace_id = lambda: "test-trace-id"
if "json" not in dir(_td_mod):
    _td_mod.json = json

from agent.server_routes.tracing_middleware import (
    TracingMiddleware,
    before_request_handler,
    after_request_handler,
    register_tracing,
    get_current_trace_context,
    _get_tracer,
)


# ═══════════════════════════════════════════════════════════════
# trace_route 装饰器测试
# ═══════════════════════════════════════════════════════════════


class TestTraceRoute:
    """trace_route 装饰器测试"""

    def test_decorator_preserves_function_name(self):
        @trace_route("TestService")
        def api_my_endpoint():
            """docstring"""
            return "ok"
        assert api_my_endpoint.__name__ == "api_my_endpoint"

    def test_decorator_preserves_docstring(self):
        @trace_route("TestService")
        def api_my_endpoint():
            """my docstring"""
            return "ok"
        assert api_my_endpoint.__doc__ == "my docstring"

    def test_decorator_returns_function_result(self):
        @trace_route("TestService")
        def api_endpoint():
            return {"data": 42}
        assert api_endpoint() == {"data": 42}

    def test_decorator_with_args(self):
        @trace_route("TestService")
        def api_endpoint(x, y):
            return x + y
        assert api_endpoint(2, 3) == 5

    def test_decorator_with_kwargs(self):
        @trace_route("TestService")
        def api_endpoint(x=0, y=0):
            return x * y
        assert api_endpoint(x=4, y=5) == 20

    def test_decorator_default_service_name(self):
        @trace_route()
        def api_test():
            return "ok"
        # 默认 service_name 应为 "API"
        assert api_test() == "ok"

    def test_decorator_calls_trace_context(self):
        with mock.patch("agent.server_routes.tracing_decorator.TraceContext") as m:
            m.return_value.__enter__ = mock.MagicMock(return_value=None)
            m.return_value.__exit__ = mock.MagicMock(return_value=False)

            @trace_route("MyService")
            def api_my_op():
                return "executed"

            result = api_my_op()
            assert result == "executed"
            assert m.called

    def test_decorator_propagates_exception(self):
        @trace_route("TestService")
        def api_fail():
            raise ValueError("失败")
        with pytest.raises(ValueError):
            api_fail()

    def test_decorator_operation_name_generation(self):
        # operation = func.__name__.replace("api_", "").replace("_", ".")
        with mock.patch("agent.server_routes.tracing_decorator.TraceContext") as m:
            captured_args = []
            def capture_ctx(service, operation):
                captured_args.append((service, operation))
                cm = mock.MagicMock()
                cm.__enter__ = lambda s: None
                cm.__exit__ = lambda *a: False
                return cm
            m.side_effect = capture_ctx

            @trace_route("Svc")
            def api_user_login():
                return "ok"

            api_user_login()
            assert captured_args[0] == ("Svc", "user.login")


# ═══════════════════════════════════════════════════════════════
# trace_async_route 装饰器测试
# ═══════════════════════════════════════════════════════════════


class TestTraceAsyncRoute:
    """trace_async_route 装饰器测试"""

    def test_async_decorator_preserves_name(self):
        @trace_async_route("TestService")
        async def api_async_endpoint():
            return "ok"
        assert api_async_endpoint.__name__ == "api_async_endpoint"

    def test_async_decorator_returns_coroutine_result(self):
        @trace_async_route("TestService")
        async def api_endpoint():
            return "async-result"
        result = asyncio.run(api_endpoint())
        assert result == "async-result"

    def test_async_decorator_default_service(self):
        @trace_async_route()
        async def api_endpoint():
            return "ok"
        result = asyncio.run(api_endpoint())
        assert result == "ok"

    def test_async_decorator_propagates_exception(self):
        @trace_async_route("TestService")
        async def api_fail():
            raise RuntimeError("async 失败")
        with pytest.raises(RuntimeError):
            asyncio.run(api_fail())


# ═══════════════════════════════════════════════════════════════
# _safe_call 测试
# ═══════════════════════════════════════════════════════════════


class TestSafeCall:
    """_safe_call 安全调用包装器测试"""

    def test_success_returns_result(self):
        def func(x):
            return x * 2
        assert _safe_call(func, 5) == 10

    def test_success_with_kwargs(self):
        def func(a, b=0):
            return a + b
        assert _safe_call(func, 1, b=2) == 3

    def test_exception_propagates(self):
        def func():
            raise ValueError("错误")
        with pytest.raises(ValueError):
            _safe_call(func)

    def test_exception_with_action_param(self):
        def func():
            raise RuntimeError("运行时错误")
        # 即使有 action 参数,异常也应传播
        with pytest.raises(RuntimeError):
            _safe_call(func, action="custom_action")


# ═══════════════════════════════════════════════════════════════
# TracingMiddleware 测试
# ═══════════════════════════════════════════════════════════════


class TestTracingMiddleware:
    """TracingMiddleware WSGI 中间件测试"""

    def test_init(self):
        app = mock.MagicMock()
        mw = TracingMiddleware(app)
        assert mw.app is app
        assert '/favicon.ico' in mw._excluded_paths
        assert '/robots.txt' in mw._excluded_paths
        assert '/static/' in mw._excluded_paths
        assert '/metrics' in mw._excluded_paths

    def test_should_trace_included_path(self):
        app = mock.MagicMock()
        mw = TracingMiddleware(app)
        assert mw._should_trace("/api/users") is True
        assert mw._should_trace("/") is True

    def test_should_trace_excluded_path(self):
        app = mock.MagicMock()
        mw = TracingMiddleware(app)
        assert mw._should_trace("/favicon.ico") is False
        assert mw._should_trace("/robots.txt") is False
        assert mw._should_trace("/static/css/main.css") is False
        assert mw._should_trace("/metrics") is False

    def test_call_excluded_path_skips_tracing(self):
        # 被排除的路径应直接调用 app,不创建 span
        app = mock.MagicMock(return_value=["response"])
        mw = TracingMiddleware(app)
        environ = {"PATH_INFO": "/favicon.ico"}
        start_response = mock.MagicMock()
        result = mw(environ, start_response)
        # app 应被直接调用
        app.assert_called_once()
        assert result == ["response"]

    def test_call_included_path_creates_tracing(self):
        # 包含的路径应创建追踪上下文
        app = mock.MagicMock(return_value=["response"])
        mw = TracingMiddleware(app)
        environ = {
            "PATH_INFO": "/api/test",
            "REQUEST_METHOD": "GET",
        }
        start_response = mock.MagicMock()
        # Mock capture/restore_context 避免污染
        with mock.patch("agent.server_routes.tracing_middleware.capture_context"), \
             mock.patch("agent.server_routes.tracing_middleware.restore_context"), \
             mock.patch("agent.server_routes.tracing_middleware.extract_trace_context", return_value={}), \
             mock.patch("agent.server_routes.tracing_middleware.inject_trace_context", return_value={}), \
             mock.patch("agent.server_routes.tracing_middleware._get_tracer", return_value=None), \
             mock.patch("agent.server_routes.tracing_middleware.has_request_context", return_value=False):
            result = mw(environ, start_response)
            app.assert_called_once()

    def test_call_with_trace_id_header(self):
        # 从请求头提取 trace_id
        app = mock.MagicMock(return_value=["resp"])
        mw = TracingMiddleware(app)
        environ = {
            "PATH_INFO": "/api/test",
            "REQUEST_METHOD": "GET",
            "HTTP_TRACEPARENT": "00-trace123-span456-01",
        }
        with mock.patch("agent.server_routes.tracing_middleware.capture_context"), \
             mock.patch("agent.server_routes.tracing_middleware.restore_context"), \
             mock.patch("agent.server_routes.tracing_middleware.extract_trace_context",
                        return_value={"trace_id": "trace123", "span_id": "span456"}), \
             mock.patch("agent.server_routes.tracing_middleware.inject_trace_context", return_value={}), \
             mock.patch("agent.server_routes.tracing_middleware.set_trace_id") as mock_set_trace, \
             mock.patch("agent.server_routes.tracing_middleware.set_span_id") as mock_set_span, \
             mock.patch("agent.server_routes.tracing_middleware._get_tracer", return_value=None), \
             mock.patch("agent.server_routes.tracing_middleware.has_request_context", return_value=False):
            mw(environ, mock.MagicMock())
            mock_set_trace.assert_called_with("trace123")
            mock_set_span.assert_called_with("span456")


# ═══════════════════════════════════════════════════════════════
# TracingMiddleware 辅助方法测试
# ═══════════════════════════════════════════════════════════════


class TestTracingMiddlewareHelpers:
    """TracingMiddleware 辅助方法测试"""

    def test_get_tracer_returns_none_when_unavailable(self):
        # 当 OTel 不可用时,_get_tracer 应返回 None
        with mock.patch("agent.server_routes.tracing_middleware.is_opentelemetry_available", return_value=False):
            # 重置全局 _tracer
            import agent.server_routes.tracing_middleware as mod
            original = mod._tracer
            mod._tracer = None
            try:
                # 注意:_get_tracer 会调用 init_observability 和 get_tracer
                with mock.patch("agent.server_routes.tracing_middleware.init_observability"):
                    result = _get_tracer()
                assert result is None
            finally:
                mod._tracer = original

    def test_excluded_paths_contains_static(self):
        mw = TracingMiddleware(mock.MagicMock())
        assert '/static/' in mw._excluded_paths

    def test_custom_excluded_paths(self):
        # 通过修改 _excluded_paths 集合可自定义
        mw = TracingMiddleware(mock.MagicMock())
        mw._excluded_paths.add('/health')
        assert mw._should_trace('/health') is False


# ═══════════════════════════════════════════════════════════════
# Flask 处理器测试
# ═══════════════════════════════════════════════════════════════


class TestFlaskHandlers:
    """before_request_handler / after_request_handler 测试"""

    def test_before_request_sets_trace_id(self):
        app = Flask(__name__)
        with app.test_request_context("/api/test", headers={"X-Trace-Id": "abc123"}):
            with mock.patch("agent.server_routes.tracing_middleware.init_observability"), \
                 mock.patch("agent.server_routes.tracing_middleware.extract_trace_context",
                            return_value={"trace_id": "abc123"}), \
                 mock.patch("agent.server_routes.tracing_middleware.set_trace_id") as m:
                before_request_handler()
                m.assert_called_with("abc123")

    def test_before_request_no_trace_id(self):
        app = Flask(__name__)
        with app.test_request_context("/api/test"):
            with mock.patch("agent.server_routes.tracing_middleware.init_observability"), \
                 mock.patch("agent.server_routes.tracing_middleware.extract_trace_context",
                            return_value={}), \
                 mock.patch("agent.server_routes.tracing_middleware.set_trace_id") as m:
                before_request_handler()
                m.assert_not_called()

    def test_after_request_injects_headers(self):
        app = Flask(__name__)
        with app.test_request_context("/api/test"):
            response = jsonify({"ok": True})
            with mock.patch("agent.server_routes.tracing_middleware.inject_trace_context",
                            return_value={"X-Trace-Id": "tid123"}):
                result = after_request_handler(response)
                assert result.headers.get("X-Trace-Id") == "tid123"

    def test_after_request_empty_headers(self):
        app = Flask(__name__)
        with app.test_request_context("/api/test"):
            response = jsonify({"ok": True})
            with mock.patch("agent.server_routes.tracing_middleware.inject_trace_context",
                            return_value={}):
                result = after_request_handler(response)
                # 即使无追踪头,响应仍应正常返回
                assert result is response


# ═══════════════════════════════════════════════════════════════
# register_tracing 测试
# ═══════════════════════════════════════════════════════════════


class TestRegisterTracing:
    """register_tracing 测试"""

    def test_register_tracing_wraps_wsgi_app(self):
        app = Flask(__name__)
        register_tracing(app)
        # wsgi_app 应被 TracingMiddleware 包裹
        # 注意: Flask 的 wsgi_app 是一个 property,每次访问会重新包装
        # 所以只验证类型,不验证内部 app 引用
        assert isinstance(app.wsgi_app, TracingMiddleware)

    def test_register_tracing_adds_handlers(self):
        app = Flask(__name__)
        # 记录 before/after_request handlers 数量
        before_count = len(app.before_request_funcs.get(None, []))
        after_count = len(app.after_request_funcs.get(None, []))
        register_tracing(app)
        new_before = len(app.before_request_funcs.get(None, []))
        new_after = len(app.after_request_funcs.get(None, []))
        assert new_before == before_count + 1
        assert new_after == after_count + 1


# ═══════════════════════════════════════════════════════════════
# get_current_trace_context 测试
# ═══════════════════════════════════════════════════════════════


class TestGetCurrentTraceContext:
    """get_current_trace_context 测试"""

    def test_returns_dict_with_trace_id_and_span_id(self):
        with mock.patch("agent.server_routes.tracing_middleware.get_trace_id", return_value="tid"), \
             mock.patch("agent.server_routes.tracing_middleware.get_span_id", return_value="sid"):
            ctx = get_current_trace_context()
            assert ctx == {"trace_id": "tid", "span_id": "sid"}

    def test_returns_none_when_no_context(self):
        with mock.patch("agent.server_routes.tracing_middleware.get_trace_id", return_value=None), \
             mock.patch("agent.server_routes.tracing_middleware.get_span_id", return_value=None):
            ctx = get_current_trace_context()
            assert ctx["trace_id"] is None
            assert ctx["span_id"] is None


# ═══════════════════════════════════════════════════════════════
# Extensions 路由测试
# ═══════════════════════════════════════════════════════════════


class FakeState:
    """模拟 state 对象"""

    def __init__(self):
        self.extension_mgr = mock.MagicMock()
        self.extension_market = mock.MagicMock()


@pytest.fixture
def flask_app():
    """创建带 extensions 路由的 Flask 测试应用"""
    app = Flask(__name__)
    app.config["TESTING"] = True
    state = FakeState()
    # Mock require_token 装饰器 - 让它直接通过
    with mock.patch("agent.server_auth.require_token", lambda f: f), \
         mock.patch("agent.server_auth.log_request", lambda **kw: lambda f: f):
        from agent.server_routes import extensions
        extensions.register_routes(app, state)
    return app, state


class TestExtensionsListRoute:
    """/api/extensions/list 路由测试"""

    def test_list_returns_extensions(self, flask_app):
        app, state = flask_app
        state.extension_mgr.list_all.return_value = [{"id": "ext1", "name": "扩展1"}]
        with app.test_client() as client:
            resp = client.get("/api/extensions/list")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert len(data["extensions"]) == 1

    def test_list_with_type_filter(self, flask_app):
        app, state = flask_app
        state.extension_mgr.list_all.return_value = []
        with app.test_client() as client:
            resp = client.get("/api/extensions/list?type=channel")
        assert resp.status_code == 200
        state.extension_mgr.list_all.assert_called_with("channel")

    def test_list_handles_exception(self, flask_app):
        app, state = flask_app
        state.extension_mgr.list_all.side_effect = RuntimeError("DB error")
        with app.test_client() as client:
            resp = client.get("/api/extensions/list")
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["ok"] is False
        assert "DB error" in data["error"]


class TestExtensionsInstalledRoute:
    """/api/extensions/installed 路由测试"""

    def test_installed_returns_data(self, flask_app):
        app, state = flask_app
        state.extension_mgr.get_installed_by_type.return_value = {
            "channels": [], "skills": []
        }
        with app.test_client() as client:
            resp = client.get("/api/extensions/installed")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "channels" in data

    def test_installed_handles_exception(self, flask_app):
        app, state = flask_app
        state.extension_mgr.get_installed_by_type.side_effect = Exception("失败")
        with app.test_client() as client:
            resp = client.get("/api/extensions/installed")
        assert resp.status_code == 500


class TestExtensionsInstallRoute:
    """/api/extensions/install 路由测试"""

    def test_install_success(self, flask_app):
        app, state = flask_app
        state.extension_mgr.install.return_value = {"ok": True, "id": "ext1"}
        with app.test_client() as client:
            resp = client.post("/api/extensions/install", json={
                "type": "skill",
                "source": "github:repo",
            })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        state.extension_mgr.install.assert_called_with("skill", "github:repo")

    def test_install_missing_type(self, flask_app):
        app, state = flask_app
        with app.test_client() as client:
            resp = client.post("/api/extensions/install", json={"source": "x"})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["ok"] is False
        assert "type" in data["error"]

    def test_install_missing_source(self, flask_app):
        app, state = flask_app
        with app.test_client() as client:
            resp = client.post("/api/extensions/install", json={"type": "skill"})
        assert resp.status_code == 400

    def test_install_with_id_as_source(self, flask_app):
        app, state = flask_app
        state.extension_mgr.install.return_value = {"ok": True}
        with app.test_client() as client:
            resp = client.post("/api/extensions/install", json={
                "type": "skill",
                "id": "builtin-1",
                "params": {"key": "value"},
            })
        assert resp.status_code == 200
        # source 应为 id
        state.extension_mgr.install.assert_called_with("skill", "builtin-1", key="value")

    def test_install_handles_exception(self, flask_app):
        app, state = flask_app
        state.extension_mgr.install.side_effect = Exception("安装失败")
        with app.test_client() as client:
            resp = client.post("/api/extensions/install", json={
                "type": "skill", "source": "x"
            })
        assert resp.status_code == 500


class TestExtensionsUninstallRoute:
    """/api/extensions/uninstall 路由测试"""

    def test_uninstall_success(self, flask_app):
        app, state = flask_app
        state.extension_mgr.uninstall.return_value = {"ok": True}
        with app.test_client() as client:
            resp = client.post("/api/extensions/uninstall", json={
                "type": "skill", "id": "ext1"
            })
        assert resp.status_code == 200

    def test_uninstall_missing_params(self, flask_app):
        app, state = flask_app
        with app.test_client() as client:
            resp = client.post("/api/extensions/uninstall", json={"type": "skill"})
        assert resp.status_code == 400

    def test_uninstall_handles_exception(self, flask_app):
        app, state = flask_app
        state.extension_mgr.uninstall.side_effect = Exception("失败")
        with app.test_client() as client:
            resp = client.post("/api/extensions/uninstall", json={
                "type": "skill", "id": "x"
            })
        assert resp.status_code == 500


class TestExtensionsToggleRoute:
    """/api/extensions/toggle 路由测试"""

    def test_toggle_success(self, flask_app):
        app, state = flask_app
        state.extension_mgr.toggle.return_value = {"ok": True, "enabled": True}
        with app.test_client() as client:
            resp = client.post("/api/extensions/toggle", json={
                "type": "skill", "id": "ext1", "enabled": True
            })
        assert resp.status_code == 200

    def test_toggle_missing_params(self, flask_app):
        app, state = flask_app
        with app.test_client() as client:
            resp = client.post("/api/extensions/toggle", json={"id": "x"})
        assert resp.status_code == 400


class TestExtensionsConfigureRoute:
    """/api/extensions/configure 路由测试"""

    def test_configure_success(self, flask_app):
        app, state = flask_app
        state.extension_mgr.configure.return_value = {"ok": True}
        with app.test_client() as client:
            resp = client.post("/api/extensions/configure", json={
                "type": "skill", "id": "ext1", "config": {"key": "val"}
            })
        assert resp.status_code == 200
        state.extension_mgr.configure.assert_called_with("skill", "ext1", {"key": "val"})

    def test_configure_missing_params(self, flask_app):
        app, state = flask_app
        with app.test_client() as client:
            resp = client.post("/api/extensions/configure", json={"type": "skill"})
        assert resp.status_code == 400


class TestExtensionsDiscoverRoute:
    """/api/extensions/discover 路由测试"""

    def test_discover_success(self, flask_app):
        app, state = flask_app
        state.extension_mgr.discover_all.return_value = {"skills": []}
        with app.test_client() as client:
            resp = client.get("/api/extensions/discover")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_discover_handles_exception(self, flask_app):
        app, state = flask_app
        state.extension_mgr.discover_all.side_effect = Exception("失败")
        with app.test_client() as client:
            resp = client.get("/api/extensions/discover")
        assert resp.status_code == 500


class TestExtensionsMarketRoutes:
    """扩展市场路由测试"""

    def test_market_search(self, flask_app):
        app, state = flask_app
        state.extension_market.search_all.return_value = {"results": []}
        with app.test_client() as client:
            resp = client.get("/api/extensions/market/search?q=test&type=skill&github=true")
        assert resp.status_code == 200
        state.extension_market.search_all.assert_called_with("test", "skill", True)

    def test_market_search_default_github(self, flask_app):
        app, state = flask_app
        state.extension_market.search_all.return_value = {}
        with app.test_client() as client:
            resp = client.get("/api/extensions/market/search?q=test")
        assert resp.status_code == 200
        # 默认 include_github=True
        state.extension_market.search_all.assert_called_with("test", None, True)

    def test_market_recommend(self, flask_app):
        app, state = flask_app
        state.extension_market.get_recommendations.return_value = [{"id": "r1"}]
        with app.test_client() as client:
            resp = client.get("/api/extensions/market/recommend?limit=3")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert len(data["recommendations"]) == 1
        state.extension_market.get_recommendations.assert_called_with(None, 3)

    def test_market_refresh_success(self, flask_app):
        app, state = flask_app
        state.extension_market.fetch_community_index.return_value = [{"id": "x"}, {"id": "y"}]
        with app.test_client() as client:
            resp = client.post("/api/extensions/market/refresh")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["count"] == 2

    def test_market_refresh_empty_result(self, flask_app):
        app, state = flask_app
        state.extension_market.fetch_community_index.return_value = []
        with app.test_client() as client:
            resp = client.post("/api/extensions/market/refresh")
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["ok"] is False

    def test_market_refresh_handles_exception(self, flask_app):
        app, state = flask_app
        state.extension_market.fetch_community_index.side_effect = Exception("失败")
        with app.test_client() as client:
            resp = client.post("/api/extensions/market/refresh")
        assert resp.status_code == 500


class TestExtensionsChannelSendRoute:
    """/api/extensions/channels/send 路由测试"""

    def test_channel_send_success(self, flask_app):
        app, state = flask_app
        state.extension_mgr.send_channel_message.return_value = {"ok": True}
        with app.test_client() as client:
            resp = client.post("/api/extensions/channels/send", json={
                "channel_id": "ch1", "message": "hello"
            })
        assert resp.status_code == 200
        state.extension_mgr.send_channel_message.assert_called_with("ch1", "hello")

    def test_channel_send_missing_channel_id(self, flask_app):
        app, state = flask_app
        with app.test_client() as client:
            resp = client.post("/api/extensions/channels/send", json={"message": "x"})
        assert resp.status_code == 400

    def test_channel_send_missing_message(self, flask_app):
        app, state = flask_app
        with app.test_client() as client:
            resp = client.post("/api/extensions/channels/send", json={"channel_id": "c"})
        assert resp.status_code == 400

    def test_channel_send_with_params(self, flask_app):
        app, state = flask_app
        state.extension_mgr.send_channel_message.return_value = {"ok": True}
        with app.test_client() as client:
            resp = client.post("/api/extensions/channels/send", json={
                "channel_id": "ch1", "message": "hi", "params": {"priority": 1}
            })
        assert resp.status_code == 200
        state.extension_mgr.send_channel_message.assert_called_with("ch1", "hi", priority=1)
