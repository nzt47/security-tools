"""健康度评估 API 路由

提供系统健康度评分、历史记录、趋势分析等接口。
"""
import logging
import json
import uuid
import time
from datetime import datetime, timedelta
from flask import request, jsonify
from agent.server_auth import log_request
from agent.health.health_score import (
    HealthScoreCalculator,
    get_health_calculator,
    calculate_health_score,
    HealthLevel,
    HealthDimension,
)
from .tracing_decorator import trace_route
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# ═══════════════════════════════════════════════════════════════
#  Prometheus 健康度指标（供 /metrics 暴露，Grafana 采集）
# ═══════════════════════════════════════════════════════════════

try:
    from prometheus_client import Gauge as PromGauge, REGISTRY as PROM_REGISTRY
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    PromGauge = None
    PROM_REGISTRY = None

_HEALTH_GAUGES = {}


def _get_health_gauge(name: str, desc: str) -> object:
    """获取或创建健康度 Gauge 指标（线程安全）"""
    global _HEALTH_GAUGES
    if not _PROMETHEUS_AVAILABLE:
        return None
    if name not in _HEALTH_GAUGES:
        try:
            _HEALTH_GAUGES[name] = PromGauge(name, desc)
        except Exception:
            # 指标可能已被其他模块注册，尝试从注册表获取
            try:
                from prometheus_client import Gauge
                _HEALTH_GAUGES[name] = Gauge(name, desc)
            except Exception:
                return None
    return _HEALTH_GAUGES[name]


def _update_health_metrics(report) -> None:
    """将健康度报告写入 Prometheus Gauge，供外部监控采集"""
    if not _PROMETHEUS_AVAILABLE:
        return
    try:
        gauge = _get_health_gauge("yunshu_health_score", "云枢综合健康度得分 (0-100)")
        if gauge is not None:
            gauge.set(report.overall_score)

        dims = report.dimensions or {}
        for dim_name, dim_score in dims.items():
            g = _get_health_gauge(f"yunshu_health_dimension_{dim_name}", f"云枢{dim_name}维度健康度得分")
            if g is not None:
                g.set(dim_score.score)
    except Exception as e:
        logger.error(f"[Health] 更新 Prometheus 指标失败: {e}")


# ═══════════════════════════════════════════════════════════════
#  应用内告警管理器（加载健康度告警规则）
# ═══════════════════════════════════════════════════════════════

_ALERT_MANAGER_STARTED = False


def _init_alert_manager() -> None:
    """启动应用内告警管理器并注册健康度告警规则（幂等）"""
    global _ALERT_MANAGER_STARTED
    if _ALERT_MANAGER_STARTED:
        return
    _ALERT_MANAGER_STARTED = True
    try:
        from agent.monitoring.alert_manager import start_alert_manager
        import os
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "monitoring", "alerts.yml"
        )
        manager = start_alert_manager(config_path=os.path.normpath(config_path))
        rules_count = len(manager._evaluator._rules) if manager._evaluator else 0
        logger.info(f"[Health] 应用内告警管理器已启动，健康度规则数: {rules_count}")
    except Exception as e:
        logger.error(f"[Health] 启动应用内告警管理器失败: {e}")


# 全局健康度计算器实例
_health_calculator = None  # 保留作为 fallback

try:
    from agent.utils.singleton_manager import register_singleton, get_singleton
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = None
    get_singleton = None


def _create_health_calculator(config=None):
    """HealthScoreCalculator 工厂函数（供 SingletonManager 使用）"""
    return HealthScoreCalculator()


def get_calculator() -> HealthScoreCalculator:
    """获取健康度计算器单例"""
    if _SINGLETON_AVAILABLE:
        return get_singleton("health_calculator")
    global _health_calculator
    if _health_calculator is None:
        _health_calculator = _create_health_calculator()
    return _health_calculator


if _SINGLETON_AVAILABLE:
    register_singleton("health_calculator", _create_health_calculator)


