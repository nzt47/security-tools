"""MCP 执行器单元测试 — 覆盖 mock 调用成功、网络超时、鉴权失败三大场景

【不易】不依赖真实网络;不修改生产 TOOL_PROTOCOL_MAP(用 protocol_info 注入)
【变易】通过 mock.patch.object 注入 McpClient._mock_call 模拟异常分支
【简易】三场景为核心,辅以边界场景(未注册/非mcp/无endpoint/未初始化)保完整

覆盖层级:
  1. McpResponse 数据结构
  2. McpClient 协议流程(initialize/list/call)
  3. McpExecutor 执行器(协议查询/client复用/超时透传)
  4. 便捷接口(execute_mcp_tool/is_mcp_tool)
"""
from __future__ import annotations

import time
import os
import logging
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from agent.mcp_executor import (
    McpResponse,
    McpClient,
    McpExecutor,
    McpAuthError,
    McpTimeoutError,
    execute_mcp_tool,
    get_mcp_executor,
    is_mcp_tool,
    set_logger_level,
    get_logger_level,
)


# ════════════════════════════════════════════════════════════
#  1. McpResponse 数据结构
# ════════════════════════════════════════════════════════════

class TestMcpResponse:
    """McpResponse 数据结构和序列化。"""

    def test_default_values(self):
        resp = McpResponse(success=True)
        assert resp.success is True
        assert resp.result is None
        assert resp.error is None
        assert resp.latency_ms == 0.0
        assert resp.tool_name == ""
        assert resp.endpoint == ""
        assert resp.protocol_phase == ""

    def test_to_dict_round_trip(self):
        resp = McpResponse(
            success=True,
            result={"rows": [1, 2]},
            latency_ms=12.345,
            tool_name="db_query",
            endpoint="https://mcp.example.com/db",
            protocol_phase="tools.call",
        )
        d = resp.to_dict()
        assert d["success"] is True
        assert d["result"] == {"rows": [1, 2]}
        # latency_ms 应四舍五入到 2 位
        assert d["latency_ms"] == 12.35
        assert d["tool_name"] == "db_query"
        assert d["protocol_phase"] == "tools.call"

    def test_to_dict_error_response(self):
        resp = McpResponse(success=False, error="鉴权失败: 401")
        d = resp.to_dict()
        assert d["success"] is False
        assert d["error"] == "鉴权失败: 401"
        assert d["result"] is None


# ════════════════════════════════════════════════════════════
#  2. McpClient — initialize 握手
# ════════════════════════════════════════════════════════════

class TestMcpClientInitialize:
    """MCP initialize 握手阶段。"""

    def test_initialize_success(self):
        client = McpClient("https://mcp.example.com/db")
        resp = client.initialize()
        assert resp.success is True
        assert resp.protocol_phase == "initialize"
        assert resp.endpoint == "https://mcp.example.com/db"
        assert isinstance(resp.result, dict)
        assert resp.result["name"] == "mock-mcp-server"
        assert client.initialized is True

    def test_initialize_empty_endpoint_fails(self):
        client = McpClient("")
        resp = client.initialize()
        assert resp.success is False
        assert "endpoint 格式异常" in resp.error
        assert client.initialized is False

    def test_initialize_invalid_scheme_fails(self):
        """非 http/https scheme 应被拒绝。"""
        client = McpClient("ftp://mcp.example.com/db")
        resp = client.initialize()
        assert resp.success is False
        assert "endpoint 格式异常" in resp.error

    def test_initialize_idempotent(self):
        """重复 initialize 不报错,server_info 保持稳定。"""
        client = McpClient("https://mcp.example.com/db")
        r1 = client.initialize()
        r2 = client.initialize()
        assert r1.success and r2.success
        assert r1.result == r2.result


# ════════════════════════════════════════════════════════════
#  3. McpClient — tools/list
# ════════════════════════════════════════════════════════════

class TestMcpClientListTools:
    """MCP tools/list 阶段。"""

    def test_list_tools_requires_initialize(self):
        """未初始化时 list_tools 应失败。"""
        client = McpClient("https://mcp.example.com/db")
        resp = client.list_tools()
        assert resp.success is False
        assert "未初始化" in resp.error
        assert resp.protocol_phase == "tools.list"

    def test_list_tools_returns_empty_after_init(self):
        """初始化后 list_tools 返回空列表(工具 schema 在本地 YAML)。"""
        client = McpClient("https://mcp.example.com/db")
        client.initialize()
        resp = client.list_tools()
        assert resp.success is True
        assert resp.result == {"tools": []}
        assert resp.protocol_phase == "tools.list"


