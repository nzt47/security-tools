"""云枢 MCP 服务端单元测试 —— ``mcp_services/yunshu_mcp_server.py``

覆盖：
1. ``initialize`` 握手（protocolVersion / serverInfo / capabilities.tools）
2. ``tools/list`` 的**默认只读五件套**与 MCP 形状（name/description/inputSchema）
3. ``tools/call`` 成功（读临时文件）与失败（不存在 / 未暴露 / 工具抛异常）
4. ``tools/call`` 命中集中式闸门（证明走的是 ``agent.tools.call`` 而非直调 handler）
5. 未知方法 → ``-32601``
6. ``CP_MCP_SERVER_TOOLS`` 覆盖生效；含未注册名 ⇒ **启动即失败**（退出码 2）
7. stdout 纯净化（除 JSON-RPC 帧外不得有内容落到真实 stdout）

【不易】不启动子进程、不开端口、不跑完整 DigitalLife；直接 import 模块调
        ``handle_request``。默认清单是**安全边界**，逐名钉死写入/执行类不在其中。
【变易】``mcp_services`` 不是包（无 ``__init__.py``），用
        ``importlib.util.spec_from_file_location`` 加载
        （同 ``tests/unit/test_tool_definitions_yaml.py:34-43`` 的手法）。
【简易】只用 ``tmp_path`` / ``monkeypatch`` / ``capsys``；不依赖网络与真实数据目录。
"""
from __future__ import annotations

import importlib.util
import io
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SERVER_PATH = _PROJECT_ROOT / "mcp_services" / "yunshu_mcp_server.py"


