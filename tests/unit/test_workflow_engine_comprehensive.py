#!/usr/bin/env python3
"""WorkflowEngine 综合单元测试

【生成日志摘要】
- 生成时间戳: 2026-07-02
- 内容描述: workflow_engine 模块全量单元测试（engine/matcher/registry/builtin_rules）
- 生成参数: 覆盖 WorkflowResult/Rule/RuleRegistry/keyword_match/regex_match/function_match/RuleMatcher/WorkflowEngine/8条内置规则
- 模型配置: GLM-5.2
- 关键状态变化: 新增 ~80 个测试，目标覆盖率 95%+
"""

import re
import pytest
from unittest.mock import MagicMock

from agent.workflow_engine.engine import WorkflowEngine, WorkflowResult
from agent.workflow_engine.registry import Rule, RuleRegistry
from agent.workflow_engine.matcher import keyword_match, regex_match, function_match, RuleMatcher
from agent.workflow_engine.builtin_rules import (
    register_builtin_rules,
    _safe_calc,
    _current_time_fmt,
    _current_date_fmt,
    _current_weekday,
    _greeting_time,
)


# ═══════════════════════════════════════════════════════════════
# WorkflowResult 数据类测试
# ═══════════════════════════════════════════════════════════════


class TestWorkflowResult:
    def test_defaults(self):
        r = WorkflowResult()
        assert r.matched is False
        assert r.rule_name == ""
        assert r.intent == ""
        assert r.output == ""
        assert r.data is None
        assert r.confidence == 1.0
        assert r.execution_time_ms == 0.0

    def test_custom_values(self):
        r = WorkflowResult(
            matched=True,
            rule_name="test_rule",
            intent="greeting",
            output="hello",
            data={"key": "value"},
            confidence=0.95,
            execution_time_ms=12.5,
        )
        assert r.matched is True
        assert r.rule_name == "test_rule"
        assert r.intent == "greeting"
        assert r.output == "hello"
        assert r.data == {"key": "value"}
        assert r.confidence == 0.95
        assert r.execution_time_ms == 12.5

    def test_data_can_be_any_type(self):
        r = WorkflowResult(data=[1, 2, 3])
        assert r.data == [1, 2, 3]

        r2 = WorkflowResult(data="string")
        assert r2.data == "string"

        r3 = WorkflowResult(data=42)
        assert r3.data == 42


# ═══════════════════════════════════════════════════════════════
# Rule 数据类测试
# ═══════════════════════════════════════════════════════════════


class TestRule:
    def test_defaults(self):
        rule = Rule(
            name="test",
            description="desc",
            match_fn=lambda _: True,
            execute_fn=lambda _: "ok",
        )
        assert rule.name == "test"
        assert rule.description == "desc"
        assert rule.priority == 50
        assert rule.category == "general"
        assert rule.enabled is True

    def test_custom_values(self):
        rule = Rule(
            name="r",
            description="d",
            match_fn=lambda _: True,
            execute_fn=lambda _: "ok",
            priority=100,
            category="query",
            enabled=False,
        )
        assert rule.priority == 100
        assert rule.category == "query"
        assert rule.enabled is False

    def test_callable_fields(self):
        rule = Rule(
            name="r",
            description="d",
            match_fn=lambda t: "hello" in t,
            execute_fn=lambda t: f"echo:{t}",
        )
        assert rule.match_fn("hello world") is True
        assert rule.match_fn("foo") is False
        assert rule.execute_fn("test") == "echo:test"


# ═══════════════════════════════════════════════════════════════
# RuleRegistry 测试
# ═══════════════════════════════════════════════════════════════


