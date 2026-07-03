"""health 模块补全测试

覆盖：
- observability.py: trackEvent / _emit_structured_log / _trace_id（之前 0% 覆盖）
- dashboard.py: Flask Blueprint 路由（之前 0% 覆盖）
- health_score.py: HealthScoreCalculator / HealthLevel / HealthReport 补充单元测试

状态同步机制：caplog 捕获日志，Flask test client 隔离 HTTP 请求。
"""
import json
from unittest import mock

import pytest

# ── observability 测试 ──

from agent.health import observability as health_obs


class TestHealthObservability:
    """health.observability 埋点模块"""

    def test_trace_id_length(self):
        tid = health_obs._trace_id()
        assert isinstance(tid, str)
        assert len(tid) == 16

    def test_trace_id_unique(self):
        ids = {health_obs._trace_id() for _ in range(100)}
        assert len(ids) == 100

    def test_emit_structured_log_basic(self, caplog):
        with caplog.at_level("INFO", logger="agent.health"):
            health_obs._emit_structured_log("test_action", duration_ms=42.5)
        assert any("test_action" in r.message for r in caplog.records)

    def test_emit_structured_log_with_trace_id(self, caplog):
        with caplog.at_level("INFO", logger="agent.health"):
            health_obs._emit_structured_log("act", trace_id="custom-tid", duration_ms=10)
        assert any("custom-tid" in r.message for r in caplog.records)

    def test_emit_structured_log_level_warning(self, caplog):
        with caplog.at_level("WARNING", logger="agent.health"):
            health_obs._emit_structured_log("warn_act", level="warning")
        assert any("warn_act" in r.message for r in caplog.records)

    def test_emit_structured_log_extra_payload(self, caplog):
        with caplog.at_level("INFO", logger="agent.health"):
            health_obs._emit_structured_log("act", user_id="u123", action_type="click")
        msgs = [r.message for r in caplog.records]
        assert any("u123" in m for m in msgs)
        assert any("click" in m for m in msgs)

    def test_track_event_basic(self, caplog):
        with caplog.at_level("INFO", logger="agent.health"):
            health_obs.trackEvent("health_check", {"score": 95})
        assert any("track.health_check" in r.message for r in caplog.records)

    def test_track_event_no_payload(self, caplog):
        with caplog.at_level("INFO", logger="agent.health"):
            health_obs.trackEvent("simple_event")
        assert any("track.simple_event" in r.message for r in caplog.records)

    def test_track_event_reserved_keys_filtered(self, caplog):
        with caplog.at_level("INFO", logger="agent.health"):
            health_obs.trackEvent("evt", {
                "action": "should_be_filtered",
                "trace_id": "should_be_filtered",
                "custom_field": "kept",
            })
        msgs = " ".join(r.message for r in caplog.records)
        assert "kept" in msgs
        assert "should_be_filtered" not in msgs

    def test_track_event_does_not_raise(self):
        """埋点失败不影响主流程"""
        with mock.patch.object(health_obs, "_emit_structured_log", side_effect=Exception("boom")):
            health_obs.trackEvent("fail_test")  # 不应抛异常


# ── dashboard 测试 ──

from agent.health.dashboard import health_bp


class TestHealthDashboard:
    """health.dashboard Flask Blueprint"""

    @pytest.fixture
    def app(self):
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(health_bp)
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_dashboard_returns_200(self, client):
        resp = client.get('/api/health/dashboard')
        assert resp.status_code == 200

    def test_dashboard_returns_json(self, client):
        resp = client.get('/api/health/dashboard')
        data = resp.get_json()
        assert isinstance(data, dict)

    def test_dashboard_has_overall_health(self, client):
        resp = client.get('/api/health/dashboard')
        data = resp.get_json()
        assert "overall_health" in data

    def test_dashboard_has_dimensions(self, client):
        resp = client.get('/api/health/dashboard')
        data = resp.get_json()
        assert "dimensions" in data

    def test_dashboard_has_issues(self, client):
        resp = client.get('/api/health/dashboard')
        data = resp.get_json()
        assert "issues" in data

    def test_dashboard_has_history(self, client):
        resp = client.get('/api/health/dashboard')
        data = resp.get_json()
        assert "history" in data
        assert isinstance(data["history"], list)

    def test_dashboard_history_max_10(self, client):
        """dashboard 返回最多 10 条历史"""
        resp = client.get('/api/health/dashboard')
        data = resp.get_json()
        assert len(data["history"]) <= 10