# ════════════════════════════════════════════════════════════
#  4. McpClient — tools/call 调用成功场景 ★核心场景1
# ════════════════════════════════════════════════════════════

class TestMcpClientCallSuccess:
    """★核心场景1: mock 调用成功 — 3 个 MCP 工具各调用一次。"""

    def test_call_db_query_success(self):
        client = McpClient("https://mcp.example.com/db")
        client.initialize()
        resp = client.call_tool("db_query", {"sql": "SELECT 1", "database": "main"})
        assert resp.success is True
        assert resp.tool_name == "db_query"
        assert resp.protocol_phase == "tools.call"
        assert resp.endpoint == "https://mcp.example.com/db"
        assert resp.latency_ms > 0
        result = resp.result
        assert result["sql_executed"] == "SELECT 1"
        assert result["database"] == "main"
        assert len(result["rows"]) == 2
        assert result["affected"] == 2

    def test_call_remote_file_read_success(self):
        client = McpClient("https://mcp.example.com/files")
        client.initialize()
        resp = client.call_tool("remote_file_read", {"path": "/etc/hostname", "encoding": "utf-8"})
        assert resp.success is True
        assert resp.tool_name == "remote_file_read"
        result = resp.result
        assert "/etc/hostname" in result["content"]
        assert result["encoding"] == "utf-8"
        assert result["path"] == "/etc/hostname"

    def test_call_api_webhook_success(self):
        client = McpClient("https://mcp.example.com/webhook")
        client.initialize()
        args = {"url": "https://hooks.example.com/x", "method": "POST", "body": {"k": "v"}}
        resp = client.call_tool("api_webhook", args)
        assert resp.success is True
        result = resp.result
        assert result["status"] == 200
        assert result["method"] == "POST"
        assert result["url"] == "https://hooks.example.com/x"
        assert result["body"]["ok"] is True

    def test_call_unknown_tool_returns_generic_mock(self):
        """未识别工具返回通用 mock(不报错)。"""
        client = McpClient("https://mcp.example.com/generic")
        client.initialize()
        resp = client.call_tool("some_new_tool", {"foo": "bar"})
        assert resp.success is True
        assert resp.result["mock"] is True
        assert resp.result["tool"] == "some_new_tool"
        assert resp.result["args"] == {"foo": "bar"}

    def test_call_requires_initialize(self):
        """未初始化时 call_tool 应失败。"""
        client = McpClient("https://mcp.example.com/db")
        resp = client.call_tool("db_query", {"sql": "SELECT 1"})
        assert resp.success is False
        assert "未初始化" in resp.error
        assert resp.tool_name == "db_query"


# ════════════════════════════════════════════════════════════
#  5. McpClient — tools/call 网络超时场景 ★核心场景2
# ════════════════════════════════════════════════════════════

class TestMcpClientCallTimeout:
    """★核心场景2: 网络超时 — _mock_call 抛 TimeoutError/McpTimeoutError。"""

    def test_call_timeout_returns_timeout_error(self):
        """_mock_call 抛原生 TimeoutError → 返回超时响应。"""
        client = McpClient("https://mcp.example.com/db", timeout=5.0)
        client.initialize()

        def _raise_timeout(*args, **kwargs):
            raise TimeoutError("simulated network timeout")

        with patch.object(client, "_mock_call", side_effect=_raise_timeout):
            resp = client.call_tool("db_query", {"sql": "SELECT 1"})

        assert resp.success is False
        assert "调用超时" in resp.error
        assert "5.0s" in resp.error  # 错误信息含配置的 timeout
        assert resp.tool_name == "db_query"
        assert resp.protocol_phase == "tools.call"
        assert resp.latency_ms >= 0  # 超时也记录延迟

    def test_call_mcp_timeout_error_subtype(self):
        """McpTimeoutError(TimeoutError 子类)也应被超时分支捕获。"""
        client = McpClient("https://mcp.example.com/db", timeout=10.0)
        client.initialize()

        def _raise_mcp_timeout(*args, **kwargs):
            raise McpTimeoutError("httpx timeout")

        with patch.object(client, "_mock_call", side_effect=_raise_mcp_timeout):
            resp = client.call_tool("db_query", {"sql": "SELECT 1"})

        assert resp.success is False
        assert "调用超时" in resp.error
        assert "10.0s" in resp.error

    def test_timeout_value_propagates_to_error_message(self):
        """不同 timeout 值应反映在错误信息中,便于排查。"""
        for timeout in (1.0, 15.0, 30.0):
            client = McpClient("https://mcp.example.com/db", timeout=timeout)
            client.initialize()
            with patch.object(client, "_mock_call", side_effect=TimeoutError):
                resp = client.call_tool("db_query", {"sql": "SELECT 1"})
            assert resp.success is False
            assert f"{timeout}s" in resp.error, f"timeout={timeout} 未出现在错误信息"


