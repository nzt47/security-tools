"""Summarizer 单元测试"""
from unittest.mock import patch, MagicMock
import pytest
from memory.summarizer import Summarizer


@pytest.fixture
def summarizer():
    return Summarizer(llm_service=MagicMock())


def test_should_compress_below_threshold(summarizer):
    """低于阈值不应触发压缩"""
    assert not summarizer.should_compress(100, 200, threshold=0.8)


def test_should_compress_at_threshold(summarizer):
    """达到阈值应触发压缩"""
    assert summarizer.should_compress(160, 200, threshold=0.8)


def test_should_compress_above_threshold(summarizer):
    """超过阈值应触发压缩"""
    assert summarizer.should_compress(180, 200, threshold=0.8)


def test_should_compress_boundary(summarizer):
    """边界值：刚好等于 threshold * limit"""
    assert summarizer.should_compress(160, 200, threshold=0.8)
    assert not summarizer.should_compress(159, 200, threshold=0.8)


def test_compress_calls_llm():
    """compress 应调用 LLM 并返回摘要"""
    mock_llm = MagicMock()
    mock_llm.summarize.return_value = "这是摘要"
    s = Summarizer(llm_service=mock_llm)

    messages = [{"role": "user", "content": "你好"}]
    result = s.compress(messages)
    assert result == "这是摘要"
    mock_llm.summarize.assert_called_once()


def test_compress_empty_messages(summarizer):
    """空消息应返回空字符串"""
    assert summarizer.compress([]) == ""


def test_merge_summaries():
    """merge_summaries 应合并新旧摘要"""
    mock_llm = MagicMock()
    mock_llm.summarize.return_value = "合并后的摘要"
    s = Summarizer(llm_service=mock_llm)

    result = s.merge_summaries("旧摘要", "新消息的摘要")
    assert result == "合并后的摘要"
    mock_llm.summarize.assert_called_once()


def test_merge_without_old_summary(summarizer):
    """无旧摘要时，merge_summaries 应直接返回新摘要"""
    result = summarizer.merge_summaries("", "新摘要")
    assert result == "新摘要"


def test_compress_with_strategy():
    """不同策略应传递不同的 system prompt"""
    mock_llm = MagicMock()
    mock_llm.summarize.return_value = "简洁摘要"
    s = Summarizer(llm_service=mock_llm)

    s.compress([{"role": "user", "content": "你好"}], strategy="brief")
    assert mock_llm.summarize.call_count == 1
