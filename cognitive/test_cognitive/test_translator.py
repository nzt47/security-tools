# cognitive/test_cognitive/test_translator.py
import pytest
from cognitive.config import PromptConfig
from cognitive.translator import Translator


class TestTranslator:
    def setup_method(self):
        self.config = PromptConfig()
        self.translator = Translator(self.config)

    def test_translate_cpu_critical(self):
        """CPU 温度 >= 80 应返回发烧描述"""
        reading = {"sensor_name": "cpu_temperature", "value": 85.0, "unit": "°C"}
        result = self.translator.translate(reading)
        assert "发烧" in result or "发烫" in result

    def test_translate_cpu_warning(self):
        """CPU 温度 70-80 应返回有点热"""
        reading = {"sensor_name": "cpu_temperature", "value": 75.0, "unit": "°C"}
        result = self.translator.translate(reading)
        assert "有点热" in result

    def test_translate_cpu_normal(self):
        """CPU 温度 < 70 应返回体温正常"""
        reading = {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C"}
        result = self.translator.translate(reading)
        assert "体温正常" in result

    def test_translate_battery_critical(self):
        """电量 < 10 应返回饥饿"""
        reading = {"sensor_name": "battery_percentage", "value": 5.0, "unit": "%"}
        result = self.translator.translate(reading)
        assert "饿" in result

    def test_translate_battery_warning(self):
        """电量 10-20 应返回开始饿了"""
        reading = {"sensor_name": "battery_percentage", "value": 15.0, "unit": "%"}
        result = self.translator.translate(reading)
        assert "开始饿" in result

    def test_translate_battery_normal(self):
        """电量 > 20 应返回能量充足"""
        reading = {"sensor_name": "battery_percentage", "value": 50.0, "unit": "%"}
        result = self.translator.translate(reading)
        assert "能量充足" in result

    def test_translate_memory_critical(self):
        """内存 >= 90 应返回脑子装不下"""
        reading = {"sensor_name": "memory_usage", "value": 95.0, "unit": "%"}
        result = self.translator.translate(reading)
        assert "装不下" in result

    def test_translate_unknown_sensor_fallback(self):
        """未知传感器应返回通用格式"""
        reading = {"sensor_name": "unknown_sensor", "value": 42.0, "unit": "",
                   "description": "测试传感器"}
        result = self.translator.translate(reading)
        assert "测试传感器" in result
        assert "42" in result

    def test_translate_all_returns_list(self):
        """批量翻译应返回等长字符串列表"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C"},
            {"sensor_name": "battery_percentage", "value": 50.0, "unit": "%"},
        ]
        results = self.translator.translate_all(readings)
        assert len(results) == 2
        assert all(isinstance(r, str) for r in results)

    def test_get_status_line(self):
        """get_status_line 应返回非空摘要"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 50.0, "unit": "°C", "severity": "normal"},
            {"sensor_name": "battery_percentage", "value": 50.0, "unit": "%", "severity": "normal"},
        ]
        result = self.translator.get_status_line(readings)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_status_line_with_alerts(self):
        """有告警时摘要应包含告警信息"""
        readings = [
            {"sensor_name": "cpu_temperature", "value": 85.0, "unit": "°C", "severity": "critical"},
            {"sensor_name": "battery_percentage", "value": 50.0, "unit": "%", "severity": "normal"},
        ]
        result = self.translator.get_status_line(readings)
        assert "发烧" in result or "发烫" in result

    def test_translate_boundary_values(self):
        """边界值应正确匹配对应阈值"""
        # 刚好 80 度（min=80 包含）
        r1 = self.translator.translate({"sensor_name": "cpu_temperature", "value": 80.0})
        assert "发烧" in r1 or "发烫" in r1
        # 刚好 70 度（min=70, max=80）
        r2 = self.translator.translate({"sensor_name": "cpu_temperature", "value": 70.0})
        assert "有点热" in r2

    def test_translate_missing_sensor_name(self):
        """缺少 sensor_name 时应降级为通用描述"""
        result = self.translator.translate({"value": 42.0})
        assert "42" in result

    def test_translate_all_empty(self):
        """空列表输入应返回空列表"""
        assert self.translator.translate_all([]) == []

    def test_get_status_line_empty(self):
        """空列表输入应返回默认摘要"""
        assert self.translator.get_status_line([]) == "一切正常"
