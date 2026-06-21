"""MCP 工具注册框架 — 云枢可调用的行动接口

我是云枢的"双手"——每个工具都是一项我能够执行的具体操作。
工具按照 MCP（Model Context Protocol）风格设计，拥有统一接口。
"""

import logging
import time
import uuid
from typing import Callable, Any

logger = logging.getLogger(__name__)

# 全局工具注册表
_registry: dict[str, dict] = {}

# 操作追踪器（可选，由权限模块设置）
_action_tracker = None

# 全局限流器（在 call() 中检查调用频率）
from agent.rate_limiter import RateLimiter as _RateLimiter
_rate_limiter = _RateLimiter()

# ── Task 3.3: 注册表缓存 ──
_registry_version = 0
_list_tools_cache: dict = {"version": -1, "data": None}
_get_tool_defs_cache: dict = {"version": -1, "data": None}

# ── Task 3.4: 工具健康追踪 ──
_tool_health: dict[str, dict] = {}

# 工具来源枚举
SOURCE_BUILTIN = "builtin"    # 内置工具（8 个模块注册）
SOURCE_PLUGIN = "plugin"      # 插件系统提供
SOURCE_MCP = "mcp"           # MCP 服务提供
SOURCE_GENERATED = "generated"  # LLM 自生成
SOURCE_MARKET = "market"     # 从扩展市场安装

# 可选：工具发现服务实例（由 DigitalLife 通过 set_discovery_service 设置）
_discovery_service = None


class ToolError(Exception):
    """工具执行异常"""
    pass


def register(name: str, description: str = "", schema: dict | None = None, **kwargs):
    """注册一个工具到全局注册表

    支持装饰器模式和直接调用模式两种用法：

    @register("tool_name", "工具描述")
    def my_tool(**kw): ...

    # 或直接注册已定义的函数
    register("tool_name", "工具描述", handler=my_tool)

    Args:
        name: 工具名称（唯一标识）
        description: 工具描述
        schema: 工具参数的 JSON Schema（可选，用于 tool calling）
        **kwargs: 额外元数据或 handler=func 直接传入函数
    """
    def _do_register(handler: Callable) -> Callable:
        global _registry_version
        if name in _registry:
            logger.warning(f"工具 '{name}' 已存在，将被覆盖")
        entry = {
            "name": name,
            "description": description,
            "handler": handler,
        }
        if schema:
            entry["schema"] = schema
        _registry[name] = entry
        _registry_version += 1
        logger.info(f"工具注册: {name} — {description}")
        return handler

    # 如果 kwargs 中有 handler，直接注册
    handler = kwargs.get("handler")
    if handler is not None:
        _do_register(handler)
        return handler

    # 否则返回装饰器
    return _do_register


def unregister(name: str):
    """注销一个工具"""
    global _registry_version
    if name in _registry:
        del _registry[name]
        _registry_version += 1
        logger.info(f"工具注销: {name}")


def register_dynamic(name: str, description: str = "",
                     handler: Callable = None, schema: dict | None = None,
                     source: str = "dynamic", source_id: str | None = None) -> Callable:
    """注册动态获取的工具到全局注册表（带来源元数据）

    与 register() 的区别在于记录来源信息，支持后续按来源批量管理。
    若名称与已有工具冲突，自动添加数字后缀。

    Args:
        name: 工具名称
        description: 工具描述
        handler: 处理函数
        schema: JSON Schema
        source: 来源（SOURCE_BUILTIN / SOURCE_PLUGIN / SOURCE_MCP /
                SOURCE_GENERATED / SOURCE_MARKET）
        source_id: 来源标识符（如插件 ID）

    Returns:
        实际注册的处理函数（名称冲突时可能有别名）
    """
    global _registry_version

    # 处理名称冲突：如果已存在，加数字后缀
    final_name = name
    suffix = 1
    while final_name in _registry:
        suffix += 1
        final_name = f"{name}_{suffix}"

    if final_name != name:
        logger.warning(f"工具 '{name}' 已存在，以 '{final_name}' 注册")

    entry = {
        "name": final_name,
        "description": description,
        "handler": handler,
        "source": source,
        "source_id": source_id,
        "dynamic": source != SOURCE_BUILTIN,
        "registered_at": time.time(),
    }
    if schema:
        entry["schema"] = schema
    _registry[final_name] = entry
    _registry_version += 1
    logger.info(f"动态工具注册: {final_name} (来源: {source}, ID: {source_id})")
    return handler


