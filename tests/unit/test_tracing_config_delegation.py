# -*- coding: utf-8 -*-
"""
tracing_config.py 委托链单元测试

【测试目标】
验证 agent/monitoring/tracing_config.py 已真正收拢到 observability_config.py：
1. TracingConfig 等价于 get_tracing_config_compat（纯委托，无独立实现）
2. 修改 observability_config 的 tracing.* 配置 → tracing_config 即时读取到新值
3. TRACING_* 环境变量覆盖优先级正确（env var > obs config > 默认值）
4. 原 TracingConfig 全部对外接口保持向后兼容（含 sampler_rate_limit）

【可观测性约束】
- 结构化日志：trace_id / module_name / action / duration_ms（由被测模块保证）
- 边界显性化：非法 env var 值降级路径显式覆盖
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

import pytest

from agent.monitoring.observability_config import (
    ObservabilityConfig,
    _TracingConfigCompat,
    get_observability_config,
    get_tracing_config_compat,
    reset_observability_config,
)
from agent.monitoring import tracing_config as tracing_config_module


# ═══════════════════════════════════════════════════════════════
#  公共夹具
# ═══════════════════════════════════════════════════════════════

# 所有可能影响委托结果的 TRACING_* 环境变量清单
_TRACING_ENV_KEYS = [
    "TRACING_ENV",
    "TRACING_LOG_LEVEL",
    "TRACING_SAMPLER",
    "TRACING_SAMPLER_RATIO",
    "TRACING_SAMPLER_RATE_LIMIT",
    "TRACING_EXPORTER",
    "TRACING_EXPORTER_ENDPOINT",
    "TRACING_EXPORTER_PROTOCOL",
    "TRACING_DATA_RETENTION_DAYS",
]


@pytest.fixture(autouse=True)
def clean_tracing_env(monkeypatch):
    """清除所有 TRACING_* 环境变量，避免宿主环境污染委托结果"""
    for key in _TRACING_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def fresh_config() -> ObservabilityConfig:
    """返回独立的 ObservabilityConfig 实例（不污染全局单例）"""
    return ObservabilityConfig()


@pytest.fixture
def reset_global():
    """重置全局 ObservabilityConfig 单例（测试前后均重置）"""
    reset_observability_config()
    yield
    reset_observability_config()


# ═══════════════════════════════════════════════════════════════
#  1. 委托关系验证
# ═══════════════════════════════════════════════════════════════

class TestDelegationRelationship:
    """验证 tracing_config.py 已删除独立实现并委托到 observability_config"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_tracing_config_alias_is_compat_factory(self):
        """TracingConfig 应等价于 get_tracing_config_compat（无独立类实现）"""
        # tracing_config 模块导出的 TracingConfig 即 get_tracing_config_compat
        assert tracing_config_module.TracingConfig is get_tracing_config_compat

    @pytest.mark.unit
    @pytest.mark.p0
    def test_module_singleton_is_compat_instance(self):
        """模块级 tracing_config 单例应为 _TracingConfigCompat 实例"""
        assert isinstance(tracing_config_module.tracing_config, _TracingConfigCompat)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_tracing_config_compat_returns_compat(self, reset_global):
        compat = get_tracing_config_compat()
        assert isinstance(compat, _TracingConfigCompat)
        assert isinstance(compat._obs, ObservabilityConfig)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_no_independent_tracing_config_class(self):
        """tracing_config 模块不应再定义独立的 TracingConfig 类"""
        # TracingConfig 应是导入的别名，而非本模块定义的类
        # 即 __dict__ 中 TracingConfig 的 __module__ 应为 observability_config
        tc = tracing_config_module.TracingConfig
        # get_tracing_config_compat 是函数，其 __module__ 为 observability_config
        assert tc.__module__ == "agent.monitoring.observability_config"


# ═══════════════════════════════════════════════════════════════
#  2. 热修改委托：obs config 改 → tracing_config 读到新值
# ═══════════════════════════════════════════════════════════════

