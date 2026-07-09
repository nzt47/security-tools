"""HealthScoreCalculator 集成测试

覆盖健康度评分计算器的六大维度评分逻辑、报告生成、历史趋势、
全局单例、快捷函数和安全调用包装器。

设计:
- 参数化测试覆盖各指标的区间边界（优秀/良好/一般/警告/危险）
- 完整场景测试验证综合评分与等级
- 历史趋势测试验证数据点累积与趋势判定
"""

import pytest

from agent.health.health_score import (
    HealthLevel,
    HealthDimension,
    DimensionScore,
    HealthReport,
    HealthScoreCalculator,
    get_health_calculator,
    calculate_health_score,
    _safe_call,
)


pytestmark = pytest.mark.integration


# ═══════════════════════════════════════════════════════════════
#  HealthLevel.from_score 区间测试
# ═══════════════════════════════════════════════════════════════

class TestHealthLevel:
    """健康等级枚举与分数转换"""

    @pytest.mark.parametrize("score,expected", [
        (100, HealthLevel.EXCELLENT),
        (90, HealthLevel.EXCELLENT),
        (89.9, HealthLevel.GOOD),
        (70, HealthLevel.GOOD),
        (69.9, HealthLevel.FAIR),
        (50, HealthLevel.FAIR),
        (49.9, HealthLevel.WARNING),
        (30, HealthLevel.WARNING),
        (29.9, HealthLevel.CRITICAL),
        (0, HealthLevel.CRITICAL),
    ])
    def test_from_score_boundaries(self, score, expected):
        assert HealthLevel.from_score(score) == expected

    def test_enum_values(self):
        assert HealthLevel.EXCELLENT.value == "excellent"
        assert HealthLevel.CRITICAL.value == "critical"

    def test_health_dimension_values(self):
        dims = [d.value for d in HealthDimension]
        assert "stability" in dims
        assert "security" in dims
        assert len(dims) == 6


# ═══════════════════════════════════════════════════════════════
#  DimensionScore / HealthReport 数据模型
# ═══════════════════════════════════════════════════════════════

class TestHealthReport:
    """报告数据模型与序列化"""

    def test_dimension_score_defaults(self):
        d = DimensionScore(name="test")
        assert d.score == 100.0
        assert d.weight == 1.0
        assert d.indicators == {}
        assert d.issues == []

    def test_health_report_to_dict(self):
        report = HealthReport(
            overall_score=85.5,
            level=HealthLevel.GOOD.value,
            dimensions={
                "stability": DimensionScore(
                    name="stability",
                    score=90.0,
                    weight=0.2,
                    indicators={"error_rate": 100.0},
                    issues=[],
                ),
            },
            summary=["系统良好"],
            recommendations=["继续保持"],
            critical_issues=[],
        )
        d = report.to_dict()
        assert d["overall_score"] == 85.5
        assert d["level"] == "good"
        assert "stability" in d["dimensions"]
        assert d["dimensions"]["stability"]["score"] == 90.0
        assert d["dimensions"]["stability"]["indicators"]["error_rate"] == 100.0
        assert d["summary"] == ["系统良好"]

    def test_health_report_to_dict_rounds_floats(self):
        report = HealthReport(
            overall_score=85.567,
            dimensions={
                "perf": DimensionScore(
                    name="perf",
                    score=72.346,
                    indicators={"cpu": 0.333},
                ),
            },
        )
        d = report.to_dict()
        assert d["overall_score"] == 85.57
        assert d["dimensions"]["perf"]["score"] == 72.35
        assert d["dimensions"]["perf"]["indicators"]["cpu"] == 0.33


# ═══════════════════════════════════════════════════════════════
#  稳定性维度
# ═══════════════════════════════════════════════════════════════

