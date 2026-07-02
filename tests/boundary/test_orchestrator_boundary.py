"""BT-010: orchestrator 模块边界测试

覆盖 agent/orchestrator/ 下可独立测试的模块：
- ResponseBuilder (response_builder.py) — 纯静态工厂
- MessageHandler (message_handler.py) — 静态方法，纯字符串处理
- PromptBuilder (prompt_builder.py) — 接受 token_counter 和 memory 参数

边界场景覆盖（满足 boundary_config.yaml 中 orchestrator 模块要求）：
- timeout: token 预算超限截断、大输入性能上限
- invalid: None 输入、非法类型、None profile、token_counter 异常
- extreme: 极大 data、超长文本、大量关键词、极长 tool_status
- empty/null: 空字符串、空 context、None 输入

【可观测性约束】
- 边界显性化：非法输入抛 AttributeError/TypeError 而非静默返回
- 健康检查：ResponseBuilder.from_exception 提供异常到响应的转换
"""
import pytest
import time
from types import SimpleNamespace

from agent.orchestrator.response_builder import ResponseBuilder, Response
from agent.orchestrator.message_handler import MessageHandler
from agent.orchestrator.prompt_builder import PromptBuilder


# ── fixtures ──

@pytest.fixture
def prompt_builder():
    """无 token_counter 的 PromptBuilder"""
    return PromptBuilder()


@pytest.fixture
def profile():
    """简单的 profile 对象"""
    return SimpleNamespace(label="测试模式", description="测试模式描述")


@pytest.fixture
def template_fn():
    """简单的模板函数"""
    return lambda: "{current_date}|{body_status}|{mode_name}|{mode_description}|{memory_context}|{tool_status}|{skill_instructions}"


class MockTokenCounter:
    """Mock token 计数器"""
    def __init__(self, count_result=0):
        self._count_result = count_result

    def count(self, text):
        return self._count_result


class MockMemory:
    """Mock Memory 对象"""
    def __init__(self, summary=None, context=None, working_memory=None):
        self._summary = summary
        self._context = context
        self._working_memory = working_memory

    def load_summary(self):
        return self._summary

    def get_context(self, token_limit=None):
        return self._context

    def get_working_memory(self):
        return self._working_memory


# ═══════════════════════════════════════════════════════════════
#  Timeout 边界场景
# ═══════════════════════════════════════════════════════════════

class TestTimeoutBoundary:
    """超时/性能边界测试

    orchestrator 模块无天然 timeout 语义（同步纯计算），
    通过 token 预算超限截断和性能上限断言覆盖 timeout 场景。
    """

    def test_timeout_prompt_token_budget_exceeded(self, profile, template_fn):
        """token 超预算 — build_system_prompt 截断 tool_status"""
        builder = PromptBuilder(token_counter=MockTokenCounter(count_result=20000))
        result = builder.build_system_prompt(
            body_status="正常",
            tool_status="工具" * 200,  # 400 字符 > 300
            skill_instructions="指令",
            profile=profile,
            get_template_fn=template_fn,
        )
        assert isinstance(result, str)
        assert "已截断" in result

    def test_timeout_prompt_within_budget(self, profile, template_fn):
        """token 在预算内 — 不截断"""
        builder = PromptBuilder(token_counter=MockTokenCounter(count_result=5000))
        result = builder.build_system_prompt(
            body_status="正常",
            tool_status="工具状态",
            skill_instructions="指令",
            profile=profile,
            get_template_fn=template_fn,
        )
        assert isinstance(result, str)
        assert "已截断" not in result

    def test_timeout_parse_large_text_performance(self):
        """parse 超长输入 — 应在合理时间内完成"""
        large_text = "测试" * 10000
        start = time.time()
        result = MessageHandler.parse(large_text)
        duration = time.time() - start
        assert isinstance(result, dict)
        assert duration < 1.0

    def test_timeout_extract_keywords_large_text(self):
        """extract_keywords 超长文本 — 应在合理时间内完成"""
        large_text = "关键词 " * 5000
        start = time.time()
        result = MessageHandler.extract_keywords(large_text)
        duration = time.time() - start
        assert isinstance(result, list)
        assert duration < 2.0

    def test_timeout_build_system_prompt_large_input(self, profile, template_fn):
        """build_system_prompt 大输入 — 应在合理时间内完成"""
        builder = PromptBuilder(token_counter=MockTokenCounter(count_result=100))
        start = time.time()
        result = builder.build_system_prompt(
            body_status="状态" * 1000,
            tool_status="工具" * 1000,
            skill_instructions="指令" * 1000,
            profile=profile,
            get_template_fn=template_fn,
        )
        duration = time.time() - start
        assert isinstance(result, str)
        assert duration < 2.0


