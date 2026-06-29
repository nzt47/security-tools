"""人格配置 API 路由"""
import logging
import json
import uuid
from flask import request, jsonify
from agent.server_auth import require_token, log_request
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



def register_routes(app, state):
    """注册所有人格配置路由"""

    personality_mgr = state.personality_mgr

    @app.route("/api/personality", methods=["GET"])
    @trace_route("Personality")
    @log_request(show_response=False)
    def api_personality_get():
        return jsonify(personality_mgr.get())

    @app.route("/api/personality/params", methods=["POST"])
    @trace_route("Personality")
    @require_token
    @log_request()
    def api_personality_params():
        data = request.get_json() or {}
        params = data.get("params", {})
        result = personality_mgr.update_params(params)
        return jsonify(result)

    @app.route("/api/personality/profile", methods=["POST"])
    @trace_route("Personality")
    @require_token
    @log_request()
    def api_personality_profile():
        data = request.get_json() or {}
        profile = data.get("profile", "")
        result = personality_mgr.apply_profile(profile)
        return jsonify(result)

    @app.route("/api/personality/reset", methods=["POST"])
    @trace_route("Personality")
    @require_token
    @log_request()
    def api_personality_reset():
        result = personality_mgr.reset()
        return jsonify(result)


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
            "module_name": "routes_personality",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
