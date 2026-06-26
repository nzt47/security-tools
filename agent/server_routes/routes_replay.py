"""用户行为回放 API 路由

提供 rrweb 录制数据上传、查询、获取详情等接口。

API 列表：
    POST /api/replay/upload   上传录制数据
    GET  /api/replay/list     按 trace_id/时间范围查询回放列表
    GET  /api/replay/<id>     获取单条回放元数据
    GET  /api/replay/<id>/data 获取回放数据（用于播放）
    GET  /api/replay/stats    获取关联统计（供 /api/diagnostics/health）

可观测性约束：
- 所有路由通过 @log_request 装饰器捕获请求/响应与错误栈
- 所有路由通过 @trace_route 装饰器自动注入 trace_id
- 上传/查询埋点通过 BusinessMetricsCollector 记录
- 失败路径返回带业务错误码的 JSON
"""
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict

from flask import request, jsonify, Response

from agent.server_auth import log_request
from agent.monitoring.replay_storage import (
    get_replay_storage,
    ReplayStorageError,
)
from .tracing_decorator import trace_route

logger = logging.getLogger(__name__)


def register_routes(app, state):
    """注册回放相关路由

    Args:
        app: Flask 应用实例
        state: 应用状态对象（保留接口一致性，当前未使用）
    """
    storage = get_replay_storage()

    # ═══════════════════════════════════════════════════════════════
    #  上传录制数据
    # ═══════════════════════════════════════════════════════════════

    @app.route("/api/replay/upload", methods=["POST"])
    @trace_route("Replay")
    @log_request(show_response=False)
    def api_replay_upload():
        """上传回放录制数据

        请求体（JSON）：
            replay_id: str         回放唯一 ID（前端生成）
            trace_id: str?         关联的 trace_id
            user_session_id: str?  关联的用户会话 ID
            error_id: str?         关联的错误事件 ID（Sentry）
            timestamp: str?        回放时间戳（ISO 8601）
            duration_sec: int?     回放时长（秒）
            event_count: int?      事件数量
            compressed: bool?      数据是否已压缩
            encoding: str?         编码方式（gzip-base64 / json）
            data: str              录制数据

        状态同步机制：上传成功后返回 replay_id 与 file_path，
        前端可立即通过 /api/replay/<id>/data 拉取验证。
        """
        start = time.time()
        try:
            payload = request.get_json(silent=True) or {}
        except Exception as e:
            return jsonify({
                "ok": False,
                "error_code": "REPLAY_API_001",
                "error": f"请求体解析失败: {e}",
            }), 400

        # 必填字段校验（边界显性化）
        required = ["replay_id", "data"]
        missing = [f for f in required if not payload.get(f)]
        if missing:
            return jsonify({
                "ok": False,
                "error_code": "REPLAY_API_002",
                "error": f"缺少必填字段: {missing}",
            }), 400

        try:
            meta = storage.store(
                replay_id=payload["replay_id"],
                data=payload["data"],
                trace_id=payload.get("trace_id"),
                user_session_id=payload.get("user_session_id"),
                error_id=payload.get("error_id"),
                timestamp=payload.get("timestamp"),
                duration_sec=int(payload.get("duration_sec", 0)),
                event_count=int(payload.get("event_count", 0)),
                compressed=bool(payload.get("compressed", False)),
                encoding=payload.get("encoding", "json"),
            )
        except ReplayStorageError as e:
            logger.warning(
                f"[ReplayAPI] 存储失败 code={e.code} replay_id={payload.get('replay_id')}: {e.message}"
            )
            return jsonify({
                "ok": False,
                "error_code": e.code,
                "error": e.message,
            }), 500
        except (ValueError, TypeError) as e:
            return jsonify({
                "ok": False,
                "error_code": "REPLAY_API_003",
                "error": f"参数类型错误: {e}",
            }), 400

        duration_ms = round((time.time() - start) * 1000, 2)
        logger.info(
            json.dumps({
                "trace_id": payload.get("trace_id", ""),
                "module_name": "routes_replay",
                "action": "upload",
                "duration_ms": duration_ms,
                "replay_id": meta["replay_id"],
                "size_bytes": meta["size_bytes"],
            }, ensure_ascii=False)
        )

        return jsonify({
            "ok": True,
            "replay_id": meta["replay_id"],
            "file_path": meta["file_path"],
            "size_bytes": meta["size_bytes"],
            "created_at": meta["created_at"],
        }), 201

    # ═══════════════════════════════════════════════════════════════
    #  查询回放列表
    # ═══════════════════════════════════════════════════════════════

    @app.route("/api/replay/list", methods=["GET"])
    @trace_route("Replay")
    @log_request(show_response=False)
    def api_replay_list():
        """查询回放列表

        支持的查询参数（任选其一或组合）：
            trace_id: str          按 trace_id 精确匹配
            user_session_id: str  按用户会话 ID 精确匹配
            start_time: str       起始时间（ISO 8601）
            end_time: str         结束时间（ISO 8601）
            limit: int             最大返回数量（默认 50，上限 500）

        状态同步机制：查询不修改数据，仅返回元数据列表。
        """
        trace_id = request.args.get("trace_id")
        user_session_id = request.args.get("user_session_id")
        start_time = request.args.get("start_time")
        end_time = request.args.get("end_time")
        limit = request.args.get("limit", default=50, type=int)

        # 限制 limit 范围
        limit = max(1, min(limit, 500))

        try:
            if trace_id:
                records = storage.list_by_trace_id(trace_id, limit=limit)
            elif user_session_id:
                records = storage.list_by_user_session(user_session_id, limit=limit)
            elif start_time and end_time:
                records = storage.list_by_time_range(start_time, end_time, limit=limit)
            else:
                # 默认返回最近 24 小时
                records = storage.list_recent_24h(limit=limit)
        except Exception as e:
            logger.error(f"[ReplayAPI] 查询列表失败: {e}", exc_info=True)
            return jsonify({
                "ok": False,
                "error_code": "REPLAY_API_004",
                "error": f"查询失败: {e}",
            }), 500

        return jsonify({
            "ok": True,
            "total": len(records),
            "limit": limit,
            "records": records,
        })

    # ═══════════════════════════════════════════════════════════════
    #  获取单条回放元数据
    # ═══════════════════════════════════════════════════════════════

    @app.route("/api/replay/<replay_id>", methods=["GET"])
    @trace_route("Replay")
    @log_request(show_response=False)
    def api_replay_get(replay_id: str):
        """获取单条回放元数据"""
        try:
            meta = storage.get_by_id(replay_id)
        except Exception as e:
            logger.error(f"[ReplayAPI] 获取元数据失败 replay_id={replay_id}: {e}", exc_info=True)
            return jsonify({
                "ok": False,
                "error_code": "REPLAY_API_005",
                "error": f"查询失败: {e}",
            }), 500

        if meta is None:
            return jsonify({
                "ok": False,
                "error_code": "REPLAY_API_006",
                "error": f"回放记录不存在: {replay_id}",
            }), 404

        return jsonify({"ok": True, "record": meta})

    # ═══════════════════════════════════════════════════════════════
    #  获取回放数据（用于播放）
    # ═══════════════════════════════════════════════════════════════

    @app.route("/api/replay/<replay_id>/data", methods=["GET"])
    @trace_route("Replay")
    @log_request(show_response=False)
    def api_replay_data(replay_id: str):
        """获取回放数据（解码后的 JSON 字符串）

        响应：
            200: 返回 JSON 字符串（rrweb 事件数组）
            404: 回放记录不存在
            500: 文件读取或解码失败
        """
        try:
            data = storage.get_data_by_id(replay_id)
        except ReplayStorageError as e:
            logger.warning(f"[ReplayAPI] 获取数据失败 replay_id={replay_id}: {e.message}")
            return jsonify({
                "ok": False,
                "error_code": e.code,
                "error": e.message,
            }), 500

        if data is None:
            return jsonify({
                "ok": False,
                "error_code": "REPLAY_API_006",
                "error": f"回放记录不存在: {replay_id}",
            }), 404

        # 直接返回原始数据（rrweb-player 可直接消费）
        return Response(data, mimetype="application/json")

    # ═══════════════════════════════════════════════════════════════
    #  关联统计（供 /api/diagnostics/health 使用）
    # ═══════════════════════════════════════════════════════════════

    @app.route("/api/replay/stats", methods=["GET"])
    @trace_route("Replay")
    @log_request(show_response=False)
    def api_replay_stats():
        """获取回放关联统计

        查询参数：
            hours: int  统计时间窗口（小时），默认 24
        """
        hours = request.args.get("hours", default=24, type=int)
        hours = max(1, min(hours, 24 * 30))  # 限制在 1 小时 ~ 30 天

        try:
            stats = storage.get_correlation_stats(hours=hours)
        except Exception as e:
            logger.error(f"[ReplayAPI] 关联统计查询失败: {e}", exc_info=True)
            return jsonify({
                "ok": False,
                "error_code": "REPLAY_API_007",
                "error": f"统计查询失败: {e}",
            }), 500

        return jsonify({"ok": True, "stats": stats})

    # ═══════════════════════════════════════════════════════════════
    #  清理过期数据
    # ═══════════════════════════════════════════════════════════════

    @app.route("/api/replay/cleanup", methods=["POST"])
    @trace_route("Replay")
    @log_request()
    def api_replay_cleanup():
        """清理过期回放数据

        请求体（JSON）：
            days: int  保留天数（默认 30）
        """
        payload = request.get_json(silent=True) or {}
        days = max(1, min(int(payload.get("days", 30)), 365))

        try:
            cleaned = storage.cleanup_old_records(days=days)
        except Exception as e:
            logger.error(f"[ReplayAPI] 清理失败: {e}", exc_info=True)
            return jsonify({
                "ok": False,
                "error_code": "REPLAY_API_008",
                "error": f"清理失败: {e}",
            }), 500

        return jsonify({"ok": True, "cleaned": cleaned, "days": days})

    logger.info("[ReplayRoutes] 已注册 6 个回放相关路由")
