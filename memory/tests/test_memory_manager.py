"""MemoryManager 单元测试"""
from unittest.mock import MagicMock
import pytest
from memory.memory_manager import MemoryManager


@pytest.fixture
def manager(tmp_path):
    """创建使用临时目录的 MemoryManager 实例"""
    config = {
        "data_dir": str(tmp_path / "memory_data"),
        "llm": {
            "provider": "openai",
            "api_key": "sk-test-key-valid-12345",
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


def test_execute_compression_no_old_summary(manager):
    """测试首次压缩（无旧摘要）"""
    manager.add_message("user", "第一条消息")
    manager.add_message("assistant", "第一条回复")
    
    manager._summarizer._llm.summarize = MagicMock(return_value="首次对话摘要")
    
    result = manager._execute_compression()
    
    assert result is True
    summary = manager.load_summary()
    assert summary is not None
    summary_text, version = summary
    assert summary_text == "首次对话摘要"
    assert version == 1


def test_execute_compression_with_existing_summary(manager):
    """测试有旧摘要时的压缩合并"""
    manager.add_message("user", "第一条消息")
    manager._storage.save_summary("旧摘要内容", version=1)
    
    manager._summarizer._llm.summarize = MagicMock(side_effect=[
        "新消息摘要",
        "合并后的摘要"
    ])
    
    result = manager._execute_compression()
    
    assert result is True
    summary = manager.load_summary()
    assert summary is not None
    summary_text, version = summary
    assert version == 2
    assert manager._summarizer._llm.summarize.call_count == 2


def test_execute_compression_no_messages(manager):
    """测试无消息时的压缩行为"""
    result = manager._execute_compression()
    
    assert result is False
    summary = manager.load_summary()
    assert summary is None


def test_execute_compression_error_handling(manager):
    """测试压缩失败时的错误处理"""
    manager.add_message("user", "测试消息")
    
    manager._summarizer._llm.summarize = MagicMock(side_effect=Exception("LLM调用失败"))
    
    result = manager._execute_compression()
    
    assert result is False


def test_multi_message_compression_scenario(manager):
    """模拟实际运行场景：多条消息的压缩和摘要合并"""
    conversation = [
        ("user", "你好，我想了解天气情况"),
        ("assistant", "好的，请问您想了解哪个城市的天气？"),
        ("user", "北京"),
        ("assistant", "北京今天晴朗，温度25度左右"),
        ("user", "那上海呢"),
        ("assistant", "上海今天多云，温度23度左右"),
        ("user", "谢谢"),
        ("assistant", "不客气，还有什么可以帮您的吗？"),
        ("user", "没有了"),
        ("assistant", "好的，祝您愉快！"),
    ]
    
    manager._summarizer._llm.summarize = MagicMock(return_value="用户询问了北京和上海的天气，助手分别给出了天气信息")
    
    for role, content in conversation:
        manager.add_message(role, content)
    
    manager._need_compress = True
    result = manager._execute_compression()
    
    assert result is True
    assert manager._summarizer._llm.summarize.call_count == 1
    
    summary = manager.load_summary()
    assert summary is not None
    summary_text, version = summary
    assert version == 1
    assert len(summary_text) > 0
    
    new_conversation = [
        ("user", "明天天气如何"),
        ("assistant", "明天北京有小雨，请注意带伞"),
    ]
    
    for role, content in new_conversation:
        manager.add_message(role, content)
    
    manager._summarizer._llm.summarize = MagicMock(return_value="用户继续询问天气")
    
    manager._need_compress = True
    result = manager._execute_compression()
    
    assert result is True
    assert manager._summarizer._llm.summarize.call_count >= 1
    
    summary = manager.load_summary()
    assert summary is not None
    summary_text, version = summary
    assert version == 2


def test_get_context_with_compression_triggered(manager):
    """测试触发压缩后的上下文获取"""
    manager._summarizer._llm.summarize = MagicMock(return_value="对话摘要")
    
    manager.add_message("user", "消息1")
    manager.add_message("assistant", "回复1")
    manager.add_message("user", "消息2")
    manager.add_message("assistant", "回复2")
    
    manager._need_compress = True
    context = manager.get_context(token_limit=1000)
    
    assert len(context) > 0
    
    summary = manager.load_summary()
    assert summary is not None


def test_async_compressor_calls_execute_compression(manager, tmp_path):
    """测试 AsyncCompressor 正确调用 _execute_compression"""
    from memory.memory_manager import AsyncCompressor
    
    manager._summarizer._llm.summarize = MagicMock(return_value="后台压缩摘要")
    
    compressor = AsyncCompressor(memory_manager=manager, interval=60)
    compressor._pending = True
    
    for i in range(5):
        manager.add_message("user", f"消息{i}")
    
    compressor._do_compress()
    
    assert compressor._pending is False
    summary = manager.load_summary()
    assert summary is not None
    summary_text, version = summary
    assert summary_text == "后台压缩摘要"
    assert version == 1


def test_compression_version_increment(manager):
    """测试压缩版本号正确递增"""
    manager._summarizer._llm.summarize = MagicMock(return_value="摘要内容")
    
    for i in range(3):
        manager.add_message("user", f"消息{i}")
        manager._need_compress = True
        result = manager._execute_compression()
        assert result is True
    
    summary = manager.load_summary()
    assert summary is not None
    _, version = summary
    assert version == 3
