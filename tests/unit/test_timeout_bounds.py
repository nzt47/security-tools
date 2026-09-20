"""超时上界单测（TASK-08 子工作流 D / E1j · E15）

【本文件证明什么】
    两条**改动前无超时上界**的路径，现在都能在**有限时间**内返回结构化超时
    错误，而不是无限阻塞：

      路径 2 —— `agent/tools/__init__.py::call()` 的 `result = tool["handler"](**params)`
      路径 1 —— `agent/skills_mgmt/mcp_adapter.py` 的 `session.initialize()` /
                `session.list_tools()` 裸调

【纪律（D12：时间类断言不真等）】
    不 sleep 真实的长超时。做法是**注入极短的上界**（0.3~1s），并断言
    "确实在远小于挂死时长的范围内返回"。挂死的 handler 用 `threading.Event().wait()`
    且该事件**永不 set**，即真正的无限阻塞 —— 因此本测试若通过，就等于
    "上界真的生效"，而非"恰好跑得快"。

【路径 1 为什么要注入假的 mcp SDK】
    实测本环境 **`mcp` 包未安装**（`importlib.util.find_spec("mcp") is None`）。
    因此 `mcp_adapter` 的那两个裸调当前是**不可达的死代码**：`_list_tools` 会先在
    `ImportError` 分支抛 `MCP_SDK_UNAVAILABLE`，根本走不到 `session.initialize()`。
    为了让修复真的被测到（而不是"测了个寂寞"），这里用 `sys.modules` 注入一个
    最小假 SDK，并 `monkeypatch.setitem` 保证测试后自动还原。
"""

from __future__ import annotations

import sys
import threading
import time
import types

import pytest


# ════════════════════════════════════════════════════════════
#  路径 2：工具 handler 分发（agent/tools/__init__.py::call）
# ════════════════════════════════════════════════════════════

class TestToolHandlerTimeoutBound:
    """`call()` 必须在全局上界内返回，而不是被挂死的 handler 永久阻塞"""

    def _register_hanging_tool(self, name: str, hang_forever: bool = True):
        from agent import tools as tools_mod
        gate = threading.Event()

        def _hanging_handler(**kwargs):
            # 真·无限阻塞：wait() 无超时且事件永不 set
            gate.wait()
            return {"ok": True, "should": "never_reach"}

        tools_mod.register(name, "测试用挂死工具", handler=_hanging_handler)
        return tools_mod, gate

    def test_hanging_handler_returns_timeout_error_bounded(self, monkeypatch):
        """挂死的 handler ⇒ `call()` 在极短时间内返回结构化超时错误"""
        # 注入极短上界（0.3s），避免真等默认 1800s
        monkeypatch.setenv("CP_TOOL_HANDLER_TIMEOUT_SEC", "0.3")
        name = "_t08d_hanging_tool"
        tools_mod, _gate = self._register_hanging_tool(name)

        t0 = time.monotonic()
        result = tools_mod.call(name)
        elapsed = time.monotonic() - t0

        # 核心断言 1：有界返回（远小于任何"无限"）
        assert elapsed < 5.0, f"未在限定时间内返回，耗时 {elapsed:.2f}s"
        # 核心断言 2：返回**结构化错误**而不是抛异常/阻塞
        assert isinstance(result, dict), f"应返回结构化错误 dict，实际 {type(result)}"
        assert result.get("ok") is False
        assert result.get("error_code") == "timeout", result
        assert result.get("tool") == name
        assert "超时" in str(result.get("error", ""))

    def test_fast_handler_still_returns_normally(self, monkeypatch):
        """不变量：加界不得伤到正常路径（D2 向后兼容）"""
        monkeypatch.setenv("CP_TOOL_HANDLER_TIMEOUT_SEC", "5")
        from agent import tools as tools_mod
        name = "_t08d_fast_tool"
        tools_mod.register(name, "测试用正常工具",
                           handler=lambda **kw: {"ok": True, "echo": kw.get("x")})

        result = tools_mod.call(name, x=42)
        assert result == {"ok": True, "echo": 42}

    def test_timeout_zero_disables_bound_preserving_old_behavior(self, monkeypatch):
        """回滚开关：`CP_TOOL_HANDLER_TIMEOUT_SEC=0` ⇒ 不限时（旧行为）"""
        monkeypatch.setenv("CP_TOOL_HANDLER_TIMEOUT_SEC", "0")
        from agent.timeout_budget import resolve_tool_handler_timeout
        assert resolve_tool_handler_timeout({"name": "x"}, {}) == 0.0

    def test_handler_exception_still_propagates_as_tool_error(self, monkeypatch):
        """不变量：有界调用不得吞掉 handler 的异常语义（仍应抛 ToolError）"""
        monkeypatch.setenv("CP_TOOL_HANDLER_TIMEOUT_SEC", "5")
        from agent import tools as tools_mod
        name = "_t08d_raising_tool"

        def _boom(**kw):
            raise ValueError("boom")

        tools_mod.register(name, "测试用抛错工具", handler=_boom)
        with pytest.raises(tools_mod.ToolError):
            tools_mod.call(name)

    def test_resolve_bound_follows_declared_scalar_timeout(self, monkeypatch):
        """外层上界随工具自述的标量 timeout 收紧（但不低于它）"""
        monkeypatch.setenv("CP_TOOL_HANDLER_TIMEOUT_SEC", "1800")
        from agent.timeout_budget import resolve_tool_handler_timeout
        # 自述 30s ⇒ 外层 45s（30 + 15s 宽限）
        assert resolve_tool_handler_timeout({"name": "x"}, {"timeout": 30}) == 45.0
        # 不带自述 ⇒ 用全局天花板
        assert resolve_tool_handler_timeout({"name": "x"}, {}) == 1800.0
        # `timeout_seconds` 是**每个子任务**的时长，不得据此收紧外层
        # （subagent/fan_out 整批合法地远大于它）
        assert resolve_tool_handler_timeout({"name": "x"},
                                            {"timeout_seconds": 30}) == 1800.0


