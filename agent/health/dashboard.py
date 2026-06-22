"""健康看板 API"""
from flask import Blueprint, jsonify
from agent.health.assessor import health_assessor

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
