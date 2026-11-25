"""E1-2d Goal 路由（/api/goal/*）—— GoalBar 的读侧与三个用户操作。

三条端点：
  · GET  /api/goal/state?session_id=   目标状态 + 最近事件（只读）
  · POST /api/goal/mode                {session_id, action: pause|resume|clear}
  · POST /api/goal/accept              {session_id, item}

【为什么与 /api/plan-mode/* 分开】语义不同（目标 vs 工作方式），族分开后
route_ui_intent / route_domains 的标注与后来人都不会混。写端点一律 ``@require_token``。
"""
from __future__ import annotations

import logging

from flask import jsonify, request

from agent.server_auth import require_token
from agent.server_routes.tracing_decorator import trace_route
from agent.server_routes._errors import safe_error as _safe

logger = logging.getLogger(__name__)

_MODE_ACTIONS = ("pause", "resume", "clear")


def _flatten(state: dict) -> dict:
    return {
        "ok": True,
        "session_id": state.get("session_id", ""),
        "goal_id": state.get("goal_id", ""),
        "title": state.get("title", ""),
        "why": state.get("why", ""),
        "acceptance": state.get("acceptance", []),
        "state": state.get("state", "idle"),
        "round": int(state.get("round") or 0),
        "max_rounds": int(state.get("max_rounds") or 0),
        "blocked": state.get("blocked"),
        "evidence": state.get("evidence", ""),
        "pause_reason": state.get("pause_reason", ""),
        "recent_events": state.get("recent_events", []),
        "created_at": state.get("created_at", ""),
        "updated_at": state.get("updated_at", ""),
    }


def _request_session_id() -> str:
    body = request.get_json(silent=True) or {}
    return str(body.get("session_id") or request.args.get("session_id") or "").strip()


def register_routes(app, state=None):
    from agent.context import goal as _goal

    @app.route("/api/goal/state", methods=["GET"])
    @trace_route("GoalState")
    def api_goal_state():
        """目标状态 + 最近事件（只读；无目标时安全返回 idle）"""
        sid = _request_session_id()
        if not sid:
            return jsonify({"ok": False, "error": "session_id 不能为空"}), 400
        try:
            return jsonify(_flatten(_goal.get_state(sid)))
        except _goal.GoalError as exc:
            return jsonify({"ok": False, "code": exc.code, "error": exc.message}), 400
        except Exception as exc:  # noqa: BLE001
            logger.warning("[goal] 读取状态失败: %s", exc)
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500

    @app.route("/api/goal/mode", methods=["POST"])
    @trace_route("GoalSetMode")
    @require_token
    def api_goal_set_mode():
        """暂停 / 恢复 / 清除 {session_id, action}（用户侧操作，不进 LLM）"""
        sid = _request_session_id()
        if not sid:
            return jsonify({"ok": False, "error": "session_id 不能为空"}), 400
        body = request.get_json(silent=True) or {}
        action = str(body.get("action") or "").strip().lower()
        reason = str(body.get("reason") or "").strip()
        if action not in _MODE_ACTIONS:
            return jsonify({"ok": False,
                            "error": "action 必须是 %s 之一" % "/".join(_MODE_ACTIONS)}), 400
        try:
            if action == "pause":
                st = _goal.pause(sid, reason)
            elif action == "resume":
                st = _goal.resume(sid)
            else:
                st = _goal.clear(sid)
        except _goal.GoalError as exc:
            return jsonify({"ok": False, "code": exc.code, "error": exc.message}), 409
        except Exception as exc:  # noqa: BLE001
            logger.warning("[goal] 操作失败: %s", exc)
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500
        payload = _flatten(st)
        payload["action"] = action
        return jsonify(payload)

    @app.route("/api/goal/accept", methods=["POST"])
    @trace_route("GoalAccept")
    @require_token
    def api_goal_accept():
        """追加一条验收条件 {session_id, item}"""
        sid = _request_session_id()
        if not sid:
            return jsonify({"ok": False, "error": "session_id 不能为空"}), 400
        body = request.get_json(silent=True) or {}
        item = str(body.get("item") or "").strip()
        try:
            st = _goal.accept(sid, item)
        except _goal.GoalError as exc:
            return jsonify({"ok": False, "code": exc.code, "error": exc.message}), 409
        except Exception as exc:  # noqa: BLE001
            logger.warning("[goal] 追加验收条件失败: %s", exc)
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500
        return jsonify(_flatten(st))
