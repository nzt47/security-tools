# plugins/memory.py
"""记忆与上下文管理插件（任务 T1.3）。

迁移自 app_server.py 的记忆/上下文域路由（路径与行为 100% 不变）：
  - 上下文监视器：/api/context/*
  - 记忆操作：/api/memory/*
  - 向量检索：/api/vector/search
  - 窗口监控：/api/memory/windows/*

共享依赖约定（PLAN-1 §4）：
  - 插件模块顶层只 import flask / plugin_api，绝不顶层 import app_server（循环导入红线）。
  - require_token / log_request / _Yunshu / _cfg / _session_mgr / _window_sensor 等
    共享装饰器与全局保留在 app_server.py，视图函数内延迟 import。
"""
import functools
from flask import Blueprint, request, jsonify
from .plugin_api import Plugin, register_plugin

bp = Blueprint("memory", __name__)


def _view(*, auth=False, log=None):
    """延迟应用 app_server 的共享装饰器（require_token / log_request）。

    app_server 在本插件模块被导入时尚在初始化（require_token / log_request 尚未定义），
    因此路由函数先以裸函数注册到 blueprint；首次请求时惰性从 app_server 取回装饰器
    并一次性包装，之后直接命中包装版，行为与迁移前完全一致。

    Args:
        auth: True 时套用 require_token（最外层，与原 @require_token 位置一致）。
        log: 不为 None 时套用 log_request(show_response=log)，与原装饰器参数一致。
    """
    def decorator(f):
        state = {"wrapped": None}

        @functools.wraps(f)
        def proxy(*args, **kwargs):
            if state["wrapped"] is None:
                from app_server import require_token, log_request
                wrapped = f
                if log is not None:
                    wrapped = log_request(show_response=log)(wrapped)
                if auth:
                    wrapped = require_token(wrapped)
                state["wrapped"] = wrapped
            return state["wrapped"](*args, **kwargs)

        return proxy
    return decorator


# ════════════════════════════════════════════════════════════
#  上下文监视器 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/context/status")
@_view(log=False)
def api_context_status():
    """获取当前上下文使用状态"""
    from app_server import _get_current_session_id, _session_mgr, _get_token_counter, _cfg, _Yunshu
    session_id = _get_current_session_id()
    messages = _session_mgr.get_messages(session_id, limit=0)  # 全部消息

    counter = _get_token_counter()
    send_tokens = 0
    recv_tokens = 0
    recent = []

    for msg in messages:
        content = msg.get("content", "") or ""
        tokens = counter.count(content)
        role = msg.get("role", "")
        if role == "user":
            send_tokens += tokens
        elif role == "assistant":
            recv_tokens += tokens
        recent.append({
            "role": role,
            "tokens": tokens,
            "content_preview": content[:60],
        })

    # 只保留最近 10 条用于展示
    recent = recent[-10:]

    total = send_tokens + recv_tokens
    limit = _cfg.get("memory", "token_limit", default=4096)
    pct = round(total / limit * 100, 1) if limit > 0 else 0

    # 压缩次数
    compress_rounds = 0
    try:
        compress_rounds = getattr(_Yunshu._memory, 'compress_rounds', 0)
        if callable(compress_rounds):
            compress_rounds = compress_rounds()
    except Exception:
        pass

    # 上下文状态级别
    compress_warn = compress_rounds >= 3
    compress_crit = compress_rounds >= 5
    pct_warn = pct >= 80 or pct >= 60
    pct_crit = pct >= 95
    if pct_crit or compress_crit:
        status_level = "critical"
    elif pct >= 80 or compress_warn:
        status_level = "warning"
    elif pct >= 60:
        status_level = "info"
    else:
        status_level = "ok"

    return jsonify({
        "current_tokens": total,
        "token_limit": limit,
        "percentage": pct,
        "per_message_send_limit": _cfg.get("memory", "per_message_send_limit", default=2048),
        "per_message_recv_limit": _cfg.get("memory", "per_message_recv_limit", default=4096),
        "compress_threshold": _cfg.get("memory", "compress_threshold", default=0.8),
        "compress_rounds": compress_rounds,
        "status_level": status_level,
        "send_tokens": send_tokens,
        "recv_tokens": recv_tokens,
        "messages_count": len(messages),
        "recent_messages": recent,
    })