# ── health_score 补充测试 ──

from agent.health.health_score import (
    HealthScoreCalculator,
    HealthLevel,
    HealthDimension,
    DimensionScore,
    HealthReport,
)


class TestHealthLevel:
    """HealthLevel 枚举"""

    def test_from_score_excellent(self):
        assert HealthLevel.from_score(95) == HealthLevel.EXCELLENT

    def test_from_score_good(self):
        assert HealthLevel.from_score(75) == HealthLevel.GOOD

    def test_from_score_fair(self):
        assert HealthLevel.from_score(55) == HealthLevel.FAIR

    def test_from_score_warning(self):
        assert HealthLevel.from_score(35) == HealthLevel.WARNING

    def test_from_score_critical(self):
        assert HealthLevel.from_score(20) == HealthLevel.CRITICAL

    def test_from_score_boundary_90(self):
        assert HealthLevel.from_score(90) == HealthLevel.EXCELLENT

    def test_from_score_boundary_70(self):
        assert HealthLevel.from_score(70) == HealthLevel.GOOD

    def test_from_score_boundary_50(self):
        assert HealthLevel.from_score(50) == HealthLevel.FAIR

    def test_from_score_boundary_30(self):
        assert HealthLevel.from_score(30) == HealthLevel.WARNING

    def test_from_score_zero(self):
        assert HealthLevel.from_score(0) == HealthLevel.CRITICAL


class TestDimensionScore:
    """DimensionScore 数据类"""

    def test_default_values(self):
        d = DimensionScore(name="test")
        assert d.score == 100.0
        assert d.weight == 1.0
        assert d.indicators == {}
        assert d.issues == []
        assert d.details == {}

    def test_custom_values(self):
        d = DimensionScore(name="x", score=50.0, weight=0.5)
        assert d.score == 50.0
        assert d.weight == 0.5

    def test_instances_are_independent(self):
        d1 = DimensionScore(name="a")
        d2 = DimensionScore(name="b")
        d1.indicators["k"] = 1
        assert "k" not in d2.indicators


class TestHealthReport:
    """HealthReport 数据类"""

    def test_default_values(self):
        r = HealthReport()
        assert r.overall_score == 100.0
        assert r.level == HealthLevel.EXCELLENT.value
        assert r.dimensions == {}
        assert r.summary == []

    def test_to_dict_structure(self):
        r = HealthReport()
        r.dimensions["test"] = DimensionScore(name="test", score=80.0)
        d = r.to_dict()
        assert "timestamp" in d
        assert "overall_score" in d
        assert "level" in d
        assert "dimensions" in d
        assert "test" in d["dimensions"]
        assert d["dimensions"]["test"]["score"] == 80.0

    def test_to_dict_rounds_scores(self):
        r = HealthReport()
        r.overall_score = 75.56789
        r.dimensions["x"] = DimensionScore(name="x", score=88.123)
        d = r.to_dict()
        assert d["overall_score"] == 75.57
        assert d["dimensions"]["x"]["score"] == 88.12

    def test_to_dict_with_float_indicators(self):
        r = HealthReport()
        dim = DimensionScore(name="x")
        dim.indicators = {"latency": 99.876, "count": 5}
        r.dimensions["x"] = dim
        d = r.to_dict()
        assert d["dimensions"]["x"]["indicators"]["latency"] == 99.88
        assert d["dimensions"]["x"]["indicators"]["count"] == 5


