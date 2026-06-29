"""HealthAssessor 健康度评估测试"""
from agent.health.assessor import HealthAssessor, HealthScore


class TestHealthAssessor:
    """健康度评估器测试"""

    def setup_method(self):
        self.assessor = HealthAssessor()

    def test_assess_default(self):
        score = self.assessor.assess()
        assert score.overall == 1.0
        assert score.issues == []

    def test_assess_fast_response(self):
        score = self.assessor.assess({"avg_response_ms": 100, "error_rate": 0})
        assert score.dimensions["response_time"] == 1.0
        assert score.overall >= 0.9

    def test_assess_slow_response(self):
        score = self.assessor.assess({"avg_response_ms": 6000, "error_rate": 0})
        assert score.dimensions["response_time"] == 0.6

    def test_assess_very_slow_response(self):
        score = self.assessor.assess({"avg_response_ms": 15000, "error_rate": 0})
        assert score.dimensions["response_time"] == 0.3
        assert any("响应时间" in i for i in score.issues)

    def test_assess_high_error_rate(self):
        score = self.assessor.assess({"avg_response_ms": 100, "error_rate": 0.3})
        assert score.dimensions["error_rate"] == 0.2
        assert any("错误率" in i for i in score.issues)

    def test_assess_moderate_error_rate(self):
        score = self.assessor.assess({"avg_response_ms": 100, "error_rate": 0.15})
        assert score.dimensions["error_rate"] == 0.6

    def test_assess_low_error_rate(self):
        score = self.assessor.assess({"avg_response_ms": 100, "error_rate": 0.05})
        assert score.dimensions["error_rate"] == 1.0

    def test_overall_average(self):
        score = self.assessor.assess({"avg_response_ms": 6000, "error_rate": 0.15})
        expected = (0.6 + 0.6 + 1.0) / 3
        assert abs(score.overall - expected) < 0.01

    def test_history(self):
        self.assessor.assess()
        self.assessor.assess()
        history = self.assessor.get_history(5)
        assert len(history) == 2

    def test_history_limit(self):
        for i in range(150):
            self.assessor.assess()
        assert len(self.assessor.get_history()) <= 100

    def test_get_history_n(self):
        for i in range(10):
            self.assessor.assess()
        assert len(self.assessor.get_history(3)) == 3


class TestHealthScore:
    """HealthScore 数据类测试"""

    def test_create_default(self):
        s = HealthScore()
        assert s.overall == 1.0
        assert "response_time" in s.dimensions
        assert s.issues == []

    def test_has_timestamp(self):
        s = HealthScore()
        assert s.timestamp is not None
