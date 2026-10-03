# plugins/plugin_api.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from flask import Blueprint

@dataclass
class Plugin:
    name: str
    version: str
    description: str = ""
    schema: Dict[str, Any] = field(default_factory=dict)
    blueprint: Optional[Blueprint] = None
    routes: List[str] = field(default_factory=list)
    submit_url: str = ""  # 配置提交端点（T3.3）：空串表示「暂不支持在线修改」
    client_slot: Optional[Dict[str, str]] = None  # 前端动态装载（T4.2）：{slotId, module}

_REGISTRY: List[Plugin] = []

def _validate_schema(name: str, schema: Any) -> None:
    """校验 Plugin.schema（JSON Schema 子集协议，见 docs/yunshu-pluginization/PLAN-3-schema-ui.md §2）。

    - schema 必须为 dict 或 None；
    - 非空 dict 顶层必须声明 type == "object"；
    - 空 dict（默认占位）与 None 视为「未声明配置」，合法；
    - 非法时抛 ValueError（开发期早失败）。
    """
    if schema is None:
        return
    if not isinstance(schema, dict):
        raise ValueError(f"plugin {name}: invalid schema")
    if schema and schema.get("type") != "object":
        raise ValueError(f"plugin {name}: invalid schema")

def register_plugin(plugin: Plugin) -> Plugin:
    _validate_schema(plugin.name, plugin.schema)
    if not any(p.name == plugin.name for p in _REGISTRY):
        _REGISTRY.append(plugin)
    return plugin

def get_plugins() -> List[Plugin]:
    return list(_REGISTRY)


# ════════════════════════════════════════════════════════════
#  上下文窗口读数（**单一口径**，供各 plugin 共用）
# ════════════════════════════════════════════════════════════

def context_limit_info(yunshu) -> Dict[str, Any]:
    """读取**编排窗口上限**及其来源。

    单一事实源是 ``DigitalLife.context_limit_info()``（LifecycleManager 初始化时写入
    ``_memory_token_limit`` / ``_memory_token_limit_source``，见
    agent/orchestrator/lifecycle_manager.py:283-299）。

    【为什么放在这里】2026-10-03 复核发现本逻辑被**抄了三份**：
    plugins/chat.py、plugins/memory.py 各一份（同口径），plugins/status.py 干脆写成
    ``_cfg.get("memory", "token_limit", default=4096)`` —— 第三份会在取不到配置时
    冒出 4096 这个**假分母**，于是同一个占用在不同面板显示不同占比。
    同一口径写三遍必然漂移，故上收到 plugin_api（plugins 的公共叶子模块）。

    【不易】取不到时返回 ``limit_tokens=None`` + ``limit_source="unavailable"``，
    **绝不**回退到 4096 之类的硬编码值 —— 那比没有读数更坏：它看起来像个可信的数，
    然后所有"占比"结论都建在假分母上。
    """
    getter = getattr(yunshu, "context_limit_info", None)
    if callable(getter):
        try:
            info = getter()
        except Exception:  # noqa: BLE001 读数不得炸请求
            info = None
        if isinstance(info, dict):
            limit = info.get("limit_tokens")
            source = info.get("limit_source")
            if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
                return {
                    "limit_tokens": int(limit),
                    "limit_source": (
                        source if isinstance(source, str) and source
                        else "unavailable"
                    ),
                }
    return {"limit_tokens": None, "limit_source": "unavailable"}

def manifest() -> Dict[str, Any]:
    import sys, flask
    return {
        "plugins": [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "schema": p.schema or {},  # 统一约定：无 schema 输出为空 dict
                "submit_url": p.submit_url,  # 配置提交端点（T3.3）；空串 = 不支持在线修改
                "client_slot": p.client_slot,  # 前端动态装载（T4.2）；None = 无客户端模块
                "routes": sorted(p.routes),
            }
            for p in _REGISTRY
        ],
        "host": {"python": sys.version.split()[0], "flask": flask.__version__},
    }
