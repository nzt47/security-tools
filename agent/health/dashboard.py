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
