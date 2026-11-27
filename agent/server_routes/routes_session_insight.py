"""E1-4c 会话智能路由：轮次大纲 / 每轮统计 / 导出 /（E4-5）轨迹

四条只读端点（都按 `session_id` 取会话，**不新增采集**，全部现算）：
  · GET /api/sessions/<session_id>/outline        → 每轮：用户问题 / 工具名 / 结论一句
  · GET /api/sessions/<session_id>/stats          → 每轮 tokens/工具数与汇总
  · GET /api/sessions/<session_id>/export?format=md|zip → 文件流（文件名 sha256 派生）
  · GET /api/sessions/<session_id>/trajectory     → E4-5 Inspector：消息/步骤只读投影（默认关）

【为什么文件名要派生】会话 ID 来自客户端（`sess_...` / 自定义），直接拼进
`Content-Disposition` 会有引号/控制字符注入面；这里只用 `sha256(sid)[:12]`。
"""
from __future__ import annotations

import logging
import os

from flask import Response, jsonify, request

from agent.server_routes.tracing_decorator import trace_route
from agent.server_routes._errors import safe_error as _safe

logger = logging.getLogger(__name__)

EXPORT_FORMATS = ("md", "zip")

#: E4-5 Inspector 开关（默认关；判据只在这一处与 registry 登记项）
TRAJECTORY_ENABLED_ENV = "CP_INSPECTOR_ENABLED"


def _trajectory_enabled() -> bool:
    return str(os.environ.get(TRAJECTORY_ENABLED_ENV, "") or "").strip().lower() in (
        "1", "true", "yes", "on")


def register_routes(app, state=None):
    from agent.context import session_insight as _insight
    from agent.context import trajectory as _trajectory

    def _mgr():
        from app_server import _session_mgr

        return _session_mgr

    def _load(session_id: str):
        """取 (meta, messages)；会话不存在回 (None, None)。"""
        mgr = _mgr()
        meta = mgr.get_session_metadata(session_id)
        if not meta:
            return None, None
        messages = mgr.get_messages(session_id, limit=_insight.MAX_ROUNDS * 8)
        return meta, messages

    @app.route("/api/sessions/<session_id>/outline", methods=["GET"])
    @trace_route("SessionOutline")
    def api_session_outline(session_id):
        """轮次大纲（只读）"""
        try:
            meta, messages = _load(session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session-insight] 读会话失败: %s", exc)
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500
        if meta is None:
            return jsonify({"ok": False, "error": "会话不存在"}), 404
        doc = _insight.outline(messages)
        return jsonify({"ok": True, "session_id": session_id,
                        "title": meta.get("title", ""), **doc})

    @app.route("/api/sessions/<session_id>/stats", methods=["GET"])
    @trace_route("SessionStats")
    def api_session_stats(session_id):
        """每轮统计 + 汇总（只读）"""
        try:
            meta, messages = _load(session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session-insight] 读会话失败: %s", exc)
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500
        if meta is None:
            return jsonify({"ok": False, "error": "会话不存在"}), 404
        doc = _insight.stats(messages)
        return jsonify({"ok": True, "session_id": session_id,
                        "message_count": meta.get("message_count", len(messages or [])),
                        **doc})

    @app.route("/api/sessions/<session_id>/trajectory", methods=["GET"])
    @trace_route("SessionTrajectory")
    def api_session_trajectory(session_id):
        """E4-5 Inspector：轨迹只读投影（默认关；开关关时不读会话）"""
        if not _trajectory_enabled():
            return jsonify({"ok": False, "code": "disabled",
                            "error": "轨迹查看未启用（CP_INSPECTOR_ENABLED=0）"})
        try:
            meta, messages = _load(session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session-insight] 读会话失败: %s", exc)
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500
        if meta is None:
            return jsonify({"ok": False, "error": "会话不存在"}), 404
        doc = _trajectory.build(messages, limit=request.args.get("limit"),
                                offset=request.args.get("offset"))
        return jsonify({"ok": True, "session_id": session_id,
                        "title": meta.get("title", ""), **doc})

    @app.route("/api/sessions/<session_id>/export", methods=["GET"])
    @trace_route("SessionExport")
    def api_session_export(session_id):
        """导出会话（md 文本 / zip 包；文件名 sha256 派生）"""
        fmt = str(request.args.get("format") or "md").strip().lower()
        if fmt not in EXPORT_FORMATS:
            return jsonify({"ok": False, "error": "format 只支持 md | zip"}), 400
        try:
            meta, messages = _load(session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session-insight] 读会话失败: %s", exc)
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500
        if meta is None:
            return jsonify({"ok": False, "error": "会话不存在"}), 404
        filename = _insight.export_filename(session_id, fmt)
        try:
            if fmt == "md":
                payload = _insight.to_markdown(meta, messages)
                resp = Response(payload, mimetype="text/markdown; charset=utf-8")
            else:
                payload = _insight.to_zip(meta, messages)
                resp = Response(payload, mimetype="application/zip")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session-insight] 导出失败: %s", exc)
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500
        resp.headers["Content-Disposition"] = 'attachment; filename="%s"' % filename
        resp.headers["Cache-Control"] = "no-store"
        return resp
