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

    @_tools.register("market_search", "搜索扩展市场寻找可用工具。当你发现当前缺少某个能力时，用此工具搜索有没有现成的扩展可以安装。", schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，如'发送邮件'、'日期计算'、'天气预报'",
            },
            "category": {
                "type": "string",
                "enum": ["tool", "skill", "mcp", "plugin", ""],
                "description": "过滤类别（留空搜索全部）",
            },
        },
        "required": ["query"],
    })
    def _market_search(**kw):
        query = kw.get("query", "")
        category = kw.get("category") or None
        try:
            discovery = getattr(dl, '_discovery_service', None)
            if discovery:
                return discovery.search_market(query, category)
            return {"ok": False, "error": "发现服务未初始化"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @_tools.register("install_tool", "从扩展市场安装工具。安装后工具立即可用，无需重启。", schema={
        "type": "object",
        "properties": {
            "tool_id": {
                "type": "string",
                "description": "工具/扩展ID，如 'yunshu-email-plugin'",
            },
            "source": {
                "type": "string",
                "description": "安装来源（可选），如 'github:user/repo'",
            },
        },
        "required": ["tool_id"],
    })
    def _install_tool(**kw):
        tool_id = kw.get("tool_id", "")
        source = kw.get("source")
        try:
            discovery = getattr(dl, '_discovery_service', None)
            if discovery:
                return discovery.install_and_register(tool_id, source)
            return {"ok": False, "error": "发现服务未初始化"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @_tools.register("generate_tool", "生成一个自定义工具。当你需要的能力没有现成扩展时，可以自主编写代码生成工具。轻量工具不保存文件，复杂工具可持久化。", schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "工具名称（字母数字下划线）",
            },
            "description": {
                "type": "string",
                "description": "工具描述",
            },
            "code": {
                "type": "string",
                "description": "Python 函数实现代码。函数签名应与工具名称一致，接收 **kwargs 参数",
            },
            "schema": {
                "type": "object",
                "description": "参数的 JSON Schema（可选，不提供时使用空 schema）",
            },
            "persist": {
                "type": "boolean",
                "description": "是否持久化保存到文件（默认 False，仅注册到内存）",
            },
        },
        "required": ["name", "description", "code"],
    })
    def _generate_tool(**kw):
        name = kw.get("name", "")
        description = kw.get("description", "")
        code = kw.get("code", "")
        schema = kw.get("schema")
        persist = kw.get("persist", False)
        try:
            from agent.tools.tool_generator import ToolGenEngine
            engine = ToolGenEngine()
            if persist:
                ok = engine.generate_persistent(name, description, code, schema)
            else:
                ok = engine.generate_simple(name, description, code, schema)
            return {"ok": ok, "name": name, "persisted": persist}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @_tools.register("scan_mcp", "扫描并发现可用的 MCP 服务，自动注册其工具到工具列表。扫描已配置的服务和已安装的 MCP 扩展。", schema={
        "type": "object",
        "properties": {},
    })
    def _scan_mcp(**kw):
        try:
            discovery = getattr(dl, '_discovery_service', None)
            if discovery:
                results = discovery.scan_mcp_services()
                return {"ok": True, "services": results, "count": len(results)}
            return {"ok": False, "error": "发现服务未初始化"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @_tools.register("connect_mcp", "手动连接一个 MCP 服务并注册其工具。支持 STDIO(本地进程)和 HTTP(远程服务)两种模式。", schema={
        "type": "object",
        "properties": {
            "service_id": {"type": "string", "description": "服务标识（唯一）"},
            "transport": {
                "type": "string",
                "enum": ["stdio", "http"],
                "description": "传输模式：stdio=本地进程, http=远程HTTP服务",
            },
            "command": {"type": "string", "description": "STDIO 模式：启动命令（如 python、node）"},
            "args": {
                "type": "array", "items": {"type": "string"},
                "description": "STDIO 模式：命令参数列表",
            },
            "address": {"type": "string", "description": "HTTP 模式：服务地址"},
            "port": {"type": "integer", "description": "HTTP 模式：服务端口"},
        },
        "required": ["service_id", "transport"],
    })
    def _connect_mcp(**kw):
        service_id = kw.get("service_id", "")
        transport = kw.get("transport", "stdio")
        try:
            discovery = getattr(dl, '_discovery_service', None)
            if discovery:
                result = discovery.connect_mcp_service(
                    service_id=service_id,
                    transport=transport,
                    command=kw.get("command", "python"),
                    args=kw.get("args", []),
                    address=kw.get("address", "127.0.0.1"),
                    port=kw.get("port", 8080),
                )
                return result
            return {"ok": False, "error": "发现服务未初始化"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @_tools.register("disconnect_mcp", "断开一个 MCP 服务连接，注销其注册的工具。", schema={
        "type": "object",
        "properties": {
            "service_id": {"type": "string", "description": "要断开的服务标识"},
        },
        "required": ["service_id"],
    })
    def _disconnect_mcp(**kw):
        service_id = kw.get("service_id", "")
        try:
            discovery = getattr(dl, '_discovery_service', None)
            if discovery:
                return discovery.disconnect_mcp_service(service_id)
            return {"ok": False, "error": "发现服务未初始化"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
