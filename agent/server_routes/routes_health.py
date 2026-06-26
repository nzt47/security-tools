"""健康度评估 API 路由

提供系统健康度评分、历史记录、趋势分析等接口。
"""
import logging
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

logger = logging.getLogger(__name__)

# 全局健康度计算器实例
_health_calculator = None


def get_calculator() -> HealthScoreCalculator:
    """获取健康度计算器单例"""
    global _health_calculator
    if _health_calculator is None:
        _health_calculator = HealthScoreCalculator()
    return _health_calculator


def register_routes(app, state):
    """注册健康度相关路由"""

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
            
            logger.info(f"[HealthScore] 权重已更新: {new_weights}")
            
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
            logger.debug(f"[HealthScore] 获取心跳数据失败: {e}")
        
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
            logger.debug(f"[HealthScore] 获取Prometheus指标失败: {e}")
        
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
            logger.debug(f"[HealthScore] 参考历史数据失败: {e}")
        
    except Exception as e:
        logger.error(f"[HealthScore] 收集系统指标失败: {e}", exc_info=True)
    
    return metrics
