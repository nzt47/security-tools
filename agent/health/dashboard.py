"""健康看板 API"""
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from agent.health.assessor import health_assessor
from agent.health.probes import run_all_probes
from agent.health.storage import health_storage

health_bp = Blueprint('health', __name__)


@health_bp.route('/api/health/dashboard', methods=['GET'])
def dashboard():
    health = health_assessor.assess()
    return jsonify({
        "overall_health": health.overall,
        "dimensions": health.dimensions,
        "issues": health.issues,
        "history": [{"timestamp": h.timestamp, "overall": h.overall} for h in health_assessor.get_history(10)],
    })


def get_probe_overview(storage=None) -> dict:
    """五层探针当前状态概览（供 Dashboard / 集成测试）"""
    probes = run_all_probes()
    score = health_assessor.assess_with_probes(probes)
    layers = [
        {
            "layer": layer,
            "score": detail["score"],
            "available": detail["available"],
            "detail": detail["detail"],
        }
        for layer, detail in score.probe_details.items()
    ]
    return {"overall": score.overall, "issues": score.issues, "layers": layers}


def get_trend(days: int = 7, storage=None) -> dict:
    """最近 N 天健康评分按天聚合趋势（同一天多条记录取均值）"""
    storage = storage if storage is not None else health_storage
    cutoff = datetime.now() - timedelta(days=days)
    records = storage.query_history()

    by_day: dict = {}
    total = 0
    for record in records:
        timestamp = record.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(timestamp)
        except (TypeError, ValueError):
            continue
        if ts < cutoff:
            continue
        overall = record.get("overall")
        if overall is None:
            continue
        by_day.setdefault(ts.date().isoformat(), []).append(overall)
        total += 1

    trend = [
        {"date": day, "overall": sum(vals) / len(vals)}
        for day, vals in sorted(by_day.items())
    ]
    return {"days": days, "total_records": total, "trend": trend}


def get_probe_trend(hours: int = 1, storage=None) -> dict:
    """最近 N 小时健康评分时间序列（含五层探针明细，供 Dashboard 图表）"""
    storage = storage if storage is not None else health_storage
    cutoff = datetime.now() - timedelta(hours=hours)
    records = storage.query_history()

    series = []
    for record in records:
        timestamp = record.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(timestamp)
        except (TypeError, ValueError):
            continue
        if ts < cutoff:
            continue
        series.append({
            "timestamp": timestamp,
            "overall": record.get("overall"),
            "dimensions": record.get("dimensions"),
            "probe_details": record.get("probe_details"),
        })
    return {"hours": hours, "points": series, "total_records": len(series)}


@health_bp.route('/api/health/probe-trend', methods=['GET'])
def probe_trend():
    hours = request.args.get("hours", 1, type=int)
    return jsonify(get_probe_trend(hours=hours))


def get_evolution_audit(days: int = 7, archive=None, approvals=None,
                        recent_limit: int = 20) -> dict:
    """进化审计统计视图（EVO-T6 验收 6，供服务网关/仪表盘消费）

    Args:
        days: 统计窗口（天），仅统计窗口内记录
        archive: agent.skills_mgmt.lineage.EvolutionArchive 实例
                 （None=无谱系数据，返回空视图不抛异常）
        approvals: agent.skills_mgmt.approval.ApprovalFlow 实例
                   （None=不输出 approval_stats，兼容未接入审批流场景）
        recent_limit: 近期事件条数上限

    Returns:
        {
          "recent_events": [...],        # 窗口内近期事件（时间倒序，限 recent_limit）
          "decision_stats": {...},       # 按 decision 计数
          "object_score_trend": [...],   # 按对象分组、时间升序的评分序列
          "cost_summary": {...},         # total_events / total_tokens
          "approval_stats": {...},       # 仅注入 approvals 时输出
        }
    """
    cutoff = datetime.now() - timedelta(days=days)
    records = list(archive.query()) if archive is not None else []

    def in_window(rec) -> bool:
        try:
            return datetime.fromisoformat(rec.created_at) >= cutoff
        except (TypeError, ValueError):
            return True  # 时间缺失按窗口内处理（保守不排除）

    windowed = [r for r in records if in_window(r)]

    # 近期事件（时间倒序）
    recent_events = []
    for rec in sorted(windowed, key=lambda r: r.created_at,
                      reverse=True)[:recent_limit]:
        recent_events.append({
            "record_id": rec.record_id,
            "object_id": rec.object_id,
            "object_type": rec.object_type,
            "new_version": rec.new_version,
            "parent_version": rec.parent_version,
            "decision": rec.decision,
            "decision_reason": rec.decision_reason,
            "created_at": rec.created_at,
            "score": rec.get_score(),
        })

    # 决策统计
    decision_stats: dict = {}
    for rec in windowed:
        decision_stats[rec.decision] = decision_stats.get(rec.decision, 0) + 1

    # 评分趋势（按对象分组、时间升序）
    by_obj: dict = {}
    for rec in sorted(windowed, key=lambda r: r.created_at):
        score = rec.get_score()
        if score is None:
            continue
        by_obj.setdefault(rec.object_id, []).append({"score": score})
    object_score_trend = [
        {"object_id": oid, "series": series}
        for oid, series in by_obj.items()
    ]

    # 成本汇总
    total_tokens = 0
    for rec in windowed:
        total_tokens += (rec.cost or {}).get("tokens", 0)
    cost_summary = {"total_events": len(windowed), "total_tokens": total_tokens}

    view = {
        "recent_events": recent_events,
        "decision_stats": decision_stats,
        "object_score_trend": object_score_trend,
        "cost_summary": cost_summary,
    }
    if approvals is not None:
        stats = approvals.stats()
        view["approval_stats"] = {
            "total": stats.get("total", 0),
            "pending": stats.get("pending", 0),
            "merged": stats.get("merged", 0),
            "rejected": stats.get("rejected", 0),
        }
    return view
