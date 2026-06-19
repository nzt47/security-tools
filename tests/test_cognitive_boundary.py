#!/usr/bin/env python3
"""认知系统边界值和异常输入单元测试"""

import pytest
from cognitive import PromptInjector
from cognitive.translator import Translator
from cognitive.config import PromptConfig

class TestBoundaryValues:
    """边界值测试"""

    def test_cpu_temperature_boundaries(self):
        """测试 CPU 温度边界值"""
        config = PromptConfig()
        translator = Translator(config)
        
        # 默认配置: <70=正常, 70-80=警告, >=80=严重
        assert translator.translate({"sensor_name": "cpu_temperature", "value": 0.0}) == "体温正常，感觉舒服"
        assert translator.translate({"sensor_name": "cpu_temperature", "value": 69.9}) == "体温正常，感觉舒服"
        assert translator.translate({"sensor_name": "cpu_temperature", "value": 70.0}) == "有点热，需要透透气"
        assert translator.translate({"sensor_name": "cpu_temperature", "value": 79.9}) == "有点热，需要透透气"
        assert translator.translate({"sensor_name": "cpu_temperature", "value": 80.0}) == "我感觉发烧了，浑身发烫"
        assert translator.translate({"sensor_name": "cpu_temperature", "value": 100.0}) == "我感觉发烧了，浑身发烫"

    def test_battery_percentage_boundaries(self):
        """测试电池电量边界值"""
        config = PromptConfig()
        translator = Translator(config)
        
        # 默认配置: <10=严重, 10-20=警告, >=20=正常 (左闭右开)
        assert translator.translate({"sensor_name": "battery_percentage", "value": 0.0}) == "我太饿了，急需补充能量"
        assert translator.translate({"sensor_name": "battery_percentage", "value": 9.9}) == "我太饿了，急需补充能量"
        assert translator.translate({"sensor_name": "battery_percentage", "value": 10.0}) == "我开始饿了，记得给我充电"
        assert translator.translate({"sensor_name": "battery_percentage", "value": 19.9}) == "我开始饿了，记得给我充电"
        assert translator.translate({"sensor_name": "battery_percentage", "value": 20.0}) == "能量充足，随时待命"
        assert translator.translate({"sensor_name": "battery_percentage", "value": 50.0}) == "能量充足，随时待命"
        assert translator.translate({"sensor_name": "battery_percentage", "value": 100.0}) == "能量充足，随时待命"

    def test_memory_usage_boundaries(self):
        """测试内存使用率边界值"""
        config = PromptConfig()
        translator = Translator(config)
        
        # 默认配置: <70=正常, 70-90=警告, >=90=严重
        assert translator.translate({"sensor_name": "memory_usage", "value": 0.0}) == "头脑清晰，思维敏捷"
        assert translator.translate({"sensor_name": "memory_usage", "value": 69.9}) == "头脑清晰，思维敏捷"
        assert translator.translate({"sensor_name": "memory_usage", "value": 70.0}) == "有点拥挤，但还能工作"
        assert translator.translate({"sensor_name": "memory_usage", "value": 89.9}) == "有点拥挤，但还能工作"
        assert translator.translate({"sensor_name": "memory_usage", "value": 90.0}) == "我的脑子快装不下了，需要整理一下"
        assert translator.translate({"sensor_name": "memory_usage", "value": 100.0}) == "我的脑子快装不下了，需要整理一下"

    def test_network_latency_boundaries(self):
        """测试网络延迟边界值"""
        config = PromptConfig()
        translator = Translator(config)
        
        # 默认配置: <200=正常, 200-500=警告, >=500=严重
        assert translator.translate({"sensor_name": "network_latency", "value": 0.0}) == "网络通畅，沟通无阻"
        assert translator.translate({"sensor_name": "network_latency", "value": 199.9}) == "网络通畅，沟通无阻"
        assert translator.translate({"sensor_name": "network_latency", "value": 200.0}) == "网络有点延迟"
        assert translator.translate({"sensor_name": "network_latency", "value": 499.9}) == "网络有点延迟"
        assert translator.translate({"sensor_name": "network_latency", "value": 500.0}) == "我听不太清你说话，信号不太好"
        assert translator.translate({"sensor_name": "network_latency", "value": 1000.0}) == "我听不太清你说话，信号不太好"


