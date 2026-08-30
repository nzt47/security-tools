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

def manifest() -> Dict[str, Any]:
    import sys, flask
    return {
        "plugins": [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "schema": p.schema or {},  # 统一约定：无 schema 输出为空 dict
                "routes": sorted(p.routes),
            }
            for p in _REGISTRY
        ],
        "host": {"python": sys.version.split()[0], "flask": flask.__version__},
    }
