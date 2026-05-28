"""LLMService 单元测试"""
from unittest.mock import patch, MagicMock
import pytest
from memory.llm_service import LLMService, LLMServiceError


def test_openai_summarize():
    """OpenAI 摘要应返回正常结果"""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "这是摘要内容"

    with patch("openai.OpenAI") as mock_client:
        instance = mock_client.return_value
        instance.chat.completions.create.return_value = mock_response

        service = LLMService(provider="openai", api_key="sk-test", model="gpt-4")
        result = service.summarize([{"role": "user", "content": "你好"}])
        assert result == "这是摘要内容"


def test_anthropic_summarize():
    """Anthropic 摘要应返回正常结果"""
    mock_response = MagicMock()
    mock_response.content = [MagicMock()]
    mock_response.content[0].text = "这是 Claude 摘要"

    with patch("anthropic.Anthropic") as mock_client:
        instance = mock_client.return_value
        instance.messages.create.return_value = mock_response

        service = LLMService(provider="anthropic", api_key="sk-ant-test", model="claude-3-sonnet-20240229")
        result = service.summarize([{"role": "user", "content": "你好"}])
        assert result == "这是 Claude 摘要"


def test_invalid_provider():
    """无效 provider 应抛出异常"""
    service = LLMService(provider="invalid", api_key="test", model="test")
    with pytest.raises(LLMServiceError):
        service.summarize([{"role": "user", "content": "你好"}])


def test_openai_api_error():
    """OpenAI API 异常应包装为 LLMServiceError"""
    with patch("openai.OpenAI") as mock_client:
        instance = mock_client.return_value
        instance.chat.completions.create.side_effect = Exception("API Error")

        service = LLMService(provider="openai", api_key="sk-test", model="gpt-4")
        with pytest.raises(LLMServiceError):
            service.summarize([{"role": "user", "content": "你好"}])


def test_empty_messages():
    """空消息列表应返回空字符串"""
    service = LLMService(provider="openai", api_key="sk-test", model="gpt-4")
    result = service.summarize([])
    assert result == ""


def test_count_tokens():
    """count_tokens 应返回正整数"""
    service = LLMService(provider="openai", api_key="sk-test", model="gpt-4")
    count = service.count_tokens("Hello world")
    assert count > 0
    assert isinstance(count, int)
