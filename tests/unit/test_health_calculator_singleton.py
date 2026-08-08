"""health_score._default_calculator 单例迁移单元测试

覆盖：
- 单例行为：唯一性、注册、工厂 config 通道（weights）、reset/GC/幂等
- 计算逻辑（重点）：全优/全差对比、error_rate 分档、便捷函数走单例、报告结构
- 边界条件（重点）：空 metrics、阈值分档边界、缺失指标默认值、全 0 权重（total_weight=0）、极端值、历史上限
- 并发首次初始化、fallback 行为
"""
import gc
import threading
import weakref

import pytest

import agent.health.health_score as module
from agent.health.health_score import (
    HealthDimension,
    HealthLevel,
    HealthReport,
    HealthScoreCalculator,
    calculate_health_score,
    get_health_calculator,
)
from agent.utils.singleton_manager import get_singleton, is_initialized


GOOD_METRICS = {
    "error_rate": 0, "crash_count": 0, "retry_count": 0,
    "p99_latency": 0.05, "p95_latency": 0.03, "throughput": 100,
    "cpu_usage": 0.1, "memory_usage": 0.1,
    "schema_pass_rate": 1.0, "critic_score": 95, "task_success_rate": 1.0,
    "token_usage": 0.1, "avg_retries": 0.1, "cache_hit_rate": 0.9,
    "uptime": 1.0, "dependency_health": 1.0,
    "security_alerts": 0, "auth_fail_rate": 0, "anomaly_access": 0,
}

