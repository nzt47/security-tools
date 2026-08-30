# plugins/mcp_scheduler.py
# -*- coding: utf-8 -*-
"""云枢 MCP 与调度域插件（任务 T1.8）：MCP 服务管理 / 定时任务 / 计划调度 / 异步任务。

从 app_server.py 迁移而来，路由路径与行为 100% 不变：
  - MCP 服务：/api/mcp/services、/api/mcp/services/<string:service_id>、/api/mcp/enable
  - 定时任务：/api/scheduler/*
  - 计划调度：/api/schedules*
  - 异步任务：/api/tasks*

共享依赖约定（PLAN-1 §4）：
  - 模块顶层只 import flask / plugin_api / 标准库，绝不顶层 import app_server（循环导入红线）；
  - 共享装饰器（require_token / log_request）与全局单例（_network_config_mgr、_safety_guard）
    保留在 app_server.py，视图函数内延迟 import，调用时才执行；
  - 调度器/MCP 执行器单例（get_scheduler / get_schedule_scheduler / get_async_executor）
    按项目 SingletonManager 风格在函数内延迟取用。
"""
import functools

from flask import Blueprint, request, jsonify

from .plugin_api import Plugin, register_plugin

bp = Blueprint("mcp_scheduler", __name__)


# ════════════════════════════════════════════════════════════════════════════
#  共享装饰器（延迟包装）
# ----------------------------------------------------------------------------
# require_token / log_request 保留在 app_server.py；插件顶层不得 import app_server
# （循环导入红线，PLAN-1 §4），故在请求时取用真实装饰器再调用。
# 包装顺序与语义和迁移前一致：日志装饰器在内、令牌校验装饰器在外。
# ════════════════════════════════════════════════════════════════════════════

def _lazy_wrap(f, build):
    """占位包装器：每次调用时用 app_server 的真实装饰器包装 f 后执行。"""
    @functools.wraps(f)
    def _wrapped(*args, **kwargs):
        return build(f)(*args, **kwargs)
    return _wrapped


def _require_token(f):
    """延迟版 @require_token（app_server 共享装饰器）"""
    def _build(fn):
        from app_server import require_token as _real
        return _real(fn)
    return _lazy_wrap(f, _build)


def _log_request(*args, **kwargs):
    """延迟版 @log_request(...)（app_server 共享装饰器）"""
    def _decorator(f):
        def _build(fn):
            from app_server import log_request as _real
            return _real(*args, **kwargs)(fn)
        return _lazy_wrap(f, _build)
    return _decorator


# ════════════════════════════════════════════════════════════
#  MCP 服务管理 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/mcp/services", methods=["GET"])
@_require_token
@_log_request(show_response=False)
def api_mcp_services_get():
    """获取所有 MCP 服务"""
    from app_server import _network_config_mgr
    try:
        services = _network_config_mgr.get_mcp_services()
        return jsonify({"ok": True, "services": services})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/mcp/services/<string:service_id>", methods=["GET"])
