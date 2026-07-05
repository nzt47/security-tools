#!/usr/bin/env python3
"""
运行时诊断端点 - 可观测性 API 路由

提供运行时诊断功能，支持"存在即可见"原则：
1. 工具注册清单查询
2. 配置状态检查
3. 连接健康诊断
4. 实时日志流
5. 追踪上下文查询
6. Prometheus 指标导出
7. 告警规则配置

API 端点清单：
- GET  /metrics                         - Prometheus 格式指标导出
- GET  /api/diagnostics/tools           - 获取已注册工具清单
- GET  /api/diagnostics/config          - 获取配置状态摘要
- GET  /api/diagnostics/health          - 综合健康检查
- GET  /api/diagnostics/trace           - 获取当前追踪上下文
- GET  /api/diagnostics/metrics         - 获取运行时指标(JSON)
- GET  /api/diagnostics/logs            - 获取最近日志
- GET  /api/observability/state         - 获取可观测性状态
- GET  /api/observability/trace/extract - 从请求头提取追踪上下文
- GET  /api/observability/alerts        - 获取告警规则列表
- POST /api/observability/alerts        - 创建告警规则
- PUT  /api/observability/alerts/<id>   - 更新告警规则
- DELETE /api/observability/alerts/<id> - 删除告警规则
"""

import logging
import json
import time
import os
from datetime import datetime
from flask import request, jsonify, Response, stream_with_context

# 导入核心模块
from agent.server_auth import require_token, log_request
from agent.monitoring.tracing import (
    get_trace_id, 
    get_span_id, 
    extract_trace_context, 
    inject_trace_context,
    is_opentelemetry_available,
    TraceContext
)
from agent.monitoring.metrics import get_metrics_collector
from agent.monitoring.performance import get_performance_recorder
from agent.monitoring.prometheus import (
    PrometheusMetricsExporter,
    record_error, record_encoding_fallback,
    record_read_duration, set_loaded_history_count, set_invalid_ratio
)
from agent.health.assessor import health_assessor
from agent.tools import list_tools
from agent.server_routes.tracing_decorator import trace_route
from agent.monitoring.sensitive_data_filter import (
    filter_sensitive_data, 
    filter_dict,
    get_access_logger
)
from agent.logging_utils import log_dict

try:
    from config import Config
except ImportError:
    class Config:
        @staticmethod
        def get(*args, **kwargs):
            return kwargs.get('default', None)

logger = logging.getLogger(__name__)

# Prometheus 全局导出器
_prometheus_exporter = None

def get_prometheus_exporter():
    """获取 Prometheus 指标导出器"""
    global _prometheus_exporter
    if _prometheus_exporter is None:
        try:
            _prometheus_exporter = PrometheusMetricsExporter(port=0)  # 使用共享端口
            logger.info(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': '[Prometheus] 指标导出器已初始化'}))
        except Exception as e:
            logger.warning(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'[Prometheus] 初始化失败: {e}'}))
    return _prometheus_exporter

# 告警规则存储
_ALERT_RULES_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'monitoring', 'alerts.yml')
_ALERT_RULES_CACHE = None

def _load_alert_rules():
    """加载告警规则"""
    global _ALERT_RULES_CACHE
    if _ALERT_RULES_CACHE is None:
        try:
            import yaml
            with open(_ALERT_RULES_FILE, 'r', encoding='utf-8') as f:
                _ALERT_RULES_CACHE = yaml.safe_load(f)
        except Exception as e:
            logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'加载告警规则失败: {e}'}))
            _ALERT_RULES_CACHE = {"groups": []}
    return _ALERT_RULES_CACHE

def _save_alert_rules(rules):
    """保存告警规则"""
    global _ALERT_RULES_CACHE
    try:
        import yaml
        with open(_ALERT_RULES_FILE, 'w', encoding='utf-8') as f:
            yaml.dump(rules, f, default_flow_style=False, allow_unicode=True)
        _ALERT_RULES_CACHE = rules
        return True
    except Exception as e:
        logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'保存告警规则失败: {e}'}))
        return False


def _get_tool_summary():
    """获取工具注册摘要
    
    返回所有已注册工具的基本信息，包含名称、描述和类别。
    
    Returns:
        dict: 工具摘要字典，包含工具列表和统计信息
    """
    try:
        tools = list_tools()
        summary = []
        
        for tool in tools:
            tool_info = {
                "name": tool.get("name", "unknown"),
                "description": tool.get("description", ""),
                "category": tool.get("category", "general"),
                "version": tool.get("version", "1.0.0"),
                "enabled": tool.get("enabled", True)
            }
            summary.append(tool_info)
        
        # 按类别分组统计
        category_counts = {}
        for tool in summary:
            cat = tool["category"]
            category_counts[cat] = category_counts.get(cat, 0) + 1
        
        return {
            "total_tools": len(summary),
            "categories": category_counts,
            "tools": summary,
            "timestamp": time.time()
        }
    except Exception as e:
        logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'获取工具摘要失败: {e}'}))
        return {"error": str(e), "timestamp": time.time()}


