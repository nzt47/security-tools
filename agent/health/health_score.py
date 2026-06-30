"""系统健康度评估核心模块

基于六大维度的综合健康度评分体系，实时监控云枢智能体的运行状态。

维度设计：
1. 稳定性（Stability）- 错误率、崩溃率、重试次数
2. 性能（Performance）- 响应时间、吞吐量、资源占用
3. 质量（Quality）- Schema校验通过率、Critic评分、任务完成率
4. 效率（Efficiency）- Token使用率、平均重试次数、缓存命中率
5. 可用性（Availability）- 服务在线率、依赖健康度
6. 安全性（Security）- 安全告警数、认证失败率、异常访问

评分机制：
- 每个维度 0-100 分
- 综合得分 = 加权平均
- 健康等级：优秀(90+) / 良好(70-89) / 一般(50-69) / 警告(30-49) / 危险(<30)
"""

import json
import uuid
import time
import logging
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class HealthLevel(Enum):
    """健康等级"""
    EXCELLENT = "excellent"    # 优秀 90+
    GOOD = "good"             # 良好 70-89
    FAIR = "fair"             # 一般 50-69
    WARNING = "warning"       # 警告 30-49
    CRITICAL = "critical"     # 危险 <30

    @classmethod
    def from_score(cls, score: float) -> "HealthLevel":
        if score >= 90:
            return cls.EXCELLENT
        elif score >= 70:
            return cls.GOOD
        elif score >= 50:
            return cls.FAIR
        elif score >= 30:
            return cls.WARNING
        else:
            return cls.CRITICAL


class HealthDimension(Enum):
    """健康度维度"""
    STABILITY = "stability"      # 稳定性
    PERFORMANCE = "performance"  # 性能
    QUALITY = "quality"         # 质量
    EFFICIENCY = "efficiency"    # 效率
    AVAILABILITY = "availability"  # 可用性
    SECURITY = "security"       # 安全性


@dataclass
class DimensionScore:
    """维度得分"""
    name: str
    score: float = 100.0
    weight: float = 1.0
    indicators: Dict[str, float] = field(default_factory=dict)
    issues: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthReport:
    """健康度报告"""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    overall_score: float = 100.0
    level: str = HealthLevel.EXCELLENT.value
    dimensions: Dict[str, DimensionScore] = field(default_factory=dict)
    summary: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    critical_issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "overall_score": round(self.overall_score, 2),
            "level": self.level,
            "dimensions": {
                name: {
                    "score": round(d.score, 2),
                    "weight": d.weight,
                    "indicators": {k: round(v, 2) if isinstance(v, float) else v
                                  for k, v in d.indicators.items()},
                    "issues": d.issues,
                }
                for name, d in self.dimensions.items()
            },
            "summary": self.summary,
            "recommendations": self.recommendations,
            "critical_issues": self.critical_issues,
        }


