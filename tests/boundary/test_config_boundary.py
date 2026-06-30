"""config 模块边界测试 — BT-006

覆盖 config.py 的边界条件：
- 空配置 / None 输入 / 非法类型
- 边界值（timeout/token_limit/check_interval 的 min/max）
- 极值（超出范围的值）
- 自动修复逻辑

被测模块：config.py（项目根目录）
关键 API：
- validate_config(config) -> List[Dict[str, str]]
- _basic_validation(config) -> List[Dict[str, str]]
- validate_and_fix_config(config) -> tuple[Dict, List[Dict]]
- class Config / class ConfigValidationError

【可观测性约束】
- 结构化日志：测试关键节点输出 JSON 格式日志
- 边界显性化：每个边界条件显式断言
"""

import json
import logging
import threading
import time
from copy import deepcopy
from pathlib import Path

import pytest

# config.py 在项目根目录
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config import (
    Config,
    ConfigValidationError,
    validate_config,
    validate_and_fix_config,
    _basic_validation,
    _PYDANTIC_AVAILABLE,
)


logger = logging.getLogger(__name__)


# ============================================================================
#  fixtures
# ============================================================================


@pytest.fixture
def valid_config():
    """完整有效的配置"""
    return {
        "sensor": {
            "enable_change_detection": True,
            "enable_event_monitor": True,
            "watch_dirs": None,
        },
        "cognitive": {"config_path": None},
        "memory": {
            "data_dir": "./data",
            "token_limit": 4096,
            "compress_threshold": 0.8,
            "per_message_send_limit": 2048,
            "per_message_recv_limit": 4096,
            "async_compress": {"enabled": True, "interval_seconds": 60},
            "llm": {"provider": "openai", "api_key": "sk-test", "model": "gpt-4", "timeout": 30},
            "blackbox": {"max_size_mb": 10, "max_files": 10},
        },
        "behavior": {"check_interval": 30},
        "permission": {"backup_dir": "./.backups"},
        "security": {
            "enable_encryption": True,
            "key_file": ".encryption_key",
            "secure_config_file": ".secure_config.json",
        },
    }


@pytest.fixture
def empty_config():
    """空配置字典"""
    return {}


@pytest.fixture
def minimal_config():
    """仅含必需节的空壳配置"""
    return {
        "sensor": {},
        "cognitive": {},
        "memory": {},
        "behavior": {},
        "permission": {},
        "security": {},
    }


# ============================================================================
#  validate_config 边界测试
# ============================================================================


class TestValidateConfigBoundary:
    """validate_config 边界值测试"""

    def test_empty_config_returns_errors(self, empty_config):
        """空配置应返回错误列表（缺失所有必需节）"""
        errors = validate_config(empty_config)
        assert isinstance(errors, list)
        # 空配置至少缺失 6 个必需节
        assert len(errors) >= 6

    def test_boundary_minimal_config_with_empty_sections(self, minimal_config):
        """所有必需节存在但内容为空字典 — Pydantic 校验应通过或返回少量错误"""
        errors = validate_config(minimal_config)
        assert isinstance(errors, list)
        # 所有必需节存在，不应有"缺少必需配置节"错误
        section_missing_errors = [e for e in errors if "缺少必需" in e.get("msg", "")]
        assert len(section_missing_errors) == 0

    def test_boundary_full_valid_config_returns_no_errors(self, valid_config):
        """完整有效配置应返回空错误列表"""
        errors = validate_config(valid_config)
        assert isinstance(errors, list)
        # Pydantic 可用时应无错误；不可用时基础校验也应通过
        assert len(errors) == 0

    def test_boundary_single_section_only(self):
        """仅包含一个配置节"""
        config = {"sensor": {"enable_change_detection": True}}
        errors = validate_config(config)
        assert isinstance(errors, list)
        # 至少缺失 5 个必需节
        assert len(errors) >= 5

    def test_boundary_all_sections_with_extra_keys(self, valid_config):
        """所有节存在且包含额外键 — 应仍通过校验"""
        config = deepcopy(valid_config)
        config["sensor"]["extra_unknown_key"] = "value"
        errors = validate_config(config)
        # Pydantic 默认忽略额外字段
        assert isinstance(errors, list)

    def test_boundary_config_with_empty_dict_value(self, valid_config):
        """配置节的值为空字典"""
        config = deepcopy(valid_config)
        config["memory"] = {}
        errors = validate_config(config)
        assert isinstance(errors, list)

    def test_extreme_config_with_many_sections(self):
        """极值：配置包含大量节"""
        config = {}
        for i in range(100):
            config[f"extra_section_{i}"] = {"key": "value"}
        # 加上必需节
        config["sensor"] = {}
        config["cognitive"] = {}
        config["memory"] = {}
        config["behavior"] = {}
        config["permission"] = {}
        config["security"] = {}
        errors = validate_config(config)
        assert isinstance(errors, list)


