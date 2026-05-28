"""Storage 单元测试"""
import json
import tempfile
from pathlib import Path
import pytest
from memory.storage import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(data_dir=str(tmp_path))


def test_save_and_load_recent_messages(storage):
    """保存消息后应能读取最近消息"""
    msg_id = storage.save_message({"role": "user", "content": "你好"})
    assert msg_id is not None
    messages = storage.load_recent_messages(limit=10)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "你好"


def test_load_recent_messages_limit(storage):
    """load_recent_messages 应返回正确的限制数量"""
    for i in range(5):
        storage.save_message({"role": "user", "content": f"msg{i}"})
    messages = storage.load_recent_messages(limit=3)
    # 应返回最近 3 条（倒序）
    assert len(messages) == 3


def test_save_and_load_summary(storage):
    """保存摘要后应能读取"""
    storage.save_summary("测试摘要", version=1)
    result = storage.load_summary()
    assert result is not None
    summary, version = result
    assert summary == "测试摘要"
    assert version == 1


def test_load_summary_not_exists(storage):
    """无摘要时应返回 None"""
    assert storage.load_summary() is None


def test_save_summary_overwrite(storage):
    """保存新摘要应覆盖旧摘要并递增版本"""
    storage.save_summary("版本1", version=1)
    storage.save_summary("版本2", version=2)
    summary, version = storage.load_summary()
    assert summary == "版本2"
    assert version == 2


def test_clear_messages(storage):
    """清空消息后应保留摘要文件"""
    storage.save_message({"role": "user", "content": "你好"})
    storage.save_summary("测试摘要", version=1)
    storage.clear_messages()
    assert len(storage.load_recent_messages(limit=10)) == 0
    # 摘要应保留
    assert storage.load_summary() is not None


def test_auto_create_directory():
    """自动创建不存在的目录"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nested" / "deep"
        s = Storage(data_dir=str(path))
        s.save_message({"role": "user", "content": "test"})
        assert path.exists()
