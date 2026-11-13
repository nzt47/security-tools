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


@alarms_bp.route("/rules", methods=["GET"])
def alarms_rules():
    """规则求值状态 + 覆盖率（哪些规则真能算、哪些因为没指标/没样本算不了）"""
    try:
        from agent.alarms import rules as rule_engine
        payload = {"rules": rule_engine.snapshot(), "coverage": rule_engine.coverage()}
        try:
            from agent.alarms.samples import get_sample_store
            payload["samples"] = get_sample_store().size()
        except Exception:  # noqa: BLE001 采样环读不到不影响规则状态
            payload["samples"] = None
        return _ok(payload)
    except Exception as exc:  # noqa: BLE001
        logger.error("报警器规则状态读取失败: %s", type(exc).__name__)
        return jsonify({"code": 500, "message": _ERR_TEXT, "data": None}), 500


#: consistency 自检回看多少条事件（与 store 默认值同量级；够覆盖当前台账）
_CONSISTENCY_EVENT_LINES = 5000


def _perception_gap() -> dict:
    """感知面「最后事件时间」读数（只读状态文件；异常只回固定文案）。"""
    try:
        from agent.learning.perception_cycle import gap_summary
        return gap_summary()
    except Exception as exc:  # noqa: BLE001
        logger.warning("报警器：感知覆盖读数失败（%s）", type(exc).__name__)
        return {"available": False, "detail": "感知覆盖读数不可用（详见服务日志）"}


def _notify_status() -> dict:
    """告警外发读数（只读：把 events.jsonl 行数与游标对一下，不发送任何东西）。"""
    try:
        from agent.alarms.notify import status
        return status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("报警器：外发读数失败（%s）", type(exc).__name__)
        return {"available": False, "detail": "外发读数不可用（详见服务日志）"}


def _ci_gap() -> dict:
    """CI 事件源「最后同步时间」读数（只读 status.json；不联网）。"""
    try:
        from agent.monitoring.ci_status import gap_summary
        return gap_summary()
    except Exception as exc:  # noqa: BLE001
        logger.warning("报警器：CI 覆盖读数失败（%s）", type(exc).__name__)
        return {"available": False, "detail": "CI 覆盖读数不可用（详见服务日志）"}


def _coverage_gaps() -> dict:
    """覆盖缺口自检读数（只读状态文件；**不触发**自检、不外发）。

    2026-11-12（monitoring-gap-detector）：回答"新增了但没人感知/监控/告警的能力有哪些"。
    这里只读上一轮结果 —— 拉一次页面不该等于跑一次全仓扫描（也不该顺手往外推消息）。
    """
    try:
        from agent.monitoring.coverage_gaps import summary as gap_summary
        return gap_summary()
    except Exception as exc:  # noqa: BLE001
        logger.warning("报警器：覆盖缺口读数失败（%s）", type(exc).__name__)
        return {"available": False, "count": None, "gaps": [],
                "detail": "覆盖缺口读数不可用（详见服务日志）"}


