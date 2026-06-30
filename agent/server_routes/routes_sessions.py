"""会话管理 API 路由"""
import logging
import json
import uuid
from flask import request, jsonify
from agent.server_auth import require_token, log_request, _API_TOKEN_ENABLED
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



def _get_current_session_id(session_mgr):
    """获取当前会话 ID，如无则创建新会话"""
    session_id = session_mgr.get_current_id()
    if not session_id:
        session = session_mgr.create_session("新会话")
        session_id = session["id"]
    return session_id


def register_routes(app, state):
    """注册所有会话管理路由"""

    session_mgr = state.session_mgr
    chat_history = state.chat_history

    # ── 会话列表 ──

    @app.route("/api/sessions", methods=["GET"])
    @trace_route("Sessions")
    def api_sessions_list():
        sessions = session_mgr.list_sessions()
        current_id = session_mgr.get_current_id()
        return jsonify({
            "sessions": sessions,
            "current_id": current_id,
        })

    @app.route("/api/sessions", methods=["POST"])
    @trace_route("Sessions")
    def api_sessions_create():
        data = request.get_json() or {}
        title = data.get("title", "")
        session = session_mgr.create_session(title=title)
        logger.info("通过 Web 界面创建新会话: %s", session["id"])
        return jsonify(session), 201

    @app.route("/api/sessions/<session_id>", methods=["DELETE"])
    @trace_route("Sessions")
    @require_token
    def api_sessions_delete(session_id):
        if session_mgr.delete_session(session_id):
            if session_id == session_mgr.get_current_id():
                chat_history.clear()
            return jsonify({"ok": True})
        return jsonify({"error": "会话不存在"}), 404

    @app.route("/api/sessions/<session_id>/rename", methods=["PUT"])
    @trace_route("Sessions")
    @require_token
    def api_sessions_rename(session_id):
        data = request.get_json() or {}
        title = data.get("title", "")
        if not title:
            return jsonify({"error": "标题不能为空"}), 400
        if session_mgr.rename_session(session_id, title):
            return jsonify({"ok": True})
        return jsonify({"error": "会话不存在"}), 404

    @app.route("/api/sessions/current", methods=["POST"])
    @trace_route("Sessions")
    @require_token
    def api_sessions_set_current():
        data = request.get_json() or {}
        session_id = data.get("session_id", "")
        if not session_id:
            return jsonify({"error": "session_id 不能为空"}), 400
        if session_mgr.set_current(session_id):
            chat_history.clear()
            messages = session_mgr.get_messages(session_id, limit=50)
            for i in range(0, len(messages), 2):
                user_msg = messages[i]
                assistant_msg = messages[i + 1] if i + 1 < len(messages) else {}
                if user_msg.get("role") == "user":
                    chat_history.append({
                        "user": user_msg.get("content", ""),
                        "Yunshu": assistant_msg.get("content", ""),
                        "mode": "normal",
                        "timestamp": user_msg.get("timestamp", ""),
                    })
            return jsonify({"ok": True})
        return jsonify({"error": "会话不存在"}), 404

    @app.route("/api/sessions/<session_id>/messages", methods=["GET"])
    @trace_route("Sessions")
    def api_sessions_messages(session_id):
        limit = request.args.get("limit", 50, type=int)
        messages = session_mgr.get_messages(session_id, limit=limit)
        return jsonify(messages)

    # ── 历史记录 ──

    @app.route("/api/history")
    @trace_route("Sessions")
    @log_request(show_response=False)
    def api_history():
        session_id = request.args.get("session") or _get_current_session_id(session_mgr)
        total_count = session_mgr.get_message_count(session_id)
        messages = session_mgr.get_messages(session_id, limit=50)
        offset = max(0, total_count - len(messages))
        result = []
        for i in range(0, len(messages), 2):
            user_msg = messages[i]
            assistant_msg = messages[i + 1] if i + 1 < len(messages) else {}
            if user_msg.get("role") == "user":
                result.append({
                    "user": user_msg.get("content", ""),
                    "Yunshu": assistant_msg.get("content", ""),
                    "mode": "normal",
                    "timestamp": user_msg.get("timestamp", ""),
                    "_real_index": (offset + i) // 2,
                })
        return jsonify(result)

    @app.route("/api/clear", methods=["POST"])
    @trace_route("Sessions")
    @require_token
    @log_request()
    def api_clear():
        session_id = request.args.get("session") or _get_current_session_id(session_mgr)
        session_mgr.clear_messages(session_id)
        chat_history.clear()
        return jsonify({"ok": True})

    @app.route("/api/auth/token-check")
    @trace_route("Sessions")
    @log_request(show_response=False)
    def api_auth_token_check():
        return jsonify({"enabled": _API_TOKEN_ENABLED, "valid": True})

    @app.route("/api/history/search")
    @trace_route("Sessions")
    @log_request(show_response=False)
    def api_history_search():
        q = request.args.get("q", "").strip().lower()
        session_id = request.args.get("session") or _get_current_session_id(session_mgr)
        messages = session_mgr.get_messages(session_id, limit=500)
        if not q:
            return jsonify(messages[-50:])
        results = [
            {"index": i, **m}
            for i, m in enumerate(messages)
            if m.get("role") == "user" and q in m.get("content", "").lower()
            or m.get("role") == "assistant" and q in m.get("content", "").lower()
        ]
        return jsonify(results)

    @app.route("/api/history/<int:index>", methods=["DELETE"])
    @trace_route("Sessions")
    @require_token
    @log_request()
    def api_history_delete(index):
        session_id = request.args.get("session") or _get_current_session_id(session_mgr)
        messages = session_mgr.get_messages(session_id, limit=1000)
        msg_idx = index * 2
        if msg_idx >= len(messages):
            return jsonify({"ok": False, "error": "索引超出范围"}), 404
        if msg_idx + 1 < len(messages):
            messages.pop(msg_idx + 1)
        messages.pop(msg_idx)
        session_mgr.clear_messages(session_id)
        for msg in messages:
            session_mgr.add_message(
                session_id,
                msg.get("role", "user"),
                msg.get("content", ""),
                tool_calls=msg.get("tool_calls"),
                tool_steps=msg.get("tool_steps"),
                reasoning=msg.get("reasoning"),
            )
        if session_id == session_mgr.get_current_id():
            new_messages = session_mgr.get_messages(session_id, limit=50)
            chat_history.clear()
            for i in range(0, len(new_messages), 2):
                user_msg = new_messages[i]
                assistant_msg = new_messages[i + 1] if i + 1 < len(new_messages) else {}
                if user_msg.get("role") == "user":
                    chat_history.append({
                        "user": user_msg.get("content", ""),
                        "Yunshu": assistant_msg.get("content", ""),
                        "mode": "normal",
                        "timestamp": user_msg.get("timestamp", ""),
                    })
        return jsonify({"ok": True})


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "routes_sessions",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
