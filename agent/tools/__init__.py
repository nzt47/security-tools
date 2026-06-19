"""MCP 工具注册框架 — 云枢可调用的行动接口

我是云枢的"双手"——每个工具都是一项我能够执行的具体操作。
工具按照 MCP（Model Context Protocol）风格设计，拥有统一接口。
"""

import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)

# 全局工具注册表
_registry: dict[str, dict] = {}

# 操作追踪器（可选，由权限模块设置）
_action_tracker = None


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
    if name in _registry:
        del _registry[name]
        logger.info(f"工具注销: {name}")


def set_action_tracker(tracker):
    """设置操作追踪器（可选），用于记录工具调用历史

    Args:
        tracker: ActionTracker 实例，或 None 以清除追踪
    """
    global _action_tracker
    _action_tracker = tracker


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
    # 从 args 或 params 中提取工具名称，避免 name 关键字冲突
    name = args[0] if args else params.pop("name", None)
    if not name:
        raise ToolError("调用工具时缺少工具名称")

    tool = _registry.get(name)
    if not tool:
        raise ToolError(f"未知工具: '{name}'，可用工具: {list_tools()}")

    # 操作追踪（可选）
    if _action_tracker:
        target = str(params.get("path", params.get("url", params.get("target", ""))))
        _action_tracker.start_action(name, params, target)

    try:
        logger.info(f"调用工具: {name}, 参数: {params}")
        result = tool["handler"](**params)
        logger.info(f"工具返回: {name} → {str(result)[:200]}")

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
        logger.error(f"工具执行失败: {name} — {e}")

        # 操作追踪失败
        if _action_tracker:
            _action_tracker.finish_action("failed", str(e)[:200])

        raise ToolError(f"工具 '{name}' 执行失败: {e}") from e


def list_tools() -> list[dict]:
    """列出所有已注册的工具"""
    return [
        {"name": t["name"], "description": t["description"]}
        for t in _registry.values()
    ]


def get_tool_defs(whitelist: list[str] | None = None) -> list[dict]:
    """获取工具定义的 OpenAI/Anthropic 格式列表

    Args:
        whitelist: 允许返回的工具名称列表，None 表示全部

    Returns:
        OpenAI-compatible tool definitions list
    """
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
    logger.info(f"[工具] web_search 引擎 enum 已同步: {engine_names}")
    return True


def clear():
    """清空工具注册表（主要用于测试）"""
    _registry.clear()
