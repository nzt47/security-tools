"""BT-009: health 模块边界测试

覆盖 agent/health/ 下的 HealthAssessor（assessor.py）和
HealthScoreCalculator（health_score.py）的边界场景。

边界场景覆盖（满足 boundary_config.yaml 中 health 模块要求）：
- empty: 空 metrics 字典、空 history、空 issues
- invalid: None metrics、非法类型、负数值、超范围值、除零防御
- extreme: 极端健康度（全最差/全最好）、大量 history、极值指标
- boundary: 各维度评分阈值切换点
- null: None 输入处理

【可观测性约束】
- 结构化日志：HealthScoreCalculator.calculate 已内置 logger.info 输出
- 边界显性化：非法类型输入抛 AttributeError 而非静默返回
- 健康检查：HealthReport.to_dict() 提供完整状态输出
"""
import pytest

from agent.health.assessor import HealthAssessor, HealthScore
from agent.health.health_score import (
    HealthScoreCalculator,
    HealthLevel,
    HealthDimension,
    DimensionScore,
    HealthReport,
)


# ── fixtures ──

@pytest.fixture
def assessor():
    """全新的 HealthAssessor 实例（避免全局单例污染）"""
    return HealthAssessor()


@pytest.fixture
def calculator():
    """全新的 HealthScoreCalculator 实例（避免全局单例污染）"""
    return HealthScoreCalculator()


@pytest.fixture
def good_metrics():
    """优秀健康度指标 — 所有维度满分"""
    return {
        "error_rate": 0.001,
        "crash_count": 0,
        "retry_count": 1,
        "total_requests": 1000,
        "error_spike": False,
        "p99_latency": 0.5,
        "p95_latency": 0.2,
        "throughput": 100,
        "cpu_usage": 0.3,
        "memory_usage": 0.4,
        "latency_spike": False,
        "schema_pass_rate": 0.999,
        "critic_score": 95,
        "task_success_rate": 0.99,
        "tool_success_rate": 0.98,
        "token_efficiency": 0.95,
        "avg_retries": 1.0,
        "cache_hit_rate": 0.9,
        "cost_per_task": 0.3,
        "uptime": 0.9999,
        "dependency_health": 0.99,
        "healthy_services": 10,
        "total_services": 10,
        "avg_recovery_time": 10,
        "security_alerts": 0,
        "auth_fail_rate": 0.001,
        "anomaly_access": 0,
        "vulnerability_count": 0,
    }


@pytest.fixture
def worst_metrics():
    """最差健康度指标 — 所有维度低分"""
    return {
        "error_rate": 0.5,
        "crash_count": 10,
        "retry_count": 500,
        "total_requests": 1000,
        "error_spike": True,
        "p99_latency": 10.0,
        "p95_latency": 5.0,
        "throughput": 1,
        "cpu_usage": 0.95,
        "memory_usage": 0.95,
        "latency_spike": True,
        "schema_pass_rate": 0.5,
        "critic_score": 40,
        "task_success_rate": 0.5,
        "tool_success_rate": 0.5,
        "token_efficiency": 0.3,
        "avg_retries": 2.0,
        "cache_hit_rate": 0.1,
        "cost_per_task": 10.0,
        "uptime": 0.9,
        "dependency_health": 0.5,
        "healthy_services": 2,
        "total_services": 10,
        "avg_recovery_time": 600,
        "security_alerts": 20,
        "auth_fail_rate": 0.1,
        "anomaly_access": 50,
        "vulnerability_count": 10,
    }


# ═══════════════════════════════════════════════════════════════
#  Empty 边界场景
# ═══════════════════════════════════════════════════════════════

