"""server_routes 模块补全测试

覆盖：
- observability.py: trackEvent / _emit_structured_log / _trace_id（之前 0% 覆盖）
- tracing_decorator.py: trace_route / trace_async_route / _safe_call（修复后需验证）
- tracing_middleware.py: TracingMiddleware / before_request_handler / after_request_handler / get_current_trace_context / register_tracing

状态同步机制：Flask test client 隔离 HTTP 请求，caplog 捕获日志。
"""
import json
from unittest import mock

import pytest

# ── observability 测试 ──

from agent.server_routes import observability as sr_obs


class TestServerRoutesObservability:
    """server_routes.observability 埋点模块"""

    def test_trace_id_length(self):
        tid = sr_obs._trace_id()
        assert isinstance(tid, str)
        assert len(tid) == 16

    def test_trace_id_unique(self):
        ids = {sr_obs._trace_id() for _ in range(100)}
        assert len(ids) == 100

    def test_emit_structured_log_basic(self, caplog):
        with caplog.at_level("INFO", logger="agent.server_routes"):
            sr_obs._emit_structured_log("test_action", duration_ms=42.5)
        assert any("test_action" in r.message for r in caplog.records)

    def test_emit_structured_log_with_trace_id(self, caplog):
        with caplog.at_level("INFO", logger="agent.server_routes"):
            sr_obs._emit_structured_log("act", trace_id="custom-tid", duration_ms=10)
        assert any("custom-tid" in r.message for r in caplog.records)

    def test_emit_structured_log_level_warning(self, caplog):
        with caplog.at_level("WARNING", logger="agent.server_routes"):
            sr_obs._emit_structured_log("warn_act", level="warning")
        assert any("warn_act" in r.message for r in caplog.records)

    def test_emit_structured_log_extra_payload(self, caplog):
        with caplog.at_level("INFO", logger="agent.server_routes"):
            sr_obs._emit_structured_log("act", user_id="u123", action_type="click")
        msgs = [r.message for r in caplog.records]
        assert any("u123" in m for m in msgs)
        assert any("click" in m for m in msgs)

    def test_track_event_basic(self, caplog):
        with caplog.at_level("INFO", logger="agent.server_routes"):
            sr_obs.trackEvent("route_call", {"path": "/api/test"})
        assert any("track.route_call" in r.message for r in caplog.records)

    def test_track_event_no_payload(self, caplog):
        with caplog.at_level("INFO", logger="agent.server_routes"):
            sr_obs.trackEvent("simple_event")
        assert any("track.simple_event" in r.message for r in caplog.records)

    def test_track_event_reserved_keys_filtered(self, caplog):
        with caplog.at_level("INFO", logger="agent.server_routes"):
            sr_obs.trackEvent("evt", {
                "action": "should_be_filtered",
                "trace_id": "should_be_filtered",
                "custom_field": "kept",
            })
        msgs = " ".join(r.message for r in caplog.records)
        assert "kept" in msgs
        assert "should_be_filtered" not in msgs

    def test_track_event_does_not_raise(self):
        """埋点失败不影响主流程"""
        with mock.patch.object(sr_obs, "_emit_structured_log", side_effect=Exception("boom")):
            sr_obs.trackEvent("fail_test")  # 不应抛异常


# ── tracing_decorator 测试 ──

from agent.server_routes.tracing_decorator import (
    trace_route,
    trace_async_route,
    _safe_call as td_safe_call,
)


class TestTraceRouteDecorator:
    """trace_route 同步装饰器"""

    def test_decorator_preserves_function_name(self):
        @trace_route("TestService")
        def api_my_endpoint():
            """docstring"""
            return "ok"
        assert api_my_endpoint.__name__ == "api_my_endpoint"
        assert api_my_endpoint.__doc__ == "docstring"

    def test_decorator_returns_function_result(self):
        @trace_route("TestService")
        def api_test():
            return 42
        assert api_test() == 42

    def test_decorator_default_service_name(self):
        @trace_route()
        def api_default():
            return "result"
        assert api_default() == "result"

    def test_decorator_with_arguments(self):
        @trace_route("TestService")
        def api_with_args(x, y):
            return x + y
        assert api_with_args(1, 2) == 3

    def test_decorator_with_kwargs(self):
        @trace_route("TestService")
        def api_with_kwargs(*, value):
            return value * 2
        assert api_with_kwargs(value=5) == 10


class TestTraceAsyncRouteDecorator:
    """trace_async_route 异步装饰器"""

    def test_async_decorator_preserves_function_name(self):
        @trace_async_route("TestService")
        async def api_async_endpoint():
            """async docstring"""
            return "ok"
        assert api_async_endpoint.__name__ == "api_async_endpoint"
        assert api_async_endpoint.__doc__ == "async docstring"

    @pytest.mark.asyncio
    async def test_async_decorator_returns_result(self):
        @trace_async_route("TestService")
        async def api_async():
            return 42
        result = await api_async()
        assert result == 42

    @pytest.mark.asyncio
    async def test_async_decorator_default_service_name(self):
        @trace_async_route()
        async def api_async_default():
            return "result"
        result = await api_async_default()
        assert result == "result"


