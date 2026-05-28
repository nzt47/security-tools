"""MemoryManager 单元测试"""
import json
import tempfile
from unittest.mock import MagicMock, patch
from pathlib import Path
import pytest
from memory.memory_manager import MemoryManager


@pytest.fixture
def manager(tmp_path):
    """创建使用临时目录的 MemoryManager 实例"""
    config = {
        "data_dir": str(tmp_path / "memory_data"),
        "llm": {
            "provider": "openai",
            "api_key": "sk-test",
            "model": "gpt-4"
        },
        "async_compress": {
            "enabled": False  # 测试中禁用后台压缩
        }
    }
    return MemoryManager(config=config)


def test_add_message(manager):
    """添加消息应成功"""
    msg_id = manager.add_message("user", "你好灵犀")
    assert msg_id is not None


def test_get_context_empty(manager):
    """空记忆应返回空列表"""
    ctx = manager.get_context(token_limit=1000)
    assert ctx is None or ctx == []


def test_get_context_with_messages(manager):
    """有消息时应返回上下文"""
    manager.add_message("user", "你好")
    ctx = manager.get_context(token_limit=1000)
    assert ctx is not None
    # 应为列表格式
    assert isinstance(ctx, list)


def test_compress(manager):
    """compress 应返回摘要字符串"""
    messages = [
        {"role": "user", "content": "今天天气怎么样？"},
        {"role": "assistant", "content": "今天天气很好。"}
    ]
    # mock LLM 返回摘要
    original_llm = manager._summarizer._llm
    manager._summarizer._llm.summarize = MagicMock(return_value="关于天气的对话摘要")

    result = manager.compress(messages)
    assert result is not None
    assert len(result) > 0

    # 恢复
    manager._summarizer._llm = original_llm


def test_save_and_load_summary(manager):
    """保存后应能加载摘要"""
    manager.add_message("user", "你好")
    manager._storage.save_summary("测试摘要", version=1)
    summary = manager.load_summary()
    assert summary is not None
    text, version = summary
    assert text == "测试摘要"
    assert version == 1


def test_clear_memory(manager):
    """清空记忆应保留摘要"""
    manager.add_message("user", "你好")
    manager._storage.save_summary("测试摘要", version=1)
    manager.clear_memory()
    messages = manager._storage.load_recent_messages(limit=10)
    assert len(messages) == 0
    # 摘要应保留
    assert manager._storage.load_summary() is not None


def test_save_log(manager):
    """黑匣子快捷入口应工作"""
    manager.save_log("test_event", {"key": "value"})
    logs = manager.query_logs()
    assert len(logs) >= 1


def test_query_logs(manager):
    """查询日志应返回结果"""
    manager.save_log("event_a", {"msg": "test"})
    manager.save_log("event_b", {"msg": "test"})
    results = manager.query_logs(event_type="event_a")
    assert len(results) >= 1
    assert results[0]["event_type"] == "event_a"


def test_default_config():
    """无配置时应使用默认值"""
    m = MemoryManager()
    assert m is not None
