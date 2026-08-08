"""mcp_executor 单例迁移单元测试

覆盖：
- 单例行为：唯一性、注册、工厂 config 通道（default_timeout）、reset/GC/幂等
- 工具执行：execute_mcp_tool 走单例、协议校验（未注册/非 mcp/无 endpoint）、client 复用、外部 protocol_info
- 异常处理（重点）：McpAuthError / TimeoutError / 通用异常 / 未初始化 / initialize 失败
- 并发首次初始化、fallback 行为
"""
import gc
import threading
import weakref

import pytest

import agent.mcp_executor as module
from agent.mcp_executor import (
    McpAuthError,
    McpExecutor,
    McpResponse,
    execute_mcp_tool,
    get_mcp_executor,
    is_mcp_tool,
)
from agent.utils.singleton_manager import get_singleton, is_initialized

MOCK_ENDPOINT = "https://mcp.mock.test/api"


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后重置单例，保证测试隔离"""
    module.reset_mcp_executor()
    yield
    module.reset_mcp_executor()


def _protocol(endpoint=MOCK_ENDPOINT):
    """构造外部传入的 protocol_info"""
    return {"protocol": "mcp", "endpoint": endpoint}


class TestMcpExecutorSingleton:
    """单例行为测试"""

    def test_get_mcp_executor_returns_same_instance(self):
        a = get_mcp_executor()
        b = get_mcp_executor()
        assert a is b

    def test_registers_in_singleton_manager(self):
        get_mcp_executor()
        assert is_initialized("mcp_executor")

    def test_singleton_manager_channel_returns_same_instance(self):
        ev = get_mcp_executor()
        assert get_singleton("mcp_executor") is ev

    def test_factory_unpacks_config_channel(self):
        """工厂：dict 通道含 default_timeout 键时解包"""
        ex = module._create_mcp_executor({"default_timeout": 5.0})
        assert ex._default_timeout == 5.0

    def test_factory_default_when_none(self):
        """工厂：无 config 用默认 30s"""
        ex = module._create_mcp_executor(None)
        assert ex._default_timeout == 30.0

    def test_reset_returns_new_instance(self):
        first = get_mcp_executor()
        module.reset_mcp_executor()
        second = get_mcp_executor()
        assert first is not second

    def test_reset_releases_instance_for_gc(self):
        ref = weakref.ref(get_mcp_executor())
        module.reset_mcp_executor()
        gc.collect()
        assert ref() is None

    def test_reset_idempotent_when_not_initialized(self):
        module.reset_mcp_executor()
        module.reset_mcp_executor()


class TestToolExecution:
    """工具执行测试（重点）"""

    def test_execute_mcp_tool_returns_success_response(self):
        """便捷函数 execute_mcp_tool 返回 McpResponse（success=True）"""
        resp = get_mcp_executor().execute("db_query", {"sql": "SELECT 1"}, _protocol())
        assert isinstance(resp, McpResponse)
        assert resp.success is True

    def test_execute_reuses_client_per_endpoint(self):
        """同 endpoint 复用同一个 client（连接池语义）"""
        executor = get_mcp_executor()
        executor.execute("db_query", {}, _protocol())
        executor.execute("db_query", {}, _protocol())
        assert len(executor._clients) == 1

    def test_execute_distinct_endpoints_separate_clients(self):
        """不同 endpoint 各自建 client"""
        executor = get_mcp_executor()
        executor.execute("t1", {}, _protocol("https://a.mock"))
        executor.execute("t2", {}, _protocol("https://b.mock"))
        assert len(executor._clients) == 2

    def test_execute_unregistered_tool_fails(self):
        """工具未在 TOOL_PROTOCOL_MAP 注册时返回错误响应"""
        resp = get_mcp_executor().execute("no_such_tool", {})
        assert resp.success is False
        assert "未在 TOOL_PROTOCOL_MAP 中注册" in resp.error

    def test_execute_non_mcp_protocol_fails(self):
        """非 mcp 协议工具拒绝执行"""
        resp = get_mcp_executor().execute(
            "native_tool", {}, {"protocol": "native", "endpoint": ""}
        )
        assert resp.success is False
        assert "非 mcp 协议工具" in resp.error

    def test_execute_missing_endpoint_fails(self):
        """mcp 协议但未配置 endpoint 时拒绝执行"""
        resp = get_mcp_executor().execute("t", {}, {"protocol": "mcp", "endpoint": ""})
        assert resp.success is False
        assert "未配置 protocol_endpoint" in resp.error

    def test_execute_returns_result_and_tool_name(self):
        """成功响包含结果、工具名与端点信息"""
        resp = get_mcp_executor().execute("db_query", {"sql": "x"}, _protocol())
        assert resp.result is not None
        assert resp.tool_name == "db_query"
        assert resp.endpoint == MOCK_ENDPOINT

    def test_execute_mcp_tool_module_function(self):
        """模块级 execute_mcp_tool 委托给单例"""
        resp = execute_mcp_tool("db_query", {"sql": "x"})
        assert isinstance(resp, McpResponse)


class TestExceptionHandling:
    """异常处理测试（重点）"""

    def _initialized_client(self, executor):
        """执行一次触发 client 创建并初始化，返回该 client"""
        executor.execute("db_query", {}, _protocol())
        return executor._clients[MOCK_ENDPOINT]

    def test_auth_error_returns_failure_response(self, monkeypatch):
        """McpAuthError → 鉴权失败 McpResponse（不抛异常）"""
        executor = get_mcp_executor()
        client = self._initialized_client(executor)
        monkeypatch.setattr(
            client, "_mock_call",
            lambda *a, **k: (_ for _ in ()).throw(McpAuthError("denied")),
        )
        resp = executor.execute("db_query", {}, _protocol())
        assert resp.success is False
        assert "鉴权失败" in resp.error

    def test_timeout_error_returns_timeout_response(self, monkeypatch):
        """TimeoutError → 超时 McpResponse"""
        executor = get_mcp_executor()
        client = self._initialized_client(executor)
        monkeypatch.setattr(
            client, "_mock_call",
            lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow")),
        )
        resp = executor.execute("db_query", {}, _protocol())
        assert resp.success is False
        assert "超时" in resp.error

    def test_generic_exception_returns_error_response(self, monkeypatch):
        """通用异常 → McpResponse 携带错误信息"""
        executor = get_mcp_executor()
        client = self._initialized_client(executor)
        monkeypatch.setattr(
            client, "_mock_call",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        resp = executor.execute("db_query", {}, _protocol())
        assert resp.success is False
        assert "boom" in resp.error

    def test_call_tool_when_not_initialized_rejected(self):
        """未 initialize 直接 call_tool → 拒绝响应"""
        executor = get_mcp_executor()
        executor._clients[MOCK_ENDPOINT] = module.McpClient(MOCK_ENDPOINT)
        client = executor._clients[MOCK_ENDPOINT]
        assert client.initialized is False
        resp = client.call_tool("db_query", {})
        assert resp.success is False
        assert "未初始化" in resp.error

    def test_initialize_failure_propagates_from_execute(self, monkeypatch):
        """client.initialize 失败 → execute 直接返回失败响应"""
        executor = get_mcp_executor()
        client = self._initialized_client(executor)
        fail_resp = McpResponse(success=False, error="握手失败", tool_name="db_query")
        monkeypatch.setattr(client, "initialize", lambda: fail_resp)
        # 强制重新初始化
        client.initialized = False
        resp = executor.execute("db_query", {}, _protocol())
        assert resp is fail_resp


class TestMcpExecutorConcurrency:
    """并发场景测试"""

    def test_concurrent_first_get_initializes_once(self):
        """多线程并发首次 get 只构造一个实例（双检锁）"""
        orig_cls = module.McpExecutor
        created = []

        class CountingExecutor(orig_cls):
            def __init__(self, default_timeout=30.0):
                created.append(1)
                super().__init__(default_timeout)

        module.McpExecutor = CountingExecutor
        try:
            results = []
            errors = []
            barrier = threading.Barrier(8)

            def worker():
                barrier.wait()
                try:
                    results.append(get_mcp_executor())
                except Exception as e:  # pragma: no cover
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors
            assert len(created) == 1, f"应只构造一次，实际 {len(created)} 次"
            assert all(r is results[0] for r in results)
        finally:
            module.McpExecutor = orig_cls

    def test_concurrent_get_after_init_returns_same_instance(self):
        get_mcp_executor()
        instances = []

        def worker():
            instances.append(get_mcp_executor())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(i is instances[0] for i in instances)


class TestMcpExecutorFallback:
    """SingletonManager 不可用时的 fallback 行为"""

    def test_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        a = get_mcp_executor()
        b = get_mcp_executor()
        assert a is b

    def test_fallback_reset_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_mcp_executor()
        module.reset_mcp_executor()
        second = get_mcp_executor()
        assert first is not second

    def test_fallback_execute_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        resp = execute_mcp_tool("db_query", {"sql": "x"})
        assert isinstance(resp, McpResponse)
