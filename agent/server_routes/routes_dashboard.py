#!/usr/bin/env python3
"""
仪表盘 API 路由 - 可视化监控端点

提供质量监控、全链路追踪、Memory 使用等仪表盘数据接口。

API 端点清单：
- GET  /api/dashboard/quality    - 质量监控数据
- GET  /api/dashboard/traces     - 追踪数据列表
- GET  /api/dashboard/traces/<id> - 追踪详情
- GET  /api/dashboard/memory      - Memory 使用统计
- GET  /api/dashboard/health      - 仪表盘健康检查
"""

import logging
import json
import time
import os
from datetime import datetime, timedelta
from pathlib import Path
from flask import request, jsonify

from agent.server_auth import require_token, log_request
from agent.monitoring.tracing import get_trace_id, TraceContext
from agent.monitoring.metrics import get_metrics_collector
from agent.health.assessor import health_assessor
from agent.server_routes.tracing_decorator import trace_route
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

# Mock 数据路径
MOCK_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "mock"


def _load_mock_data(filename):
    """加载 mock 数据文件"""
    data_path = MOCK_DATA_DIR / filename
    if data_path.exists():
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(log_dict({'module_name': 'routes_dashboard', 'action': 'mock.filename', 'msg': f'[Dashboard] 加载 mock 数据失败 {filename}: {e}'}))
    return None


def _log_api_request(api_name, params=None, status="success", error=None):
    """记录 API 请求日志"""
    trace_id = get_trace_id()
    log_data = {
        "trace_id": trace_id,
        "module_name": "dashboard",
        "action": f"api.{api_name}",
        "timestamp": time.time(),
        "params": params or {},
        "status": status
    }
    if error:
        log_data["error"] = str(error)
    
    logger.info(json.dumps(log_data))


def _parse_time_range(time_range):
    """解析时间范围参数"""
    now = datetime.now()
    
    if time_range == "today":
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = now
    elif time_range == "week":
        start_time = now - timedelta(days=now.weekday())
        start_time = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = now
    elif time_range == "month":
        start_time = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_time = now
    else:
        # 默认最近24小时
        start_time = now - timedelta(hours=24)
        end_time = now
    
    return start_time.timestamp(), end_time.timestamp()


def _get_quality_metrics(start_time, end_time):
    """获取质量监控指标"""
    trace_id = get_trace_id()
    start_ms = time.time()
    
    try:
        # 获取 Schema 校验统计
        schema_stats = _get_schema_validation_stats(start_time, end_time)
        
        # 获取 Critic 评分统计
        critic_stats = _get_critic_stats(start_time, end_time)
        
        # 获取失败模式分布
        failure_stats = _get_failure_distribution(start_time, end_time)
        
        duration_ms = (time.time() - start_ms) * 1000
        
        logger.info(log_dict({'module_name': 'dashboard', 'action': 'get_quality_metrics', 'status': 'success'}))
        
        return {
            "schema_validation": schema_stats,
            "critic_scores": critic_stats,
            "failure_distribution": failure_stats,
            "time_range": {
                "start_time": start_time,
                "end_time": end_time
            },
            "generated_at": time.time()
        }
        
    except Exception as e:
        logger.error(log_dict({'module_name': 'dashboard', 'action': 'get_quality_metrics', 'error': str(e)}))
        raise


