"""
展现层 — Flask Blueprint 提供日志仪表盘与 REST API

提供：
- /logs/dashboard       — 日志仪表盘页面
- /logs/dashboard/data  — 仪表盘实时数据 API
- /logs/api/stats       — 日志统计
- /logs/api/query       — 日志查询
- /logs/api/errors      — 错误查询
- /logs/api/insights    — 内省洞察
- /logs/api/actions     — 行动建议
- /logs/api/knowledge   — 知识发现
- /logs/api/trends      — 趋势数据
- /logs/api/introspection/run   — 手动触发内省分析
- /logs/api/introspection/status — 内省引擎状态
"""

import time
import json
import uuid
import logging
from datetime import datetime
from flask import Blueprint, jsonify, request, render_template

from .storage import get_storage
from .models import LogQuery, LogCategory, LogLevel
from .analyzer import LogAnalyzer
from .introspection import IntrospectionEngine
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# 创建蓝图
log_system_bp = Blueprint('log_system', __name__, url_prefix='/logs')

# 全局分析器与内省引擎实例
_analyzer = LogAnalyzer()
_introspection = None


def get_introspection():
    """获取内省引擎单例"""
    global _introspection
    if _introspection is None:
        _introspection = IntrospectionEngine()
    return _introspection


# ════════════════════════════════════════════════════════════
# 页面路由
# ════════════════════════════════════════════════════════════

@log_system_bp.route('/dashboard')
def dashboard():
    """日志仪表盘页面"""
    return render_template('log_dashboard.html')


@log_system_bp.route('/dashboard/data')
def dashboard_data():
    """仪表盘实时数据 API"""
    storage = get_storage()
    if not storage:
        return jsonify({'error': '日志系统未初始化'}), 503

    hours = request.args.get('hours', 24, type=float)
    stats = storage.get_stats(hours)

    # 最近的规则分析（使用缓存的分析结果）
    analysis = _analyzer.analyze(hours)

    # 最近的洞察
    insights = storage.query_insights(limit=10)

    # 打开的行动建议
    actions = storage.query_action_items(status='open', limit=10)

    return jsonify({
        'stats': {
            'total_count': stats.total_count,
            'by_category': stats.by_category,
            'by_level': stats.by_level,
            'error_rate': round(stats.error_rate, 4),
            'avg_duration_ms': round(stats.avg_duration_ms, 2),
            'p95_duration_ms': round(stats.p95_duration_ms, 2),
            'top_sources': stats.top_sources[:5],
        },
        'rule_hits': analysis.get('rule_hits', []),
        'anomalies': analysis.get('anomalies', [])[:10],
        'patterns': analysis.get('patterns', [])[:10],
        'recent_insights': insights[:5],
        'open_actions': actions[:5],
        'summary': analysis.get('summary', ''),
        'introspection_status': get_introspection().get_status(),
    })


# ════════════════════════════════════════════════════════════
# 查询 API
# ════════════════════════════════════════════════════════════

@log_system_bp.route('/api/stats')
def api_stats():
    """日志统计 API"""
    storage = get_storage()
    if not storage:
        return jsonify({'error': '日志系统未初始化'}), 503

    hours = request.args.get('hours', 24, type=float)
    stats = storage.get_stats(hours)
    return jsonify({
        'total_count': stats.total_count,
        'by_category': stats.by_category,
        'by_level': stats.by_level,
        'error_rate': round(stats.error_rate, 4),
        'avg_duration_ms': round(stats.avg_duration_ms, 2),
        'p95_duration_ms': round(stats.p95_duration_ms, 2),
        'p99_duration_ms': round(stats.p99_duration_ms, 2),
        'top_sources': stats.top_sources[:10],
        'time_range_hours': hours,
    })


@log_system_bp.route('/api/query')
def api_query():
    """日志查询 API"""
    storage = get_storage()
    if not storage:
        return jsonify({'error': '日志系统未初始化'}), 503

    query = LogQuery(
        start_time=request.args.get('start', 0, type=float),
        end_time=request.args.get('end', 0, type=float),
        source=request.args.get('source', ''),
        user_id=request.args.get('user_id', ''),
        text_search=request.args.get('search', ''),
        limit=min(request.args.get('limit', 100, type=int), 500),
        offset=request.args.get('offset', 0, type=int),
    )

    results = storage.query_operations(query)
    return jsonify({
        'total': len(results),
        'limit': query.limit,
        'offset': query.offset,
        'results': results,
    })


@log_system_bp.route('/api/errors')
def api_errors():
    """错误查询 API"""
    storage = get_storage()
    if not storage:
        return jsonify({'error': '日志系统未初始化'}), 503

    severity = request.args.get('severity')
    start = request.args.get('start', 0, type=float)
    end = request.args.get('end', 0, type=float)
    limit = min(request.args.get('limit', 100, type=int), 500)

    results = storage.query_errors(severity=severity, start=start, end=end, limit=limit)
    return jsonify({'total': len(results), 'results': results})


