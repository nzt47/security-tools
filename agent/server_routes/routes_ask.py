"""E1-3a 结构化提问路由（/api/ask/*）

两条端点：
  · GET  /api/ask/pending?session_id=   未答问题（刷新/重启后恢复卡片）
  · POST /api/ask/answer                {session_id, question_id, answers[], custom}

回答只做三件事：校验 → 记台账（answers.jsonl）→ 清 pending，并把回答作为一条**普通用户
消息**落盘；返回 ``next_message`` 由前端用现有 ``sendMessage`` 续跑（与 plan/goal 同一模式）。
"""
from __future__ import annotations

import logging

from flask import jsonify, request

from agent.server_auth import require_token
from agent.server_routes.tracing_decorator import trace_route
from agent.server_routes._errors import safe_error as _safe

logger = logging.getLogger(__name__)


def _request_session_id() -> str:
    body = request.get_json(silent=True) or {}
    return str(body.get("session_id") or request.args.get("session_id") or "").strip()


def _next_message(question: str, answers: list, custom: str) -> str:
    parts = []
    if answers:
        parts.append("选项 " + "、".join(answers))
    if custom:
        parts.append("补充：" + custom)
    body = "；".join(parts) if parts else "（空）"
    return "回答（问题「%s」）：%s" % (question[:120], body)


def register_routes(app, state=None):
    from agent.context import ask as _ask

    @app.route("/api/ask/pending", methods=["GET"])
    @trace_route("AskPending")
    def api_ask_pending():
        """未答问题（无则 pending=null；只读）"""
        sid = _request_session_id()
        if not sid:
            return jsonify({"ok": False, "error": "session_id 不能为空"}), 400
        try:
            doc = _ask.get_pending(sid)
        except _ask.AskError as exc:
            return jsonify({"ok": False, "code": exc.code, "error": exc.message}), 400
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ask] 读取待答问题失败: %s", exc)
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500
        return jsonify({"ok": True, "pending": doc})

    @app.route("/api/ask/answer", methods=["POST"])
    @trace_route("AskAnswer")
    @require_token
    def api_ask_answer():
        """回答问题（答题台账 + 落一条用户消息；next_message 交前端续跑）"""
        sid = _request_session_id()
        if not sid:
            return jsonify({"ok": False, "error": "session_id 不能为空"}), 400
        body = request.get_json(silent=True) or {}
        qid = str(body.get("question_id") or "").strip()
        answers = body.get("answers") or []
        custom = str(body.get("custom") or "").strip()
        try:
            rec = _ask.answer(sid, qid, answers=answers, custom=custom)
        except _ask.AskError as exc:
            return jsonify({"ok": False, "code": exc.code, "error": exc.message}), 409
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ask] 记录回答失败: %s", exc)
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500
        next_message = _next_message(rec.get("question", ""), rec.get("answers") or [],
                                     rec.get("custom") or "")
        # 回答作为普通用户消息落盘（与手打一致：记忆/审计/上下文都走同一条链）。
        try:
            from app_server import _session_mgr as _sm
            _sm.add_message(sid, "user", next_message)
        except Exception as exc:  # noqa: BLE001 落盘失败不影响答题结果
            logger.warning("[ask] 回答落盘失败: %s", exc)
        return jsonify({
            "ok": True,
            "question_id": rec.get("question_id", ""),
            "answers": rec.get("answers") or [],
            "custom": rec.get("custom") or "",
            "next_message": next_message,
        })
