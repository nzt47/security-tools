#!/usr/bin/env python3
"""ModelRouter 综合单元测试

【生成日志摘要】
- 生成时间戳: 2026-07-02
- 内容描述: model_router 模块全量单元测试
- 生成参数: 覆盖 ROUTING_TABLE/COMPLEX_KEYWORDS/ModelRouter.route 全部分支
- 模型配置: GLM-5.2
- 关键状态变化: 新增 ~40 个测试，目标覆盖率 100%
"""

import pytest
from agent.model_router.router import ModelRouter, ROUTING_TABLE, COMPLEX_KEYWORDS


class TestRoutingTable:
    def test_has_two_levels(self):
        assert len(ROUTING_TABLE) == 2

    def test_simple_level(self):
        level, model, keywords = ROUTING_TABLE[0]
        assert level == "simple"
        assert model == "gpt-3.5-turbo"
        assert len(keywords) > 0

    def test_normal_level(self):
        level, model, keywords = ROUTING_TABLE[1]
        assert level == "normal"
        assert model == "gpt-4o-mini"
        assert len(keywords) > 0

    def test_keywords_are_strings(self):
        for _, _, keywords in ROUTING_TABLE:
            for kw in keywords:
                assert isinstance(kw, str)
                assert len(kw) > 0


class TestComplexKeywords:
    def test_has_seven_keywords(self):
        assert len(COMPLEX_KEYWORDS) == 7

    def test_contains_analysis(self):
        assert "分析" in COMPLEX_KEYWORDS

    def test_contains_design(self):
        assert "设计" in COMPLEX_KEYWORDS

    def test_contains_architecture(self):
        assert "架构" in COMPLEX_KEYWORDS

    def test_contains_planning(self):
        assert "规划" in COMPLEX_KEYWORDS

    def test_contains_refactor(self):
        assert "重构" in COMPLEX_KEYWORDS

    def test_contains_debug(self):
        assert "调试" in COMPLEX_KEYWORDS

    def test_contains_optimize(self):
        assert "优化" in COMPLEX_KEYWORDS


class TestComplexRouting:
    """复杂关键词 → gpt-4"""

    @pytest.mark.parametrize("keyword", COMPLEX_KEYWORDS)
    def test_complex_keyword_routes_to_gpt4(self, keyword):
        router = ModelRouter()
        result = router.route("chat", f"帮我{keyword}这个功能")
        assert result == "gpt-4"

    def test_analysis_in_sentence(self):
        router = ModelRouter()
        assert router.route("chat", "请分析这段代码的性能瓶颈") == "gpt-4"

    def test_design_in_sentence(self):
        router = ModelRouter()
        assert router.route("chat", "设计一个微服务架构") == "gpt-4"

    def test_multiple_complex_keywords(self):
        router = ModelRouter()
        text = "分析并重构这个模块的架构设计"
        assert router.route("chat", text) == "gpt-4"


class TestSimpleRouting:
    """simple 关键词 → gpt-3.5-turbo"""

    @pytest.mark.parametrize("keyword", [
        "hello", "hi", "你好", "现在几点", "今天几号",
        "谢谢", "再见", "ok", "好吧", "yes", "no",
    ])
    def test_simple_keyword_routes_to_small_model(self, keyword):
        router = ModelRouter()
        result = router.route("chat", keyword)
        assert result == "gpt-3.5-turbo"

    def test_hello_in_sentence(self):
        router = ModelRouter()
        assert router.route("chat", "hello world") == "gpt-3.5-turbo"

    def test_hi_alone(self):
        router = ModelRouter()
        assert router.route("chat", "hi") == "gpt-3.5-turbo"


class TestNormalRouting:
    """normal 关键词 → gpt-4o-mini"""

    @pytest.mark.parametrize("keyword", [
        "翻译", "解释", "总结", "缩写", "格式化",
        "写邮件", "写标题", "写摘要",
    ])
    def test_normal_keyword_routes_to_medium_model(self, keyword):
        router = ModelRouter()
        result = router.route("chat", f"请{keyword}这段内容")
        assert result == "gpt-4o-mini"

    def test_translate_in_sentence(self):
        router = ModelRouter()
        assert router.route("chat", "翻译这段英文") == "gpt-4o-mini"

    def test_summarize_in_sentence(self):
        router = ModelRouter()
        assert router.route("chat", "总结这篇文章") == "gpt-4o-mini"


