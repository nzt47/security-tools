"""response_workflows 模块单元测试

覆盖：
- Confidence 枚举契约（.name 属性，与 orchestrator.py L293 对齐）
- IntentRouter.classify 各意图分类正确性
- ResponseTemplates.for_intent 模板生成 + None 降级
- register_intent 运行时扩展
- 空输入/边界处理
"""

import pytest
from agent.response_workflows import (
    IntentRouter,
    ResponseTemplates,
    Confidence,
    INTENT_TIME_QUERY,
    INTENT_IDENTITY,
    INTENT_CAPABILITY,
    INTENT_WEATHER,
    INTENT_GREETING,
    INTENT_SIMPLE_CHAT,
    INTENT_DISSATISFACTION,
    INTENT_FOLLOW_UP,
    INTENT_UNKNOWN,
)


class TestConfidenceEnum:
    """Confidence 枚举契约测试"""

    def test_has_name_attribute(self):
        """orchestrator.py L293 使用 confidence.name，必须存在"""
        assert Confidence.HIGH.name == "HIGH"
        assert Confidence.MEDIUM.name == "MEDIUM"
        assert Confidence.LOW.name == "LOW"

    def test_has_value_attribute(self):
        """置信度数值可用于阈值比较"""
        assert Confidence.HIGH.value > Confidence.MEDIUM.value
        assert Confidence.MEDIUM.value > Confidence.LOW.value


class TestIntentRouterClassify:
    """IntentRouter.classify 意图分类测试"""

    def setup_method(self):
        """每个测试前重置规则，避免 register_intent 污染"""
        IntentRouter._reset_rules()

    def test_identity_intent(self):
        intent, conf = IntentRouter.classify("你是谁")
        assert intent == INTENT_IDENTITY
        assert conf == Confidence.HIGH

        intent, conf = IntentRouter.classify("你叫什么名字")
        assert intent == INTENT_IDENTITY

    def test_time_query_intent(self):
        """验收标准: IntentRouter.classify("现在几点") 返回 ("time_query", HIGH)"""
        intent, conf = IntentRouter.classify("现在几点")
        assert intent == INTENT_TIME_QUERY
        assert conf == Confidence.HIGH

        # 变体覆盖
        intent, _ = IntentRouter.classify("几点了")
        assert intent == INTENT_TIME_QUERY

        intent, _ = IntentRouter.classify("当前时间")
        assert intent == INTENT_TIME_QUERY

    def test_greeting_intent(self):
        """验收标准关联: greeting 意图分类（模板生成前置）"""
        intent, conf = IntentRouter.classify("你好")
        assert intent == INTENT_GREETING
        assert conf == Confidence.HIGH

        intent, _ = IntentRouter.classify("早上好")
        assert intent == INTENT_GREETING

    def test_capability_intent(self):
        intent, conf = IntentRouter.classify("你能做什么")
        assert intent == INTENT_CAPABILITY
        assert conf == Confidence.HIGH

        intent, conf = IntentRouter.classify("你有什么功能")
        assert intent == INTENT_CAPABILITY

    def test_weather_intent(self):
        intent, conf = IntentRouter.classify("今天天气怎么样")
        assert intent == INTENT_WEATHER
        assert conf == Confidence.HIGH

    def test_dissatisfaction_intent(self):
        intent, conf = IntentRouter.classify("你怎么还不回答")
        assert intent == INTENT_DISSATISFACTION
        assert conf == Confidence.HIGH

    def test_follow_up_intent(self):
        intent, conf = IntentRouter.classify("然后呢")
        assert intent == INTENT_FOLLOW_UP
        assert conf == Confidence.MEDIUM

    def test_simple_chat_intent(self):
        intent, conf = IntentRouter.classify("好无聊啊")
        assert intent == INTENT_SIMPLE_CHAT

    def test_unknown_intent(self):
        intent, conf = IntentRouter.classify("帮我把这份PDF转成Word然后压缩发邮件")
        assert intent == INTENT_UNKNOWN
        assert conf == Confidence.LOW

    def test_empty_input(self):
        intent, conf = IntentRouter.classify("")
        assert intent == INTENT_UNKNOWN
        assert conf == Confidence.LOW

    def test_whitespace_only(self):
        intent, conf = IntentRouter.classify("   ")
        assert intent == INTENT_UNKNOWN
        assert conf == Confidence.LOW

    def test_priority_ordering(self):
        """高优先级规则先匹配"""
        # identity (priority=90) 应优先于 simple_chat (priority=40)
        intent, _ = IntentRouter.classify("你是谁")
        assert intent == INTENT_IDENTITY