class TestHotConfigDelegation:
    """验证修改 observability_config 的 tracing.* 即时反映到 compat"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_env_hot_change_reflected(self, fresh_config):
        compat = _TracingConfigCompat(fresh_config)
        assert compat.env == "development"
        fresh_config.set("tracing.env", "production")
        assert compat.env == "production"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_level_hot_change_reflected(self, fresh_config):
        compat = _TracingConfigCompat(fresh_config)
        assert compat.log_level == "INFO"
        fresh_config.set("tracing.log_level", "WARN")
        assert compat.log_level == "WARN"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sampler_ratio_hot_change_reflected(self, fresh_config):
        compat = _TracingConfigCompat(fresh_config)
        assert compat.sampler_ratio == 0.1
        fresh_config.set("tracing.sampler_ratio", 0.5)
        assert compat.sampler_ratio == 0.5

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sampler_type_derived_from_env_after_hot_change(self, fresh_config):
        """sampler_type 在无 env var 时根据 env 推导"""
        compat = _TracingConfigCompat(fresh_config)
        # development → ALWAYS_ON
        assert compat.sampler_type == "ALWAYS_ON"
        # 切到 production → PARENT_BASED_RATIO
        fresh_config.set("tracing.env", "production")
        assert compat.sampler_type == "PARENT_BASED_RATIO"
        # 切到 staging → ALWAYS_ON
        fresh_config.set("tracing.env", "staging")
        assert compat.sampler_type == "ALWAYS_ON"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_global_config_hot_change_visible_via_compat(self, reset_global):
        """通过全局 get_tracing_config_compat 读取全局配置的热修改"""
        cfg = get_observability_config()
        compat = get_tracing_config_compat()
        cfg.set("tracing.env", "production")
        cfg.set("tracing.log_level", "ERROR")
        assert compat.env == "production"
        assert compat.log_level == "ERROR"
        assert compat.sampler_type == "PARENT_BASED_RATIO"


# ═══════════════════════════════════════════════════════════════
#  3. 环境变量覆盖优先级
# ═══════════════════════════════════════════════════════════════

class TestEnvVarPriority:
    """验证 TRACING_* 环境变量优先级高于 observability_config"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_tracing_env_overrides_obs_config(self, fresh_config, monkeypatch):
        fresh_config.set("tracing.env", "staging")
        compat = _TracingConfigCompat(fresh_config)
        monkeypatch.setenv("TRACING_ENV", "production")
        assert compat.env == "production"  # env var 胜出

    @pytest.mark.unit
    @pytest.mark.p0
    def test_tracing_log_level_overrides_obs_config(self, fresh_config, monkeypatch):
        fresh_config.set("tracing.log_level", "INFO")
        compat = _TracingConfigCompat(fresh_config)
        monkeypatch.setenv("TRACING_LOG_LEVEL", "debug")
        # 应被 upper 化为 DEBUG
        assert compat.log_level == "DEBUG"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_tracing_sampler_overrides_derived(self, fresh_config, monkeypatch):
        compat = _TracingConfigCompat(fresh_config)
        # 默认 development 推导 ALWAYS_ON
        assert compat.sampler_type == "ALWAYS_ON"
        monkeypatch.setenv("TRACING_SAMPLER", "always_off")
        assert compat.sampler_type == "ALWAYS_OFF"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_tracing_sampler_ratio_overrides_obs_config(self, fresh_config, monkeypatch):
        fresh_config.set("tracing.sampler_ratio", 0.1)
        compat = _TracingConfigCompat(fresh_config)
        monkeypatch.setenv("TRACING_SAMPLER_RATIO", "0.75")
        assert compat.sampler_ratio == 0.75

    @pytest.mark.unit
    @pytest.mark.p0
    def test_tracing_sampler_rate_limit_env(self, fresh_config, monkeypatch):
        compat = _TracingConfigCompat(fresh_config)
        # 无 env var 时默认 100
        assert compat.sampler_rate_limit == 100
        monkeypatch.setenv("TRACING_SAMPLER_RATE_LIMIT", "250")
        assert compat.sampler_rate_limit == 250

    @pytest.mark.unit
    @pytest.mark.p1
    def test_tracing_exporter_type_overrides_derived(self, fresh_config, monkeypatch):
        compat = _TracingConfigCompat(fresh_config)
        # development 默认 CONSOLE
        assert compat.exporter_type == "CONSOLE"
        monkeypatch.setenv("TRACING_EXPORTER", "otlp")
        assert compat.exporter_type == "OTLP"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_tracing_exporter_endpoint_overrides_derived(self, fresh_config, monkeypatch):
        compat = _TracingConfigCompat(fresh_config)
        # development 默认空串
        assert compat.exporter_endpoint == ""
        monkeypatch.setenv("TRACING_EXPORTER_ENDPOINT", "collector:4317")
        assert compat.exporter_endpoint == "collector:4317"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_tracing_exporter_protocol_overrides_default(self, fresh_config, monkeypatch):
        compat = _TracingConfigCompat(fresh_config)
        assert compat.exporter_protocol == "GRPC"
        monkeypatch.setenv("TRACING_EXPORTER_PROTOCOL", "http")
        assert compat.exporter_protocol == "HTTP"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_tracing_data_retention_days_overrides_derived(self, fresh_config, monkeypatch):
        compat = _TracingConfigCompat(fresh_config)
        # development 默认 7
        assert compat.data_retention_days == 7
        monkeypatch.setenv("TRACING_DATA_RETENTION_DAYS", "60")
        assert compat.data_retention_days == 60

    @pytest.mark.unit
    @pytest.mark.p0
    def test_env_var_priority_full_matrix(self, fresh_config, monkeypatch):
        """完整优先级矩阵：env var > obs config > 默认值"""
        # obs config 设一组值
        fresh_config.set("tracing.env", "staging")
        fresh_config.set("tracing.log_level", "INFO")
        fresh_config.set("tracing.sampler_ratio", 0.2)
        compat = _TracingConfigCompat(fresh_config)

        # 仅设 obs config（无 env var）：读取 obs config 值
        assert compat.env == "staging"
        assert compat.log_level == "INFO"
        assert compat.sampler_ratio == 0.2

        # 设 env var：应覆盖 obs config
        monkeypatch.setenv("TRACING_ENV", "production")
        monkeypatch.setenv("TRACING_LOG_LEVEL", "WARN")
        monkeypatch.setenv("TRACING_SAMPLER_RATIO", "0.9")
        assert compat.env == "production"
        assert compat.log_level == "WARN"
        assert compat.sampler_ratio == 0.9


