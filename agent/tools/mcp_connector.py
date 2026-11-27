"""MCP 连接管理器 — 连接 MCP 服务并自动注册其工具到全局注册表

支持两种传输模式：
1. STDIO: 通过子进程启动 MCP 服务（使用 mcp_client.py 的 MCPClient）
2. HTTP: 连接远程 HTTP MCP 服务
"""
import asyncio
import json
import logging
import os
import time
import urllib.request
import urllib.parse
from typing import Any

from agent import tools as _tools


def _ssrf_block_reason(url: str, *, surface: str = "") -> str:
    """MCP 出站目标判定：放行返回 None，拒绝返回原因（**fail-closed**）

    【为什么 MCP 不能只靠"配置写入时校验过"】`agent/network_config.py:validate_mcp_service`
    已在**写入配置**时判过一次（TASK-07 补），但配置可在运行期被 `connect_mcp` /
    扩展安装流程改写，且 `_make_http_handler` 是**每次调用**都会真正发包的执行体。
    "校验点"与"使用点"之间只要有可写窗口，就必须在使用点再判一次 ——
    调用的是**同一个** `ssrf_guard.check_url`，不新增第二份判定口径（D1）。
    """
    try:
        from agent.guardrails import ssrf_guard
    except Exception as exc:  # noqa: BLE001  守卫不可用
        if str(os.environ.get("CP_SSRF_GUARD", "1")).strip().lower() \
                in ("0", "false", "no", "off"):
            return None
        return f"SSRF 守卫不可用（{type(exc).__name__}）⇒ 按 fail-closed 拒绝"
    verdict = ssrf_guard.check_url(url)
    if verdict.allowed:
        return None
    ssrf_guard.audit_block(verdict, url=url, surface=surface or "tools.mcp_connector")
    return verdict.reason


logger = logging.getLogger(__name__)

#: HTTP JSON-RPC 单次调用超时（秒）。与 tools/call 的 30s 分开：resources/prompts
#: 是只读列举/读取，卡住 30s 会把工具循环拖死；10s 足够本地/局域网的 MCP 服务。
_HTTP_RPC_TIMEOUT_SEC = 10


def _resource_field(item: Any, *names: str) -> Any:
    """从 MCP 资源条目取字段（兼容 dataclass 与 dict 两种形状）

    【为什么需要】MCPClient.list_resources 返回 `MCPResource`，而 HTTP 传输
    直接给 JSON dict；两端口径必须一致（否则 stdio 的 uri/name 会静默变空串）。
    """
    for name in names:
        if isinstance(item, dict) and name in item:
            return item.get(name)
        value = getattr(item, name, None)
        if value is not None:
            return value
    return ""