class HealthScoreCalculator:
    """健康度评分计算器

    六大维度的评分逻辑：
    - 每个维度包含多个指标
    - 每个指标有独立的评分函数
    - 维度得分 = 加权平均指标得分
    - 总得分 = 加权平均维度得分
    """

    DEFAULT_WEIGHTS = {
        HealthDimension.STABILITY.value: 0.20,
        HealthDimension.PERFORMANCE.value: 0.15,
        HealthDimension.QUALITY.value: 0.20,
        HealthDimension.EFFICIENCY.value: 0.15,
        HealthDimension.AVAILABILITY.value: 0.20,
        HealthDimension.SECURITY.value: 0.10,
    }

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self.weights = weights or self.DEFAULT_WEIGHTS
        self._history: List[HealthReport] = []
        self._max_history = 1000

    def calculate(self, metrics: Dict[str, Any]) -> HealthReport:
        """计算综合健康度

        Args:
            metrics: 各项指标数据，包括：
                - error_rate: 错误率
                - crash_count: 崩溃次数
                - retry_count: 重试次数
                - p99_latency: P99延迟(秒)
                - p95_latency: P95延迟(秒)
                - throughput: 吞吐量(请求/秒)
                - cpu_usage: CPU使用率(0-1)
                - memory_usage: 内存使用率(0-1)
                - schema_pass_rate: Schema校验通过率
                - critic_score: Critic平均评分
                - task_success_rate: 任务完成率
                - tool_success_rate: 工具调用成功率
                - token_usage: Token使用率
                - avg_retries: 平均重试次数
                - cache_hit_rate: 缓存命中率
                - uptime: 可用性(0-1)
                - dependency_health: 依赖健康度(0-1)
                - security_alerts: 安全告警数
                - auth_fail_rate: 认证失败率
                - anomaly_access: 异常访问次数

        Returns:
            HealthReport 健康度报告
        """
        report = HealthReport()

        dim_stability = self._calc_stability(metrics)
        dim_performance = self._calc_performance(metrics)
        dim_quality = self._calc_quality(metrics)
        dim_efficiency = self._calc_efficiency(metrics)
        dim_availability = self._calc_availability(metrics)
        dim_security = self._calc_security(metrics)

        report.dimensions = {
            HealthDimension.STABILITY.value: dim_stability,
            HealthDimension.PERFORMANCE.value: dim_performance,
            HealthDimension.QUALITY.value: dim_quality,
            HealthDimension.EFFICIENCY.value: dim_efficiency,
            HealthDimension.AVAILABILITY.value: dim_availability,
            HealthDimension.SECURITY.value: dim_security,
        }

        total_weight = sum(
            self.weights.get(name, dim.weight)
            for name, dim in report.dimensions.items()
        )
        weighted_sum = sum(
            dim.score * self.weights.get(name, dim.weight)
            for name, dim in report.dimensions.items()
        )
        report.overall_score = weighted_sum / total_weight if total_weight > 0 else 0
        report.level = HealthLevel.from_score(report.overall_score).value

        report.summary = self._generate_summary(report)
        report.recommendations = self._generate_recommendations(report)
        report.critical_issues = [
            issue
            for dim in report.dimensions.values()
            for issue in dim.issues
            if any(keyword in issue for keyword in ["严重", "危险", "CRITICAL", "critical", "严重告警"])
        ]

        self._history.append(report)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        logger.info(
            "[HealthScore] 健康度评估完成: score=%.2f, level=%s, dimensions=%s",
            report.overall_score, report.level,
            {k: round(v.score, 1) for k, v in report.dimensions.items()}
        )

        return report

    def _calc_stability(self, metrics: Dict[str, Any]) -> DimensionScore:
        """计算稳定性得分"""
        dim = DimensionScore(name=HealthDimension.STABILITY.value, weight=0.20)
        indicators = {}
        issues = []

        error_rate = metrics.get("error_rate", 0)
        if error_rate <= 0.01:
            indicators["error_rate"] = 100
        elif error_rate <= 0.03:
            indicators["error_rate"] = 90
        elif error_rate <= 0.05:
            indicators["error_rate"] = 70
            issues.append(f"错误率偏高: {error_rate:.1%}")
        elif error_rate <= 0.10:
            indicators["error_rate"] = 50
            issues.append(f"错误率过高: {error_rate:.1%}")
        else:
            indicators["error_rate"] = 20
            issues.append(f"错误率严重: {error_rate:.1%}")

        crash_count = metrics.get("crash_count", 0)
        if crash_count == 0:
            indicators["crash_rate"] = 100
        elif crash_count <= 1:
            indicators["crash_rate"] = 80
            issues.append(f"检测到 {crash_count} 次崩溃")
        elif crash_count <= 3:
            indicators["crash_rate"] = 50
            issues.append(f"崩溃次数较多: {crash_count} 次")
        else:
            indicators["crash_rate"] = 20
            issues.append(f"严重告警: 崩溃 {crash_count} 次")

        retry_count = metrics.get("retry_count", 0)
        total_requests = metrics.get("total_requests", 100)
        retry_rate = retry_count / max(total_requests, 1)
        if retry_rate <= 0.05:
            indicators["retry_rate"] = 100
        elif retry_rate <= 0.10:
            indicators["retry_rate"] = 85
        elif retry_rate <= 0.20:
            indicators["retry_rate"] = 60
            issues.append(f"重试率偏高: {retry_rate:.1%}")
        else:
            indicators["retry_rate"] = 40
            issues.append(f"重试率过高: {retry_rate:.1%}")

        error_spike = metrics.get("error_spike", False)
        if error_spike:
            indicators["error_spike"] = 30
            issues.append("错误率突增，需要立即关注")
        else:
            indicators["error_spike"] = 100

        dim.indicators = indicators
        dim.issues = issues
        dim.score = sum(indicators.values()) / len(indicators) if indicators else 0
        return dim

    def _calc_performance(self, metrics: Dict[str, Any]) -> DimensionScore:
        """计算性能得分"""
        dim = DimensionScore(name=HealthDimension.PERFORMANCE.value, weight=0.15)
        indicators = {}
        issues = []

        p99 = metrics.get("p99_latency", 1.0)
        if p99 <= 1.0:
            indicators["p99_latency"] = 100
        elif p99 <= 2.0:
            indicators["p99_latency"] = 85
        elif p99 <= 3.0:
            indicators["p99_latency"] = 70
            issues.append(f"P99延迟偏高: {p99:.2f}s")
        elif p99 <= 5.0:
            indicators["p99_latency"] = 50
            issues.append(f"P99延迟过高: {p99:.2f}s")
        else:
            indicators["p99_latency"] = 30
            issues.append(f"P99延迟严重: {p99:.2f}s")

        p95 = metrics.get("p95_latency", 0.5)
        if p95 <= 0.5:
            indicators["p95_latency"] = 100
        elif p95 <= 1.0:
            indicators["p95_latency"] = 90
        elif p95 <= 2.0:
            indicators["p95_latency"] = 70
        else:
            indicators["p95_latency"] = 50
            issues.append(f"P95延迟偏高: {p95:.2f}s")

        throughput = metrics.get("throughput", 10)
        if throughput >= 50:
            indicators["throughput"] = 100
        elif throughput >= 20:
            indicators["throughput"] = 85
        elif throughput >= 10:
            indicators["throughput"] = 70
        elif throughput >= 5:
            indicators["throughput"] = 50
        else:
            indicators["throughput"] = 30

        cpu = metrics.get("cpu_usage", 0.5)
        if cpu <= 0.5:
            indicators["cpu_usage"] = 100
        elif cpu <= 0.7:
            indicators["cpu_usage"] = 85
        elif cpu <= 0.85:
            indicators["cpu_usage"] = 60
            issues.append(f"CPU使用率偏高: {cpu:.1%}")
        else:
            indicators["cpu_usage"] = 30
            issues.append(f"CPU使用率过高: {cpu:.1%}")

        memory = metrics.get("memory_usage", 0.5)
        if memory <= 0.6:
            indicators["memory_usage"] = 100
        elif memory <= 0.75:
            indicators["memory_usage"] = 80
        elif memory <= 0.85:
            indicators["memory_usage"] = 50
            issues.append(f"内存使用率偏高: {memory:.1%}")
        else:
            indicators["memory_usage"] = 20
            issues.append(f"内存使用率危险: {memory:.1%}")

        latency_spike = metrics.get("latency_spike", False)
        if latency_spike:
            indicators["latency_spike"] = 40
            issues.append("响应时间突增")
        else:
            indicators["latency_spike"] = 100

        dim.indicators = indicators
        dim.issues = issues
        dim.score = sum(indicators.values()) / len(indicators) if indicators else 0
        return dim

    def _calc_quality(self, metrics: Dict[str, Any]) -> DimensionScore:
        """计算质量得分"""
        dim = DimensionScore(name=HealthDimension.QUALITY.value, weight=0.20)
        indicators = {}
        issues = []

        schema_pass = metrics.get("schema_pass_rate", 0.95)
        if schema_pass >= 0.99:
            indicators["schema_pass_rate"] = 100
        elif schema_pass >= 0.95:
            indicators["schema_pass_rate"] = 90
        elif schema_pass >= 0.90:
            indicators["schema_pass_rate"] = 70
            issues.append(f"Schema校验通过率偏低: {schema_pass:.1%}")
        elif schema_pass >= 0.80:
            indicators["schema_pass_rate"] = 50
            issues.append(f"Schema校验通过率过低: {schema_pass:.1%}")
        else:
            indicators["schema_pass_rate"] = 20
            issues.append(f"Schema校验严重异常: {schema_pass:.1%}")

        critic = metrics.get("critic_score", 80)
        if critic >= 90:
            indicators["critic_score"] = 100
        elif critic >= 80:
            indicators["critic_score"] = 85
        elif critic >= 70:
            indicators["critic_score"] = 70
        elif critic >= 60:
            indicators["critic_score"] = 50
            issues.append(f"Critic评分偏低: {critic:.1f}")
        else:
            indicators["critic_score"] = 30
            issues.append(f"Critic评分过低: {critic:.1f}")

        task_success = metrics.get("task_success_rate", 0.9)
        if task_success >= 0.95:
            indicators["task_success_rate"] = 100
        elif task_success >= 0.85:
            indicators["task_success_rate"] = 85
        elif task_success >= 0.70:
            indicators["task_success_rate"] = 65
            issues.append(f"任务完成率偏低: {task_success:.1%}")
        else:
            indicators["task_success_rate"] = 40
            issues.append(f"任务完成率过低: {task_success:.1%}")

        tool_success = metrics.get("tool_success_rate", 0.9)
        if tool_success >= 0.95:
            indicators["tool_success_rate"] = 100
        elif tool_success >= 0.85:
            indicators["tool_success_rate"] = 80
        elif tool_success >= 0.75:
            indicators["tool_success_rate"] = 60
            issues.append(f"工具调用成功率偏低: {tool_success:.1%}")
        else:
            indicators["tool_success_rate"] = 35
            issues.append(f"工具调用成功率过低: {tool_success:.1%}")

        dim.indicators = indicators
        dim.issues = issues
        dim.score = sum(indicators.values()) / len(indicators) if indicators else 0
        return dim

    def _calc_efficiency(self, metrics: Dict[str, Any]) -> DimensionScore:
        """计算效率得分"""
        dim = DimensionScore(name=HealthDimension.EFFICIENCY.value, weight=0.15)
        indicators = {}
        issues = []

        token_efficiency = metrics.get("token_efficiency", 0.8)
        if token_efficiency >= 0.9:
            indicators["token_efficiency"] = 100
        elif token_efficiency >= 0.75:
            indicators["token_efficiency"] = 85
        elif token_efficiency >= 0.6:
            indicators["token_efficiency"] = 65
        else:
            indicators["token_efficiency"] = 40
            issues.append(f"Token效率偏低: {token_efficiency:.1%}")

        avg_retries = metrics.get("avg_retries", 1.1)
        if avg_retries <= 1.1:
            indicators["avg_retries"] = 100
        elif avg_retries <= 1.3:
            indicators["avg_retries"] = 85
        elif avg_retries <= 1.5:
            indicators["avg_retries"] = 65
            issues.append(f"平均重试次数偏高: {avg_retries:.2f}")
        else:
            indicators["avg_retries"] = 40
            issues.append(f"平均重试次数过高: {avg_retries:.2f}")

        cache_hit = metrics.get("cache_hit_rate", 0.5)
        if cache_hit >= 0.8:
            indicators["cache_hit_rate"] = 100
        elif cache_hit >= 0.6:
            indicators["cache_hit_rate"] = 85
        elif cache_hit >= 0.4:
            indicators["cache_hit_rate"] = 65
        elif cache_hit >= 0.2:
            indicators["cache_hit_rate"] = 45
        else:
            indicators["cache_hit_rate"] = 25
            issues.append(f"缓存命中率过低: {cache_hit:.1%}")

        cost_per_task = metrics.get("cost_per_task", 1.0)
        if cost_per_task <= 0.5:
            indicators["cost_efficiency"] = 100
        elif cost_per_task <= 1.0:
            indicators["cost_efficiency"] = 85
        elif cost_per_task <= 2.0:
            indicators["cost_efficiency"] = 65
        elif cost_per_task <= 5.0:
            indicators["cost_efficiency"] = 40
            issues.append(f"单任务成本偏高: ${cost_per_task:.2f}")
        else:
            indicators["cost_efficiency"] = 20
            issues.append(f"单任务成本过高: ${cost_per_task:.2f}")

        dim.indicators = indicators
        dim.issues = issues
        dim.score = sum(indicators.values()) / len(indicators) if indicators else 0
        return dim

    def _calc_availability(self, metrics: Dict[str, Any]) -> DimensionScore:
        """计算可用性得分"""
        dim = DimensionScore(name=HealthDimension.AVAILABILITY.value, weight=0.20)
        indicators = {}
        issues = []

        uptime = metrics.get("uptime", 1.0)
        if uptime >= 0.999:
            indicators["uptime"] = 100
        elif uptime >= 0.995:
            indicators["uptime"] = 95
        elif uptime >= 0.99:
            indicators["uptime"] = 85
        elif uptime >= 0.95:
            indicators["uptime"] = 65
            issues.append(f"可用性偏低: {uptime:.3%}")
        else:
            indicators["uptime"] = 30
            issues.append(f"可用性严重不足: {uptime:.3%}")

        dependency = metrics.get("dependency_health", 1.0)
        if dependency >= 0.95:
            indicators["dependency_health"] = 100
        elif dependency >= 0.85:
            indicators["dependency_health"] = 80
        elif dependency >= 0.70:
            indicators["dependency_health"] = 55
            issues.append(f"依赖健康度偏低: {dependency:.1%}")
        else:
            indicators["dependency_health"] = 25
            issues.append(f"依赖健康度过低: {dependency:.1%}")

        service_count = metrics.get("healthy_services", 1)
        total_services = metrics.get("total_services", 1)
        service_ratio = service_count / max(total_services, 1)
        if service_ratio >= 1.0:
            indicators["service_health"] = 100
        elif service_ratio >= 0.9:
            indicators["service_health"] = 80
        elif service_ratio >= 0.75:
            indicators["service_health"] = 55
            issues.append(f"部分服务异常: {service_count}/{total_services}")
        else:
            indicators["service_health"] = 25
            issues.append(f"大量服务异常: {service_count}/{total_services}")

        recovery_time = metrics.get("avg_recovery_time", 60)
        if recovery_time <= 30:
            indicators["recovery_time"] = 100
        elif recovery_time <= 60:
            indicators["recovery_time"] = 85
        elif recovery_time <= 300:
            indicators["recovery_time"] = 60
        else:
            indicators["recovery_time"] = 30
            issues.append(f"平均恢复时间过长: {recovery_time}s")

        dim.indicators = indicators
        dim.issues = issues
        dim.score = sum(indicators.values()) / len(indicators) if indicators else 0
        return dim

    def _calc_security(self, metrics: Dict[str, Any]) -> DimensionScore:
        """计算安全性得分"""
        dim = DimensionScore(name=HealthDimension.SECURITY.value, weight=0.10)
        indicators = {}
        issues = []

        security_alerts = metrics.get("security_alerts", 0)
        if security_alerts == 0:
            indicators["security_alerts"] = 100
        elif security_alerts <= 2:
            indicators["security_alerts"] = 70
            issues.append(f"检测到 {security_alerts} 个安全告警")
        elif security_alerts <= 5:
            indicators["security_alerts"] = 40
            issues.append(f"安全告警较多: {security_alerts} 个")
        else:
            indicators["security_alerts"] = 10
            issues.append(f"严重告警: 安全告警 {security_alerts} 个")

        auth_fail_rate = metrics.get("auth_fail_rate", 0)
        if auth_fail_rate <= 0.01:
            indicators["auth_security"] = 100
        elif auth_fail_rate <= 0.03:
            indicators["auth_security"] = 80
        elif auth_fail_rate <= 0.05:
            indicators["auth_security"] = 50
            issues.append(f"认证失败率偏高: {auth_fail_rate:.1%}")
        else:
            indicators["auth_security"] = 20
            issues.append(f"认证失败率过高，可能存在攻击: {auth_fail_rate:.1%}")

        anomaly_access = metrics.get("anomaly_access", 0)
        if anomaly_access == 0:
            indicators["anomaly_access"] = 100
        elif anomaly_access <= 3:
            indicators["anomaly_access"] = 75
            issues.append(f"检测到 {anomaly_access} 次异常访问")
        elif anomaly_access <= 10:
            indicators["anomaly_access"] = 45
            issues.append(f"异常访问较多: {anomaly_access} 次")
        else:
            indicators["anomaly_access"] = 15
            issues.append(f"严重: 异常访问频繁: {anomaly_access} 次")

        vuln_count = metrics.get("vulnerability_count", 0)
        if vuln_count == 0:
            indicators["vulnerabilities"] = 100
        elif vuln_count <= 2:
            indicators["vulnerabilities"] = 70
            issues.append(f"发现 {vuln_count} 个漏洞")
        elif vuln_count <= 5:
            indicators["vulnerabilities"] = 40
            issues.append(f"漏洞较多: {vuln_count} 个")
        else:
            indicators["vulnerabilities"] = 10
            issues.append(f"严重: 存在 {vuln_count} 个漏洞")

        dim.indicators = indicators
        dim.issues = issues
        dim.score = sum(indicators.values()) / len(indicators) if indicators else 0
        return dim

    def _generate_summary(self, report: HealthReport) -> List[str]:
        """生成健康度摘要"""
        summary = []
        level = HealthLevel(report.level)

        if level == HealthLevel.EXCELLENT:
            summary.append("✅ 系统运行状态优秀，所有指标健康")
        elif level == HealthLevel.GOOD:
            summary.append("✅ 系统运行状态良好，整体健康")
        elif level == HealthLevel.FAIR:
            summary.append("⚠️ 系统状态一般，部分指标需要关注")
        elif level == HealthLevel.WARNING:
            summary.append("⚠️ 系统状态警告，多项指标异常")
        else:
            summary.append("🔴 系统状态危险，需要立即处理")

        best_dim = max(report.dimensions.values(), key=lambda d: d.score)
        worst_dim = min(report.dimensions.values(), key=lambda d: d.score)

        summary.append(f"最佳维度: {best_dim.name} ({best_dim.score:.1f}分)")
        if worst_dim.score < 70:
            summary.append(f"最弱维度: {worst_dim.name} ({worst_dim.score:.1f}分)")

        total_issues = sum(len(d.issues) for d in report.dimensions.values())
        if total_issues > 0:
            summary.append(f"发现 {total_issues} 个待改进项")

        return summary

    def _generate_recommendations(self, report: HealthReport) -> List[str]:
        """生成优化建议"""
        recommendations = []

        dim_stability = report.dimensions.get(HealthDimension.STABILITY.value)
        if dim_stability and dim_stability.score < 70:
            if any("错误率" in issue for issue in dim_stability.issues):
                recommendations.append("排查近期代码变更，定位错误率上升原因")
            if any("崩溃" in issue for issue in dim_stability.issues):
                recommendations.append("检查崩溃日志，分析内存泄漏或资源耗尽问题")

        dim_performance = report.dimensions.get(HealthDimension.PERFORMANCE.value)
        if dim_performance and dim_performance.score < 70:
            if any("延迟" in issue for issue in dim_performance.issues):
                recommendations.append("分析性能瓶颈，优化慢查询和外部API调用")
            if any("内存" in issue for issue in dim_performance.issues):
                recommendations.append("排查内存占用，考虑优化缓存或增加内存")

        dim_quality = report.dimensions.get(HealthDimension.QUALITY.value)
        if dim_quality and dim_quality.score < 70:
            if any("Schema" in issue for issue in dim_quality.issues):
                recommendations.append("优化Prompt，提升结构化输出稳定性")
            if any("Critic" in issue for issue in dim_quality.issues):
                recommendations.append("分析低分案例，优化核心Prompt和工具调用策略")

        dim_efficiency = report.dimensions.get(HealthDimension.EFFICIENCY.value)
        if dim_efficiency and dim_efficiency.score < 70:
            if any("缓存" in issue for issue in dim_efficiency.issues):
                recommendations.append("增加缓存策略，提升常用查询命中率")
            if any("成本" in issue for issue in dim_efficiency.issues):
                recommendations.append("优化Prompt长度，考虑使用更经济的模型")

        dim_availability = report.dimensions.get(HealthDimension.AVAILABILITY.value)
        if dim_availability and dim_availability.score < 70:
            recommendations.append("检查依赖服务健康度，考虑添加降级策略")

        dim_security = report.dimensions.get(HealthDimension.SECURITY.value)
        if dim_security and dim_security.score < 70:
            recommendations.append("立即排查安全告警，加强访问控制")

        if not recommendations:
            recommendations.append("系统运行良好，继续保持监控")

        return recommendations

    def get_history(self, n: int = 10) -> List[HealthReport]:
        """获取历史健康度报告"""
        return self._history[-n:]

    def get_trend(self, n: int = 10) -> Dict[str, Any]:
        """获取健康度趋势"""
        if len(self._history) < 2:
            return {"trend": "insufficient_data", "change": 0}

        recent = self._history[-min(n, len(self._history)):]
        scores = [r.overall_score for r in recent]

        if len(scores) >= 2:
            change = scores[-1] - scores[0]
            if change > 5:
                trend = "improving"
            elif change < -5:
                trend = "deteriorating"
            else:
                trend = "stable"
        else:
            change = 0
            trend = "stable"

        return {
            "trend": trend,
            "change": round(change, 2),
            "avg_score": round(sum(scores) / len(scores), 2),
            "min_score": round(min(scores), 2),
            "max_score": round(max(scores), 2),
            "data_points": len(scores),
        }


_default_calculator: Optional[HealthScoreCalculator] = None


def get_health_calculator() -> HealthScoreCalculator:
    """获取全局健康度计算器实例"""
    global _default_calculator
    if _default_calculator is None:
        _default_calculator = HealthScoreCalculator()
    return _default_calculator


def calculate_health_score(metrics: Dict[str, Any]) -> HealthReport:
    """快捷函数：计算健康度"""
    return get_health_calculator().calculate(metrics)


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "health_score",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