# ════════════════════════════════════════════════════════════
#  6. McpClient — tools/call 鉴权失败场景 ★核心场景3
# ════════════════════════════════════════════════════════════

class TestMcpClientCallAuthError:
    """★核心场景3: 鉴权失败 — _mock_call 抛 McpAuthError(401/403)。"""

    def test_call_auth_error_returns_auth_failure(self):
        """_mock_call 抛 McpAuthError → 返回明确的鉴权失败响应。"""
        client = McpClient("https://mcp.example.com/db")
        client.initialize()

        def _raise_auth(*args, **kwargs):
            raise McpAuthError("401 Unauthorized: token expired")

        with patch.object(client, "_mock_call", side_effect=_raise_auth):
            resp = client.call_tool("db_query", {"sql": "SELECT 1"})

        assert resp.success is False
        # 鉴权失败错误信息应明确,而非通用"超时"或"异常"
        assert "鉴权失败" in resp.error
        assert "401 Unauthorized" in resp.error
        assert resp.tool_name == "db_query"
        assert resp.protocol_phase == "tools.call"

    def test_call_auth_error_403_forbidden(self):
        """403 Forbidden 也应走鉴权失败分支。"""
        client = McpClient("https://mcp.example.com/files")
        client.initialize()

        with patch.object(client, "_mock_call",
                          side_effect=McpAuthError("403 Forbidden: insufficient scope")):
            resp = client.call_tool("remote_file_read", {"path": "/etc/shadow"})

        assert resp.success is False
        assert "鉴权失败" in resp.error
        assert "403" in resp.error

    def test_auth_error_distinct_from_generic_exception(self):
        """鉴权失败不应落入通用 Exception 分支(错误信息可区分)。"""
        client = McpClient("https://mcp.example.com/db")
        client.initialize()

        # 鉴权失败
        with patch.object(client, "_mock_call", side_effect=McpAuthError("401")):
            auth_resp = client.call_tool("db_query", {})

        # 通用异常
        with patch.object(client, "_mock_call", side_effect=RuntimeError("boom")):
            generic_resp = client.call_tool("db_query", {})

        assert auth_resp.error.startswith("鉴权失败")
        assert not generic_resp.error.startswith("鉴权失败")
        assert generic_resp.error == "boom"

    def test_auth_error_distinct_from_timeout(self):
        """鉴权失败与超时错误信息可区分。"""
        client = McpClient("https://mcp.example.com/db", timeout=8.0)
        client.initialize()

        with patch.object(client, "_mock_call", side_effect=McpAuthError("401")):
            auth_resp = client.call_tool("db_query", {})

        with patch.object(client, "_mock_call", side_effect=TimeoutError):
            timeout_resp = client.call_tool("db_query", {})

        assert "鉴权失败" in auth_resp.error
        assert "调用超时" in timeout_resp.error
        assert "鉴权" not in timeout_resp.error


# ════════════════════════════════════════════════════════════
#  7. McpClient — 通用异常场景
# ════════════════════════════════════════════════════════════

class TestMcpClientCallGenericError:
    """通用异常(非超时/非鉴权)走 Exception 分支。"""

    def test_call_runtime_error_returns_error_response(self):
        client = McpClient("https://mcp.example.com/db")
        client.initialize()

        with patch.object(client, "_mock_call", side_effect=RuntimeError("internal error")):
            resp = client.call_tool("db_query", {"sql": "SELECT 1"})

        assert resp.success is False
        assert resp.error == "internal error"
        assert resp.tool_name == "db_query"

    def test_call_value_error_returns_error_response(self):
        client = McpClient("https://mcp.example.com/db")
        client.initialize()

        with patch.object(client, "_mock_call", side_effect=ValueError("bad param")):
            resp = client.call_tool("db_query", {})

        assert resp.success is False
        assert resp.error == "bad param"


# ════════════════════════════════════════════════════════════
#  8. McpExecutor — 执行器集成
# ════════════════════════════════════════════════════════════