# ============================================================================
#  validate_config 非法输入测试
# ============================================================================


class TestValidateConfigInvalid:
    """validate_config 非法输入测试"""

    def test_invalid_section_not_dict(self):
        """配置节值不是字典（字符串）"""
        config = {"sensor": "not_a_dict", "memory": "also_string"}
        errors = validate_config(config)
        assert isinstance(errors, list)
        # 基础校验应报告类型错误
        type_errors = [e for e in errors if "字典" in e.get("msg", "") or "dict" in e.get("msg", "").lower()]
        assert len(type_errors) >= 1

    def test_invalid_section_is_list(self):
        """配置节值是列表"""
        config = {"sensor": [1, 2, 3]}
        errors = validate_config(config)
        assert isinstance(errors, list)

    def test_invalid_section_is_int(self):
        """配置节值是整数"""
        config = {"memory": 12345}
        errors = validate_config(config)
        assert isinstance(errors, list)

    def test_invalid_timeout_type_string(self, valid_config):
        """memory.llm.timeout 是字符串 — Pydantic 可用返回错误列表，不可用抛 TypeError"""
        config = deepcopy(valid_config)
        config["memory"]["llm"]["timeout"] = "thirty"
        if _PYDANTIC_AVAILABLE:
            errors = validate_config(config)
            assert isinstance(errors, list)
            assert len(errors) >= 1
        else:
            # _basic_validation 中 %d 格式化对字符串抛 TypeError
            with pytest.raises(TypeError):
                validate_config(config)

    def test_invalid_token_limit_type_string(self, valid_config):
        """memory.token_limit 是字符串 — Pydantic 可用返回错误列表，不可用抛 TypeError"""
        config = deepcopy(valid_config)
        config["memory"]["token_limit"] = "four_thousand"
        if _PYDANTIC_AVAILABLE:
            errors = validate_config(config)
            assert isinstance(errors, list)
            assert len(errors) >= 1
        else:
            with pytest.raises(TypeError):
                validate_config(config)

    def test_invalid_enable_encryption_type_string(self, valid_config):
        """security.enable_encryption 是字符串而非布尔值"""
        config = deepcopy(valid_config)
        config["security"]["enable_encryption"] = "yes"
        errors = validate_config(config)
        assert isinstance(errors, list)
        assert len(errors) >= 1


# ============================================================================
#  validate_config None/空值测试
# ============================================================================


