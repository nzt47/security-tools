#!/usr/bin/env python3
"""
业务仪表盘 API 路由 - 业务价值监控端点

提供用户交互、任务完成率、知识库命中率、扩展使用统计等业务指标数据接口。

API 端点清单：
- GET  /api/business/dashboard          - 业务仪表盘总览数据
- GET  /api/business/metrics/<name>     - 单个指标详情
- GET  /api/business/prometheus         - Prometheus 格式导出
- GET  /api/business/health             - 业务指标健康检查
- GET  /api/business/definitions        - 指标定义列表
"""

import logging
import json
import time
from datetime import datetime, timedelta
from flask import request, jsonify

from agent.server_auth import require_token, log_request
from agent.monitoring.tracing import get_trace_id, TraceContext
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)


def _parse_time_range(time_range):
    """解析时间范围参数
    
    Args:
        time_range: 时间范围字符串（today/week/month/custom）
    
    Returns:
        (start_time, end_time): 时间戳元组
    """
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
    elif time_range == "hour":
        start_time = now - timedelta(hours=1)
        end_time = now
    else:
        # 默认最近24小时
        start_time = now - timedelta(hours=24)
        end_time = now
    
    return start_time.timestamp(), end_time.timestamp()


def _get_business_dashboard(time_range_seconds=None):
    """获取业务仪表盘数据
    
    Args:
        time_range_seconds: 时间范围（秒），None 表示全部数据
    
    Returns:
        业务仪表盘数据字典
    """
    trace_id = get_trace_id()
    start_ms = time.time()
    
    try:
        # 导入业务指标收集器
        from agent.monitoring.business_metrics import get_business_metrics_collector
        
        collector = get_business_metrics_collector()
        dashboard_data = collector.get_dashboard_data(time_range_seconds)
        
        duration_ms = (time.time() - start_ms) * 1000
        
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "business_dashboard",
            "action": "get_dashboard",
            "duration_ms": round(duration_ms, 2),
            "status": "success",
            "time_range_seconds": time_range_seconds
        }))
        
        return dashboard_data
        
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": trace_id,
            "module_name": "business_dashboard",
            "action": "get_dashboard",
            "duration_ms": round((time.time() - start_ms) * 1000, 2),
            "error": str(e)
        }))
        raise


def _get_metric_detail(metric_name):
    """获取单个指标详情
    
    Args:
        metric_name: 指标名称
    
    Returns:
        指标详情字典
    """
    trace_id = get_trace_id()
    start_ms = time.time()
    
    try:
        from agent.monitoring.business_metrics import get_business_metrics_collector
        
        collector = get_business_metrics_collector()
        metric_detail = collector.get_metric_by_name(metric_name)
        
        duration_ms = (time.time() - start_ms) * 1000
        
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "business_dashboard",
            "action": "get_metric_detail",
            "duration_ms": round(duration_ms, 2),
            "metric_name": metric_name,
            "status": "success" if metric_detail else "not_found"
        }))
        
        return metric_detail
        
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": trace_id,
            "module_name": "business_dashboard",
            "action": "get_metric_detail",
            "duration_ms": round((time.time() - start_ms) * 1000, 2),
            "metric_name": metric_name,
            "error": str(e)
        }))
        raise


def _export_prometheus():
    """导出 Prometheus 格式的业务指标
    
    Returns:
        Prometheus 格式的文本
    """
    trace_id = get_trace_id()
    start_ms = time.time()
    
    try:
        from agent.monitoring.business_metrics import get_business_metrics_collector
        
        collector = get_business_metrics_collector()
        prometheus_text = collector.export_prometheus()
        
        duration_ms = (time.time() - start_ms) * 1000
        
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "business_dashboard",
            "action": "export_prometheus",
            "duration_ms": round(duration_ms, 2),
            "status": "success"
        }))
        
        return prometheus_text
        
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": trace_id,
            "module_name": "business_dashboard",
            "action": "export_prometheus",
            "duration_ms": round((time.time() - start_ms) * 1000, 2),
            "error": str(e)
        }))
        raise


def _get_business_health():
    """获取业务指标健康状态
    
    Returns:
        健康状态字典
    """
    try:
        from agent.monitoring.business_metrics import get_business_metrics_collector
        
        collector = get_business_metrics_collector()
        dashboard = collector.get_dashboard_data()
        
        # 计算健康状态
        summary = dashboard.get("summary", {})
        
        # 交互活跃度（最近是否有交互）
        interaction_active = summary.get("total_interactions", 0) > 0
        
        # 任务成功率（是否高于阈值）
        task_success_rate = summary.get("task_success_rate", 0)
        task_health = task_success_rate >= 70
        
        # 记忆命中率（是否高于阈值）
        memory_hit_rate = summary.get("memory_hit_rate", 0)
        memory_health = memory_hit_rate >= 50
        
        # 扩展活跃度（是否有活跃扩展）
        extension_active = summary.get("active_extensions", 0) > 0
        
        # 计算整体健康评分
        health_score = 0
        if interaction_active:
            health_score += 25
        if task_health:
            health_score += 25
        if memory_health:
            health_score += 25
        if extension_active:
            health_score += 25
        
        status = "healthy" if health_score >= 75 else "warning" if health_score >= 50 else "critical"
        
        return {
            "status": status,
            "health_score": health_score,
            "checks": {
                "interaction_active": interaction_active,
                "task_success_rate": task_success_rate,
                "task_health": task_health,
                "memory_hit_rate": memory_hit_rate,
                "memory_health": memory_health,
                "extension_active": extension_active,
            },
            "timestamp": time.time()
        }
        
    except Exception as e:
        logger.error(json.dumps({"trace_id": get_trace_id(), "module_name": "routes_business_dashboard", "action": "log", "msg": f"获取业务指标健康状态失败: {e}"}, ensure_ascii=False))
        return {
            "status": "error",
            "error": str(e),
            "timestamp": time.time()
        }


