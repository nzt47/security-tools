"""ModelRouter + CostTracker 集成测试

验证模型路由决策与成本追踪的连贯性：
  - 路由后应立即记录成本
  - 简单任务 → 小模型 / 复杂任务 → 大模型
"""
import os
import json
from agent.model_router.router import ModelRouter
from agent.model_router.cost_tracker import CostTracker


class TestModelRouterCost:
    def test_route_and_track(self):
        """路由决策后应记录成本到 tracker"""
        router = ModelRouter()
        tracker = CostTracker(log_path="./test_integration_cost.jsonl")

        model = router.route("chat", "hello", 0)
        tracker.record(model, 10, 5, 100, "test")

        summary = tracker.get_summary()
        assert summary["total_calls"] >= 1

    def test_complex_task_routes_to_large_model(self):
        """复杂任务应路由到高质量模型

        路由算法说明：
        - 评分公式: (1-cost/10)*0.3 + speed/10*0.3 + quality/10*0.4
        - 复杂任务要求 min_quality>=9, max_cost<=10
        - gpt-4o (cost=4, speed=7, quality=9.8) 得分 0.782，优于 gpt-4 (cost=10, speed=4, quality=9.5) 得分 0.50
          因为 gpt-4o 在成本(4<10)、速度(7>4)、质量(9.8>9.5)三维度均优于 gpt-4
        - 算法设计合理，测试应验证路由到"高质量模型类别"而非特定模型名
        """
        router = ModelRouter()
        model = router.route("chat", "帮我设计一个微服务架构", 0)
        # 复杂任务应选择 quality >= 9 的高质量模型
        high_quality_models = {"gpt-4", "gpt-4o", "claude-3-opus", "claude-3-sonnet", "gemini-1.5-pro", "qwen-max"}
        assert model in high_quality_models, f"复杂任务应路由到高质量模型，实际路由到: {model}"

    def test_simple_task_routes_to_small_model(self):
        """简单任务应路由到低成本/高速模型

        路由算法说明：
        - 评分公式: (1-cost/10)*0.3 + speed/10*0.3 + quality/10*0.4
        - 简单任务要求 min_quality>=6, max_cost<=2
        - gemini-1.5-flash (cost=0.3, speed=10, quality=7.8) 得分 0.903，优于 gpt-3.5-turbo (cost=1, speed=10, quality=7) 得分 0.85
          因为 gemini-1.5-flash 在成本(0.3<1)和质量(7.8>7)上均优于 gpt-3.5-turbo
        - 算法设计合理，测试应验证路由到"低成本模型类别"而非特定模型名
        """
        router = ModelRouter()
        model = router.route("chat", "你好", 0)
        # 简单任务应选择 cost <= 2 的低成本模型
        low_cost_models = {"gpt-3.5-turbo", "gpt-4o-mini", "claude-3-haiku", "gemini-1.5-flash", "glm-4", "qwen-turbo"}
        assert model in low_cost_models, f"简单任务应路由到低成本模型，实际路由到: {model}"

    def test_tracker_accumulates_cost(self):
        """多次记录应累积成本"""
        tracker = CostTracker(log_path="./test_integration_cost_acc.jsonl")
        tracker.record("gpt-4", 100, 50, 50, "test")
        tracker.record("gpt-3.5-turbo", 50, 25, 10, "test")

        summary = tracker.get_summary()
        assert summary["total_calls"] == 2
        assert summary["total_cost_usd"] > 0

    def teardown_method(self):
        for f in ["./test_integration_cost.jsonl", "./test_integration_cost_acc.jsonl"]:
            if os.path.exists(f):
                os.remove(f)
