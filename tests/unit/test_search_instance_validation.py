"""搜索实例校验逻辑边界测试

覆盖：
- SEARCH_INSTANCE_VALIDATION_RULES 声明式规则集（字段覆盖、必填/可选、边界值、类型错误）
- validate_search_instance 包装函数（合法配置、所有内置引擎、条件逻辑、多字段错误组合）
- BUILTIN_ENGINES 常量完整性
"""
import pytest
from agent.config_validation import (
    SEARCH_INSTANCE_VALIDATION_RULES,
    ValidationRule,
    validate_dict_against_rules,
)
from agent.server_routes.routes_config import (
    BUILTIN_ENGINES,
    validate_search_instance,
)


# ============================================================================
# 声明式规则集测试
# ============================================================================

class TestSearchInstanceValidationRules:
    """SEARCH_INSTANCE_VALIDATION_RULES 规则集结构与字段覆盖"""

    def test_rules_is_list(self):
        assert isinstance(SEARCH_INSTANCE_VALIDATION_RULES, list)

    def test_rules_non_empty(self):
        assert len(SEARCH_INSTANCE_VALIDATION_RULES) > 0

    def test_each_rule_is_validation_rule(self):
        for rule in SEARCH_INSTANCE_VALIDATION_RULES:
            assert isinstance(rule, ValidationRule)

    def test_name_rule_exists(self):
        names = [r.path for r in SEARCH_INSTANCE_VALIDATION_RULES]
        assert "name" in names

    def test_timeout_rule_exists(self):
        paths = [r.path for r in SEARCH_INSTANCE_VALIDATION_RULES]
        assert "timeout" in paths

    def test_name_rule_required(self):
        rule = next(r for r in SEARCH_INSTANCE_VALIDATION_RULES if r.path == "name")
        assert rule.required is True

    def test_timeout_rule_optional(self):
        rule = next(r for r in SEARCH_INSTANCE_VALIDATION_RULES if r.path == "timeout")
        assert rule.required is False

    def test_name_rule_error_message(self):
        rule = next(r for r in SEARCH_INSTANCE_VALIDATION_RULES if r.path == "name")
        assert "名称" in rule.error_message

    def test_timeout_rule_error_message(self):
        rule = next(r for r in SEARCH_INSTANCE_VALIDATION_RULES if r.path == "timeout")
        assert "超时" in rule.error_message

    def test_timeout_default_is_30(self):
        rule = next(r for r in SEARCH_INSTANCE_VALIDATION_RULES if r.path == "timeout")
        assert rule.default == 30

    def test_name_default_is_empty_string(self):
        rule = next(r for r in SEARCH_INSTANCE_VALIDATION_RULES if r.path == "name")
        assert rule.default == ""

    def test_name_non_empty_string_passes(self):
        errors = validate_dict_against_rules({"name": "test"}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert not any("名称" in e for e in errors)

    def test_name_empty_string_fails(self):
        errors = validate_dict_against_rules({"name": ""}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert any("名称" in e for e in errors)

    def test_name_whitespace_only_fails(self):
        errors = validate_dict_against_rules({"name": "   "}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert any("名称" in e for e in errors)

    def test_name_missing_fails(self):
        errors = validate_dict_against_rules({}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert any("名称" in e for e in errors)

    def test_name_none_fails(self):
        errors = validate_dict_against_rules({"name": None}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert any("名称" in e for e in errors)

    def test_name_non_string_fails(self):
        errors = validate_dict_against_rules({"name": 123}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert any("名称" in e for e in errors)

    def test_timeout_at_min_boundary_passes(self):
        errors = validate_dict_against_rules({"name": "x", "timeout": 1}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert not any("超时" in e for e in errors)

    def test_timeout_at_max_boundary_passes(self):
        errors = validate_dict_against_rules({"name": "x", "timeout": 300}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert not any("超时" in e for e in errors)

    def test_timeout_below_min_fails(self):
        errors = validate_dict_against_rules({"name": "x", "timeout": 0}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert any("超时" in e for e in errors)

    def test_timeout_above_max_fails(self):
        errors = validate_dict_against_rules({"name": "x", "timeout": 301}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert any("超时" in e for e in errors)

    def test_timeout_negative_fails(self):
        errors = validate_dict_against_rules({"name": "x", "timeout": -1}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert any("超时" in e for e in errors)

    def test_timeout_missing_passes(self):
        errors = validate_dict_against_rules({"name": "x"}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert not any("超时" in e for e in errors)

    def test_timeout_none_passes(self):
        errors = validate_dict_against_rules({"name": "x", "timeout": None}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert not any("超时" in e for e in errors)

    def test_timeout_string_numeric_passes(self):
        errors = validate_dict_against_rules({"name": "x", "timeout": "30"}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert not any("超时" in e for e in errors)

    def test_timeout_string_out_of_range_fails(self):
        errors = validate_dict_against_rules({"name": "x", "timeout": "999"}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert any("超时" in e for e in errors)

    def test_timeout_string_non_numeric_fails(self):
        errors = validate_dict_against_rules({"name": "x", "timeout": "abc"}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert any("超时" in e for e in errors)

    def test_timeout_float_in_range_passes(self):
        errors = validate_dict_against_rules({"name": "x", "timeout": 30.5}, SEARCH_INSTANCE_VALIDATION_RULES)
        assert not any("超时" in e for e in errors)


# ============================================================================
# validate_search_instance 包装函数测试
# ============================================================================

class TestValidateSearchInstanceWrapper:
    """validate_search_instance 混合校验（声明式 + 条件逻辑）"""

    def test_valid_minimal(self):
        assert validate_search_instance({"name": "test", "engine_type": "tavily"}) == []

    def test_valid_with_all_fields(self):
        instance = {"name": "test", "engine_type": "tavily", "timeout": 60, "api_endpoint": "https://x.com"}
        assert validate_search_instance(instance) == []

    def test_valid_custom_engine_with_endpoint(self):
        instance = {"name": "test", "engine_type": "custom", "api_endpoint": "https://api.example.com"}
        assert validate_search_instance(instance) == []

    def test_valid_custom_engine_with_all_fields(self):
        instance = {
            "name": "my_engine", "engine_type": "custom",
            "api_endpoint": "https://api.example.com", "timeout": 120,
        }
        assert validate_search_instance(instance) == []

    @pytest.mark.parametrize("engine", sorted(BUILTIN_ENGINES))
    def test_all_builtin_engines_valid(self, engine):
        assert validate_search_instance({"name": "x", "engine_type": engine}) == []

    def test_empty_name(self):
        errors = validate_search_instance({"name": "", "engine_type": "tavily"})
        assert "名称不能为空" in errors

    def test_missing_name(self):
        errors = validate_search_instance({"engine_type": "tavily"})
        assert "名称不能为空" in errors

    def test_whitespace_name(self):
        errors = validate_search_instance({"name": "  ", "engine_type": "tavily"})
        assert "名称不能为空" in errors

    def test_empty_engine_type(self):
        errors = validate_search_instance({"name": "test", "engine_type": ""})
        assert "引擎类型不能为空" in errors

    def test_missing_engine_type(self):
        errors = validate_search_instance({"name": "test"})
        assert "引擎类型不能为空" in errors

    def test_unknown_engine(self):
        errors = validate_search_instance({"name": "test", "engine_type": "unknown_engine"})
        assert any("未知" in e for e in errors)

    def test_empty_engine_does_not_produce_unknown_error(self):
        errors = validate_search_instance({"name": "test", "engine_type": ""})
        assert not any("未知" in e for e in errors)

    def test_custom_engine_no_endpoint(self):
        errors = validate_search_instance({"name": "test", "engine_type": "custom"})
        assert any("API" in e for e in errors)

    def test_custom_engine_empty_endpoint(self):
        errors = validate_search_instance({"name": "test", "engine_type": "custom", "api_endpoint": ""})
        assert any("API" in e for e in errors)

    def test_custom_engine_with_endpoint(self):
        errors = validate_search_instance({
            "name": "test", "engine_type": "custom", "api_endpoint": "https://api.example.com"
        })
        assert errors == []

    def test_builtin_engine_does_not_require_endpoint(self):
        errors = validate_search_instance({"name": "test", "engine_type": "bing"})
        assert not any("API" in e for e in errors)

    def test_timeout_too_low(self):
        errors = validate_search_instance({"name": "test", "engine_type": "tavily", "timeout": 0})
        assert any("超时" in e for e in errors)

    def test_timeout_too_high(self):
        errors = validate_search_instance({"name": "test", "engine_type": "tavily", "timeout": 301})
        assert any("超时" in e for e in errors)

    def test_timeout_valid(self):
        errors = validate_search_instance({"name": "test", "engine_type": "tavily", "timeout": 30})
        assert errors == []

    def test_timeout_boundary_min(self):
        errors = validate_search_instance({"name": "test", "engine_type": "tavily", "timeout": 1})
        assert not any("超时" in e for e in errors)

    def test_timeout_boundary_max(self):
        errors = validate_search_instance({"name": "test", "engine_type": "tavily", "timeout": 300})
        assert not any("超时" in e for e in errors)

    def test_timeout_missing_uses_default(self):
        errors = validate_search_instance({"name": "test", "engine_type": "tavily"})
        assert not any("超时" in e for e in errors)

    def test_multiple_errors_name_and_engine(self):
        errors = validate_search_instance({"name": "", "engine_type": ""})
        assert "名称不能为空" in errors
        assert "引擎类型不能为空" in errors

    def test_multiple_errors_name_and_timeout(self):
        errors = validate_search_instance({"name": "", "engine_type": "tavily", "timeout": 0})
        assert "名称不能为空" in errors
        assert any("超时" in e for e in errors)

    def test_multiple_errors_all_fields(self):
        errors = validate_search_instance({"name": "", "engine_type": "unknown", "timeout": 999})
        assert "名称不能为空" in errors
        assert any("未知" in e for e in errors)
        assert any("超时" in e for e in errors)

    def test_custom_engine_multiple_errors(self):
        errors = validate_search_instance({"name": "", "engine_type": "custom"})
        assert "名称不能为空" in errors
        assert any("API" in e for e in errors)

    def test_custom_engine_all_errors(self):
        errors = validate_search_instance({"name": "", "engine_type": "custom", "timeout": 0})
        assert "名称不能为空" in errors
        assert any("API" in e for e in errors)
        assert any("超时" in e for e in errors)

    def test_returns_list(self):
        result = validate_search_instance({"name": "x", "engine_type": "tavily"})
        assert isinstance(result, list)

    def test_returns_list_of_strings(self):
        result = validate_search_instance({"name": "", "engine_type": ""})
        assert all(isinstance(e, str) for e in result)

    def test_empty_dict(self):
        errors = validate_search_instance({})
        assert "名称不能为空" in errors
        assert "引擎类型不能为空" in errors

    def test_none_instance_raises(self):
        with pytest.raises(AttributeError):
            validate_search_instance(None)


# ============================================================================
# BUILTIN_ENGINES 常量完整性
# ============================================================================

class TestBuiltinEngines:
    """BUILTIN_ENGINES 集合完整性"""

    def test_is_set(self):
        assert isinstance(BUILTIN_ENGINES, set)

    def test_contains_tavily(self):
        assert "tavily" in BUILTIN_ENGINES

    def test_contains_firecrawl(self):
        assert "firecrawl" in BUILTIN_ENGINES

    def test_does_not_contain_custom(self):
        assert "custom" not in BUILTIN_ENGINES