# ═══════════════════════════════════════════════════════════════
#  Invalid 边界场景
# ═══════════════════════════════════════════════════════════════

class TestInvalidBoundary:
    """非法输入边界测试"""

    def test_invalid_parse_none(self):
        """None 输入 — parse 抛 AttributeError（None.strip() 非法）"""
        with pytest.raises(AttributeError):
            MessageHandler.parse(None)

    def test_invalid_parse_int(self):
        """整数输入 — parse 抛 AttributeError（int.strip() 非法）"""
        with pytest.raises(AttributeError):
            MessageHandler.parse(123)

    def test_invalid_is_simple_query_none(self):
        """None 输入 — is_simple_query 抛 AttributeError"""
        with pytest.raises(AttributeError):
            MessageHandler.is_simple_query(None)

    def test_invalid_detect_dissatisfaction_none(self):
        """None 输入 — detect_dissatisfaction 抛 TypeError（re.search(None)）"""
        with pytest.raises(TypeError):
            MessageHandler.detect_dissatisfaction(None)

    def test_invalid_extract_keywords_none(self):
        """None 输入 — extract_keywords 抛 TypeError（re.sub(None)）"""
        with pytest.raises(TypeError):
            MessageHandler.extract_keywords(None)

    def test_invalid_is_follow_up_none_text(self):
        """None text + history>0 — is_follow_up 抛 TypeError（re.match(None)）"""
        with pytest.raises(TypeError):
            MessageHandler.is_follow_up({"text": None, "history_count": 1})

    def test_invalid_response_from_exception_none(self):
        """None 异常 — from_exception 返回 error='None'（str(None)）"""
        response = ResponseBuilder.from_exception(None)
        assert isinstance(response, Response)
        assert response.success is False
        assert response.error == "None"

    def test_invalid_prompt_builder_none_profile(self, template_fn):
        """None profile — build_system_prompt 抛 AttributeError（None.label）"""
        builder = PromptBuilder()
        with pytest.raises(AttributeError):
            builder.build_system_prompt(
                body_status="正常",
                tool_status="工具",
                skill_instructions="",
                profile=None,
                get_template_fn=template_fn,
            )

    def test_invalid_prompt_builder_template_fn_raises(self, profile):
        """get_template_fn 抛异常 — build_system_prompt 传播异常"""
        builder = PromptBuilder()

        def raising_fn():
            raise RuntimeError("模板获取失败")

        with pytest.raises(RuntimeError):
            builder.build_system_prompt(
                body_status="正常",
                tool_status="工具",
                skill_instructions="",
                profile=profile,
                get_template_fn=raising_fn,
            )

    def test_invalid_token_counter_raises_swallowed(self, profile, template_fn):
        """token_counter.count 抛异常 — 被 try/except 吞掉，返回未截断结果

        注意：此处验证现有代码的静默吞异常行为（违反边界显性化原则），
        确保异常不影响主流程。
        """
        class RaisingCounter:
            def count(self, text):
                raise RuntimeError("计数失败")

        builder = PromptBuilder(token_counter=RaisingCounter())
        result = builder.build_system_prompt(
            body_status="正常",
            tool_status="工具",
            skill_instructions="",
            profile=profile,
            get_template_fn=template_fn,
        )
        assert isinstance(result, str)
        assert "已截断" not in result

    def test_invalid_build_memory_context_none_memory(self, prompt_builder):
        """None memory — build_memory_context 吞掉异常返回默认文本"""
        result = prompt_builder.build_memory_context(None)
        assert isinstance(result, str)
        assert "暂无历史对话" in result


