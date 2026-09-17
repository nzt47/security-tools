"""工具发现服务 — 按需获取工具的协调器

负责 A) 按需安装 和 C) MCP 发现的协调工作。
将搜索结果映射到工具注册表，提供一键安装+注册能力。
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ToolDiscoveryService:
    """工具发现服务 — 协调扩展市场和 MCP 扫描的工具获取"""

    def __init__(self, extension_manager=None, market=None):
        self._ext_mgr = extension_manager
        self._market = market
        # MCP 连接管理器（延迟初始化）
        self._mcp_connector = None

    @property
    def mcp(self):
        """获取 MCP 连接管理器实例"""
        if self._mcp_connector is None:
            from agent.tools.mcp_connector import McpConnector
            self._mcp_connector = McpConnector()
        return self._mcp_connector

    def search_market(self, query: str, category: str = None) -> dict:
        """搜索扩展市场，返回可用工具列表

        Args:
            query: 搜索关键词
            category: 过滤类别 (tool/skill/mcp/plugin)

        Returns:
            {"ok": bool, "results": list[dict], "count": int}
        """
        if not self._market:
            # 延迟初始化
            try:
                from agent.extensions.market import ExtensionMarket
                self._market = ExtensionMarket()
            except Exception as e:
                logger.error(f"扩展市场初始化失败: {e}")
                return {"ok": False, "error": "扩展市场未初始化", "results": [], "count": 0}

        try:
            results = self._market.search_all(query, category)
            # 展平结果
            flat = []
            for source, items in results.items():
                for item in items:
                    item["_market_source"] = source
                    flat.append(item)
            return {"ok": True, "results": flat, "count": len(flat)}
        except Exception as e:
            logger.error(f"市场搜索失败: {e}")
            return {"ok": False, "error": str(e), "results": [], "count": 0}

    def install_and_register(self, tool_id: str, source: str = None) -> dict:
        """安装工具并自动注册到工具表

        Args:
            tool_id: 扩展/工具 ID
            source: 安装来源 (如 "github:user/repo")

        Returns:
            {"ok": bool, "message": str, "tools": list[str]}
        """
        if not self._ext_mgr:
            return {"ok": False, "error": "扩展管理器未初始化"}

        try:
            # 根据 ID 猜测类型和来源
            ext_type = self._guess_ext_type(tool_id)
            install_source = source or tool_id
            result = self._ext_mgr.install(ext_type, install_source)
            ok = result.get("ok", False)
            return {
                "ok": ok,
                "message": result.get("message", str(result)),
                "tools": [],
            }
        except Exception as e:
            logger.error(f"工具安装失败: {tool_id}: {e}")
            return {"ok": False, "error": str(e)}

    def on_tool_not_found(self, tool_name: str, params: dict = None) -> dict:
        """工具未找到时的回调 — 触发 A 链

        Args:
            tool_name: 未找到的工具名称
            params: 调用参数（可用于推测用途）

        Returns:
            {"acquired": bool, "tool": str, "message": str}
        """
        logger.info(f"[发现] 工具 '{tool_name}' 未找到，尝试自动获取...")

        # 根据工具名推测关键词搜索市场
        # 将驼峰/下划线命名转为自然语言查询
        query = tool_name.replace("_", " ").replace("-", " ")
        result = self.search_market(query)
        if result.get("ok") and result.get("count", 0) > 0:
            first_match = result["results"][0]
            logger.info(f"[发现] 在市场中找到匹配: {first_match.get('name', first_match.get('ext_id', '?'))}")
            # 尝试安装第一个匹配
            ext_id = first_match.get("ext_id") or first_match.get("name", "")

            # ── 审批边界（2026-09-17）──
            # 自动安装 = **自我改造**（`ext_install` 是 plane=govern / effect=extend）。
            # 本方法是"模型调了一个不存在的工具名"触发的**隐性**安装路径：
            # 若在此直接 install_and_register，就绕过了显式 `ext_install` 必须走的审批边界——
            # 模型打错一个工具名即可装进一个扩展。故与显式调用走**同一道闸门**：
            # 未获人工批准时挂单并如实上报，绝不静默安装。
            denied = self._approval_gate(ext_id)
            if denied is not None:
                logger.warning("[发现] 自动安装被审批边界拦下: ext_id=%s error_code=%s",
                               ext_id, denied.get("error_code", ""))
                return {
                    "acquired": False,
                    "tool": tool_name,
                    "error_code": str(denied.get("error_code") or ""),
                    "approval_id": str(denied.get("approval_id") or ""),
                    "message": (f"匹配到 {first_match.get('name', ext_id)}，但自动安装需要人工审批："
                                f"{denied.get('error') or denied.get('reason') or '需审批'}"),
                }

            install_result = self.install_and_register(ext_id)
            if install_result.get("ok"):
                return {
                    "acquired": True,
                    "tool": tool_name,
                    "message": f"已自动安装 {first_match.get('name', ext_id)}",
                }

        return {"acquired": False, "tool": tool_name, "message": "未找到匹配工具"}

    @staticmethod
    def _approval_gate(ext_id: str):
        """对"自动安装"取一次闸门判定：``None`` = 放行；否则为拒绝结果

        与 ``agent/tool_gate.check_tool_call("ext_install", ...)`` 同源：命中审批边界时
        会在审批收件箱挂单并返回 ``APPROVAL_REQUIRED``（人工批准后原样重试即放行）。
        **闸门不可用按既有 fail-open 口径放行**（与 ``agent/tools/__init__.py::call()``
        的 try/except 同一纪律：闸门自身 bug 不得阻断既有链路）；闸门**明确返回拒绝**时
        才算拦下。
        """
        try:
            from agent.tool_gate import check_tool_call
            return check_tool_call("ext_install", {"source": str(ext_id or ""), "type": "auto"})
        except Exception as e:  # noqa: BLE001 闸门不可用 ⇒ 不阻断既有自动发现链路
            logger.debug("[发现] 审批闸门不可用（按放行处理）: %s: %s", type(e).__name__, e)
            return None

    def scan_mcp_services(self, network_range: str = None) -> list[dict]:
        """扫描并注册已知 MCP 服务

        扫描两种来源：
        1. NetworkConfigManager 中配置的 MCP 服务（HTTP/STDIO）
        2. 内置 MCP 服务模板

        Args:
            network_range: 网络范围（暂未使用，保留接口兼容）

        Returns:
            发现的 MCP 服务列表
        """
        results = []
        processed = set()

        # 来源1: 从 NetworkConfigManager 获取已配置的服务
        try:
            if self._ext_mgr and hasattr(self._ext_mgr, '_network_config_mgr'):
                ncm = self._ext_mgr._network_config_mgr
                if ncm:
                    # get_mcp_services() 返回 list[dict]，每个服务有 id/name/address/port/protocol
                    services = ncm.get_mcp_services()
                    for svc in services:
                        svc_id = svc.get("id", "")
                        if svc_id in processed:
                            continue
                        processed.add(svc_id)

                        # 判断传输模式
                        transport = svc.get("protocol", "http")
                        name = svc.get("name", svc_id)

                        if transport in ("http", "https", "sse"):
                            result = self.mcp.connect_http(
                                service_id=svc_id,
                                name=name,
                                address=svc.get("address", "127.0.0.1"),
                                port=svc.get("port", 8080),
                            )
                        elif transport == "stdio":
                            result = self.mcp.connect_stdio(
                                service_id=svc_id,
                                name=name,
                                command=svc.get("command", "python"),
                                args=svc.get("args", []),
                            )
                        else:
                            result = {"ok": False, "error": f"不支持的传输模式: {transport}"}

                        results.append({
                            "id": svc_id,
                            "name": name,
                            "transport": transport,
                            "ok": result.get("ok", False),
                            "tools": result.get("tools", 0),
                            "message": result.get("message") or result.get("error", ""),
                        })
        except Exception as e:
            logger.error(f"[发现] 扫描 MCP 配置失败: {e}")

        # 来源2: 扫描内置 MCP 服务的安装状态
        if not self._ext_mgr:
            logger.info(f"[发现] MCP 服务扫描完成: {len(results)} 个")
            return results

        try:
            # 检查已安装的 MCP 服务（通过 ExtensionManager）
            installed = self._ext_mgr.discover_all()
            for mcp_svc in installed.get("mcp_services", []):
                svc_id = mcp_svc.get("ext_id") or mcp_svc.get("id", "")
                if svc_id in processed:
                    continue
                processed.add(svc_id)

                # 检查是否已连接
                conn = self.mcp.get_connection(svc_id)
                if conn:
                    results.append({
                        "id": svc_id,
                        "name": mcp_svc.get("name", svc_id),
                        "transport": conn["transport"],
                        "ok": True,
                        "tools": conn["tools"],
                        "message": "已连接",
                    })
                else:
                    results.append({
                        "id": svc_id,
                        "name": mcp_svc.get("name", svc_id),
                        "transport": mcp_svc.get("protocol", "http"),
                        "ok": False,
                        "tools": 0,
                        "message": "未连接（使用 connect_mcp 工具连接）",
                    })
        except Exception as e:
            logger.warning(f"[发现] 扫描已安装 MCP 失败: {e}")

        logger.info(f"[发现] MCP 服务扫描完成: {len(results)} 个")
        return results

    def connect_mcp_service(self, service_id: str,
                            transport: str = "stdio",
                            command: str = "python",
                            args: list[str] | None = None,
                            address: str = "127.0.0.1",
                            port: int = 8080) -> dict:
        """连接一个 MCP 服务并注册其工具

        Args:
            service_id: 服务标识
            transport: 传输模式 ("stdio" 或 "http")
            command: STDIO 模式下的启动命令
            args: STDIO 模式下的参数
            address: HTTP 模式下的地址
            port: HTTP 模式下的端口

        Returns:
            {"ok": bool, "message": str, "tools": int}
        """
        if transport == "stdio":
            return self.mcp.connect_stdio(service_id, service_id, command, args or [])
        elif transport == "http":
            return self.mcp.connect_http(service_id, service_id, address, port)
        return {"ok": False, "error": f"不支持的传输模式: {transport}"}

    def disconnect_mcp_service(self, service_id: str) -> dict:
        """断开一个 MCP 服务连接

        Args:
            service_id: 服务标识

        Returns:
            {"ok": bool, "message": str}
        """
        return self.mcp.disconnect(service_id)

    def list_mcp_connections(self) -> list[dict]:
        """列出所有活跃的 MCP 连接"""
        return self.mcp.list_connections()

    def _guess_ext_type(self, tool_id: str) -> str:
        """根据 ID 猜测扩展类型"""
        from agent.extensions.base import BUILTIN_EXTENSIONS
        for ext_type, exts in BUILTIN_EXTENSIONS.items():
            for ext in exts:
                if ext.get("ext_id") == tool_id or ext.get("name") == tool_id:
                    return ext_type.value
        # 默认按插件处理
        return "plugin"
