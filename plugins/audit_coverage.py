# -*- coding: utf-8 -*-
"""路由↔UI 覆盖盘点：只读端点（2026-10-07）。

读 scripts/audit/route_ui_coverage.py 生成的**提交产物** reports/route_ui_coverage.json，
经统一信封返回。**不在请求期重算**：那是一次全树扫描（路由 AST + 前端数百文件），
放进 HTTP 热路径既慢又没必要；产物由脚本与守卫保证新鲜（--check 比对 content_hash）。

【为什么是一个独立插件文件】plugins/loader.py 目录扫描自动加载（T4.1）：
新增本文件即生效，无需改 app_server.py 的任何清单 —— 与「新功能零接线」同向。
"""
import json
import os

from flask import Blueprint

from .plugin_api import Plugin, register_plugin, require_auth as _require_token
from agent.api_envelope import ok as _ok, problem as _problem

bp = Blueprint("audit_coverage", __name__)

_ARTIFACT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "reports", "route_ui_coverage.json",
)


@bp.route("/api/audit/route-ui-coverage")
@_require_token
def api_route_ui_coverage():
    """路由↔UI 覆盖盘点（只读，读提交产物）。"""
    try:
        with open(_ARTIFACT, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        return _problem(404, detail="盘点产物不存在；请运行 python scripts/audit/route_ui_coverage.py")
    except Exception as e:  # noqa: BLE001
        return _problem(500, detail="读取盘点产物失败：" + str(e)[:200])
    return _ok(payload)


PLUGIN = register_plugin(Plugin(
    name="audit_coverage",
    version="1.0.0",
    description="路由↔UI 覆盖盘点（只读；数据源 reports/route_ui_coverage.json）",
    blueprint=bp,
))