@bp.route("/api/context/config", methods=["POST"])
@_view(auth=True)
def api_context_config():
    """更新上下文控制参数"""
    from app_server import _cfg, logger
    data = request.get_json() or {}
    changed = []

    if "token_limit" in data:
        val = int(data["token_limit"])
        val = max(512, min(32768, val))
        _cfg.set(val, "memory", "token_limit")
        changed.append("token_limit")

    if "per_message_send_limit" in data:
        val = int(data["per_message_send_limit"])
        val = max(0, min(32768, val))
        _cfg.set(val, "memory", "per_message_send_limit")
        changed.append("per_message_send_limit")

    if "per_message_recv_limit" in data:
        val = int(data["per_message_recv_limit"])
        val = max(0, min(32768, val))
        _cfg.set(val, "memory", "per_message_recv_limit")
        changed.append("per_message_recv_limit")

    if changed:
        logger.info(f"上下文配置已更新: {', '.join(changed)}")

    return jsonify({
        "ok": True,
        "changed": changed,
        "token_limit": _cfg.get("memory", "token_limit", default=4096),
        "per_message_send_limit": _cfg.get("memory", "per_message_send_limit", default=2048),
        "per_message_recv_limit": _cfg.get("memory", "per_message_recv_limit", default=4096),
    })