class TestMcpExecutorExecute:
    """McpExecutor 执行器:协议查询/client复用/边界场景。"""

    def test_execute_success_with_protocol_info(self):
        """显式传入 protocol_info → 跳过 TOOL_PROTOCOL_MAP 查询,直接调用。"""
        executor = McpExecutor()
        protocol_info = {"protocol": "mcp", "endpoint": "https://mcp.example.com/db"}
        resp = executor.execute("db_query", {"sql": "SELECT 1"}, protocol_info=protocol_info)
        assert resp.success is True
        assert resp.tool_name == "db_query"
        assert resp.endpoint == "https://mcp.example.com/db"

    def test_execute_unregistered_tool_fails(self):
        """未注册工具(无 protocol_info)应返回错误,不抛异常。"""
        executor = McpExecutor()
        resp = executor.execute("nonexistent_tool_xyz", {})
        assert resp.success is False
        # tool_router 可能加载了 YAML,也可能未加载;两种情况都不应崩溃
        assert "未在 TOOL_PROTOCOL_MAP" in resp.error or "非 mcp" in resp.error

    def test_execute_non_mcp_protocol_fails(self):
        """protocol != mcp 的工具应被拒绝。"""
        executor = McpExecutor()
        resp = executor.execute("web_search", {},
                                protocol_info={"protocol": "native", "endpoint": ""})
        assert resp.success is False
        assert "非 mcp" in resp.error

    def test_execute_missing_endpoint_fails(self):
        """protocol=mcp 但 endpoint 为空应返回错误。"""
        executor = McpExecutor()
        resp = executor.execute("db_query", {},
                                protocol_info={"protocol": "mcp", "endpoint": ""})
        assert resp.success is False
        assert "未配置 protocol_endpoint" in resp.error

    def test_execute_client_reused_per_endpoint(self):
        """同一 endpoint 的多次调用复用同一 client(连接池语义)。"""
        executor = McpExecutor()
        info = {"protocol": "mcp", "endpoint": "https://mcp.example.com/db"}
        executor.execute("db_query", {"sql": "SELECT 1"}, protocol_info=info)
        executor.execute("db_query", {"sql": "SELECT 2"}, protocol_info=info)
        # 同一 endpoint 应只创建一个 client
        assert len(executor._clients) == 1
        assert "https://mcp.example.com/db" in executor._clients

    def test_execute_different_endpoints_create_separate_clients(self):
        """不同 endpoint 创建独立 client。"""
        executor = McpExecutor()
        executor.execute("db_query", {},
                         protocol_info={"protocol": "mcp", "endpoint": "https://mcp1.example.com"})
        executor.execute("remote_file_read", {},
                         protocol_info={"protocol": "mcp", "endpoint": "https://mcp2.example.com"})
        assert len(executor._clients) == 2

    def test_execute_initializes_client_only_once(self):
        """client 复用时 initialize 只执行一次(后续调用直接 tools/call)。"""
        executor = McpExecutor()
        info = {"protocol": "mcp", "endpoint": "https://mcp.example.com/db"}
        executor.execute("db_query", {"sql": "SELECT 1"}, protocol_info=info)
        client = executor._clients["https://mcp.example.com/db"]
        assert client.initialized is True
        # 第二次调用不应重新 initialize
        with patch.object(client, "initialize") as mock_init:
            executor.execute("db_query", {"sql": "SELECT 2"}, protocol_info=info)
            mock_init.assert_not_called()


# ════════════════════════════════════════════════════════════
#  9. McpExecutor — default_timeout 透传
# ════════════════════════════════════════════════════════════

class TestMcpExecutorTimeout:
    """default_timeout 透传到 McpClient,CLI/真实环境可配置。"""

    def test_default_timeout_propagated_to_client(self):
        executor = McpExecutor(default_timeout=15.0)
        info = {"protocol": "mcp", "endpoint": "https://mcp.example.com/db"}
        executor.execute("db_query", {}, protocol_info=info)
        client = executor._clients["https://mcp.example.com/db"]
        assert client.timeout == 15.0

    def test_default_timeout_reflected_in_timeout_error(self):
        """超时时错误信息应包含配置的 timeout 值。"""
        executor = McpExecutor(default_timeout=7.5)
        info = {"protocol": "mcp", "endpoint": "https://mcp.example.com/db"}
        # 先正常 initialize
        executor.execute("db_query", {"sql": "SELECT 1"}, protocol_info=info)
        client = executor._clients["https://mcp.example.com/db"]
        # 注入超时
        with patch.object(client, "_mock_call", side_effect=TimeoutError):
            resp = executor.execute("db_query", {"sql": "SELECT 2"}, protocol_info=info)
        assert resp.success is False
        assert "7.5s" in resp.error

    def test_default_timeout_default_30s(self):
        """未指定时 default_timeout 默认 30s(向后兼容)。"""
        executor = McpExecutor()
        assert executor._default_timeout == 30.0


# ════════════════════════════════════════════════════════════
#  10. 便捷接口 execute_mcp_tool / is_mcp_tool
# ════════════════════════════════════════════════════════════