def _get_config_status():
    """获取配置状态摘要
    
    返回当前配置的关键信息，对敏感数据进行过滤。
    
    Returns:
        dict: 配置状态字典
    """
    try:
        config = Config.get()
        
        # 获取配置的非敏感部分
        config_status = {
            "version": config.get("version", "unknown"),
            "environment": config.get("environment", "development"),
            "debug_mode": config.get("debug", False),
            "modules": {
                "memory": config.get("memory", {}).get("enabled", False),
                "monitoring": config.get("monitoring", {}).get("enabled", False),
                "security": config.get("security", {}).get("enabled", False),
                "extensions": config.get("extensions", {}).get("enabled", False),
            },
            "performance": {
                "max_workers": config.get("performance", {}).get("max_workers", 4),
                "pool_size": config.get("performance", {}).get("pool_size", 10),
                "max_concurrency": config.get("performance", {}).get("max_concurrency", 5),
            },
            "validation_errors": config.get("_validation_errors", []),
            "loaded_at": config.get("_loaded_at", ""),
            "timestamp": time.time()
        }
        
        # 对配置进行敏感数据过滤
        return filter_dict(config_status)
    except Exception as e:
        logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'获取配置状态失败: {e}'}))
        return {"error": str(e), "timestamp": time.time()}


def _get_health_status():
    """获取综合健康状态

    整合多个健康检查源的结果，提供统一的健康视图。

    Returns:
        dict: 健康状态字典
    """
    try:
        health = health_assessor.assess()
        history = health_assessor.get_history(10)

        # 异常关联统计：trace_id ↔ user_session_id ↔ error_id 三向关联
        # 失败不得影响健康检查主流程，仅记录空结果
        error_correlation = _get_error_correlation_stats(hours=24)

        return {
            "overall_health": health.overall,
            "dimensions": health.dimensions,
            "issues": health.issues,
            "history": [
                {"timestamp": h.timestamp, "overall": h.overall, "issues": len(h.issues)}
                for h in history
            ],
            "opentelemetry_available": is_opentelemetry_available(),
            # 最近 24 小时错误关联统计（异常关联分析）
            "error_correlation": error_correlation,
            "timestamp": time.time()
        }
    except Exception as e:
        logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'获取健康状态失败: {e}'}))
        return {"error": str(e), "timestamp": time.time()}


def _get_error_correlation_stats(hours: int = 24) -> dict:
    """获取异常关联统计

    整合三方关联数据：
    - trace_id → 链路追踪（OpenTelemetry）
    - user_session_id → 用户行为回放
    - error_id → Sentry 事件

    失败不得影响健康检查主流程，仅返回空统计。

    Args:
        hours: 统计时间窗口（小时），默认 24

    Returns:
        统计字典，包含回放关联数与 Sentry 状态
    """
    result = {
        "window_hours": hours,
        "replay_stats": None,
        "sentry_enabled": False,
        "sentry_events_count": None,
    }
    # 1. 回放关联统计（来自 ReplayStorage）
    try:
        from agent.monitoring.replay_storage import get_replay_storage
        storage = get_replay_storage()
        result["replay_stats"] = storage.get_correlation_stats(hours=hours)
    except Exception as e:
        logger.debug(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'[HealthStatus] 回放关联统计获取失败（已降级）: {e}'}))
        result["replay_stats"] = {
            "total_replays": 0,
            "with_trace_id": 0,
            "with_user_session_id": 0,
            "with_error_id": 0,
            "fully_correlated": 0,
            "by_error_id": [],
            "window_hours": hours,
            "error": str(e),
        }

    # 2. Sentry 状态（仅探测是否启用，不查询 Sentry API 避免外部依赖）
    try:
        from agent.error_reporting_config import is_sentry_enabled
        result["sentry_enabled"] = is_sentry_enabled()
    except Exception:
        result["sentry_enabled"] = False

    return result


