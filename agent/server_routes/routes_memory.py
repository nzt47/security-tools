"""记忆 & 窗口监控 & 隐私 API 路由"""
import os
import asyncio
import logging
from flask import request, jsonify
from agent.server_auth import require_token, log_request

logger = logging.getLogger(__name__)


def register_routes(app, state):
    """注册所有记忆 & 窗口监控 & 隐私路由"""

    Yunshu = state.Yunshu
    window_sensor = state.window_sensor

    # ═══════════════════════════════════════════════════
    #  记忆
    # ═══════════════════════════════════════════════════

    @app.route("/api/memory/overview")
    @log_request(show_response=False)
    def api_memory_overview():
        try:
            summary = Yunshu._memory.load_summary()
            recent = Yunshu._memory._storage.load_recent_messages(limit=20)
            logs = Yunshu._memory._black_box.analyze()
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

    @app.route("/api/memory/manual", methods=["POST"])
    @require_token
    @log_request()
    def api_memory_manual():
        data = request.get_json() or {}
        content = data.get("content", "").strip()
        priority = data.get("priority", "normal")
        if not content:
            return jsonify({"ok": False, "error": "内容不能为空"}), 400
        try:
            Yunshu._memory.add_memory({
                "role": "user",
                "content": f"[手动记忆·优先级:{priority}] {content}"
            })
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/memory/compress", methods=["POST"])
    @require_token
    @log_request()
    def api_memory_compress():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(Yunshu._memory.compress())
            loop.close()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/memory/<int:index>", methods=["DELETE"])
    @require_token
    @log_request()
    def api_memory_delete_index(index):
        return jsonify({"ok": True})

    # ═══════════════════════════════════════════════════
    #  窗口事件
    # ═══════════════════════════════════════════════════

    @app.route("/api/memory/windows/events")
    @log_request(show_response=False)
    def api_window_events():
        limit = request.args.get("limit", 50, type=int)
        limit = min(limit, 500)
        try:
            events = Yunshu._memory._black_box.query(
                event_type="window_event", limit=limit
            )
            return jsonify({"events": events})
        except Exception as e:
            return jsonify({"events": [], "error": str(e)})

    @app.route("/api/memory/windows/stats")
    @log_request(show_response=False)
    def api_window_stats():
        try:
            events = Yunshu._memory._black_box.query(
                event_type="window_event", limit=2000
            )
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

    @app.route("/api/memory/windows/current")
    @log_request(show_response=False)
    def api_window_current():
        if window_sensor:
            return jsonify(window_sensor.get_current())
        return jsonify({"process": None, "title": None, "elapsed_sec": 0, "is_idle": False})

    @app.route("/api/memory/windows/config", methods=["GET", "POST"])
    @require_token
    @log_request()
    def api_window_config():
        if not window_sensor:
            return jsonify({"enabled": False, "error": "WindowSensor 未初始化"})
        if request.method == "POST":
            try:
                new_config = request.get_json()
                window_sensor.save_config(new_config)
                return jsonify({"ok": True, "config": window_sensor.get_config()})
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify(window_sensor.get_config())

    @app.route("/api/memory/windows/clear", methods=["POST"])
    @require_token
    @log_request()
    def api_window_clear():
        try:
            return jsonify({"ok": True, "message": "窗口事件将在滚动日志中自然过期"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  窗口监控同意
    # ═══════════════════════════════════════════════════

    @app.route("/api/window/consent", methods=["POST"])
    @log_request()
    def api_window_consent():
        data = request.get_json() or {}
        consent = data.get("consent", False)
        state.window_sensor_consented = consent

        if window_sensor:
            config = window_sensor.get_config()
            if consent:
                config["enabled"] = True
                window_sensor.save_config(config)
                if not window_sensor.is_running:
                    window_sensor.start()
                logger.info("用户已同意窗口监控")
            else:
                config["enabled"] = False
                window_sensor.save_config(config)
                if window_sensor.is_running:
                    window_sensor.stop()
                logger.info("用户已拒绝窗口监控")
            return jsonify({"ok": True, "consent": consent, "enabled": consent})

        return jsonify({"ok": False, "error": "窗口传感器未初始化"})

    # ═══════════════════════════════════════════════════
    #  隐私
    # ═══════════════════════════════════════════════════

    @app.route("/api/privacy/info")
    @log_request(show_response=False)
    def api_privacy_info():
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
                    "currently_active": window_sensor is not None and hasattr(window_sensor, 'is_running') and bool(window_sensor.is_running),
                },
            ],
            "不采集的信息": ["键盘输入内容", "鼠标点击位置", "文件内容", "浏览器历史"],
            "数据存储": {
                "location": "本地 memory_data/ 目录",
                "format": "JSONL 文件",
                "retention": "日志文件按大小滚动保留",
            },
        })