class TestConvenienceInterfaces:
    """模块级便捷接口。"""

    def test_execute_mcp_tool_returns_mcp_response(self):
        """execute_mcp_tool 对已注册 mcp 工具返回成功响应。

        依赖 data/tool_definitions/*.yaml 已加载(db_query 为 mcp 工具)。
        """
        resp = execute_mcp_tool("db_query", {"sql": "SELECT 1"})
        # 若 TOOL_PROTOCOL_MAP 已加载(有 YAML),应成功;否则未注册
        if resp.success:
            assert resp.tool_name == "db_query"
            assert resp.endpoint  # 非空
        else:
            # 未加载场景下应是"未注册"错误,而非崩溃
            assert "未在 TOOL_PROTOCOL_MAP" in resp.error or "非 mcp" in resp.error

    def test_execute_mcp_tool_rejects_native_tool(self):
        """execute_mcp_tool 拒绝 native 工具(web_search)。"""
        resp = execute_mcp_tool("web_search", {"query": "test"})
        assert resp.success is False
        # web_search 在默认 TOOL_CATEGORIES 中,protocol=native
        assert "native" in resp.error or "未在 TOOL_PROTOCOL_MAP" in resp.error

    def test_get_mcp_executor_singleton(self):
        """get_mcp_executor 返回单例。"""
        e1 = get_mcp_executor()
        e2 = get_mcp_executor()
        assert e1 is e2

    def test_is_mcp_tool_db_query(self):
        """db_query 声明为 mcp(YAML 已加载时)。"""
        # is_mcp_tool 不抛异常即可;具体值依赖 YAML 加载状态
        result = is_mcp_tool("db_query")
        assert isinstance(result, bool)

    def test_is_mcp_tool_web_search_is_false(self):
        """web_search 非 mcp 工具。"""
        assert is_mcp_tool("web_search") is False

    def test_is_mcp_tool_unknown_tool_is_false(self):
        """未知工具返回 False(不抛异常)。"""
        assert is_mcp_tool("nonexistent_tool_xyz") is False


# ════════════════════════════════════════════════════════════
#  11. 异常类型语义
# ════════════════════════════════════════════════════════════

class TestExceptionTypes:
    """异常类型继承关系,确保 except 链顺序正确。"""

    def test_mcp_timeout_error_is_timeout_error(self):
        """McpTimeoutError 是 TimeoutError 子类,可被 except TimeoutError 捕获。"""
        assert issubclass(McpTimeoutError, TimeoutError)

    def test_mcp_auth_error_is_exception(self):
        """McpAuthError 是 Exception 子类,但非 TimeoutError。"""
        assert issubclass(McpAuthError, Exception)
        assert not issubclass(McpAuthError, TimeoutError)

    def test_mcp_auth_error_distinct_from_mcp_timeout_error(self):
        """两个异常类型互不继承,except 链可区分。"""
        assert not issubclass(McpAuthError, McpTimeoutError)
        assert not issubclass(McpTimeoutError, McpAuthError)


# ════════════════════════════════════════════════════════════
#  12. Logger 配置缺失 — 优雅降级与默认日志输出 ★新增场景
# ════════════════════════════════════════════════════════════

