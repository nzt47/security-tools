"""可观测性配置集中化模块测试

覆盖维度：
1. ValidationRule 验证器（范围/枚举/布尔/路径）
2. ObservabilityConfig get/set 基础读写
3. 启动时自动验证与修复
4. 原子性变更与回滚
5. 热加载 reload_from_dict
6. 回调注册与触发
7. 变更日志
8. 向后兼容 _TracingConfigCompat
9. 全局实例与并发安全
"""

import json
import threading

import pytest

from agent.monitoring.observability_config import (
    ValidationRule,
    OBSERVABILITY_VALIDATION_RULES,
    ObservabilityConfig,
    get_observability_config,
    reset_observability_config,
    get_tracing_config_compat,
    _range_validator,
    _choice_validator,
    _bool_validator,
    _path_validator,
    _default_config,
)


# ============================================================================
# 验证器单元测试
# ============================================================================

class TestRangeValidator:
    """范围验证器测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_valid_value(self):
        is_valid, repaired = _range_validator(1, 100)(50)
        assert is_valid is True
        assert repaired == 50.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_out_of_range_repairs_to_midpoint(self):
        is_valid, repaired = _range_validator(1, 100)(200)
        assert is_valid is False
        assert repaired == 50.5  # 中点

    @pytest.mark.unit
    @pytest.mark.p1
    def test_below_range(self):
        is_valid, repaired = _range_validator(10, 100)(5)
        assert is_valid is False
        assert repaired == 55.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_invalid_type(self):
        is_valid, repaired = _range_validator(1, 100)("not_a_number")
        assert is_valid is False
        assert repaired is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_string_numeric(self):
        is_valid, repaired = _range_validator(1, 100)("50")
        assert is_valid is True
        assert repaired == 50.0


class TestChoiceValidator:
    """枚举验证器测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_valid_choice(self):
        is_valid, repaired = _choice_validator(["a", "b", "c"])("b")
        assert is_valid is True
        assert repaired == "b"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_invalid_choice_repairs_to_first(self):
        is_valid, repaired = _choice_validator(["a", "b", "c"])("z")
        assert is_valid is False
        assert repaired == "a"


class TestBoolValidator:
    """布尔验证器测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_native_bool(self):
        assert _bool_validator()(True) == (True, True)
        assert _bool_validator()(False) == (True, False)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_string_true(self):
        for val in ("true", "True", "1", "yes", "YES"):
            is_valid, repaired = _bool_validator()(val)
            assert is_valid is True
            assert repaired is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_string_false(self):
        for val in ("false", "0", "no", ""):
            is_valid, repaired = _bool_validator()(val)
            assert is_valid is False or repaired is False

    @pytest.mark.unit
    @pytest.mark.p1
    def test_int_value(self):
        is_valid, repaired = _bool_validator()(1)
        assert is_valid is True
        assert repaired is True
        is_valid, repaired = _bool_validator()(0)
        assert repaired is False


class TestPathValidator:
    """路径验证器测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_empty_path_is_valid(self):
        is_valid, repaired = _path_validator()("")
        assert is_valid is True
        assert repaired == ""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_existing_dir(self, tmp_path):
        path = str(tmp_path / "log.txt")
        is_valid, repaired = _path_validator()(path)
        assert is_valid is True
        assert repaired == path

    @pytest.mark.unit
    @pytest.mark.p1
    def test_nonexistent_parent_repairs_to_empty(self, tmp_path):
        # 使用 tmp_path 下明确不存在的子目录，避免 Windows 路径解析歧义
        nonexistent = str(tmp_path / "definitely_nonexistent_xyz" / "log.txt")
        is_valid, repaired = _path_validator()(nonexistent)
        assert is_valid is False
        assert repaired == ""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_non_string(self):
        is_valid, repaired = _path_validator()(123)
        assert is_valid is False
        assert repaired == ""


class TestValidationRuleDataclass:
    """ValidationRule 数据类测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rule_fields(self):
        rule = ValidationRule(
            path="test.key",
            validator=_range_validator(1, 10),
            default=5,
            error_message="test error",
            description="test desc",
        )
        assert rule.path == "test.key"
        assert rule.default == 5
        assert rule.error_message == "test error"
        assert rule.description == "test desc"
        assert callable(rule.validator)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_default_description(self):
        rule = ValidationRule(
            path="x",
            validator=_bool_validator(),
            default=True,
            error_message="err",
        )
        assert rule.description == ""


# ============================================================================
# 默认配置树测试
# ============================================================================

class TestDefaultConfig:
    """默认配置树生成测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_default_config_has_all_sections(self):
        cfg = _default_config()
        assert "tracing" in cfg
        assert "logging" in cfg
        assert "metrics" in cfg
        assert "health_check" in cfg
        assert "resource_monitor" in cfg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_default_resource_monitor_values(self):
        cfg = _default_config()
        rm = cfg["resource_monitor"]
        assert rm["enabled"] is True
        assert rm["sample_interval_sec"] == 60
        assert rm["stress_test_interval_sec"] == 1.0
        assert rm["leak_slope_threshold"] == 1.0
        assert rm["history_size"] == 1440

    @pytest.mark.unit
    @pytest.mark.p1
    def test_all_rules_have_defaults(self):
        for rule in OBSERVABILITY_VALIDATION_RULES:
            assert rule.default is not None or rule.path.endswith((".enabled",))