# ═══════════════════════════════════════════════════════════════
#  4. 非法 env var 值的降级（边界显性化）
# ═══════════════════════════════════════════════════════════════

class TestEnvVarInvalidFallback:
    """非法 env var 值应安全降级到 obs config / 默认值，而非崩溃"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_invalid_sampler_ratio_falls_back(self, fresh_config, monkeypatch):
        fresh_config.set("tracing.sampler_ratio", 0.3)
        compat = _TracingConfigCompat(fresh_config)
        monkeypatch.setenv("TRACING_SAMPLER_RATIO", "not-a-number")
        # 非法值应被忽略，降级到 obs config
        assert compat.sampler_ratio == 0.3

    @pytest.mark.unit
    @pytest.mark.p1
    def test_invalid_sampler_rate_limit_falls_back(self, fresh_config, monkeypatch):
        compat = _TracingConfigCompat(fresh_config)
        monkeypatch.setenv("TRACING_SAMPLER_RATE_LIMIT", "abc")
        # 非法值降级到默认 100
        assert compat.sampler_rate_limit == 100

    @pytest.mark.unit
    @pytest.mark.p1
    def test_invalid_data_retention_days_falls_back(self, fresh_config, monkeypatch):
        compat = _TracingConfigCompat(fresh_config)
        monkeypatch.setenv("TRACING_DATA_RETENTION_DAYS", "12.5x")
        # 非法值降级到 derived（development → 7）
        assert compat.data_retention_days == 7

    @pytest.mark.unit
    @pytest.mark.p1
    def test_empty_env_var_treated_as_unset(self, fresh_config, monkeypatch):
        """空字符串环境变量应视为未设置"""
        fresh_config.set("tracing.env", "staging")
        compat = _TracingConfigCompat(fresh_config)
        monkeypatch.setenv("TRACING_ENV", "")
        assert compat.env == "staging"  # 空串视为未设置，使用 obs config


# ═══════════════════════════════════════════════════════════════
#  5. 向后兼容接口完整性
# ═══════════════════════════════════════════════════════════════

class TestBackwardCompatInterface:
    """验证原 TracingConfig 全部对外接口保持可用"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_all_attributes_present(self, fresh_config):
        compat = _TracingConfigCompat(fresh_config)
        # 原 TracingConfig 暴露的全部属性
        for attr in [
            "env", "log_level", "sampler_type", "sampler_ratio",
            "sampler_rate_limit", "exporter_type", "exporter_endpoint",
            "exporter_protocol", "data_retention_days", "debug_mode",
        ]:
            assert hasattr(compat, attr), f"缺少属性: {attr}"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_config_dict_contains_all_keys(self, fresh_config):
        compat = _TracingConfigCompat(fresh_config)
        d = compat.get_config_dict()
        expected_keys = {
            "env", "log_level", "sampler_type", "sampler_ratio",
            "sampler_rate_limit", "exporter_type", "exporter_endpoint",
            "exporter_protocol", "data_retention_days", "debug_mode",
        }
        assert expected_keys.issubset(set(d.keys()))
        # sampler_rate_limit 必须存在（原 compat 缺失，已补齐）
        assert "sampler_rate_limit" in d
        assert isinstance(d["sampler_rate_limit"], int)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_logging_level_mapping(self, fresh_config):
        compat = _TracingConfigCompat(fresh_config)
        # INFO → logging.INFO
        fresh_config.set("tracing.log_level", "INFO")
        assert compat.get_logging_level() == logging.INFO
        fresh_config.set("tracing.log_level", "DEBUG")
        assert compat.get_logging_level() == logging.DEBUG
        fresh_config.set("tracing.log_level", "WARN")
        assert compat.get_logging_level() == logging.WARN
        fresh_config.set("tracing.log_level", "ERROR")
        assert compat.get_logging_level() == logging.ERROR
        fresh_config.set("tracing.log_level", "CRITICAL")
        assert compat.get_logging_level() == logging.CRITICAL

    @pytest.mark.unit
    @pytest.mark.p1
    def test_is_debug_enabled(self, fresh_config, monkeypatch):
        compat = _TracingConfigCompat(fresh_config)
        # development → debug_mode True
        assert compat.is_debug_enabled() is True
        # production → debug_mode False，但 log_level=DEBUG 时仍 True
        fresh_config.set("tracing.env", "production")
        fresh_config.set("tracing.log_level", "INFO")
        assert compat.is_debug_enabled() is False
        fresh_config.set("tracing.log_level", "DEBUG")
        assert compat.is_debug_enabled() is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_repr_contains_tracing_config(self, fresh_config):
        compat = _TracingConfigCompat(fresh_config)
        r = repr(compat)
        assert "TracingConfig" in r
        assert "env=" in r

    @pytest.mark.unit
    @pytest.mark.p0
    def test_module_exports_preserved(self):
        """tracing_config 模块导出的全部函数/对象保持可用"""
        # 这些是 tracing.py 等模块依赖的对外接口
        assert hasattr(tracing_config_module, "tracing_config")
        assert hasattr(tracing_config_module, "TracingConfig")
        assert callable(tracing_config_module.setup_tracing_logging)
        assert callable(tracing_config_module.get_sampler)
        assert callable(tracing_config_module.get_custom_sampler)
        assert callable(tracing_config_module.get_exporter_config)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_exporter_config_structure(self):
        """get_exporter_config 返回 {type, endpoint, protocol} 字典"""
        cfg = tracing_config_module.get_exporter_config()
        assert isinstance(cfg, dict)
        assert {"type", "endpoint", "protocol"} == set(cfg.keys())

    @pytest.mark.unit
    @pytest.mark.p1
    def test_setup_tracing_logging_idempotent(self):
        """setup_tracing_logging 可重复调用不崩溃"""
        tracing_config_module.setup_tracing_logging()
        tracing_config_module.setup_tracing_logging()
        logger = logging.getLogger("agent.monitoring.tracing")
        assert logger.level is not None