def set_action_tracker(tracker):
    """设置操作追踪器（可选），用于记录工具调用历史

    Args:
        tracker: ActionTracker 实例，或 None 以清除追踪
    """
    global _action_tracker
    _action_tracker = tracker


def set_discovery_service(service):
    """设置工具发现服务实例

    由 DigitalLife 在初始化时设置，用于 call() 中找不到工具时自动触发发现流程。

    Args:
        service: ToolDiscoveryService 实例，或 None 以清除
    """
    global _discovery_service
    _discovery_service = service


def _update_health(name: str, ok: bool, duration: float):
    """更新工具健康状态"""
    if name not in _tool_health:
        _tool_health[name] = {
            "last_call_time": None,
            "last_ok": True,
            "last_duration": 0.0,
            "call_count": 0,
            "error_count": 0,
        }
    h = _tool_health[name]
    h["last_call_time"] = time.time()
    h["last_ok"] = ok
    h["last_duration"] = duration
    h["call_count"] += 1
    if not ok:
        h["error_count"] += 1


def call(*args, **params) -> Any:
    """调用指定工具

    支持两种调用方式:
      1. call("tool_name", **params) — 标准方式
      2. call(**params_with_name) — params 中包含 name 字段（用于扩展管理工具等场景）

    Args:
        *args: 第一个参数为工具名称（可选，也可从 params 中取）
        **params: 工具参数

    Returns:
        工具执行结果

    Raises:
        ToolError: 工具不存在或执行失败
    """
    # 生成追踪 ID
    trace_id = uuid.uuid4().hex[:12]

    # 从 args 或 params 中提取工具名称，避免 name 关键字冲突
    name = args[0] if args else params.pop("name", None)
    if not name:
        raise ToolError("调用工具时缺少工具名称")

    # 限流检查
    if not _rate_limiter.check(name):
        wait = _rate_limiter.wait_time(name)
        return {"ok": False, "error": f"调用频率过高，请稍后重试", "retry_after": round(wait, 1)}

    tool = _registry.get(name)
    if not tool:
        raise ToolError(f"未知工具: '{name}'，可用工具: {list_tools()}")

    # 操作追踪（可选）
    if _action_tracker:
        target = str(params.get("path", params.get("url", params.get("target", ""))))
        _action_tracker.start_action(name, params, target)

    # 关键工具：额外追踪信息
    if name == "web_search":
        query_preview = str(params.get("query", ""))[:100]
        engine = params.get("engine", "auto")
        logger.info(f"[{trace_id}] 调用工具: {name}, 查询: {query_preview}, 引擎: {engine}")
    elif name == "shell_execute":
        cmd_preview = str(params.get("command", ""))[:100]
        logger.info(f"[{trace_id}] 调用工具: {name}, 命令: {cmd_preview}")
    else:
        logger.info(f"[{trace_id}] 调用工具: {name}, 参数: {params}")

    start = time.time()
    try:
        result = tool["handler"](**params)
        duration = time.time() - start
        _update_health(name, True, duration)

        # 关键工具：记录返回信息
        if name == "web_search":
            if isinstance(result, dict):
                result_count = len(result.get("results", []))
                logger.info(f"[{trace_id}] 工具返回: {name} → 结果数: {result_count}")
            else:
                logger.info(f"[{trace_id}] 工具返回: {name} → {str(result)[:200]}")
        elif name == "shell_execute":
            if isinstance(result, dict):
                exit_code = result.get("returncode", result.get("code", "?"))
                output_size = len(str(result.get("stdout", result.get("output", ""))))
                logger.info(f"[{trace_id}] 工具返回: {name} → 退出码: {exit_code}, 输出大小: {output_size}")
            else:
                logger.info(f"[{trace_id}] 工具返回: {name} → {str(result)[:200]}")
        else:
            logger.info(f"[{trace_id}] 工具返回: {name} → {str(result)[:200]}")

        # 完成操作追踪
        if _action_tracker:
            _action_tracker.finish_action("completed", str(result)[:200])
            if any(k in name for k in ["http", "fetch", "search", "api", "browse"]):
                access_type = "network"
            elif any(k in name for k in ["read", "write", "list", "delete", "rename", "copy"]):
                access_type = "file"
            else:
                access_type = "sensor"
            _action_tracker.log_access(access_type, target or name, name, "allowed")

        return result
    except Exception as e:
        duration = time.time() - start
        _update_health(name, False, duration)
        logger.error(f"[{trace_id}] 工具执行失败: {name} — {e}")

        # 操作追踪失败
        if _action_tracker:
            _action_tracker.finish_action("failed", str(e)[:200])

        raise ToolError(f"工具 '{name}' 执行失败: {e}") from e