class TestEmptyBoundary:
    """空值/空容器边界测试"""

    def test_empty_metrics_assessor(self, assessor):
        """空 metrics 字典 — assess 返回默认满分"""
        score = assessor.assess({})
        assert isinstance(score, HealthScore)
        assert score.overall == 1.0
        assert score.issues == []

    def test_empty_metrics_calculator(self, calculator):
        """空 metrics 字典 — calculate 使用所有默认值，返回有效报告"""
        report = calculator.calculate({})
        assert isinstance(report, HealthReport)
        assert report.overall_score > 0
        assert isinstance(report.dimensions, dict)
        assert len(report.dimensions) == 6  # 六大维度

    def test_empty_history_get_trend(self, calculator):
        """空 history — get_trend 返回 insufficient_data"""
        trend = calculator.get_trend()
        assert trend["trend"] == "insufficient_data"
        assert trend["change"] == 0

    def test_empty_history_single_point_get_trend(self, calculator):
        """单个 history 点 — get_trend 仍返回 insufficient_data（len<2）"""
        calculator.calculate({})
        trend = calculator.get_trend()
        assert trend["trend"] == "insufficient_data"

    def test_empty_issues_good_metrics(self, calculator, good_metrics):
        """优秀指标 — 所有维度无 issues"""
        report = calculator.calculate(good_metrics)
        total_issues = sum(len(d.issues) for d in report.dimensions.values())
        assert total_issues == 0
        assert report.level == HealthLevel.EXCELLENT.value
        assert report.overall_score >= 90


# ═══════════════════════════════════════════════════════════════
#  Invalid 边界场景
# ═══════════════════════════════════════════════════════════════

class TestInvalidBoundary:
    """非法输入边界测试"""

    def test_invalid_metrics_none_assessor(self, assessor):
        """None 作为 metrics — assessor.assess(None) 返回默认满分（None 是 falsy 跳过分支）"""
        score = assessor.assess(None)
        assert isinstance(score, HealthScore)
        assert score.overall == 1.0

    def test_invalid_metrics_none_calculator(self, calculator):
        """None 作为 metrics — calculator.calculate(None) 抛 AttributeError"""
        with pytest.raises(AttributeError):
            calculator.calculate(None)

    def test_invalid_metrics_string_calculator(self, calculator):
        """字符串作为 metrics — 抛 AttributeError（字符串无 .get 方法）"""
        with pytest.raises(AttributeError):
            calculator.calculate("invalid_metrics")

    def test_invalid_metrics_list_calculator(self, calculator):
        """列表作为 metrics — 抛 AttributeError"""
        with pytest.raises(AttributeError):
            calculator.calculate([1, 2, 3])

    def test_invalid_negative_error_rate(self, calculator):
        """负数 error_rate — 被当作低错误率处理（防御性，不抛异常）"""
        metrics = {"error_rate": -0.5}
        report = calculator.calculate(metrics)
        dim = report.dimensions[HealthDimension.STABILITY.value]
        assert dim.indicators["error_rate"] == 100  # -0.5 <= 0.01

    def test_invalid_negative_latency(self, calculator):
        """负数 latency — 被当作低延迟处理（防御性）"""
        metrics = {"p99_latency": -1.0, "p95_latency": -0.5}
        report = calculator.calculate(metrics)
        dim = report.dimensions[HealthDimension.PERFORMANCE.value]
        assert dim.indicators["p99_latency"] == 100
        assert dim.indicators["p95_latency"] == 100

    def test_invalid_overflow_usage(self, calculator):
        """usage > 1.0 — 被当作高使用率处理（防御性，不抛异常）"""
        metrics = {"cpu_usage": 1.5, "memory_usage": 2.0}
        report = calculator.calculate(metrics)
        dim = report.dimensions[HealthDimension.PERFORMANCE.value]
        assert dim.indicators["cpu_usage"] == 30  # > 0.85 走 else
        assert dim.indicators["memory_usage"] == 20  # > 0.85 走 else

    def test_invalid_zero_total_requests(self, calculator):
        """total_requests=0 — retry_rate 使用 max(0,1) 防御除零"""
        metrics = {"retry_count": 10, "total_requests": 0}
        report = calculator.calculate(metrics)
        assert isinstance(report, HealthReport)

    def test_invalid_zero_total_services(self, calculator):
        """total_services=0 — service_ratio 使用 max(0,1) 防御除零"""
        metrics = {"healthy_services": 0, "total_services": 0}
        report = calculator.calculate(metrics)
        assert isinstance(report, HealthReport)


# ═══════════════════════════════════════════════════════════════
#  Extreme 边界场景
# ═══════════════════════════════════════════════════════════════