# ═══════════════════════════════════════════════════════════════
#  5.1 get_sampler 分支覆盖（依赖 opentelemetry）
# ═══════════════════════════════════════════════════════════════

class TestGetSampler:
    """验证 get_sampler 根据 sampler_type 返回正确的 OpenTelemetry 采样器"""

    @pytest.fixture(autouse=True)
    def _clean_sampler_env(self, monkeypatch):
        """每个用例前清除采样器相关 env var，确保分支可控"""
        for key in ("TRACING_SAMPLER", "TRACING_SAMPLER_RATIO"):
            monkeypatch.delenv(key, raising=False)
        yield

    @pytest.mark.unit
    @pytest.mark.p1
    def test_always_on(self, monkeypatch):
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON
        monkeypatch.setenv("TRACING_SAMPLER", "ALWAYS_ON")
        assert tracing_config_module.get_sampler() is ALWAYS_ON

    @pytest.mark.unit
    @pytest.mark.p1
    def test_always_off(self, monkeypatch):
        from opentelemetry.sdk.trace.sampling import ALWAYS_OFF
        monkeypatch.setenv("TRACING_SAMPLER", "ALWAYS_OFF")
        assert tracing_config_module.get_sampler() is ALWAYS_OFF

    @pytest.mark.unit
    @pytest.mark.p1
    def test_ratio(self, monkeypatch):
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
        monkeypatch.setenv("TRACING_SAMPLER", "RATIO")
        monkeypatch.setenv("TRACING_SAMPLER_RATIO", "0.5")
        sampler = tracing_config_module.get_sampler()
        assert isinstance(sampler, TraceIdRatioBased)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_parent_based(self, monkeypatch):
        from opentelemetry.sdk.trace.sampling import ParentBased
        monkeypatch.setenv("TRACING_SAMPLER", "PARENT_BASED")
        sampler = tracing_config_module.get_sampler()
        assert isinstance(sampler, ParentBased)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_parent_based_ratio(self, monkeypatch):
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
        monkeypatch.setenv("TRACING_SAMPLER", "PARENT_BASED_RATIO")
        monkeypatch.setenv("TRACING_SAMPLER_RATIO", "0.3")
        sampler = tracing_config_module.get_sampler()
        assert isinstance(sampler, ParentBased)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_unknown_type_falls_back_to_always_on(self, monkeypatch):
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON
        monkeypatch.setenv("TRACING_SAMPLER", "UNKNOWN_TYPE")
        assert tracing_config_module.get_sampler() is ALWAYS_ON