def _load_server_module():
    """动态加载服务端模块（mcp_services 非包）"""
    spec = importlib.util.spec_from_file_location("yunshu_mcp_server", _SERVER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["yunshu_mcp_server"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


srv = _load_server_module()


# ════════════════════════════════════════════════════════════
#  夹具
# ════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """隔离环境变量：避免外部 ``CP_MCP_SERVER_TOOLS`` / 闸门开关影响用例"""
    monkeypatch.delenv(srv.ENV_EXPOSED_TOOLS, raising=False)
    monkeypatch.delenv("CP_TOOL_GATE_ENABLED", raising=False)
    yield


@pytest.fixture(scope="module", autouse=True)
def _restore_tool_registry():
    """**本模块跑完**把进程级工具注册表逐条还原（快照在手）

    【不易·为什么必须有】`srv.YunshuMCPHandler()` 构造时会 `_register_exposed_tools`，
    把暴露的**真实工具**登记进 `agent/tools/__init__.py:_registry`；本文件在夹具
    （`handler`）与**多个用例体**里都会构造它 ⇒ 用例体那几处没有 teardown 可言。
    实测本文件单独跑会留下 10 个真实工具（grep/edit/read_file/write_file/
    list_directory/get_file_info/search_files/compress/decompress/diff_files）；
    留着就污染同进程后续测试（本文件排在 `tests/unit/test_tool_count_consistency.py`
    之前时，后者"下发集 == 我登记的 3 个工具"的前置条件当场失配 ⇒ 2 failed）。

    【口径不降】断言一条未改：只做"快照 → 用例跑 → 逐条放回"（含 source / schema /
    handler / source_id），并推进 `_registry_version` 让各级缓存失效。
    【为什么不在用例开始时清空】**只还原**作用面最小：本文件不要求空注册表，
    清空会改变用例看到的世界（`test_exposed_dl_dependent_tool_fails_closed`
    本来就靠自己先注销同名工具来保证确定性）。
    【为什么不用 `tools.clear()`】它会连 `_tool_health` 一起清。

    【不易·为什么是 module 而不是 function 作用域（实测踩过）】改成逐用例还原会**打红**
    `TestExposedToolsEnvOverride::test_override_can_narrow_to_one_tool`：
      · 该用例要求 `get_file_info` 已是**已注册**工具（`_register_exposed_tools()` 只在
        `"read_file" not in known` 时才整体注册文件工具五件套）；
      · 逐用例还原后，残留只有 `read_file`（来自 `test_tool_exception_is_wrapped_not_raised`
        的 `monkeypatch.setitem(agent_tools._registry, "read_file", …)`）⇒ 五件套不再注册
        ⇒ 构造即抛 `ToolConfigError: 含未注册的工具名: ['get_file_info']`。
      · 根因是**夹具 teardown 顺序**：autouse 夹具在 fixture 闭包里排最后 ⇒ **最先**被 teardown，
        `monkeypatch` 的 undo 在它之后执行，于是把 read_file 又写回注册表。
    模块级夹具在该模块**所有**用例的函数级夹具都收尾之后才 teardown ⇒ 既真正清干净，
    又保留本文件内部原有的"先注册后暴露"用例间可见性（不改变任何用例看到的世界）。
    """
    from agent import tools as _tools

    saved = dict(_tools._registry)
    try:
        yield
    finally:
        _tools._registry.clear()
        _tools._registry.update(saved)
        _tools._registry_version += 1


@pytest.fixture()
def handler(_clean_env):
    """默认配置的处理器（只读五件套）"""
    return srv.YunshuMCPHandler()


@pytest.fixture()
def _restore_root_logging():
    """``main()`` 会 ``basicConfig(force=True)``，用完还原宿主日志配置"""
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _call(handler, tool_name: str, arguments: Dict[str, Any], req_id: int = 1) -> Dict[str, Any]:
    """发一帧 tools/call 并返回 JSON-RPC 响应"""
    response = handler.handle_request({
        "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    })
    assert response is not None
    return response


def _text_of(response: Dict[str, Any]) -> str:
    """取工具结果里的 text 内容"""
    return response["result"]["content"][0]["text"]


# ════════════════════════════════════════════════════════════
#  1. initialize
# ════════════════════════════════════════════════════════════

class TestInitialize:
    """``initialize`` 握手。"""

    def test_returns_protocol_version_server_info_and_tools_capability(self, handler):
        response = handler.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert response is not None
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        result = response["result"]
        assert isinstance(result["protocolVersion"], str) and result["protocolVersion"]
        assert result["serverInfo"]["name"] == "yunshu-mcp-server"
        assert result["serverInfo"]["version"]
        assert "tools" in result["capabilities"]
        assert isinstance(result["capabilities"]["tools"], dict)

    def test_capabilities_do_not_declare_unimplemented_primitives(self, handler):
        """resources / prompts 未实现 ⇒ 不得声明（声明了却返回 -32601 会误导客户端）"""
        response = handler.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert response is not None
        capabilities = response["result"]["capabilities"]
        assert "resources" not in capabilities
        assert "prompts" not in capabilities

    def test_echoes_supported_protocol_version(self, handler):
        """客户端请求受支持的版本 → 回显（MCP 规定服务端须回支持的版本）"""
        for version in srv.SUPPORTED_PROTOCOL_VERSIONS:
            response = handler.handle_request({
                "jsonrpc": "2.0", "id": 2, "method": "initialize",
                "params": {"protocolVersion": version},
            })
            assert response is not None
            assert response["result"]["protocolVersion"] == version

    def test_unsupported_protocol_version_falls_back_to_default(self, handler):
        response = handler.handle_request({
            "jsonrpc": "2.0", "id": 3, "method": "initialize",
            "params": {"protocolVersion": "1999-01-01"},
        })
        assert response is not None
        assert response["result"]["protocolVersion"] == srv.DEFAULT_PROTOCOL_VERSION


# ════════════════════════════════════════════════════════════
#  2. tools/list
# ════════════════════════════════════════════════════════════

class TestToolsList:
    """``tools/list`` 的清单与字段形状。"""

    def test_exactly_default_readonly_five(self, handler):
        """恰好返回默认只读五件套（集合相等）"""
        response = handler.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        assert response is not None
        names = {tool["name"] for tool in response["result"]["tools"]}
        assert names == set(srv.DEFAULT_EXPOSED_TOOLS)
        assert names == {"read_file", "list_directory", "get_file_info", "search_files", "grep"}

    def test_item_shape_is_mcp_not_openai(self, handler):
        """每项必须是 MCP 形状 {"name","description","inputSchema"}（非 OpenAI function 形状）"""
        response = handler.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        assert response is not None
        tools: List[Dict[str, Any]] = response["result"]["tools"]
        assert len(tools) == len(srv.DEFAULT_EXPOSED_TOOLS)
        for tool in tools:
            assert set(tool.keys()) == {"name", "description", "inputSchema"}
            assert isinstance(tool["name"], str) and tool["name"]
            assert isinstance(tool["description"], str) and tool["description"].strip()
            schema = tool["inputSchema"]
            assert isinstance(schema, dict)
            assert schema["type"] == "object"
            assert isinstance(schema["properties"], dict)

    def test_write_and_exec_tools_are_absent(self, handler):
        """写入/执行类工具不得出现在列表里"""
        response = handler.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        assert response is not None
        names = {tool["name"] for tool in response["result"]["tools"]}
        for forbidden in ("write_file", "edit", "shell_execute", "run_program",
                          "software_install", "delegate", "todo_write"):
            assert forbidden not in names, f"{forbidden} 不应出现在 tools/list"

    def test_default_exposed_list_has_no_write_or_exec_tool(self):
        """默认清单逐名断言不含写入/执行类（安全边界的静态钉死）"""
        for name in srv.WRITE_OR_EXEC_TOOLS:
            assert name not in srv.DEFAULT_EXPOSED_TOOLS, f"{name} 不得进默认暴露清单"


# ════════════════════════════════════════════════════════════
#  3. tools/call —— 成功 / 失败
# ════════════════════════════════════════════════════════════

class TestToolsCallSuccess:
    """``tools/call`` 成功路径。"""

    def test_read_file_on_tmp_path(self, handler, tmp_path):
        """对临时文件调 read_file：text 类型 + 含文件内容 + 无 isError"""
        target = tmp_path / "hello.txt"
        target.write_text("yunshu-mcp-smoke-content-ABC", encoding="utf-8")

        response = _call(handler, "read_file", {"path": str(target)})
        result = response["result"]
        assert result["content"][0]["type"] == "text"
        text = _text_of(response)
        assert "yunshu-mcp-smoke-content-ABC" in text
        assert "isError" not in result

    def test_read_file_chinese_only_file_comes_back_as_text(self, handler, tmp_path):
        """中文为主的 UTF-8 文件必须以**文本**返回，不能落进 base64

        回归背景：``agent/tools/file_tools.py`` 的 ``is_binary_content``（L149-162）只把
        ASCII 可打印字节计为文本，而中文字节均 >= 0x80 ⇒ 中文稍多就被判成二进制、
        ``read_file`` 于是返回 base64——对一个中文智能体等于"读不了中文文件"。
        ``read_file`` 现已改用 ``_is_probably_binary``（NUL 快速判二进制，否则逐个尝试
        严格解码），本条守该行为，防止回退。
        """
        target = tmp_path / "zh.txt"
        target.write_text("云枢中文内容", encoding="utf-8")

        response = _call(handler, "read_file", {"path": str(target)})
        payload = json.loads(_text_of(response))
        assert payload["ok"] is True
        assert payload.get("binary") is not True
        assert payload["content"] == "云枢中文内容"

    def test_grep_on_tmp_path(self, handler, tmp_path):
        """grep 命中：返回 ok=True 且带命中行号"""
        target = tmp_path / "sample.py"
        target.write_text("line1\nneedle_here\nline3\n", encoding="utf-8")

        response = _call(handler, "grep", {"pattern": "needle_here", "path": str(tmp_path)})
        payload = json.loads(_text_of(response))
        assert payload["ok"] is True
        assert payload["count"] >= 1


class TestToolsCallFailure:
    """``tools/call`` 失败路径 —— 收口成错误结果，服务不崩。"""

    def test_unknown_tool_returns_error_result(self, handler):
        response = _call(handler, "no_such_tool_xyz", {})
        result = response["result"]
        assert result["isError"] is True
        assert "no_such_tool_xyz" in _text_of(response)

    def test_service_survives_after_unknown_tool(self, handler, tmp_path):
        """失败之后仍能正常服务（不崩）"""
        _call(handler, "no_such_tool_xyz", {}, req_id=1)
        target = tmp_path / "alive.txt"
        target.write_text("still alive", encoding="utf-8")
        response = _call(handler, "read_file", {"path": str(target)}, req_id=2)
        assert "still alive" in _text_of(response)

    def test_not_exposed_tool_is_refused_before_handler(self, handler, tmp_path):
        """未暴露的 write_file 被清单短路拒绝，且**磁盘上什么都没写**"""
        target = tmp_path / "must_not_exist.txt"
        response = _call(handler, "write_file", {"path": str(target), "content": "不该被写入"})
        result = response["result"]
        assert result["isError"] is True
        text = _text_of(response)
        assert "未在本服务端暴露" in text
        assert "TOOL_NOT_EXPOSED" in text
        assert not target.exists(), "未暴露的写工具不得真的写盘"

    def test_tool_exception_is_wrapped_not_raised(self, handler, monkeypatch):
        """工具执行异常收口成错误结果（不抛到协议层）"""
        from agent import tools as agent_tools

        def _boom(**kwargs):
            raise RuntimeError("模拟工具内部异常")

        monkeypatch.setitem(agent_tools._registry, "read_file", {
            "name": "read_file", "description": "stub", "handler": _boom,
        })
        response = _call(handler, "read_file", {"path": "whatever"})
        result = response["result"]
        assert result["isError"] is True
        assert "模拟工具内部异常" in _text_of(response)


# ════════════════════════════════════════════════════════════
#  4. 集中式闸门 —— 证明走的是 tools.call
# ════════════════════════════════════════════════════════════

class TestToolGateIsOnThePath:
    """把 ``read_file`` 放进假策略的 ``denied_tools`` ⇒ 必须被闸门拒绝。

    闸门只装在 ``agent/tools/__init__.py::call()`` 里；本用例命中即证明服务端
    走的是 ``tools.call`` 而不是直接调 handler。
    """

    def test_gate_denial_surfaces_as_error_result(self, handler, monkeypatch, tmp_path):
        from agent import tool_gate

        policy = tmp_path / "fake_policies.json"
        policy.write_text(
            json.dumps({"roles": {"guest": {"denied_tools": ["read_file"]}}}, ensure_ascii=False),
            encoding="utf-8",
        )
        monkeypatch.setattr(tool_gate, "POLICY_POLICIES_PATH", str(policy))
        monkeypatch.setattr(tool_gate, "DESCRIPTORS_PATH", str(tmp_path / "absent_descriptors.json"))
        tool_gate._reset_cache()

        target = tmp_path / "gated.txt"
        target.write_text("闸门命中前的文件内容", encoding="utf-8")
        try:
            response = _call(handler, "read_file", {"path": str(target)})
        finally:
            tool_gate._reset_cache()

        result = response["result"]
        assert result["isError"] is True
        text = _text_of(response)
        assert "PERMISSION_DENIED" in text
        assert "blocked" in text
        # 内容没有被读出来（拒绝发生在 handler 之前）
        assert "闸门命中前的文件内容" not in text


# ════════════════════════════════════════════════════════════
#  5. 协议层：未知方法 / 通知 / ping
# ════════════════════════════════════════════════════════════

class TestProtocol:
    """JSON-RPC 路由与错误码。"""

    def test_unknown_method_returns_method_not_found(self, handler):
        for method in ("resources/list", "prompts/list", "no/such/method"):
            response = handler.handle_request({"jsonrpc": "2.0", "id": 7, "method": method, "params": {}})
            assert response is not None
            assert response["error"]["code"] == -32601
            assert response["id"] == 7

    def test_ping_returns_pong(self, handler):
        response = handler.handle_request({"jsonrpc": "2.0", "id": 8, "method": "ping", "params": {}})
        assert response is not None
        assert response["result"] == {"pong": True}

    def test_notification_produces_no_response_frame(self, handler):
        """``notifications/*`` 是通知（无 id），按 JSON-RPC 2.0 不产生响应帧"""
        assert handler.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    def test_malformed_request_returns_invalid_request(self, handler):
        assert handler.handle_request("not-a-dict")["error"]["code"] == -32600
        assert handler.handle_request({"jsonrpc": "2.0", "id": 9})["error"]["code"] == -32600


# ════════════════════════════════════════════════════════════
#  6. dl 替身 fail-closed
# ════════════════════════════════════════════════════════════

class TestMinimalDlFailClosed:
    """``_MinimalDl`` 必须 fail-closed，不能静默跳过 ``dl._permission``。"""

    def test_any_attribute_access_raises(self):
        dl = srv._MinimalDl()
        with pytest.raises(srv._DlAccessError):
            dl._permission

    def test_getattr_default_does_not_swallow_error(self):
        """``getattr(dl, '_permission', None)`` 不得返回 None（那就是静默绕过安全检查）"""
        dl = srv._MinimalDl()
        with pytest.raises(srv._DlAccessError):
            getattr(dl, "_permission", None)

    def test_error_is_not_attribute_error(self):
        """刻意不是 AttributeError —— 否则会被 ``getattr(..., default)`` 吞掉"""
        assert not issubclass(srv._DlAccessError, AttributeError)

    def test_exposed_dl_dependent_tool_fails_closed(self, monkeypatch, tmp_path):
        """放开依赖 dl 的 write_file ⇒ 调用时当场报错且不写盘（fail-closed）

        必须先清掉可能由**其它测试**先注册的同名工具：``_register_exposed_tools``
        刻意"已注册的不重复注册"（``yunshu_mcp_server.py:202,209-212``），若别的测试
        已把 ``read_file``/``write_file`` 绑定到它自己的 stub dl，本测试就会拿到那个
        handler 而不是 ``_MinimalDl`` 绑定的，断言随之失真（实测：全量/随机顺序下
        报 ``'NoneType' object has no attribute 'check_action'``）。
        """
        from agent import tools as _tools
        for _name in ("read_file", "grep"):
            _tools.unregister(_name)

        monkeypatch.setenv(srv.ENV_EXPOSED_TOOLS, "write_file")
        handler = srv.YunshuMCPHandler()
        assert "write_file" in handler.exposed_tools

        target = tmp_path / "dl_fail_closed.txt"
        response = _call(handler, "write_file", {"path": str(target), "content": "不该落盘"})
        result = response["result"]
        assert result["isError"] is True
        assert "未启动 DigitalLife" in _text_of(response)
        assert not target.exists()


# ════════════════════════════════════════════════════════════
#  7. CP_MCP_SERVER_TOOLS
# ════════════════════════════════════════════════════════════

class TestExposedToolsEnvOverride:
    """环境变量放宽清单：生效、且未注册名一律拒绝启动。"""

    def test_override_takes_effect(self, monkeypatch):
        monkeypatch.setenv(srv.ENV_EXPOSED_TOOLS, "grep,read_file")
        handler = srv.YunshuMCPHandler()
        assert handler.exposed_tools == ("grep", "read_file")
        response = handler.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        assert response is not None
        assert {t["name"] for t in response["result"]["tools"]} == {"grep", "read_file"}

    def test_override_can_narrow_to_one_tool(self, monkeypatch):
        monkeypatch.setenv(srv.ENV_EXPOSED_TOOLS, "get_file_info")
        handler = srv.YunshuMCPHandler()
        response = handler.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "read_file", "arguments": {}}})
        assert response is not None
        assert response["result"]["isError"] is True
        assert "未在本服务端暴露" in _text_of(response)

    def test_unknown_tool_name_rejected_at_construction(self, monkeypatch):
        """含不存在的工具名 ⇒ 构造处理器即失败（不静默忽略）"""
        monkeypatch.setenv(srv.ENV_EXPOSED_TOOLS, "read_file,no_such_tool_zzz")
        with pytest.raises(srv.ToolConfigError) as excinfo:
            srv.YunshuMCPHandler()
        assert "no_such_tool_zzz" in str(excinfo.value)

    def test_empty_list_rejected_at_construction(self, monkeypatch):
        """已设置但解析不出名字（如逗号）⇒ 同样失败，不静默退化成空清单"""
        monkeypatch.setenv(srv.ENV_EXPOSED_TOOLS, " , ")
        with pytest.raises(srv.ToolConfigError):
            srv.YunshuMCPHandler()

    def test_main_exits_nonzero_on_bad_tool_name(self, monkeypatch, capsys, _restore_root_logging):
        """启动即失败：退出码 2 + stderr 有说明 + stdout 一个字都没有"""
        monkeypatch.setenv(srv.ENV_EXPOSED_TOOLS, "read_file,no_such_tool_zzz")
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        code = srv.main()
        captured = capsys.readouterr()
        assert code == 2
        assert captured.out == "", f"启动失败时 stdout 不得有任何内容: {captured.out!r}"
        assert "no_such_tool_zzz" in captured.err


# ════════════════════════════════════════════════════════════
#  8. stdout 纯净化
# ════════════════════════════════════════════════════════════

class TestStdoutPurity:
    """stdout 只许出现 JSON-RPC 帧。"""

    def test_emit_writes_one_valid_json_line(self, monkeypatch):
        buffer = io.StringIO()
        monkeypatch.setattr(srv, "_ORIGINAL_STDOUT", buffer)
        srv._emit({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})

        written = buffer.getvalue()
        assert written.endswith("\n")
        lines = [line for line in written.splitlines() if line.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}

    def test_stdout_guard_redirects_stray_prints(self, monkeypatch, capsys):
        """守护期间 ``print`` 落到 stderr，真实 stdout 只剩 JSON 帧"""
        buffer = io.StringIO()
        monkeypatch.setattr(srv, "_ORIGINAL_STDOUT", buffer)
        with srv._stdout_guard():
            print("导入期模块往 stdout 打印的内容")
            srv._emit({"jsonrpc": "2.0", "id": 2, "result": {}})

        captured = capsys.readouterr()
        assert buffer.getvalue().strip() == '{"jsonrpc": "2.0", "id": 2, "result": {}}'
        assert "导入期模块往 stdout 打印的内容" not in buffer.getvalue()
        assert "导入期模块往 stdout 打印的内容" in captured.err

    def test_main_stdout_is_pure_json_frames(self, monkeypatch, capsys, _restore_root_logging, tmp_path):
        """整条 ``main()`` 主循环：喂三帧，真实 stdout 逐行都是合法 JSON"""
        target = tmp_path / "smoke.txt"
        target.write_text("main-smoke-content-XYZ", encoding="utf-8")
        frames = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "read_file", "arguments": {"path": str(target)}}}),
        ]) + "\n"
        monkeypatch.setattr(sys, "stdin", io.StringIO(frames))

        # 模拟"导入/运行期有别的模块往 stdout 打印"
        original_handle = srv.YunshuMCPHandler.handle_request

        def _noisy(self, request):
            print("污染输出：不应出现在 stdout")
            return original_handle(self, request)

        monkeypatch.setattr(srv.YunshuMCPHandler, "handle_request", _noisy)

        code = srv.main()
        captured = capsys.readouterr()

        assert code == 0
        lines = [line for line in captured.out.splitlines() if line.strip()]
        assert len(lines) == 3, f"应恰好三帧，实际: {captured.out!r}"
        parsed = [json.loads(line) for line in lines]  # 逐行都必须是合法 JSON
        assert [item["id"] for item in parsed] == [1, 2, 3]
        assert "污染输出" not in captured.out
        assert "污染输出" in captured.err
        # 第三帧读到了真实文件内容
        assert "main-smoke-content-XYZ" in parsed[2]["result"]["content"][0]["text"]
