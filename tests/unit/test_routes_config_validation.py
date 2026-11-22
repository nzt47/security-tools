"""路由配置验证函数测试"""
import pytest
from agent.config_validation import (
    BUILTIN_ENGINES,
    normalize_engine_type,
    validate_search_instance,
)


class TestValidateSearchInstance:
    """搜索实例配置验证测试"""

    def test_valid_minimal(self):
        errors = validate_search_instance({"name": "test", "engine_type": "tavily"})
        assert errors == []

    def test_empty_name(self):
        errors = validate_search_instance({"name": "", "engine_type": "tavily"})
        assert "名称不能为空" in errors

    def test_missing_name(self):
        errors = validate_search_instance({"engine_type": "tavily"})
        assert "名称不能为空" in errors

    def test_empty_engine_type(self):
        errors = validate_search_instance({"name": "test", "engine_type": ""})
        assert "引擎类型不能为空" in errors

    def test_unknown_engine(self):
        errors = validate_search_instance({"name": "test", "engine_type": "unknown_engine"})
        assert any("未知" in e for e in errors)
        # 报错要列出可选值（否则调用方只知道"未知"，不知道该填什么）
        assert any("tavily" in e and "custom" in e for e in errors)

    def test_unknown_engine_case_sensitive_here_but_route_normalizes(self):
        """校验函数本身是精确匹配；大小写由路由入口 normalize_engine_type 归一化。"""
        errors = validate_search_instance({"name": "test", "engine_type": "Tavily"})
        assert any("未知" in e for e in errors)
        assert validate_search_instance(
            {"name": "test", "engine_type": normalize_engine_type("  Tavily ")}
        ) == []

    def test_custom_engine_no_endpoint(self):
        errors = validate_search_instance({"name": "test", "engine_type": "custom"})
        assert any("API" in e for e in errors)

    def test_custom_engine_with_endpoint(self):
        errors = validate_search_instance({
            "name": "test", "engine_type": "custom",
            "api_endpoint": "https://api.example.com"
        })
        assert errors == []

    def test_timeout_too_low(self):
        errors = validate_search_instance({
            "name": "test", "engine_type": "tavily", "timeout": 0
        })
        assert any("超时" in e for e in errors)

    def test_timeout_too_high(self):
        errors = validate_search_instance({
            "name": "test", "engine_type": "tavily", "timeout": 301
        })
        assert any("超时" in e for e in errors)

    def test_timeout_valid(self):
        errors = validate_search_instance({
            "name": "test", "engine_type": "tavily", "timeout": 30
        })
        assert errors == []

    def test_builtin_engines_defined(self):
        assert "tavily" in BUILTIN_ENGINES
        assert "firecrawl" in BUILTIN_ENGINES
        assert "bing" in BUILTIN_ENGINES
        assert "google" in BUILTIN_ENGINES
        assert "custom" not in BUILTIN_ENGINES

class TestNormalizeEngineType:
    """engine_type 入口归一化（2026-11-22：界面/API 常写 Tavily、Bing）"""

    def test_lowercases_and_strips(self):
        assert normalize_engine_type('  Tavily ') == 'tavily'
        assert normalize_engine_type('Bing') == 'bing'
        assert normalize_engine_type('CUSTOM') == 'custom'

    def test_empty_values(self):
        assert normalize_engine_type(None) == ''
        assert normalize_engine_type('   ') == ''