class TestExtremeBoundary:
    """极值边界测试"""

    def test_extreme_all_worst_metrics(self, calculator, worst_metrics):
        """所有指标最差 — 健康度应为 CRITICAL 或 WARNING"""
        report = calculator.calculate(worst_metrics)
        assert report.overall_score < 50
        assert report.level in (HealthLevel.WARNING.value, HealthLevel.CRITICAL.value)
        total_issues = sum(len(d.issues) for d in report.dimensions.values())
        assert total_issues >= 6
        assert len(report.critical_issues) > 0

    def test_extreme_all_best_metrics(self, calculator, good_metrics):
        """所有指标最好 — 健康度应为 EXCELLENT 满分"""
        report = calculator.calculate(good_metrics)
        assert report.overall_score >= 90
        assert report.level == HealthLevel.EXCELLENT.value

    def test_extreme_huge_error_rate(self, calculator):
        """极大错误率 100% — 错误率严重"""
        metrics = {"error_rate": 1.0}
        report = calculator.calculate(metrics)
        dim = report.dimensions[HealthDimension.STABILITY.value]
        assert dim.indicators["error_rate"] == 20
        assert any("错误率严重" in i for i in dim.issues)

    def test_extreme_huge_latency(self, calculator):
        """极大延迟 100s — P99 延迟严重"""
        metrics = {"p99_latency": 100.0}
        report = calculator.calculate(metrics)
        dim = report.dimensions[HealthDimension.PERFORMANCE.value]
        assert dim.indicators["p99_latency"] == 30
        assert any("P99延迟严重" in i for i in dim.issues)

    def test_extreme_many_crashes(self, calculator):
        """大量崩溃 100 次 — 严重告警并出现在 critical_issues"""
        metrics = {"crash_count": 100}
        report = calculator.calculate(metrics)
        dim = report.dimensions[HealthDimension.STABILITY.value]
        assert dim.indicators["crash_rate"] == 20
        assert any("严重告警" in i for i in dim.issues)
        assert any("崩溃" in i for i in report.critical_issues)

    def test_extreme_many_security_alerts(self, calculator):
        """大量安全告警 — 严重告警"""
        metrics = {"security_alerts": 100}
        report = calculator.calculate(metrics)
        dim = report.dimensions[HealthDimension.SECURITY.value]
        assert dim.indicators["security_alerts"] == 10
        assert any("严重告警" in i for i in dim.issues)

    def test_extreme_large_history_calculator(self, calculator):
        """calculator 大量 history（>1000）— 自动 pop 最旧记录"""
        for i in range(1100):
            calculator.calculate({"error_rate": 0.01 * (i % 10)})
        assert len(calculator._history) == 1000
        assert len(calculator.get_history(10)) == 10

    def test_extreme_large_history_assessor(self, assessor):
        """assessor 大量 history（>100）— 自动 pop"""
        for i in range(150):
            assessor.assess({"avg_response_ms": 100})
        assert len(assessor._history) == 100

    def test_extreme_low_score_stability(self, calculator):
        """稳定性维度极低分 — 所有稳定性指标走最低分分支"""
        metrics = {
            "error_rate": 1.0,      # 20
            "crash_count": 100,     # 20
            "retry_count": 1000,    # 40
            "total_requests": 100,
            "error_spike": True,    # 30
        }
        report = calculator.calculate(metrics)
        dim = report.dimensions[HealthDimension.STABILITY.value]
        assert dim.score < 30

    def test_extreme_recommendations_generated(self, calculator, worst_metrics):
        """最差指标 — 应生成优化建议，不出现【运行良好】字样"""
        report = calculator.calculate(worst_metrics)
        assert len(report.recommendations) > 0
        assert not any("运行良好" in r for r in report.recommendations)


# ═══════════════════════════════════════════════════════════════
#  Boundary 阈值边界场景
# ═══════════════════════════════════════════════════════════════

