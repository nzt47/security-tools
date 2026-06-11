"""SessionManager 单元测试"""
import json
from pathlib import Path
import pytest
from agent.session_manager import SessionManager, SessionNotFoundError


@pytest.fixture
def sm(tmp_path):
    """创建使用临时目录的 SessionManager"""
    return SessionManager(sessions_dir=str(tmp_path / "sessions"))


def test_create_session(sm):
    """创建会话后应在索引中可见"""
    session = sm.create_session("测试会话")
    assert session["id"] is not None
    assert session["title"] == "测试会话"
    assert session["message_count"] == 0

    sessions = sm.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == session["id"]


def test_create_session_default_title(sm):
    """未指定标题时自动生成"""
    session = sm.create_session()
    assert session["title"] is not None
    assert len(session["title"]) > 0


def test_list_sessions_order(sm):
    """会话应按 updated_at 降序"""
    s1 = sm.create_session("会话1")
    s2 = sm.create_session("会话2")
    sessions = sm.list_sessions()
    assert sessions[0]["id"] == s2["id"]
    assert sessions[1]["id"] == s1["id"]


def test_get_session(sm):
    """应按 ID 获取会话"""
    created = sm.create_session("测试")
    fetched = sm.get_session(created["id"])
    assert fetched is not None
    assert fetched["title"] == "测试"


def test_get_session_not_found(sm):
    """不存在的会话返回 None"""
    assert sm.get_session("nonexistent") is None


def test_delete_session(sm):
    """删除后不应在索引中"""
    s1 = sm.create_session("会话1")
    s2 = sm.create_session("会话2")
    sm.delete_session(s1["id"])
    sessions = sm.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == s2["id"]
    assert not (Path(sm._sessions_dir) / s1["id"]).exists()


def test_delete_nonexistent_session(sm):
    """删除不存在的会话返回 False"""
    assert sm.delete_session("nonexistent") is False


def test_rename_session(sm):
    """重命名应更新标题"""
    session = sm.create_session("旧名")
    assert sm.rename_session(session["id"], "新名") is True
    updated = sm.get_session(session["id"])
    assert updated["title"] == "新名"


def test_rename_nonexistent_session(sm):
    """重命名不存在的会话返回 False"""
    assert sm.rename_session("nonexistent", "新名") is False


def test_set_and_get_current(sm):
    """应支持设置和获取当前会话"""
    s1 = sm.create_session("会话1")
    s2 = sm.create_session("会话2")
    assert sm.set_current(s1["id"]) is True
    assert sm.get_current_id() == s1["id"]
    current = sm.get_current()
    assert current["id"] == s1["id"]
    assert sm.set_current(s2["id"]) is True
    assert sm.get_current_id() == s2["id"]


def test_set_current_invalid(sm):
    """设置不存在的会话应返回 False"""
    assert sm.set_current("nonexistent") is False


def test_get_current_none(sm):
    """无当前会话时返回 None"""
    assert sm.get_current_id() is None
    assert sm.get_current() is None


def test_add_and_get_messages(sm):
    """添加消息后应能读取"""
    session = sm.create_session("测试")
    msg = sm.add_message(session["id"], "user", "你好")
    assert msg["role"] == "user"
    assert msg["content"] == "你好"

    messages = sm.get_messages(session["id"])
    assert len(messages) == 1
    assert messages[0]["content"] == "你好"


def test_add_message_with_tool_calls(sm):
    """应支持存储 tool_calls"""
    session = sm.create_session("测试")
    msg = sm.add_message(
        session["id"], "assistant", "",
        tool_calls=[{"name": "web_search", "arguments": '{"query": "test"}'}]
    )
    assert msg["tool_calls"] is not None
    assert len(msg["tool_calls"]) == 1


def test_add_message_increments_count(sm):
    """添加消息应递增计数"""
    session = sm.create_session("测试")
    for i in range(3):
        sm.add_message(session["id"], "user", f"msg{i}")
    updated = sm.get_session(session["id"])
    assert updated["message_count"] == 3


def test_get_messages_limit(sm):
    """get_messages 应支持 limit 限制"""
    session = sm.create_session("测试")
    for i in range(10):
        sm.add_message(session["id"], "user", f"msg{i}")
    messages = sm.get_messages(session["id"], limit=3)
    assert len(messages) == 3
    assert messages[-1]["content"] == "msg9"


def test_get_messages_offset(sm):
    """get_messages 应支持 offset 偏移"""
    session = sm.create_session("测试")
    for i in range(10):
        sm.add_message(session["id"], "user", f"msg{i}")
    messages = sm.get_messages(session["id"], limit=5, offset=5)
    assert len(messages) == 5
    assert messages[0]["content"] == "msg5"


def test_add_message_to_nonexistent_session(sm):
    """向不存在的会话添加消息应抛异常"""
    with pytest.raises(SessionNotFoundError):
        sm.add_message("nonexistent", "user", "你好")


def test_clear_messages(sm):
    """清空消息后计数归零"""
    session = sm.create_session("测试")
    sm.add_message(session["id"], "user", "你好")
    sm.clear_messages(session["id"])
    assert len(sm.get_messages(session["id"])) == 0
    updated = sm.get_session(session["id"])
    assert updated["message_count"] == 0


def test_persistence_across_instances(tmp_path):
    """会话数据应在 SessionManager 实例间持久化"""
    dir_path = str(tmp_path / "persist_test")
    sm1 = SessionManager(sessions_dir=dir_path)
    session = sm1.create_session("持久化测试")
    sm1.add_message(session["id"], "user", "消息1")

    sm2 = SessionManager(sessions_dir=dir_path)
    sessions = sm2.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["title"] == "持久化测试"
    messages = sm2.get_messages(session["id"])
    assert len(messages) == 1
    assert messages[0]["content"] == "消息1"