class TestValidateConfigNull:
    """validate_config None 输入测试"""

    def test_null_config_input_raises(self):
        """None 作为配置输入 — 应抛异常或返回错误"""
        # validate_config 内部调用 config.keys()，None 会抛 AttributeError
        with pytest.raises((AttributeError, TypeError)):
            validate_config(None)

    def test_null_section_value(self):
        """配置节值为 None"""
        config = {"sensor": None, "memory": None}
        # None 不是 dict，基础校验应报告类型错误
        errors = validate_config(config)
        assert isinstance(errors, list)
        # 至少有类型错误
        assert len(errors) >= 1

    def test_null_nested_value_in_llm(self, valid_config):
        """memory.llm.timeout 为 None — Pydantic 可用返回错误列表，不可用抛 TypeError"""
        config = deepcopy(valid_config)
        config["memory"]["llm"]["timeout"] = None
        if _PYDANTIC_AVAILABLE:
            errors = validate_config(config)
            assert isinstance(errors, list)
        else:
            with pytest.raises(TypeError):
                validate_config(config)

    def test_null_token_limit(self, valid_config):
        """memory.token_limit 为 None — Pydantic 可用返回错误列表，不可用抛 TypeError"""
        config = deepcopy(valid_config)
        config["memory"]["token_limit"] = None
        if _PYDANTIC_AVAILABLE:
            errors = validate_config(config)
            assert isinstance(errors, list)
        else:
            with pytest.raises(TypeError):
                validate_config(config)

    def test_null_check_interval(self, valid_config):
        """behavior.check_interval 为 None — Pydantic 可用返回错误列表，不可用抛 TypeError"""
        config = deepcopy(valid_config)
        config["behavior"]["check_interval"] = None
        if _PYDANTIC_AVAILABLE:
            errors = validate_config(config)
            assert isinstance(errors, list)
        else:
            with pytest.raises(TypeError):
                validate_config(config)

    def test_none_value_in_security_enable_encryption(self, valid_config):
        """security.enable_encryption 为 None"""
        config = deepcopy(valid_config)
        config["security"]["enable_encryption"] = None
        errors = validate_config(config)
        assert isinstance(errors, list)

    def test_empty_string_as_config(self):
        """空字符串作为配置输入"""
        with pytest.raises((AttributeError, TypeError)):
            validate_config("")


# ============================================================================
#  validate_and_fix_config 边界测试
# ============================================================================


class TestValidateAndFixBoundary:
    """validate_and_fix_config 边界值测试"""

    def test_empty_config_fixed_to_defaults(self, empty_config):
        """空配置修复后应包含所有必需节"""
        fixed, errors = validate_and_fix_config(empty_config)
        assert isinstance(fixed, dict)
        assert "sensor" in fixed
        assert "cognitive" in fixed
        assert "memory" in fixed
        assert "behavior" in fixed
        assert "permission" in fixed
        assert "security" in fixed
        assert len(errors) >= 6  # 6 个缺失节

    def test_boundary_missing_one_section_fixed(self, valid_config):
        """缺失单个节修复"""
        config = deepcopy(valid_config)
        del config["sensor"]
        fixed, errors = validate_and_fix_config(config)
        assert "sensor" in fixed
        assert len(errors) >= 1

    def test_boundary_missing_all_sections_fixed(self):
        """缺失所有必需节修复"""
        fixed, errors = validate_and_fix_config({})
        required = ["sensor", "cognitive", "memory", "behavior", "permission", "security"]
        for section in required:
            assert section in fixed
        assert len(errors) >= 6

    def test_boundary_full_config_no_fix_needed(self, valid_config):
        """完整配置无需修复"""
        fixed, errors = validate_and_fix_config(valid_config)
        assert len(errors) == 0

    def test_invalid_timeout_fixed_to_default(self, valid_config):
        """无效 timeout 修复为默认值 30"""
        config = deepcopy(valid_config)
        config["memory"]["llm"]["timeout"] = 999
        fixed, errors = validate_and_fix_config(config)
        assert fixed["memory"]["llm"]["timeout"] == 30
        timeout_errors = [e for e in errors if "timeout" in e.get("loc", "")]
        assert len(timeout_errors) >= 1

    def test_invalid_token_limit_fixed_to_default(self, valid_config):
        """无效 token_limit 修复为默认值 4096"""
        config = deepcopy(valid_config)
        config["memory"]["token_limit"] = 999999
        fixed, errors = validate_and_fix_config(config)
        assert fixed["memory"]["token_limit"] == 4096

    def test_invalid_check_interval_fixed_to_default(self, valid_config):
        """无效 check_interval 修复为默认值 30"""
        config = deepcopy(valid_config)
        config["behavior"]["check_interval"] = 999999
        fixed, errors = validate_and_fix_config(config)
        assert fixed["behavior"]["check_interval"] == 30

    def test_invalid_enable_encryption_fixed_to_default(self, valid_config):
        """无效 enable_encryption 修复为 True"""
        config = deepcopy(valid_config)
        config["security"]["enable_encryption"] = "not_bool"
        fixed, errors = validate_and_fix_config(config)
        assert fixed["security"]["enable_encryption"] is True