# ============================================================================
# ObservabilityConfig 核心测试
# ============================================================================

class TestObservabilityConfigGetSet:
    """get/set 基础读写测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_observability_config()
        self.config = ObservabilityConfig()
        yield
        reset_observability_config()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_existing_key(self):
        assert self.config.get("resource_monitor.sample_interval_sec") == 60

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_nested_key(self):
        assert self.config.get("tracing.env") == "development"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_missing_key_returns_default(self):
        assert self.config.get("nonexistent.key", "fallback") == "fallback"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_set_valid_value(self):
        assert self.config.set("resource_monitor.sample_interval_sec", 30) is True
        assert self.config.get("resource_monitor.sample_interval_sec") == 30

    @pytest.mark.unit
    @pytest.mark.p1
    def test_set_invalid_value_repairs(self):
        # 超出范围的值会被修复到中点
        result = self.config.set("resource_monitor.sample_interval_sec", 99999)
        assert result is True
        value = self.config.get("resource_monitor.sample_interval_sec")
        assert 1 <= value <= 3600

    @pytest.mark.unit
    @pytest.mark.p1
    def test_set_invalid_type_falls_back_to_default(self):
        result = self.config.set("resource_monitor.enabled", "not_a_bool")
        assert result is True
        # 布尔验证器对非法字符串返回 False 修复值
        value = self.config.get("resource_monitor.enabled")
        assert isinstance(value, bool)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_set_unknown_key_still_writes(self):
        # 未知键无验证规则，直接写入
        assert self.config.set("custom.unknown_key", "value") is True
        assert self.config.get("custom.unknown_key") == "value"


class TestStartupValidationRepair:
    """启动时自动验证修复测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_invalid_initial_config_gets_repaired(self):
        # 传入非法配置，应被修复
        bad_config = {
            "resource_monitor": {
                "sample_interval_sec": 99999,  # 超范围
                "enabled": "invalid",          # 非法布尔
            },
            "tracing": {
                "env": "invalid_env",          # 非法枚举
            },
        }
        config = ObservabilityConfig(initial_config=bad_config)
        # 修复后应在合法范围
        interval = config.get("resource_monitor.sample_interval_sec")
        assert 1 <= interval <= 3600
        env = config.get("tracing.env")
        assert env in ("development", "staging", "production")

    @pytest.mark.unit
    @pytest.mark.p1
    def test_missing_keys_get_defaults(self):
        config = ObservabilityConfig(initial_config={})
        assert config.get("resource_monitor.sample_interval_sec") == 60
        assert config.get("health_check.interval_sec") == 60


class TestAtomicRollback:
    """原子性变更与回滚测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_observability_config()
        self.config = ObservabilityConfig()
        yield
        reset_observability_config()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_valid_change_is_committed(self):
        original = self.config.get("resource_monitor.history_size")
        self.config.set("resource_monitor.history_size", 100)
        assert self.config.get("resource_monitor.history_size") == 100
        assert self.config.get("resource_monitor.history_size") != original

    @pytest.mark.unit
    @pytest.mark.p1
    def test_change_log_recorded(self):
        self.config.set("resource_monitor.sample_interval_sec", 30)
        log = self.config.get_change_log()
        assert len(log) >= 1
        latest = log[-1]
        assert latest["key"] == "resource_monitor.sample_interval_sec"
        assert latest["new_value"] == 30
        assert "old_value" in latest
        assert "timestamp" in latest

    @pytest.mark.unit
    @pytest.mark.p1
    def test_reload_from_valid_dict(self):
        new_config = {"resource_monitor": {"sample_interval_sec": 15}}
        assert self.config.reload_from_dict(new_config) is True
        assert self.config.get("resource_monitor.sample_interval_sec") == 15

    @pytest.mark.unit
    @pytest.mark.p0
    def test_reload_from_invalid_dict_repairs_and_commits(self):
        # 非法值会被修复后提交
        bad_config = {"resource_monitor": {"sample_interval_sec": 99999}}
        result = self.config.reload_from_dict(bad_config)
        assert result is True
        value = self.config.get("resource_monitor.sample_interval_sec")
        assert 1 <= value <= 3600


class TestCallbacks:
    """回调注册与触发测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_observability_config()
        self.config = ObservabilityConfig()
        yield
        reset_observability_config()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_callback_fired_on_set(self):
        received = []
        self.config.register_callback("resource_monitor", lambda k, v: received.append((k, v)))
        self.config.set("resource_monitor.sample_interval_sec", 30)
        assert len(received) == 1
        assert received[0][0] == "resource_monitor.sample_interval_sec"
        assert received[0][1] == 30

    @pytest.mark.unit
    @pytest.mark.p1
    def test_callback_pattern_match_prefix(self):
        received = []
        self.config.register_callback("tracing", lambda k, v: received.append(k))
        self.config.set("tracing.log_level", "DEBUG")
        assert len(received) == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_callback_non_matching_pattern_not_fired(self):
        received = []
        self.config.register_callback("metrics", lambda k, v: received.append(k))
        self.config.set("tracing.log_level", "DEBUG")
        assert len(received) == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_callback_exception_isolated(self):
        def bad_callback(k, v):
            raise RuntimeError("callback boom")
        good_received = []
        self.config.register_callback("resource_monitor", bad_callback)
        self.config.register_callback("resource_monitor", lambda k, v: good_received.append(k))
        # 异常回调不影响后续回调与配置提交
        assert self.config.set("resource_monitor.sample_interval_sec", 30) is True
        assert len(good_received) == 1


