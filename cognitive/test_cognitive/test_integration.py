# cognitive/test_cognitive/test_integration.py
"""端到端集成测试——模拟完整的数据流程"""
import pytest
from cognitive import PromptInjector, PromptConfig


class TestIntegration:
    def test_full_pipeline(self):
        """完整流程：传感器数据 → 翻译 → 注入 → 拒绝判断"""
        injector = PromptInjector()
        readings = [
            {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C",
             "description": "CPU 温度", "severity": "normal"},
            {"sensor_name": "battery_percentage", "value": 80.0, "unit": "%",
             "description": "电池电量", "severity": "normal"},
            {"sensor_name": "memory_usage", "value": 45.0, "unit": "%",
             "description": "内存使用率", "severity": "normal"},
        ]

        # inject
        prompt = injector.inject(readings)
        assert "灵犀" in prompt
        assert "体温正常" in prompt
        assert "能量充足" in prompt

        # translate single
        desc = injector.translate(readings[0])
        assert "体温正常" in desc

        # get_summary
        summary = injector.get_summary(readings)
        assert len(summary) > 0

        # should_reject_task
        rejected, reason = injector.should_reject_task(readings)
        assert rejected is False

    def test_crisis_mode(self):
        """危机模式：多个 CRITICAL 告警"""
        injector = PromptInjector()
        readings = [
            {"sensor_name": "cpu_temperature", "value": 95.0, "unit": "°C",
             "description": "CPU 温度", "severity": "critical"},
            {"sensor_name": "memory_usage", "value": 95.0, "unit": "%",
             "description": "内存使用率", "severity": "critical"},
        ]
        prompt = injector.inject(readings)
        assert "发烧" in prompt
        assert "装不下" in prompt

        rejected, reason = injector.should_reject_task(readings)
        assert rejected is True
        assert "严重不适" in reason

    def test_import_all(self):
        """验证所有公开接口可导入"""
        from cognitive import PromptInjector, PromptConfig
        from cognitive.translator import Translator
        from cognitive.templates import TemplateManager
        assert PromptInjector is not None
        assert PromptConfig is not None
        assert Translator is not None
        assert TemplateManager is not None