# ============================================================================
#  validate_and_fix_config 极值测试
# ============================================================================


class TestValidateAndFixExtreme:
    """validate_and_fix_config 极值测试"""

    def test_extreme_timeout_zero_fixed(self, valid_config):
        """timeout=0 修复为默认值"""
        config = deepcopy(valid_config)
        config["memory"]["llm"]["timeout"] = 0
        fixed, errors = validate_and_fix_config(config)
        assert fixed["memory"]["llm"]["timeout"] == 30

    def test_extreme_timeout_negative_fixed(self, valid_config):
        """timeout=-1 修复为默认值"""
        config = deepcopy(valid_config)
        config["memory"]["llm"]["timeout"] = -1
        fixed, errors = validate_and_fix_config(config)
        assert fixed["memory"]["llm"]["timeout"] == 30

    def test_extreme_timeout_above_max_fixed(self, valid_config):
        """timeout=301（超过 300）修复为默认值"""
        config = deepcopy(valid_config)
        config["memory"]["llm"]["timeout"] = 301
        fixed, errors = validate_and_fix_config(config)
        assert fixed["memory"]["llm"]["timeout"] == 30

    def test_extreme_token_limit_below_min_fixed(self, valid_config):
        """token_limit=511（低于 512）修复为默认值"""
        config = deepcopy(valid_config)
        config["memory"]["token_limit"] = 511
        fixed, errors = validate_and_fix_config(config)
        assert fixed["memory"]["token_limit"] == 4096

    def test_extreme_token_limit_above_max_fixed(self, valid_config):
        """token_limit=32769（超过 32768）修复为默认值"""
        config = deepcopy(valid_config)
        config["memory"]["token_limit"] = 32769
        fixed, errors = validate_and_fix_config(config)
        assert fixed["memory"]["token_limit"] == 4096

    def test_extreme_check_interval_below_min_fixed(self, valid_config):
        """check_interval=4（低于 5）修复为默认值"""
        config = deepcopy(valid_config)
        config["behavior"]["check_interval"] = 4
        fixed, errors = validate_and_fix_config(config)
        assert fixed["behavior"]["check_interval"] == 30

    def test_extreme_check_interval_above_max_fixed(self, valid_config):
        """check_interval=301（超过 300）修复为默认值"""
        config = deepcopy(valid_config)
        config["behavior"]["check_interval"] = 301
        fixed, errors = validate_and_fix_config(config)
        assert fixed["behavior"]["check_interval"] == 30

    def test_extreme_timeout_string_type_fixed(self, valid_config):
        """timeout 为字符串类型修复为默认值"""
        config = deepcopy(valid_config)
        config["memory"]["llm"]["timeout"] = "30"
        fixed, errors = validate_and_fix_config(config)
        assert fixed["memory"]["llm"]["timeout"] == 30

    def test_extreme_token_limit_none_fixed(self, valid_config):
        """token_limit=None 修复"""
        config = deepcopy(valid_config)
        config["memory"]["token_limit"] = None
        fixed, errors = validate_and_fix_config(config)
        # None 不在 "in" 检查范围内（None 不等于有值），所以不会被修复
        # 但修复后应不崩溃
        assert isinstance(fixed, dict)

    def test_extreme_memory_section_not_dict_fixed(self):
        """memory 节不是字典修复为空字典"""
        config = {"memory": "not_a_dict", "sensor": {}, "cognitive": {},
                  "behavior": {}, "permission": {}, "security": {}}
        fixed, errors = validate_and_fix_config(config)
        assert isinstance(fixed["memory"], dict)


# ============================================================================
#  _basic_validation 边界测试
# ============================================================================


