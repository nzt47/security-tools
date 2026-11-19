# -*- coding: utf-8 -*-
"""ui_guard 的 HTTP 面（面板 + 工具 + 拓扑状态源共用）。

    GET  /api/ui-guard/check    检查（?mode=fast|full&rule=a,b&limit=200）
    GET  /api/ui-guard/rules    规则清单（面板分组用）
    POST /api/ui-guard/fix      白名单修复（默认 dry_run；落盘需 confirm=true）
    GET  /api/ui-guard/health   状态源（系统监控拓扑采集）
"""
from __future__ import annotations

import logging

from flask import jsonify, request

from agent.server_auth import require_token
from agent.server_routes._errors import safe_error as _safe
from agent.server_routes.tracing_decorator import trace_route
from agent.ui_guard import engine, fixer

logger = logging.getLogger(__name__)

_RULES = [
    {"id": engine.RULE_BUTTON, "label": "Button 对齐", "fixable": True,
     "why": "满宽/flex-1 的 Button 基类居中；text-left 管不到 flex 子项"},
    {"id": engine.RULE_HEADING, "label": "标题字号 token", "fixable": True,
     "why": "标题字号必须写在标签上（h1 text-title / h2 text-ui / h3 text-body）"},
    {"id": engine.RULE_TABS, "label": "标题在 Tab 上面", "fixable": False,
     "why": "页头用 PageTabsHeader；标题落到 Tab 下面会与其它叶子不统一"},
    {"id": engine.RULE_RAW, "label": "原生控件", "fixable": False,
     "why": "用 ui/Button、ui/Input、ui/Select、ui/Textarea、ui/Slider"},
    {"id": engine.RULE_ARBITRARY_TEXT, "label": "任意值字号", "fixable": False,
     "why": "只用 9 档字号 token"},
    {"id": engine.RULE_RAW_HEADING, "label": "原生标题标签", "fixable": False,
     "why": "标题标签本身允许，但字号必须显式 text-*（见 heading.scale）"},
    {"id": engine.RULE_CSS_COLOR, "label": "CSS 硬编码颜色", "fixable": False,
     "why": "改用语义 token / 调色板类"},
]


def register_routes(app, state=None) -> None:
    @app.route("/api/ui-guard/check", methods=["GET"])
    @trace_route("UiGuard")
    def ui_guard_check():
        try:
            mode = (request.args.get("mode") or "fast").strip().lower()
            if mode not in ("fast", "full"):
                mode = "fast"
            raw_rule = (request.args.get("rule") or "").strip()
            rules = [r for r in raw_rule.split(",") if r] or None
            try:
                limit = int(request.args.get("limit") or 200)
            except ValueError:
                limit = 200
            report = engine.check(mode=mode, rules=rules, limit=max(0, min(limit, 1000)))
            return jsonify({"ok": True, "report": report.to_dict(limit=limit)})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500

    @app.route("/api/ui-guard/rules", methods=["GET"])
    @trace_route("UiGuard")
    def ui_guard_rules():
        return jsonify({"ok": True, "rules": _RULES, "fixable": list(engine.FIXABLE_RULES)})

    @app.route("/api/ui-guard/fix", methods=["POST"])
    @trace_route("UiGuard")
    @require_token
    def ui_guard_fix():
        try:
            data = request.get_json(silent=True) or {}
            dry_run = data.get("dry_run", True)
            dry_run = True if dry_run is None else bool(dry_run)
            if not dry_run and not bool(data.get("confirm")):
                return jsonify({"ok": False, "error": "落盘需要 confirm=true（先用 dry_run 看 diff）"}), 400
            rules = data.get("rules")
            files = data.get("files")
            result = fixer.fix(
                dry_run=dry_run,
                rules=[str(r) for r in rules] if isinstance(rules, list) else None,
                files=[str(f) for f in files] if isinstance(files, list) else None,
                max_files=int(data.get("max_files") or 20),
            )
            return jsonify({"ok": True, "result": result})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500

    @app.route("/api/ui-guard/health", methods=["GET"])
    @trace_route("UiGuard")
    def ui_guard_health():
        try:
            return jsonify(engine.health())
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500