@alarms_bp.route("/coverage", methods=["GET"])
def alarms_coverage():
    """报警器覆盖读数：事件源清单 + 规则覆盖率 + 上一轮采集统计 + 两个"静默缺口"。

    回答"它到底看得到什么、看不到什么"——看不到的部分**如实列出来**，
    而不是让页面上一片绿（L5：没有测量不等于没有问题）。

    2026-11-12 追加两个"过期即静默"的读数（此前只能靠人翻日志发现）：
    - `ci`：data/ci/status.json 的最后同步时间与是否已超 2h 阈值；
    - `perception`：感知周期采集的最后 tick / 最后文件事件 / 监听目录 / watcher 是否在跑。

    2026-11-12 追加 `gaps`（覆盖缺口自检，monitoring-gap-detector）：
    count / by_kind / 逐条 {kind,id,why,fix} / surfaces（每个输入面读到了什么）/ degraded。
    口径：gap=0 只说"这一轮没发现新缺口"，**不说**"全域覆盖已完成"——读不到的输入面
    会进 degraded，规则算不了的 10 条仍在 rules.blocked 里单列（不在 gaps 里重复计数）。
    """
    try:
        from agent.alarms import rules as rule_engine
        from agent.alarms.collector import last_stats
        sources = [
            {"id": "health", "name": "健康五层探针", "detail": "L1/L2/L3/L4 低于阈值报警；L5 无数据不报警"},
            {"id": "units", "name": "功能单位看板", "detail": "12 个单位，只报 warn/error（never/off 不报）"},
            {"id": "rules", "name": "告警规则（alerts.yml）", "detail": "16 条 PromQL 子集在本进程求值"},
            {"id": "audit", "name": "审计链", "detail": "尾部 500 条重算不一致 = critical（每小时最多一次）"},
            {"id": "scheduler", "name": "计划任务",
             "detail": "近 6 小时执行历史里 status=failed 的任务（读 data/task_history.jsonl）"},
            {"id": "ci", "name": "CI 结果",
             "detail": "data/ci/status.json（由 scripts/sync_ci_status.py 或 30 分钟周期任务同步 gh 结果）；"
                       "超过 2 小时未同步按不可用（最后同步时间见 coverage.ci）"},
        ]
        coverage = {
            "sources": sources,
            "rules": rule_engine.coverage(),
            "last_collect": last_stats(),
        }
        # 外部 Prometheus（可选）：没配置就如实说"未配置"，配置了给最近一轮是否可用。
        try:
            from agent.alarms import prometheus_source
            coverage["prometheus"] = {
                "configured": prometheus_source.configured(),
                "url_set": bool(prometheus_source.configured()),
                "last_ok": prometheus_source.last_ok(),
                "detail": ("已配置：每轮拉一次 instant up，供 up{job=...} 类规则求值"
                           if prometheus_source.configured()
                           else "未配置（CP_PROMETHEUS_URL 为空）：up{} 类规则只能报 no_data"),
            }
        except Exception:  # noqa: BLE001 外部源模块坏了不影响其余覆盖读数
            coverage["prometheus"] = None
        # 【两个"静默缺口"（2026-11-12）】此前这两处过期都是**无声的**：
        # ① CI 源离线时 last_collect.checked.ci=false 只说"本轮没读到"，不说"上次同步是几点"；
        # ② 感知面只有 enabled 开关，没有"最后一次事件是什么时候" ⇒ "没在感知"读起来像"没变化"。
        # 两个读数都只读本地文件、不联网、不创建任何实例；读不到就 available=false（不假装正常）。
        coverage["perception"] = _perception_gap()
        coverage["ci"] = _ci_gap()
        # 告警外发（2026-11-12）：开关 / 游标 / 待发条数（读不到就 available=false）
        coverage["notify"] = _notify_status()
        # 覆盖缺口（2026-11-12，monitoring-gap-detector）：新增能力有没有被感知/监控/告警覆盖。
        # **只读状态文件**（自检由每小时周期任务跑）—— 打开告警页不该触发一次全仓扫描。
        coverage["gaps"] = _coverage_gaps()
        # 【2026-11-13】state 与事件流的一致性自检（"绿色已恢复"可信度的前提）。
        # 只读事件文件末尾若干行 + 内存台账，不写任何东西；dirty=True 表示上一轮落盘失败。
        try:
            coverage["consistency"] = get_store().consistency(limit_events=_CONSISTENCY_EVENT_LINES)
        except Exception as exc:  # noqa: BLE001 自检失败不影响覆盖读数
            logger.warning("报警器：一致性自检失败（%s）", type(exc).__name__)
            coverage["consistency"] = {"available": False}
        return _ok(coverage)
    except Exception as exc:  # noqa: BLE001
        logger.error("报警器覆盖读数失败: %s", type(exc).__name__)
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
