"""Log System 分析层 — 规则引擎测试"""
import pytest
from agent.log_system.analyzer import Rule, ThresholdRule
from agent.log_system.models import LogStats


class TestRule:
    """规则基类测试"""

    def test_create(self):
        rule = Rule(name="test_rule", description="测试规则", severity="warning")
        assert rule.name == "test_rule"
        assert rule.description == "测试规则"
        assert rule.severity == "warning"
        assert rule.hit_count == 0

    def test_evaluate_not_implemented(self):
        rule = Rule(name="base")
        with pytest.raises(NotImplementedError):
            rule.evaluate(None, {})


class TestThresholdRule:
    """阈值规则测试"""

    def setup_method(self):
        self.stats = LogStats(total_count=100)

    def test_no_match_below_threshold(self):
        rule = ThresholdRule("error_rate_high", "error_rate", 0.1, operator="gt")
        context = {"error_rate": 0.05}
        result = rule.evaluate(self.stats, context)
        assert result is None
        assert rule.hit_count == 0

    def test_match_above_threshold(self):
        rule = ThresholdRule("error_rate_high", "error_rate", 0.1, operator="gt")
        context = {"error_rate": 0.2}
        result = rule.evaluate(self.stats, context)
        assert result is not None
        assert result["rule"] == "error_rate_high"
        assert rule.hit_count == 1

    def test_match_lt_operator(self):
        rule = ThresholdRule("low_count", "count", 50, operator="lt")
        context = {"count": 10}
        result = rule.evaluate(self.stats, context)
        assert result is not None

    def test_no_match_lt_operator(self):
        rule = ThresholdRule("low_count", "count", 50, operator="lt")
        context = {"count": 100}
        result = rule.evaluate(self.stats, context)
        assert result is None

    def test_match_gte_operator(self):
        rule = ThresholdRule("min_requests", "requests", 10, operator="gte")
        context = {"requests": 10}
        result = rule.evaluate(self.stats, context)
        assert result is not None

    def test_match_lte_operator(self):
        rule = ThresholdRule("max_requests", "requests", 100, operator="lte")
        context = {"requests": 100}
        result = rule.evaluate(self.stats, context)
        assert result is not None

    def test_hit_count_tracking(self):
        rule = ThresholdRule("test", "x", 0.5, operator="gt")
        for val in [0.6, 0.7, 0.3]:  # 2 hits
            rule.evaluate(self.stats, {"x": val})
        assert rule.hit_count == 2
