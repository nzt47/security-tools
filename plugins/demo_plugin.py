# plugins/demo_plugin.py
"""演示插件（任务 T4.2：前端运行时发现 + 动态装载）。

- 丢进 plugins/ 目录即被 loader 扫描发现（无需改任何代码）；
- schema 自解释：前端插件中心用 SchemaRenderer 自动渲染配置表单；
- submit_url：GET /api/demo/config 读当前值 / POST 应用（schema 驱动闭环）；
- client_slot：声明前端可动态装载的客户端模块（进阶演示：前端点「加载 UI」后
  用 import() 拉取 public/plugins/demo-ui.js 并挂入 panels 插槽）。

模块级副作用仅含 Blueprint / 常量（loader reload 安全，无线程无网络）。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from .plugin_api import Plugin, register_plugin

bp = Blueprint("demo", __name__)

# 演示配置（进程内；schema 驱动面板的读写目标）
_demo_config = {
    "greeting": "你好，云枢",
    "show_badge": True,
    "poll_interval": 30,
}


@bp.route("/api/demo/probe")
def demo_probe():
    """演示插件自证路由（刷新后 /api/plugins 的 routes 可见）。"""
    return jsonify({"plugin": "demo", "ok": True, "config": dict(_demo_config)})


@bp.route("/api/demo/config", methods=["GET"])
def demo_config_get():
    """读取当前生效配置（schema 驱动面板的值预填）。"""
    return jsonify(dict(_demo_config))


@bp.route("/api/demo/config", methods=["POST"])
def demo_config_post():
    """应用配置（只接受 schema 已声明的字段）。"""
    payload = request.get_json(silent=True) or {}
    allowed = {"greeting", "show_badge", "poll_interval"}
    applied = {}
    for key in allowed:
        if key in payload:
            _demo_config[key] = payload[key]
            applied[key] = payload[key]
    return jsonify({"ok": True, "applied": applied, "config": dict(_demo_config)})


PLUGIN = register_plugin(Plugin(
    name="demo",
    version="1.0.0",
    description="动态装载演示：新增即发现，Schema 面板 + 前端动态 UI（T4.2）",
    schema={
        "type": "object",
        "title": "Demo 插件配置",
        "description": "演示「新增插件 → 刷新 → Schema 面板 → 加载 UI」全链路",
        "properties": {
            "greeting": {
                "type": "string",
                "title": "问候语",
                "default": "你好，云枢",
            },
            "show_badge": {
                "type": "boolean",
                "title": "显示徽标",
                "default": True,
            },
            "poll_interval": {
                "type": "integer",
                "title": "轮询间隔（秒）",
                "minimum": 1,
                "maximum": 3600,
                "default": 30,
            },
        },
        "required": ["greeting"],
    },
    blueprint=bp,
    submit_url="/api/demo/config",  # T3.3：schema 驱动提交端点
    client_slot={  # T4.2：前端动态装载声明
        "slotId": "panels",       # 挂入的目标插槽
        "module": "/plugins/demo-ui.js",  # Vite public/ 下的客户端模块
    },
    routes=["/api/demo/probe", "/api/demo/config"],
))
