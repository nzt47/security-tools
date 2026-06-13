"""MCP 工具注册框架 — 云枢可调用的行动接口

我是云枢的"双手"——每个工具都是一项我能够执行的具体操作。
工具按照 MCP（Model Context Protocol）风格设计，拥有统一接口。
"""

import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)

# 全局工具注册表
_registry: dict[str, dict] = {}


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
    try:
        logger.info(f"调用工具: {name}, 参数: {params}")
        result = tool["handler"](**params)
        logger.info(f"工具返回: {name} → {str(result)[:200]}")
        return result
    except Exception as e:
        logger.error(f"工具执行失败: {name} — {e}")
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


def clear():
    """清空工具注册表（主要用于测试）"""
    _registry.clear()