def list_tools() -> list[dict]:
    """列出所有已注册的工具（带缓存）"""
    global _list_tools_cache
    if _list_tools_cache["version"] != _registry_version:
        _list_tools_cache = {
            "version": _registry_version,
            "data": [
                {"name": t["name"], "description": t["description"]}
                for t in _registry.values()
            ],
        }
    return _list_tools_cache["data"]


def list_tools_by_source(source: str) -> list[dict]:
    """按来源列出工具

    Args:
        source: 来源标识（如 SOURCE_PLUGIN）

    Returns:
        匹配的工具列表
    """
    return [
        {"name": t["name"], "description": t["description"]}
        for t in _registry.values()
        if t.get("source") == source
    ]


def unregister_by_source(source: str, source_id: str | None = None) -> int:
    """按来源注销工具组

    用于插件卸载/MCP 断连时批量清理。

    Args:
        source: 来源标识
        source_id: 来源标识符（可选，指定则只注销特定来源实例的工具）

    Returns:
        注销的工具数量
    """
    global _registry_version
    to_remove = [
        name for name, t in _registry.items()
        if t.get("source") == source
        and (source_id is None or t.get("source_id") == source_id)
    ]
    for name in to_remove:
        del _registry[name]
    if to_remove:
        _registry_version += 1
        logger.info(f"按来源注销 {len(to_remove)} 个工具: source={source}, source_id={source_id}")
    return len(to_remove)


def get_tool_defs(whitelist: list[str] | None = None) -> list[dict]:
    """获取工具定义的 OpenAI/Anthropic 格式列表（带缓存）

    Args:
        whitelist: 允许返回的工具名称列表，None 表示全部

    Returns:
        OpenAI-compatible tool definitions list
    """
    # 无白名单时使用缓存
    if whitelist is None:
        global _get_tool_defs_cache
        if _get_tool_defs_cache["version"] != _registry_version:
            defs = []
            for name, tool in _registry.items():
                schema = tool.get("schema", {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                })
                defs.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool["description"],
                        "parameters": schema,
                    }
                })
            _get_tool_defs_cache = {
                "version": _registry_version,
                "data": defs,
            }
        return _get_tool_defs_cache["data"]

    # 有白名单时不使用缓存，实时计算
    defs = []
    for name, tool in _registry.items():
        if whitelist and name not in whitelist:
            continue
        schema = tool.get("schema", {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        })
        defs.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool["description"],
                "parameters": schema,
            }
        })
    return defs


