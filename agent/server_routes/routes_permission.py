"""权限 & 安全 & 操作追踪 API 路由"""
import logging
from flask import request, jsonify
from agent.server_auth import require_token, log_request
from agent.tools import list_tools as _list_tools
from agent.tools import set_action_tracker
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)


def _setup_action_tracker(state):
    """设置 action tracker 到 tools 模块（内置追踪，无需猴子补丁）"""
    if state.action_tracker is None:
        return
    set_action_tracker(state.action_tracker)


def register_routes(app, state):
    """注册所有权限 & 安全路由"""

    safety_guard = state.safety_guard
    action_tracker = state.action_tracker
    alert_queue = state.alert_queue
    permission_toggles = state.permission_toggles
    window_sensor = state.window_sensor
    Yunshu = state.Yunshu

    # 设置 action tracker 包装
    _setup_action_tracker(state)

    # ═══════════════════════════════════════════════════
    #  安全检查
    # ═══════════════════════════════════════════════════

    @app.route("/api/safety/check", methods=["POST"])
    @trace_route("Permission")
    @require_token
    @log_request()
    def api_safety_check():
        data = request.get_json() or {}
        text = data.get("text", "")
        result = safety_guard.check(text)
        return jsonify(result)

    @app.route("/api/safety/alerts")
    @trace_route("Permission")
    @log_request(show_response=False)
    def api_safety_alerts():
        limit = request.args.get("limit", 20, type=int)
        alerts = alert_queue[-limit:]
        return jsonify({"alerts": alerts, "stats": safety_guard.get_stats()})

    @app.route("/api/safety/keywords", methods=["GET", "POST"])
    @trace_route("Permission")
    @require_token
    @log_request()
    def api_safety_keywords():
        if request.method == "POST":
            data = request.get_json() or {}
            pattern = data.get("pattern", "")
            description = data.get("description", "")
            level = data.get("level", "warning")
            category = data.get("category", "")
            if not pattern:
                return jsonify({"ok": False, "error": "缺少 pattern"}), 400
            safety_guard.add_keyword(pattern, description, level, category)
            safety_guard.reload()
            return jsonify({"ok": True})
        return jsonify({"keywords": safety_guard._keywords, "stats": safety_guard.get_stats()})

    # ═══════════════════════════════════════════════════
    #  权限控制面板
    # ═══════════════════════════════════════════════════

    @app.route("/api/permission/status")
    @trace_route("Permission")
    @log_request(show_response=False)
    def api_permission_status():
        tracker_status = action_tracker.get_status()
        perm_stats = safety_guard.get_stats()
        try:
            perm_logs = Yunshu._permission.get_permission_log()
            perm_check_count = len(perm_logs)
        except Exception:
            perm_check_count = 0

        tools = _list_tools()
        tool_count = len(tools)
        alert_count = len(alert_queue)

        return jsonify({
            "current_action": tracker_status["current_action"],
            "emergency": tracker_status["emergency"],
            "stats": {
                "blocked": perm_stats.get("blocked_count", 0),
                "warned": perm_stats.get("warned_count", 0),
                "total_alerts": alert_count,
                "perm_checks": perm_check_count,
                "tools": tool_count,
                "actions_tracked": tracker_status["action_count"],
                "access_tracked": tracker_status["access_count"],
            },
            "toggles": dict(permission_toggles),
        })

    @app.route("/api/permission/log")
    @trace_route("Permission")
    @log_request(show_response=False)
    def api_permission_log():
        limit = request.args.get("limit", 20, type=int)
        logs = action_tracker.get_action_history(limit)
        try:
            perm_logs = Yunshu._permission.get_permission_log(limit)
        except Exception:
            perm_logs = []
        return jsonify({
            "action_logs": logs,
            "perm_logs": perm_logs,
        })

    @app.route("/api/permission/stats")
    @trace_route("Permission")
    @log_request(show_response=False)
    def api_permission_stats():
        guard_stats = safety_guard.get_stats()
        try:
            perm = Yunshu._permission
            perm_logs = perm.get_permission_log()
            perm_stats = {
                "total_checks": len(perm_logs),
                "backup_count": getattr(perm, '_backup_count', 0),
                "pending_confirm": sum(1 for l in perm_logs if l.get("requires_confirmation") and not l.get("confirmed")),
            }
        except Exception:
            perm_stats = {"total_checks": 0, "backup_count": 0, "pending_confirm": 0}

        tools = _list_tools()
        tool_perms = []
        for t in tools:
            name = t["name"]
            dangerous_keywords = ["delete", "remove", "format", "stop", "shutdown", "exec", "write"]
            sensitive_keywords = ["write", "modify", "config", "setting"]
            is_dangerous = any(k in name.lower() for k in dangerous_keywords)
            is_sensitive = any(k in name.lower() for k in sensitive_keywords)
            if is_dangerous:
                level = "dangerous"
            elif is_sensitive:
                level = "requires_confirm"
            else:
                level = "allowed"
            tool_perms.append({"name": name, "description": t.get("description", ""), "level": level})

        return jsonify({
            "guard_stats": {
                "blocked": guard_stats.get("blocked_count", 0),
                "warned": guard_stats.get("warned_count", 0),
                "total_alerts": guard_stats.get("total_alerts", 0),
                "keywords": guard_stats.get("keywords_loaded", {}),
            },
            "perm_stats": perm_stats,
            "tools": tool_perms,
            "toggles": dict(permission_toggles),
        })

    @app.route("/api/permission/access-log")
    @trace_route("Permission")
    @log_request(show_response=False)
    def api_permission_access_log():
        limit = request.args.get("limit", 20, type=int)
        type_filter = request.args.get("type", None)
        logs = action_tracker.get_access_log(limit, type_filter)
        return jsonify({"access_logs": logs})

    @app.route("/api/permission/emergency", methods=["POST"])
    @trace_route("Permission")
    @require_token
    @log_request()
    def api_permission_emergency():
        data = request.get_json() or {}
        action = data.get("action", "")

        if action == "stop":
            action_tracker.emergency_stop()
            return jsonify({"ok": True, "action": "stop", "message": "🚨 已触发紧急停止"})
        elif action == "pause":
            paused = action_tracker.emergency_pause()
            msg = "⏸ 智能体已暂停" if paused else "▶ 智能体已恢复"
            return jsonify({"ok": True, "action": "pause", "paused": paused, "message": msg})
        elif action == "network_block":
            blocked = action_tracker.toggle_network_block()
            msg = "🔌 网络访问已封锁" if blocked else "🌐 网络访问已恢复"
            return jsonify({"ok": True, "action": "network_block", "blocked": blocked, "message": msg})
        elif action == "reset":
            action_tracker.reset()
            return jsonify({"ok": True, "action": "reset", "message": "🔄 操作追踪器已重置"})
        elif action == "cancel":
            action_tracker.finish_action("cancelled", "用户手动取消")
            try:
                Yunshu.abort_chat()
            except Exception as e:
                logger.warning("中止聊天时出错: %s", e)
            return jsonify({"ok": True, "action": "cancel", "message": "⏹ 当前操作已取消"})

        return jsonify({"ok": False, "error": f"未知操作: {action}"}), 400

    @app.route("/api/permission/toggle", methods=["POST"])
    @trace_route("Permission")
    @require_token
    @log_request()
    def api_permission_toggle():
        data = request.get_json() or {}
        key = data.get("key", "")
        enabled = data.get("enabled")

        if key not in permission_toggles:
            return jsonify({"ok": False, "error": f"未知开关: {key}"}), 400

        if enabled is not None:
            permission_toggles[key] = bool(enabled)
        else:
            permission_toggles[key] = not permission_toggles[key]

        # 特殊处理：窗口监控开关联动
        if key == "window_monitor":
            state.window_sensor_consented = permission_toggles[key]
            if window_sensor:
                config = window_sensor.get_config()
                config["enabled"] = permission_toggles[key]
                window_sensor.save_config(config)
                if permission_toggles[key] and not window_sensor.is_running:
                    window_sensor.start()
                elif not permission_toggles[key] and window_sensor.is_running:
                    window_sensor.stop()

        return jsonify({"ok": True, "key": key, "enabled": permission_toggles[key]})