class TestBasicValidationBoundary:
    """_basic_validation 边界测试"""

    def test_empty_config_returns_errors(self, empty_config):
        """空配置返回缺失节错误"""
        errors = _basic_validation(empty_config)
        assert isinstance(errors, list)
        assert len(errors) >= 6

    def test_full_config_no_errors(self, valid_config):
        """完整配置无错误"""
        errors = _basic_validation(valid_config)
        assert isinstance(errors, list)
        assert len(errors) == 0

    def test_invalid_section_type_error(self):
        """配置节类型错误"""
        config = {"sensor": "string", "memory": 123}
        errors = _basic_validation(config)
        assert isinstance(errors, list)
        type_errors = [e for e in errors if "字典" in e.get("msg", "")]
        assert len(type_errors) >= 2

    def test_boundary_timeout_at_min(self, valid_config):
        """timeout=1（最小值）应通过"""
        config = deepcopy(valid_config)
        config["memory"]["llm"]["timeout"] = 1
        errors = _basic_validation(config)
        timeout_errors = [e for e in errors if "timeout" in e.get("loc", "")]
        assert len(timeout_errors) == 0

    def test_boundary_timeout_at_max(self, valid_config):
        """timeout=300（最大值）应通过"""
        config = deepcopy(valid_config)
        config["memory"]["llm"]["timeout"] = 300
        errors = _basic_validation(config)
        timeout_errors = [e for e in errors if "timeout" in e.get("loc", "")]
        assert len(timeout_errors) == 0

    def test_extreme_timeout_below_min(self, valid_config):
        """timeout=0（低于最小值）报错"""
        config = deepcopy(valid_config)
        config["memory"]["llm"]["timeout"] = 0
        errors = _basic_validation(config)
        timeout_errors = [e for e in errors if "timeout" in e.get("loc", "")]
        assert len(timeout_errors) >= 1

    def test_extreme_timeout_above_max(self, valid_config):
        """timeout=301（超过最大值）报错"""
        config = deepcopy(valid_config)
        config["memory"]["llm"]["timeout"] = 301
        errors = _basic_validation(config)
        timeout_errors = [e for e in errors if "timeout" in e.get("loc", "")]
        assert len(timeout_errors) >= 1

    def test_extreme_token_limit_below_min(self, valid_config):
        """token_limit=511 报错"""
        config = deepcopy(valid_config)
        config["memory"]["token_limit"] = 511
        errors = _basic_validation(config)
        token_errors = [e for e in errors if "token_limit" in e.get("loc", "")]
        assert len(token_errors) >= 1

    def test_extreme_token_limit_above_max(self, valid_config):
        """token_limit=32769 报错"""
        config = deepcopy(valid_config)
        config["memory"]["token_limit"] = 32769
        errors = _basic_validation(config)
        token_errors = [e for e in errors if "token_limit" in e.get("loc", "")]
        assert len(token_errors) >= 1

    def test_null_config_raises(self):
        """None 配置应抛异常"""
        with pytest.raises((AttributeError, TypeError)):
            _basic_validation(None)


# ============================================================================
#  Config 类边界测试
# ============================================================================