class TestExceptionalInput:
    """异常输入测试"""

    def test_empty_sensor_name(self):
        """测试空传感器名称"""
        config = PromptConfig()
        translator = Translator(config)
        result = translator.translate({"sensor_name": "", "value": 50.0})
        assert result == "传感器读数未识别"

    def test_none_sensor_name(self):
        """测试 None 传感器名称"""
        config = PromptConfig()
        translator = Translator(config)
        result = translator.translate({"sensor_name": None, "value": 50.0})
        assert result == "传感器读数未识别"

    def test_nonexistent_sensor(self):
        """测试不存在的传感器"""
        config = PromptConfig()
        translator = Translator(config)
        result = translator.translate({"sensor_name": "nonexistent_sensor", "value": 50.0})
        assert result == "传感器读数未识别"

    def test_missing_sensor_name(self):
        """测试缺少传感器名称"""
        config = PromptConfig()
        translator = Translator(config)
        result = translator.translate({"value": 50.0})
        assert result == "传感器读数未识别"

    def test_missing_value(self):
        """测试缺少值"""
        config = PromptConfig()
        translator = Translator(config)
        result = translator.translate({"sensor_name": "cpu_temperature"})
        assert "传感器读数未识别" in result or "体温正常" in result

    def test_none_value(self):
        """测试 None 值"""
        config = PromptConfig()
        translator = Translator(config)
        result = translator.translate({"sensor_name": "cpu_temperature", "value": None})
        assert result == "传感器读数未识别"

    def test_string_value(self):
        """测试字符串值（可转换）"""
        config = PromptConfig()
        translator = Translator(config)
        result = translator.translate({"sensor_name": "cpu_temperature", "value": "60"})
        assert result == "体温正常，感觉舒服"

    def test_invalid_string_value(self):
        """测试无效字符串值"""
        config = PromptConfig()
        translator = Translator(config)
        result = translator.translate({"sensor_name": "cpu_temperature", "value": "not_a_number"})
        assert result == "传感器读数未识别"

    def test_negative_value(self):
        """测试负值"""
        config = PromptConfig()
        translator = Translator(config)
        result = translator.translate({"sensor_name": "cpu_temperature", "value": -10.0})
        assert result == "体温正常，感觉舒服"

    def test_extreme_value(self):
        """测试极端值"""
        config = PromptConfig()
        translator = Translator(config)
        result = translator.translate({"sensor_name": "cpu_temperature", "value": 1000.0})
        assert result == "我感觉发烧了，浑身发烫"

    def test_zero_value(self):
        """测试零值"""
        config = PromptConfig()
        translator = Translator(config)
        assert translator.translate({"sensor_name": "cpu_temperature", "value": 0.0}) == "体温正常，感觉舒服"
        assert translator.translate({"sensor_name": "memory_usage", "value": 0.0}) == "头脑清晰，思维敏捷"


class TestEmptyAndNullData:
    """空数据和无效数据测试"""

    def test_empty_list(self):
        """测试空列表"""
        injector = PromptInjector()
        result = injector.inject([])
        assert "身体状态正常" in result

    def test_none_input(self):
        """测试 None 输入"""
        injector = PromptInjector()
        result = injector.inject(None)
        assert "身体状态正常" in result

    def test_non_list_input(self):
        """测试非列表输入"""
        injector = PromptInjector()
        result = injector.inject("invalid")
        assert "身体状态正常" in result

    def test_list_with_non_dict(self):
        """测试包含非字典的列表"""
        injector = PromptInjector()
        result = injector.inject([
            {"sensor_name": "cpu_temperature", "value": 50.0},
            "invalid_data",
            123,
            None,
            {"sensor_name": "battery_percentage", "value": 80.0}
        ])
        assert "体温正常" in result or "身体状态正常" in result

    def test_empty_dict(self):
        """测试空字典"""
        config = PromptConfig()
        translator = Translator(config)
        result = translator.translate({})
        assert result == "传感器读数未识别"


class TestEdgeCases:
    """边缘情况测试"""

    def test_max_float_value(self):
        """测试最大浮点值"""
        config = PromptConfig()
        translator = Translator(config)
        result = translator.translate({"sensor_name": "cpu_temperature", "value": float('inf')})
        assert result == "我感觉发烧了，浑身发烫"

    def test_min_float_value(self):
        """测试最小浮点值"""
        config = PromptConfig()
        translator = Translator(config)
        result = translator.translate({"sensor_name": "cpu_temperature", "value": float('-inf')})
        assert result == "体温正常，感觉舒服"

    def test_nan_value(self):
        """测试 NaN 值"""
        import math
        config = PromptConfig()
        translator = Translator(config)
        result = translator.translate({"sensor_name": "cpu_temperature", "value": math.nan})
        assert result == "传感器读数未识别"

    def test_large_data_set(self):
        """测试大量传感器数据"""
        injector = PromptInjector()
        readings = [{"sensor_name": "cpu_temperature", "value": 50.0 + i} for i in range(100)]
        result = injector.inject(readings)
        assert len(result) > 0

    def test_duplicate_sensors(self):
        """测试重复传感器"""
        injector = PromptInjector()
        readings = [
            {"sensor_name": "cpu_temperature", "value": 50.0},
            {"sensor_name": "cpu_temperature", "value": 80.0},
            {"sensor_name": "cpu_temperature", "value": 60.0}
        ]
        result = injector.inject(readings)
        assert "体温正常" in result or "发烧" in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])