class TestRuleRegistry:
    def test_init_empty(self):
        reg = RuleRegistry()
        assert reg.count() == 0
        assert reg.get_enabled() == []

    def test_register_single(self):
        reg = RuleRegistry()
        rule = Rule("r1", "desc", lambda _: True, lambda _: "ok")
        reg.register(rule)
        assert reg.count() == 1

    def test_register_multiple(self):
        reg = RuleRegistry()
        for i in range(5):
            reg.register(Rule(f"r{i}", "", lambda _: True, lambda _: "ok"))
        assert reg.count() == 5

    def test_register_sorts_by_priority(self):
        reg = RuleRegistry()
        reg.register(Rule("low", "", lambda _: True, lambda _: "low", priority=10))
        reg.register(Rule("high", "", lambda _: True, lambda _: "high", priority=100))
        reg.register(Rule("mid", "", lambda _: True, lambda _: "mid", priority=50))
        enabled = reg.get_enabled()
        assert enabled[0].name == "high"
        assert enabled[1].name == "mid"
        assert enabled[2].name == "low"

    def test_register_same_priority_preserves_order(self):
        reg = RuleRegistry()
        reg.register(Rule("first", "", lambda _: True, lambda _: "1", priority=50))
        reg.register(Rule("second", "", lambda _: True, lambda _: "2", priority=50))
        enabled = reg.get_enabled()
        # Python sort is stable，相同优先级保持插入顺序
        assert enabled[0].name == "first"
        assert enabled[1].name == "second"

    def test_unregister_existing(self):
        reg = RuleRegistry()
        reg.register(Rule("r1", "", lambda _: True, lambda _: "ok"))
        reg.unregister("r1")
        assert reg.count() == 0

    def test_unregister_nonexistent_no_error(self):
        reg = RuleRegistry()
        reg.unregister("not_exist")
        assert reg.count() == 0

    def test_unregister_only_removes_matching(self):
        reg = RuleRegistry()
        reg.register(Rule("r1", "", lambda _: True, lambda _: "ok"))
        reg.register(Rule("r2", "", lambda _: True, lambda _: "ok"))
        reg.unregister("r1")
        assert reg.count() == 1
        assert reg.get_enabled()[0].name == "r2"

    def test_get_enabled_excludes_disabled(self):
        reg = RuleRegistry()
        reg.register(Rule("enabled", "", lambda _: True, lambda _: "ok", enabled=True))
        reg.register(Rule("disabled", "", lambda _: True, lambda _: "ok", enabled=False))
        enabled = reg.get_enabled()
        assert len(enabled) == 1
        assert enabled[0].name == "enabled"

    def test_get_enabled_returns_all_enabled(self):
        reg = RuleRegistry()
        for i in range(5):
            reg.register(Rule(f"r{i}", "", lambda _: True, lambda _: "ok"))
        assert len(reg.get_enabled()) == 5

    def test_get_by_category(self):
        reg = RuleRegistry()
        reg.register(Rule("r1", "", lambda _: True, lambda _: "ok", category="query"))
        reg.register(Rule("r2", "", lambda _: True, lambda _: "ok", category="greeting"))
        reg.register(Rule("r3", "", lambda _: True, lambda _: "ok", category="query"))
        query_rules = reg.get_by_category("query")
        assert len(query_rules) == 2

    def test_get_by_category_excludes_disabled(self):
        reg = RuleRegistry()
        reg.register(Rule("r1", "", lambda _: True, lambda _: "ok", category="query", enabled=True))
        reg.register(Rule("r2", "", lambda _: True, lambda _: "ok", category="query", enabled=False))
        query_rules = reg.get_by_category("query")
        assert len(query_rules) == 1

    def test_get_by_category_empty(self):
        reg = RuleRegistry()
        assert reg.get_by_category("nonexistent") == []

    def test_decorator_registers_rule(self):
        reg = RuleRegistry()

        @reg.decorator(name="custom", description="custom rule", priority=80)
        def my_handler(text):
            return f"handled:{text}"

        assert reg.count() == 1
        rule = reg.get_enabled()[0]
        assert rule.name == "custom"
        assert rule.priority == 80

    def test_decorator_uses_function_name_if_no_name(self):
        reg = RuleRegistry()

        @reg.decorator()
        def my_func(text):
            return "ok"

        assert reg.get_enabled()[0].name == "my_func"

    def test_decorator_uses_docstring_if_no_description(self):
        reg = RuleRegistry()

        @reg.decorator()
        def documented_func(text):
            """This is a docstring"""
            return "ok"

        assert reg.get_enabled()[0].description == "This is a docstring"

    def test_clear(self):
        reg = RuleRegistry()
        for i in range(5):
            reg.register(Rule(f"r{i}", "", lambda _: True, lambda _: "ok"))
        reg.clear()
        assert reg.count() == 0
        assert reg.get_enabled() == []

    def test_clear_when_empty(self):
        reg = RuleRegistry()
        reg.clear()
        assert reg.count() == 0