class TestLengthBasedRouting:
    """无关键词时按长度路由"""

    def test_short_text_uses_small_model(self):
        router = ModelRouter()
        # 29 字符（< 30）
        text = "a" * 29
        assert router.route("chat", text) == "gpt-3.5-turbo"

    def test_boundary_30_uses_medium_model(self):
        router = ModelRouter()
        # 30 字符（>= 30, < 200）
        text = "a" * 30
        assert router.route("chat", text) == "gpt-4o-mini"

    def test_medium_text_uses_medium_model(self):
        router = ModelRouter()
        # 199 字符（< 200）
        text = "a" * 199
        assert router.route("chat", text) == "gpt-4o-mini"

    def test_boundary_200_uses_large_model(self):
        router = ModelRouter()
        # 200 字符（>= 200）
        text = "a" * 200
        assert router.route("chat", text) == "gpt-4"

    def test_long_text_uses_large_model(self):
        router = ModelRouter()
        text = "a" * 500
        assert router.route("chat", text) == "gpt-4"

    def test_empty_string_uses_small_model(self):
        router = ModelRouter()
        assert router.route("chat", "") == "gpt-3.5-turbo"


class TestCaseInsensitivity:
    """关键词匹配大小写不敏感（input_text.lower()）"""

    def test_hello_uppercase(self):
        router = ModelRouter()
        assert router.route("chat", "HELLO") == "gpt-3.5-turbo"

    def test_hi_mixed_case(self):
        router = ModelRouter()
        assert router.route("chat", "Hi") == "gpt-3.5-turbo"

    def test_ok_uppercase(self):
        router = ModelRouter()
        assert router.route("chat", "OK") == "gpt-3.5-turbo"

    def test_yes_uppercase(self):
        router = ModelRouter()
        assert router.route("chat", "YES") == "gpt-3.5-turbo"

    def test_no_uppercase(self):
        router = ModelRouter()
        assert router.route("chat", "NO") == "gpt-3.5-turbo"


class TestComplexKeywordCaseInsensitive:
    """复杂关键词也大小写不敏感"""

    def test_complex_keyword_still_chinese(self):
        # 中文关键词不受大小写影响
        router = ModelRouter()
        assert router.route("chat", "分析") == "gpt-4"


class TestHistoryLength:
    """history_len 参数当前未使用，但不影响路由"""

    def test_history_zero(self):
        router = ModelRouter()
        assert router.route("chat", "hello", 0) == "gpt-3.5-turbo"

    def test_history_large(self):
        router = ModelRouter()
        assert router.route("chat", "hello", 1000) == "gpt-3.5-turbo"

    def test_history_default(self):
        router = ModelRouter()
        assert router.route("chat", "hello") == "gpt-3.5-turbo"


class TestEdgeCases:
    def test_whitespace_only(self):
        router = ModelRouter()
        # 空白字符串长度 < 30，无关键词匹配
        assert router.route("chat", "   ") == "gpt-3.5-turbo"

    def test_single_char(self):
        router = ModelRouter()
        assert router.route("chat", "a") == "gpt-3.5-turbo"

    def test_task_type_ignored(self):
        """task_type 参数当前未在路由逻辑中使用"""
        router = ModelRouter()
        assert router.route("any_type", "hello") == "gpt-3.5-turbo"
        assert router.route("chat", "hello") == "gpt-3.5-turbo"

    def test_complex_keyword_takes_precedence_over_simple(self):
        """复杂关键词优先级高于 simple 关键词"""
        router = ModelRouter()
        # 同时包含 "分析"（复杂）和 "你好"（simple）
        text = "分析你好"
        assert router.route("chat", text) == "gpt-4"

    def test_complex_keyword_takes_precedence_over_normal(self):
        """复杂关键词优先级高于 normal 关键词"""
        router = ModelRouter()
        # 同时包含 "设计"（复杂）和 "翻译"（normal）
        text = "设计翻译方案"
        assert router.route("chat", text) == "gpt-4"


class TestRoutingTablePriority:
    """ROUTING_TABLE 遍历顺序：simple 先于 normal"""

    def test_simple_before_normal(self):
        """同时命中 simple 和 normal 关键词时，simple 优先（因 ROUTING_TABLE[0]）"""
        router = ModelRouter()
        # "你好" 在 simple，"翻译" 在 normal
        text = "你好翻译"
        assert router.route("chat", text) == "gpt-3.5-turbo"


class TestIntegration:
    def test_full_routing_flow(self):
        router = ModelRouter()
        # 1. 简单问候
        assert router.route("chat", "hi") == "gpt-3.5-turbo"
        # 2. 翻译任务
        assert router.route("chat", "翻译这篇文档") == "gpt-4o-mini"
        # 3. 架构设计
        assert router.route("chat", "设计系统架构") == "gpt-4"
        # 4. 长文本无关键词
        assert router.route("chat", "x" * 300) == "gpt-4"

    def test_router_instances_independent(self):
        r1 = ModelRouter()
        r2 = ModelRouter()
        assert r1.route("chat", "hello") == r2.route("chat", "hello")