class TestGetAllAndRules:
    """get_all 与验证规则查询测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_observability_config()
        self.config = ObservabilityConfig()
        yield
        reset_observability_config()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_all_returns_deep_copy(self):
        all_config = self.config.get_all()
        # 修改返回值不影响内部配置
        all_config["resource_monitor"]["sample_interval_sec"] = 999
        assert self.config.get("resource_monitor.sample_interval_sec") == 60

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_validation_rules(self):
        rules = self.config.get_validation_rules()
        assert len(rules) == len(OBSERVABILITY_VALIDATION_RULES)
        paths = [r["path"] for r in rules]
        assert "resource_monitor.sample_interval_sec" in paths
        for rule in rules:
            assert "default" in rule
            assert "error_message" in rule


class TestTracingConfigCompat:
    """向后兼容 TracingConfig 委托层测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_observability_config()
        yield
        reset_observability_config()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_compat_delegates_to_observability(self):
        compat = get_tracing_config_compat()
        # 默认值应与 observability_config 一致
        assert compat.env == "development"
        assert compat.log_level == "INFO"
        assert 0.0 <= compat.sampler_ratio <= 1.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_compat_reflects_hot_changes(self):
        config = get_observability_config()
        config.set("tracing.env", "production")
        compat = get_tracing_config_compat()
        assert compat.env == "production"
        # 生产环境推导出 PARENT_BASED_RATIO
        assert compat.sampler_type == "PARENT_BASED_RATIO"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_compat_get_config_dict(self):
        compat = get_tracing_config_compat()
        d = compat.get_config_dict()
        assert "env" in d
        assert "log_level" in d
        assert "sampler_ratio" in d

    @pytest.mark.unit
    @pytest.mark.p1
    def test_compat_get_logging_level(self):
        import logging
        compat = get_tracing_config_compat()
        assert compat.get_logging_level() == logging.INFO

    @pytest.mark.unit
    @pytest.mark.p1
    def test_compat_repr(self):
        compat = get_tracing_config_compat()
        r = repr(compat)
        assert "TracingConfig" in r


class TestGlobalInstance:
    """全局实例与并发安全测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_observability_config()
        yield
        reset_observability_config()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_singleton_returns_same_instance(self):
        a = get_observability_config()
        b = get_observability_config()
        assert a is b

    @pytest.mark.unit
    @pytest.mark.p0
    def test_reset_creates_new_instance(self):
        a = get_observability_config()
        reset_observability_config()
        b = get_observability_config()
        assert a is not b

    @pytest.mark.unit
    @pytest.mark.p1
    def test_concurrent_set_thread_safe(self):
        config = get_observability_config()

        def worker():
            for i in range(50):
                config.set("resource_monitor.history_size", 100 + (i % 1000))

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 最终值应在合法范围内
        value = config.get("resource_monitor.history_size")
        assert 10 <= value <= 10000

    @pytest.mark.unit
    @pytest.mark.p1
    def test_watch_config_file_handles_missing_reloader(self):
        # 监听不存在的文件应返回 False 或 True（取决于 reloader 实现），不抛异常
        config = get_observability_config()
        # 使用不存在的路径，方法应捕获异常
        result = config.watch_config_file("/nonexistent/path/config.json")
        # ConfigHotReloader.watch_config 不校验存在性，但 start 应正常
        # 此处仅验证不抛异常
        assert isinstance(result, bool)


class TestEdgeCases:
    """边界与异常用例测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        reset_observability_config()
        self.config = ObservabilityConfig()
        yield
        reset_observability_config()

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_root_level_key(self):
        # 读取整个 resource_monitor 段
        rm = self.config.get("resource_monitor")
        assert isinstance(rm, dict)
        assert "sample_interval_sec" in rm

    @pytest.mark.unit
    @pytest.mark.p1
    def test_set_creates_nested_path(self):
        self.config.set("new.section.key", "value")
        assert self.config.get("new.section.key") == "value"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_change_log_capped(self):
        # 变更日志超过上限应截断
        for i in range(150):
            self.config.set("resource_monitor.history_size", 100 + (i % 1000))
        log = self.config.get_change_log(limit=200)
        assert len(log) <= 100

    @pytest.mark.unit
    @pytest.mark.p0
    def test_reload_empty_dict_keeps_config(self):
        original = self.config.get("resource_monitor.sample_interval_sec")
        assert self.config.reload_from_dict({}) is True
        assert self.config.get("resource_monitor.sample_interval_sec") == original
