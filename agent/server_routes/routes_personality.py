"""人格配置 API 路由"""
import logging
from flask import request, jsonify
from agent.server_auth import require_token, log_request

logger = logging.getLogger(__name__)


def register_routes(app, state):
    """注册所有人格配置路由"""

    personality_mgr = state.personality_mgr

    @app.route("/api/personality", methods=["GET"])
    @log_request(show_response=False)
    def api_personality_get():
        return jsonify(personality_mgr.get())

    @app.route("/api/personality/params", methods=["POST"])
    @require_token
    @log_request()
    def api_personality_params():
        data = request.get_json() or {}
        params = data.get("params", {})
        result = personality_mgr.update_params(params)
        return jsonify(result)

    @app.route("/api/personality/profile", methods=["POST"])
    @require_token
    @log_request()
    def api_personality_profile():
        data = request.get_json() or {}
        profile = data.get("profile", "")
        result = personality_mgr.apply_profile(profile)
        return jsonify(result)

    @app.route("/api/personality/reset", methods=["POST"])
    @require_token
    @log_request()
    def api_personality_reset():
        result = personality_mgr.reset()
        return jsonify(result)