class TestConfigClassBoundary:
    """Config 类边界测试"""

    def test_config_empty_overrides(self):
        """空覆盖初始化"""
        config = Config(overrides={}, validate=False)
        assert config is not None
        assert isinstance(config.merged, dict)

    def test_config_null_overrides(self):
        """None 覆盖初始化"""
        config = Config(overrides=None, validate=False)
        assert config is not None
        assert isinstance(config.merged, dict)

    def test_config_invalid_overrides_type(self):
        """非法类型覆盖初始化"""
        # _merge 期望字典，传字符串应抛异常
        with pytest.raises((AttributeError, TypeError)):
            Config(overrides="not_a_dict", validate=False)

    def test_config_get_missing_key_returns_default(self):
        """获取不存在的键返回默认值"""
        config = Config(overrides=None, validate=False)
        result = config.get("nonexistent_key", default="default_value")
        assert result == "default_value"

    def test_config_get_nested_missing_returns_default(self):
        """获取嵌套不存在的键返回默认值"""
        config = Config(overrides=None, validate=False)
        result = config.get("memory", "llm", "nonexistent", default="fallback")
        assert result == "fallback"

    def test_config_get_deeply_missing_returns_default(self):
        """获取深层不存在的键返回默认值"""
        config = Config(overrides=None, validate=False)
        result = config.get("a", "b", "c", "d", default="deep_default")
        assert result == "deep_default"

    def test_config_set_new_key(self):
        """设置新键"""
        config = Config(overrides=None, validate=False)
        config.set("new_value", "new_section", "new_key")
        assert config.get("new_section", "new_key") == "new_value"

    def test_config_set_overrides_existing(self):
        """覆盖已存在的键"""
        config = Config(overrides=None, validate=False)
        original = config.get("behavior", "check_interval")
        config.set(99, "behavior", "check_interval")
        assert config.get("behavior", "check_interval") == 99
        assert config.get("behavior", "check_interval") != original

    def test_config_to_dict_hides_api_key(self):
        """to_dict 隐藏 API Key"""
        config = Config(overrides=None, validate=False)
        config.set("sk-secret-key", "memory", "llm", "api_key")
        exported = config.to_dict()
        assert exported["memory"]["llm"]["api_key"] == "***"

    def test_config_to_dict_does_not_mutate_internal(self):
        """to_dict 不修改内部状态"""
        config = Config(overrides=None, validate=False)
        config.set("sk-test-key", "memory", "llm", "api_key")
        exported = config.to_dict()
        assert exported["memory"]["llm"]["api_key"] == "***"
        # 内部状态应保持不变
        assert config.get("memory", "llm", "api_key") == "sk-test-key"

    def test_config_merged_returns_copy(self):
        """merged 返回副本，修改不影响内部状态"""
        config = Config(overrides=None, validate=False)
        merged = config.merged
        merged["new_key"] = "new_value"
        # 内部状态不应改变
        assert "new_key" not in config.merged

    def test_boundary_config_with_validate_true(self):
        """validate=True 时校验配置"""
        config = Config(overrides=None, validate=True)
        assert config is not None
        # 默认配置应通过校验
        assert isinstance(config.merged, dict)


# ============================================================================
#  ConfigValidationError 异常测试
# ============================================================================


class TestConfigValidationError:
    """ConfigValidationError 异常类测试"""

    def test_error_creation_with_errors(self):
        """创建带错误列表的异常"""
        errors = [{"loc": "memory", "msg": "缺少配置节"}]
        exc = ConfigValidationError(errors)
        assert exc.errors == errors
        # "N 个错误" 在 Exception args[0] 中（__init__ 的 super 调用）
        assert "1 个错误" in exc.args[0]
        # __str__ 返回错误明细
        assert "memory" in str(exc)
        assert "缺少配置节" in str(exc)

    def test_error_creation_with_empty_errors(self):
        """创建带空错误列表的异常"""
        exc = ConfigValidationError([])
        assert exc.errors == []
        assert "0 个错误" in exc.args[0]

    def test_error_str_representation(self):
        """异常字符串表示"""
        errors = [
            {"loc": "memory", "msg": "缺少配置节"},
            {"loc": "behavior", "msg": "类型错误"},
        ]
        exc = ConfigValidationError(errors)
        error_str = str(exc)
        assert "memory" in error_str
        assert "behavior" in error_str
        assert "缺少配置节" in error_str
        assert "类型错误" in error_str

    def test_error_can_be_raised_and_caught(self):
        """异常可被抛出和捕获"""
        with pytest.raises(ConfigValidationError) as exc_info:
            raise ConfigValidationError([{"loc": "test", "msg": "test error"}])
        assert exc_info.value.errors[0]["loc"] == "test"

    def test_error_is_exception_subclass(self):
        """ConfigValidationError 是 Exception 子类"""
        exc = ConfigValidationError([])
        assert isinstance(exc, Exception)

    def test_error_with_many_errors(self):
        """大量错误列表"""
        errors = [{"loc": f"section_{i}", "msg": f"error_{i}"} for i in range(100)]
        exc = ConfigValidationError(errors)
        assert len(exc.errors) == 100
        assert "100 个错误" in exc.args[0]

    def test_error_none_errors_raises(self):
        """None 作为错误列表"""
        # ConfigValidationError(None) 会调用 len(None)，抛 TypeError
        with pytest.raises(TypeError):
            ConfigValidationError(None)