# ═══════════════════════════════════════════════════════════════
# keyword_match 测试
# ═══════════════════════════════════════════════════════════════


class TestKeywordMatch:
    def test_single_keyword_match(self):
        fn = keyword_match(["hello"])
        assert fn("hello world") is True

    def test_single_keyword_no_match(self):
        fn = keyword_match(["hello"])
        assert fn("foo bar") is False

    def test_multiple_keywords_any_match(self):
        fn = keyword_match(["hello", "world", "foo"])
        assert fn("hello") is True
        assert fn("world") is True
        assert fn("foo") is True
        assert fn("bar") is False

    def test_case_insensitive_default(self):
        fn = keyword_match(["hello"])
        assert fn("HELLO") is True
        assert fn("Hello") is True
        assert fn("HeLLo") is True

    def test_case_sensitive(self):
        fn = keyword_match(["Hello"], case_sensitive=True)
        assert fn("Hello") is True
        assert fn("hello") is False

    def test_chinese_keywords(self):
        fn = keyword_match(["你好", "再见"])
        assert fn("你好世界") is True
        assert fn("说再见") is True
        assert fn("早安") is False

    def test_empty_keywords_filtered(self):
        """防御性：空字符串关键词应被过滤，避免 "" in text 始终为 True"""
        fn = keyword_match(["", "hello", ""])
        assert fn("foo bar") is False  # 空字符串不应匹配所有文本
        assert fn("hello") is True

    def test_whitespace_keywords_filtered(self):
        """防御性：纯空白关键词应被过滤"""
        fn = keyword_match(["   ", "hello"])
        assert fn("foo bar") is False
        assert fn("hello") is True

    def test_all_empty_keywords(self):
        fn = keyword_match(["", "  ", ""])
        assert fn("anything") is False

    def test_empty_keyword_list(self):
        fn = keyword_match([])
        assert fn("anything") is False

    def test_keyword_in_middle_of_text(self):
        fn = keyword_match(["hello"])
        assert fn("say hello now") is True

    def test_partial_match(self):
        fn = keyword_match(["he"])
        assert fn("hello") is True  # "he" in "hello" is True


# ═══════════════════════════════════════════════════════════════
# regex_match 测试
# ═══════════════════════════════════════════════════════════════


class TestRegexMatch:
    def test_simple_pattern_match(self):
        fn = regex_match(r"\d+")
        assert fn("abc123") is True
        assert fn("abc") is False

    def test_anchored_pattern(self):
        fn = regex_match(r"^\d+$")
        assert fn("12345") is True
        assert fn("12a45") is False

    def test_case_insensitive_default(self):
        fn = regex_match(r"hello")
        assert fn("HELLO") is True
        assert fn("Hello") is True

    def test_case_sensitive_flag(self):
        fn = regex_match(r"hello", flags=0)
        assert fn("hello") is True
        assert fn("HELLO") is False

    def test_special_pattern(self):
        fn = regex_match(r"^[\d\s\+\-\*\/\(\)\.]+$")
        assert fn("1 + 2 * 3") is True
        assert fn("1 + abc") is False

    def test_email_pattern(self):
        fn = regex_match(r"\w+@\w+\.\w+")
        assert fn("user@example.com") is True
        assert fn("not an email") is False


# ═══════════════════════════════════════════════════════════════
# function_match 测试
# ═══════════════════════════════════════════════════════════════


class TestFunctionMatch:
    def test_returns_original_function(self):
        def my_fn(text):
            return len(text) > 5

        result = function_match(my_fn)
        assert result is my_fn

    def test_function_works(self):
        fn = function_match(lambda t: len(t) > 5)
        assert fn("hello world") is True
        assert fn("hi") is False

    def test_lambda(self):
        fn = function_match(lambda t: "x" in t)
        assert fn("xyz") is True
        assert fn("abc") is False


# ═══════════════════════════════════════════════════════════════
# RuleMatcher.match_text 测试
# ═══════════════════════════════════════════════════════════════


