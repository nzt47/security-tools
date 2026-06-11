"""ToolCallingService 单元测试"""
import json
from unittest.mock import patch, MagicMock
import pytest
from agent.tool_calling import ToolCallingService, ToolCallError


class FakeLLMService:
    """用于测试的假 LLMService"""
    def __init__(self):
        self.provider = "openai"
        self.model = "gpt-4"
        self._client = None

    def _get_client(self):
        return None

    def _is_openai_compat(self):
        return True

    def chat(self, messages, system_prompt="", max_tokens=1024, temperature=0.7):
        return "（降级回复）"


class FakeResponse:
    """模拟 OpenAI 响应对象"""
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self._tool_calls = tool_calls or []

    @property
    def tool_calls(self):
        return self._tool_calls if self._tool_calls else None


def test_chat_returns_text_directly():
    """无工具调用时直接返回 LLM 文本"""
    service = ToolCallingService(FakeLLMService())
    service._call_llm_with_tools = lambda *a: FakeResponse(content="你好！")
    result = service.chat([{"role": "user", "content": "你好"}])
    assert result == "你好！"


def test_chat_invokes_tool_and_returns():
    """有工具调用时执行工具并返回最终回复"""
    service = ToolCallingService(FakeLLMService())

    round_num = [0]
    def mock_call_llm(messages, *a, **kw):
        round_num[0] += 1
        if round_num[0] == 1:
            return FakeResponse(content=None, tool_calls=[
                MagicMock(
                    id="call_1",
                    function=MagicMock(
                        name="check_health",
                        arguments=json.dumps({}),
                    )
                )
            ])
        return FakeResponse(content="健康检查完毕，一切正常！")

    service._call_llm_with_tools = mock_call_llm
    service._execute_safe = lambda name, args: {"ok": True, "status": "healthy"}

    result = service.chat([{"role": "user", "content": "检查身体"}])
    assert result == "健康检查完毕，一切正常！"


def test_chat_max_rounds_exceeded():
    """超过最大轮次时返回最后一条回复"""
    service = ToolCallingService(FakeLLMService(), max_rounds=2)

    round_num = [0]
    def mock_call_llm(messages, *a, **kw):
        round_num[0] += 1
        mock = MagicMock()
        mock.id = f"call_{round_num[0]}"
        mock.function = MagicMock()
        mock.function.name = "check_health"
        mock.function.arguments = "{}"
        return FakeResponse(content=None, tool_calls=[mock])

    service._call_llm_with_tools = mock_call_llm
    service._execute_safe = lambda name, args: {"ok": True}

    result = service.chat([{"role": "user", "content": "测试"}])
    assert result == "（无法生成回复）"


def test_execute_safe_catches_error():
    """工具执行异常应安全捕获不抛出"""
    service = ToolCallingService(FakeLLMService())
    result = service._execute_safe("nonexistent_tool", {})
    assert result["ok"] is False
    assert "error" in result


def test_fallback_on_first_round_failure():
    """首轮 LLM 失败应降级为无工具调用"""
    service = ToolCallingService(FakeLLMService())

    def failing_call(*args, **kwargs):
        raise Exception("API Error")

    service._call_llm_with_tools = failing_call

    result = service.chat([{"role": "user", "content": "你好"}])
    assert result == "（降级回复）"


def test_execute_safe_returns_dict():
    """工具返回非 dict 时应被包装"""
    service = ToolCallingService(FakeLLMService())

    # Use a simple function
    def fake_tool(name, **kw):
        return "string result"

    with patch("agent.tools.call", side_effect=fake_tool):
        result = service._execute_safe("fake_tool", {})
        assert result["ok"] is True
        assert result["result"] == "string result"