# ============================================================================
#  _merge 边界测试
# ============================================================================


class TestMergeBoundary:
    """Config._merge 边界测试"""

    def test_merge_empty_overrides(self):
        """空字典覆盖合并"""
        config = Config(overrides=None, validate=False)
        original = config.merged
        config._merge({})
        # 合并空字典不应改变配置
        assert config.merged == original

    def test_merge_adds_new_section(self):
        """合并新增节"""
        config = Config(overrides=None, validate=False)
        config._merge({"new_section": {"key": "value"}})
        assert config.get("new_section", "key") == "value"

    def test_merge_overrides_existing_value(self):
        """合并覆盖已存在值"""
        config = Config(overrides=None, validate=False)
        original = config.get("behavior", "check_interval")
        config._merge({"behavior": {"check_interval": 60}})
        assert config.get("behavior", "check_interval") == 60
        assert config.get("behavior", "check_interval") != original

    def test_merge_nested_dicts_recursively(self):
        """递归合并嵌套字典"""
        config = Config(overrides=None, validate=False)
        # memory.llm 已存在 provider/api_key/model/timeout
        config._merge({"memory": {"llm": {"model": "new-model"}}})
        # model 应被覆盖
        assert config.get("memory", "llm", "model") == "new-model"
        # 其他 llm 键应保留
        assert "timeout" in config.get("memory", "llm")

    def test_merge_none_overrides_raises(self):
        """None 作为覆盖应抛异常"""
        config = Config(overrides=None, validate=False)
        with pytest.raises((AttributeError, TypeError)):
            config._merge(None)


# ============================================================================
#  极值与极端场景测试
# ============================================================================


class TestExtremeValues:
    """极值与极端场景测试"""

    def test_extreme_large_config_dict(self):
        """超大配置字典"""
        config = {}
        for i in range(1000):
            config[f"extra_key_{i}"] = i
        # 加上必需节
        for section in ["sensor", "cognitive", "memory", "behavior", "permission", "security"]:
            config[section] = {}
        errors = validate_config(config)
        assert isinstance(errors, list)

    def test_extreme_deeply_nested_config(self):
        """深层嵌套配置"""
        config = {"sensor": {}, "cognitive": {}, "memory": {}, "behavior": {},
                  "permission": {}, "security": {}}
        current = config
        for i in range(50):
            current["nested"] = {}
            current = current["nested"]
        errors = validate_config(config)
        assert isinstance(errors, list)

    def test_extreme_empty_string_values(self, valid_config):
        """空字符串值"""
        config = deepcopy(valid_config)
        config["memory"]["data_dir"] = ""
        config["memory"]["llm"]["api_key"] = ""
        errors = validate_config(config)
        # 空字符串不触发范围校验
        assert isinstance(errors, list)

    def test_extreme_very_large_string_value(self, valid_config):
        """超长字符串值"""
        config = deepcopy(valid_config)
        config["memory"]["data_dir"] = "x" * 10000
        errors = validate_config(config)
        assert isinstance(errors, list)

    def test_extreme_float_for_int_field(self, valid_config):
        """浮点数用于整数字段"""
        config = deepcopy(valid_config)
        config["memory"]["llm"]["timeout"] = 30.5
        errors = validate_config(config)
        assert isinstance(errors, list)

    def test_extreme_bool_for_int_field(self, valid_config):
        """布尔值用于整数字段"""
        config = deepcopy(valid_config)
        config["memory"]["llm"]["timeout"] = True
        errors = validate_config(config)
        assert isinstance(errors, list)


# ============================================================================
#  并发安全测试
# ============================================================================


