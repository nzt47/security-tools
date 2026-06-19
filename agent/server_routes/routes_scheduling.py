"""定时调度 API 路由"""
import logging
from flask import request, jsonify
from agent.server_auth import require_token, log_request
from agent.scheduling import get_schedule_scheduler, Scheduler

logger = logging.getLogger(__name__)


def register_routes(app, state):
    """注册定时调度 API 路由"""

    scheduler = get_schedule_scheduler()

    @app.route("/api/schedules", methods=["GET"])
    @log_request(show_response=False)
    def api_schedules_list():
        """获取所有定时任务"""
        return jsonify(scheduler.get_tasks())

    @app.route("/api/schedules", methods=["POST"])
    @require_token
    @log_request()
    def api_schedules_create():
        """创建定时任务"""
        data = request.get_json() or {}

        name = data.get("name", "")
        action = data.get("action", "")
        params = data.get("params", {})
        interval_minutes = data.get("interval_minutes", 0)
        cron_expr = data.get("cron_expr", "")
        enabled = data.get("enabled", True)

        if not name.strip():
            return jsonify({"ok": False, "error": "任务名称不能为空"}), 400

        if interval_minutes <= 0 and not cron_expr.strip():
            return jsonify({"ok": False, "error": "必须提供 interval_minutes 或 cron_expr"}), 400

        # 验证 cron 表达式
        if cron_expr.strip() and not Scheduler.validate_cron_expr(cron_expr):
            return jsonify({"ok": False, "error": f"无效的 cron 表达式: {cron_expr}"}), 400

        if interval_minutes > 0 and interval_minutes < 1:
            return jsonify({"ok": False, "error": "interval_minutes 必须 >= 1"}), 400

        result = scheduler.add_task(
            name=name,
            action=action,
            params=params,
            interval_minutes=interval_minutes,
            cron_expr=cron_expr,
            enabled=enabled,
        )

        if result.get("ok"):
            return jsonify(result), 201
        return jsonify(result), 400

    @app.route("/api/schedules/<task_id>", methods=["DELETE"])
    @require_token
    @log_request()
    def api_schedules_delete(task_id):
        """删除定时任务"""
        result = scheduler.remove_task(task_id)
        if result.get("ok"):
            return jsonify(result)
        return jsonify(result), 404

    @app.route("/api/schedules/<task_id>/pause", methods=["POST"])
    @require_token
    @log_request()
    def api_schedules_pause(task_id):
        """暂停定时任务"""
        result = scheduler.pause_task(task_id)
        if result.get("ok"):
            return jsonify(result)
        return jsonify(result), 404

    @app.route("/api/schedules/<task_id>/resume", methods=["POST"])
    @require_token
    @log_request()
    def api_schedules_resume(task_id):
        """恢复定时任务"""
        result = scheduler.resume_task(task_id)
        if result.get("ok"):
            return jsonify(result)
        return jsonify(result), 404

    @app.route("/api/schedules/history", methods=["GET"])
    @log_request(show_response=False)
    def api_schedules_history():
        """获取执行历史"""
        limit = request.args.get("limit", 100, type=int)
        offset = request.args.get("offset", 0, type=int)
        return jsonify(scheduler.get_history(limit=limit, offset=offset))
