"""E1-1 计划模式路由（/api/plan-mode/*）

三条端点，对应 UI 的三件事：读状态、开/关模式、对计划做决策。

【为什么不用 /api/plan】``/api/planning/*`` 属于既有的 planning 引擎（开关、
checkpoint 迁移），``PLAN_MODE`` 是 DSH 对齐的工作方式开关，两者语义不同；
同名族会让"路由覆盖图"与后来人都分不清谁是谁。

【为什么 GET 不挂令牌】计划状态是只读、与会话同权限的界面数据；主 UI 在未配置
UI 令牌时也要能回填计划卡（否则刷新后卡片消失）。两个写端点一律 ``@require_token``。
"""
from __future__ import annotations

import logging

from flask import jsonify, request

from agent.server_auth import require_token
from agent.server_routes.tracing_decorator import trace_route
from agent.server_routes._errors import safe_error as _safe

logger = logging.getLogger(__name__)

#: 决策后前端应当发送的用户消息（服务端不自己发起模型轮，复用普通对话链路）
_NEXT_MESSAGE = {
    "approve": "计划已批准，开始按计划执行。",
    "reject": "计划未通过，请根据意见继续规划。",
}


def _flatten(state: dict) -> dict:
    """状态 → 扁平响应（工作台设置页同款扁平口径，无 {code,data} 信封）"""
    return {
        "ok": True,
        "session_id": state.get("session_id", ""),
        "plan_mode": bool(state.get("plan_mode")),
        "status": state.get("status", "idle"),
        "version": int(state.get("version") or 0),
        "plan": state.get("plan", ""),
        "revisions": state.get("revisions", []),
        "feedback": state.get("feedback", []),
        "created_at": state.get("created_at", ""),
        "updated_at": state.get("updated_at", ""),
    }


def _next_message_for(action: str, feedback) -> str | None:
    """决策之后前端应发送的用户消息（复用普通对话链路，不造第二条 LLM 通道）"""
    note = str(feedback or "").strip()
    act = str(action or "").strip().lower()
    if act == "approve":
        return _NEXT_MESSAGE["approve"]
    if act == "reject":
        return ("计划未通过。请根据以下意见继续规划：\n" + note) if note else _NEXT_MESSAGE["reject"]
    if act == "feedback":
        return note or None
    return None


def _request_session_id() -> str:
    body = request.get_json(silent=True) or {}
    return str(body.get("session_id") or request.args.get("session_id") or "").strip()


def register_routes(app, state=None):
    from agent.context import plan_mode

    @app.route("/api/plan-mode/state", methods=["GET"])
    @trace_route("PlanModeState")
    def api_plan_mode_state():
        """计划模式状态 + 最新计划全文 + 版本列表（只读）"""
        sid = _request_session_id()
        if not sid:
            return jsonify({"ok": False, "error": "session_id 不能为空"}), 400
        try:
            return jsonify(_flatten(plan_mode.get_state(sid)))
        except plan_mode.PlanModeError as exc:
            return jsonify({"ok": False, "code": exc.code, "error": exc.message}), 400
        except Exception as exc:  # noqa: BLE001
            logger.warning("[plan-mode] 读取状态失败: %s", exc)
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500

    @app.route("/api/plan-mode/mode", methods=["POST"])
    @trace_route("PlanModeSetMode")
    @require_token
    def api_plan_mode_set_mode():
        """进入 / 退出计划模式 {session_id, enabled: bool}"""
        sid = _request_session_id()
        if not sid:
            return jsonify({"ok": False, "error": "session_id 不能为空"}), 400
        body = request.get_json(silent=True) or {}
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            return jsonify({"ok": False, "error": "enabled 必须是布尔值"}), 400
        try:
            result = plan_mode.enter(sid) if enabled else plan_mode.exit(sid)
            return jsonify(_flatten(result))
        except plan_mode.PlanModeError as exc:
            return jsonify({"ok": False, "code": exc.code, "error": exc.message}), 400
        except Exception as exc:  # noqa: BLE001
            logger.warning("[plan-mode] 切换模式失败: %s", exc)
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500

    @app.route("/api/plan-mode/decision", methods=["POST"])
    @trace_route("PlanModeDecision")
    @require_token
    def api_plan_mode_decision():
        """批准 / 拒绝 / 反馈 / 回滚 {session_id, action, feedback?, version?}"""
        sid = _request_session_id()
        if not sid:
            return jsonify({"ok": False, "error": "session_id 不能为空"}), 400
        body = request.get_json(silent=True) or {}
        action = str(body.get("action") or "").strip().lower()
        feedback = body.get("feedback")
        try:
            version = int(body.get("version") or 0) or None
        except (TypeError, ValueError):
            version = None
        try:
            result = plan_mode.decide(sid, action, feedback=feedback, version=version)
        except plan_mode.PlanModeError as exc:
            return jsonify({"ok": False, "code": exc.code, "error": exc.message}), 409
        except Exception as exc:  # noqa: BLE001
            logger.warning("[plan-mode] 决策失败: %s", exc)
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500
        payload = _flatten(result)
        payload["next_message"] = _next_message_for(action, feedback)
        logger.info("[plan-mode] 决策 session=%s action=%s -> %s v%d",
                    sid, action, payload["status"], payload["version"])
        return jsonify(payload)