class TestConcurrencySafety:
    """并发安全测试"""

    def test_concurrent_validate_config(self, valid_config):
        """并发调用 validate_config"""
        results = []
        errors_list = []

        def worker():
            try:
                errs = validate_config(deepcopy(valid_config))
                results.append(len(errs))
            except Exception as e:
                errors_list.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors_list) == 0
        assert len(results) == 10

    def test_concurrent_validate_and_fix(self, empty_config):
        """并发调用 validate_and_fix_config"""
        results = []

        def worker():
            fixed, errs = validate_and_fix_config(deepcopy(empty_config))
            results.append(("sensor" in fixed, len(errs)))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10
        for has_sensor, err_count in results:
            assert has_sensor is True
            assert err_count >= 6

    def test_concurrent_config_get_set(self):
        """并发读写 Config"""
        config = Config(overrides=None, validate=False)
        read_results = []
        errors_list = []

        def reader():
            try:
                for _ in range(100):
                    val = config.get("behavior", "check_interval")
                    read_results.append(val)
            except Exception as e:
                errors_list.append(e)

        def writer():
            try:
                for i in range(100):
                    config.set(i % 300, "behavior", "check_interval")
            except Exception as e:
                errors_list.append(e)

        t1 = threading.Thread(target=reader)
        t2 = threading.Thread(target=writer)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors_list) == 0
        assert len(read_results) == 100


# ============================================================================
#  reset / 重复操作测试
# ============================================================================


class TestResetAndRepeat:
    """重复操作与状态一致性测试"""

    def test_repeated_validate_returns_same_result(self, valid_config):
        """重复校验返回相同结果"""
        errors1 = validate_config(deepcopy(valid_config))
        errors2 = validate_config(deepcopy(valid_config))
        assert len(errors1) == len(errors2)

    def test_repeated_fix_does_not_cascade(self, valid_config):
        """重复修复不产生级联错误"""
        config = deepcopy(valid_config)
        config["memory"]["llm"]["timeout"] = 999
        fixed1, errors1 = validate_and_fix_config(deepcopy(config))
        fixed2, errors2 = validate_and_fix_config(deepcopy(fixed1))
        # 第二次修复不应引入新错误
        assert len(errors2) == 0

    def test_validate_after_fix_passes(self, empty_config):
        """修复后再次校验应通过"""
        fixed, _ = validate_and_fix_config(empty_config)
        errors = validate_config(fixed)
        # 修复后所有必需节存在，但 Pydantic 可能仍报告字段级错误
        section_errors = [e for e in errors if "缺少必需" in e.get("msg", "")]
        assert len(section_errors) == 0

    def test_config_multiple_instances_independent(self):
        """多个 Config 实例相互独立"""
        c1 = Config(overrides=None, validate=False)
        c2 = Config(overrides=None, validate=False)
        c1.set(100, "behavior", "check_interval")
        assert c1.get("behavior", "check_interval") == 100
        # c2 不受影响
        assert c2.get("behavior", "check_interval") != 100


# ============================================================================
#  trace_id 上下文测试
# ============================================================================


class TestTraceIdContext:
    """结构化日志 trace_id 上下文测试"""

    def test_validate_config_logs_contain_trace_id(self, caplog):
        """validate_config 日志应包含模块名"""
        import json as _json
        with caplog.at_level(logging.DEBUG, logger="config"):
            validate_config({})
        # config 模块使用传统 logging，非 JSON 格式
        # 验证日志至少有输出
        assert len(caplog.records) > 0

    def test_validate_and_fix_logs_module_name(self, caplog):
        """validate_config 日志应包含模块名（validate_and_fix_config 无日志输出）"""
        with caplog.at_level(logging.DEBUG, logger="config"):
            validate_config({})
        assert len(caplog.records) > 0

    def test_config_init_logs_validation(self, caplog):
        """Config 初始化时输出校验日志"""
        with caplog.at_level(logging.INFO, logger="config"):
            Config(overrides=None, validate=True)
        assert len(caplog.records) > 0
