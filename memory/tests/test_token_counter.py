"""TokenCounter 单元测试"""
import pytest
from memory.token_counter import TokenCounter


def test_count_gpt4_text():
    """gpt-4 模型计数应返回准确 Token 数"""
    counter = TokenCounter()
    text = "Hello, world!"
    count = counter.count(text, model="gpt-4")
    assert count > 0
    assert isinstance(count, int)


def test_count_gpt4_empty():
    """空字符串应返回 0"""
    counter = TokenCounter()
    assert counter.count("", model="gpt-4") == 0


def test_count_unknown_model():
    """未知模型应降级使用 cl100k_base 估算"""
    counter = TokenCounter()
    text = "Hello, world!"
    count = counter.count(text, model="unknown-model")
    assert count > 0
    assert isinstance(count, int)


def test_count_messages():
    """消息列表计数应返回总和"""
    counter = TokenCounter()
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"}
    ]
    total = counter.count_messages(messages, model="gpt-4")
    assert total > 0
    # 两条消息的 token 应大于单条
    single = counter.count("Hello", model="gpt-4")
    assert total >= single


def test_count_claude_text():
    """claude-3 模型计数不应抛出异常"""
    counter = TokenCounter()
    text = "Hello, Claude!"
    # 使用近似策略，不应报错
    count = counter.count(text, model="claude-3-sonnet-20240229")
    assert count > 0
    assert isinstance(count, int)