class TestHealthScoreCalculator:
    """HealthScoreCalculator 计算器"""

    @pytest.fixture
    def calc(self):
        return HealthScoreCalculator()

    def test_calculate_returns_report(self, calc):
        report = calc.calculate({})
        assert isinstance(report, HealthReport)

    def test_calculate_default_metrics(self, calc):
        """空 metrics 应返回优秀等级报告"""
        report = calc.calculate({})
        assert report.overall_score >= 80.0  # 各维度默认值较高
        assert report.level == HealthLevel.EXCELLENT.value

    def test_calculate_has_all_dimensions(self, calc):
        report = calc.calculate({})
        assert "stability" in report.dimensions
        assert "performance" in report.dimensions
        assert "quality" in report.dimensions
        assert "efficiency" in report.dimensions
        assert "availability" in report.dimensions
        assert "security" in report.dimensions

    def test_calculate_stability_low_error_rate(self, calc):
        report = calc.calculate({"error_rate": 0.005})
        dim = report.dimensions["stability"]
        assert dim.indicators["error_rate"] == 100

    def test_calculate_stability_high_error_rate(self, calc):
        report = calc.calculate({"error_rate": 0.15})
        dim = report.dimensions["stability"]
        assert dim.indicators["error_rate"] == 20
        assert any("错误率严重" in i for i in dim.issues)

    def test_calculate_stability_no_crashes(self, calc):
        report = calc.calculate({"crash_count": 0})
        dim = report.dimensions["stability"]
        assert dim.indicators["crash_rate"] == 100

    def test_calculate_stability_many_crashes(self, calc):
        report = calc.calculate({"crash_count": 5})
        dim = report.dimensions["stability"]
        assert dim.indicators["crash_rate"] == 20

    def test_calculate_stability_low_retry_rate(self, calc):
        report = calc.calculate({"retry_count": 2, "total_requests": 100})
        dim = report.dimensions["stability"]
        assert dim.indicators["retry_rate"] == 100

    def test_calculate_stability_error_spike(self, calc):
        report = calc.calculate({"error_spike": True})
        dim = report.dimensions["stability"]
        assert dim.indicators["error_spike"] == 30
        assert any("错误率突增" in i for i in dim.issues)

    def test_calculate_performance_fast_p99(self, calc):
        report = calc.calculate({"p99_latency": 0.5})
        dim = report.dimensions["performance"]
        assert dim.indicators["p99_latency"] == 100

    def test_calculate_performance_slow_p99(self, calc):
        report = calc.calculate({"p99_latency": 6.0})
        dim = report.dimensions["performance"]
        assert dim.indicators["p99_latency"] == 30

    def test_calculate_with_custom_weights(self):
        calc = HealthScoreCalculator(weights={
            "stability": 0.5, "performance": 0.1, "quality": 0.1,
            "efficiency": 0.1, "availability": 0.1, "security": 0.1,
        })
        report = calc.calculate({})
        assert report.overall_score >= 80.0  # 自定义权重下仍是优秀等级
        assert report.level == HealthLevel.EXCELLENT.value

    def test_calculate_history_tracking(self, calc):
        calc.calculate({})
        calc.calculate({})
        assert len(calc._history) == 2

    def test_calculate_history_max_limit(self):
        calc = HealthScoreCalculator()
        for _ in range(1100):
            calc.calculate({})
        assert len(calc._history) <= 1000

    def test_calculate_generates_summary(self, calc):
        report = calc.calculate({})
        assert isinstance(report.summary, list)

    def test_calculate_generates_recommendations(self, calc):
        report = calc.calculate({})
        assert isinstance(report.recommendations, list)

    def test_calculate_critical_issues_collected(self, calc):
        """严重告警应进入 critical_issues"""
        report = calc.calculate({"crash_count": 5})
        assert len(report.critical_issues) > 0
