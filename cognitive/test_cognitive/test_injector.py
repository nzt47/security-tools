# cognitive/test_cognitive/test_injector.py
import pytest
from cognitive.prompt_injector import PromptInjector


class TestPromptInjector:
    def setup_method(self):
        self.injector = PromptInjector()

    def test_inject_returns_string(self):
        """inject 应返回字符串"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C", "severity": "normal"},
        ]
        result = self.injector.inject(readings)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_inject_contains_body_status(self):
        """inject 返回的 prompt 应包含拟人化描述"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 85.0, "unit": "°C", "severity": "critical"},
        ]
        result = self.injector.inject(readings)
        assert "发烧" in result or "发烫" in result

    def test_inject_contains_template(self):
        """inject 返回的 prompt 应包含模板内容"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C", "severity": "normal"},
        ]
        result = self.injector.inject(readings)
        assert "云枢" in result

    def test_translate_single(self):
        """translate 应返回单条翻译"""
        reading = {"sensor_name": "cpu_temperature", "value": 85.0, "unit": "°C", "severity": "critical"}
        result = self.injector.translate(reading)
        assert isinstance(result, str)
        assert "发烧" in result or "发烫" in result

    def test_get_summary(self):
        """get_summary 应返回非空摘要"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C", "severity": "normal"},
            {"sensor_name": "battery_percentage", "value": 50.0, "unit": "%", "severity": "normal"},
        ]
        result = self.injector.get_summary(readings)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_should_reject_with_critical(self):
        """有 critical 告警时应建议拒绝"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 85.0, "unit": "°C", "severity": "critical"},
        ]
        rejected, reason = self.injector.should_reject_task(readings)
        assert rejected is True
        assert len(reason) > 0

    def test_should_not_reject_normal(self):
        """一切正常时不应拒绝"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C", "severity": "normal"},
        ]
        rejected, reason = self.injector.should_reject_task(readings)
        assert rejected is False
        assert "一切正常" in reason

    def test_should_warn_with_many_warnings(self):
        """3 个及以上 warning 时应给出警告但不拒绝"""
        readings = [
            {"sensor_name": "sensor_a", "value": 50.0, "unit": "", "severity": "warning"},
            {"sensor_name": "sensor_b", "value": 50.0, "unit": "", "severity": "warning"},
            {"sensor_name": "sensor_c", "value": 50.0, "unit": "", "severity": "warning"},
        ]
        rejected, reason = self.injector.should_reject_task(readings)
        assert rejected is False
        assert "不太好" in reason or "建议简化" in reason

    def test_empty_readings(self):
        """空数据输入不应崩溃"""
        result = self.injector.inject([])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_inject_with_custom_config_and_templates(self):
        """应支持自定义配置和模板"""
        from cognitive.config import PromptConfig
        config = PromptConfig()
        config.register_rule("test_sensor", {
            "thresholds": [{"min": 0, "max": 100, "severity": "normal",
                            "message": "自定义测试"}]
        })
        templates = {"default": "自定义: {body_status}"}
        injector = PromptInjector(config=config, templates=templates)
        readings = [{"sensor_name": "test_sensor", "value": 50, "severity": "normal"}]
        result = injector.inject(readings)
        assert "自定义测试" in result
        assert "自定义:" in result