# ════════════════════════════════════════════════════════════
#  路径 1：MCP adapter（注入最小假 SDK）
# ════════════════════════════════════════════════════════════

class _FakeClientSession:
    """最小假 ClientSession：`list_tools` 永不返回（模拟 server 沉默挂死）"""

    def __init__(self, read, write, **kwargs):
        self._read = read
        self._write = write
        self.init_called = False
        self.read_timeout_seconds = kwargs.get("read_timeout_seconds")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        self.init_called = True
        return {"protocolVersion": "2024-11-05"}

    async def list_tools(self):
        import asyncio
        await asyncio.sleep(3600)          # 永不返回
        raise AssertionError("unreachable")

    async def call_tool(self, name, arguments):
        import asyncio
        await asyncio.sleep(3600)          # 永不返回
        raise AssertionError("unreachable")


class _FakeAsyncCM:
    """假 async 上下文管理器（对应 stdio_client / sse_client）"""

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return (object(), object())

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def fake_mcp_sdk(monkeypatch):
    """注入最小假 `mcp` SDK（本环境真实 mcp 包未安装，见文件 docstring）"""
    mcp_mod = types.ModuleType("mcp")
    mcp_mod.ClientSession = _FakeClientSession

    class _StdioServerParameters:
        def __init__(self, command="", args=None, env=None):
            self.command, self.args, self.env = command, args or [], env

    mcp_mod.StdioServerParameters = _StdioServerParameters

    stdio_mod = types.ModuleType("mcp.client.stdio")
    stdio_mod.stdio_client = _FakeAsyncCM
    sse_mod = types.ModuleType("mcp.client.sse")
    sse_mod.sse_client = _FakeAsyncCM
    client_mod = types.ModuleType("mcp.client")

    monkeypatch.setitem(sys.modules, "mcp", mcp_mod)
    monkeypatch.setitem(sys.modules, "mcp.client", client_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.sse", sse_mod)
    return mcp_mod


class TestMcpAdapterTimeoutBound:
    """MCP 裸调必须被上界包住；且 `config.timeout` 必须真的生效"""

    def _adapter(self):
        from agent.skills_mgmt.mcp_adapter import McpSkillAdapter
        return McpSkillAdapter(skills_service=None, auto_review=False)

    def _config(self, timeout):
        from agent.skills_mgmt.mcp_adapter import McpServerConfig
        return McpServerConfig(name="fake", transport="stdio",
                               command="python", args=["-c", "pass"],
                               timeout=timeout)

    def test_list_tools_times_out_instead_of_hanging(self, fake_mcp_sdk):
        """server 挂死 ⇒ `_list_tools` 在有界时间内抛 SkillMcpError（而非无限阻塞）"""
        from agent.skills_mgmt.exceptions import SkillMcpError
        adapter = self._adapter()
        cfg = self._config(timeout=1)          # 注入 1s 上界

        t0 = time.monotonic()
        with pytest.raises(SkillMcpError) as ei:
            adapter._list_tools(cfg)
        elapsed = time.monotonic() - t0

        assert elapsed < 20.0, f"未在有界时间内返回，耗时 {elapsed:.2f}s"
        assert "超时" in str(ei.value), str(ei.value)

    def test_call_tool_times_out_instead_of_hanging(self, fake_mcp_sdk):
        """`_call_tool` 同样必须有界"""
        from agent.skills_mgmt.exceptions import SkillMcpError
        adapter = self._adapter()
        cfg = self._config(timeout=1)

        t0 = time.monotonic()
        with pytest.raises(SkillMcpError):
            adapter._call_tool(cfg, "some_tool", {"a": 1})
        elapsed = time.monotonic() - t0
        assert elapsed < 20.0, f"未在有界时间内返回，耗时 {elapsed:.2f}s"

    def test_configured_timeout_is_actually_used(self, fake_mcp_sdk):
        """★E1j 核心：`McpServerConfig.timeout` 不再被忽略 —— 它真的决定上界

        改动前该字段**从未传给 SDK**（配置了等于没配）。现在
        `_adapter_call_timeout(config)` 优先取它，且会传进 `ClientSession`。
        """
        from agent.skills_mgmt.mcp_adapter import _adapter_call_timeout
        assert _adapter_call_timeout(self._config(timeout=7)) == 7.0
        # 配置缺省（0）时才回落到全局兜底开关
        monkeypatch_default = 30.0
        assert _adapter_call_timeout(self._config(timeout=0)) == monkeypatch_default

    def test_read_timeout_passed_into_session_when_supported(self, fake_mcp_sdk):
        """SDK 支持时，把上界**真正传进** ClientSession（第二道独立的界）"""
        from agent.skills_mgmt.mcp_adapter import _open_session
        cfg = self._config(timeout=5)
        session = _open_session(_FakeClientSession, object(), object(), cfg)
        assert session.read_timeout_seconds is not None
        assert session.read_timeout_seconds.total_seconds() == 5.0

    def test_session_construction_falls_back_when_kwarg_unsupported(self, fake_mcp_sdk):
        """老版本 SDK 不接受 `read_timeout_seconds` ⇒ 回退且不抛（仍有线程上界）"""
        from agent.skills_mgmt.mcp_adapter import _open_session

        class _Strict:
            def __init__(self, read, write):
                self.ok = True

        cfg = self._config(timeout=5)
        session = _open_session(_Strict, object(), object(), cfg)
        assert session.ok is True