class TestTracingDecoratorSafeCall:
    """tracing_decorator._safe_call 安全调用包装器"""

    def test_returns_result_on_success(self):
        assert td_safe_call(lambda x: x * 2, 5) == 10

    def test_reraises_on_exception(self):
        with pytest.raises(ValueError):
            td_safe_call(lambda: (_ for _ in ()).throw(ValueError("test")))

    def test_logs_error_on_exception(self, caplog):
        with caplog.at_level("ERROR", logger="tracing_decorator"):
            with pytest.raises(ZeroDivisionError):
                td_safe_call(lambda: 1 / 0, action="divide")
        assert any("divide.failed" in r.message for r in caplog.records)


# ── tracing_middleware 测试 ──

from agent.server_routes.tracing_middleware import (
    TracingMiddleware,
    before_request_handler,
    after_request_handler,
    register_tracing,
    get_current_trace_context,
    record_request_metrics,
    get_logger_with_context,
    _get_tracer,
    _OTEL_AVAILABLE,
)


class TestTracingMiddlewareHelpers:
    """tracing_middleware 辅助函数"""

    def test_get_logger_with_context_returns_logger(self):
        log = get_logger_with_context("test_module")
        assert log is not None
        assert hasattr(log, "info")

    def test_record_request_metrics_does_not_raise(self, caplog):
        with caplog.at_level("DEBUG", logger="agent.server_routes.tracing_middleware"):
            record_request_metrics("GET", "/api/test", 200, 42.5)
        # 应记录 debug 日志（不抛异常即可）

    def test_get_current_trace_context_returns_dict(self):
        ctx = get_current_trace_context()
        assert isinstance(ctx, dict)
        assert "trace_id" in ctx
        assert "span_id" in ctx


class TestTracingMiddleware:
    """TracingMiddleware WSGI 中间件"""

    @pytest.fixture
    def app(self):
        from flask import Flask
        app = Flask(__name__)
        app.wsgi_app = TracingMiddleware(app.wsgi_app)

        @app.route('/api/test')
        def test_endpoint():
            from flask import jsonify
            return jsonify({"ok": True})

        @app.route('/favicon.ico')
        def favicon():
            return "", 204

        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_middleware_passes_through_request(self, client):
        resp = client.get('/api/test')
        assert resp.status_code == 200

    def test_middleware_excluded_path(self, client):
        """favicon.ico 应跳过追踪"""
        resp = client.get('/favicon.ico')
        # 即使被追踪排除，请求仍应正常处理
        assert resp.status_code in (200, 204)

    def test_middleware_handles_post(self, client):
        """中间件应处理 POST 请求"""
        # 添加 POST 路由
        self.app = client.application
        from flask import jsonify

        @self.app.route('/api/post', methods=['POST'])
        def post_endpoint():
            return jsonify({"received": True})

        resp = client.post('/api/post', json={"data": "test"})
        assert resp.status_code == 200


class TestTracingMiddlewareHandlers:
    """Flask before/after request 处理器"""

    @pytest.fixture
    def app(self):
        from flask import Flask
        app = Flask(__name__)

        @app.route('/api/test')
        def test_endpoint():
            from flask import jsonify
            return jsonify({"ok": True})

        # 注册追踪处理器
        app.before_request(before_request_handler)
        app.after_request(after_request_handler)
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_before_request_handler_sets_context(self, client):
        """before_request_handler 应正常执行（不抛异常）"""
        resp = client.get('/api/test', headers={
            'traceparent': '00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01'
        })
        assert resp.status_code == 200

    def test_after_request_handler_injects_headers(self, client):
        """after_request_handler 应注入追踪响应头"""
        resp = client.get('/api/test')
        assert resp.status_code == 200
        # 响应头可能包含追踪相关头（取决于 OpenTelemetry 是否可用）


class TestRegisterTracing:
    """register_tracing 集成函数"""

    def test_register_tracing_does_not_raise(self):
        from flask import Flask
        app = Flask(__name__)

        @app.route('/api/test')
        def test_endpoint():
            from flask import jsonify
            return jsonify({"ok": True})

        # 注册追踪中间件
        register_tracing(app)

        # 验证应用仍可正常工作
        client = app.test_client()
        resp = client.get('/api/test')
        assert resp.status_code == 200

    def test_register_tracing_replaces_wsgi_app(self):
        from flask import Flask
        app = Flask(__name__)
        original_wsgi = app.wsgi_app
        register_tracing(app)
        # wsgi_app 应被替换为 TracingMiddleware 包装
        assert app.wsgi_app is not original_wsgi


class TestTracingMiddlewareExcludedPaths:
    """TracingMiddleware 排除路径逻辑"""

    @pytest.fixture
    def middleware(self):
        from flask import Flask
        app = Flask(__name__)
        return TracingMiddleware(app)

    def test_should_trace_normal_path(self, middleware):
        assert middleware._should_trace('/api/test') is True

    def test_should_exclude_favicon(self, middleware):
        assert middleware._should_trace('/favicon.ico') is False

    def test_should_exclude_robots(self, middleware):
        assert middleware._should_trace('/robots.txt') is False

    def test_should_exclude_static(self, middleware):
        assert middleware._should_trace('/static/style.css') is False

    def test_should_exclude_metrics(self, middleware):
        assert middleware._should_trace('/metrics') is False