class TestBoundaryThreshold:
    """阈值边界测试 — 验证各维度评分阈值切换点"""

    def test_boundary_error_rate_thresholds(self, calculator):
        """错误率阈值边界 0.01/0.03/0.05/0.10"""
        r1 = calculator.calculate({"error_rate": 0.01})
        assert r1.dimensions[HealthDimension.STABILITY.value].indicators["error_rate"] == 100
        r2 = calculator.calculate({"error_rate": 0.03})
        assert r2.dimensions[HealthDimension.STABILITY.value].indicators["error_rate"] == 90
        r3 = calculator.calculate({"error_rate": 0.05})
        assert r3.dimensions[HealthDimension.STABILITY.value].indicators["error_rate"] == 70
        r4 = calculator.calculate({"error_rate": 0.10})
        assert r4.dimensions[HealthDimension.STABILITY.value].indicators["error_rate"] == 50

    def test_boundary_latency_thresholds(self, calculator):
        """P99 延迟阈值边界 1.0/2.0/3.0/5.0"""
        r1 = calculator.calculate({"p99_latency": 1.0})
        assert r1.dimensions[HealthDimension.PERFORMANCE.value].indicators["p99_latency"] == 100
        r2 = calculator.calculate({"p99_latency": 2.0})
        assert r2.dimensions[HealthDimension.PERFORMANCE.value].indicators["p99_latency"] == 85
        r3 = calculator.calculate({"p99_latency": 3.0})
        assert r3.dimensions[HealthDimension.PERFORMANCE.value].indicators["p99_latency"] == 70
        r4 = calculator.calculate({"p99_latency": 5.0})
        assert r4.dimensions[HealthDimension.PERFORMANCE.value].indicators["p99_latency"] == 50

    def test_boundary_health_level_from_score(self):
        """HealthLevel.from_score 阈值边界 90/70/50/30"""
        assert HealthLevel.from_score(90) == HealthLevel.EXCELLENT
        assert HealthLevel.from_score(89.99) == HealthLevel.GOOD
        assert HealthLevel.from_score(70) == HealthLevel.GOOD
        assert HealthLevel.from_score(69.99) == HealthLevel.FAIR
        assert HealthLevel.from_score(50) == HealthLevel.FAIR
        assert HealthLevel.from_score(49.99) == HealthLevel.WARNING
        assert HealthLevel.from_score(30) == HealthLevel.WARNING
        assert HealthLevel.from_score(29.99) == HealthLevel.CRITICAL
        assert HealthLevel.from_score(0) == HealthLevel.CRITICAL

    def test_boundary_assessor_response_time(self, assessor):
        """assessor 响应时间阈值 5000/10000"""
        s1 = assessor.assess({"avg_response_ms": 5000, "error_rate": 0})
        assert s1.dimensions["response_time"] == 1.0
        s2 = assessor.assess({"avg_response_ms": 5001, "error_rate": 0})
        assert s2.dimensions["response_time"] == 0.6
        s3 = assessor.assess({"avg_response_ms": 10001, "error_rate": 0})
        assert s3.dimensions["response_time"] == 0.3

    def test_boundary_assessor_error_rate(self, assessor):
        """assessor 错误率阈值 0.1/0.2"""
        s1 = assessor.assess({"avg_response_ms": 100, "error_rate": 0.1})
        assert s1.dimensions["error_rate"] == 1.0
        s2 = assessor.assess({"avg_response_ms": 100, "error_rate": 0.11})
        assert s2.dimensions["error_rate"] == 0.6
        s3 = assessor.assess({"avg_response_ms": 100, "error_rate": 0.21})
        assert s3.dimensions["error_rate"] == 0.2


# ═══════════════════════════════════════════════════════════════
#  Null 边界场景（额外补充）
# ═══════════════════════════════════════════════════════════════

class TestNullBoundary:
    """None/null 处理边界测试"""

    def test_null_metrics_assessor(self, assessor):
        """None metrics — assessor 返回默认满分"""
        score = assessor.assess(None)
        assert score.overall == 1.0

    def test_null_get_history_n_assessor(self, assessor):
        """None n — get_history(None) 抛 TypeError（-None 非法）"""
        assessor.assess()
        with pytest.raises(TypeError):
            assessor.get_history(None)

    def test_null_get_history_n_calculator(self, calculator):
        """None n — calculator.get_history(None) 抛 TypeError"""
        calculator.calculate({})
        with pytest.raises(TypeError):
            calculator.get_history(None)
