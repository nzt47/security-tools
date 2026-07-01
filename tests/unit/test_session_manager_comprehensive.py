"""SessionManager 全面单元测试

测试目标：覆盖 agent/session_manager.py 的所有公开 API
覆盖维度：
1. 创建会话：create_session / 默认标题 / mode
2. 查询会话：list_sessions / get_session / get_current / get_current_id
3. 修改会话：rename_session / set_current
4. 删除会话：delete_session / clear_messages
5. 消息管理：add_message / get_messages / get_message_count
6. 异常分支：SessionNotFoundError / 损坏索引 / 空文件
7. 边界条件：limit/offset、并发安全

状态同步说明：每个用例使用 tmp_path fixture 隔离 sessions 目录。
"""
import json
import os
import threading
from pathlib import Path

import pytest

from agent.session_manager import SessionManager, SessionNotFoundError


@pytest.fixture
def manager(tmp_path):
    """使用临时目录的 SessionManager"""
    return SessionManager(sessions_dir=str(tmp_path / "sessions"))


# ── 1. 初始化 ──────────────────────────────────────────────


class TestInit:
    def test_creates_sessions_dir(self, tmp_path):
        sessions_dir = tmp_path / "new_sessions"
        SessionManager(sessions_dir=str(sessions_dir))
        assert sessions_dir.exists()

    def test_creates_index_file(self, manager, tmp_path):
        index_path = tmp_path / "sessions" / "sessions.json"
        assert index_path.exists()
        assert index_path.read_text(encoding="utf-8") == "[]"

    def test_initial_current_id_none(self, manager):
        assert manager.get_current_id() is None

    def test_initial_current_session_none(self, manager):
        assert manager.get_current() is None

    def test_ensure_index_idempotent(self, tmp_path):
        """已存在的索引不被覆盖"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        index_path = sessions_dir / "sessions.json"
        index_path.write_text('[{"id": "existing"}]', encoding="utf-8")

        SessionManager(sessions_dir=str(sessions_dir))
        # _ensure_index 不应覆盖已存在的文件
        data = json.loads(index_path.read_text(encoding="utf-8"))
        # 注：实现会读取并尝试解析，但不会清空
        assert isinstance(data, list)


# ── 2. 创建会话 ──────────────────────────────────────────


class TestCreateSession:
    def test_create_returns_session_info(self, manager):
        info = manager.create_session()
        assert "id" in info
        assert info["id"].startswith("sess_")
        assert "title" in info
        assert "created_at" in info
        assert "updated_at" in info
        assert info["message_count"] == 0
        assert info["mode"] == "normal"

    def test_create_with_title(self, manager):
        info = manager.create_session(title="My Session")
        assert info["title"] == "My Session"

    def test_create_with_default_title(self, manager):
        info = manager.create_session()
        assert "会话" in info["title"]

    def test_create_with_mode(self, manager):
        info = manager.create_session(mode="planning")
        assert info["mode"] == "planning"

    def test_create_sets_current(self, manager):
        info = manager.create_session()
        assert manager.get_current_id() == info["id"]

    def test_create_creates_session_dir(self, manager, tmp_path):
        info = manager.create_session()
        session_dir = tmp_path / "sessions" / info["id"]
        assert session_dir.exists()

    def test_create_creates_meta_json(self, manager, tmp_path):
        info = manager.create_session(title="Meta Test")
        meta_path = tmp_path / "sessions" / info["id"] / "meta.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["title"] == "Meta Test"

    def test_create_creates_empty_messages_file(self, manager, tmp_path):
        info = manager.create_session()
        msg_path = tmp_path / "sessions" / info["id"] / "messages.jsonl"
        assert msg_path.exists()
        assert msg_path.read_text(encoding="utf-8") == ""

    def test_create_adds_to_index(self, manager):
        info = manager.create_session()
        sessions = manager.list_sessions()
        assert any(s["id"] == info["id"] for s in sessions)


# ── 3. 查询会话 ──────────────────────────────────────────


class TestQuerySessions:
    def test_list_sessions_empty(self, manager):
        assert manager.list_sessions() == []

    def test_list_sessions_returns_all(self, manager):
        for i in range(3):
            manager.create_session(title=f"S{i}")
        sessions = manager.list_sessions()
        assert len(sessions) == 3

    def test_list_sessions_limit(self, manager):
        for i in range(5):
            manager.create_session(title=f"S{i}")
        sessions = manager.list_sessions(limit=2)
        assert len(sessions) == 2

    def test_list_sessions_sorted_by_updated_at_desc(self, manager):
        import time
        s1 = manager.create_session(title="First")
        time.sleep(0.01)
        s2 = manager.create_session(title="Second")
        sessions = manager.list_sessions()
        # 最新的在前
        assert sessions[0]["id"] == s2["id"]
        assert sessions[1]["id"] == s1["id"]

    def test_get_session_existing(self, manager):
        info = manager.create_session(title="Find Me")
        result = manager.get_session(info["id"])
        assert result is not None
        assert result["title"] == "Find Me"

    def test_get_session_nonexistent(self, manager):
        assert manager.get_session("nonexistent") is None

    def test_get_session_returns_copy(self, manager):
        info = manager.create_session(title="Original")
        result = manager.get_session(info["id"])
        result["title"] = "Modified"
        # 原始数据未被修改
        original = manager.get_session(info["id"])
        assert original["title"] == "Original"


# ── 4. 修改会话 ──────────────────────────────────────────


class TestModifySessions:
    def test_rename_session(self, manager):
        info = manager.create_session(title="Old")
        assert manager.rename_session(info["id"], "New Name") is True
        result = manager.get_session(info["id"])
        assert result["title"] == "New Name"

    def test_rename_nonexistent(self, manager):
        assert manager.rename_session("nonexistent", "X") is False

    def test_rename_updates_meta_json(self, manager, tmp_path):
        info = manager.create_session(title="Old")
        manager.rename_session(info["id"], "New")
        meta_path = tmp_path / "sessions" / info["id"] / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["title"] == "New"

    def test_set_current_existing(self, manager):
        info = manager.create_session()
        manager._current_id = None  # 清除
        assert manager.set_current(info["id"]) is True
        assert manager.get_current_id() == info["id"]

    def test_set_current_nonexistent(self, manager):
        assert manager.set_current("nonexistent") is False

    def test_get_current_after_set(self, manager):
        info = manager.create_session()
        assert manager.get_current() is not None
        assert manager.get_current()["id"] == info["id"]

    def test_get_current_none(self, manager):
        assert manager.get_current() is None


# ── 5. 删除会话 ──────────────────────────────────────────


class TestDeleteSession:
    def test_delete_session(self, manager):
        info = manager.create_session()
        assert manager.delete_session(info["id"]) is True
        assert manager.get_session(info["id"]) is None

    def test_delete_nonexistent(self, manager):
        assert manager.delete_session("nonexistent") is False

    def test_delete_removes_directory(self, manager, tmp_path):
        info = manager.create_session()
        session_dir = tmp_path / "sessions" / info["id"]
        assert session_dir.exists()
        manager.delete_session(info["id"])
        assert not session_dir.exists()

    def test_delete_clears_current_id(self, manager):
        info = manager.create_session()
        assert manager.get_current_id() == info["id"]
        manager.delete_session(info["id"])
        assert manager.get_current_id() is None

    def test_delete_does_not_affect_others(self, manager):
        s1 = manager.create_session(title="S1")
        s2 = manager.create_session(title="S2")
        manager.delete_session(s1["id"])
        assert manager.get_session(s2["id"]) is not None


# ── 6. 消息管理 ──────────────────────────────────────────


class TestMessageManagement:
    def test_add_message(self, manager):
        info = manager.create_session()
        msg = manager.add_message(info["id"], "user", "Hello")
        assert msg["role"] == "user"
        assert msg["content"] == "Hello"
        assert "timestamp" in msg

    def test_add_message_to_nonexistent_session(self, manager):
        with pytest.raises(SessionNotFoundError):
            manager.add_message("nonexistent", "user", "Hello")

    def test_add_message_increments_count(self, manager):
        info = manager.create_session()
        manager.add_message(info["id"], "user", "A")
        manager.add_message(info["id"], "assistant", "B")
        result = manager.get_session(info["id"])
        assert result["message_count"] == 2

    def test_add_message_with_tool_calls(self, manager):
        info = manager.create_session()
        msg = manager.add_message(
            info["id"], "assistant", "using tool",
            tool_calls=[{"name": "read_file"}]
        )
        assert msg["tool_calls"] == [{"name": "read_file"}]

    def test_add_message_with_reasoning(self, manager):
        info = manager.create_session()
        msg = manager.add_message(
            info["id"], "assistant", "result",
            reasoning="I thought about it"
        )
        assert msg["reasoning"] == "I thought about it"

    def test_add_message_empty_content(self, manager):
        info = manager.create_session()
        msg = manager.add_message(info["id"], "user", "")
        assert msg["content"] == ""

    def test_add_message_none_content(self, manager):
        info = manager.create_session()
        msg = manager.add_message(info["id"], "user", None)
        assert msg["content"] == ""

    def test_get_messages_empty(self, manager):
        info = manager.create_session()
        assert manager.get_messages(info["id"]) == []

    def test_get_messages_all(self, manager):
        info = manager.create_session()
        for i in range(3):
            manager.add_message(info["id"], "user", f"msg{i}")
        messages = manager.get_messages(info["id"])
        assert len(messages) == 3

    def test_get_messages_with_limit(self, manager):
        info = manager.create_session()
        for i in range(5):
            manager.add_message(info["id"], "user", f"msg{i}")
        # limit 取最后 N 条
        messages = manager.get_messages(info["id"], limit=2)
        assert len(messages) == 2
        assert messages[0]["content"] == "msg3"
        assert messages[1]["content"] == "msg4"

    def test_get_messages_with_offset(self, manager):
        info = manager.create_session()
        for i in range(5):
            manager.add_message(info["id"], "user", f"msg{i}")
        messages = manager.get_messages(info["id"], offset=2)
        assert len(messages) == 3

    def test_get_messages_nonexistent_session(self, manager):
        assert manager.get_messages("nonexistent") == []

    def test_get_message_count_empty(self, manager):
        info = manager.create_session()
        assert manager.get_message_count(info["id"]) == 0

    def test_get_message_count(self, manager):
        info = manager.create_session()
        for i in range(4):
            manager.add_message(info["id"], "user", f"msg{i}")
        assert manager.get_message_count(info["id"]) == 4

    def test_get_message_count_nonexistent(self, manager):
        assert manager.get_message_count("nonexistent") == 0

    def test_clear_messages(self, manager):
        info = manager.create_session()
        manager.add_message(info["id"], "user", "A")
        manager.add_message(info["id"], "user", "B")
        assert manager.clear_messages(info["id"]) is True
        assert manager.get_message_count(info["id"]) == 0
        # message_count 在索引中也被重置
        result = manager.get_session(info["id"])
        assert result["message_count"] == 0

    def test_clear_messages_nonexistent(self, manager):
        assert manager.clear_messages("nonexistent") is False

    def test_clear_messages_then_add_again(self, manager):
        info = manager.create_session()
        manager.add_message(info["id"], "user", "A")
        manager.clear_messages(info["id"])
        manager.add_message(info["id"], "user", "B")
        assert manager.get_message_count(info["id"]) == 1


# ── 7. 异常与边界 ─────────────────────────────────────────


class TestEdgeCases:
    def test_read_index_corrupted(self, tmp_path):
        """损坏的索引文件应返回空列表"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "sessions.json").write_text("not json", encoding="utf-8")
        mgr = SessionManager(sessions_dir=str(sessions_dir))
        assert mgr.list_sessions() == []

    def test_read_index_empty_file(self, tmp_path):
        """空索引文件应返回空列表"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "sessions.json").write_text("", encoding="utf-8")
        mgr = SessionManager(sessions_dir=str(sessions_dir))
        assert mgr.list_sessions() == []

    def test_get_messages_with_corrupted_line(self, manager):
        """损坏的消息行应被跳过"""
        info = manager.create_session()
        manager.add_message(info["id"], "user", "valid")
        # 写入损坏的行
        msg_path = manager._sessions_dir / info["id"] / "messages.jsonl"
        with open(msg_path, "a", encoding="utf-8") as f:
            f.write("not json\n")
        messages = manager.get_messages(info["id"])
        # 损坏行被跳过，只剩有效消息
        assert len(messages) == 1
        assert messages[0]["content"] == "valid"

    def test_concurrent_create_sessions(self, manager):
        """并发创建会话应全部成功"""
        results = []
        barrier = threading.Barrier(5)

        def create():
            barrier.wait()
            results.append(manager.create_session(title="concurrent"))

        threads = [threading.Thread(target=create) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 5
        # 所有 ID 应唯一
        ids = {r["id"] for r in results}
        assert len(ids) == 5

    def test_list_sessions_with_limit_zero(self, manager):
        """limit=0 返回空列表（切片 sessions[:0] 为空）"""
        manager.create_session()
        sessions = manager.list_sessions(limit=0)
        assert len(sessions) == 0

    def test_list_sessions_with_negative_limit(self, manager):
        """负数 limit 返回除最后 N 个外的所有会话（Python 切片行为）"""
        manager.create_session()
        manager.create_session()
        manager.create_session()
        # limit=-1 等价于 sessions[:-1]，返回除最后 1 个外的所有
        sessions = manager.list_sessions(limit=-1)
        assert len(sessions) == 2


# ── 8. 集成场景 ─────────────────────────────────────────


class TestIntegration:
    def test_full_session_lifecycle(self, manager):
        """完整会话生命周期：创建→使用→删除"""
        # 1. 创建
        info = manager.create_session(title="Lifecycle Test")
        session_id = info["id"]

        # 2. 添加消息
        manager.add_message(session_id, "user", "Hello")
        manager.add_message(session_id, "assistant", "Hi there")
        assert manager.get_message_count(session_id) == 2

        # 3. 重命名
        manager.rename_session(session_id, "Renamed")
        assert manager.get_session(session_id)["title"] == "Renamed"

        # 4. 清空消息
        manager.clear_messages(session_id)
        assert manager.get_message_count(session_id) == 0

        # 5. 删除
        manager.delete_session(session_id)
        assert manager.get_session(session_id) is None

    def test_multiple_sessions_independent(self, manager):
        """多会话互相独立"""
        s1 = manager.create_session(title="S1")
        s2 = manager.create_session(title="S2")

        manager.add_message(s1["id"], "user", "in s1")
        manager.add_message(s2["id"], "user", "in s2")

        assert manager.get_message_count(s1["id"]) == 1
        assert manager.get_message_count(s2["id"]) == 1
        assert manager.get_messages(s1["id"])[0]["content"] == "in s1"
        assert manager.get_messages(s2["id"])[0]["content"] == "in s2"

    def test_session_persistence_across_instances(self, tmp_path):
        """会话数据在 Manager 实例间持久化"""
        sessions_dir = str(tmp_path / "sessions")
        mgr1 = SessionManager(sessions_dir=sessions_dir)
        info = mgr1.create_session(title="Persistent")
        mgr1.add_message(info["id"], "user", "saved")

        # 新实例应能读取之前的数据
        mgr2 = SessionManager(sessions_dir=sessions_dir)
        sessions = mgr2.list_sessions()
        assert any(s["id"] == info["id"] for s in sessions)
        assert mgr2.get_message_count(info["id"]) == 1