class TestMcpClientLoggerGracefulDegradation:
    """模拟 logger 配置缺失场景,验证优雅降级与默认日志输出。

    【不易】logger 配置缺失不应导致 call_tool 崩溃,响应结构必须正确;
           各异常分支(鉴权/超时/通用)日志调用均不得抛异常
    【变易】通过清理 logger.handlers + 禁用 propagate 模拟"配置缺失":
           - logger 无 handler 且不向 root 传播 → 走 Python lastResort 兜底
           - lastResort = StreamHandler(stderr, level=WARNING),仅输出 WARNING+
    【简易】复用现有 client 调用流程,仅断言关键行为不变;对照实验验证正常配置
    """

    def test_call_tool_works_without_logger_handlers(self):
        """logger 无 handler(配置缺失)时,call_tool 仍正常返回响应(优雅降级)。

        覆盖成功/鉴权失败/超时三分支,确保 logger.info 调用不抛异常。
        """
        from agent.mcp_executor import logger as mcp_logger
        client = McpClient("https://mcp.example.com/db")
        client.initialize()

        # 保存原始状态,finally 中恢复(避免污染其他测试)
        original_handlers = mcp_logger.handlers[:]
        original_level = mcp_logger.level
        original_propagate = mcp_logger.propagate
        try:
            # 模拟配置缺失:清空 handlers,置 NOTSET,禁用向 root 传播
            mcp_logger.handlers = []
            mcp_logger.setLevel(logging.NOTSET)
            mcp_logger.propagate = False

            # 成功分支 — logger.info 进入/退出不应抛异常
            resp1 = client.call_tool("db_query", {"sql": "SELECT 1"})
            assert resp1.success is True
            assert resp1.tool_name == "db_query"
            assert resp1.latency_ms > 0

            # 鉴权失败分支 — logger.info 异常分支不应抛异常
            with patch.object(client, "_mock_call", side_effect=McpAuthError("401")):
                resp2 = client.call_tool("db_query", {})
            assert resp2.success is False
            assert "鉴权失败" in resp2.error

            # 超时分支 — logger.info 异常分支不应抛异常
            with patch.object(client, "_mock_call", side_effect=TimeoutError):
                resp3 = client.call_tool("db_query", {})
            assert resp3.success is False
            assert "调用超时" in resp3.error
        finally:
            mcp_logger.handlers = original_handlers
            mcp_logger.setLevel(original_level)
            mcp_logger.propagate = original_propagate

    def test_warning_logs_fall_back_to_last_resort(self):
        """logger 无 handler 时,WARNING+ 日志通过 lastResort 兜底输出(默认日志)。

        lastResort 是 Python logging 内置的 StreamHandler(stderr, level=WARNING),
        当 logger 链找不到任何 handler 时启用,确保 warning 及以上日志不丢失。
        INFO 级别低于 WARNING,被 lastResort 丢弃(符合默认日志语义)。
        """
        from agent.mcp_executor import logger as mcp_logger

        original_handlers = mcp_logger.handlers[:]
        original_level = mcp_logger.level
        original_propagate = mcp_logger.propagate
        original_last_resort = logging.lastResort
        emitted_records = []

        # 自定义捕获 handler,替换 lastResort 以验证兜底输出
        class _CaptureHandler(logging.Handler):
            def emit(self, record):
                emitted_records.append(record)

        capture_handler = _CaptureHandler(level=logging.WARNING)
        try:
            mcp_logger.handlers = []
            mcp_logger.setLevel(logging.NOTSET)
            mcp_logger.propagate = False
            logging.lastResort = capture_handler

            mcp_logger.warning("[TEST] 模拟配置缺失下的 warning 日志")
            mcp_logger.error("[TEST] error 级别日志")
            # info 低于 WARNING,lastResort 应丢弃
            mcp_logger.info("[TEST] info 级别日志不应出现")

            messages = [r.getMessage() for r in emitted_records]
            # WARNING+ 应被 lastResort 捕获
            assert any("warning 日志" in m for m in messages), "warning 日志未走 lastResort 兜底"
            assert any("error 级别日志" in m for m in messages), "error 日志未走 lastResort 兜底"
            # info 不应出现(lastResort level=WARNING)
            assert not any("info 级别日志不应出现" in m for m in messages), \
                "info 日志不应被 lastResort 捕获"
        finally:
            mcp_logger.handlers = original_handlers
            mcp_logger.setLevel(original_level)
            mcp_logger.propagate = original_propagate
            logging.lastResort = original_last_resort

    def test_call_tool_logs_emitted_when_configured(self, caplog):
        """对照实验:logger 正常配置时,info 日志能被捕获(进入/退出日志)。

        与 test_call_tool_works_without_logger_handlers 形成对照,
        证明配置缺失时日志丢失但不崩溃,配置正常时日志完整输出。
        """
        from agent.mcp_executor import logger as mcp_logger
        client = McpClient("https://mcp.example.com/db")
        client.initialize()

        with caplog.at_level(logging.INFO, logger="agent.mcp_executor"):
            resp = client.call_tool("db_query", {"sql": "SELECT 1"})

        assert resp.success is True
        log_text = caplog.text
        # 应捕获进入/退出日志
        assert "[MCP] call_tool 进入" in log_text
        assert "[MCP] call_tool 退出" in log_text
        assert "结果=成功" in log_text
        assert "异常=无" in log_text


# ════════════════════════════════════════════════════════════
#  13. 动态日志级别 — 生产环境临时排查用 ★新增场景
# ════════════════════════════════════════════════════════════

