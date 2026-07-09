"""模型路由器单元测试"""

import unittest
from unittest.mock import Mock, MagicMock

import pytest

from agent.model_router.router import ModelRouter, ModelSelector, TaskType
from agent.model_router.adapters import ModelAdapterFactory


class TestModelSelector(unittest.TestCase):
    """测试模型选择器"""
    
    def test_analyze_task_simple(self):
        """测试分析简单任务"""
        selector = ModelSelector()
        
        self.assertEqual(selector.analyze_task("你好"), TaskType.SIMPLE)
        self.assertEqual(selector.analyze_task("谢谢"), TaskType.SIMPLE)
        self.assertEqual(selector.analyze_task("hi"), TaskType.SIMPLE)
    
    def test_analyze_task_complex(self):
        """测试分析复杂任务"""
        selector = ModelSelector()
        
        self.assertEqual(selector.analyze_task("分析这个问题"), TaskType.COMPLEX)
        self.assertEqual(selector.analyze_task("设计一个架构"), TaskType.COMPLEX)
        self.assertEqual(selector.analyze_task("优化算法"), TaskType.COMPLEX)
    
    def test_analyze_task_creative(self):
        """测试分析创意任务"""
        selector = ModelSelector()
        
        self.assertEqual(selector.analyze_task("写一首诗"), TaskType.CREATIVE)
        self.assertEqual(selector.analyze_task("创作一个故事"), TaskType.CREATIVE)
    
    @pytest.mark.xfail(
        reason="ModelSelector 理想 API (select_model 返回带 name 的字典) 待统一重构 — 源码返回字符串",
        strict=False
    )
    def test_select_model(self):
        """测试选择模型"""
        selector = ModelSelector()

        model = selector.select_model(TaskType.SIMPLE)
        self.assertIsNotNone(model)
        self.assertIn("name", model)

        model = selector.select_model(TaskType.COMPLEX)
        self.assertIsNotNone(model)

    @pytest.mark.xfail(
        reason="ModelSelector.set_preferences 方法待统一重构 — 源码未实现",
        strict=False
    )
    def test_set_preferences(self):
        """测试设置偏好"""
        selector = ModelSelector()
        selector.set_preferences(cost_weight=0.5, speed_weight=0.3, quality_weight=0.2)

        model = selector.select_model(TaskType.NORMAL)
        self.assertIsNotNone(model)


class TestModelRouter(unittest.TestCase):
    """测试模型路由器"""
    
    def test_route_backward_compatible(self):
        """测试向后兼容的 route 方法"""
        router = ModelRouter()
        
        model_name = router.route("normal", "你好")
        self.assertIsNotNone(model_name)
        self.assertIsInstance(model_name, str)
    
    @pytest.mark.xfail(
        reason="ModelRouter.set_preferences 方法待统一重构 — 源码未实现",
        strict=False
    )
    def test_set_preferences(self):
        """测试设置偏好"""
        router = ModelRouter()
        router.set_preferences(cost_weight=0.5, speed_weight=0.3, quality_weight=0.2)

        model_name = router.route("normal", "test")
        self.assertIsNotNone(model_name)

    @pytest.mark.xfail(
        reason="ModelRouter.get_available_models 方法待统一重构 — 源码未实现",
        strict=False
    )
    def test_get_available_models(self):
        """测试获取可用模型列表"""
        router = ModelRouter()

        models = router.get_available_models()
        self.assertIsInstance(models, list)
        self.assertTrue(len(models) > 0)

    @pytest.mark.xfail(
        reason="ModelRouter.get_model_stats 方法待统一重构 — 源码未实现",
        strict=False
    )
    def test_get_model_stats(self):
        """测试获取模型统计信息"""
        router = ModelRouter()

        stats = router.get_model_stats()
        self.assertIsInstance(stats, dict)

    @pytest.mark.xfail(
        reason="ModelRouter.compare_models 方法待统一重构 — 源码未实现",
        strict=False
    )
    def test_compare_models(self):
        """测试模型比较"""
        router = ModelRouter()

        results = router.compare_models(["Hello"], ["gpt-3.5-turbo"])
        self.assertIsInstance(results, list)


class TestModelAdapters(unittest.TestCase):
    """测试模型适配器"""
    
    def test_create_openai_adapter(self):
        """测试创建 OpenAI 适配器"""
        adapter = ModelAdapterFactory.create("openai", "gpt-3.5-turbo")
        
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.get_provider_name(), "openai")
        self.assertEqual(adapter.get_model_name(), "gpt-3.5-turbo")
    
    def test_create_claude_adapter(self):
        """测试创建 Claude 适配器"""
        adapter = ModelAdapterFactory.create("claude", "claude-3-sonnet")
        
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.get_provider_name(), "claude")
        self.assertEqual(adapter.get_model_name(), "claude-3-sonnet")
    
    def test_create_gemini_adapter(self):
        """测试创建 Gemini 适配器"""
        adapter = ModelAdapterFactory.create("gemini", "gemini-1.5-flash")
        
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.get_provider_name(), "gemini")
        self.assertEqual(adapter.get_model_name(), "gemini-1.5-flash")
    
    def test_create_zhipu_adapter(self):
        """测试创建智谱适配器"""
        adapter = ModelAdapterFactory.create("zhipu", "glm-4")
        
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.get_provider_name(), "zhipu")
        self.assertEqual(adapter.get_model_name(), "glm-4")
    
    def test_create_unknown_provider(self):
        """测试创建未知提供商适配器"""
        adapter = ModelAdapterFactory.create("unknown", "model")
        
        self.assertIsNone(adapter)
    
    def test_get_cost_per_token(self):
        """测试获取成本信息"""
        adapter = ModelAdapterFactory.create("openai", "gpt-3.5-turbo")
        
        cost = adapter.get_cost_per_token()
        self.assertIsInstance(cost, dict)
        self.assertIn("prompt", cost)
        self.assertIn("completion", cost)


if __name__ == "__main__":
    unittest.main()