# ═══════════════════════════════════════════════════════════════
#  5.2 get_custom_sampler 降级路径
# ═══════════════════════════════════════════════════════════════

class TestGetCustomSampler:
    """验证 get_custom_sampler 在 tracing_sampling 不可用时降级到 get_sampler"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_custom_sampler_returns_sampler(self, monkeypatch):
        # 清除 env var，使用默认 ALWAYS_ON
        monkeypatch.delenv("TRACING_SAMPLER", raising=False)
        monkeypatch.delenv("TRACING_SAMPLER_RATIO", raising=False)
        sampler = tracing_config_module.get_custom_sampler()
        # 无论走 tracing_sampling 还是降级路径，都应返回一个采样器对象
        assert sampler is not None


# ═══════════════════════════════════════════════════════════════
#  6. 各环境推导逻辑（无 env var 时）
# ═══════════════════════════════════════════════════════════════

class TestEnvDerivation:
    """验证无 env var 时根据 tracing.env 推导各字段（与原 TracingConfig 一致）"""

    @pytest.mark.unit
    @pytest.mark.p1
    @pytest.mark.parametrize("env,expected_sampler,expected_exporter,expected_retention", [
        ("development", "ALWAYS_ON", "CONSOLE", 7),
        ("staging", "ALWAYS_ON", "OTLP", 14),
        ("production", "PARENT_BASED_RATIO", "OTLP", 30),
    ])
    def test_env_derivation_matrix(
        self, fresh_config, env, expected_sampler, expected_exporter, expected_retention
    ):
        fresh_config.set("tracing.env", env)
        compat = _TracingConfigCompat(fresh_config)
        assert compat.sampler_type == expected_sampler
        assert compat.exporter_type == expected_exporter
        assert compat.data_retention_days == expected_retention
        if env in ("staging", "production"):
            assert compat.exporter_endpoint == "localhost:4317"
        else:
            assert compat.exporter_endpoint == ""