class TestRuleMatcher:
    def test_string_pattern_match(self):
        assert RuleMatcher.match_text("hello world", ["hello"]) is True

    def test_string_pattern_no_match(self):
        assert RuleMatcher.match_text("foo bar", ["hello"]) is False

    def test_string_pattern_case_insensitive(self):
        assert RuleMatcher.match_text("HELLO", ["hello"]) is True

    def test_empty_string_pattern_skipped(self):
        """防御性：空字符串 pattern 应被跳过"""
        assert RuleMatcher.match_text("anything", [""]) is False

    def test_compiled_pattern_match(self):
        p = re.compile(r"\d+")
        assert RuleMatcher.match_text("abc123", [p]) is True

    def test_compiled_pattern_no_match(self):
        p = re.compile(r"\d+")
        assert RuleMatcher.match_text("abc", [p]) is False

    def test_callable_pattern_match(self):
        assert RuleMatcher.match_text("hello", [lambda t: "hel" in t]) is True

    def test_callable_pattern_no_match(self):
        assert RuleMatcher.match_text("hello", [lambda t: "xyz" in t]) is False

    def test_mixed_patterns(self):
        p = re.compile(r"\d+")
        fn = lambda t: "hello" in t
        assert RuleMatcher.match_text("hello123", ["foo", p, fn]) is True

    def test_mixed_patterns_first_match_wins(self):
        p = re.compile(r"\d+")
        assert RuleMatcher.match_text("123", ["foo", p]) is True

    def test_empty_patterns(self):
        assert RuleMatcher.match_text("anything", []) is False

    def test_multiple_string_patterns(self):
        assert RuleMatcher.match_text("hello", ["foo", "bar", "hello"]) is True


# ═══════════════════════════════════════════════════════════════
# WorkflowEngine 测试
# ═══════════════════════════════════════════════════════════════