def get_tool_schema(name: str) -> dict | None:
    """获取指定工具的 JSON Schema

    Args:
        name: 工具名称

    Returns:
        工具参数的 JSON Schema，如果工具不存在则返回 None
    """
    tool = _registry.get(name)
    if not tool:
        return None
    return tool.get("schema", {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    })


def sync_web_search_engines(engine_names: list[str], search_engine=None) -> bool:
    """同步 web_search 工具的可用搜索引擎列表（动态更新 enum）

    当搜索引擎新增/删除时调用，让 LLM 知道哪些引擎可以用。

    Args:
        engine_names: 可用的搜索引擎名称列表
        search_engine: 可选的 SearchEngine 实例，传入后自动过滤不可用的引擎

    Returns:
        bool: 是否成功更新
    """
    tool = _registry.get("web_search")
    if not tool:
        logger.warning("[工具] web_search 工具未注册，无法同步引擎列表")
        return False

    # 如果传入了 SearchEngine 实例，过滤出真正可用的引擎
    if search_engine is not None:
        try:
            available = search_engine.get_available_engines()
            engine_names = [
                e["name"] for e in available
                if e.get("enabled", True)
                and (not e.get("needs_key") or e.get("configured"))
            ]
        except Exception:
            pass  # 回退到传入的 engine_names

    if not engine_names:
        engine_names = []  # 至少为空列表

    schema = tool.get("schema", {})
    props = schema.get("properties", {})
    engine_prop = props.get("engine")
    if engine_prop is None:
        logger.warning("[工具] web_search 工具的 engine 参数不存在")
        return False

    engine_prop["enum"] = engine_names
    engine_prop["description"] = (
        f"搜索引擎名称（可选）。可用引擎: {', '.join(engine_names)}。"
        "不指定则按优先级自动选择"
    )
    global _registry_version
    _registry_version += 1
    logger.info(f"[工具] web_search 引擎 enum 已同步: {engine_names}")
    return True


def get_health_status() -> dict:
    """获取所有工具的健康状态

    Returns:
        dict: {
            "tools": {tool_name: {last_call_time, last_ok, last_duration, call_count, error_count}},
            "overall_score": int (0-100),
            "total_tools": int,
            "healthy_tools": int,
        }
    """
    tools_health = dict(_tool_health)
    total = len(tools_health)

    if total == 0:
        return {
            "tools": {},
            "overall_score": 100,
            "total_tools": 0,
            "healthy_tools": 0,
        }

    healthy_count = sum(1 for h in tools_health.values() if h["last_ok"])
    # 计算整体评分: 基于健康工具比例和错误率
    total_calls = sum(h["call_count"] for h in tools_health.values())
    total_errors = sum(h["error_count"] for h in tools_health.values())

    if total_calls == 0:
        # 尚未有任何调用，返回完美评分
        overall_score = 100
    else:
        error_rate = total_errors / total_calls
        healthy_ratio = healthy_count / total
        # 加权评分: 健康比例占 70%，错误率占 30%
        overall_score = int(healthy_ratio * 70 + (1 - error_rate) * 30)
        overall_score = max(0, min(100, overall_score))

    return {
        "tools": {
            name: {
                "last_call_time": h["last_call_time"],
                "last_ok": h["last_ok"],
                "last_duration": round(h["last_duration"], 4),
                "call_count": h["call_count"],
                "error_count": h["error_count"],
            }
            for name, h in tools_health.items()
        },
        "overall_score": overall_score,
        "total_tools": total,
        "healthy_tools": healthy_count,
    }


def clear():
    """清空工具注册表（主要用于测试）"""
    global _registry_version, _list_tools_cache, _get_tool_defs_cache
    _registry.clear()
    _registry_version += 1
    _list_tools_cache = {"version": -1, "data": None}
    _get_tool_defs_cache = {"version": -1, "data": None}
    _tool_health.clear()