BAD_METRICS = {
    "error_rate": 0.5, "crash_count": 50, "retry_count": 50,
    "p99_latency": 10, "p95_latency": 8, "throughput": 0.1,
    "cpu_usage": 0.95, "memory_usage": 0.95,
    "schema_pass_rate": 0.1, "critic_score": 10, "task_success_rate": 0.1,
    "token_usage": 1.0, "avg_retries": 10, "cache_hit_rate": 0,
    "uptime": 0.5, "dependency_health": 0.1,
    "security_alerts": 100, "auth_fail_rate": 0.5, "anomaly_access": 100,
}


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后重置单例，保证测试隔离"""
    module.reset_health_calculator()
    yield
    module.reset_health_calculator()


class TestHealthCalculatorSingleton:
    """单例行为测试"""

    def test_get_health_calculator_returns_same_instance(self):
        a = get_health_calculator()
        b = get_health_calculator()
        assert a is b

    def test_registers_in_singleton_manager(self):
        get_health_calculator()
        assert is_initialized("health_score_calculator")

    def test_singleton_manager_channel_returns_same_instance(self):
        c = get_health_calculator()
        assert get_singleton("health_score_calculator") is c

    def test_factory_unpacks_config_channel(self):
        """工厂：dict 通道含 weights 键时解包"""
        weights = {"stability": 1.0}
        calc = module._create_health_calculator({"weights": weights})
        assert calc.weights["stability"] == 1.0

    def test_factory_ignores_plain_dict(self):
        """工厂：非通道 dict 用默认权重，不误解包"""
        calc = module._create_health_calculator({"some_key": 1})
        assert calc.weights == HealthScoreCalculator.DEFAULT_WEIGHTS

    def test_factory_default_when_none(self):
        calc = module._create_health_calculator(None)
        assert calc.weights == HealthScoreCalculator.DEFAULT_WEIGHTS

    def test_reset_returns_new_instance(self):
        first = get_health_calculator()
        module.reset_health_calculator()
        second = get_health_calculator()
        assert first is not second

    def test_reset_releases_instance_for_gc(self):
        ref = weakref.ref(get_health_calculator())
        module.reset_health_calculator()
        gc.collect()
        assert ref() is None

    def test_reset_idempotent_when_not_initialized(self):
        module.reset_health_calculator()
        module.reset_health_calculator()


class TestCalculationLogic:
    """计算逻辑测试（重点）"""

    def test_good_metrics_high_score_excellent(self):
        """理想指标 → 高分且 level 为 excellent"""
        report = get_health_calculator().calculate(GOOD_METRICS)
        assert report.overall_score >= 95
        assert report.level == HealthLevel.EXCELLENT.value

    def test_bad_metrics_lower_score(self):
        """差指标分数明显低于理想指标"""
        good = get_health_calculator().calculate(GOOD_METRICS)
        bad = get_health_calculator().calculate(BAD_METRICS)
        assert bad.overall_score < good.overall_score
        assert bad.overall_score < 60

    def test_custom_weights_affect_score(self):
        """自定义权重改变加权结果（稳定性权重拉高时，稳定性好的场景得分更高）"""
        report = get_health_calculator().calculate(GOOD_METRICS)
        heavy_stability = HealthScoreCalculator(
            weights={"stability": 1.0, "performance": 0, "quality": 0,
                     "efficiency": 0, "availability": 0, "security": 0}
        )
        report2 = heavy_stability.calculate(GOOD_METRICS)
        assert report2.overall_score >= report.overall_score

    def test_calculate_health_score_module_function(self):
        """便捷函数 calculate_health_score 走单例"""
        report = calculate_health_score(GOOD_METRICS)
        assert isinstance(report, HealthReport)

    def test_report_contains_all_dimensions(self):
        """报告包含六大维度"""
        report = get_health_calculator().calculate(GOOD_METRICS)
        assert set(report.dimensions.keys()) == {
            HealthDimension.STABILITY.value,
            HealthDimension.PERFORMANCE.value,
            HealthDimension.QUALITY.value,
            HealthDimension.EFFICIENCY.value,
            HealthDimension.AVAILABILITY.value,
            HealthDimension.SECURITY.value,
        }

    def test_report_has_summary_and_recommendations(self):
        """报告包含 summary 与 recommendations"""
        report = get_health_calculator().calculate(GOOD_METRICS)
        assert report.summary
        assert isinstance(report.recommendations, list)

    def test_error_rate_tier_scoring(self):
        """error_rate 分档：0.01 满分档，0.05 触发降分与告警"""
        calc = get_health_calculator()
        tier_100 = calc.calculate({"error_rate": 0.01}).dimensions["stability"].score
        tier_70 = calc.calculate({"error_rate": 0.05}).dimensions["stability"].score
        assert tier_100 > tier_70
        # 0.05 档应产生"错误率偏高"issue
        dim = calc.calculate({"error_rate": 0.05}).dimensions["stability"]
        assert any("错误率" in issue for issue in dim.issues)


class TestBoundaryConditions:
    """边界条件测试（重点）"""

    def test_empty_metrics_does_not_crash(self):
        """空 metrics 不崩溃，返回有效报告"""
        report = get_health_calculator().calculate({})
        assert isinstance(report, HealthReport)
        assert 0 <= report.overall_score <= 100

    def test_missing_metrics_use_defaults(self):
        """缺失指标用默认值（不抛 KeyError）"""
        report = get_health_calculator().calculate({"error_rate": 0.0})
        assert 0 <= report.overall_score <= 100

    def test_threshold_boundary_error_rate(self):
        """error_rate 阈值边界：0.01 与 0.0101 分属不同档"""
        calc = get_health_calculator()
        at = calc.calculate({"error_rate": 0.01}).dimensions["stability"].score
        just_above = calc.calculate({"error_rate": 0.0101}).dimensions["stability"].score
        assert at > just_above

    def test_zero_weights_total_weight_zero(self):
        """权重全 0 → total_weight=0 → overall_score 为 0（边界防护）"""
        zero_weights = {d.value: 0.0 for d in HealthDimension}
        calc = HealthScoreCalculator(weights=zero_weights)
        report = calc.calculate(GOOD_METRICS)
        assert report.overall_score == 0

    def test_extreme_values_do_not_crash(self):
        """极端大/小值不崩溃且分数在 [0, 100]"""
        extreme = {
            "error_rate": 10.0, "crash_count": 10**9, "retry_count": 10**9,
            "p99_latency": 10**9, "p95_latency": 10**9, "throughput": -1,
            "cpu_usage": 10**9, "memory_usage": 10**9,
            "schema_pass_rate": -1, "critic_score": -1, "task_success_rate": -1,
            "token_usage": 10**9, "avg_retries": 10**9, "cache_hit_rate": -1,
            "uptime": -1, "dependency_health": -1,
            "security_alerts": 10**9, "auth_fail_rate": 10**9, "anomaly_access": 10**9,
        }
        report = get_health_calculator().calculate(extreme)
        assert 0 <= report.overall_score <= 100

    def test_history_bounded(self):
        """历史记录有上限（_max_history=1000）"""
        calc = get_health_calculator()
        for i in range(1050):
            calc.calculate({"error_rate": i / 10000})
        assert len(calc._history) <= calc._max_history


class TestHealthCalculatorConcurrency:
    """并发场景测试"""

    def test_concurrent_first_get_initializes_once(self):
        """多线程并发首次 get 只构造一个实例（双检锁）"""
        orig_cls = module.HealthScoreCalculator
        created = []

        class CountingCalculator(orig_cls):
            def __init__(self, weights=None):
                created.append(1)
                super().__init__(weights)

        module.HealthScoreCalculator = CountingCalculator
        try:
            results = []
            errors = []
            barrier = threading.Barrier(8)

            def worker():
                barrier.wait()
                try:
                    results.append(get_health_calculator())
                except Exception as e:  # pragma: no cover
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors
            assert len(created) == 1, f"应只构造一次，实际 {len(created)} 次"
            assert all(r is results[0] for r in results)
        finally:
            module.HealthScoreCalculator = orig_cls

    def test_concurrent_get_after_init_returns_same_instance(self):
        get_health_calculator()
        instances = []

        def worker():
            instances.append(get_health_calculator())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(i is instances[0] for i in instances)


class TestHealthCalculatorFallback:
    """SingletonManager 不可用时的 fallback 行为"""

    def test_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        a = get_health_calculator()
        b = get_health_calculator()
        assert a is b

    def test_fallback_reset_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_health_calculator()
        module.reset_health_calculator()
        second = get_health_calculator()
        assert first is not second

    def test_fallback_calculate_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        report = calculate_health_score(GOOD_METRICS)
        assert isinstance(report, HealthReport)