class TestWorkflowEngine:
    def test_init(self):
        engine = WorkflowEngine()
        assert engine.registry is not None
        assert engine.registry.count() == 0

    def test_try_match_no_rules(self):
        engine = WorkflowEngine()
        result = engine.try_match("hello")
        assert result.matched is False
        assert result.rule_name == ""
        assert result.output == ""

    def test_try_match_match_success(self):
        engine = WorkflowEngine()
        engine.registry.register(
            Rule("test", "", lambda t: "hello" in t, lambda t: f"echo:{t}")
        )
        result = engine.try_match("hello world")
        assert result.matched is True
        assert result.rule_name == "test"
        assert result.output == "echo:hello world"
        assert result.confidence == 1.0

    def test_try_match_no_match(self):
        engine = WorkflowEngine()
        engine.registry.register(
            Rule("test", "", lambda t: "hello" in t, lambda t: "ok")
        )
        result = engine.try_match("foo bar")
        assert result.matched is False

    def test_try_match_execution_time_recorded(self):
        engine = WorkflowEngine()
        engine.registry.register(
            Rule("test", "", lambda t: True, lambda t: "ok")
        )
        result = engine.try_match("anything")
        assert result.matched is True
        assert result.execution_time_ms >= 0.0

    def test_try_match_priority_order(self):
        engine = WorkflowEngine()
        engine.registry.register(
            Rule("low", "", lambda t: "a" in t, lambda t: "low", priority=10)
        )
        engine.registry.register(
            Rule("high", "", lambda t: "a" in t, lambda t: "high", priority=100)
        )
        result = engine.try_match("a")
        assert result.rule_name == "high"

    def test_try_match_disabled_rule_skipped(self):
        engine = WorkflowEngine()
        engine.registry.register(
            Rule("disabled", "", lambda t: True, lambda t: "disabled", enabled=False)
        )
        engine.registry.register(
            Rule("enabled", "", lambda t: True, lambda t: "enabled")
        )
        result = engine.try_match("test")
        assert result.rule_name == "enabled"

    def test_try_match_match_fn_exception_handled(self):
        engine = WorkflowEngine()
        engine.registry.register(
            Rule("bad", "", lambda t: (_ for _ in ()).throw(RuntimeError("boom")), lambda t: "ok")
        )
        engine.registry.register(
            Rule("good", "", lambda t: True, lambda t: "good")
        )
        result = engine.try_match("test")
        assert result.rule_name == "good"

    def test_try_match_execute_fn_exception_handled(self):
        engine = WorkflowEngine()
        engine.registry.register(
            Rule("bad_exec", "", lambda t: True, lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
        )
        engine.registry.register(
            Rule("good_exec", "", lambda t: True, lambda t: "good")
        )
        result = engine.try_match("test")
        # bad_exec 匹配但 execute 抛异常 → continue → good_exec 匹配
        assert result.rule_name == "good_exec"

    def test_try_match_first_match_wins(self):
        engine = WorkflowEngine()
        engine.registry.register(
            Rule("first", "", lambda t: True, lambda t: "first", priority=100)
        )
        engine.registry.register(
            Rule("second", "", lambda t: True, lambda t: "second", priority=90)
        )
        result = engine.try_match("test")
        assert result.rule_name == "first"
        assert result.output == "first"

    def test_match_is_alias_of_try_match(self):
        engine = WorkflowEngine()
        engine.registry.register(
            Rule("test", "", lambda t: "hello" in t, lambda t: "ok")
        )
        result = engine.match("hello")
        assert result.matched is True
        assert result.rule_name == "test"

    def test_intent_equals_rule_name(self):
        engine = WorkflowEngine()
        engine.registry.register(
            Rule("my_rule", "", lambda t: True, lambda t: "ok")
        )
        result = engine.try_match("test")
        assert result.intent == "my_rule"


# ═══════════════════════════════════════════════════════════════
# 内置规则测试
# ═══════════════════════════════════════════════════════════════


class TestBuiltinRules:
    def test_register_builtin_rules_count(self):
        reg = RuleRegistry()
        register_builtin_rules(reg)
        assert reg.count() == 8

    def test_builtin_rules_sorted_by_priority(self):
        reg = RuleRegistry()
        register_builtin_rules(reg)
        enabled = reg.get_enabled()
        # 第一条应该是 check_time 或 check_date（priority=100）
        assert enabled[0].priority >= 100

    def test_check_time_rule_matches(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        result = engine.try_match("现在几点")
        assert result.matched is True
        assert result.rule_name == "check_time"

    def test_check_time_rule_variants(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        for text in ["当前时间", "几点了", "什么时间", "几点钟"]:
            result = engine.try_match(text)
            assert result.matched is True, f"应匹配 check_time: {text}"
            assert result.rule_name == "check_time"

    def test_check_date_rule_matches(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        result = engine.try_match("今天几号")
        assert result.matched is True
        assert result.rule_name == "check_date"

    def test_check_date_rule_variants(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        for text in ["今天日期", "今天周几", "今天星期几", "什么日子"]:
            result = engine.try_match(text)
            assert result.matched is True, f"应匹配 check_date: {text}"
            assert result.rule_name == "check_date"

    def test_check_health_rule_matches(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        result = engine.try_match("你还好吗")
        assert result.matched is True
        assert result.rule_name == "check_health"

    def test_check_health_rule_variants(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        for text in ["状态", "在吗", "在不在", "hello", "hi", "你好"]:
            result = engine.try_match(text)
            assert result.matched is True, f"应匹配 check_health: {text}"
            assert result.rule_name == "check_health"

    def test_simple_calc_rule_matches(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        result = engine.try_match("1 + 2")
        assert result.matched is True
        assert result.rule_name == "simple_calc"

    def test_simple_calc_result(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        result = engine.try_match("3 + 4")
        assert result.matched is True
        assert "7" in result.output

    def test_greeting_rule_matches(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        result = engine.try_match("早上好")
        assert result.matched is True
        assert result.rule_name == "greeting"

    def test_greeting_rule_variants(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        # "你好" 同时在 check_health(priority=90) 和 greeting(priority=80) 中，匹配 check_health
        for text in ["下午好", "晚上好", "大家好"]:
            result = engine.try_match(text)
            assert result.matched is True, f"应匹配 greeting: {text}"
            assert result.rule_name == "greeting"

    def test_farewell_rule_matches(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        result = engine.try_match("再见")
        assert result.matched is True
        assert result.rule_name == "farewell"

    def test_farewell_rule_variants(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        for text in ["拜拜", "bye", "goodbye", "下次见", "明天见"]:
            result = engine.try_match(text)
            assert result.matched is True, f"应匹配 farewell: {text}"
            assert result.rule_name == "farewell"

    def test_thanks_rule_matches(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        result = engine.try_match("谢谢")
        assert result.matched is True
        assert result.rule_name == "thanks"

    def test_thanks_rule_variants(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        for text in ["感谢", "多谢", "thank", "thanks", "thx"]:
            result = engine.try_match(text)
            assert result.matched is True, f"应匹配 thanks: {text}"
            assert result.rule_name == "thanks"

    def test_confirmation_rule_matches(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        result = engine.try_match("好的")
        assert result.matched is True
        assert result.rule_name == "confirmation"

    def test_confirmation_rule_variants(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        for text in ["可以", "明白", "懂了", "知道了", "收到"]:
            result = engine.try_match(text)
            assert result.matched is True, f"应匹配 confirmation: {text}"
            assert result.rule_name == "confirmation"

    def test_no_match_for_unknown_input(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        result = engine.try_match("这是一段未知的话xyz123")
        assert result.matched is False


# ═══════════════════════════════════════════════════════════════
# _safe_calc 辅助函数测试
# ═══════════════════════════════════════════════════════════════


class TestSafeCalc:
    def test_simple_addition(self):
        result = _safe_calc("1 + 2")
        assert "3" in result

    def test_simple_subtraction(self):
        result = _safe_calc("10 - 4")
        assert "6" in result

    def test_simple_multiplication(self):
        result = _safe_calc("3 * 4")
        assert "12" in result

    def test_simple_division(self):
        result = _safe_calc("10 / 2")
        assert "5" in result

    def test_complex_expression(self):
        result = _safe_calc("(1 + 2) * 3")
        assert "9" in result

    def test_decimal_numbers(self):
        result = _safe_calc("1.5 + 2.5")
        assert "4" in result

    def test_invalid_expression_letters(self):
        result = _safe_calc("abc")
        assert "无法计算" in result

    def test_division_by_zero(self):
        result = _safe_calc("1 / 0")
        # 除以零会抛异常，被捕获后返回错误信息
        assert "计算错误" in result or "无法计算" in result

    def test_empty_string(self):
        result = _safe_calc("")
        # 空字符串不匹配正则 ^[\d\s\+\-\*\/\(\)\.]+$ → 返回"无法计算"
        assert "无法计算" in result

    def test_whitespace_only(self):
        result = _safe_calc("   ")
        assert "无法计算" in result


# ═══════════════════════════════════════════════════════════════
# 时间/日期辅助函数测试
# ═══════════════════════════════════════════════════════════════


class TestTimeHelpers:
    def test_current_time_fmt(self):
        result = _current_time_fmt()
        # 格式 HH:MM
        assert len(result) == 5
        assert ":" in result
        parts = result.split(":")
        assert len(parts) == 2
        assert 0 <= int(parts[0]) <= 23
        assert 0 <= int(parts[1]) <= 59

    def test_current_date_fmt(self):
        result = _current_date_fmt()
        # 格式 YYYY-MM-DD
        assert len(result) == 10
        assert result[4] == "-"
        assert result[7] == "-"

    def test_current_weekday(self):
        result = _current_weekday()
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        assert result in weekdays

    def test_greeting_time_morning_range(self):
        # 无法直接控制时间，但可验证返回值在预期集合内
        result = _greeting_time()
        valid_greetings = ["凌晨好", "早上好", "中午好", "下午好", "晚上好"]
        assert result in valid_greetings


# ═══════════════════════════════════════════════════════════════
# 集成测试
# ═══════════════════════════════════════════════════════════════


class TestIntegration:
    def test_full_workflow_with_builtin_rules(self):
        """完整工作流：注册内置规则 → 匹配 → 执行"""
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)

        # 时间查询
        result = engine.try_match("现在几点")
        assert result.matched is True
        assert ":" in result.output  # 时间格式包含冒号

        # 问候
        result = engine.try_match("你好")
        assert result.matched is True

        # 计算
        result = engine.try_match("2 + 3")
        assert result.matched is True
        assert "5" in result.output

    def test_custom_rule_with_builtin(self):
        """自定义规则与内置规则共存"""
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)

        # 添加自定义规则（高优先级）
        engine.registry.register(
            Rule("custom_high", "", lambda t: "custom" in t,
                 lambda t: "custom response", priority=200)
        )

        result = engine.try_match("custom command")
        assert result.rule_name == "custom_high"

    def test_multiple_engines_independent(self):
        engine1 = WorkflowEngine()
        engine2 = WorkflowEngine()
        register_builtin_rules(engine1.registry)

        # engine2 不应受影响
        assert engine1.registry.count() == 8
        assert engine2.registry.count() == 0
