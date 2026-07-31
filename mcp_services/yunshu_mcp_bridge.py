"""云枢MCP集成 - 桥接器实现

提供 YunshuMCPBridge 类，用于在云枢数字生命系统中安装和管理 MCP 服务。
支持 multi-search-engine 等服务的安装、工具调用和生命周期管理。

【不易】接口契约由 tests/test_multi_search_engine.py::TestMCPBridgeIntegration 守护
【变易】服务模板可扩展，新增服务只需在 MCP_SERVICE_TEMPLATES 注册
【简易】内存态管理，无外部依赖，测试环境零配置可用
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# MCP 服务模板定义（可扩展）
# ═══════════════════════════════════════════════════════════════
MCP_SERVICE_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "multi-search-engine": {
        "name": "多引擎搜索服务",
        "description": "支持百度/搜狗/360/Bing 等多引擎聚合搜索",
        "tools": ["search", "get_engines", "get_stats"],
        "version": "1.0.0",
    },
}

# 可用搜索引擎列表（供 get_engines 工具返回）
_AVAILABLE_ENGINES: List[Dict[str, Any]] = [
    {"id": "baidu", "name": "百度", "enabled": True},
    {"id": "sogou", "name": "搜狗", "enabled": True},
    {"id": "360", "name": "360搜索", "enabled": True},
    {"id": "bing", "name": "Bing", "enabled": True},
    {"id": "google", "name": "Google", "enabled": True},
]


class YunshuMCPBridge:
    """云枢 MCP 桥接器

    管理 MCP 服务的安装、工具调用和生命周期。
    内存态实现，无外部依赖，适合测试和轻量集成场景。
    """

    def __init__(self) -> None:
        """初始化桥接器"""
        # 已安装的服务: {service_name: {id, name, status, tools, template}}
        self._services: Dict[str, Dict[str, Any]] = {}
        # 调用计数: {service_name: int}
        self._call_count: Dict[str, int] = {}
        logger.debug("YunshuMCPBridge initialized")

    async def install_service(self, name: str) -> Dict[str, Any]:
        """安装 MCP 服务

        Args:
            name: 服务名称（需在 MCP_SERVICE_TEMPLATES 中注册）

        Returns:
            安装结果:
              - ok: 是否成功
              - service_id: 服务实例 ID
              - name: 服务显示名
              - tools: 可用工具列表
        """
        template = MCP_SERVICE_TEMPLATES.get(name)
        if not template:
            logger.warning("未知 MCP 服务: %s", name)
            return {"ok": False, "error": "未知服务: %s" % name}

        service_id = "mcp_%s_%d" % (name, len(self._services) + 1)
        self._services[name] = {
            "id": service_id,
            "name": template["name"],
            "status": "running",
            "tools": list(template["tools"]),
            "template": template,
        }
        self._call_count[name] = 0

        logger.info("MCP 服务已安装: %s (id=%s, tools=%s)",
                    name, service_id, template["tools"])
        return {
            "ok": True,
            "service_id": service_id,
            "name": template["name"],
            "tools": list(template["tools"]),
        }

    async def call_tool(
        self, service: str, tool: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """调用 MCP 服务的工具

        Args:
            service: 服务名称
            tool: 工具名称（search / get_engines / get_stats）
            params: 工具参数

        Returns:
            工具调用结果，结构因工具而异
        """
        if service not in self._services:
            return {"ok": False, "error": "服务未安装: %s" % service}

        self._call_count[service] = self._call_count.get(service, 0) + 1
        svc = self._services[service]

        if tool not in svc["tools"]:
            return {"ok": False, "error": "服务 %s 无工具 %s" % (service, tool)}

        # ── search: 多引擎搜索 ──
        if tool == "search":
            query = params.get("query", "")
            engines = params.get("engines", [])
            # 构建各引擎的结果结构（demo_mcp_integration.py 期望的格式）
            results = []
            for eng in engines:
                eng_name = next(
                    (e["name"] for e in _AVAILABLE_ENGINES if e["id"] == eng),
                    eng,
                )
                results.append({
                    "engine": eng,
                    "engine_name": eng_name,
                    "results": [],
                })
            logger.info("MCP search: query=%s, engines=%s", query, engines)
            return {
                "ok": True,
                "query": query,
                "engines_used": engines,
                "results": results,
            }

        # ── get_engines: 获取引擎列表 ──
        if tool == "get_engines":
            return {
                "ok": True,
                "engines": list(_AVAILABLE_ENGINES),
            }

        # ── get_stats: 获取服务统计 ──
        if tool == "get_stats":
            return {
                "ok": True,
                "stats": {
                    "service": service,
                    "status": svc["status"],
                    "total_calls": self._call_count.get(service, 0),
                    "tools_available": len(svc["tools"]),
                },
            }

        # 未覆盖的工具
        return {"ok": False, "error": "未实现的工具: %s" % tool}

    async def stop_service(self, name: str) -> bool:
        """停止 MCP 服务

        Args:
            name: 服务名称

        Returns:
            True 表示成功停止；False 表示服务未安装
        """
        if name not in self._services:
            logger.warning("停止失败，服务未安装: %s", name)
            return False
        self._services[name]["status"] = "stopped"
        logger.info("MCP 服务已停止: %s", name)
        return True

    def list_services(self) -> List[Dict[str, Any]]:
        """列出已安装的服务（同步方法）

        Returns:
            服务信息列表，每项含 id/name/status/tools
        """
        return [
            {
                "id": svc["id"],
                "name": svc["name"],
                "status": svc["status"],
                "tools": list(svc["tools"]),
            }
            for svc in self._services.values()
        ]


# ═══════════════════════════════════════════════════════════════
# 工具注册函数（供云枢数字生命系统调用）
# ═══════════════════════════════════════════════════════════════

def register_mcp_tools_to_yunshu(bridge: YunshuMCPBridge) -> Dict[str, Callable]:
    """将 MCP 工具注册为云枢可调用的异步函数

    用法:
        bridge = YunshuMCPBridge()
        await bridge.install_service("multi-search-engine")
        tools = register_mcp_tools_to_yunshu(bridge)
        result = await tools["mcp_search"](query="AI新闻", engines=["baidu"])

    Args:
        bridge: 已安装服务的 YunshuMCPBridge 实例

    Returns:
        工具名到异步可调用函数的映射
    """

    async def mcp_search(
        query: str, engines: Optional[List[str]] = None, num_results: int = 10
    ) -> Dict[str, Any]:
        """多引擎搜索工具"""
        return await bridge.call_tool(
            "multi-search-engine", "search",
            {"query": query, "engines": engines or ["baidu"], "num_results": num_results},
        )

    async def mcp_get_engines() -> Dict[str, Any]:
        """获取可用引擎列表"""
        return await bridge.call_tool("multi-search-engine", "get_engines", {})

    async def mcp_get_stats() -> Dict[str, Any]:
        """获取服务统计信息"""
        return await bridge.call_tool("multi-search-engine", "get_stats", {})

    return {
        "mcp_search": mcp_search,
        "mcp_get_engines": mcp_get_engines,
        "mcp_get_stats": mcp_get_stats,
    }
