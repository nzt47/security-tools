# -*- coding: utf-8 -*-
"""报警器 HTTP 面：`/api/alarms/*`（读 + 已读/确认/静默）。

【契约】
- GET 三个只读端点**不要求令牌**（与 /api/health/dashboard、/api/observability/alerts 同族：
  健康/告警读数本来就是页面可见面的一部分），返回统一信封 `{code,data,message}`。
- POST 四个写端点（read / read-all / ack / silence / collect）要求令牌：它们改的是"谁被告知过"
  的状态，属于写操作（与其它写路由同族）。
- 错误只回固定文案 + 日志，异常原文不进响应（CodeQL py/stack-trace-exposure）。

【为什么 read/ack/silence 都挂在 /api/alarms 而不是 /api/notification】
notification 是"消息中心的历史门面"（分页/已读），alarms 是"事件与当前态"的唯一事实源；
顶栏铃铛（非管理页）消费 alarms，消息中心继续用 notification 投影 —— 两者读同一张表。
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from agent.alarms.store import (
    DEFAULT_SILENCE_MINUTES,
    STATE_FIRING,
    STATE_RESOLVED,
    STATES,
    get_store,
)
from agent.api_envelope import ok as _ok
from agent.server_auth import require_token

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

alarms_bp = Blueprint("alarms", __name__, url_prefix="/api/alarms")

#: 单次列出上限（铃铛只取 5 条；消息中心分页另算）—— 防止一次拉全表
LIST_LIMIT_DEFAULT = 50
LIST_LIMIT_MAX = 500

_ERR_TEXT = "报警器读取失败（详见服务日志）"


def _int_arg(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def register_alarms_api(app) -> Blueprint:
    """注册报警器蓝图（app_server 启动时调用）"""
    app.register_blueprint(alarms_bp)
    logger.info("[启动] 报警器路由已注册 (/api/alarms/*)")
    return alarms_bp


@alarms_bp.route("/list", methods=["GET"])
def alarms_list():
    """报警列表（当前态）。state=firing|resolved，unread_only=1，kind=health|unit|audit"""
    try:
        state = (request.args.get("state") or "").strip() or None
        if state is not None and state not in STATES:
            state = None
        unread_only = (request.args.get("unread_only") or "").strip() in ("1", "true", "yes")
        kind = (request.args.get("kind") or "").strip() or None
        limit = _int_arg("limit", LIST_LIMIT_DEFAULT, minimum=1, maximum=LIST_LIMIT_MAX)
        offset = _int_arg("offset", 0, minimum=0, maximum=10_000)
        store = get_store()
        rows = store.list(state=state, unread_only=unread_only, kind=kind,
                          limit=limit, offset=offset)
        return _ok({"items": rows, "limit": limit, "offset": offset,
                    "total": store.count(state=state), "summary": store.summary()})
    except Exception as exc:  # noqa: BLE001 端点不因单次读失败而崩
        logger.error("报警器列表读取失败: %s", type(exc).__name__)
        return jsonify({"code": 500, "message": _ERR_TEXT, "data": None}), 500


@alarms_bp.route("/unread-count", methods=["GET"])
def alarms_unread():
    """未读数（铃铛徽标用；静默中的不计入）"""
    try:
        store = get_store()
        return _ok({"unread": store.unread_count(), "summary": store.summary()})
    except Exception as exc:  # noqa: BLE001
        logger.error("报警器未读数读取失败: %s", type(exc).__name__)
        return jsonify({"code": 500, "message": _ERR_TEXT, "data": None}), 500


@alarms_bp.route("/<int:alarm_id>/read", methods=["POST"])
@require_token
def alarms_mark_read(alarm_id: int):
    """标记一条已读"""
    if not get_store().mark_read(alarm_id):
        return jsonify({"code": 404, "message": "报警不存在", "data": None}), 404
    return _ok({"id": alarm_id, "read": True})


@alarms_bp.route("/read-all", methods=["POST"])
@require_token
def alarms_mark_all_read():
    """全部标记已读"""
    return _ok({"updated": get_store().mark_all_read()})


@alarms_bp.route("/<int:alarm_id>/ack", methods=["POST"])
@require_token
def alarms_ack(alarm_id: int):
    """确认（ack）：表示"人已知悉"，与已读分开记（谁、何时）"""
    body = request.get_json(silent=True) or {}
    by = str(body.get("by") or "owner")[:32]
    if not get_store().ack(alarm_id, by=by):
        return jsonify({"code": 404, "message": "报警不存在", "data": None}), 404
    return _ok({"id": alarm_id, "acked": True, "acked_by": by})


@alarms_bp.route("/<int:alarm_id>/silence", methods=["POST"])
@require_token
def alarms_silence(alarm_id: int):
    """静默 N 分钟（默认 60；上限 24h）。不改状态、不删除，到期自动重新计入未读。"""
    body = request.get_json(silent=True) or {}
    raw = body.get("minutes", DEFAULT_SILENCE_MINUTES)
    until = get_store().silence(alarm_id, minutes=raw)
    if until is None:
        return jsonify({"code": 404, "message": "报警不存在", "data": None}), 404
    return _ok({"id": alarm_id, "silenced_until": until})


@alarms_bp.route("/collect", methods=["POST"])
@require_token
def alarms_collect():
    """手动跑一轮采集（诊断/验收用；后台线程正常情况下每分钟自己跑）"""
    try:
        from agent.alarms.collector import collect_once
        stats = collect_once()
        return _ok(stats)
    except Exception as exc:  # noqa: BLE001
        logger.error("报警器采集失败: %s", type(exc).__name__)
        return jsonify({"code": 500, "message": _ERR_TEXT, "data": None}), 500
