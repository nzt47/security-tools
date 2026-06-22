"""HealthAssessor 测试"""
from agent.health.assessor import HealthAssessor

class TestHealthAssessor:
    def setup_method(self):
        self.a = HealthAssessor()

    def test_good_health(self):
        s = self.a.assess({"avg_response_ms": 500, "error_rate": 0.01})
        assert s.overall > 0.8

    def test_poor_health(self):
        s = self.a.assess({"avg_response_ms": 15000, "error_rate": 0.3})
        assert s.overall < 0.6

    def test_history(self):
        for i in range(5):
            self.a.assess({"avg_response_ms": 1000, "error_rate": 0.05})
        assert len(self.a.get_history(3)) == 3
