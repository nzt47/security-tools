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

# ════════════════════════════════════════════════════════════
#  每会话摘要（2026-11-09）—— 存储层先支持，何时分家由调用方决定
# ════════════════════════════════════════════════════════════


def test_两会话摘要互不覆盖(storage):
    """A 的摘要不能出现在 B 的文件里；全局文件也不受影响（旧口径保持）"""
    storage.save_summary("A 的摘要", version=1, session_id="sess_A")
    storage.save_summary("B 的摘要", version=2, session_id="sess_B")

    assert storage.load_summary("sess_A") == ("A 的摘要", 1)
    assert storage.load_summary("sess_B") == ("B 的摘要", 2)
    # 未传会话 = 全局文件：仍然是"没有"，证明分家写不会污染旧口径
    assert storage.load_summary() is None


def test_与会话摘要不影响全局摘要(storage):
    storage.save_summary("全局摘要", version=7)
    storage.save_summary("A 的摘要", version=1, session_id="sess_A")
    assert storage.load_summary() == ("全局摘要", 7)
    assert storage.load_summary("sess_A") == ("A 的摘要", 1)


def test_会话文件名可预测且无路径分隔符(storage):
    """文件名 = sha256(sid) 前缀：可预测、无分隔符（路径注入断链）"""
    storage.save_summary("x", version=1, session_id="../etc/passwd")
    files = [p.name for p in Path(storage.data_dir).glob("summary_*.txt")
             if not p.name.endswith(".version.txt")]
    assert len(files) == 1
    name = files[0]
    assert "/" not in name and "\\" not in name and ".." not in name
    # 同一个 sid 每次算出同一个文件名 ⇒ 能读回
    assert storage.load_summary("../etc/passwd") == ("x", 1)


def test_清空只清该会话(storage):
    storage.save_summary("A", version=1, session_id="sess_A")
    storage.save_summary("B", version=1, session_id="sess_B")
    storage.clear_summary("sess_A")
    assert storage.load_summary("sess_A") == ("", 0)
    assert storage.load_summary("sess_B") == ("B", 1)


def test_没有该会话摘要时返回_None(storage):
    """存储层不替调用方猜「要不要回退全局」——没有就是 None"""
    storage.save_summary("全局摘要", version=3)
    assert storage.load_summary("sess_never_written") is None