class TestResponseTemplates:
    """ResponseTemplates 模板回复测试"""

    def test_identity_template(self):
        resp = ResponseTemplates.for_intent(INTENT_IDENTITY, Confidence.HIGH, hour=10)
        assert resp is not None
        assert "云枢" in resp

    def test_capability_template(self):
        resp = ResponseTemplates.for_intent(INTENT_CAPABILITY, Confidence.HIGH, hour=10)
        assert resp is not None
        assert "帮你" in resp

    def test_weather_template(self):
        resp = ResponseTemplates.for_intent(INTENT_WEATHER, Confidence.HIGH, hour=10)
        assert resp is not None
        assert "城市" in resp

    def test_greeting_template(self):
        """验收标准: ResponseTemplates.for_intent("greeting", hour=10) 返回非空字符串"""
        resp = ResponseTemplates.for_intent(INTENT_GREETING, Confidence.HIGH, hour=10)
        assert resp is not None
        assert len(resp) > 0
        # hour=10 应触发"早上好"分时问候
        assert "早上好" in resp

        # 时段感知验证
        resp_evening = ResponseTemplates.for_intent(INTENT_GREETING, Confidence.HIGH, hour=20)
        assert "晚上好" in resp_evening

    def test_time_query_returns_none(self):
        """time_query 不返回模板：纯函数无法读时钟（零 IO 约束），交 LLM 兜底"""
        resp = ResponseTemplates.for_intent(INTENT_TIME_QUERY, Confidence.HIGH, hour=10)
        assert resp is None

    def test_follow_up_returns_none(self):
        """追问类意图不返回模板，交由 LLM"""
        assert ResponseTemplates.for_intent(INTENT_FOLLOW_UP, Confidence.MEDIUM) is None

    def test_dissatisfaction_returns_none(self):
        """不满类意图不返回模板，交由 LLM"""
        assert ResponseTemplates.for_intent(INTENT_DISSATISFACTION, Confidence.HIGH) is None

    def test_unknown_returns_none(self):
        """未知意图不返回模板"""
        assert ResponseTemplates.for_intent(INTENT_UNKNOWN, Confidence.LOW) is None

    def test_low_confidence_returns_none(self):
        """LOW 置信度不返回模板（避免误模板化）"""
        assert ResponseTemplates.for_intent(INTENT_IDENTITY, Confidence.LOW) is None

    def test_time_greeting(self):
        """时段感知问候"""
        resp_morning = ResponseTemplates.for_intent(INTENT_SIMPLE_CHAT, Confidence.MEDIUM, hour=9)
        assert "早上好" in resp_morning

        resp_evening = ResponseTemplates.for_intent(INTENT_SIMPLE_CHAT, Confidence.MEDIUM, hour=20)
        assert "晚上好" in resp_evening

    def test_none_confidence_uses_default_hour(self):
        """hour=None 时使用当前时间，不报错"""
        resp = ResponseTemplates.for_intent(INTENT_IDENTITY, Confidence.HIGH, hour=None)
        assert resp is not None


class TestRegisterIntent:
    """运行时扩展规则测试"""

    def setup_method(self):
        IntentRouter._reset_rules()

    def test_register_new_intent(self):
        IntentRouter.register_intent(
            name="test_intent",
            patterns=[r"(?i)测试意图"],
            confidence=Confidence.HIGH,
            priority=100,
        )
        intent, conf = IntentRouter.classify("这是一个测试意图")
        assert intent == "test_intent"
        assert conf == Confidence.HIGH

    def test_register_does_not_affect_default_rules(self):
        """注册新规则不影响默认规则"""
        IntentRouter.register_intent(
            name="custom",
            patterns=[r"custom_pattern_xyz"],
            priority=100,
        )
        # 默认规则仍可用
        intent, _ = IntentRouter.classify("你是谁")
        assert intent == INTENT_IDENTITY


class TestPureFunctionContract:
    """纯函数契约验证（【不易】零 LLM、零 IO、零副作用）"""

    def test_classify_is_deterministic(self):
        """相同输入相同输出"""
        r1 = IntentRouter.classify("你是谁")
        r2 = IntentRouter.classify("你是谁")
        assert r1 == r2

    def test_classify_no_side_effects(self):
        """多次调用不改变状态"""
        before = list(IntentRouter._rules)
        IntentRouter.classify("你是谁")
        IntentRouter.classify("你能做什么")
        after = list(IntentRouter._rules)
        assert len(before) == len(after)
