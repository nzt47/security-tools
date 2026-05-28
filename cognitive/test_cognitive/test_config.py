# cognitive/test_cognitive/test_config.py
import pytest
from cognitive.config import PromptConfig


class TestPromptConfig:
    def setup_method(self):
        self.config = PromptConfig()

    def test_get_known_rule(self):
        """已知传感器应返回规则"""
        rule = self.config.get_rule("cpu_temperature")
        assert rule is not None
        assert "thresholds" in rule
        assert len(rule["thresholds"]) > 0

    def test_get_unknown_rule_returns_empty_dict(self):
        """未知传感器应返回空字典"""
        rule = self.config.get_rule("nonexistent_sensor")
        assert rule == {}

    def test_thresholds_have_required_fields(self):
        """每个阈值应有 severity 和 message"""
        for sensor_name in ["cpu_temperature", "battery_percentage", "memory_usage",
                            "network_latency", "disk_space_usage"]:
            rule = self.config.get_rule(sensor_name)
            for t in rule["thresholds"]:
                assert "severity" in t
                assert "message" in t

    def test_register_rule_overrides(self):
        """运行时注册规则应覆盖已有规则"""
        custom = {"thresholds": [{"min": 0, "max": 100, "severity": "normal",
                                  "message": "自定义描述"}]}
        self.config.register_rule("cpu_temperature", custom)
        rule = self.config.get_rule("cpu_temperature")
        assert rule["thresholds"][0]["message"] == "自定义描述"

    def test_get_all_rules_returns_dict(self):
        """get_all_rules 应返回包含已知传感器的字典"""
        rules = self.config.get_all_rules()
        assert "cpu_temperature" in rules
        assert "battery_percentage" in rules
        assert "memory_usage" in rules

    def test_register_rule_invalid_input_raises(self):
        """注册无效规则应抛出 ValueError"""
        with pytest.raises(ValueError, match="thresholds"):
            self.config.register_rule("bad", {"invalid": True})
        with pytest.raises(ValueError, match="thresholds"):
            self.config.register_rule("bad", {"thresholds": "not_a_list"})

    def test_load_from_file_graceful_fallback(self):
        """yaml 未安装时 load_from_file 应静默降级"""
        # 不应抛出异常
        self.config.load_from_file("nonexistent.yaml")
        assert True

    def test_get_rule_returns_deep_copy(self):
        """get_rule 应返回深拷贝，修改返回值不影响内部状态"""
        rule = self.config.get_rule("cpu_temperature")
        rule["thresholds"].pop()
        # 再次获取应仍包含完整数据
        rule2 = self.config.get_rule("cpu_temperature")
        assert len(rule2["thresholds"]) == 3

    def test_threshold_no_overlap(self):
        """同一传感器的阈值区间不应重叠（基本正确性检查）"""
        for sensor_name in ["cpu_temperature", "battery_percentage", "memory_usage",
                            "network_latency", "disk_space_usage"]:
            rule = self.config.get_rule(sensor_name)
            ranges = []
            for t in rule["thresholds"]:
                lo = t.get("min", float("-inf"))
                hi = t.get("max", float("inf"))
                ranges.append((lo, hi))
            sorted_ranges = sorted(ranges, key=lambda x: x[0])
            for i in range(len(sorted_ranges) - 1):
                assert sorted_ranges[i][1] <= sorted_ranges[i + 1][0], \
                    f"阈值区间重叠: {sorted_ranges[i]} 与 {sorted_ranges[i + 1]}"
