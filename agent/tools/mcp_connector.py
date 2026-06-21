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

logger = logging.getLogger(__name__)


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
                "connected_at": time.time(),
            }
            logger.info(f"[MCP] HTTP 连接成功: {service_id} ({name} @ {base_url}), {count} 个工具")
            return {"ok": True, "message": f"已连接 {name}，注册 {count} 个工具", "tools": count}

        except Exception as e:
            logger.error(f"[MCP] HTTP 连接失败: {service_id} @ {address}:{port}: {e}")
            return {"ok": False, "error": str(e)}

    def _http_list_tools(self, base_url: str) -> list[dict] | None:
        """通过 HTTP JSON-RPC 获取工具列表"""
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

    def _make_http_handler(self, base_url: str, tool_name: str):
        """创建 HTTP 转发的工具处理函数"""
        def _handler(**kwargs):
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