@log_system_bp.route('/api/insights')
def api_insights():
    """内省洞察 API"""
    storage = get_storage()
    if not storage:
        return jsonify({'error': '日志系统未初始化'}), 503

    insight_type = request.args.get('type')
    limit = min(request.args.get('limit', 20, type=int), 100)
    results = storage.query_insights(insight_type=insight_type, limit=limit)
    return jsonify({'total': len(results), 'results': results})


@log_system_bp.route('/api/actions')
def api_actions():
    """行动建议 API"""
    storage = get_storage()
    if not storage:
        return jsonify({'error': '日志系统未初始化'}), 503

    status = request.args.get('status')
    priority = request.args.get('priority')
    limit = min(request.args.get('limit', 50, type=int), 200)
    results = storage.query_action_items(status=status, priority=priority, limit=limit)

    # 统计
    open_count = sum(1 for r in results if r.get('status') == 'open')
    high_count = sum(1 for r in results if r.get('priority') == 'high')

    return jsonify({
        'total': len(results),
        'open_count': open_count,
        'high_priority_count': high_count,
        'results': results,
    })


@log_system_bp.route('/api/knowledge')
def api_knowledge():
    """知识发现 API"""
    storage = get_storage()
    if not storage:
        return jsonify({'error': '日志系统未初始化'}), 503

    domain = request.args.get('domain')
    limit = min(request.args.get('limit', 50, type=int), 200)
    results = storage.query_knowledge(domain=domain, limit=limit)
    return jsonify({'total': len(results), 'results': results})


@log_system_bp.route('/api/trends')
def api_trends():
    """趋势数据 API"""
    storage = get_storage()
    if not storage:
        return jsonify({'error': '日志系统未初始化'}), 503

    metric = request.args.get('metric', 'response_time')
    hours = request.args.get('hours', 24, type=float)
    bucket = request.args.get('bucket', 10, type=int)

    perf_trend = storage.get_metric_trend(metric, hours, bucket)
    err_trend = storage.get_error_trend(hours, max(bucket * 3, 30))

    return jsonify({
        'metric': metric,
        'hours': hours,
        'performance_trend': perf_trend,
        'error_trend': err_trend,
    })


# ════════════════════════════════════════════════════════════
# 内省管理 API
# ════════════════════════════════════════════════════════════

@log_system_bp.route('/api/introspection/status')
def introspection_status():
    """内省引擎状态 API"""
    engine = get_introspection()
    return jsonify(engine.get_status())


@log_system_bp.route('/api/introspection/run', methods=['POST'])
def introspection_run():
    """手动触发内省分析"""
    engine = get_introspection()
    result = engine.run_cycle(force=True)
    if result:
        return jsonify({'success': True, 'result': result})
    return jsonify({'success': False, 'error': '分析执行失败或在运行中'}), 409


# ════════════════════════════════════════════════════════════
# Prometheus 扩展指标（可选挂载点）
# ════════════════════════════════════════════════════════════

def register_prometheus_metrics(metrics_obj):
    """向 Prometheus 注册日志系统指标"""
    try:
        from prometheus_client import Gauge, Counter

        log_total = Counter('yunshu_log_total', '日志总数', ['category', 'level'])
        error_total = Counter('yunshu_log_errors_total', '错误总数', ['severity'])
        insight_gauge = Gauge('yunshu_log_insights_pending', '待处理洞察数')

        # 挂载到 metrics 对象上避免 GC
        metrics_obj._log_metrics = {
            'log_total': log_total,
            'error_total': error_total,
            'insight_gauge': insight_gauge,
        }
        logger.info(log_dict({'module_name': 'dashboard', 'action': 'prometheus', 'msg': '[LogSystem] Prometheus 指标已注册'}))
    except ImportError:
        logger.warning(log_dict({'module_name': 'dashboard', 'action': 'prometheus_client', 'msg': '[LogSystem] prometheus_client 不可用，跳过指标注册'}))


# ════════════════════════════════════════════════════════════
# 注册蓝图辅助函数
# ════════════════════════════════════════════════════════════

def register_log_system(app, metrics_obj=None):
    """在 Flask 应用中注册日志系统

    用法:
        from agent.log_system.dashboard import register_log_system
        register_log_system(app, metrics)
    """
    app.register_blueprint(log_system_bp)

    # 注册 Prometheus 指标
    if metrics_obj:
        register_prometheus_metrics(metrics_obj)

    logger.info(log_dict({'module_name': 'dashboard', 'action': 'api', 'msg': '[LogSystem] 仪表盘与 API 路由已注册'}))

    # 启动内省引擎后台循环
    engine = get_introspection()
    engine.start_background_loop(interval_seconds=1800)

    return engine
