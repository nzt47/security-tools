# -*- coding: utf-8 -*-
"""云枢安全域插件（T1.7）：安全审查 / 权限控制面板 / 隐私 / 窗口采集同意。

从 app_server.py 迁移而来，路由路径与行为 100% 不变。
约定（PLAN-1 §4）：
  - Blueprint 不设 url_prefix，路由保持 /api/... 原样；
  - 插件模块顶层只 import flask / plugin_api / 标准库；
  - 共享依赖（require_token、log_request、_safety_guard、_alert_queue、
    _window_sensor、_action_tracker、_Yunshu 等）保留在 app_server.py，
    视图函数内部延迟 import，规避循环导入。
"""

import functools

from flask import Blueprint, request, jsonify

from .plugin_api import Plugin, register_plugin

bp = Blueprint("safety", __name__)


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


# ── 权限开关状态（允许用户快速切换；原为 app_server 模块级全局，仅本域使用）──
_permission_toggles = {
    "window_monitor": True,
    "sensor": True,
    "network_access": True,
    "file_write": True,
    "dangerous_ops": False,  # 默认关闭危险操作授权
}

# 用户是否同意过窗口监控（仅本域读写，随 consent/toggle 路由迁入）
_window_sensor_consented = False


# ════════════════════════════════════════════════════════════════════════════
#  隐私 & 窗口采集同意
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/privacy/info")
@_log_request(show_response=False)
def api_privacy_info():
    """返回数据采集透明度信息"""
    from app_server import _window_sensor
    from sensor.window_sensor import HAS_WIN32
    return jsonify({
        "version": 1,
        "采集说明": "云枢为了感知自己的身体状态，会采集以下系统信息：",
        "categories": [
            {
                "name": "硬件状态",
                "items": ["CPU 使用率和温度", "内存使用率", "磁盘空间", "电池电量"],
                "purpose": "感知身体状态，调整行为模式",
            },
            {
                "name": "系统信息",
                "items": ["操作系统版本", "Python 版本", "主机名"],
                "purpose": "了解运行环境",
            },
            {
                "name": "窗口活动",
                "items": ["当前活跃窗口标题", "当前进程名称", "窗口切换频率"],
                "purpose": "了解用户注意力焦点（**需用户明确同意**）",
                "requires_consent": True,
                "currently_active": _window_sensor is not None and hasattr(_window_sensor, 'is_running') and bool(_window_sensor.is_running),
            },
        ],
        "不采集的信息": ["键盘输入内容", "鼠标点击位置", "文件内容", "浏览器历史"],
        "数据存储": {
            "location": "本地 memory_data/ 目录",
            "format": "JSONL 文件",
            "retention": "日志文件按大小滚动保留",
        },
    })


@bp.route("/api/window/consent", methods=["POST"])
@_log_request()
def api_window_consent():
    """用户同意或拒绝窗口监控"""
    global _window_sensor_consented
    from app_server import _window_sensor, logger
    data = request.get_json() or {}
    consent = data.get("consent", False)
    _window_sensor_consented = consent

    if _window_sensor:
        config = _window_sensor.get_config()
        if consent:
            config["enabled"] = True
            _window_sensor.save_config(config)
            if not _window_sensor.is_running:
                _window_sensor.start()
            logger.info("用户已同意窗口监控")
        else:
            config["enabled"] = False
            _window_sensor.save_config(config)
            if _window_sensor.is_running:
                _window_sensor.stop()
            logger.info("用户已拒绝窗口监控")
        return jsonify({"ok": True, "consent": consent, "enabled": consent})

    return jsonify({"ok": False, "error": "窗口传感器未初始化"})


# ════════════════════════════════════════════════════════════════════════════
#  安全守护接口
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/safety/check", methods=["POST"])
@_require_token
@_log_request()
def api_safety_check():
    """检查文本是否包含危险操作"""
    from app_server import _safety_guard
    data = request.get_json() or {}
    text = data.get("text", "")
    result = _safety_guard.check(text)
    return jsonify(result)


@bp.route("/api/safety/alerts")
@_log_request(show_response=False)
def api_safety_alerts():
    """获取最近的告警通知"""
    from app_server import _alert_queue, _safety_guard
    limit = request.args.get("limit", 20, type=int)
    alerts = _alert_queue[-limit:]
    return jsonify({"alerts": alerts, "stats": _safety_guard.get_stats()})


@bp.route("/api/safety/keywords", methods=["GET", "POST"])
@_require_token
@_log_request()
def api_safety_keywords():
    """获取或添加危险关键词"""
    from app_server import _safety_guard
    if request.method == "POST":
        data = request.get_json() or {}
        pattern = data.get("pattern", "")
        description = data.get("description", "")
        level = data.get("level", "warning")
        category = data.get("category", "")
        if not pattern:
            return jsonify({"ok": False, "error": "缺少 pattern"}), 400
        _safety_guard.add_keyword(pattern, description, level, category)
        _safety_guard.reload()
        return jsonify({"ok": True})
    return jsonify({"keywords": _safety_guard._keywords, "stats": _safety_guard.get_stats()})


# ════════════════════════════════════════════════════════════════════════════
#  权限控制面板 API（ActionTracker 类与 _action_tracker 实例保留在
#  app_server.py：_tracked_tool_call 工具调用包装仍在那里引用）
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/api/permission/status")
@_log_request(show_response=False)
def api_permission_status():
    """获取权限控制面板状态 — 当前操作 + 总览统计"""
    from app_server import _action_tracker, _safety_guard, _Yunshu, _alert_queue
    tracker_status = _action_tracker.get_status()

    # 统计信息
    perm_stats = _safety_guard.get_stats()
    try:
        perm_logs = _Yunshu._permission.get_permission_log()
        perm_check_count = len(perm_logs)
    except Exception:
        perm_check_count = 0

    # 工具数量
    from agent.tools import list_tools as _list_tools
    tools = _list_tools()
    tool_count = len(tools)

    # 告警数量
    alert_count = len(_alert_queue)

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
        "toggles": dict(_permission_toggles),
    })


