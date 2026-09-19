"""
云枢 · 后台任务（AsyncExecutor）路由
------------------------------------
「会话任务」区域的「后台任务」下拉需要系统后台在跑什么，而 AsyncExecutor
此前只有工具面（`agent.tools.code_tools`），没有 HTTP 面 —— 本模块补上：

    GET  /api/background/tasks             列出全部后台任务（含状态/进度/耗时）
    GET  /api/background/tasks/<task_id>   单任务状态
    GET  /api/background/tasks/<task_id>/result  任务结果（未完成时提示进行中）
    POST /api/background/tasks/<task_id>/cancel  取消任务（仅 pending/running）

只读为主 + 一个取消动作；执行器是 SingletonManager 单例（get_async_executor），
首次调用即创建，故本模块不持有额外状态。
"""

import logging
import time

from flask import jsonify, request

from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)


def _safe_int(value, default: int, lo: int, hi: int) -> int:
    """请求参数整型收敛（越界/非法一律回落到合法区间）"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _task_summary(task: dict) -> dict:
    """任务摘要：只保留下拉菜单渲染需要的叶子字段（不搬运执行结果本体）"""
    result = task.get("result")
    return {
        "id": task.get("id", ""),
        "name": task.get("name", ""),
        "tool_name": task.get("tool_name", ""),
        "status": task.get("status", "unknown"),
        "progress": task.get("progress", "") or "",
        "created_at": task.get("created_at", ""),
        "started_at": task.get("started_at", ""),
        "completed_at": task.get("completed_at", ""),
        "error": (task.get("error") or "")[:400],
        "timeout": task.get("timeout"),
        "has_result": result is not None,
    }


def register_routes(app, state):
    """注册后台任务路由"""

    def _executor():
        from agent.async_executor import get_async_executor
        return get_async_executor()

    @app.route("/api/background/tasks", methods=["GET"])
    @trace_route("BackgroundTasks")
    def api_background_tasks():
        """列出后台任务（默认按创建时间倒序；limit 截断，status 过滤）"""
        try:
            limit = _safe_int(request.args.get("limit", 50), 50, 1, 500)
            status = (request.args.get("status") or "").strip()
            data = _executor().list_tasks()
            tasks = data.get("tasks", []) if isinstance(data, dict) else []
            if status:
                tasks = [t for t in tasks if t.get("status") == status]
            running = sum(1 for t in tasks if t.get("status") in ("pending", "running"))
            return jsonify({
                "ok": True,
                "tasks": [_task_summary(t) for t in tasks[:limit]],
                "total": len(tasks),
                "running": running,
                "active": running,  # 别名：下拉按钮角标使用
                "status_filter": status,
                "ts": time.strftime("%H:%M:%S"),
            })
        except Exception as e:
            logger.error("后台任务列表查询失败: %s", e)
            return jsonify({"ok": False, "error": str(e), "tasks": []}), 500

    @app.route("/api/background/tasks/<task_id>", methods=["GET"])
    @trace_route("BackgroundTasks")
    def api_background_task_status(task_id):
        """单任务状态"""
        try:
            data = _executor().get_status(task_id)
            if not data.get("ok"):
                return jsonify(data), 404
            return jsonify(data)
        except Exception as e:
            logger.error("后台任务状态查询失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/background/tasks/<task_id>/result", methods=["GET"])
    @trace_route("BackgroundTasks")
    def api_background_task_result(task_id):
        """任务结果（未完成时返回进行中提示，不视为错误）"""
        try:
            data = _executor().get_result(task_id)
            if not data.get("ok"):
                return jsonify(data), 404
            return jsonify(data)
        except Exception as e:
            logger.error("后台任务结果查询失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/background/tasks/<task_id>/cancel", methods=["POST"])
    @trace_route("BackgroundTasks")
    def api_background_task_cancel(task_id):
        """取消后台任务（仅 pending/running 可取消）"""
        try:
            data = _executor().cancel(task_id)
            code = 200 if data.get("ok") else 400
            return jsonify(data), code
        except Exception as e:
            logger.error("后台任务取消失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500