def _get_metric_definitions():
    """获取所有业务指标定义
    
    Returns:
        指标定义列表
    """
    try:
        from agent.monitoring.business_metrics import BUSINESS_METRICS_DEFINITIONS
        
        definitions = []
        for name, definition in BUSINESS_METRICS_DEFINITIONS.items():
            definitions.append({
                "name": definition.name,
                "description": definition.description,
                "metric_type": definition.metric_type,
                "labels": definition.labels,
                "unit": definition.unit,
                "category": definition.category,
                "business_value": definition.business_value,
                "aggregation": definition.aggregation,
                "retention_days": definition.retention_days,
            })
        
        return {
            "definitions": definitions,
            "total": len(definitions),
            "categories": {
                "interaction": len([d for d in definitions if d["category"] == "interaction"]),
                "task": len([d for d in definitions if d["category"] == "task"]),
                "knowledge": len([d for d in definitions if d["category"] == "knowledge"]),
                "extension": len([d for d in definitions if d["category"] == "extension"]),
            },
            "timestamp": time.time()
        }
        
    except Exception as e:
        logger.error(json.dumps({"trace_id": get_trace_id(), "module_name": "routes_business_dashboard", "action": "log", "msg": f"获取指标定义失败: {e}"}, ensure_ascii=False))
        return {
            "definitions": [],
            "total": 0,
            "error": str(e),
            "timestamp": time.time()
        }


def register_routes(app, state):
    """注册业务仪表盘路由"""
    
    @app.route("/api/business/health", methods=["GET"])
    @log_request(show_response=False)
    def api_business_health():
        """
        业务指标健康检查
        
        Response:
            {
                "status": "healthy"|"warning"|"critical"|"error",
                "health_score": int (0-100),
                "checks": {
                    "interaction_active": bool,
                    "task_success_rate": float,
                    "task_health": bool,
                    "memory_hit_rate": float,
                    "memory_health": bool,
                    "extension_active": bool
                },
                "timestamp": float
            }
        """
        result = _get_business_health()
        return jsonify(result)
    
    @app.route("/api/business/dashboard", methods=["GET"])
    @trace_route("BusinessDashboard")
    @log_request(show_response=False)
    def api_business_dashboard():
        """
        业务仪表盘总览
        
        Query Parameters:
            time_range: str, 时间范围 (hour/today/week/month/custom)
            time_range_seconds: float, 时间范围秒数（可选）
        
        Response:
            {
                "generated_at": str,
                "time_range_seconds": float,
                "interaction": {...},
                "task": {...},
                "knowledge": {...},
                "extension": {...},
                "summary": {
                    "total_interactions": int,
                    "total_tool_calls": int,
                    "task_success_rate": float,
                    "memory_hit_rate": float,
                    "active_extensions": int
                }
            }
        """
        time_range = request.args.get("time_range", "today")
        time_range_seconds = request.args.get("time_range_seconds", type=float)
        
        # 如果指定了 time_range_seconds，直接使用
        if time_range_seconds is not None:
            result = _get_business_dashboard(time_range_seconds)
        else:
            # 否则根据 time_range 参数计算
            start_time, end_time = _parse_time_range(time_range)
            time_range_seconds = end_time - start_time
            result = _get_business_dashboard(time_range_seconds)
        
        return jsonify(result)
    
    @app.route("/api/business/metrics/<metric_name>", methods=["GET"])
    @trace_route("BusinessDashboard")
    @log_request(show_response=False)
    def api_business_metric_detail(metric_name):
        """
        单个指标详情
        
        Args:
            metric_name: 指标名称
        
        Response:
            {
                "definition": {
                    "name": str,
                    "description": str,
                    "metric_type": str,
                    "labels": list,
                    "unit": str,
                    "category": str,
                    "business_value": str
                },
                "data": {...}
            }
        """
        result = _get_metric_detail(metric_name)
        
        if result:
            return jsonify(result)
        return jsonify({"error": f"指标 '{metric_name}' 不存在"}), 404
    
    @app.route("/api/business/prometheus", methods=["GET"])
    @log_request(show_response=False)
    def api_business_prometheus():
        """
        Prometheus 格式导出
        
        Response:
            Prometheus 格式的文本（Content-Type: text/plain）
        """
        prometheus_text = _export_prometheus()
        return prometheus_text, 200, {"Content-Type": "text/plain; charset=utf-8"}
    
    @app.route("/api/business/definitions", methods=["GET"])
    @trace_route("BusinessDashboard")
    @log_request(show_response=False)
    def api_business_definitions():
        """
        指标定义列表
        
        Response:
            {
                "definitions": [...],
                "total": int,
                "categories": {
                    "interaction": int,
                    "task": int,
                    "knowledge": int,
                    "extension": int
                },
                "timestamp": float
            }
        """
        result = _get_metric_definitions()
        return jsonify(result)
    
    logger.info(json.dumps({"trace_id": get_trace_id(), "module_name": "routes_business_dashboard", "action": "log", "msg": "[Routes] 业务仪表盘端点已注册"}, ensure_ascii=False))