def _get_runtime_metrics():
    """获取运行时指标
    
    返回性能指标、计数器和延迟统计。
    
    Returns:
        dict: 运行时指标字典
    """
    try:
        collector = get_metrics_collector()
        metrics = collector.get_all_metrics()
        
        return {
            "histograms": metrics.get("histograms", {}),
            "counters": metrics.get("counters", {}),
            "generated_at": metrics.get("generated_at", time.time()),
            "timestamp": time.time()
        }
    except Exception as e:
        logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'获取运行时指标失败: {e}'}))
        return {"error": str(e), "timestamp": time.time()}


def _get_recent_logs(limit=50):
    """获取最近日志记录

    Args:
        limit: 返回日志条数，默认50条

    Returns:
        dict: 包含日志列表的字典
    """
    try:
        # 尝试从日志存储系统获取
        try:
            from agent.log_system.storage import get_storage
            storage = get_storage()
            if storage and storage._initialized:
                # 从性能日志和错误日志中获取最近记录
                perf_logs = storage.query_performance(limit=limit)
                error_logs = storage.query_errors(limit=limit)
                
                # 合并并按时间排序
                all_logs = []
                for log in perf_logs:
                    log['_type'] = 'performance'
                    all_logs.append(log)
                for log in error_logs:
                    log['_type'] = 'error'
                    all_logs.append(log)
                
                # 按时间戳降序排序，取最新的
                all_logs.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
                logs = all_logs[:limit]
                
                # 敏感数据过滤
                filtered_logs = []
                for log in logs:
                    filtered_log = filter_dict(log) if isinstance(log, dict) else log
                    filtered_logs.append(filtered_log)
                
                return {
                    "logs": filtered_logs,
                    "limit": limit,
                    "total_available": len(filtered_logs),
                    "source": "log_system",
                    "timestamp": time.time()
                }
        except (ImportError, Exception) as e:
            logger.debug(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'日志存储系统不可用: {e}'}))
        
        # 降级方案：从性能记录器获取模块初始化记录
        recorder = get_performance_recorder()
        records = list(recorder.records.values()) if hasattr(recorder, 'records') else []
        records.sort(key=lambda x: x.end_time if hasattr(x, 'end_time') else 0, reverse=True)
        logs = []
        for r in records[:limit]:
            log_entry = {
                "module": r.name if hasattr(r, 'name') else str(r),
                "duration_ms": r.duration_ms if hasattr(r, 'duration_ms') else 0,
                "success": r.success if hasattr(r, 'success') else True,
                "error": r.error if hasattr(r, 'error') else "",
                "timestamp": r.end_time if hasattr(r, 'end_time') else time.time()
            }
            logs.append(log_entry)
        
        return {
            "logs": logs,
            "limit": limit,
            "total_available": len(logs),
            "source": "init_performance",
            "timestamp": time.time()
        }
    except Exception as e:
        logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'获取最近日志失败: {e}'}))
        return {"error": str(e), "timestamp": time.time()}