class TestDynamicLogLevel:
    """动态日志级别调整:set_logger_level / get_logger_level。

    【不易】set_logger_level 必须立即生效且不崩溃;无效输入回退 INFO
    【变易】运行时调整 logger.level,影响后续日志过滤(无需重启进程)
    【简易】get_logger_level 返回字符串,便于运维确认;测试用 finally 恢复
    """

    def teardown_method(self):
        """每个测试后恢复默认 INFO 级别,避免污染其他测试。"""
        set_logger_level("INFO")

    def test_set_level_to_debug(self):
        """set_logger_level('DEBUG') 应将级别设为 DEBUG。"""
        result = set_logger_level("DEBUG")
        assert result == "DEBUG"
        assert get_logger_level() == "DEBUG"

    def test_set_level_to_warning(self):
        """set_logger_level('WARNING') 应将级别设为 WARNING。"""
        result = set_logger_level("WARNING")
        assert result == "WARNING"
        assert get_logger_level() == "WARNING"

    def test_set_level_case_insensitive(self):
        """级别字符串大小写不敏感('debug' 等价 'DEBUG')。"""
        result = set_logger_level("debug")
        assert result == "DEBUG"
        assert get_logger_level() == "DEBUG"

    def test_invalid_level_falls_back_to_info(self):
        """无效级别字符串应回退到 INFO,不抛异常。"""
        result = set_logger_level("VERBOSE")
        assert result == "INFO"
        assert get_logger_level() == "INFO"

    def test_empty_string_falls_back_to_info(self):
        """空字符串应回退到 INFO。"""
        result = set_logger_level("")
        assert result == "INFO"

    def test_level_change_affects_log_filtering(self):
        """级别调整应实际影响日志过滤(用 isEnabledFor 直接验证 logger 过滤)。

        注: 不用 caplog.at_level() 因为它会临时覆盖 logger 级别,
        无法验证 set_logger_level 的过滤效果。改用 isEnabledFor 直接断言。
        """
        from agent.mcp_executor import logger as mcp_logger

        # INFO 级别: INFO enabled, DEBUG disabled
        set_logger_level("INFO")
        assert mcp_logger.isEnabledFor(logging.INFO)
        assert not mcp_logger.isEnabledFor(logging.DEBUG)

        # WARNING 级别: INFO disabled, WARNING enabled
        set_logger_level("WARNING")
        assert not mcp_logger.isEnabledFor(logging.INFO)
        assert mcp_logger.isEnabledFor(logging.WARNING)

        # DEBUG 级别: 所有级别 enabled
        set_logger_level("DEBUG")
        assert mcp_logger.isEnabledFor(logging.DEBUG)
        assert mcp_logger.isEnabledFor(logging.INFO)

    def test_get_logger_level_returns_string(self):
        """get_logger_level 返回字符串(便于运维打印/记录)。"""
        set_logger_level("ERROR")
        level = get_logger_level()
        assert isinstance(level, str)
        assert level == "ERROR"


# ════════════════════════════════════════════════════════════
#  14. --verbose CLI 标志测试(subprocess 调用 __main__ 块) ★新增
# ════════════════════════════════════════════════════════════

