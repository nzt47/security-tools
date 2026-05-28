"""MemoryManager 集成测试 — 使用临时目录，验证全流程"""
from unittest.mock import MagicMock
import pytest
from memory import MemoryManager


@pytest.fixture
def manager(tmp_path):
    config = {
        "data_dir": str(tmp_path / "memory_data"),
        "llm": {"provider": "openai", "api_key": "sk-test", "model": "gpt-4"},
        "async_compress": {"enabled": False}
    }
    m = MemoryManager(config=config)
    # Mock LLM 避免真实调用
    m._summarizer._llm.summarize = MagicMock(return_value="集成测试摘要")
    return m


def test_full_lifecycle(manager):
    """完整生命周期：添加消息 → 压缩 → 加载摘要"""
    # 添加消息
    for i in range(5):
        manager.add_message("user", f"这是第{i}条消息")

    # 获取上下文
    ctx = manager.get_context(token_limit=1000)
    assert ctx is not None
    assert len(ctx) > 0

    # 压缩
    messages = [{"role": "user", "content": f"msg{i}"} for i in range(3)]
    summary = manager.compress(messages)
    assert summary is not None

    # 保存并加载摘要
    manager._storage.save_summary("集成测试摘要", version=1)
    loaded = manager.load_summary()
    assert loaded is not None
    text, ver = loaded
    assert text == "集成测试摘要"
    assert ver == 1


def test_blackbox_integration(manager):
    """黑匣子日志应与 MemoryManager 协同工作"""
    manager.save_log("test_event", {"data": 123})
    manager.save_log("another_event", {"data": 456})
    results = manager.query_logs(event_type="test_event")
    assert len(results) >= 1
    assert results[0]["event_type"] == "test_event"


def test_clear_preserves_summary(manager):
    """清空记忆应保留摘要"""
    manager._storage.save_summary("保留的摘要", version=1)
    manager.add_message("user", "你好")
    manager.clear_memory()
    # 摘要应保留
    assert manager.load_summary() is not None
    # 消息应清空
    assert len(manager._storage.load_recent_messages(limit=10)) == 0


def test_persistence_across_instances(tmp_path):
    """不同实例应能读取同一数据目录"""
    data_dir = str(tmp_path / "memory_data")

    # 实例1：写入
    m1 = MemoryManager({
        "data_dir": data_dir,
        "async_compress": {"enabled": False}
    })
    m1.add_message("user", "持久化测试")
    m1._storage.save_summary("持久摘要", version=1)

    # 实例2：读取
    m2 = MemoryManager({
        "data_dir": data_dir,
        "async_compress": {"enabled": False}
    })
    messages = m2._storage.load_recent_messages(limit=10)
    assert len(messages) == 1
    assert messages[0]["content"] == "持久化测试"
    summary = m2.load_summary()
    assert summary is not None
    assert summary[0] == "持久摘要"


def test_get_context_with_summary(manager):
    """get_context 在有摘要时应包含 system 消息"""
    manager._storage.save_summary("已有摘要", version=1)
    manager.add_message("user", "新消息")
    ctx = manager.get_context(token_limit=4000)
    # 应有 system 摘要消息 + 用户消息
    assert any(msg["role"] == "system" for msg in ctx)
    assert any(msg["content"] == "新消息" for msg in ctx if msg["role"] == "user")