class TestStabilityDimension:
    """稳定性评分各指标区间"""

    @pytest.mark.parametrize("error_rate,expected_score,min_expected", [
        (0.005, 100, 90),    # <= 0.01
        (0.02, 90, 85),      # <= 0.03
        (0.04, 70, 65),      # <= 0.05, 有 issues
        (0.08, 50, 45),      # <= 0.10
        (0.15, 20, 15),      # > 0.10
    ])
    def test_error_rate_levels(self, error_rate, expected_score, min_expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_stability({"error_rate": error_rate})
        assert dim.indicators["error_rate"] == expected_score
        if error_rate > 0.03:
            assert any("错误率" in i for i in dim.issues)

    @pytest.mark.parametrize("crash_count,expected", [
        (0, 100),
        (1, 80),
        (3, 50),
        (5, 20),
    ])
    def test_crash_count_levels(self, crash_count, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_stability({"crash_count": crash_count})
        assert dim.indicators["crash_rate"] == expected

    @pytest.mark.parametrize("retry_count,total,expected_min", [
        (3, 100, 90),    # 3% <= 5%
        (8, 100, 80),    # 8% <= 10%
        (15, 100, 55),   # 15% <= 20%
        (30, 100, 35),   # 30% > 20%
    ])
    def test_retry_rate_levels(self, retry_count, total, expected_min):
        calc = HealthScoreCalculator()
        dim = calc._calc_stability({
            "retry_count": retry_count,
            "total_requests": total,
        })
        assert dim.indicators["retry_rate"] >= expected_min

    def test_error_spike_flag(self):
        calc = HealthScoreCalculator()
        dim_ok = calc._calc_stability({"error_spike": False})
        dim_bad = calc._calc_stability({"error_spike": True})
        assert dim_ok.indicators["error_spike"] == 100
        assert dim_bad.indicators["error_spike"] == 30
        assert any("突增" in i for i in dim_bad.issues)

    def test_zero_total_requests_no_division_error(self):
        calc = HealthScoreCalculator()
        dim = calc._calc_stability({"retry_count": 5, "total_requests": 0})
        assert "retry_rate" in dim.indicators


# ═══════════════════════════════════════════════════════════════
#  性能维度
# ═══════════════════════════════════════════════════════════════

class TestPerformanceDimension:
    """性能评分各指标区间"""

    @pytest.mark.parametrize("p99,expected", [
        (0.5, 100),
        (1.5, 85),
        (2.5, 70),
        (4.0, 50),
        (6.0, 30),
    ])
    def test_p99_latency_levels(self, p99, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_performance({"p99_latency": p99})
        assert dim.indicators["p99_latency"] == expected

    @pytest.mark.parametrize("p95,expected", [
        (0.3, 100),
        (0.8, 90),
        (1.5, 70),
        (3.0, 50),
    ])
    def test_p95_latency_levels(self, p95, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_performance({"p95_latency": p95})
        assert dim.indicators["p95_latency"] == expected

    @pytest.mark.parametrize("throughput,expected", [
        (60, 100),
        (25, 85),
        (12, 70),
        (6, 50),
        (3, 30),
    ])
    def test_throughput_levels(self, throughput, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_performance({"throughput": throughput})
        assert dim.indicators["throughput"] == expected

    @pytest.mark.parametrize("cpu,expected", [
        (0.3, 100),
        (0.6, 85),
        (0.8, 60),
        (0.9, 30),
    ])
    def test_cpu_usage_levels(self, cpu, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_performance({"cpu_usage": cpu})
        assert dim.indicators["cpu_usage"] == expected

    @pytest.mark.parametrize("memory,expected", [
        (0.5, 100),
        (0.7, 80),
        (0.8, 50),
        (0.9, 20),
    ])
    def test_memory_usage_levels(self, memory, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_performance({"memory_usage": memory})
        assert dim.indicators["memory_usage"] == expected

    def test_latency_spike_flag(self):
        calc = HealthScoreCalculator()
        dim_bad = calc._calc_performance({"latency_spike": True})
        assert dim_bad.indicators["latency_spike"] == 40
        assert any("突增" in i for i in dim_bad.issues)


# ═══════════════════════════════════════════════════════════════
#  质量维度
# ═══════════════════════════════════════════════════════════════

class TestQualityDimension:
    """质量评分各指标区间"""

    @pytest.mark.parametrize("schema_pass,expected", [
        (0.999, 100),
        (0.96, 90),
        (0.91, 70),
        (0.85, 50),
        (0.70, 20),
    ])
    def test_schema_pass_rate_levels(self, schema_pass, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_quality({"schema_pass_rate": schema_pass})
        assert dim.indicators["schema_pass_rate"] == expected

    @pytest.mark.parametrize("critic,expected", [
        (95, 100),
        (85, 85),
        (75, 70),
        (65, 50),
        (50, 30),
    ])
    def test_critic_score_levels(self, critic, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_quality({"critic_score": critic})
        assert dim.indicators["critic_score"] == expected

    @pytest.mark.parametrize("task_rate,expected", [
        (0.96, 100),
        (0.87, 85),
        (0.72, 65),
        (0.60, 40),
    ])
    def test_task_success_rate_levels(self, task_rate, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_quality({"task_success_rate": task_rate})
        assert dim.indicators["task_success_rate"] == expected

    @pytest.mark.parametrize("tool_rate,expected", [
        (0.96, 100),
        (0.87, 80),
        (0.76, 60),
        (0.70, 35),
    ])
    def test_tool_success_rate_levels(self, tool_rate, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_quality({"tool_success_rate": tool_rate})
        assert dim.indicators["tool_success_rate"] == expected


# ═══════════════════════════════════════════════════════════════
#  效率维度
# ═══════════════════════════════════════════════════════════════

class TestEfficiencyDimension:
    """效率评分各指标区间"""

    @pytest.mark.parametrize("token_eff,expected", [
        (0.95, 100),
        (0.80, 85),
        (0.65, 65),
        (0.50, 40),
    ])
    def test_token_efficiency_levels(self, token_eff, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_efficiency({"token_efficiency": token_eff})
        assert dim.indicators["token_efficiency"] == expected

    @pytest.mark.parametrize("retries,expected", [
        (1.0, 100),
        (1.2, 85),
        (1.4, 65),
        (1.6, 40),
    ])
    def test_avg_retries_levels(self, retries, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_efficiency({"avg_retries": retries})
        assert dim.indicators["avg_retries"] == expected

    @pytest.mark.parametrize("cache_hit,expected", [
        (0.85, 100),
        (0.65, 85),
        (0.45, 65),
        (0.25, 45),
        (0.10, 25),
    ])
    def test_cache_hit_rate_levels(self, cache_hit, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_efficiency({"cache_hit_rate": cache_hit})
        assert dim.indicators["cache_hit_rate"] == expected

    @pytest.mark.parametrize("cost,expected", [
        (0.3, 100),
        (0.8, 85),
        (1.5, 65),
        (3.0, 40),
        (6.0, 20),
    ])
    def test_cost_per_task_levels(self, cost, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_efficiency({"cost_per_task": cost})
        assert dim.indicators["cost_efficiency"] == expected


# ═══════════════════════════════════════════════════════════════
#  可用性维度
# ═══════════════════════════════════════════════════════════════

class TestAvailabilityDimension:
    """可用性评分各指标区间"""

    @pytest.mark.parametrize("uptime,expected", [
        (0.9999, 100),
        (0.996, 95),
        (0.992, 85),
        (0.96, 65),
        (0.90, 30),
    ])
    def test_uptime_levels(self, uptime, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_availability({"uptime": uptime})
        assert dim.indicators["uptime"] == expected

    @pytest.mark.parametrize("dep,expected", [
        (0.97, 100),
        (0.87, 80),
        (0.72, 55),
        (0.60, 25),
    ])
    def test_dependency_health_levels(self, dep, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_availability({"dependency_health": dep})
        assert dim.indicators["dependency_health"] == expected

    @pytest.mark.parametrize("healthy,total,expected", [
        (10, 10, 100),   # 1.0 >= 1.0
        (9, 10, 80),     # 0.9 >= 0.9
        (8, 10, 55),     # 0.8 >= 0.75
        (7, 10, 25),     # 0.7 < 0.75
    ])
    def test_service_ratio_levels(self, healthy, total, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_availability({
            "healthy_services": healthy,
            "total_services": total,
        })
        assert dim.indicators["service_health"] == expected

    @pytest.mark.parametrize("recovery,expected", [
        (20, 100),
        (45, 85),
        (120, 60),
        (400, 30),
    ])
    def test_recovery_time_levels(self, recovery, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_availability({"avg_recovery_time": recovery})
        assert dim.indicators["recovery_time"] == expected

    def test_zero_total_services_no_error(self):
        calc = HealthScoreCalculator()
        dim = calc._calc_availability({
            "healthy_services": 0,
            "total_services": 0,
        })
        assert "service_health" in dim.indicators


# ═══════════════════════════════════════════════════════════════
#  安全性维度
# ═══════════════════════════════════════════════════════════════

class TestSecurityDimension:
    """安全性评分各指标区间"""

    @pytest.mark.parametrize("alerts,expected", [
        (0, 100),
        (1, 70),
        (4, 40),
        (8, 10),
    ])
    def test_security_alerts_levels(self, alerts, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_security({"security_alerts": alerts})
        assert dim.indicators["security_alerts"] == expected

    @pytest.mark.parametrize("auth_fail,expected", [
        (0.005, 100),
        (0.02, 80),
        (0.04, 50),
        (0.08, 20),
    ])
    def test_auth_fail_rate_levels(self, auth_fail, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_security({"auth_fail_rate": auth_fail})
        assert dim.indicators["auth_security"] == expected

    @pytest.mark.parametrize("anomaly,expected", [
        (0, 100),
        (2, 75),
        (7, 45),
        (15, 15),
    ])
    def test_anomaly_access_levels(self, anomaly, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_security({"anomaly_access": anomaly})
        assert dim.indicators["anomaly_access"] == expected

    @pytest.mark.parametrize("vuln,expected", [
        (0, 100),
        (1, 70),
        (4, 40),
        (8, 10),
    ])
    def test_vulnerability_count_levels(self, vuln, expected):
        calc = HealthScoreCalculator()
        dim = calc._calc_security({"vulnerability_count": vuln})
        assert dim.indicators["vulnerabilities"] == expected


# ═══════════════════════════════════════════════════════════════
#  综合计算场景
# ═══════════════════════════════════════════════════════════════

class TestCalculateScenarios:
    """综合健康度计算场景"""

    def test_excellent_system(self):
        """所有指标优秀 → EXCELLENT"""
        metrics = {
            "error_rate": 0.001,
            "crash_count": 0,
            "retry_count": 2,
            "total_requests": 100,
            "p99_latency": 0.5,
            "p95_latency": 0.3,
            "throughput": 60,
            "cpu_usage": 0.3,
            "memory_usage": 0.4,
            "schema_pass_rate": 0.999,
            "critic_score": 95,
            "task_success_rate": 0.96,
            "tool_success_rate": 0.96,
            "token_efficiency": 0.95,
            "avg_retries": 1.0,
            "cache_hit_rate": 0.85,
            "cost_per_task": 0.3,
            "uptime": 0.9999,
            "dependency_health": 0.97,
            "healthy_services": 10,
            "total_services": 10,
            "avg_recovery_time": 20,
            "security_alerts": 0,
            "auth_fail_rate": 0.005,
            "anomaly_access": 0,
            "vulnerability_count": 0,
        }
        calc = HealthScoreCalculator()
        report = calc.calculate(metrics)
        assert report.overall_score >= 90
        assert report.level == HealthLevel.EXCELLENT.value
        assert len(report.dimensions) == 6
        assert all(d.score >= 90 for d in report.dimensions.values())

    def test_critical_system(self):
        """所有指标危险 → CRITICAL"""
        metrics = {
            "error_rate": 0.15,
            "crash_count": 5,
            "retry_count": 30,
            "total_requests": 100,
            "error_spike": True,
            "p99_latency": 6.0,
            "p95_latency": 3.0,
            "throughput": 3,
            "cpu_usage": 0.9,
            "memory_usage": 0.9,
            "latency_spike": True,
            "schema_pass_rate": 0.70,
            "critic_score": 50,
            "task_success_rate": 0.60,
            "tool_success_rate": 0.70,
            "token_efficiency": 0.50,
            "avg_retries": 1.6,
            "cache_hit_rate": 0.10,
            "cost_per_task": 6.0,
            "uptime": 0.90,
            "dependency_health": 0.60,
            "healthy_services": 5,
            "total_services": 10,
            "avg_recovery_time": 400,
            "security_alerts": 8,
            "auth_fail_rate": 0.08,
            "anomaly_access": 15,
            "vulnerability_count": 8,
        }
        calc = HealthScoreCalculator()
        report = calc.calculate(metrics)
        assert report.overall_score < 30
        assert report.level == HealthLevel.CRITICAL.value
        assert len(report.critical_issues) > 0
        assert len(report.recommendations) > 0

    def test_empty_metrics_uses_defaults(self):
        """空 metrics 使用默认值 → 分数较高"""
        calc = HealthScoreCalculator()
        report = calc.calculate({})
        assert report.overall_score > 50
        assert len(report.dimensions) == 6

    def test_custom_weights(self):
        """自定义权重影响综合得分"""
        metrics = {
            "error_rate": 0.15,
            "crash_count": 5,
            "security_alerts": 0,
            "vulnerability_count": 0,
        }
        default_calc = HealthScoreCalculator()
        security_heavy = HealthScoreCalculator(weights={
            HealthDimension.STABILITY.value: 0.05,
            HealthDimension.PERFORMANCE.value: 0.05,
            HealthDimension.QUALITY.value: 0.05,
            HealthDimension.EFFICIENCY.value: 0.05,
            HealthDimension.AVAILABILITY.value: 0.05,
            HealthDimension.SECURITY.value: 0.75,
        })
        default_report = default_calc.calculate(metrics)
        security_report = security_heavy.calculate(metrics)
        # 安全维度满分，提高权重后综合分应更高
        assert security_report.overall_score > default_report.overall_score

    def test_report_dimensions_keys(self):
        calc = HealthScoreCalculator()
        report = calc.calculate({})
        expected_keys = {d.value for d in HealthDimension}
        assert set(report.dimensions.keys()) == expected_keys

    def test_summary_contains_level_info(self):
        calc = HealthScoreCalculator()
        report = calc.calculate({"error_rate": 0.15, "crash_count": 5})
        assert len(report.summary) >= 1
        assert any("最佳维度" in s for s in report.summary)

    def test_recommendations_generated_for_low_scores(self):
        calc = HealthScoreCalculator()
        report = calc.calculate({
            "error_rate": 0.15,
            "crash_count": 5,
            "p99_latency": 6.0,
            "memory_usage": 0.9,
            "schema_pass_rate": 0.70,
            "cache_hit_rate": 0.10,
            "cost_per_task": 6.0,
            "uptime": 0.90,
            "security_alerts": 8,
        })
        assert len(report.recommendations) > 0
        assert any("错误率" in r or "崩溃" in r for r in report.recommendations)

    def test_recommendations_empty_when_healthy(self):
        calc = HealthScoreCalculator()
        report = calc.calculate({
            "error_rate": 0.001,
            "crash_count": 0,
            "p99_latency": 0.5,
            "schema_pass_rate": 0.999,
            "security_alerts": 0,
            "uptime": 0.9999,
        })
        assert "系统运行良好" in report.recommendations[0]

    def test_warning_level_summary(self):
        """覆盖 WARNING 级别 (30-49) 的摘要生成"""
        calc = HealthScoreCalculator()
        report = HealthReport(
            overall_score=40,
            level=HealthLevel.WARNING.value,
            dimensions={
                d.value: DimensionScore(name=d.value, score=40)
                for d in HealthDimension
            },
        )
        summary = calc._generate_summary(report)
        assert any("警告" in s for s in summary)


# ═══════════════════════════════════════════════════════════════
#  历史与趋势
# ═══════════════════════════════════════════════════════════════

class TestHistoryAndTrend:
    """历史记录与趋势分析"""

    def test_history_accumulates(self):
        calc = HealthScoreCalculator()
        calc.calculate({})
        calc.calculate({})
        calc.calculate({})
        history = calc.get_history()
        assert len(history) == 3

    def test_history_limit(self):
        calc = HealthScoreCalculator(max_history_override=5) if hasattr(
            HealthScoreCalculator, "max_history_override") else None
        if calc is None:
            # 直接操作 _max_history
            calc = HealthScoreCalculator()
            calc._max_history = 5
        for _ in range(10):
            calc.calculate({})
        assert len(calc._history) == 5

    def test_get_history_n(self):
        calc = HealthScoreCalculator()
        for _ in range(10):
            calc.calculate({})
        recent = calc.get_history(3)
        assert len(recent) == 3

    def test_trend_insufficient_data(self):
        calc = HealthScoreCalculator()
        trend = calc.get_trend()
        assert trend["trend"] == "insufficient_data"

    def test_trend_improving(self):
        calc = HealthScoreCalculator()
        calc.calculate({"error_rate": 0.15, "crash_count": 5})
        calc.calculate({"error_rate": 0.001, "crash_count": 0})
        trend = calc.get_trend()
        assert trend["trend"] == "improving"
        assert trend["change"] > 5

    def test_trend_deteriorating(self):
        calc = HealthScoreCalculator()
        calc.calculate({"error_rate": 0.001, "crash_count": 0})
        calc.calculate({"error_rate": 0.15, "crash_count": 5})
        trend = calc.get_trend()
        assert trend["trend"] == "deteriorating"
        assert trend["change"] < -5

    def test_trend_stable(self):
        calc = HealthScoreCalculator()
        calc.calculate({})
        calc.calculate({})
        trend = calc.get_trend()
        assert trend["trend"] == "stable"
        assert abs(trend["change"]) <= 5

    def test_trend_data_points(self):
        calc = HealthScoreCalculator()
        for _ in range(5):
            calc.calculate({})
        trend = calc.get_trend(3)
        assert trend["data_points"] == 3
        assert "avg_score" in trend
        assert "min_score" in trend
        assert "max_score" in trend

    def test_trend_single_data_point(self):
        """get_trend(n=1) 时只有 1 个数据点 → stable"""
        calc = HealthScoreCalculator()
        calc.calculate({})
        calc.calculate({})
        trend = calc.get_trend(n=1)
        assert trend["trend"] == "stable"
        assert trend["change"] == 0


# ═══════════════════════════════════════════════════════════════
#  全局单例与快捷函数
# ═══════════════════════════════════════════════════════════════

class TestGlobalAPI:
    """全局单例与快捷函数"""

    def test_get_health_calculator_singleton(self):
        c1 = get_health_calculator()
        c2 = get_health_calculator()
        assert c1 is c2

    def test_calculate_health_score_returns_report(self):
        report = calculate_health_score({"error_rate": 0.001})
        assert isinstance(report, HealthReport)
        assert report.overall_score > 0


# ═══════════════════════════════════════════════════════════════
#  _safe_call 包装器
# ═══════════════════════════════════════════════════════════════

class TestSafeCall:
    """安全调用包装器"""

    def test_safe_call_success(self):
        result = _safe_call(lambda x: x * 2, 5, action="test_double")
        assert result == 10

    def test_safe_call_reraises(self):
        with pytest.raises(ValueError, match="boom"):
            _safe_call(self._raise_value_error, action="test_raise")

    def test_safe_call_passes_kwargs(self):
        def adder(a, b=0):
            return a + b
        assert _safe_call(adder, 3, b=4, action="test_add") == 7

    @staticmethod
    def _raise_value_error():
        raise ValueError("boom")
