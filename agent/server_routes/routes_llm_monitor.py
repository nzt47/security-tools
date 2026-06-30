"""
云枢 · LLM 通信监控路由
提供收发记录查询、统计、详情和清除 API
"""

import logging
from flask import jsonify, request
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)


def register_routes(app, state):
    """注册 LLM 监控相关路由"""

    def _get_monitor():
        from ..llm_monitor import get_monitor
        return get_monitor()

    # ═══════════════════════════════════════════════════
    #  获取记录列表
    # ═══════════════════════════════════════════════════

    @app.route("/api/llm-monitor/records", methods=["GET"])
    @trace_route("LLMMonitor")
    def api_llm_monitor_records():
        """获取 LLM 通信记录列表（倒序）"""
        try:
            limit = request.args.get("limit", 50, type=int)
            offset = request.args.get("offset", 0, type=int)
            session_id = request.args.get("session_id", "")
            source = request.args.get("source", "")

            monitor = _get_monitor()
            records, total = monitor.get_records(
                limit=min(limit, 200),
                offset=offset,
                session_id=session_id,
                source=source,
            )
            return jsonify({
                "records": records,
                "total": total,
                "limit": limit,
                "offset": offset,
            })
        except Exception as e:
            logger.error("获取 LLM 监控记录失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  获取单条记录详情
    # ═══════════════════════════════════════════════════

    @app.route("/api/llm-monitor/records/<record_id>", methods=["GET"])
    @trace_route("LLMMonitor")
    def api_llm_monitor_record_detail(record_id):
        """获取单条 LLM 通信记录详情"""
        try:
            monitor = _get_monitor()
            record = monitor.get_record(record_id)
            if not record:
                return jsonify({"ok": False, "error": "记录不存在"}), 404
            return jsonify(record)
        except Exception as e:
            logger.error("获取 LLM 记录详情失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  获取统计概览
    # ═══════════════════════════════════════════════════

    @app.route("/api/llm-monitor/stats", methods=["GET"])
    @trace_route("LLMMonitor")
    def api_llm_monitor_stats():
        """获取 LLM 通信统计概览"""
        try:
            monitor = _get_monitor()
            stats = monitor.get_stats()
            stats.update({
                "enabled": monitor.enabled,
                "max_records": monitor._max,
                "buffer_usage": f"{monitor.record_count}/{monitor._max}",
            })
            return jsonify(stats)
        except Exception as e:
            logger.error("获取 LLM 监控统计失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  启用/禁用/清除
    # ═══════════════════════════════════════════════════

    @app.route("/api/llm-monitor/toggle", methods=["POST"])
    @trace_route("LLMMonitor")
    def api_llm_monitor_toggle():
        """启用或禁用监控"""
        try:
            data = request.get_json() or {}
            enabled = data.get("enabled", True)
            monitor = _get_monitor()
            monitor.set_enabled(enabled)
            return jsonify({"ok": True, "enabled": enabled})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/llm-monitor/clear", methods=["POST"])
    @trace_route("LLMMonitor")
    def api_llm_monitor_clear():
        """清除所有记录"""
        try:
            monitor = _get_monitor()
            count = monitor.record_count
            monitor.clear()
            return jsonify({"ok": True, "cleared": count})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
