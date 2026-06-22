"""ModelRouter 测试"""
from agent.model_router.router import ModelRouter
from agent.model_router.cost_tracker import CostTracker

class TestModelRouter:
    def setup_method(self):
        self.router = ModelRouter()

    def test_simple_uses_small_model(self):
        m = self.router.route("chat", "hello", 0)
        assert m in ("gpt-3.5-turbo", "gpt-4o-mini")

    def test_complex_uses_large_model(self):
        m = self.router.route("chat", "帮我分析这段代码的性能", 0)
        assert m == "gpt-4"

class TestCostTracker:
    def test_record_and_summary(self):
        t = CostTracker(log_path="./test_cost_log.jsonl")
        t.record("gpt-4", 100, 50, 500, "test")
        s = t.get_summary()
        assert s["total_calls"] >= 1