@bp.route("/api/permission/log")
@_log_request(show_response=False)
def api_permission_log():
    """获取权限操作日志"""
    from app_server import _action_tracker, _Yunshu
    limit = request.args.get("limit", 20, type=int)
    logs = _action_tracker.get_action_history(limit)

    # 也包含 PermissionSystem 的日志
    try:
        perm_logs = _Yunshu._permission.get_permission_log(limit)
    except Exception:
        perm_logs = []

    return jsonify({
        "action_logs": logs,
        "perm_logs": perm_logs,
    })


@bp.route("/api/permission/stats")
@_log_request(show_response=False)
def api_permission_stats():
    """获取聚合统计"""
    from app_server import _safety_guard, _Yunshu
    guard_stats = _safety_guard.get_stats()
    try:
        perm = _Yunshu._permission
        perm_logs = perm.get_permission_log()
        perm_stats = {
            "total_checks": len(perm_logs),
            "backup_count": getattr(perm, '_backup_count', 0),
            "pending_confirm": sum(1 for l in perm_logs if l.get("requires_confirmation") and not l.get("confirmed")),
        }
    except Exception:
        perm_stats = {"total_checks": 0, "backup_count": 0, "pending_confirm": 0}

    # 所有注册工具及其权限等级
    from agent.tools import list_tools as _list_tools
    tools = _list_tools()
    tool_perms = []
    for t in tools:
        name = t["name"]
        # 简单权限分类：根据名称推断
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
        "toggles": dict(_permission_toggles),
    })


@bp.route("/api/permission/access-log")
@_log_request(show_response=False)
def api_permission_access_log():
    """获取数据访问记录"""
    from app_server import _action_tracker
    limit = request.args.get("limit", 20, type=int)
    type_filter = request.args.get("type", None)
    logs = _action_tracker.get_access_log(limit, type_filter)
    return jsonify({"access_logs": logs})


@bp.route("/api/permission/emergency", methods=["POST"])
@_require_token
@_log_request()
def api_permission_emergency():
    """紧急控制 — 暂停/停止/重置"""
    from app_server import _action_tracker, _Yunshu, logger
    data = request.get_json() or {}
    action = data.get("action", "")

    if action == "stop":
        result = _action_tracker.emergency_stop()
        return jsonify({"ok": True, "action": "stop", "message": "🚨 已触发紧急停止"})
    elif action == "pause":
        paused = _action_tracker.emergency_pause()
        msg = "⏸ 智能体已暂停" if paused else "▶ 智能体已恢复"
        return jsonify({"ok": True, "action": "pause", "paused": paused, "message": msg})
    elif action == "network_block":
        blocked = _action_tracker.toggle_network_block()
        msg = "🔌 网络访问已封锁" if blocked else "🌐 网络访问已恢复"
        return jsonify({"ok": True, "action": "network_block", "blocked": blocked, "message": msg})
    elif action == "reset":
        _action_tracker.reset()
        return jsonify({"ok": True, "action": "reset", "message": "🔄 操作追踪器已重置"})
    elif action == "cancel":
        _action_tracker.finish_action("cancelled", "用户手动取消")
        # 真正中止正在进行的聊天
        try:
            _Yunshu.abort_chat()
        except Exception as e:
            logger.warning("中止聊天时出错: %s", e)
        return jsonify({"ok": True, "action": "cancel", "message": "⏹ 当前操作已取消"})

    return jsonify({"ok": False, "error": f"未知操作: {action}"}), 400


@bp.route("/api/permission/toggle", methods=["POST"])
@_require_token
@_log_request()
def api_permission_toggle():
    """切换权限开关"""
    from app_server import _window_sensor, logger
    global _window_sensor_consented
    data = request.get_json() or {}
    key = data.get("key", "")
    enabled = data.get("enabled")

    if key not in _permission_toggles:
        return jsonify({"ok": False, "error": f"未知开关: {key}"}), 400

    if enabled is not None:
        _permission_toggles[key] = bool(enabled)
    else:
        _permission_toggles[key] = not _permission_toggles[key]

    # 特殊处理：窗口监控开关联动
    if key == "window_monitor":
        _window_sensor_consented = _permission_toggles[key]
        if _window_sensor:
            config = _window_sensor.get_config()
            config["enabled"] = _permission_toggles[key]
            _window_sensor.save_config(config)
            if _permission_toggles[key] and not _window_sensor.is_running:
                _window_sensor.start()
            elif not _permission_toggles[key] and _window_sensor.is_running:
                _window_sensor.stop()

    logger.info(f"权限开关 {key} → {'开' if _permission_toggles[key] else '关'}")
    return jsonify({"ok": True, "key": key, "enabled": _permission_toggles[key]})


PLUGIN = register_plugin(Plugin(
    name="safety",
    version="1.0.0",
    description="安全、权限与隐私",
    blueprint=bp,
    routes=[
        "/api/privacy/info",
        "/api/window/consent",
        "/api/safety/check",
        "/api/safety/alerts",
        "/api/safety/keywords",
        "/api/permission/status",
        "/api/permission/log",
        "/api/permission/stats",
        "/api/permission/access-log",
        "/api/permission/emergency",
        "/api/permission/toggle",
    ],
))