@bp.route("/api/context/compress", methods=["POST"])
@_view(auth=True)
def api_context_compress():
    """手动触发上下文压缩"""
    from app_server import _Yunshu, logger
    try:
        result = _Yunshu._memory.compress()
        return jsonify({
            "ok": True,
            "freed_tokens": result.get("freed", 0),
            "current_tokens": result.get("current", 0),
        })
    except Exception as e:
        logger.warning(f"手动压缩失败: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 记忆操作 API ──

@bp.route("/api/memory/overview")
@_view(log=False)
def api_memory_overview():
    """获取记忆概览"""
    from app_server import _Yunshu
    try:
        summary = _Yunshu._memory.load_summary()
        recent = _Yunshu._memory._storage.load_recent_messages(limit=20)
        logs = _Yunshu._memory._black_box.analyze()
        log_stats = logs if isinstance(logs, dict) else {}
        return jsonify({
            "summary_version": summary[1] if summary else None,
            "summary_text": summary[0][:300] if summary and summary[0] else None,
            "recent_messages": [
                {"index": i, "role": m.get("role", "?"), "content": m.get("content", "")[:100]}
                for i, m in enumerate(recent)
            ] if recent else [],
            "message_count": len(recent) if recent else 0,
            "log_stats": log_stats,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/memory/manual", methods=["POST"])
@_view(auth=True, log=True)
def api_memory_manual():
    """手动添加记忆"""
    from app_server import _Yunshu
    data = request.get_json() or {}
    content = data.get("content", "").strip()
    priority = data.get("priority", "normal")
    if not content:
        return jsonify({"ok": False, "error": "内容不能为空"}), 400
    try:
        _Yunshu._memory.add_memory({
            "role": "user",
            "content": f"[手动记忆·优先级:{priority}] {content}"
        })
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/memory/compress", methods=["POST"])
@_view(auth=True, log=True)
def api_memory_compress():
    """触发记忆压缩"""
    from app_server import _Yunshu
    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_Yunshu._memory.compress())
        loop.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/memory/<int:index>", methods=["DELETE"])
@_view(auth=True, log=True)
def api_memory_delete_index(index):
    """删除指定索引的记忆"""
    # 标记删除操作已接收（简化实现）
    return jsonify({"ok": True})


@bp.route("/api/memory/clear-summary", methods=["POST"])
@_view(auth=True, log=True)
def api_memory_clear_summary():
    """清空长期摘要"""
    from app_server import _Yunshu
    try:
        _Yunshu._memory.clear_summary()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/memory/summary", methods=["PUT"])
@_view(auth=True, log=True)
def api_memory_update_summary():
    """更新长期摘要内容"""
    from app_server import _Yunshu
    data = request.get_json() or {}
    summary = data.get("summary", "").strip()
    try:
        old = _Yunshu._memory.load_summary()
        version = old[1] if old else 0
        _Yunshu._memory._storage.save_summary(summary, version + 1)
        _Yunshu._memory._black_box.log("summary_updated", {"version": version + 1, "length": len(summary)})
        return jsonify({"ok": True, "version": version + 1})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  向量记忆/语义搜索 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/vector/search", methods=["POST"])
@_view(auth=True, log=True)
def api_vector_search():
    """语义搜索向量记忆"""
    from app_server import _Yunshu, logger
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    top_k = min(int(data.get("top_k", 5)), 50)
    if not query:
        return jsonify({"ok": False, "error": "查询内容不能为空"}), 400

    vs = getattr(_Yunshu, '_vector_memory', None)
    if not vs:
        return jsonify({"ok": True, "results": [], "count": 0, "available": False})

    try:
        results = vs.search(query, top_k)
        return jsonify({
            "ok": True,
            "results": [item.to_dict() for item in results],
            "count": len(results),
        })
    except Exception as e:
        logger.error("向量搜索失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  窗口监控 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/memory/windows/events")
@_view(log=False)
def api_window_events():
    """获取窗口切换事件"""
    from app_server import _Yunshu
    limit = request.args.get("limit", 50, type=int)
    limit = min(limit, 500)
    try:
        events = _Yunshu._memory._black_box.query(
            event_type="window_event", limit=limit
        )
        return jsonify({"events": events})
    except Exception as e:
        return jsonify({"events": [], "error": str(e)})


@bp.route("/api/memory/windows/stats")
@_view(log=False)
def api_window_stats():
    """获取窗口使用统计"""
    from app_server import _Yunshu
    try:
        events = _Yunshu._memory._black_box.query(
            event_type="window_event", limit=2000
        )
        # 按 to_process 聚合
        app_stats = {}
        total_duration = 0
        total_switches = 0
        for ev in events:
            data = ev.get("data", {})
            if data.get("action") != "switch":
                continue
            proc = data.get("to_process") or "unknown"
            title = data.get("to_title") or proc
            dur = data.get("duration_sec", 0)
            if proc not in app_stats:
                app_stats[proc] = {"process": proc, "title": title,
                                   "duration_sec": 0, "switch_count": 0}
            app_stats[proc]["duration_sec"] += dur
            app_stats[proc]["switch_count"] += 1
            total_duration += dur
            total_switches += 1

        apps = sorted(app_stats.values(), key=lambda a: a["duration_sec"], reverse=True)
        for a in apps:
            a["duration_sec"] = round(a["duration_sec"], 1)
            a["percentage"] = round(a["duration_sec"] / total_duration * 100, 1) if total_duration > 0 else 0

        return jsonify({
            "total_duration_sec": round(total_duration, 1),
            "total_switches": total_switches,
            "apps": apps[:20],
        })
    except Exception as e:
        return jsonify({"total_duration_sec": 0, "total_switches": 0, "apps": [], "error": str(e)})


@bp.route("/api/memory/windows/current")
@_view(log=False)
def api_window_current():
    """获取当前活跃窗口"""
    from app_server import _window_sensor
    if _window_sensor:
        return jsonify(_window_sensor.get_current())
    return jsonify({"process": None, "title": None, "elapsed_sec": 0, "is_idle": False})


@bp.route("/api/memory/windows/config", methods=["GET", "POST"])
@_view(auth=True, log=True)
def api_window_config():
    """获取或更新窗口监控配置"""
    from app_server import _window_sensor
    if not _window_sensor:
        return jsonify({"enabled": False, "error": "WindowSensor 未初始化"})
    if request.method == "POST":
        try:
            new_config = request.get_json()
            _window_sensor.save_config(new_config)
            return jsonify({"ok": True, "config": _window_sensor.get_config()})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify(_window_sensor.get_config())


@bp.route("/api/memory/windows/clear", methods=["POST"])
@_view(auth=True, log=True)
def api_window_clear():
    """清空窗口事件记录"""
    try:
        return jsonify({"ok": True, "message": "窗口事件将在滚动日志中自然过期"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


PLUGIN = register_plugin(Plugin(
    name="memory",
    version="1.0.0",
    description="记忆与上下文管理",
    blueprint=bp,
    routes=[
        "/api/context/status",
        "/api/context/config",
        "/api/context/compress",
        "/api/memory/overview",
        "/api/memory/manual",
        "/api/memory/compress",
        "/api/memory/<int:index>",
        "/api/memory/clear-summary",
        "/api/memory/summary",
        "/api/vector/search",
        "/api/memory/windows/events",
        "/api/memory/windows/stats",
        "/api/memory/windows/current",
        "/api/memory/windows/config",
        "/api/memory/windows/clear",
    ],
))