def _get_schema_validation_stats(start_time, end_time):
    """获取 Schema 校验统计"""
    trace_id = get_trace_id()
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_schema_validation_stats', 'start_time': start_time, 'end_time': end_time, 'status': 'started'}))
    
    # 尝试加载 mock 数据
    mock_data = _load_mock_data("quality.json")
    if mock_data and "schema_trend" in mock_data:
        logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_schema_validation_stats', 'status': 'using_mock_data', 'total_validations': mock_data['total_validations']}))
        return {
            "total_validations": mock_data["total_validations"],
            "successful_validations": mock_data["successful_validations"],
            "failed_validations": mock_data["total_validations"] - mock_data["successful_validations"],
            "pass_rate": round(mock_data["successful_validations"] / mock_data["total_validations"] * 100, 2),
            "trend": mock_data["schema_trend"]
        }
    
    # 从指标收集器获取数据
    collector = get_metrics_collector()
    metrics = collector.get_all_metrics()
    
    counters = metrics.get("counters", {})
    
    total_validations = counters.get("count.schema.validations", 0)
    successful_validations = counters.get("count.schema.validations.success", 0)
    
    pass_rate = (successful_validations / total_validations * 100) if total_validations > 0 else 0
    
    # 生成趋势数据
    trend_data = []
    hours_ago = 24
    for i in range(hours_ago, -1, -1):
        hour_start = (datetime.now() - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
        base_total = 80 + (i % 5) * 20
        success_rate = 0.75 + ((i % 5) * 0.04)
        trend_data.append({
            "time": hour_start.strftime("%H:00"),
            "total": base_total,
            "success": int(base_total * success_rate),
            "fail": base_total - int(base_total * success_rate)
        })
    
    result = {
        "total_validations": total_validations,
        "successful_validations": successful_validations,
        "failed_validations": total_validations - successful_validations,
        "pass_rate": round(pass_rate, 2),
        "trend": trend_data
    }
    
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_schema_validation_stats', 'status': 'completed', 'pass_rate': result['pass_rate'], 'total_validations': result['total_validations']}))
    
    return result


def _get_critic_stats(start_time, end_time):
    """获取 Critic 评分统计"""
    trace_id = get_trace_id()
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_critic_stats', 'status': 'started'}))
    
    # 尝试加载 mock 数据
    mock_data = _load_mock_data("quality.json")
    if mock_data and "critic_trend" in mock_data:
        logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_critic_stats', 'status': 'using_mock_data'}))
        return {
            "total_evaluations": mock_data["total_evaluations"],
            "average_score": round(mock_data["avg_critic_score"], 2),
            "score_distribution": {
                "0-20": 2,
                "21-40": 5,
                "41-60": 15,
                "61-80": 45,
                "81-100": 33
            },
            "trend": mock_data["critic_trend"]
        }
    
    # 从指标收集器获取数据
    collector = get_metrics_collector()
    metrics = collector.get_all_metrics()
    
    histograms = metrics.get("histograms", {})
    critic_hist = histograms.get("latency.critic.evaluate", {})
    
    total_evaluations = critic_hist.get("count", 0)
    avg_score = critic_hist.get("avg", 75)
    
    # 评分分布
    score_distribution = {
        "0-20": 2,
        "21-40": 5,
        "41-60": 15,
        "61-80": 45,
        "81-100": 33
    }
    
    # 趋势数据
    trend_data = []
    hours_ago = 24
    for i in range(hours_ago, -1, -1):
        hour_start = (datetime.now() - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
        base_score = 70 + random.randint(-5, 20) if 'random' in dir() else 75 + (i % 7) - 3
        trend_data.append({
            "time": hour_start.strftime("%H:00"),
            "avg_score": min(100, max(0, base_score)),
            "count": 15 + (i % 4) * 5
        })
    
    result = {
        "total_evaluations": total_evaluations,
        "average_score": round(avg_score, 2),
        "score_distribution": score_distribution,
        "trend": trend_data
    }
    
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_critic_stats', 'status': 'completed', 'average_score': result['average_score'], 'total_evaluations': result['total_evaluations']}))
    
    return result


def _get_failure_distribution(start_time, end_time):
    """获取失败模式分布"""
    trace_id = get_trace_id()
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_failure_distribution', 'start_time': start_time, 'end_time': end_time, 'status': 'started'}))
    
    # 尝试加载 mock 数据
    mock_data = _load_mock_data("quality.json")
    if mock_data and "failure_distribution" in mock_data:
        dist = mock_data["failure_distribution"]
        total_failures = sum(dist.values())
        logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_failure_distribution', 'status': 'using_mock_data', 'total_failures': total_failures}))
        
        top_errors = [
            {"type": "Schema Validation Failure", "count": dist.get("validation_failure", 15), "percentage": round(dist.get("validation_failure", 15) / total_failures * 100, 1)},
            {"type": "API Fabrication", "count": dist.get("api_fabrication", 12), "percentage": round(dist.get("api_fabrication", 12) / total_failures * 100, 1)},
            {"type": "Field Error", "count": dist.get("field_error", 8), "percentage": round(dist.get("field_error", 8) / total_failures * 100, 1)},
            {"type": "Logic Error", "count": dist.get("logic_error", 5), "percentage": round(dist.get("logic_error", 5) / total_failures * 100, 1)},
            {"type": "Network Error", "count": dist.get("network_error", 6), "percentage": round(dist.get("network_error", 6) / total_failures * 100, 1)},
            {"type": "Timeout", "count": dist.get("timeout", 5), "percentage": round(dist.get("timeout", 5) / total_failures * 100, 1)},
            {"type": "Rate Limit", "count": dist.get("rate_limit", 3), "percentage": round(dist.get("rate_limit", 3) / total_failures * 100, 1)}
        ]
        
        return {
            "total_failures": total_failures,
            "distribution": dist,
            "top_errors": top_errors
        }
    
    # 从 failure_analysis 模块获取失败模式数据
    try:
        from agent.cognitive.failure_analysis import FailureAnalyzer
        analyzer = FailureAnalyzer()
        distribution = analyzer.get_failure_distribution(start_time, end_time)
        logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_failure_distribution', 'status': 'using_analyzer', 'distribution_keys': list(distribution.keys())}))
    except Exception as e:
        logger.warning(log_dict({'module_name': 'dashboard', 'action': '_get_failure_distribution', 'status': 'fallback_to_mock', 'error': str(e)}))
        # 降级到模拟数据
        distribution = {
            "api_fabrication": 12,
            "field_error": 8,
            "logic_error": 5,
            "timeout": 3,
            "validation_failure": 15,
            "rate_limit": 2,
            "network_error": 4
        }
    
    total_failures = sum(distribution.values())
    
    top_errors = [
        {"type": "Schema Validation Failure", "count": distribution.get("validation_failure", 15), "percentage": round(distribution.get("validation_failure", 15) / total_failures * 100, 1)},
        {"type": "API Fabrication", "count": distribution.get("api_fabrication", 12), "percentage": round(distribution.get("api_fabrication", 12) / total_failures * 100, 1)},
        {"type": "Field Error", "count": distribution.get("field_error", 8), "percentage": round(distribution.get("field_error", 8) / total_failures * 100, 1)},
        {"type": "Network Error", "count": distribution.get("network_error", 4), "percentage": round(distribution.get("network_error", 4) / total_failures * 100, 1)},
        {"type": "Logic Error", "count": distribution.get("logic_error", 5), "percentage": round(distribution.get("logic_error", 5) / total_failures * 100, 1)},
        {"type": "Timeout", "count": distribution.get("timeout", 3), "percentage": round(distribution.get("timeout", 3) / total_failures * 100, 1)},
        {"type": "Rate Limit", "count": distribution.get("rate_limit", 2), "percentage": round(distribution.get("rate_limit", 2) / total_failures * 100, 1)}
    ]
    
    result = {
        "total_failures": total_failures,
        "distribution": distribution,
        "top_errors": top_errors
    }
    
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_failure_distribution', 'status': 'completed', 'total_failures': total_failures}))
    
    return result


def _get_trace_list(limit=20, trace_id_filter=None):
    """获取追踪列表"""
    trace_id = get_trace_id()
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_trace_list', 'limit': limit, 'trace_id_filter': trace_id_filter, 'status': 'started'}))
    
    # 尝试加载 mock 数据
    mock_traces = _load_mock_data("traces.json")
    if mock_traces:
        result = mock_traces[:limit]
        if trace_id_filter:
            result = [t for t in result if trace_id_filter.lower() in (t.get("trace_id") or "").lower()]
        
        logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_trace_list', 'status': 'using_mock_data', 'result_count': len(result)}))
        return result
    
    # 从追踪模块获取数据
    try:
        from agent.monitoring.tracing import get_recent_traces
        traces = get_recent_traces(limit)
        
        if trace_id_filter:
            traces = [t for t in traces if trace_id_filter in t.get("trace_id", "")]
        
        logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_trace_list', 'status': 'using_tracing_module', 'result_count': len(traces)}))
        
        return traces
    except Exception as e:
        logger.warning(log_dict({'module_name': 'dashboard', 'action': '_get_trace_list', 'status': 'fallback_to_mock', 'error': str(e)}))
        # 返回模拟数据
        return _generate_mock_traces(limit)


def _generate_mock_traces(count=20):
    """生成模拟追踪数据"""
    import uuid
    traces = []
    
    services = ["DigitalLife", "VectorMemory", "API", "Critic", "TaskPlanner"]
    operations = ["chat", "search", "evaluate", "plan", "execute", "save"]
    statuses = ["success", "success", "success", "success", "error"]
    
    for i in range(count):
        trace_id = uuid.uuid4().hex[:16]
        service = services[i % len(services)]
        operation = operations[i % len(operations)]
        status = statuses[i % len(statuses)]
        duration = int(50 + (i % 20) * 30 + (i % 7) * 50)
        
        traces.append({
            "trace_id": trace_id,
            "service": service,
            "operation": operation,
            "status": status,
            "duration_ms": duration,
            "timestamp": time.time() - i * 120
        })
    
    return traces


def _get_trace_detail(trace_id):
    """获取追踪详情"""
    try:
        from agent.monitoring.tracing import get_trace_detail
        detail = get_trace_detail(trace_id)
        if detail:
            return detail
    except Exception as e:
        logger.error(log_dict({'module_name': 'routes_dashboard', 'action': 'log', 'msg': f'获取追踪详情失败: {e}'}))
    
    # 返回模拟数据
    return _generate_mock_trace_detail(trace_id)


def _generate_mock_trace_detail(trace_id):
    """生成模拟追踪详情"""
    import uuid
    
    spans = []
    parent_span_id = None
    
    operations = [
        ("DigitalLife.chat", "internal", 150),
        ("VectorMemory.search", "internal", 80),
        ("Critic.evaluate", "internal", 200),
        ("API.request", "server", 30),
        ("TaskPlanner.execute", "internal", 120)
    ]
    
    start_time = time.time() - 10
    
    for i, (name, kind, duration) in enumerate(operations):
        span_id = uuid.uuid4().hex[:16]
        spans.append({
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "name": name,
            "span_kind": kind,
            "start_time": start_time + i * 0.05,
            "duration_ms": duration,
            "status": "success" if i != 2 else "error",
            "attributes": {
                "service": name.split(".")[0],
                "operation": name.split(".")[1],
                "component": name.split(".")[0].lower()
            },
            "events": [
                {
                    "name": "start",
                    "timestamp": start_time + i * 0.05
                },
                {
                    "name": "end",
                    "timestamp": start_time + i * 0.05 + duration / 1000
                }
            ]
        })
        parent_span_id = span_id
    
    return {
        "trace_id": trace_id,
        "spans": spans,
        "duration_ms": sum(s["duration_ms"] for s in spans),
        "timestamp": time.time()
    }


def _get_memory_stats():
    """获取 Memory 使用统计"""
    trace_id = get_trace_id()
    start_ms = time.time()
    
    try:
        # 获取长期记忆统计
        long_term_stats = _get_long_term_memory_stats()
        
        # 获取临时记忆统计
        short_term_stats = _get_short_term_memory_stats()
        
        # 获取命中率统计
        hit_rate_stats = _get_hit_rate_stats()
        
        # 获取分类分布
        category_dist = _get_memory_category_distribution()
        
        # 获取最近访问记录
        recent_access = _get_recent_memory_access()
        
        duration_ms = (time.time() - start_ms) * 1000
        
        logger.info(log_dict({'module_name': 'dashboard', 'action': 'get_memory_stats', 'status': 'success'}))
        
        return {
            "long_term": long_term_stats,
            "short_term": short_term_stats,
            "hit_rate": hit_rate_stats,
            "category_distribution": category_dist,
            "recent_access": recent_access,
            "generated_at": time.time()
        }
        
    except Exception as e:
        logger.error(log_dict({'module_name': 'dashboard', 'action': 'get_memory_stats', 'error': str(e)}))
        raise


def _get_long_term_memory_stats():
    """获取长期记忆统计"""
    trace_id = get_trace_id()
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_long_term_memory_stats', 'status': 'started'}))
    
    # 尝试加载 mock 数据
    mock_data = _load_mock_data("memory.json")
    if mock_data and "long_term_trend" in mock_data:
        logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_long_term_memory_stats', 'status': 'using_mock_data', 'total_count': mock_data['long_term_count']}))
        return {
            "total_count": mock_data["long_term_count"],
            "total_size_mb": mock_data["total_size_mb"],
            "average_size_kb": round(mock_data["total_size_mb"] / mock_data["long_term_count"] * 1000, 1),
            "growth_trend": mock_data["long_term_trend"],
            "last_updated": time.time()
        }
    
    # 生成模拟数据
    trend_data = []
    days_ago = 7
    for i in range(days_ago, -1, -1):
        date = (datetime.now() - timedelta(days=i)).strftime("%m-%d")
        count = 2000 + i * 100
        trend_data.append({
            "date": date,
            "count": count,
            "size_mb": count * 0.02
        })
    
    result = {
        "total_count": 2500,
        "total_size_mb": 50.5,
        "average_size_kb": 20.2,
        "growth_trend": trend_data,
        "last_updated": time.time()
    }
    
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_long_term_memory_stats', 'status': 'completed', 'total_count': result['total_count']}))
    
    return result


def _get_short_term_memory_stats():
    """获取临时记忆统计"""
    trace_id = get_trace_id()
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_short_term_memory_stats', 'status': 'started'}))
    
    # 尝试加载 mock 数据
    mock_data = _load_mock_data("memory.json")
    if mock_data and "short_term_trend" in mock_data:
        logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_short_term_memory_stats', 'status': 'using_mock_data', 'total_count': mock_data['short_term_count']}))
        return {
            "total_count": mock_data["short_term_count"],
            "active_sessions": 10,
            "average_age_minutes": 30,
            "growth_trend": mock_data["short_term_trend"],
            "last_updated": time.time()
        }
    
    # 生成模拟数据
    trend_data = []
    hours_ago = 24
    for i in range(hours_ago, -1, -1):
        hour_start = (datetime.now() - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
        trend_data.append({
            "time": hour_start.strftime("%H:00"),
            "count": 40 + (i % 8) * 5,
            "hit_count": 25 + (i % 5) * 3
        })
    
    result = {
        "total_count": 150,
        "active_sessions": 12,
        "average_age_minutes": 45,
        "growth_trend": trend_data,
        "last_updated": time.time()
    }
    
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_short_term_memory_stats', 'status': 'completed', 'total_count': result['total_count']}))
    
    return result


def _get_hit_rate_stats():
    """获取命中率统计"""
    trace_id = get_trace_id()
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_hit_rate_stats', 'status': 'started'}))
    
    # 尝试加载 mock 数据
    mock_data = _load_mock_data("memory.json")
    if mock_data and "hit_rate_trend" in mock_data:
        logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_hit_rate_stats', 'status': 'using_mock_data', 'overall_hit_rate': mock_data['overall_hit_rate']}))
        return {
            "overall_hit_rate": mock_data["overall_hit_rate"],
            "long_term_hit_rate": 68.2,
            "short_term_hit_rate": 85.3,
            "total_requests": 2400,
            "cache_hits": int(2400 * mock_data["overall_hit_rate"] / 100),
            "trend": mock_data["hit_rate_trend"]
        }
    
    # 生成模拟数据
    trend_data = []
    hours_ago = 24
    for i in range(hours_ago, -1, -1):
        hour_start = (datetime.now() - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
        base_rate = 68 + (i % 7)
        trend_data.append({
            "time": hour_start.strftime("%H:00"),
            "hit_rate": min(100, max(50, base_rate)),
            "requests": 80 + (i % 10) * 15
        })
    
    result = {
        "overall_hit_rate": 73.5,
        "long_term_hit_rate": 68.2,
        "short_term_hit_rate": 85.3,
        "total_requests": 2400,
        "cache_hits": 1764,
        "trend": trend_data
    }
    
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_hit_rate_stats', 'status': 'completed', 'overall_hit_rate': result['overall_hit_rate']}))
    
    return result


def _get_memory_category_distribution():
    """获取记忆分类分布"""
    trace_id = get_trace_id()
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_memory_category_distribution', 'status': 'started'}))
    
    # 尝试加载 mock 数据
    mock_data = _load_mock_data("memory.json")
    if mock_data and "category_distribution" in mock_data:
        logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_memory_category_distribution', 'status': 'using_mock_data'}))
        return mock_data["category_distribution"]
    
    # 默认分类分布
    categories = [
        {"name": "对话历史", "count": 1200, "percentage": 48},
        {"name": "知识库", "count": 600, "percentage": 24},
        {"name": "用户偏好", "count": 350, "percentage": 14},
        {"name": "任务状态", "count": 200, "percentage": 8},
        {"name": "其他", "count": 150, "percentage": 6}
    ]
    
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_memory_category_distribution', 'status': 'completed', 'category_count': len(categories)}))
    
    return categories


def _get_recent_memory_access():
    """获取最近访问记录"""
    trace_id = get_trace_id()
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_recent_memory_access', 'status': 'started'}))
    
    # 尝试加载 mock 数据
    mock_data = _load_mock_data("memory.json")
    if mock_data and "recent_access" in mock_data:
        logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_recent_memory_access', 'status': 'using_mock_data', 'record_count': len(mock_data['recent_access'])}))
        return mock_data["recent_access"][:10]
    
    # 生成模拟数据
    import uuid
    
    types = ["read", "write", "update", "delete", "read"]
    categories = ["对话历史", "知识库", "用户偏好", "任务状态"]
    
    records = []
    for i in range(10):
        records.append({
            "id": uuid.uuid4().hex[:8],
            "type": types[i % len(types)],
            "category": categories[i % len(categories)],
            "content": f"记忆条目 {i + 1}",
            "timestamp": time.time() - i * 60 - (i % 5) * 30,
            "duration_ms": 15 + (i % 3) * 10
        })
    
    logger.info(log_dict({'module_name': 'dashboard', 'action': '_get_recent_memory_access', 'status': 'completed', 'record_count': len(records)}))
    
    return records


def _get_dashboard_health():
    """获取仪表盘健康状态"""
    return {
        "status": "healthy",
        "services": {
            "tracing": True,
            "metrics": True,
            "memory": True,
            "failure_analysis": True
        },
        "timestamp": time.time()
    }


def register_routes(app, state):
    """注册仪表盘路由"""
    
    @app.route("/api/dashboard/health", methods=["GET"])
    @trace_route("Dashboard")
    @log_request(show_response=False)
    def api_dashboard_health():
        """
        仪表盘健康检查
        
        Response:
            {
                "status": "healthy",
                "services": {
                    "tracing": bool,
                    "metrics": bool,
                    "memory": bool,
                    "failure_analysis": bool
                },
                "timestamp": float
            }
        """
        result = _get_dashboard_health()
        return jsonify(result)
    
    @app.route("/api/dashboard/quality", methods=["GET"])
    @trace_route("Dashboard")
    @log_request(show_response=False)
    def api_dashboard_quality():
        """
        质量监控仪表盘
        
        Query Parameters:
            time_range: str, 时间范围 (today/week/month/custom)
            start_time: float, 开始时间戳 (time_range=custom时必填)
            end_time: float, 结束时间戳 (time_range=custom时必填)
        
        Response:
            {
                "schema_validation": {
                    "total_validations": int,
                    "successful_validations": int,
                    "failed_validations": int,
                    "pass_rate": float,
                    "trend": [...]
                },
                "critic_scores": {
                    "total_evaluations": int,
                    "average_score": float,
                    "score_distribution": {...},
                    "trend": [...]
                },
                "failure_distribution": {
                    "total_failures": int,
                    "distribution": {...},
                    "top_errors": [...]
                },
                "time_range": {
                    "start_time": float,
                    "end_time": float
                },
                "generated_at": float
            }
        """
        time_range = request.args.get("time_range", "today")
        start_time = request.args.get("start_time", type=float)
        end_time = request.args.get("end_time", type=float)

        # ── 链路追踪：入口日志（含完整参数值 + 初始 trace_id） ──
        # @trace_route 已创建 TraceContext，此处读取的 trace_id 为本次请求的唯一标识
        # 记录 trace_id_entry 作为基准，后续节点对比 trace_id_changed 以排查链路断裂
        _tid_entry = get_trace_id()
        _q_start = time.time()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "quality.entry", '
            '"duration_ms": 0, "phase": "entry", "params": {"time_range": "%s", "start_time": %s, "end_time": %s}, '
            '"trace_id_phase": "entry"}',
            _tid_entry, time_range,
            str(start_time) if start_time else "null",
            str(end_time) if end_time else "null"
        )

        if time_range == "custom":
            if not start_time or not end_time:
                return jsonify({"error": "自定义时间范围需要提供 start_time 和 end_time"}), 400
        else:
            start_time, end_time = _parse_time_range(time_range)
        
        result = _get_quality_metrics(start_time, end_time)
        # ── 链路追踪：出口日志（记录 trace_id 变化 + 结果摘要） ──
        _tid_exit = get_trace_id()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "quality.exit", '
            '"duration_ms": %.2f, "phase": "exit", "schema_pass_rate": %s, "critic_avg": %s, "total_failures": %s, '
            '"trace_id_entry": "%s", "trace_id_changed": %s}',
            _tid_exit, (time.time() - _q_start) * 1000,
            str(result.get("schema_validation", {}).get("pass_rate", "null")),
            str(result.get("critic_scores", {}).get("average_score", "null")),
            str(result.get("failure_distribution", {}).get("total_failures", "null")),
            _tid_entry, str(_tid_exit != _tid_entry)
        )
        return jsonify(result)
    
    @app.route("/api/dashboard/traces", methods=["GET"])
    @require_token
    @trace_route("Dashboard")
    @log_request(show_response=False)
    def api_dashboard_traces():
        """
        追踪数据列表
        
        Query Parameters:
            limit: int, 默认20
            trace_id: str, 可选，按追踪ID过滤
        
        Response:
            {
                "traces": [...],
                "total": int,
                "limit": int,
                "timestamp": float
            }
        """
        limit = request.args.get("limit", 20, type=int)
        trace_id_filter = request.args.get("trace_id", "")

        # ── 链路追踪：入口日志（含完整参数值 + 初始 trace_id） ──
        # 记录 trace_id_entry 作为基准，出口处对比 trace_id_changed 以排查链路断裂
        _tid_entry = get_trace_id()
        _t_start = time.time()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "traces.entry", '
            '"duration_ms": 0, "phase": "entry", "params": {"limit": %d, "trace_id_filter": "%s", "filter_len": %d}, '
            '"trace_id_phase": "entry"}',
            _tid_entry, limit, trace_id_filter, len(trace_id_filter)
        )

        traces = _get_trace_list(limit, trace_id_filter)

        # ── 链路追踪：出口日志（记录 trace_id 变化 + 结果摘要） ──
        _tid_exit = get_trace_id()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "traces.exit", '
            '"duration_ms": %.2f, "phase": "exit", "traces_count": %d, "limit": %d, "has_filter": %s, '
            '"trace_id_entry": "%s", "trace_id_changed": %s}',
            _tid_exit, (time.time() - _t_start) * 1000, len(traces), limit,
            str(bool(trace_id_filter)), _tid_entry, str(_tid_exit != _tid_entry)
        )

        return jsonify({
            "traces": traces,
            "total": len(traces),
            "limit": limit,
            "timestamp": time.time()
        })
    
    @app.route("/api/dashboard/traces/<trace_id>", methods=["GET"])
    @require_token
    @trace_route("Dashboard")
    @log_request(show_response=False)
    def api_dashboard_trace_detail(trace_id):
        """
        追踪详情
        
        Response:
            {
                "trace_id": str,
                "spans": [...],
                "duration_ms": int,
                "timestamp": float
            }
        """
        detail = _get_trace_detail(trace_id)
        
        if detail:
            return jsonify(detail)
        return jsonify({"error": "追踪不存在"}), 404
    
    @app.route("/api/dashboard/memory", methods=["GET"])
    @trace_route("Dashboard")
    @log_request(show_response=False)
    def api_dashboard_memory():
        """
        Memory 使用仪表盘
        
        Query Parameters:
            search: str, 可选，搜索关键词
            limit: int, 默认10，最近访问记录条数
        
        Response:
            {
                "long_term": {
                    "total_count": int,
                    "total_size_mb": float,
                    "average_size_kb": float,
                    "growth_trend": [...],
                    "last_updated": float
                },
                "short_term": {
                    "total_count": int,
                    "active_sessions": int,
                    "average_age_minutes": int,
                    "growth_trend": [...],
                    "last_updated": float
                },
                "hit_rate": {
                    "overall_hit_rate": float,
                    "long_term_hit_rate": float,
                    "short_term_hit_rate": float,
                    "total_requests": int,
                    "cache_hits": int,
                    "trend": [...]
                },
                "category_distribution": [...],
                "recent_access": [...],
                "generated_at": float
            }
        """
        search_query = request.args.get("search", "")
        limit = request.args.get("limit", 10, type=int)
        
        result = _get_memory_stats()
        
        # 搜索过滤
        if search_query:
            result["recent_access"] = [
                r for r in result["recent_access"]
                if search_query.lower() in r["content"].lower() or
                   search_query.lower() in r["category"].lower()
            ][:limit]
        
        return jsonify(result)
    
    logger.info(log_dict({'module_name': 'routes_dashboard', 'action': 'log', 'msg': '[Routes] 仪表盘端点已注册'}))