# ═══════════════════════════════════════════════════════════════
#  Extreme 边界场景
# ═══════════════════════════════════════════════════════════════

class TestExtremeBoundary:
    """极值边界测试"""

    def test_extreme_response_huge_data(self):
        """极大 data — ResponseBuilder.success 正常处理"""
        huge_data = list(range(10000))
        response = ResponseBuilder.success(data=huge_data)
        assert isinstance(response, Response)
        assert response.success is True
        assert len(response.data) == 10000

    def test_extreme_response_very_long_error(self):
        """超长 error 字符串 — ResponseBuilder.error 正常处理"""
        long_error = "错误" * 1000
        response = ResponseBuilder.error(error=long_error)
        assert isinstance(response, Response)
        assert response.success is False
        assert len(response.error) == 2000

    def test_extreme_parse_very_long_text(self):
        """超长文本 — parse 返回正确长度"""
        long_text = "a" * 10000
        result = MessageHandler.parse(long_text)
        assert result["length"] == 10000
        assert result["is_empty"] is False

    def test_extreme_extract_keywords_many_words(self):
        """大量词 — extract_keywords 返回过滤后的列表"""
        text = " ".join(["关键词"] * 1000)
        result = MessageHandler.extract_keywords(text)
        assert isinstance(result, list)
        assert len(result) == 1000

    def test_extreme_is_follow_up_large_history(self):
        """大 history_count + 短文本 — is_follow_up 返回 True"""
        result = MessageHandler.is_follow_up({"text": "然后", "history_count": 100})
        assert result is True

    def test_extreme_prompt_builder_long_tool_status_truncated(self, profile, template_fn):
        """超长 tool_status + token 超预算 — 截断"""
        builder = PromptBuilder(token_counter=MockTokenCounter(count_result=15000))
        long_tools = "工具状态详情" * 100  # 600 字符 > 300
        result = builder.build_system_prompt(
            body_status="正常",
            tool_status=long_tools,
            skill_instructions="",
            profile=profile,
            get_template_fn=template_fn,
        )
        assert "已截断" in result


# ═══════════════════════════════════════════════════════════════
#  Empty/Null 边界场景（额外补充）
# ═══════════════════════════════════════════════════════════════

class TestEmptyNullBoundary:
    """空值/None 边界测试"""

    def test_empty_parse_empty_string(self):
        """空字符串 — parse 返回 is_empty=True"""
        result = MessageHandler.parse("")
        assert result["is_empty"] is True
        assert result["length"] == 0

    def test_empty_parse_whitespace_only(self):
        """纯空白 — parse 返回 is_empty=True"""
        result = MessageHandler.parse("   ")
        assert result["is_empty"] is True

    def test_empty_extract_keywords_no_keywords(self):
        """全停用词 — extract_keywords 返回空列表"""
        result = MessageHandler.extract_keywords("的 了 是 在 我")
        assert result == []

    def test_empty_response_success_default(self):
        """默认参数 — success 返回 data=None"""
        response = ResponseBuilder.success()
        assert response.success is True
        assert response.data is None
        assert response.msg == "ok"

    def test_null_is_follow_up_empty_context(self):
        """空 context — is_follow_up 返回 False"""
        result = MessageHandler.is_follow_up({})
        assert result is False

    def test_null_is_follow_up_none_text_zero_history(self):
        """None text + history=0 — is_follow_up 返回 False（短路不触发 p.match）"""
        result = MessageHandler.is_follow_up({"text": None, "history_count": 0})
        assert result is False

    def test_null_response_rejection_default(self):
        """默认参数 — rejection 返回 reason=''"""
        response = ResponseBuilder.rejection()
        assert response.success is False
        assert response.error == ""
