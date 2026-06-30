"""心跳 & 定时任务 & 性能监控 & 测试 API 路由"""
import logging
from flask import request, jsonify
from agent.server_auth import require_token, log_request
from agent.task_scheduler import (
    get_scheduler,
    perform_heartbeat_check,
)
from agent.system_tools import (
    list_scheduled_tasks, create_scheduled_task, delete_scheduled_task,
    toggle_scheduled_task,
)
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)


def register_routes(app, state):
    """注册所有监控 & 任务 & 测试路由"""

    # ═══════════════════════════════════════════════════
    #  心跳
    # ═══════════════════════════════════════════════════

    @app.route("/api/heartbeat")
    @trace_route("Monitoring")
    @log_request(show_response=False)
    def api_heartbeat():
        try:
            hb_result = perform_heartbeat_check(state.Yunshu)
            scheduler = get_scheduler()
            scheduler._save_heartbeat(hb_result)
            return jsonify(hb_result)
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.route("/api/heartbeat/history")
    @trace_route("Monitoring")
    @log_request(show_response=False)
    def api_heartbeat_history():
        limit = request.args.get("limit", 100, type=int)
        offset = request.args.get("offset", 0, type=int)
        scheduler = get_scheduler()
        data = scheduler.get_heartbeat_status()
        history = data.get("history", [])
        total = len(history)
        history.reverse()
        paged = history[offset:offset + limit]
        return jsonify({
            "history": paged,
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    @app.route("/api/heartbeat/status")
    @trace_route("Monitoring")
    @log_request(show_response=False)
    def api_heartbeat_status():
        scheduler = get_scheduler()
        data = scheduler.get_heartbeat_status()
        latest = data.get("latest", {})
        history = data.get("history", [])
        healthy_count = sum(1 for h in history if h.get("status") == "healthy")
        return jsonify({
            "status": latest.get("status", "unknown"),
            "timestamp": latest.get("timestamp"),
            "total_checks": len(history),
            "healthy_checks": healthy_count,
            "latest": latest,
        })

    # ═══════════════════════════════════════════════════
    #  定时任务
    # ═══════════════════════════════════════════════════

    @app.route("/api/scheduler/tasks")
    @trace_route("Monitoring")
    @log_request(show_response=False)
    def api_scheduler_list():
        return jsonify(list_scheduled_tasks())

    @app.route("/api/scheduler/create", methods=["POST"])
    @trace_route("Monitoring")
    @require_token
    @log_request()
    def api_scheduler_create():
        data = request.get_json() or {}
        name = data.get("name", "")
        command = data.get("command", "")
        interval_sec = data.get("interval_sec", 60)
        if not name or not command:
            return jsonify({"ok": False, "error": "缺少 name 或 command"}), 400
        safety = state.safety_guard.check(command)
        if safety["level"] == "critical":
            return jsonify({"ok": False, "blocked": True, "safety": safety}), 403
        result = create_scheduled_task(name, command, interval_sec)
        return jsonify(result)

    @app.route("/api/scheduler/delete", methods=["POST"])
    @trace_route("Monitoring")
    @require_token
    @log_request()
    def api_scheduler_delete():
        data = request.get_json() or {}
        task_id = data.get("id", "")
        return jsonify(delete_scheduled_task(task_id))

    @app.route("/api/scheduler/toggle", methods=["POST"])
    @trace_route("Monitoring")
    @require_token
    @log_request()
    def api_scheduler_toggle():
        data = request.get_json() or {}
        task_id = data.get("id", "")
        enabled = data.get("enabled", True)
        return jsonify(toggle_scheduled_task(task_id, enabled))

    @app.route("/api/scheduler/execute-now", methods=["POST"])
    @trace_route("Monitoring")
    @require_token
    @log_request()
    def api_scheduler_execute_now():
        data = request.get_json() or {}
        task_id = data.get("id", "")
        if not task_id:
            return jsonify({"ok": False, "error": "缺少任务ID"}), 400
        scheduler = get_scheduler()
        result = scheduler.execute_now(task_id)
        if result is None:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        return jsonify({"ok": True, "result": result})

    @app.route("/api/scheduler/history")
    @trace_route("Monitoring")
    @log_request(show_response=False)
    def api_scheduler_history():
        limit = request.args.get("limit", 100, type=int)
        offset = request.args.get("offset", 0, type=int)
        task_type = request.args.get("type", "", type=str)
        scheduler = get_scheduler()
        history = scheduler.get_history(limit=limit, offset=offset, task_type=task_type)
        return jsonify({"history": history, "limit": limit, "offset": offset})

    # ═══════════════════════════════════════════════════
    #  搜索引擎性能监控
    # ═══════════════════════════════════════════════════

    @app.route("/api/search-performance/status")
    @trace_route("Monitoring")
    @log_request()
    def api_search_performance_status():
        try:
            from agent.search_performance_monitor import get_performance_monitor_status
            status = get_performance_monitor_status()
            return jsonify({"ok": True, "status": status})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/search-performance/start", methods=["POST"])
    @trace_route("Monitoring")
    @require_token
    @log_request()
    def api_search_performance_start():
        try:
            from agent.search_performance_monitor import start_performance_monitor
            data = request.get_json() or {}
            interval_sec = data.get("interval_sec", 300)
            status = start_performance_monitor(interval_sec)
            return jsonify({"ok": True, "message": "性能监控已启动", "status": status})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/search-performance/stop", methods=["POST"])
    @trace_route("Monitoring")
    @require_token
    @log_request()
    def api_search_performance_stop():
        try:
            from agent.search_performance_monitor import stop_performance_monitor
            status = stop_performance_monitor()
            return jsonify({"ok": True, "message": "性能监控已停止", "status": status})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/search-performance/check", methods=["POST"])
    @trace_route("Monitoring")
    @require_token
    @log_request()
    def api_search_performance_check():
        try:
            from agent.search_performance_monitor import run_manual_performance_check
            result = run_manual_performance_check()
            return jsonify({"ok": True, "result": result})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/search-performance/history")
    @trace_route("Monitoring")
    @log_request()
    def api_search_performance_history():
        try:
            from agent.search_performance_monitor import get_performance_history
            limit = request.args.get("limit", 10, type=int)
            history = get_performance_history(limit)
            return jsonify({"ok": True, "history": history})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/search-performance/summary")
    @trace_route("Monitoring")
    @log_request()
    def api_search_performance_summary():
        try:
            from agent.search_performance_monitor import get_performance_summary
            summary = get_performance_summary()
            return jsonify({"ok": True, "summary": summary})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  测试端点
    # ═══════════════════════════════════════════════════

    @app.route("/api/test/error")
    @trace_route("Monitoring")
    @log_request()
    def api_test_error():
        x = 1 / 0
        return jsonify({"ok": True, "result": x})

    @app.route("/api/test/null")
    @trace_route("Monitoring")
    @log_request()
    def api_test_null():
        obj = None
        return jsonify({"ok": True, "result": obj.some_method()})

    @app.route("/api/test/division")
    @trace_route("Monitoring")
    @log_request()
    def api_test_division():
        a = request.args.get("a", 10, type=float)
        b = request.args.get("b", 2, type=float)
        try:
            result = a / b
            return jsonify({"ok": True, "result": result})
        except ZeroDivisionError as e:
            raise