class TestVerboseCliFlag:
    """--verbose CLI 标志测试:通过 subprocess 调用 mcp_executor.py __main__ 块。

    【不易】--verbose 必须将日志级别切换为 DEBUG; 默认(无参数)保持 INFO
    【变易】subprocess 隔离运行,不污染当前测试进程的 logger 状态
    【简易】断言 stdout/stderr 中的关键标记,不依赖日志格式细节
    """

    @staticmethod
    def _run_cli(*args: str) -> subprocess.CompletedProcess:
        """辅助:以子进程运行 mcp_executor.py CLI。"""
        import sys as _sys
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return subprocess.run(
            [_sys.executable, os.path.join("agent", "mcp_executor.py"), *args],
            capture_output=True, text=True, timeout=15,
            cwd=project_root,
        )

    def test_verbose_flag_switches_to_debug(self):
        """--verbose 应切换日志级别为 DEBUG,输出 DEBUG 级别协议日志。"""
        result = self._run_cli("--verbose")
        assert result.returncode == 0, f"CLI 异常退出: {result.stderr}"
        combined = result.stdout + result.stderr
        # 应显示 DEBUG 模式标记
        assert "日志级别已切换为 DEBUG" in combined
        # DEBUG 级别的 initialize 日志应出现(仅 DEBUG 可见)
        assert "[MCP] initialize 成功" in combined

    def test_default_mode_stays_info(self):
        """无 --verbose 时默认 INFO,不输出 DEBUG 级别日志。"""
        result = self._run_cli()
        assert result.returncode == 0, f"CLI 异常退出: {result.stderr}"
        combined = result.stdout + result.stderr
        # 应显示 INFO 模式标记
        assert "默认模式: 日志级别=INFO" in combined
        # DEBUG 级别的 initialize 日志不应出现
        assert "[MCP] initialize 成功" not in combined

    def test_short_flag_v_works(self):
        """-v 短标志等价于 --verbose。"""
        result = self._run_cli("-v")
        assert result.returncode == 0, f"CLI 异常退出: {result.stderr}"
        combined = result.stdout + result.stderr
        assert "日志级别已切换为 DEBUG" in combined

    def test_verbose_overrides_env_warning(self):
        """--verbose 应覆盖 env MCP_LOG_LEVEL=WARNING,最终级别为 DEBUG。

        优先级: CLI --verbose > env MCP_LOG_LEVEL > 默认 INFO
        场景: 生产环境 MCP_LOG_LEVEL=WARNING(抑制 INFO 日志),排查时
              python agent/mcp_executor.py --verbose 临时切换 DEBUG
        """
        import sys as _sys
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env = {**os.environ, "MCP_LOG_LEVEL": "WARNING", "PYTHONPATH": project_root}
        result = subprocess.run(
            [_sys.executable, os.path.join("agent", "mcp_executor.py"), "--verbose"],
            capture_output=True, text=True, timeout=15,
            cwd=project_root, env=env,
        )
        assert result.returncode == 0, f"CLI 异常退出: {result.stderr}"
        combined = result.stdout + result.stderr
        # --verbose 应强制切换到 DEBUG,覆盖 env 的 WARNING
        assert "日志级别已切换为 DEBUG" in combined
        # DEBUG 级别的 initialize 日志应出现(证明确实在 DEBUG 级别运行)
        assert "[MCP] initialize 成功" in combined
        # 不应出现 WARNING 级别的最终状态
        assert "日志级别=WARNING" not in combined

    def test_env_warning_without_verbose_stays_warning(self):
        """无 --verbose 时 env MCP_LOG_LEVEL=WARNING 应保持 WARNING。

        对照实验: 与 test_verbose_overrides_env_warning 形成对比,
        证明优先级逻辑: --verbose 是显式覆盖,无 --verbose 时 env 生效。
        """
        import sys as _sys
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env = {**os.environ, "MCP_LOG_LEVEL": "WARNING", "PYTHONPATH": project_root}
        result = subprocess.run(
            [_sys.executable, os.path.join("agent", "mcp_executor.py")],
            capture_output=True, text=True, timeout=15,
            cwd=project_root, env=env,
        )
        assert result.returncode == 0, f"CLI 异常退出: {result.stderr}"
        combined = result.stdout + result.stderr
        # 无 --verbose 时,env WARNING 生效
        assert "默认模式: 日志级别=WARNING" in combined
        # DEBUG 级别日志不应出现(WARNING > DEBUG,被过滤)
        assert "[MCP] initialize 成功" not in combined

    def test_env_critical_falls_back_to_info(self):
        """MCP_LOG_LEVEL=CRITICAL(恶意值)应回退到 INFO,程序正常启动且 ERROR 日志可见。

        【不易】CRITICAL=50 > ERROR=40,若接受会抑制 ERROR 日志;必须回退到 INFO
        【变易】模块加载时校验白名单,与 set_logger_level 统一;CRITICAL 非白名单值
        【简易】subprocess 隔离验证,断言 4 个关键标记:退出码/警告/级别/ERROR探针

        场景: 容器内环境变量被恶意修改为 CRITICAL,验证:
          1. 程序正常启动(exit code 0)
          2. 输出无效级别警告(stderr,走 lastResort 兜底)
          3. 级别回退到 INFO(stdout 显示)
          4. ERROR 级别探针日志可见(证明 ERROR 日志链路畅通,未被 CRITICAL 抑制)
        """
        import sys as _sys
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env = {**os.environ, "MCP_LOG_LEVEL": "CRITICAL", "PYTHONPATH": project_root}
        result = subprocess.run(
            [_sys.executable, os.path.join("agent", "mcp_executor.py")],
            capture_output=True, text=True, timeout=15,
            cwd=project_root, env=env,
        )
        # 1. 程序正常启动
        assert result.returncode == 0, f"程序未正常启动: {result.stderr}"
        combined = result.stdout + result.stderr
        # 2. 无效级别警告应输出到 stderr(模块加载时 logger.warning 走 lastResort)
        assert "无效日志级别 'CRITICAL',回退到 INFO" in result.stderr, \
            f"未输出 CRITICAL 回退警告。stderr: {result.stderr}"
        # 3. 最终级别应为 INFO(非 CRITICAL)
        assert "默认模式: 日志级别=INFO" in result.stdout, \
            f"级别未回退到 INFO。stdout: {result.stdout}"
        # 4. ERROR 探针日志应可见(证明 ERROR 级别日志未被 CRITICAL 抑制)
        assert "[MCP] 自检探针" in combined, \
            f"ERROR 探针日志不可见(CRITICAL 可能未回退)。combined: {combined}"
        # 5. DEBUG 级别日志不应出现(INFO 级别过滤 DEBUG)
        assert "[MCP] initialize 成功" not in combined, \
            "DEBUG 日志不应在 INFO 级别下出现"
