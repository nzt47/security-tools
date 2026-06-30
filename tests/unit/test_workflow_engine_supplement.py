"""Workflow Engine 补充测试"""
import pytest
from agent.workflow_engine.engine import WorkflowEngine, WorkflowResult
from agent.workflow_engine.registry import RuleRegistry, Rule
from agent.workflow_engine.matcher import keyword_match, regex_match


class TestRuleRegistry:
    """RuleRegistry 测试"""

    def test_init(self):
        r = RuleRegistry()
        assert r.count() == 0

    def test_register(self):
        r = RuleRegistry()
        rule = Rule(name="test", description="测试", match_fn=lambda x: True, execute_fn=lambda x: "done")
        r.register(rule)
        assert r.count() == 1

    def test_register_sorts_by_priority(self):
        r = RuleRegistry()
        low = Rule(name="low", description="", match_fn=lambda x: True, execute_fn=lambda x: "l", priority=10)
        high = Rule(name="high", description="", match_fn=lambda x: True, execute_fn=lambda x: "h", priority=90)
        r.register(low)
        r.register(high)
        enabled = r.get_enabled()
        assert enabled[0].name == "high"

    def test_unregister(self):
        r = RuleRegistry()
        rule = Rule(name="to_remove", description="", match_fn=lambda x: True, execute_fn=lambda x: "")
        r.register(rule)
        r.unregister("to_remove")
        assert r.count() == 0

    def test_get_enabled(self):
        r = RuleRegistry()
        r.register(Rule(name="disabled", description="", match_fn=lambda x: True, execute_fn=lambda x: "", enabled=False))
        r.register(Rule(name="enabled", description="", match_fn=lambda x: True, execute_fn=lambda x: ""))
        assert len(r.get_enabled()) == 1

    def test_get_by_category(self):
        r = RuleRegistry()
        r.register(Rule(name="a", description="", match_fn=lambda x: True, execute_fn=lambda x: "", category="chat"))
        r.register(Rule(name="b", description="", match_fn=lambda x: True, execute_fn=lambda x: "", category="system"))
        assert len(r.get_by_category("chat")) == 1
        assert len(r.get_by_category("general")) == 0

    def test_clear(self):
        r = RuleRegistry()
        r.register(Rule(name="a", description="", match_fn=lambda x: True, execute_fn=lambda x: ""))
        r.clear()
        assert r.count() == 0

    def test_decorator(self):
        r = RuleRegistry()
        @r.decorator(name="deco_rule", description="decorated")
        def my_rule(text: str) -> str:
            return "matched"
        assert r.count() == 1
        assert r.get_enabled()[0].name == "deco_rule"


class TestWorkflowEngineSupplement:
    """WorkflowEngine 补充测试"""

    def test_try_match_no_rule(self):
        engine = WorkflowEngine()
        result = engine.try_match("测试输入")
        assert result is not None
        assert result.matched is False

    def test_workflow_result_defaults(self):
        r = WorkflowResult()
        assert r.matched is False
        assert r.rule_name == ""
        assert r.intent == ""
        assert r.confidence == 1.0
        assert r.output == ""
        assert r.execution_time_ms == 0.0


class TestMatcher:
    """匹配器测试"""

    def test_keyword_match(self):
        match_fn = keyword_match(["天气", "温度"])
        assert match_fn("今天天气如何") is True
        assert match_fn("查询温度") is True
        assert match_fn("你好世界") is False

    def test_keyword_match_case_insensitive(self):
        match_fn = keyword_match(["hello", "Hi"])
        assert match_fn("Say Hello") is True
        assert match_fn("HI there") is True

    def test_keyword_match_case_sensitive(self):
        match_fn = keyword_match(["Hello"], case_sensitive=True)
        assert match_fn("Hello world") is True
        assert match_fn("hello world") is False

    def test_regex_match(self):
        match_fn = regex_match(r"\d{4}-\d{2}-\d{2}")
        assert match_fn("日期 2024-01-01") is True
        assert match_fn("没有日期") is False

    def test_regex_email(self):
        match_fn = regex_match(r"\w+@\w+\.\w+")
        assert match_fn("邮箱 test@example.com") is True
        assert match_fn("无邮箱") is False
