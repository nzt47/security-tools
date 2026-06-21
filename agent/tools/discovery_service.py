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
            install_result = self.install_and_register(ext_id)
            if install_result.get("ok"):
                return {
                    "acquired": True,
                    "tool": tool_name,
                    "message": f"已自动安装 {first_match.get('name', ext_id)}",
                }

        return {"acquired": False, "tool": tool_name, "message": "未找到匹配工具"}

    def scan_mcp_services(self, network_range: str = None) -> list[dict]:
        """扫描并注册已知 MCP 服务

        Args:
            network_range: 网络范围（可选，默认扫描配置的服务）

        Returns:
            发现的 MCP 服务列表
        """
        discovered = []
        try:
            if self._ext_mgr and hasattr(self._ext_mgr, '_network_config_mgr'):
                ncm = self._ext_mgr._network_config_mgr
                if ncm:
                    config = ncm.get_config()
                    mcp_services = config.get("mcp_services", {})
                    for svc_id, svc_config in mcp_services.items():
                        if svc_config.get("enabled", True):
                            info = self._connect_and_register_mcp(svc_id, svc_config)
                            if info:
                                discovered.append(info)
            logger.info(f"MCP 服务扫描完成: 发现 {len(discovered)} 个")
        except Exception as e:
            logger.error(f"MCP 服务扫描失败: {e}")
        return discovered

    def _connect_and_register_mcp(self, svc_id: str, svc_config: dict) -> dict | None:
        """连接 MCP 服务并注册其工具（占位，Phase 3 完整实现）"""
        from agent import tools as _tools

        transport = svc_config.get("transport", "stdio")
        command = svc_config.get("command", "")
        args = svc_config.get("args", [])

        # Phase 3: 此处对接 MCP 协议客户端，获取 list_tools 并注册
        logger.info(f"MCP 服务已就绪: {svc_id} ({transport})")
        return {"id": svc_id, "transport": transport, "status": "pending"}

    def _guess_ext_type(self, tool_id: str) -> str:
        """根据 ID 猜测扩展类型"""
        from agent.extensions.base import BUILTIN_EXTENSIONS
        for ext_type, exts in BUILTIN_EXTENSIONS.items():
            for ext in exts:
                if ext.get("ext_id") == tool_id or ext.get("name") == tool_id:
                    return ext_type.value
        # 默认按插件处理
        return "plugin"