class McpConnector:
    """MCP 连接管理器 — 管理 MCP 服务的连接、工具注册和生命周期"""

    def __init__(self):
        # {service_id: {"client": MCPClient, "tools": [str], "transport": str, "connected_at": float}}
        self._connections: dict[str, dict] = {}
        # 单线程事件循环（用于异步 MCP 调用）
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """获取或创建事件循环"""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop

    # ── STDIO MCP 连接 ──

    def connect_stdio(self, service_id: str, name: str,
                      command: str, args: list[str] | None = None,
                      env: dict[str, str] | None = None) -> dict:
        """连接 STDIO 模式的 MCP 服务

        Args:
            service_id: 服务唯一标识
            name: 服务显示名称
            command: 启动命令（如 "python", "node"）
            args: 命令参数（如 ["mcp_server.py"]）
            env: 环境变量

        Returns:
            {"ok": bool, "message": str, "tools": int}
        """
        loop = self._get_loop()
        try:
            from mcp_services.mcp_client import MCPClient, MCPConfig
            config = MCPConfig(timeout=30, max_retries=2)
            client = MCPClient(command, args or [], env or {}, config=config)
            loop.run_until_complete(client.start())
            loop.run_until_complete(client.initialize())

            # 注册工具
            count = self._register_mcp_tools(service_id, client, transport="stdio")

            self._connections[service_id] = {
                "client": client,
                "tool_names": [t.name for t in client.tools],
                "transport": "stdio",
                "name": name,
                "connected_at": time.time(),
            }
            logger.info(f"[MCP] STDIO 连接成功: {service_id} ({name}), {count} 个工具")
            return {"ok": True, "message": f"已连接 {name}，注册 {count} 个工具", "tools": count}

        except Exception as e:
            logger.error(f"[MCP] STDIO 连接失败: {service_id}: {e}")
            return {"ok": False, "error": str(e)}

    # ── HTTP MCP 连接 ──

    def connect_http(self, service_id: str, name: str,
                     address: str, port: int = 8080) -> dict:
        """连接 HTTP 模式的 MCP 服务

        通过 HTTP JSON-RPC 协议与 MCP 服务通信。

        Args:
            service_id: 服务唯一标识
            name: 服务显示名称
            address: 主机地址
            port: 端口

        Returns:
            {"ok": bool, "message": str, "tools": int}
        """
        base_url = f"http://{address}:{port}"
        try:
            # E4-3：先 initialize 并把服务端 `capabilities` **读回**。
            # 【为什么必须先做】没有这份读回，"服务器不支持 resources/prompts" 会被
            # 误报成"空列表"（两者对运维的含义完全不同）；而 initialize 失败不该
            # 阻断工具面 ⇒ 按"未声明"处理（见 `_http_initialize`）。
            capabilities = self._http_initialize(base_url)
            # 获取工具列表（JSON-RPC 请求）
            tools_data = self._http_list_tools(base_url)
            if tools_data is None:
                return {"ok": False, "error": f"无法连接到 {base_url}"}

            # 注册工具到全局注册表
            count = 0
            tool_names = []
            for t in tools_data:
                t_name = t.get("name", "")
                t_desc = t.get("description", "")
                t_schema = t.get("inputSchema", {})

                # 创建 HTTP 转发的 handler
                handler = self._make_http_handler(base_url, t_name)

                final_name = _tools.register_dynamic(
                    t_name, t_desc,
                    handler=handler,
                    schema=t_schema,
                    source="mcp",
                    source_id=service_id,
                )
                # register_dynamic 返回的是 handler，我们需要实际注册的名称
                # 通过 registry 中查找真实名称
                tool_names.append(t_name)
                count += 1

            self._connections[service_id] = {
                "client": None,
                "tool_names": tool_names,
                "transport": "http",
                "base_url": base_url,
                "name": name,
                "capabilities": capabilities,
                "connected_at": time.time(),
            }
            logger.info(f"[MCP] HTTP 连接成功: {service_id} ({name} @ {base_url}), {count} 个工具")
            return {"ok": True, "message": f"已连接 {name}，注册 {count} 个工具", "tools": count}

        except Exception as e:
            logger.error(f"[MCP] HTTP 连接失败: {service_id} @ {address}:{port}: {e}")
            return {"ok": False, "error": str(e)}

    def _http_list_tools(self, base_url: str) -> list[dict] | None:
        """通过 HTTP JSON-RPC 获取工具列表"""
        _blocked = _ssrf_block_reason(base_url, surface="mcp_connector.list_tools")
        if _blocked is not None:
            logger.warning("[MCP] 目标未通过出站判定，拒绝连接: %s（%s）", base_url, _blocked)
            return None
        try:
            req_data = json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method": "tools/list", "params": {},
            }).encode("utf-8")
            req = urllib.request.Request(
                base_url + "/mcp",
                data=req_data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            if "error" in result:
                logger.warning(f"[MCP] HTTP list_tools 错误: {result['error']}")
                return None
            return result.get("result", {}).get("tools", [])
        except Exception as e:
            logger.warning(f"[MCP] HTTP 请求失败 ({base_url}): {e}")
            return None

    def _http_initialize(self, base_url: str) -> dict:
        """HTTP 传输的 initialize：只取服务端 `capabilities`

        失败 ⇒ 返回空表（按"未声明任何能力"处理）而不是抛 —— 旧服务的工具面
        不该因为不支持 `resources/prompts` 就整体不可用（D2 向后兼容）。
        """
        rpc = self._http_rpc(base_url, "initialize", {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "cloud-yunshu-mcp-client", "version": "1.1.0"},
            "capabilities": {},
        })
        if not rpc.get("ok"):
            logger.warning("[MCP] HTTP initialize 未取到能力声明（按未声明处理）")
            return {}
        caps = rpc.get("result", {}).get("capabilities")
        return dict(caps) if isinstance(caps, dict) else {}

    def _http_rpc(self, base_url: str, method: str,
                  params: dict | None = None) -> dict:
        """HTTP JSON-RPC 单次调用（**每次调用都复检出站目标**）

        Returns:
            {"ok": True, "result": {...}} 或 {"ok": False, "code": <稳定码>}
            稳定码只用 no_connection / bad_request：前者是传输层失败（连不上/超时/
            守卫拒绝），后者是服务器明确回了 JSON-RPC error。**服务器原文不回显**
            （可能含租户/凭据片段），只进服务日志的类型名。
        """
        _blocked = _ssrf_block_reason(base_url, surface=f"mcp_connector.{method}")
        if _blocked is not None:
            logger.warning("[MCP] 目标未通过出站判定，拒绝调用: %s（%s）", method, _blocked)
            return {"ok": False, "code": "no_connection",
                    "error": "MCP 目标未通过出站判定（详见服务日志）"}
        try:
            req_data = json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params or {},
            }).encode("utf-8")
            req = urllib.request.Request(
                base_url + "/mcp",
                data=req_data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=_HTTP_RPC_TIMEOUT_SEC) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 连接类失败统一转稳定码
            logger.warning("[MCP] %s 调用失败（%s）", method, type(exc).__name__)
            return {"ok": False, "code": "no_connection",
                    "error": "MCP 服务器调用失败（详见服务日志）"}
        if not isinstance(payload, dict):
            return {"ok": False, "code": "bad_request",
                    "error": "MCP 服务器响应格式不可识别（详见服务日志）"}
        if "error" in payload:
            # 原文不回显：JSON-RPC error 里可能带 URI/租户片段
            logger.warning("[MCP] %s 返回 JSON-RPC error（原文不回显）", method)
            return {"ok": False, "code": "bad_request",
                    "error": "MCP 服务器拒绝了该请求（详见服务日志）"}
        result = payload.get("result")
        return {"ok": True, "result": result if isinstance(result, dict) else {}}

    def _make_http_handler(self, base_url: str, tool_name: str):
        """创建 HTTP 转发的工具处理函数

        【TASK-07 第 2 步第 7 项】本 handler **每次调用都复检目标地址**：
        它是 `location=remote` 的 MCP 工具的执行体，参数由 LLM 给（`kwargs`），
        是真实的出站路径。目标虽说来自配置，但"接入即裸出口"正是 TASK-07 §2.1
        点名的 MCP 缺陷；且配置可在运行期被改写（`connect_mcp` 属治理动作），
        故**不能只在写入配置时判一次**。
        """
        def _handler(**kwargs):
            _blocked = _ssrf_block_reason(base_url,
                                          surface=f"mcp_connector.call:{tool_name}")
            if _blocked is not None:
                return {"ok": False, "blocked": True, "blocked_by": "guardrails.ssrf_guard",
                        "error": f"MCP 目标未通过出站判定：{_blocked}"}
            try:
                req_data = json.dumps({
                    "jsonrpc": "2.0", "id": 1,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": kwargs},
                }).encode("utf-8")
                req = urllib.request.Request(
                    base_url + "/mcp",
                    data=req_data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                if "error" in result:
                    return {"ok": False, "error": str(result["error"])}
                content = result.get("result", {}).get("content", [])
                if content and content[0]["type"] == "text":
                    try:
                        return json.loads(content[0]["text"])
                    except json.JSONDecodeError:
                        return {"ok": True, "data": content[0]["text"]}
                return {"ok": True, "data": result.get("result", {})}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        return _handler

    # ── 工具注册 ──

    def _register_mcp_tools(self, service_id: str, client, transport: str) -> int:
        """将 MCP 服务的工具注册到全局注册表"""
        count = 0
        for tool in client.tools:
            handler = self._make_stdio_handler(client, tool.name)
            _tools.register_dynamic(
                tool.name, tool.description,
                handler=handler,
                schema=tool.input_schema,
                source="mcp",
                source_id=service_id,
            )
            count += 1
        return count

    def _make_stdio_handler(self, client, tool_name: str):
        """创建 STDIO 转发的工具处理函数"""
        def _handler(**kwargs):
            loop = self._get_loop()
            try:
                result = loop.run_until_complete(
                    client.call_tool(tool_name, kwargs)
                )
                return result
            except Exception as e:
                return {"ok": False, "error": str(e)}
        return _handler

    # ── 断开连接 ──

    def disconnect(self, service_id: str) -> dict:
        """断开 MCP 服务连接并注销其工具

        Args:
            service_id: 服务标识

        Returns:
            {"ok": bool, "message": str}
        """
        conn = self._connections.pop(service_id, None)
        if not conn:
            return {"ok": False, "error": f"连接不存在: {service_id}"}

        # 注销工具
        try:
            removed = _tools.unregister_by_source(source="mcp", source_id=service_id)
        except Exception as e:
            removed = 0
            logger.warning(f"[MCP] 注销工具失败: {e}")

        # 停止 MCP 客户端（STDIO 模式）
        if conn.get("client"):
            try:
                loop = self._get_loop()
                loop.run_until_complete(conn["client"].stop())
            except Exception as e:
                logger.warning(f"[MCP] 停止客户端失败: {e}")

        logger.info(f"[MCP] 已断开: {service_id}, 注销 {removed} 个工具")
        return {"ok": True, "message": f"已断开 {conn.get('name', service_id)}，注销 {removed} 个工具"}

    def disconnect_all(self) -> int:
        """断开所有 MCP 连接

        Returns:
            断开连接数
        """
        count = 0
        for sid in list(self._connections.keys()):
            self.disconnect(sid)
            count += 1
        logger.info(f"[MCP] 已断开所有连接: {count} 个")
        return count

    def list_connections(self) -> list[dict]:
        """列出所有活跃的 MCP 连接

        Returns:
            [{"id": str, "name": str, "transport": str, "tools": int, "connected_at": float}]
        """
        return [
            {
                "id": sid,
                "name": conn.get("name", sid),
                "transport": conn.get("transport", "?"),
                "tools": len(conn.get("tool_names", [])),
                "tool_names": conn.get("tool_names", []),
                "connected_at": conn.get("connected_at", 0),
            }
            for sid, conn in self._connections.items()
        ]

    def get_connection(self, service_id: str) -> dict | None:
        """获取指定连接的详情"""
        conn = self._connections.get(service_id)
        if not conn:
            return None
        return {
            "id": service_id,
            "name": conn.get("name", service_id),
            "transport": conn.get("transport", "?"),
            "tools": len(conn.get("tool_names", [])),
            "tool_names": conn.get("tool_names", []),
            "connected_at": conn.get("connected_at", 0),
        }

    # ── E4-3：resources / prompts（只读；不 subscribe、不写资源）──────────

    def capabilities_of(self, service_id: str) -> dict:
        """取连接协商到的服务端能力声明（stdio 取客户端读回值，HTTP 取连接登记值）"""
        conn = self._connections.get(service_id)
        if not conn:
            return {}
        # stdio：客户端是**唯一事实源**（initialize 读回后一直挂在 client 上）；
        # http：连接登记 initialize 的读回值（没有可用的 client 对象）。
        client = conn.get("client")
        if client is not None:
            caps = getattr(client, "capabilities", None)
            return dict(caps) if isinstance(caps, dict) else {}
        caps = conn.get("capabilities")
        return dict(caps) if isinstance(caps, dict) else {}

    def resource_action(self, service_id: str, action: str, *,
                        uri: str = "", name: str = "",
                        arguments: dict | None = None) -> dict:
        """对一个**已连接**的 MCP 服务器做一次只读 resources/prompts 调用

        【能力协商必须读回】服务器没声明 `resources`/`prompts` ⇒ 回
        `not_supported`（**不是空列表**）。空列表与"不支持"对运维的含义不同：
        前者是"服务器有该能力但当前没有条目"，后者是"这条能力根本不存在"。
        """
        conn = self._connections.get(service_id)
        if not conn:
            return {"ok": False, "code": "no_connection",
                    "error": "连接不存在或已断开（先用 connect_mcp 连接）"}
        kind = "prompts" if action in ("list_prompts", "get_prompt") else "resources"
        caps = self.capabilities_of(service_id)
        if kind not in caps:
            return {
                "ok": False, "code": "not_supported",
                "capabilities": sorted(caps),
                "error": f"该 MCP 服务器未声明 {kind} 能力（不是空列表）",
            }
        try:
            if conn.get("transport") == "http":
                return self._http_resource_action(
                    str(conn.get("base_url") or ""), action, uri, name, arguments)
            return self._stdio_resource_action(
                conn.get("client"), action, uri, name, arguments)
        except Exception as exc:  # noqa: BLE001 统一转稳定码，异常原文不进响应
            logger.warning("[MCP] resource_action %s 失败（%s）", action, type(exc).__name__)
            return {"ok": False, "code": "no_connection",
                    "error": "MCP 调用失败（详见服务日志）"}

    def _stdio_resource_action(self, client, action: str, uri: str, name: str,
                               arguments: dict | None) -> dict:
        """STDIO 传输：经 MCPClient 的异步方法读 resources/prompts"""
        if client is None:
            return {"ok": False, "code": "no_connection",
                    "error": "该连接没有可用的 MCP 客户端"}
        loop = self._get_loop()
        if action == "list_resources":
            items = loop.run_until_complete(client.list_resources())
            return {"ok": True, "resources": [
                {
                    "uri": str(_resource_field(r, "uri")),
                    "name": str(_resource_field(r, "name")),
                    "description": str(_resource_field(r, "description")),
                    "mimeType": str(_resource_field(r, "mimeType", "mime_type")),
                }
                for r in (items or [])
            ]}
        if action == "read_resource":
            content = loop.run_until_complete(client.read_resource_content(uri))
            return {"ok": True, "contents": [content] if content else []}
        if action == "list_prompts":
            prompts = loop.run_until_complete(client.list_prompts())
            return {"ok": True, "prompts": prompts}
        prompt = loop.run_until_complete(client.get_prompt(name, arguments or {}))
        return {"ok": True, "prompt": prompt}

    def _http_resource_action(self, base_url: str, action: str, uri: str, name: str,
                              arguments: dict | None) -> dict:
        """HTTP 传输：直接发 JSON-RPC（与 tools/call 同一出口，同样复检 SSRF）"""
        if not base_url:
            return {"ok": False, "code": "no_connection", "error": "该 HTTP 连接缺少地址"}
        if action == "list_resources":
            rpc = self._http_rpc(base_url, "resources/list", {})
            if not rpc.get("ok"):
                return rpc
            items = rpc["result"].get("resources", [])
            return {"ok": True, "resources": items if isinstance(items, list) else []}
        if action == "read_resource":
            rpc = self._http_rpc(base_url, "resources/read", {"uri": uri})
            if not rpc.get("ok"):
                return rpc
            contents = rpc["result"].get("contents", [])
            return {"ok": True, "contents": contents if isinstance(contents, list) else []}
        if action == "list_prompts":
            rpc = self._http_rpc(base_url, "prompts/list", {})
            if not rpc.get("ok"):
                return rpc
            prompts = rpc["result"].get("prompts", [])
            return {"ok": True, "prompts": prompts if isinstance(prompts, list) else []}
        params: dict = {"name": name}
        if arguments:
            params["arguments"] = arguments
        rpc = self._http_rpc(base_url, "prompts/get", params)
        if not rpc.get("ok"):
            return rpc
        return {"ok": True, "prompt": rpc["result"]}


def resource_action(service_id: str, action: str, *, uri: str = "", name: str = "",
                    arguments: dict | None = None) -> dict:
    """模块级入口：取进程内连接管理器执行一次只读 resources/prompts 调用

    【为什么走模块级函数】工具 handler 只依赖这个函数名 ⇒ 静态调用链能落到本模块，
    由 `McpConnector` 的跨边界事实（stdio 子进程 / HTTP）判定 location。
    """
    try:
        from agent.tool_router import get_discovery_service
    except Exception:  # noqa: BLE001 路由层不可用 ⇒ 没有连接管理器
        get_discovery_service = None  # type: ignore[assignment]
    discovery = get_discovery_service() if callable(get_discovery_service) else None
    if discovery is None:
        return {"ok": False, "code": "no_connection",
                "error": "工具发现服务未初始化（当前进程没有 MCP 连接管理器）"}
    connector = getattr(discovery, "mcp", None)
    if connector is None or not hasattr(connector, "resource_action"):
        return {"ok": False, "code": "no_connection",
                "error": "当前进程没有 MCP 连接管理器"}
    return connector.resource_action(service_id, action, uri=uri, name=name,
                                     arguments=arguments)
