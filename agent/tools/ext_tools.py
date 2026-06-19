"""工具注册模块 — 扩展管理工具（安装/卸载/查询/配置技能、MCP、通道、插件）"""
import logging
from agent import tools as _tools

logger = logging.getLogger(__name__)


def register_all(dl):
    """注册所有扩展管理工具

    Args:
        dl: DigitalLife 实例（用于访问 self 属性）
    """

    @_tools.register("ext_install", "安装扩展（技能/MCP服务/通道/插件）。让我能自主获取新能力。", schema={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["skill", "claude_skill", "mcp", "channel", "plugin"],
                "description": "扩展类型：skill=应用层技能, claude_skill=Claude Code技能, mcp=MCP服务, channel=通信通道, plugin=插件",
            },
            "source": {
                "type": "string",
                "description": "扩展来源。格式：内置ID(如 self_reflection / filesystem)，github:user/repo，url:https://...，local:/path，npm:package，pip:package",
            },
            "name": {
                "type": "string",
                "description": "扩展名称（可选，自定义安装时使用）",
            },
            "description": {
                "type": "string",
                "description": "扩展描述（可选）",
            },
            "params": {
                "type": "object",
                "description": "额外参数（可选，如技能参数、通道配置等）",
            },
        },
        "required": ["type", "source"],
    })
    def _ext_install(**kwargs):
        ext_type = kwargs.get("type", "")
        source = kwargs.get("source", "")
        params = kwargs.get("params", {})

        if not ext_type or not source:
            return {"ok": False, "error": "请指定扩展类型和来源"}

        # 使用扩展管理器单例
        try:
            _em = dl._get_ext_manager()
            result = _em.install(
                ext_type, source,
                name=kwargs.get("name", ""),
                description=kwargs.get("description", ""),
                **{k: v for k, v in params.items() if k not in ("name", "description")},
            )
            # 统一结果格式：ExtensionManager 使用 message 键，
            # 但工具调用系统期望 error 键
            if isinstance(result, dict) and "message" in result and "error" not in result:
                if not result.get("ok", False):
                    result["error"] = result["message"]
                result.pop("message")
            return result
        except Exception as e:
            logger.error(f"扩展安装失败: {e}")
            return {"ok": False, "error": f"扩展安装失败: {e}"}

    @_tools.register("ext_uninstall", "卸载扩展。移除不再需要的技能、MCP服务、通道或插件。", schema={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["skill", "claude_skill", "mcp", "channel", "plugin"],
                "description": "扩展类型",
            },
            "id": {
                "type": "string",
                "description": "扩展ID",
            },
        },
        "required": ["type", "id"],
    })
    def _ext_uninstall(**kwargs):
        ext_type = kwargs.get("type", "")
        ext_id = kwargs.get("id", "")
        try:
            _em = dl._get_ext_manager()
            return _em.uninstall(ext_type, ext_id)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @_tools.register("ext_list", "列出已安装的扩展（技能/MCP服务/通道/插件）。查询当前有哪些能力可用。", schema={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["skill", "claude_skill", "mcp", "channel", "plugin", ""],
                "description": "按类型筛选（留空列出全部）",
            },
        },
    })
    def _ext_list(**kwargs):
        ext_type = kwargs.get("type") or None
        try:
            _em = dl._get_ext_manager()
            if ext_type:
                # 非 skill 类型走扩展管理器，skill 类型也走扩展管理器（唯一数据源）
                if ext_type == "skill":
                    skills = _em.get_installed_by_type().get("skills", [])
                    formatted = []
                    for s in skills:
                        formatted.append({
                            "ext_id": s["id"], "ext_type": "skill",
                            "name": s.get("name", s["id"]),
                            "description": s.get("description", ""),
                            "status": "enabled" if s.get("enabled", True) else "disabled",
                            "enabled": s.get("enabled", True),
                        })
                    return {"ok": True, "type": "skill", "extensions": formatted}
                # 非 skill 类型走扩展管理器
                return {"ok": True, "type": ext_type, "extensions": _em.list_all(ext_type)}
            # ext_type 为 None — 列出全部
            all_types = _em.get_installed_by_type()
            all_extensions = []
            for s in all_types.get("skills", []):
                all_extensions.append({
                    "ext_id": s["id"], "ext_type": "skill",
                    "name": s.get("name", s["id"]),
                    "description": s.get("description", ""),
                    "status": "enabled" if s.get("enabled", True) else "disabled",
                    "enabled": s.get("enabled", True),
                })
            for key in ["claude_skills", "mcp_services", "channels", "plugins"]:
                all_extensions.extend(all_types.get(key, []))
            return {"ok": True, "extensions": all_extensions}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @_tools.register("ext_toggle", "启用或禁用扩展。临时打开/关闭某个技能、MCP服务或通道。", schema={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["skill", "mcp", "channel", "plugin"],
                "description": "扩展类型",
            },
            "id": {
                "type": "string",
                "description": "扩展ID",
            },
            "enabled": {
                "type": "boolean",
                "description": "是否启用（留空则切换当前状态）",
            },
        },
        "required": ["type", "id"],
    })
    def _ext_toggle(**kwargs):
        ext_type = kwargs.get("type", "")
        ext_id = kwargs.get("id", "")
        enabled = kwargs.get("enabled")
        try:
            _em = dl._get_ext_manager()
            return _em.toggle(ext_type, ext_id, enabled)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @_tools.register("ext_discover", "发现可用的扩展。搜索内置注册表、社区市场和GitHub上有什么新能力可以安装。", schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词（可选）",
            },
            "type": {
                "type": "string",
                "enum": ["skill", "claude_skill", "mcp", "channel", "plugin", ""],
                "description": "按类型筛选（可选）",
            },
        },
    })
    def _ext_discover(**kwargs):
        query = kwargs.get("query", "")
        ext_type = kwargs.get("type") or None
        try:
            from agent.extensions.market import ExtensionMarket as _ExtMarket
            _em = dl._get_ext_manager()
            _market = _ExtMarket()

            installed = _em.discover_all()
            if query:
                market_results = _market.search_all(query, ext_type)
                return {
                    "ok": True,
                    "query": query,
                    "builtin": installed,
                    "market": market_results,
                }

            return {"ok": True, **installed}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @_tools.register("ext_configure", "配置扩展参数。调整技能、MCP服务或通道的设置项。", schema={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["skill", "mcp", "channel", "plugin"],
                "description": "扩展类型",
            },
            "id": {
                "type": "string",
                "description": "扩展ID",
            },
            "config": {
                "type": "object",
                "description": "配置键值对",
            },
        },
        "required": ["type", "id", "config"],
    })
    def _ext_configure(**kwargs):
        ext_type = kwargs.get("type", "")
        ext_id = kwargs.get("id", "")
        config = kwargs.get("config", {})
        try:
            _em = dl._get_ext_manager()
            return _em.configure(ext_type, ext_id, config)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @_tools.register("ext_send_channel", "通过已安装的通信通道发送消息。比如发Webhook、邮件等。", schema={
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "通道ID",
            },
            "message": {
                "type": "string",
                "description": "消息内容",
            },
            "subject": {
                "type": "string",
                "description": "邮件主题（邮件通道专用）",
            },
            "to": {
                "type": "string",
                "description": "收件人（邮件通道专用）",
            },
        },
        "required": ["channel_id", "message"],
    })
    def _ext_send_channel(**kwargs):
        channel_id = kwargs.get("channel_id", "")
        message = kwargs.get("message", "")
        extra = {k: v for k, v in kwargs.items() if k not in ("channel_id", "message")}
        try:
            _em = dl._get_ext_manager()
            return _em.send_channel_message(channel_id, message, **extra)
        except Exception as e:
            return {"ok": False, "error": str(e)}
