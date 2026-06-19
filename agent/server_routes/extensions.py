"""扩展系统 API 路由"""

from flask import request, jsonify
from agent.server_auth import require_token, log_request


def register_routes(app, state):
    """注册所有扩展管理路由"""

    _ext_mgr = state.extension_mgr
    _ext_market = state.extension_market

    # ═══════════════════════════════════════════════════
    #  扩展 CRUD
    # ═══════════════════════════════════════════════════

    @app.route("/api/extensions/list", methods=["GET"])
    @require_token
    @log_request(show_response=False)
    def api_extensions_list():
        try:
            ext_type = request.args.get("type")
            result = _ext_mgr.list_all(ext_type)
            return jsonify({"ok": True, "extensions": result})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/extensions/installed", methods=["GET"])
    @require_token
    @log_request(show_response=False)
    def api_extensions_installed():
        try:
            result = _ext_mgr.get_installed_by_type()
            return jsonify({"ok": True, **result})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/extensions/install", methods=["POST"])
    @require_token
    @log_request()
    def api_extensions_install():
        try:
            data = request.get_json() or {}
            ext_type = data.get("type", "")
            source = data.get("source", data.get("id", ""))
            kwargs = data.get("params", {})
            if not ext_type or not source:
                return jsonify({"ok": False, "error": "缺少 type 或 source/id"}), 400
            result = _ext_mgr.install(ext_type, source, **kwargs)
            return jsonify(result)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/extensions/uninstall", methods=["POST"])
    @require_token
    @log_request()
    def api_extensions_uninstall():
        try:
            data = request.get_json() or {}
            ext_type = data.get("type", "")
            ext_id = data.get("id", "")
            if not ext_type or not ext_id:
                return jsonify({"ok": False, "error": "缺少 type 或 id"}), 400
            result = _ext_mgr.uninstall(ext_type, ext_id)
            return jsonify(result)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/extensions/toggle", methods=["POST"])
    @require_token
    @log_request()
    def api_extensions_toggle():
        try:
            data = request.get_json() or {}
            ext_type = data.get("type", "")
            ext_id = data.get("id", "")
            enabled = data.get("enabled")
            if not ext_type or not ext_id:
                return jsonify({"ok": False, "error": "缺少 type 或 id"}), 400
            result = _ext_mgr.toggle(ext_type, ext_id, enabled)
            return jsonify(result)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/extensions/configure", methods=["POST"])
    @require_token
    @log_request()
    def api_extensions_configure():
        try:
            data = request.get_json() or {}
            ext_type = data.get("type", "")
            ext_id = data.get("id", "")
            config = data.get("config", {})
            if not ext_type or not ext_id:
                return jsonify({"ok": False, "error": "缺少 type 或 id"}), 400
            result = _ext_mgr.configure(ext_type, ext_id, config)
            return jsonify(result)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/extensions/discover", methods=["GET"])
    @require_token
    @log_request(show_response=False)
    def api_extensions_discover():
        try:
            result = _ext_mgr.discover_all()
            return jsonify({"ok": True, **result})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  扩展市场
    # ═══════════════════════════════════════════════════

    @app.route("/api/extensions/market/search", methods=["GET"])
    @require_token
    @log_request(show_response=False)
    def api_extensions_market_search():
        try:
            query = request.args.get("q", "")
            ext_type = request.args.get("type")
            include_github = request.args.get("github", "true").lower() == "true"
            result = _ext_market.search_all(query, ext_type, include_github)
            return jsonify({"ok": True, **result})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/extensions/market/recommend", methods=["GET"])
    @require_token
    @log_request(show_response=False)
    def api_extensions_market_recommend():
        try:
            ext_type = request.args.get("type")
            limit = request.args.get("limit", 5, type=int)
            result = _ext_market.get_recommendations(ext_type, limit)
            return jsonify({"ok": True, "recommendations": result})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/extensions/market/refresh", methods=["POST"])
    @require_token
    @log_request()
    def api_extensions_market_refresh():
        try:
            result = _ext_market.fetch_community_index()
            if result:
                return jsonify({"ok": True, "count": len(result)})
            return jsonify({"ok": False, "error": "获取索引失败"}), 500
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  通道
    # ═══════════════════════════════════════════════════

    @app.route("/api/extensions/channels/send", methods=["POST"])
    @require_token
    @log_request()
    def api_extensions_channel_send():
        try:
            data = request.get_json() or {}
            channel_id = data.get("channel_id", "")
            message = data.get("message", "")
            kwargs = data.get("params", {})
            if not channel_id or not message:
                return jsonify({"ok": False, "error": "缺少 channel_id 或 message"}), 400
            result = _ext_mgr.send_channel_message(channel_id, message, **kwargs)
            return jsonify(result)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
