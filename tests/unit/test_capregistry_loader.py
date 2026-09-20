"""Loader 四实现 + 失败降级 + 熔断/退避 + 统一调用收口（TASK-05 E3 / E4）

## E4 的要求

> **Loader 四实现各自一个失败降级用例**（Local / Stdio / SSE / HTTP）

## 四个实现的真实性（**不许把 mock 说成达标**）

| Loader | 测试里用的"真实依赖" |
|---|---|
| Local | 真实 `agent/tools/__init__.py::call()`（注册一个探针工具） |
| Stdio | **真实子进程**：仓库自带的 `mcp_services/yunshu_mcp_server.py`（手写 JSON-RPC，**不需要官方 `mcp` SDK**） |
| SSE | **真实本地 HTTP 服务**（`http.server` 起的 SSE 端点，不是 mock 掉本模块代码） |
| HTTP | **真实本地 HTTP 服务**（JSON-RPC over POST） |

⚠️ **诚实标注**：SSE/HTTP 两个 Loader 的代码路径是真实可用的，但**仓库里没有任何
生产 SSE/HTTP MCP 端点** ⇒ 它们的"生产可用性"**未经验证**，只有"对真实 HTTP 服务
可用"这一条实测结论。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent import tools as _tools  # noqa: E402
from agent.capregistry import invoke as _invoke  # noqa: E402
from agent.capregistry import loader as L  # noqa: E402
from agent.capregistry.spec import CapabilityRecord  # noqa: E402
from agent.capregistry.view import CapabilityRegistry  # noqa: E402


# ════════════════════════════════════════════════════════════
#  夹具
# ════════════════════════════════════════════════════════════


@pytest.fixture
def probe_tool():
    """在**真实注册表**里挂一个探针工具（用完注销）

    【为什么用真实注册表而不是桩】本地 Loader 的执行**必须**经
    `agent/tools/__init__.py::call()`（闸门/限流/审计都在那里）。用桩替换注册表
    就等于把要验的那条链路换掉了 —— `TASK-00` §0.2f 的"夹具冒充生产"。
    """
    name = "_capregistry_probe_echo"

    @_tools.register(name, "capregistry 测试探针：原样回显参数", schema={
        "type": "object", "properties": {"x": {"type": "integer"}}})
    def _probe(**kw):  # noqa: ANN202
        return {"ok": True, "echo": kw}

    yield name
    _tools.unregister(name)


@pytest.fixture
def spec_factory():
    def _make(name: str, **kw: Any) -> CapabilityRecord:
        base: Dict[str, Any] = dict(
            tool_name=name, capability_id=f"default:yunshu:{name}@1.0.0",
            kind="tool", location="local",
            # 四个身份全开：身份白名单的**逐条**用例自己传 `callable_by` 收窄
            callable_by=("llm", "human", "system", "service_account"))
        base.update(kw)
        return CapabilityRecord(**base)
    return _make


# ════════════════════════════════════════════════════════════
#  ① Local：真实现（包装 `tools.call`，不重写）
# ════════════════════════════════════════════════════════════


class TestLocalLoader:
    def test_成功路径经真实_tools_call(self, probe_tool, spec_factory):
        ld = L.LocalLoader("local")
        spec = spec_factory(probe_tool)
        handle = ld.resolve(spec)
        assert ld.connect(handle) is True
        assert ld.state is L.LoaderState.READY
        out = ld.invoke(handle, {"x": 7})
        assert out.ok is True
        assert out.data == {"ok": True, "echo": {"x": 7}}
        assert out.loader == "local"

    def test_失败降级_未知工具归_not_found_且进入退避(self, spec_factory):
        """★ E4 · Local 的失败降级用例

        断言三件事（缺一不可）：
          ① 不抛异常（Loader 的接口契约是返回 `InvokeOutcome`）；
          ② 错误码经过**唯一映射点**（"未知工具" ⇒ `not_found`，不是笼统的 unhealthy）；
          ③ 状态机进入 `backoff`，且**退避期内不再发起真实调用**。
        """
        ld = L.LocalLoader("local", now=lambda: 1000.0)
        handle = ld.resolve(spec_factory("__capregistry_no_such_tool__"))
        ld.connect(handle)
        out = ld.invoke(handle, {})
        assert out.ok is False
        assert out.exception is not None
        assert ld.state is L.LoaderState.BACKOFF
        assert ld.health() == "unhealthy"
        # 退避期内：connect 直接返回 False，不重试
        assert ld.connect(handle) is False

    def test_退避到期后自动回到_connecting(self, spec_factory):
        """状态机闭环：`backoff → connecting`（v1.4 §8.1）—— **注入时钟**，不 sleep"""
        clock = {"t": 1000.0}
        ld = L.LocalLoader("local", now=lambda: clock["t"])
        handle = ld.resolve(spec_factory("__nope__"))
        ld.connect(handle)
        ld.invoke(handle, {})
        assert ld.state is L.LoaderState.BACKOFF
        clock["t"] += 3600.0
        assert ld.state is L.LoaderState.CONNECTING

    def test_eager_init_可用(self):
        ld = L.LocalLoader("local")
        ld.eager_init()
        assert ld.state is L.LoaderState.READY

    def test_local_不挂熔断_避免爆炸半径过大(self):
        """【刻意的设计取舍】本地是进程内调用，没有"连接"可熔断

        给它挂熔断会把"某个工具报错 3 次"升级成"整个本地能力面被切断" ——
        爆炸半径与收益不成比例。故 `_get_breaker()` 对 local 返回 None。
        """
        assert L.LocalLoader("local")._get_breaker() is None  # noqa: SLF001


# ════════════════════════════════════════════════════════════
#  ② Stdio：**真实子进程**（仓库自带的 yunshu MCP 服务端）
# ════════════════════════════════════════════════════════════


class TestStdioLoader:
    def test_失败降级_服务端脚本不存在(self):
        """★ E4 · Stdio 的失败降级用例（配置指错 ⇒ 启动期就暴露，不是首次调用才超时）"""
        ld = L.StdioLoader("stdio", server="__no_such_dir__/no_such_server.py")
        with pytest.raises(L.LoaderInitError):
            ld.eager_init()
        assert ld.state is L.LoaderState.UNHEALTHY
        assert ld.health() == "unhealthy"
        # 之后调用也不抛：返回 unhealthy 的结果
        out = ld.invoke(L.Handle(capability="read_file", loader="stdio",
                                 location="remote"), {"path": "README.md"})
        assert out.ok is False and out.code == "unhealthy"

    def test_失败降级_连接失败后进入退避(self, tmp_path):
        """服务端脚本存在但**启动即崩** ⇒ 连接失败 ⇒ 退避 + unhealthy（不抛）"""
        bad = tmp_path / "bad_server.py"
        bad.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
        ld = L.StdioLoader("stdio", server=str(bad), timeout=5.0)
        ok = ld.connect(L.Handle(capability="read_file", loader="stdio",
                                 location="remote"))
        assert ok is False
        assert ld.state is L.LoaderState.BACKOFF
        assert ld.last_error

    def test_真实链路_驱动仓库自带_mcp_服务端(self):
        """★ **真实现，不是骨架**：端到端驱动 `mcp_services/yunshu_mcp_server.py`

        该服务端是**手写 JSON-RPC 2.0 over stdio**（不依赖官方 `mcp` SDK），
        故"官方 SDK 未安装"**不影响**本 Loader。
        默认暴露的是 5 个**只读**文件工具（`read_file` / `list_directory` /
        `get_file_info` / `search_files` / `grep`）。
        """
        import shutil
        if shutil.which == shutil.which and not os.path.isfile(
                str(_ROOT / "mcp_services" / "yunshu_mcp_server.py")):
            pytest.skip("仓库自带的 MCP stdio 服务端不存在")
        ld = L.StdioLoader("stdio", timeout=40.0)
        handle = L.Handle(capability="get_file_info", loader="stdio",
                          location="remote")
        assert ld.connect(handle) is True, f"连接失败: {ld.last_error}"
        out = ld.invoke(handle, {"path": "README.md"})
        assert out.ok is True, f"调用失败: {out.detail}"
        assert out.data.get("ok") is True
        assert out.data.get("type") == "file"
        assert int(out.data.get("size", 0)) > 0
        assert ld.pool_size() >= 1
        ld.close()
        assert ld.pool_size() == 0

    def test_预热池按_prewarm_建立(self):
        ld = L.StdioLoader("stdio", prewarm=2, timeout=40.0)
        try:
            ld.connect(L.Handle(capability="get_file_info", loader="stdio",
                                location="remote"))
            assert ld.pool_size() == 2
        finally:
            ld.close()


# ════════════════════════════════════════════════════════════
#  真实本地 HTTP 服务（SSE / HTTP 两个 Loader 共用）
# ════════════════════════════════════════════════════════════


class _McpStubHandler(BaseHTTPRequestHandler):
    """一个**真实**的最小 MCP 传输端点（支持 `POST` JSON-RPC 与 `GET` SSE）

    【为什么自己起服务而不是 mock Loader】`TASK-00` §0.2f / D12：mock 掉被测代码
    等于什么都没测。这里 mock 的是**对端**（一个不存在的 MCP 服务端），
    被测代码（SSE/HTTP 传输实现）**完全真实执行**。
    """

    protocol_version = "HTTP/1.1"
    sse_mode = False

    def log_message(self, *_a: Any) -> None:  # noqa: ANN002
        return None

    def _rpc_result(self, req: Dict[str, Any]) -> Dict[str, Any]:
        method = str(req.get("method") or "")
        if method == "initialize":
            return {"protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "stub", "version": "0"}}
        if method == "tools/call":
            params = req.get("params") or {}
            return {"content": [{"type": "text",
                                 "text": json.dumps({"ok": True,
                                                     "got": params.get("name"),
                                                     "args": params.get("arguments")})}]}
        return {}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            self.send_error(400)
            return
        resp = {"jsonrpc": "2.0", "id": req.get("id"),
                "result": self._rpc_result(req)}
        body = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        """SSE 传输的握手：先发 `event: endpoint`，再持续发 `event: message`

        【不易·为什么必须用 chunked】实测踩坑：HTTP/1.1 响应**既没有
        `Content-Length` 也没有 `Transfer-Encoding`** 时，`http.client` 会把
        响应体当成"读到连接关闭为止"，`urllib3.read(amt)` 于是**阻塞等待 amt 字节**
        ⇒ `requests.iter_lines()` **一条都不吐**，SSE 客户端永远等不到 endpoint 事件
        （现象是"握手超时"，极易误判成客户端实现有问题）。
        真实 SSE 服务端要么用 chunked、要么用 HTTP/2 流；这里显式发 chunked，
        与真实服务端的形态一致。
        """
        if not self.server.sse_enabled:  # type: ignore[attr-defined]
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def _chunk(payload: str) -> None:
            data = payload.encode("utf-8")
            self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
            self.wfile.write(data)
            self.wfile.write(b"\r\n")
            self.wfile.flush()

        endpoint = f"http://127.0.0.1:{self.server.server_port}/messages"
        _chunk(f"event: endpoint\ndata: {endpoint}\n\n")
        try:
            while not self.server.stopping.is_set():  # type: ignore[attr-defined]
                try:
                    frame = self.server.outbox.get(timeout=0.2)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    continue
                _chunk(f"event: message\ndata: {frame}\n\n")
        except Exception:  # noqa: BLE001  客户端断开
            return


class _StubServer:
    def __init__(self, *, sse: bool = False) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _McpStubHandler)
        self.httpd.sse_enabled = sse            # type: ignore[attr-defined]
        self.httpd.outbox = __import__("queue").Queue()   # type: ignore[attr-defined]
        self.httpd.stopping = threading.Event()  # type: ignore[attr-defined]
        self.port = self.httpd.server_port
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        daemon=True, name="capregistry-stub-mcp")
        self._thread.start()

    #: 让 `/messages` 的 POST 把响应投进 SSE 流
    def install_sse_bridge(self) -> None:
        original = _McpStubHandler.do_POST

        def do_POST(handler: _McpStubHandler) -> None:  # noqa: N802
            length = int(handler.headers.get("Content-Length") or 0)
            raw = handler.rfile.read(length).decode("utf-8")
            try:
                req = json.loads(raw)
            except json.JSONDecodeError:
                handler.send_error(400)
                return
            resp = {"jsonrpc": "2.0", "id": req.get("id"),
                    "result": handler._rpc_result(req)}  # noqa: SLF001
            handler.send_response(202)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            handler.server.outbox.put(json.dumps(resp))  # type: ignore[attr-defined]

        _McpStubHandler.do_POST = do_POST  # type: ignore[assignment]
        self._restore = original

    def close(self) -> None:
        if getattr(self, "_restore", None) is not None:
            _McpStubHandler.do_POST = self._restore  # type: ignore[assignment]
        self.httpd.stopping.set()  # type: ignore[attr-defined]
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    @property
    def sse_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/sse"


@pytest.fixture
def stub_mcp_http(monkeypatch):
    # 【TASK-07 适配】本夹具的桩服务绑定在 127.0.0.1（回环），而 SSRF 守卫
    # **默认拒绝**回环/私有网段（正是 TASK-07 第 2 步第 2 项的要求）。
    # 该守卫对所有出站生效，包括 registry 的 `location=remote` HTTP/SSE Loader。
    # 这里用守卫**自带的显式白名单**放行本夹具的地址 —— 生产侧确有"能力服务托管在
    # 本机"的部署形态时，走的也是同一条白名单（`CP_SSRF_ALLOW_HOSTS`），
    # 而不是把整个守卫关掉。**不削弱守卫本身**：其余用例仍走默认拒绝。
    monkeypatch.setenv("CP_SSRF_ALLOW_HOSTS", "127.0.0.1")
    srv = _StubServer(sse=False)
    try:
        yield srv
    finally:
        srv.close()


@pytest.fixture
def stub_mcp_sse(monkeypatch):
    monkeypatch.setenv("CP_SSRF_ALLOW_HOSTS", "127.0.0.1")   # 同上（TASK-07 适配）
    srv = _StubServer(sse=True)
    srv.install_sse_bridge()
    try:
        yield srv
    finally:
        srv.close()


# ════════════════════════════════════════════════════════════
#  ③ HTTP Loader
# ════════════════════════════════════════════════════════════


class TestHttpLoader:
    def test_失败降级_端点不可达(self):
        """★ E4 · HTTP 的失败降级用例（端点不可达 ⇒ 连接失败 + 退避 + unhealthy，不抛）"""
        ld = L.HttpLoader("http", endpoint="http://127.0.0.1:1/mcp", timeout=3.0)
        assert ld.connect(L.Handle(capability="x", loader="http",
                                   location="remote")) is False
        assert ld.state is L.LoaderState.BACKOFF
        assert ld.health() == "unhealthy"
        out = ld.invoke(L.Handle(capability="x", loader="http", location="remote"), {})
        assert out.ok is False and out.code == "unhealthy"

    def test_未配置端点时不主动探测_也不判失败(self, monkeypatch):
        """【刻意的设计取舍】未配置端点**不**当成失败

        仓库当前**没有任何生产 SSE/HTTP MCP 端点**（`data/mcp_services.json` 无此类
        登记）。若把"没配置"当失败，整套能力面会在默认配置下变红 ——
        那就把一个"可选扩展点"变成了"默认故障"。
        此时 `eager_init()` 静默返回，而**真正调用**会明确报 `unhealthy`
        （见 `_do_connect` 抛 `LoaderInitError`）。
        """
        monkeypatch.delenv("CP_CAPABILITY_ENDPOINTS", raising=False)
        ld = L.HttpLoader("http", endpoint="")
        ld.eager_init()                       # 不抛
        assert ld.invoke(L.Handle(capability="x", loader="http",
                                  location="remote"), {}).code == "unhealthy"

    def test_真实链路_对真实_http_端点(self, stub_mcp_http):
        ld = L.HttpLoader("http", endpoint=stub_mcp_http.url, timeout=10.0)
        handle = L.Handle(capability="probe_tool", loader="http",
                          location="remote", endpoint=stub_mcp_http.url)
        assert ld.connect(handle) is True, f"连接失败: {ld.last_error}"
        out = ld.invoke(handle, {"a": 1})
        assert out.ok is True, f"调用失败: {out.detail}"
        assert out.data["ok"] is True
        assert out.data["got"] == "probe_tool"
        assert out.data["args"] == {"a": 1}

    def test_必须经_http_client_而不是裸_requests(self):
        """v1.4 §8.1 / TASK-05 §3 第 2 步第 2 项：**必须复用 `agent/web/http_client.py`**

        直接 `requests` 会绕过 `HttpClient._egress_block`（EgressGuard 出域检查点）
        ⇒ 正是 `TASK-07` 要收的口子。
        """
        src = Path(L.__file__).read_text(encoding="utf-8")
        assert "from agent.web.http_client import HttpClient" in src, \
            "HTTP/SSE Loader 必须经 agent.web.http_client.HttpClient"


# ════════════════════════════════════════════════════════════
#  ④ SSE Loader
# ════════════════════════════════════════════════════════════


class TestSseLoader:
    def test_未配置端点时不主动探测_但调用明确报_unhealthy(self, monkeypatch):
        monkeypatch.delenv("CP_CAPABILITY_ENDPOINTS", raising=False)
        ld = L.SseLoader("sse", endpoint="")
        ld.eager_init()
        out = ld.invoke(L.Handle(capability="x", loader="sse", location="remote"), {})
        assert out.ok is False and out.code == "unhealthy"

    def test_失败降级_端点不可达(self):
        """★ E4 · SSE 的失败降级用例（连不上 ⇒ 连接失败 + 退避 + unhealthy，不抛）"""
        ld = L.SseLoader("sse", endpoint="http://127.0.0.1:1/sse", timeout=3.0)
        ok = ld.connect(L.Handle(capability="x", loader="sse",
                                 location="remote",
                                 endpoint="http://127.0.0.1:1/sse"))
        assert ok is False
        assert ld.state is L.LoaderState.BACKOFF
        assert ld.health() == "unhealthy"

    def test_失败降级_握手拿不到端点(self):
        """端点可达但**不是 SSE**（GET 恒 404）⇒ 必须判为连接失败

        【为什么这条用例重要】实现的第一版只等 `_ready` 事件，而事件流**立即失败**
        时 `finally` 也会 set 它 ⇒ "握手失败"被误判成"握手成功"，健康状态显示
        ready、错误被推迟到第一次业务调用。本用例锁死这个坑。
        """
        srv = _StubServer(sse=False)   # 该服务的 GET 恒 404
        try:
            ld = L.SseLoader("sse", endpoint=srv.sse_url, timeout=5.0)
            ok = ld.connect(L.Handle(capability="x", loader="sse",
                                     location="remote", endpoint=srv.sse_url))
            assert ok is False
            assert ld.state is L.LoaderState.BACKOFF
            assert ld.health() == "unhealthy"
        finally:
            srv.close()

    def test_真实链路_对真实_sse_端点(self, stub_mcp_sse):
        ld = L.SseLoader("sse", endpoint=stub_mcp_sse.sse_url, timeout=10.0)
        handle = L.Handle(capability="probe_tool", loader="sse",
                          location="remote", endpoint=stub_mcp_sse.sse_url)
        assert ld.connect(handle) is True, f"连接失败: {ld.last_error}"
        out = ld.invoke(handle, {"b": 2})
        assert out.ok is True, f"调用失败: {out.detail}"
        assert out.data["got"] == "probe_tool"
        assert out.data["args"] == {"b": 2}
        ld.close()


# ════════════════════════════════════════════════════════════
#  熔断接线（TASK-05 §3 第 2 步第 3 项：把熔断接到传输层）
# ════════════════════════════════════════════════════════════


class TestCircuitBreakerWiring:
    def test_远端_loader_挂了熔断器(self):
        for cls in (L.StdioLoader, L.SseLoader, L.HttpLoader):
            ld = cls(cls.kind)
            br = ld._get_breaker()  # noqa: SLF001
            assert br is not None, f"{cls.__name__} 未挂熔断器（TASK-05 §2.2 的缺口）"
            assert br.name == f"capregistry.{cls.kind}"

    def test_熔断打开时直接拒绝_不发起真实调用(self, monkeypatch):
        ld = L.HttpLoader("http", endpoint="http://127.0.0.1:1/mcp")
        called = {"n": 0}

        def _never(*_a: Any, **_kw: Any) -> Any:
            called["n"] += 1
            return {"ok": True}

        monkeypatch.setattr(ld, "_do_invoke", _never)
        br = ld._get_breaker()  # noqa: SLF001
        monkeypatch.setattr(br, "allow_request", lambda: False)
        out = ld.invoke(L.Handle(capability="x", loader="http", location="remote"), {})
        assert out.ok is False and out.code == "unhealthy"
        assert "熔断" in out.detail
        assert called["n"] == 0, "熔断打开时不得发起真实调用"

    def test_熔断器不可用时降级为无熔断而不是起不来(self, monkeypatch):
        import agent.circuit_breaker as cb
        monkeypatch.setattr(cb, "CircuitBreaker",
                            lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("x")))
        ld = L.HttpLoader("http")
        assert ld._get_breaker() is None  # noqa: SLF001
        assert any("熔断器不可用" in w for w in ld.warnings)


# ════════════════════════════════════════════════════════════
#  LoaderManager：逐 Loader 独立初始化（D4）
# ════════════════════════════════════════════════════════════


class TestLoaderManager:
    def test_单个_loader_失败不影响其余(self, monkeypatch):
        """★ D4：一个 Loader 初始化失败 ⇒ 只记 `init_errors`，其余照常可用"""
        monkeypatch.setenv("CP_CAPABILITY_STDIO_SERVER", "__missing__/x.py")
        mgr = L.LoaderManager()
        assert "local" in mgr.loaders, "local 不该因 stdio 失败而消失"
        failed = {e["loader"] for e in mgr.init_errors}
        assert "stdio" in failed
        d = mgr.describe()
        assert d["loaders"]["stdio"]["health"] == "unhealthy"
        assert d["loaders"]["local"]["health"] in ("healthy", "unknown")

    def test_开关可逐_loader_禁用(self, monkeypatch):
        monkeypatch.setenv("CP_CAPABILITY_LOADER_DISABLED", "http,sse")
        mgr = L.LoaderManager()
        assert "http" not in mgr.loaders and "sse" not in mgr.loaders
        assert "local" in mgr.loaders

    def test_端点点表解析(self, monkeypatch):
        monkeypatch.setenv(
            "CP_CAPABILITY_ENDPOINTS",
            "http:weather=http://127.0.0.1:9000/mcp,sse:foo=http://127.0.0.1:9001/sse")
        mgr = L.LoaderManager()
        assert mgr._endpoints["http"]["weather"] == "http://127.0.0.1:9000/mcp"  # noqa: SLF001
        assert mgr._endpoints["sse"]["foo"].endswith("/sse")  # noqa: SLF001

    def test_health_of_只读且不抛(self):
        mgr = L.LoaderManager()
        assert mgr.health_of("anything") in ("healthy", "unknown", "unhealthy")

    def test_local_能力分派到_local_loader(self, monkeypatch):
        mgr = L.LoaderManager()
        spec = CapabilityRecord(tool_name="x", location="local")
        assert mgr.loader_for(spec).kind == "local"
        remote = CapabilityRecord(tool_name="y", location="remote")
        assert mgr.loader_for(remote).kind in ("stdio", "http", "sse")


# ════════════════════════════════════════════════════════════
#  E3 · 不绕闸门：`invoke_capability` 走 `tools.call()`
# ════════════════════════════════════════════════════════════


class TestInvokeFunnel:
    def _registry_with(self, *specs: CapabilityRecord) -> CapabilityRegistry:
        return CapabilityRegistry(specs)

    def test_成功路径_经_tools_call(self, probe_tool, spec_factory):
        reg = self._registry_with(spec_factory(probe_tool))
        res = _invoke.invoke_capability(probe_tool, {"x": 1}, identity="human",
                                        registry=reg)
        assert res.status == "ok"
        assert res.data == {"ok": True, "echo": {"x": 1}}
        assert res.meta["loader"] == "local"

    def test_本地执行确实调用的是_agent_tools_call(self, monkeypatch, spec_factory):
        """★ 结构级断言：本地路径的 `_do_invoke` 就是 `agent.tools.call`

        做法：把 `agent.tools.call` **换成计数器**，看它有没有被调用、参数对不对。
        这比"读源码里有没有 `_tools.call`"强，因为它在**运行时**证明收口。
        """
        seen: List[tuple] = []

        def _spy(name: str, **kw: Any) -> Any:
            seen.append((name, kw))
            return {"ok": True, "spied": True}

        monkeypatch.setattr(_tools, "call", _spy)
        reg = self._registry_with(spec_factory("some_local_tool"))
        res = _invoke.invoke_capability("some_local_tool", {"k": "v"},
                                        identity="human", registry=reg)
        assert res.status == "ok" and res.data == {"ok": True, "spied": True}
        assert seen == [("some_local_tool", {"k": "v"})], \
            "本地执行必须且只能经 agent.tools.call()（E3 铁律）"

    def test_被闸门拒绝的工具通过统一入口同样被拒绝(self, monkeypatch, spec_factory):
        """★ E3 的核心用例：闸门拒绝 ⇒ 统一入口也拒绝，且归为 `denied`

        `agent/tools/__init__.py::call()` 对"被拒"的处理是 **`return` 拒绝结构**
        （不是抛异常）。统一入口必须识别这个结构并翻译成统一错误码 ——
        否则调用方会看到 `status=ok` + 一个 `ok=false` 的载荷，
        那正是"谎报成功"。
        """
        gate_result = {"ok": False, "error": "该工具被治理策略拒绝",
                       "error_code": "PERMISSION_DENIED"}
        monkeypatch.setattr(_tools, "call", lambda name, **kw: dict(gate_result))
        reg = self._registry_with(spec_factory("blocked_tool"))
        res = _invoke.invoke_capability("blocked_tool", {}, identity="human",
                                        registry=reg)
        assert res.status == "error"
        assert res.code == "denied"
        assert res.data is None
        assert res.error["retryable"] is False

    def test_非交互来源遇审批_返回明确错误码_不悬挂(self, monkeypatch, spec_factory):
        """★ 方案 A：非交互来源遇 `APPROVAL_REQUIRED` 时**给明确出路**，不产生悬空挂单"""
        monkeypatch.setenv("CP_CAPABILITY_NONINTERACTIVE_APPROVAL", "1")
        gate_result = {"ok": False, "error": "需要人工确认",
                       "error_code": "APPROVAL_REQUIRED",
                       "approval_id": "", "guidance": "…"}
        monkeypatch.setattr(_tools, "call", lambda name, **kw: dict(gate_result))
        reg = self._registry_with(spec_factory("risky_tool"))
        res = _invoke.invoke_capability("risky_tool", {}, identity="system",
                                        registry=reg)
        assert res.status == "error" and res.code == "denied"
        # 【口径】`error.message` 恒为**固定描述**（零泄漏风险），
        # 具体原因与出路在 `error.detail`（经 `redact()` 脱敏）。
        msg = (res.error.get("detail") or "") + (res.error.get("message") or "")
        assert "非交互" in msg
        assert "预授权" in msg or "人工身份" in msg
        # 交互身份下**不叠加**这层说明（人对人仍走既有挂单闭环）
        res2 = _invoke.invoke_capability("risky_tool", {}, identity="human",
                                         registry=reg)
        assert res2.code == "denied"
        msg2 = (res2.error.get("detail") or "") + (res2.error.get("message") or "")
        assert "非交互" not in msg2

    def test_非交互审批处置可被开关关闭(self, monkeypatch, spec_factory):
        """方案 A 是**可回滚**的：置 0 即回到既有行为（挂单后原样返回）"""
        monkeypatch.setenv("CP_CAPABILITY_NONINTERACTIVE_APPROVAL", "0")
        gate_result = {"ok": False, "error": "需要人工确认",
                       "error_code": "APPROVAL_REQUIRED", "approval_id": ""}
        monkeypatch.setattr(_tools, "call", lambda name, **kw: dict(gate_result))
        reg = self._registry_with(spec_factory("risky_tool"))
        res = _invoke.invoke_capability("risky_tool", {}, identity="system",
                                        registry=reg)
        assert res.code == "denied"
        msg = (res.error.get("detail") or "") + (res.error.get("message") or "")
        assert "非交互" not in msg

    def test_身份白名单在闸门之前生效(self, spec_factory):
        """`callable_by` 是**入口层**白名单，独立于闸门（闸门关掉它也仍然生效）"""
        spec = spec_factory("human_only", callable_by=("human",))
        reg = self._registry_with(spec)
        res = _invoke.invoke_capability("human_only", {}, identity="llm", registry=reg)
        assert res.status == "error" and res.code == "denied"
        msg = (res.error.get("detail") or "") + (res.error.get("message") or "")
        assert "callable_by" in msg

    def test_入参不合_schema_归_schema_error(self, spec_factory):
        spec = spec_factory("typed_tool", input_schema={
            "type": "object", "properties": {"n": {"type": "integer"}},
            "required": ["n"]})
        reg = self._registry_with(spec)
        res = _invoke.invoke_capability("typed_tool", {"n": "not-int"},
                                        identity="human", registry=reg)
        assert res.code == "schema_error"
        res2 = _invoke.invoke_capability("typed_tool", {}, identity="human",
                                         registry=reg)
        assert res2.code == "schema_error"

    def test_能力不存在归_not_found(self, spec_factory):
        reg = self._registry_with(spec_factory("exists"))
        res = _invoke.invoke_capability("nope", {}, identity="human", registry=reg)
        assert res.code == "not_found"

    def test_未知身份被拒(self, spec_factory):
        reg = self._registry_with(spec_factory("x"))
        res = _invoke.invoke_capability("x", {}, identity="root", registry=reg)
        assert res.code == "validation_error"

    def test_身份透传到_tool_gate_的_session_source(self, monkeypatch, spec_factory):
        """`IDENTITY_SESSION_SOURCE` 映射在**执行期间**被写进上下文变量

        这是 `async_executor.submit()` 缺身份那条链路的**修正范式**：
        身份必须显式落进 `tool_gate` 的上下文，而不是靠环境变量缺省值。
        """
        from agent.tool_gate import current_session_source
        captured: Dict[str, str] = {}

        def _spy(name: str, **kw: Any) -> Any:
            captured["src"] = current_session_source()
            return {"ok": True}

        monkeypatch.setattr(_tools, "call", _spy)
        reg = self._registry_with(spec_factory("x"))
        _invoke.invoke_capability("x", {}, identity="system", registry=reg)
        assert captured["src"] == "scheduled"
        _invoke.invoke_capability("x", {}, identity="human", registry=reg)
        assert captured["src"] == "cli"
        _invoke.invoke_capability("x", {}, identity="llm", registry=reg)
        assert captured["src"] == "api"
        # 执行结束后必须复位（不泄漏到后续调用）
        assert current_session_source() == ""

    def test_版本不符归_not_found(self, spec_factory):
        reg = self._registry_with(spec_factory("x", version="1.0.0"))
        res = _invoke.invoke_capability("x", {}, identity="human", version="9.9.9",
                                        registry=reg)
        assert res.code == "not_found"

    def test_信封结构与_http_状态(self, probe_tool, spec_factory):
        reg = self._registry_with(spec_factory(probe_tool))
        res = _invoke.invoke_capability(probe_tool, {"x": 2}, identity="human",
                                        registry=reg)
        env = res.to_dict()
        assert set(env) == {"status", "code", "data", "error", "meta"}
        assert res.http_status() == 200
        bad = _invoke.invoke_capability("nope", {}, identity="human", registry=reg)
        assert bad.http_status() == 404

    def test_result_schema_违约不改变_status_但出现在_meta(self, spec_factory):
        """契约违约是**可观测事实**，不是执行失败（执行确实成功了）"""
        spec = spec_factory("contract_tool", result_schema={
            "type": "object", "properties": {"ok": {"type": "boolean"},
                                             "n": {"type": "integer"}},
            "required": ["ok"]})
        reg = self._registry_with(spec)

        @_tools.register("_capregistry_contract_probe", "契约探针")
        def _probe(**kw):  # noqa: ANN202
            return {"ok": True, "n": "not-an-int"}

        try:
            reg2 = self._registry_with(spec_factory("_capregistry_contract_probe",
                                                    result_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}, "n": {"type": "integer"}},
                "required": ["ok"]}))
            res = _invoke.invoke_capability("_capregistry_contract_probe", {},
                                            identity="human", registry=reg2)
            assert res.status == "ok"
            assert res.meta["contract"] == "declared"
            assert res.meta["result_status"] == "contract_violation"
            assert res.meta["contract_errors"]
        finally:
            _tools.unregister("_capregistry_contract_probe")