def register_routes(app, state):
    """注册健康度相关路由"""

    # 启动应用内告警管理器（加载健康度告警规则，幂等）
    _init_alert_manager()

    # ═══════════════════════════════════════════════════════════════
    #  健康度评分
    # ═══════════════════════════════════════════════════════════════

    @app.route("/api/health/score")
    @trace_route("HealthScore")
    @log_request(show_response=False)
    def api_health_score():
        """获取当前健康度评分"""
        try:
            # 收集系统指标
            metrics = _collect_system_metrics(state)
            
            # 计算健康度
            calculator = get_calculator()
            report = calculator.calculate(metrics)

            # 更新 Prometheus 指标
            _update_health_metrics(report)
            
            return jsonify(report.to_dict())
        except Exception as e:
            logger.error(f"[HealthScore] 计算健康度失败: {e}", exc_info=True)
            return jsonify({
                "error": str(e),
                "overall_score": 0,
                "level": "critical",
                "timestamp": datetime.now().isoformat(),
            }), 500

    @app.route("/api/health/score/calculate", methods=["POST"])
    @trace_route("HealthScore")
    @log_request()
    def api_health_score_calculate():
        """手动提交指标计算健康度"""
        try:
            metrics = request.get_json() or {}
            
            calculator = get_calculator()
            report = calculator.calculate(metrics)

            # 更新 Prometheus 指标
            _update_health_metrics(report)
            
            return jsonify({
                "ok": True,
                "report": report.to_dict()
            })
        except Exception as e:
            logger.error(f"[HealthScore] 计算健康度失败: {e}", exc_info=True)
            return jsonify({
                "ok": False,
                "error": str(e)
            }), 500

    @app.route("/api/health/trend")
    @trace_route("HealthScore")
    @log_request(show_response=False)
    def api_health_trend():
        """获取健康度趋势"""
        try:
            hours = request.args.get("hours", 24, type=int)
            calculator = get_calculator()
            
            history = calculator.get_history(n=hours)
            trend = calculator.get_trend(n=min(hours, len(history)))
            
            return jsonify({
                "trend": trend,
                "data_points": len(history),
            })
        except Exception as e:
            logger.error(f"[HealthScore] 获取趋势失败: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/health/history")
    @trace_route("HealthScore")
    @log_request(show_response=False)
    def api_health_history():
        """获取健康度历史记录"""
        try:
            limit = request.args.get("limit", 100, type=int)
            offset = request.args.get("offset", 0, type=int)
            
            calculator = get_calculator()
            history = calculator.get_history(n=limit * 2)  # 获取足够的数据
            
            # 分页
            total = len(history)
            paged = history[offset:offset + limit]
            
            return jsonify({
                "history": [h.to_dict() for h in paged],
                "total": total,
                "limit": limit,
                "offset": offset,
            })
        except Exception as e:
            logger.error(f"[HealthScore] 获取历史失败: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/health/weights", methods=["GET", "PUT"])
    @trace_route("HealthScore")
    @log_request()
    def api_health_weights():
        """获取或更新维度权重"""
        try:
            calculator = get_calculator()
            
            if request.method == "GET":
                return jsonify({
                    "weights": calculator.weights,
                    "dimensions": [d.value for d in HealthDimension]
                })
            
            # PUT - 更新权重
            data = request.get_json() or {}
            new_weights = data.get("weights", {})
            
            # 验证权重
            total = sum(new_weights.values())
            if abs(total - 1.0) > 0.01:
                return jsonify({
                    "ok": False,
                    "error": f"权重总和必须为1.0，当前为{total:.2f}"
                }), 400
            
            # 更新权重
            calculator.weights = new_weights
            
            logger.info(log_dict({'module_name': 'routes_health', 'action': 'new_weights', 'msg': f'[HealthScore] 权重已更新: {new_weights}'}))
            
            return jsonify({
                "ok": True,
                "weights": calculator.weights
            })
        except Exception as e:
            logger.error(f"[HealthScore] 权重操作失败: {e}", exc_info=True)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/health/summary")
    @trace_route("HealthScore")
    @log_request(show_response=False)
    def api_health_summary():
        """获取健康度摘要（简化版）"""
        try:
            calculator = get_calculator()
            history = calculator.get_history(n=1)
            
            if not history:
                return jsonify({
                    "overall_score": 100,
                    "level": "excellent",
                    "dimensions": {},
                    "summary": ["系统首次启动，暂无数据"],
                    "recommendations": ["系统正在收集数据，请稍后查看完整报告"],
                })
            
            latest = history[-1]
            return jsonify(latest.to_dict())
        except Exception as e:
            logger.error(f"[HealthScore] 获取摘要失败: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/health/alerts")
    @trace_route("HealthScore")
    @log_request(show_response=False)
    def api_health_alerts():
        """获取健康度告警状态（应用内 AlertManager 实时状态）"""
        try:
            from agent.monitoring.alert_manager import get_alert_manager
            manager = get_alert_manager()
            alerts = manager.get_alerts() if manager else []
            stats = manager.get_stats() if manager else {}

            # 仅返回健康度相关告警
            health_alerts = [
                a for a in alerts
                if a.get("name", "").startswith("YunshuHealth")
                or a.get("name", "").startswith("YunshuStability")
                or a.get("name", "").startswith("YunshuPerformance")
                or a.get("name", "").startswith("YunshuQuality")
                or a.get("name", "").startswith("YunshuAvailability")
                or a.get("name", "").startswith("YunshuSecurity")
            ]

            return jsonify({
                "ok": True,
                "alerts": health_alerts,
                "firing": [a for a in health_alerts if a.get("state") == "firing"],
                "pending": [a for a in health_alerts if a.get("state") == "pending"],
                "stats": stats,
            })
        except Exception as e:
            logger.error(f"[HealthScore] 获取告警状态失败: {e}", exc_info=True)
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════════════════
    #  快速健康检查
    # ═══════════════════════════════════════════════════════════════

    @app.route("/api/health/quick-check")
    @trace_route("HealthScore")
    @log_request(show_response=False)
    def api_health_quick_check():
        """快速健康检查（轻量级）"""
        try:
            start_time = time.time()
            
            # 基本系统检查
            import psutil
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # 简单评分
            score = 100
            issues = []
            
            if cpu_percent > 90:
                score -= 20
                issues.append(f"CPU使用率过高: {cpu_percent:.1f}%")
            elif cpu_percent > 70:
                score -= 10
            
            if memory.percent > 90:
                score -= 20
                issues.append(f"内存使用率过高: {memory.percent:.1f}%")
            elif memory.percent > 75:
                score -= 10
            
            if disk.percent > 90:
                score -= 15
                issues.append(f"磁盘使用率过高: {disk.percent:.1f}%")
            elif disk.percent > 80:
                score -= 5
            
            level = HealthLevel.from_score(score).value
            
            elapsed = (time.time() - start_time) * 1000
            
            return jsonify({
                "ok": True,
                "score": max(0, score),
                "level": level,
                "elapsed_ms": round(elapsed, 2),
                "issues": issues,
                "metrics": {
                    "cpu_percent": round(cpu_percent, 1),
                    "memory_percent": round(memory.percent, 1),
                    "memory_available_gb": round(memory.available / (1024**3), 2),
                    "disk_percent": round(disk.percent, 1),
                    "disk_free_gb": round(disk.free / (1024**3), 2),
                }
            })
        except ImportError:
            return jsonify({
                "ok": False,
                "error": "psutil 未安装",
                "score": 50,
                "level": "fair",
                "issues": ["缺少 psutil 模块"]
            }), 200
        except Exception as e:
            logger.error(f"[HealthScore] 快速检查失败: {e}", exc_info=True)
            return jsonify({
                "ok": False,
                "error": str(e),
                "score": 0,
                "level": "critical",
            }), 500

    # ═══════════════════════════════════════════════════════════════
    #  导出报告
    # ═══════════════════════════════════════════════════════════════

    @app.route("/api/health/export")
    @trace_route("HealthScore")
    @log_request(show_response=False)
    def api_health_export():
        """导出健康度报告"""
        try:
            format_type = request.args.get("format", "json", type=str)
            limit = request.args.get("limit", 100, type=int)
            
            calculator = get_calculator()
            history = calculator.get_history(n=limit)
            
            if format_type == "csv":
                # 生成CSV
                import csv
                import io
                
                output = io.StringIO()
                writer = csv.writer(output)
                
                # 表头
                writer.writerow([
                    "时间", "综合得分", "等级",
                    "稳定性", "性能", "质量", "效率", "可用性", "安全",
                    "问题数", "建议数"
                ])
                
                # 数据
                for h in history:
                    dims = h.dimensions or {}
                    writer.writerow([
                        h.timestamp,
                        f"{h.overall_score:.1f}",
                        h.level,
                        f"{dims.get('stability', {}).score:.1f}",
                        f"{dims.get('performance', {}).score:.1f}",
                        f"{dims.get('quality', {}).score:.1f}",
                        f"{dims.get('efficiency', {}).score:.1f}",
                        f"{dims.get('availability', {}).score:.1f}",
                        f"{dims.get('security', {}).score:.1f}",
                        len(h.critical_issues),
                        len(h.recommendations)
                    ])
                
                return output.getvalue(), 200, {
                    "Content-Type": "text/csv; charset=utf-8",
                    "Content-Disposition": f"attachment; filename=health-report-{datetime.now().date()}.csv"
                }
            
            # JSON格式
            return jsonify({
                "export_time": datetime.now().isoformat(),
                "count": len(history),
                "history": [h.to_dict() for h in history]
            })
        except Exception as e:
            logger.error(f"[HealthScore] 导出失败: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500


def _collect_system_metrics(state) -> dict:
    """收集系统指标用于健康度计算
    
    整合来自各模块的指标数据：
    - 心跳数据
    - Prometheus指标
    - 系统资源
    - 业务指标
    """
    metrics = {
        # 稳定性指标（默认值）
        "error_rate": 0.01,
        "crash_count": 0,
        "retry_count": 0,
        "total_requests": 100,
        "error_spike": False,
        
        # 性能指标
        "p99_latency": 1.0,
        "p95_latency": 0.5,
        "throughput": 10,
        "cpu_usage": 0.5,
        "memory_usage": 0.5,
        "latency_spike": False,
        
        # 质量指标
        "schema_pass_rate": 0.95,
        "critic_score": 80,
        "task_success_rate": 0.9,
        "tool_success_rate": 0.9,
        
        # 效率指标
        "token_efficiency": 0.8,
        "avg_retries": 1.1,
        "cache_hit_rate": 0.5,
        "cost_per_task": 1.0,
        
        # 可用性指标
        "uptime": 0.999,
        "dependency_health": 1.0,
        "healthy_services": 1,
        "total_services": 1,
        "avg_recovery_time": 60,
        
        # 安全指标
        "security_alerts": 0,
        "auth_fail_rate": 0,
        "anomaly_access": 0,
        "vulnerability_count": 0,
    }
    
    try:
        # 尝试从心跳数据获取资源信息
        try:
            from agent.task_scheduler import get_scheduler
            scheduler = get_scheduler()
            heartbeat_data = scheduler.get_heartbeat_status()
            latest = heartbeat_data.get("latest", {})
            checks = latest.get("checks", {})
            system = checks.get("system", {})
            
            if system.get("cpu"):
                metrics["cpu_usage"] = float(system["cpu"]) / 100
            if system.get("memory"):
                metrics["memory_usage"] = float(system["memory"]) / 100
            if system.get("disk"):
                metrics["disk_usage"] = float(system["disk"]) / 100
        except Exception as e:
            logger.debug(log_dict({'module_name': 'routes_health', 'action': 'log', 'msg': f'[HealthScore] 获取心跳数据失败: {e}'}))
        
        # 尝试从Prometheus获取指标
        try:
            from agent.prometheus_exporter import get_prometheus_metrics
            prom_metrics = get_prometheus_metrics()
            
            if prom_metrics:
                # 从Prometheus指标中提取
                if "latency_p99" in prom_metrics:
                    metrics["p99_latency"] = prom_metrics["latency_p99"]
                if "latency_p95" in prom_metrics:
                    metrics["p95_latency"] = prom_metrics["latency_p95"]
                if "error_rate" in prom_metrics:
                    metrics["error_rate"] = prom_metrics["error_rate"]
                if "request_count" in prom_metrics:
                    metrics["total_requests"] = prom_metrics["request_count"]
        except Exception as e:
            logger.debug(log_dict({'module_name': 'routes_health', 'action': 'prometheus', 'msg': f'[HealthScore] 获取Prometheus指标失败: {e}'}))
        
        # 从内存获取最近的任务统计
        try:
            history = get_calculator().get_history(n=10)
            if history:
                latest = history[-1]
                # 参考历史的某些指标
                dims = latest.dimensions
                if dims:
                    stability = dims.get("stability")
                    if stability and stability.indicators.get("error_rate"):
                        # 轻微调整，不完全复制
                        metrics["error_rate"] = min(metrics["error_rate"] * 1.1, 
                                                    stability.indicators["error_rate"])
        except Exception as e:
            logger.debug(log_dict({'module_name': 'routes_health', 'action': 'log', 'msg': f'[HealthScore] 参考历史数据失败: {e}'}))
        
    except Exception as e:
        logger.error(f"[HealthScore] 收集系统指标失败: {e}", exc_info=True)
    
    return metrics