@_require_token
@_log_request(show_response=False)
def api_mcp_service_get(service_id):
    """获取单个 MCP 服务"""
    from app_server import _network_config_mgr
    try:
        service = _network_config_mgr.get_mcp_service(service_id)
        if service:
            return jsonify({"ok": True, "service": service})
        return jsonify({"ok": False, "error": "服务不存在"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/mcp/services", methods=["POST"])
@_require_token
@_log_request()
def api_mcp_service_add():
    """添加 MCP 服务"""
    from app_server import _network_config_mgr
    try:
        data = request.get_json() or {}
        service = data.get("service", {})
        
        # 验证配置
        errors = _network_config_mgr.validate_mcp_service(service)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        
        result = _network_config_mgr.add_mcp_service(service)
        return jsonify({"ok": True, "service": result})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/mcp/services/<string:service_id>", methods=["PUT"])
@_require_token
@_log_request()
def api_mcp_service_update(service_id):
    """更新 MCP 服务"""
    from app_server import _network_config_mgr
    try:
        data = request.get_json() or {}
        updates = data.get("updates", {})
        
        result = _network_config_mgr.update_mcp_service(service_id, updates)
        if result:
            return jsonify({"ok": True, "service": result})
        return jsonify({"ok": False, "error": "服务不存在"}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/mcp/services/<string:service_id>", methods=["DELETE"])
@_require_token
@_log_request()
def api_mcp_service_delete(service_id):
    """删除 MCP 服务"""
    from app_server import _network_config_mgr
    try:
        success = _network_config_mgr.delete_mcp_service(service_id)
        if success:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "服务不存在"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/mcp/enable", methods=["POST"])
@_require_token
@_log_request()
def api_mcp_enable():
    """启用/禁用 MCP 服务"""
    from app_server import _network_config_mgr
    try:
        data = request.get_json() or {}
        enabled = data.get("enabled", False)
        
        config = _network_config_mgr.get_raw_config()
        config['mcp']['enabled'] = enabled
        _network_config_mgr.update(config)
        
        return jsonify({"ok": True, "enabled": enabled})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  定时任务接口
# ════════════════════════════════════════════════════════════

@bp.route("/api/scheduler/tasks")
@_log_request(show_response=False)
def api_scheduler_list():
    """列出所有定时任务"""
    from agent.system_tools import list_scheduled_tasks
    return jsonify(list_scheduled_tasks())


@bp.route("/api/scheduler/create", methods=["POST"])
@_require_token
@_log_request()
def api_scheduler_create():
    """创建定时任务"""
    from app_server import _safety_guard
    from agent.system_tools import create_scheduled_task
    data = request.get_json() or {}
    name = data.get("name", "")
    command = data.get("command", "")
    interval_sec = data.get("interval_sec", 60)
    if not name or not command:
        return jsonify({"ok": False, "error": "缺少 name 或 command"}), 400
    # 安全检查
    safety = _safety_guard.check(command)
    if safety["level"] == "critical":
        return jsonify({"ok": False, "blocked": True, "safety": safety}), 403
    result = create_scheduled_task(name, command, interval_sec)
    return jsonify(result)


@bp.route("/api/scheduler/delete", methods=["POST"])
@_require_token
@_log_request()
def api_scheduler_delete():
    """删除定时任务"""
    from agent.system_tools import delete_scheduled_task
    data = request.get_json() or {}
    task_id = data.get("id", "")
    return jsonify(delete_scheduled_task(task_id))


@bp.route("/api/scheduler/toggle", methods=["POST"])
@_require_token
@_log_request()
def api_scheduler_toggle():
    """启用/禁用定时任务"""
    from agent.system_tools import toggle_scheduled_task
    data = request.get_json() or {}
    task_id = data.get("id", "")
    enabled = data.get("enabled", True)
    return jsonify(toggle_scheduled_task(task_id, enabled))


@bp.route("/api/scheduler/execute-now", methods=["POST"])
@_require_token
@_log_request()
def api_scheduler_execute_now():
    """立即执行指定任务"""
    from agent.task_scheduler import get_scheduler
    data = request.get_json() or {}
    task_id = data.get("id", "")
    if not task_id:
        return jsonify({"ok": False, "error": "缺少任务ID"}), 400
    scheduler = get_scheduler()
    result = scheduler.execute_now(task_id)
    if result is None:
        return jsonify({"ok": False, "error": "任务不存在"}), 404
    return jsonify({"ok": True, "result": result})


@bp.route("/api/scheduler/history")
@_log_request(show_response=False)
def api_scheduler_history():
    """获取任务执行历史"""
    from agent.task_scheduler import get_scheduler
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    task_type = request.args.get("type", "", type=str)
    scheduler = get_scheduler()
    history = scheduler.get_history(limit=limit, offset=offset, task_type=task_type)
    return jsonify({"history": history, "limit": limit, "offset": offset})


# ════════════════════════════════════════════════════════════
#  定时调度 API
# ════════════════════════════════════════════════════════════
# 调度器单例（SingletonManager 风格）：函数内延迟取用 get_schedule_scheduler()，
# 与 app_server 模块级 _sched 指向同一实例（后台线程由 app_server 启动时拉起）。

@bp.route("/api/schedules", methods=["GET"])
@_log_request(show_response=False)
def api_schedules_list():
    """获取所有定时任务"""
    from agent.scheduling import get_schedule_scheduler
    return jsonify(get_schedule_scheduler().get_tasks())


@bp.route("/api/schedules", methods=["POST"])
@_require_token
@_log_request()
def api_schedules_create():
    """创建定时任务"""
    from agent.scheduling import get_schedule_scheduler, Scheduler as _SchedCls
    _sched = get_schedule_scheduler()
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
    if cron_expr.strip() and not _SchedCls.validate_cron_expr(cron_expr):
        return jsonify({"ok": False, "error": f"无效的 cron 表达式: {cron_expr}"}), 400
    result = _sched.add_task(
        name=name, action=action, params=params,
        interval_minutes=interval_minutes, cron_expr=cron_expr, enabled=enabled,
    )
    if result.get("ok"):
        return jsonify(result), 201
    return jsonify(result), 400


@bp.route("/api/schedules/<task_id>", methods=["DELETE"])
@_require_token
@_log_request()
def api_schedules_delete(task_id):
    """删除定时任务"""
    from agent.scheduling import get_schedule_scheduler
    result = get_schedule_scheduler().remove_task(task_id)
    if result.get("ok"):
        return jsonify(result)
    return jsonify(result), 404


@bp.route("/api/schedules/<task_id>/pause", methods=["POST"])
@_require_token
@_log_request()
def api_schedules_pause(task_id):
    """暂停定时任务"""
    from agent.scheduling import get_schedule_scheduler
    result = get_schedule_scheduler().pause_task(task_id)
    if result.get("ok"):
        return jsonify(result)
    return jsonify(result), 404


@bp.route("/api/schedules/<task_id>/resume", methods=["POST"])
@_require_token
@_log_request()
def api_schedules_resume(task_id):
    """恢复定时任务"""
    from agent.scheduling import get_schedule_scheduler
    result = get_schedule_scheduler().resume_task(task_id)
    if result.get("ok"):
        return jsonify(result)
    return jsonify(result), 404


@bp.route("/api/schedules/history", methods=["GET"])
@_log_request(show_response=False)
def api_schedules_history():
    """获取执行历史"""
    from agent.scheduling import get_schedule_scheduler
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    return jsonify(get_schedule_scheduler().get_history(limit=limit, offset=offset))


# ════════════════════════════════════════════════════════════
#  异步任务管理 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/tasks", methods=["GET"])
@_log_request(show_response=False)
def api_tasks_list():
    """列出所有异步任务"""
    from agent.async_executor import get_async_executor
    executor = get_async_executor()
    return jsonify(executor.list_tasks())


@bp.route("/api/tasks/<task_id>", methods=["GET"])
@_log_request(show_response=False)
def api_task_status(task_id):
    """获取单个异步任务状态"""
    from agent.async_executor import get_async_executor
    executor = get_async_executor()
    return jsonify(executor.get_status(task_id))


@bp.route("/api/tasks/<task_id>/cancel", methods=["POST"])
@_require_token
@_log_request()
def api_task_cancel(task_id):
    """取消异步任务"""
    from agent.async_executor import get_async_executor
    executor = get_async_executor()
    result = executor.cancel(task_id)
    if result.get("ok"):
        return jsonify(result)
    return jsonify(result), 400


PLUGIN = register_plugin(Plugin(
    name="mcp_scheduler",
    version="1.0.0",
    description="MCP 服务与任务调度",
    blueprint=bp,
    routes=[
        "/api/mcp/enable",
        "/api/mcp/services",
        "/api/mcp/services/<string:service_id>",
        "/api/scheduler/create",
        "/api/scheduler/delete",
        "/api/scheduler/execute-now",
        "/api/scheduler/history",
        "/api/scheduler/tasks",
        "/api/scheduler/toggle",
        "/api/schedules",
        "/api/schedules/<task_id>",
        "/api/schedules/<task_id>/pause",
        "/api/schedules/<task_id>/resume",
        "/api/schedules/history",
        "/api/tasks",
        "/api/tasks/<task_id>",
        "/api/tasks/<task_id>/cancel",
    ],
))
