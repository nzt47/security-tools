"""Workflow Engine 模块测试"""

import pytest
from agent.workflow_engine.engine import WorkflowEngine, WorkflowResult
from agent.workflow_engine.registry import Rule, RuleRegistry
from agent.workflow_engine.matcher import keyword_match, regex_match, function_match
from agent.workflow_engine.builtin_rules import register_builtin_rules


class TestRuleRegistry:
    def test_register_and_count(self):
        reg = RuleRegistry()
        assert reg.count() == 0
        reg.register(Rule("test", "测试", lambda _: True, lambda _: "ok"))
        assert reg.count() == 1

    def test_unregister(self):
        reg = RuleRegistry()
        reg.register(Rule("test", "测试", lambda _: True, lambda _: "ok"))
        reg.unregister("test")
        assert reg.count() == 0

    def test_enabled_only(self):
        reg = RuleRegistry()
        reg.register(Rule("a", "", lambda _: True, lambda _: "a"))
        reg.register(Rule("b", "", lambda _: True, lambda _: "b", enabled=False))
        assert len(reg.get_enabled()) == 1

    def test_priority_order(self):
        reg = RuleRegistry()
        reg.register(Rule("low", "", lambda _: True, lambda _: "low", priority=10))
        reg.register(Rule("high", "", lambda _: True, lambda _: "high", priority=100))
        enabled = reg.get_enabled()
        assert enabled[0].name == "high"


class TestWorkflowEngine:
    def test_no_match(self):
        engine = WorkflowEngine()
        engine.registry.register(Rule("test", "", lambda t: "hello" in t, lambda t: "world"))
        result = engine.try_match("foo bar")
        assert result is None

    def test_match(self):
        engine = WorkflowEngine()
        engine.registry.register(Rule("test", "", lambda t: "hello" in t, lambda t: "world"))
        result = engine.try_match("say hello")
        assert result is not None
        assert result.matched
        assert result.rule_name == "test"
        assert result.output == "world"

    def test_builtin_rules(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        assert engine.registry.count() == 8

    def test_builtin_time_match(self):
        engine = WorkflowEngine()
        register_builtin_rules(engine.registry)
        result = engine.try_match("现在几点")
        assert result is not None
        assert result.rule_name == "check_time"


class TestMatcher:
    def test_keyword_match(self):
        fn = keyword_match(["hello", "world"])
        assert fn("hello there")
        assert fn("big world")
        assert not fn("foo bar")

    def test_regex_match(self):
        fn = regex_match(r"^\d+$")
        assert fn("123")
        assert not fn("abc")

    def test_function_match(self):
        fn = function_match(lambda t: len(t) > 5)
        assert fn("hello world")
        assert not fn("hi")