def register_routes(app, state):
    """注册运行时诊断路由
    
    Args:
        app: Flask应用实例
        state: 应用状态对象
    """
    
    # ═══════════════════════════════════════════════════
    #  工具诊断端点
    # ═══════════════════════════════════════════════════
    
    @app.route("/api/diagnostics/tools", methods=["GET"])
    @trace_route("Diagnostics")
    @log_request(show_response=False)
    def api_diagnostics_tools():
        """
        获取已注册工具清单
        
        返回所有已注册工具的摘要信息，包括名称、描述、类别和版本。
        用于诊断"工具注册后不可见"问题。
        
        Response:
            {
                "total_tools": int,
                "categories": {category: count},
                "tools": [
                    {
                        "name": str,
                        "description": str,
                        "category": str,
                        "version": str,
                        "enabled": bool
                    }
                ],
                "timestamp": float
            }
        """
        result = _get_tool_summary()
        return jsonify(result)
    
    # ═══════════════════════════════════════════════════
    #  配置诊断端点
    # ═══════════════════════════════════════════════════
    
    @app.route("/api/diagnostics/config", methods=["GET"])
    @require_token
    @trace_route("Diagnostics")
    @log_request(show_response=False)
    def api_diagnostics_config():
        """
        获取配置状态摘要（需要认证）

        返回当前配置的关键信息，对敏感数据进行过滤。
        用于诊断配置相关问题。

        Response:
            {
                "version": str,
                "environment": str,
                "debug_mode": bool,
                "modules": {module: bool},
                "performance": {...},
                "validation_errors": list,
                "loaded_at": str,
                "timestamp": float
            }
        """
        result = _get_config_status()
        return jsonify(result)
    
    # ═══════════════════════════════════════════════════
    #  健康检查端点
    # ═══════════════════════════════════════════════════
    
    @app.route("/api/diagnostics/health", methods=["GET"])
    @trace_route("Diagnostics")
    @log_request(show_response=False)
    def api_diagnostics_health():
        """
        综合健康检查

        整合多个健康检查源的结果，提供统一的健康视图。
        包含最近 24 小时错误关联统计（trace_id ↔ user_session_id ↔ error_id）。

        Response:
            {
                "overall_health": float (0.0-1.0),
                "dimensions": {dimension: score},
                "issues": [str],
                "history": [{timestamp, overall, issues}],
                "opentelemetry_available": bool,
                "error_correlation": {
                    "window_hours": int,
                    "replay_stats": {...},
                    "sentry_enabled": bool
                },
                "timestamp": float
            }
        """
        result = _get_health_status()
        return jsonify(result)

    @app.route("/api/diagnostics/error_correlation", methods=["GET"])
    @trace_route("Diagnostics")
    @log_request(show_response=False)
    def api_diagnostics_error_correlation():
        """获取异常关联统计

        提供最近 N 小时的错误关联数据，用于跨系统排查：
        - trace_id → 链路追踪（OpenTelemetry）
        - user_session_id → 用户行为回放
        - error_id → Sentry 事件

        Query:
            hours: int  统计时间窗口（小时），默认 24
        """
        hours = request.args.get("hours", default=24, type=int)
        hours = max(1, min(hours, 24 * 30))
        result = _get_error_correlation_stats(hours=hours)
        return jsonify({"ok": True, "correlation": result, "timestamp": time.time()})
    
    # ═══════════════════════════════════════════════════
    #  追踪上下文端点
    # ═══════════════════════════════════════════════════
    
    @app.route("/api/diagnostics/trace", methods=["GET"])
    @trace_route("Diagnostics")
    @log_request(show_response=False)
    def api_diagnostics_trace():
        """
        获取当前追踪上下文
        
        返回当前请求的 Trace ID、Span ID 以及 OpenTelemetry 状态。
        
        Response:
            {
                "trace_id": str or null,
                "span_id": str or null,
                "opentelemetry_available": bool,
                "timestamp": float
            }
        """
        result = {
            "trace_id": get_trace_id(),
            "span_id": get_span_id(),
            "opentelemetry_available": is_opentelemetry_available(),
            "timestamp": time.time()
        }
        return jsonify(result)
    
    @app.route("/api/diagnostics/trace/extract", methods=["POST"])
    @trace_route("Diagnostics")
    @log_request(show_response=False)
    def api_diagnostics_trace_extract():
        """
        从请求头提取追踪上下文
        
        解析 W3C Trace Context (traceparent) 或 Jaeger 格式 (uber-trace-id)。
        优先从 HTTP 请求头提取，也支持从请求体中传入 headers。
        
        Request Body (可选):
            {
                "headers": {
                    "traceparent": str,
                    "uber-trace-id": str
                }
            }
        
        Response:
            {
                "trace_id": str or null,
                "span_id": str or null,
                "format": str ("w3c" or "jaeger" or "unknown"),
                "timestamp": float
            }
        """
        # 优先从实际 HTTP 请求头提取
        headers = dict(request.headers)
        # 如果请求体中提供了 headers，则合并覆盖
        data = request.get_json() or {}
        body_headers = data.get("headers", {})
        if body_headers:
            headers.update(body_headers)
        
        context = extract_trace_context(headers)
        
        # 检测格式（大小写不敏感）
        format_type = "unknown"
        headers_lower = {k.lower(): v for k, v in headers.items()}
        if headers_lower.get("traceparent"):
            format_type = "w3c"
        elif headers_lower.get("uber-trace-id"):
            format_type = "jaeger"
        
        result = {
            "trace_id": context.get("trace_id"),
            "span_id": context.get("span_id"),
            "format": format_type,
            "timestamp": time.time()
        }
        return jsonify(result)
    
    @app.route("/api/diagnostics/trace/inject", methods=["GET"])
    @trace_route("Diagnostics")
    @log_request(show_response=False)
    def api_diagnostics_trace_inject():
        """
        生成追踪上下文请求头
        
        生成符合 W3C Trace Context 规范的请求头，用于跨服务调用。
        
        Response:
            {
                "headers": {
                    "traceparent": str,
                    "tracestate": str
                },
                "trace_id": str,
                "span_id": str,
                "timestamp": float
            }
        """
        headers = inject_trace_context()
        result = {
            "headers": headers,
            "trace_id": get_trace_id(),
            "span_id": get_span_id(),
            "timestamp": time.time()
        }
        return jsonify(result)
    
    # ═══════════════════════════════════════════════════
    #  指标端点
    # ═══════════════════════════════════════════════════
    
    @app.route("/api/diagnostics/metrics", methods=["GET"])
    @require_token
    @log_request(show_response=False)
    def api_diagnostics_metrics():
        """
        获取运行时指标（需要认证）

        返回性能指标、计数器和延迟统计。

        Response:
            {
                "histograms": {metric: {count, sum, avg, min, max, p50, p95, p99}},
                "counters": {counter: value},
                "generated_at": float,
                "timestamp": float
            }
        """
        result = _get_runtime_metrics()
        return jsonify(result)
    
    # ═══════════════════════════════════════════════════
    #  日志端点
    # ═══════════════════════════════════════════════════
    
    @app.route("/api/diagnostics/logs", methods=["GET"])
    @require_token
    @log_request(show_response=False)
    def api_diagnostics_logs():
        """
        获取最近日志记录（需要认证）
        
        Query Parameters:
            limit: int, 默认50，返回日志条数
        
        Response:
            {
                "logs": [...],
                "limit": int,
                "total_available": int,
                "timestamp": float
            }
        """
        limit = request.args.get("limit", 50, type=int)
        result = _get_recent_logs(limit)
        return jsonify(result)
    
    # ═══════════════════════════════════════════════════
    #  Loki 日志查询端点
    # ═══════════════════════════════════════════════════
    
    @app.route("/api/observability/logs", methods=["GET"])
    @require_token
    @log_request(show_response=False)
    def api_observability_logs():
        """
        查询日志（需要认证）
        
        支持从 Loki 或本地存储查询日志，支持过滤和时间范围查询。
        
        Query Parameters:
            query: str, 搜索关键词或 LogQL 查询
            start_time: float, 开始时间戳（可选）
            end_time: float, 结束时间戳（可选）
            limit: int, 默认100，返回条数限制
            level: str, 日志级别过滤（可选）
            service: str, 服务名过滤（可选）
        
        Response:
            {
                "logs": [
                    {
                        "timestamp": float,
                        "labels": dict,
                        "message": str,
                        "source": "loki" or "local"
                    }
                ],
                "total": int,
                "limit": int,
                "timestamp": float
            }
        """
        try:
            from agent.monitoring.loki import query_loki_logs
            
            query = request.args.get("query", "")
            start_time = request.args.get("start_time", type=float)
            end_time = request.args.get("end_time", type=float)
            limit = request.args.get("limit", 100, type=int)
            level = request.args.get("level", "")
            service = request.args.get("service", "")
            
            # 构建查询条件
            log_query = query
            
            # 如果有级别过滤
            if level:
                if log_query:
                    log_query += f" |~ '{level}'"
                else:
                    log_query = f"level={level}"
            
            # 如果有服务过滤
            if service:
                if log_query:
                    log_query += f" |~ '{service}'"
                else:
                    log_query = f"service={service}"
            
            logs = query_loki_logs(
                query=log_query if log_query else ".+",
                start_time=start_time,
                end_time=end_time,
                limit=limit
            )

            # 对日志进行敏感数据过滤
            filtered_logs = []
            for log in logs:
                if isinstance(log, dict):
                    # 对日志消息进行敏感数据过滤
                    if 'message' in log:
                        log['message'] = filter_sensitive_data(log['message'])
                    # 过滤标签中的敏感数据
                    if 'labels' in log:
                        log['labels'] = filter_dict(log['labels'])
                filtered_logs.append(log)

            return jsonify({
                "logs": filtered_logs,
                "total": len(filtered_logs),
                "limit": limit,
                "timestamp": time.time()
            })
        except Exception as e:
            logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'查询日志失败: {e}'}))
            return jsonify({"ok": False, "error": str(e)}), 500
    
    @app.route("/api/observability/logs/labels", methods=["GET"])
    @require_token
    @log_request(show_response=False)
    def api_observability_logs_labels():
        """
        获取日志标签列表（需要认证）
        
        返回当前可用的日志标签及其取值，用于构建过滤条件。
        
        Response:
            {
                "labels": {
                    "label_name": ["value1", "value2", ...]
                },
                "timestamp": float
            }
        """
        try:
            from agent.monitoring.loki import get_loki_labels
            
            labels = get_loki_labels()
            
            return jsonify({
                "labels": labels,
                "timestamp": time.time()
            })
        except Exception as e:
            logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'获取日志标签失败: {e}'}))
            return jsonify({"ok": False, "error": str(e)}), 500
    
    @app.route("/api/observability/logs", methods=["POST"])
    @require_token
    @log_request()
    def api_observability_logs_push():
        """
        推送日志（需要认证）
        
        将日志消息推送到 Loki 或本地存储。
        
        Request Body:
            {
                "message": str,        # 必填，日志消息
                "labels": dict,        # 可选，标签字典
                "timestamp": float     # 可选，时间戳
            }
        
        Response:
            {"ok": bool}
        """
        try:
            from agent.monitoring.loki import log_to_loki
            
            data = request.get_json() or {}
            message = data.get("message")
            
            if not message:
                return jsonify({"ok": False, "error": "缺少 message 参数"}), 400
            
            labels = data.get("labels", {})
            timestamp = data.get("timestamp")
            
            log_to_loki(
                message=message,
                labels=labels,
                timestamp=timestamp
            )
            
            return jsonify({"ok": True})
        except Exception as e:
            logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'推送日志失败: {e}'}))
            return jsonify({"ok": False, "error": str(e)}), 500
    
    # ═══════════════════════════════════════════════════
    #  可观测性状态端点
    # ═══════════════════════════════════════════════════
    
    @app.route("/api/observability/state", methods=["GET"])
    @require_token
    @log_request(show_response=False)
    def api_observability_state():
        """
        获取可观测性状态（需要认证）

        返回综合的运行时状态信息，包括追踪、指标、健康和工具状态。

        Response:
            {
                "trace_id": str or null,
                "timestamp": float,
                "health": {...},
                "metrics": {...},
                "tools": {...},
                "config": {...}
            }
        """
        result = {
            "trace_id": get_trace_id(),
            "timestamp": time.time(),
            "health": _get_health_status(),
            "metrics": _get_runtime_metrics(),
            "tools": _get_tool_summary(),
            "config": _get_config_status()
        }
        return jsonify(result)
    
    # ═══════════════════════════════════════════════════
    #  实时日志流端点 (Server-Sent Events)
    # ═══════════════════════════════════════════════════
    
    @app.route("/api/observability/logs/stream")
    @require_token
    def api_observability_logs_stream():
        """
        实时日志流（需要认证）
        
        使用 Server-Sent Events (SSE) 推送实时日志。
        
        Query Parameters:
            trace_id: str, 可选，按 Trace ID 过滤
        
        Response:
            SSE stream with log events
        """
        target_trace_id = request.args.get("trace_id", None)
        
        def generate_logs():
            """生成日志流"""
            last_timestamp = time.time()
            
            while True:
                try:
                    # 获取最近日志
                    logs = _get_recent_logs(limit=10)
                    
                    # 过滤指定 trace_id
                    if target_trace_id:
                        filtered = [
                            log for log in logs.get("logs", [])
                            if log.get("trace_id") == target_trace_id
                        ]
                    else:
                        filtered = logs.get("logs", [])
                    
                    # 发送新日志
                    for log_entry in filtered:
                        if log_entry.get("timestamp", 0) > last_timestamp:
                            yield f"data: {json.dumps(log_entry)}\n\n"
                            last_timestamp = log_entry.get("timestamp", last_timestamp)
                    
                    # 心跳保持连接
                    yield "event: heartbeat\ndata: {}\n\n"
                    
                    # 控制流速率
                    time.sleep(1)
                    
                except Exception as e:
                    logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'日志流生成失败: {e}'}))
                    yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                    break
        
        return Response(
            stream_with_context(generate_logs()),
            content_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive"
            }
        )
    
    # ═══════════════════════════════════════════════════
    #  Prometheus 指标导出端点
    # ═══════════════════════════════════════════════════
    
    @app.route("/metrics", methods=["GET"])
    def api_prometheus_metrics():
        """
        Prometheus 格式指标导出
        
        提供标准 Prometheus 格式的指标数据，供 Prometheus Server 采集。
        
        Response:
            text/plain - Prometheus exposition format
        """
        try:
            from prometheus_client import generate_latest, CollectorRegistry
            
            collector = get_metrics_collector()
            prometheus_output = collector.export_prometheus()
            
            try:
                registry_output = generate_latest(CollectorRegistry())
                prometheus_output = registry_output.decode('utf-8') + '\n' + prometheus_output
            except Exception:
                pass
            
            return Response(
                prometheus_output,
                content_type="text/plain; version=0.0.4; charset=utf-8"
            )
        except Exception as e:
            logger.error(log_dict({'module_name': 'routes_logging', 'action': 'prometheus', 'msg': f'导出 Prometheus 指标失败: {e}'}))
            return f"# Error: {e}", 500
    
    # ═══════════════════════════════════════════════════
    #  告警规则配置端点
    # ═══════════════════════════════════════════════════
    
    @app.route("/api/observability/alerts", methods=["GET"])
    @require_token
    @log_request(show_response=False)
    def api_observability_alerts_list():
        """
        获取告警规则列表（需要认证）
        
        Response:
            {
                "groups": [...],
                "timestamp": float
            }
        """
        rules = _load_alert_rules()
        return jsonify({
            "groups": rules.get("groups", []),
            "timestamp": time.time()
        })
    
    @app.route("/api/observability/alerts", methods=["POST"])
    @require_token
    @log_request()
    def api_observability_alerts_create():
        """
        创建告警规则（需要认证）
        
        Request Body:
            {
                "name": str,        # 规则名称
                "expr": str,        # PromQL 表达式
                "for": str,         # 持续时间 (如 "5m")
                "severity": str,    # 严重级别 (critical/warning/info)
                "summary": str,     # 摘要
                "description": str  # 描述
            }
        
        Response:
            {"ok": bool, "rule": dict}
        """
        data = request.get_json() or {}
        required_fields = ["name", "expr"]
        missing = [f for f in required_fields if f not in data]
        if missing:
            return jsonify({"ok": False, "error": f"缺少必填字段: {', '.join(missing)}"}), 400
        
        new_rule = {
            "alert": data["name"],
            "expr": data["expr"],
            "for": data.get("for", "5m"),
            "labels": {
                "severity": data.get("severity", "warning")
            },
            "annotations": {
                "summary": data.get("summary", data["name"]),
                "description": data.get("description", "")
            }
        }
        
        rules = _load_alert_rules()
        if "groups" not in rules:
            rules["groups"] = []
        
        if not rules["groups"]:
            rules["groups"].append({
                "name": "yunshu_alerts",
                "interval": "30s",
                "rules": []
            })
        
        rules["groups"][0]["rules"].append(new_rule)
        
        if _save_alert_rules(rules):
            return jsonify({"ok": True, "rule": new_rule})
        return jsonify({"ok": False, "error": "保存失败"}), 500
    
    @app.route("/api/observability/alerts/<alert_name>", methods=["PUT"])
    @require_token
    @log_request()
    def api_observability_alerts_update(alert_name):
        """
        更新告警规则（需要认证）
        
        Request Body:
            {
                "expr": str,        # PromQL 表达式 (可选)
                "for": str,         # 持续时间 (可选)
                "severity": str,    # 严重级别 (可选)
                "summary": str,     # 摘要 (可选)
                "description": str  # 描述 (可选)
            }
        
        Response:
            {"ok": bool, "rule": dict or None}
        """
        data = request.get_json() or {}
        rules = _load_alert_rules()
        
        found = None
        for group in rules.get("groups", []):
            for rule in group.get("rules", []):
                if rule.get("alert") == alert_name:
                    found = rule
                    break
            if found:
                break
        
        if not found:
            return jsonify({"ok": False, "error": "规则不存在"}), 404
        
        if "expr" in data:
            found["expr"] = data["expr"]
        if "for" in data:
            found["for"] = data["for"]
        if "severity" in data:
            found["labels"]["severity"] = data["severity"]
        if "summary" in data:
            found["annotations"]["summary"] = data["summary"]
        if "description" in data:
            found["annotations"]["description"] = data["description"]
        
        if _save_alert_rules(rules):
            return jsonify({"ok": True, "rule": found})
        return jsonify({"ok": False, "error": "保存失败"}), 500
    
    @app.route("/api/observability/alerts/<alert_name>", methods=["DELETE"])
    @require_token
    @log_request()
    def api_observability_alerts_delete(alert_name):
        """
        删除告警规则（需要认证）
        
        Response:
            {"ok": bool}
        """
        rules = _load_alert_rules()
        deleted = False
        
        for group in rules.get("groups", []):
            original_count = len(group.get("rules", []))
            group["rules"] = [r for r in group.get("rules", []) if r.get("alert") != alert_name]
            if len(group["rules"]) < original_count:
                deleted = True
                break
        
        if deleted and _save_alert_rules(rules):
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "规则不存在或删除失败"}), 404
    
    @app.route("/api/observability/alerts/validate", methods=["POST"])
    @require_token
    @log_request(show_response=False)
    def api_observability_alerts_validate():
        """
        验证告警规则表达式（需要认证）
        
        Request Body:
            {"expr": str}
        
        Response:
            {"ok": bool, "error": str or None}
        """
        data = request.get_json() or {}
        expr = data.get("expr", "")
        
        if not expr:
            return jsonify({"ok": False, "error": "缺少 expr 参数"}), 400
        
        try:
            from prometheus_client import parser
            parser.parse(expr)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    
    # ═══════════════════════════════════════════════════
    #  追踪可视化端点
    # ═══════════════════════════════════════════════════
    
    @app.route("/api/observability/traces", methods=["GET"])
    @require_token
    @log_request(show_response=False)
    def api_observability_traces():
        """
        获取追踪数据列表（需要认证）
        
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
        
        try:
            from agent.monitoring.tracing import get_recent_traces
            traces = get_recent_traces(limit)
            
            if trace_id_filter:
                traces = [t for t in traces if trace_id_filter in t.get("trace_id", "")]
            
            return jsonify({
                "traces": traces,
                "total": len(traces),
                "limit": limit,
                "timestamp": time.time()
            })
        except Exception as e:
            logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'获取追踪数据失败: {e}'}))
            return jsonify({"ok": False, "error": str(e)}), 500
    
    @app.route("/api/observability/traces/<trace_id>", methods=["GET"])
    @require_token
    @log_request(show_response=False)
    def api_observability_trace_detail(trace_id):
        """
        获取追踪详情（需要认证）
        
        Response:
            {
                "trace_id": str,
                "spans": [...],
                "duration_ms": int,
                "timestamp": float
            }
        """
        try:
            from agent.monitoring.tracing import get_trace_detail
            detail = get_trace_detail(trace_id)
            
            if detail:
                return jsonify(detail)
            return jsonify({"error": "追踪不存在"}), 404
        except Exception as e:
            logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'获取追踪详情失败: {e}'}))
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  可视化仪表盘端点
    # ═══════════════════════════════════════════════════

    @app.route("/dashboard", methods=["GET"])
    def api_dashboard():
        """
        可观测性仪表盘页面

        提供指标、日志、追踪的三位一体可视化界面。
        """
        try:
            from flask import render_template
            return render_template("observability_dashboard.html")
        except Exception as e:
            logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'加载仪表盘页面失败: {e}'}))
            return f"仪表盘加载失败: {e}", 500

    # ═══════════════════════════════════════════════════
    #  访问日志审计端点
    # ═══════════════════════════════════════════════════

    @app.route("/api/observability/access_logs", methods=["GET"])
    @require_token
    @log_request(show_response=False)
    def api_observability_access_logs():
        """
        获取可观测性端点访问日志（需要认证）

        Query Parameters:
            limit: int, 默认100，返回日志条数
            endpoint: str, 可选，按端点过滤
            start_time: float, 可选，开始时间戳
            end_time: float, 可选，结束时间戳

        Response:
            {
                "access_logs": [...],
                "total": int,
                "limit": int,
                "timestamp": float
            }
        """
        try:
            limit = request.args.get("limit", 100, type=int)
            endpoint = request.args.get("endpoint", None)
            start_time = request.args.get("start_time", type=float)
            end_time = request.args.get("end_time", type=float)

            access_logger = get_access_logger()
            logs = access_logger.get_recent_access(
                limit=limit,
                endpoint=endpoint,
                start_time=start_time,
                end_time=end_time
            )

            return jsonify({
                "access_logs": logs,
                "total": len(logs),
                "limit": limit,
                "timestamp": time.time()
            })
        except Exception as e:
            logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'获取访问日志失败: {e}'}))
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/observability/access_stats", methods=["GET"])
    @require_token
    @log_request(show_response=False)
    def api_observability_access_stats():
        """
        获取访问统计信息（需要认证）

        Query Parameters:
            start_time: float, 可选，开始时间戳
            end_time: float, 可选，结束时间戳

        Response:
            {
                "total_accesses": int,
                "unique_endpoints": int,
                "unique_ips": int,
                "avg_response_time_ms": float,
                "error_rate": float,
                "status_codes": {code: count},
                "timestamp": float
            }
        """
        try:
            start_time = request.args.get("start_time", type=float)
            end_time = request.args.get("end_time", type=float)

            access_logger = get_access_logger()
            stats = access_logger.get_access_stats(
                start_time=start_time,
                end_time=end_time
            )

            return jsonify({
                **stats,
                "timestamp": time.time()
            })
        except Exception as e:
            logger.error(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': f'获取访问统计失败: {e}'}))
            return jsonify({"ok": False, "error": str(e)}), 500

    logger.info(log_dict({'module_name': 'routes_logging', 'action': 'log', 'msg': '[Routes] 运行时诊断端点已注册'}))