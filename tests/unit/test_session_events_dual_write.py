"""P5 / E5-1 Phase B（双写）+ Phase C（shadow 读灰度）用例。

判据来自 docs/plan/2026-11-27-p5-会话事件溯源迁移方案.md §7：
  4) 双写：一条新消息 → 两处都出现；人为让事件写抛错 → 消息仍在、指标 +1、对话 200；
  5) 回滚演练：READ=shadow 输出与 off 逐字节相同；
  6) 新 env 全部登记 registry.py（由 test_settings_registry 的零缺口门禁守）。
"""
from __future__ import annotations

import json

import pytest

from agent import session_events


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(session_events, "EVENTS_DIR", tmp_path / "session_events")
    monkeypatch.delenv(session_events.WRITE_ENV, raising=False)
    monkeypatch.delenv(session_events.READ_ENV, raising=False)
    session_events.reset_for_tests()
    yield
    session_events.reset_for_tests()


@pytest.fixture
def manager(tmp_path):
    from agent.session_manager import SessionManager

    return SessionManager(sessions_dir=str(tmp_path / "sessions"))


def _events(sid: str):
    path = session_events.events_path(sid)
    assert path is not None
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ════════════════════════════════════════════════════════════
#  一、默认关 + 回滚（零行为变化）
# ════════════════════════════════════════════════════════════

class TestDefaultOff:
    def test_默认两个开关都是关(self):
        assert session_events.write_enabled() is False
        assert session_events.read_mode() == "off"

    def test_关的时候不写事件(self, manager):
        session_id = manager.create_session()["id"]
        manager.add_message(session_id, "user", "hi")
        assert _events(session_id) == []
        assert len(manager.get_messages(session_id)) == 1

    def test_非法读档回落_off(self, monkeypatch):
        # 【2026-11-28】"events" 现在是**合法档**（带白名单闸）；非法值用 bogus 验证回落
        monkeypatch.setenv(session_events.READ_ENV, "bogus")
        assert session_events.read_mode() == "off"


# ════════════════════════════════════════════════════════════
#  二、双写：两处都出现、字段正确、seq 单调
# ════════════════════════════════════════════════════════════

class TestDualWrite:
    def test_打开后两处都出现(self, monkeypatch, manager):
        monkeypatch.setenv(session_events.WRITE_ENV, "1")
        session_id = manager.create_session()["id"]
        manager.add_message(session_id, "user", "第一句")
        manager.add_message(session_id, "assistant", "第二句")

        messages = [json.loads(x) for x in
                    (manager._sessions_dir / session_id / "messages.jsonl")
                    .read_text(encoding="utf-8").splitlines() if x.strip()]
        events = _events(session_id)
        assert len(messages) == len(events) == 2
        assert [e["seq"] for e in events] == [1, 2]
        assert [e["type"] for e in events] == ["message.user", "message.assistant"]
        assert all(e["src"] == "live" for e in events)
        assert [e["payload"] for e in events] == messages
        assert events[0]["id"] == session_events.event_id(
            session_id, messages[0]["timestamp"], "user", "第一句")

    def test_seq_接在已有投影之后(self, monkeypatch, manager):
        monkeypatch.setenv(session_events.WRITE_ENV, "1")
        session_id = manager.create_session()["id"]
        path = session_events.events_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"v": 1, "type": "message.user", "sid": session_id,
                                    "seq": 7, "id": "evt_legacy", "ts": "t0",
                                    "payload": {"role": "user", "content": "old",
                                                "timestamp": "t0"},
                                    "src": "legacy"}, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        manager.add_message(session_id, "user", "新句")
        events = _events(session_id)
        assert [e["seq"] for e in events] == [7, 8]

    def test_事件写失败不影响消息(self, monkeypatch, manager, tmp_path):
        monkeypatch.setenv(session_events.WRITE_ENV, "1")
        # 让事件根目录变成**文件** ⇒ mkdir/写 必失败
        blocker = tmp_path / "blocked_root"
        blocker.write_text("not a dir", encoding="utf-8")
        monkeypatch.setattr(session_events, "EVENTS_DIR", blocker)
        session_id = manager.create_session()["id"]
        msg = manager.add_message(session_id, "user", "仍然要落盘")
        assert msg["content"] == "仍然要落盘"
        assert len(manager.get_messages(session_id)) == 1
        assert session_events.write_failure_count() >= 1

    def test_非法会话_id_不写(self, monkeypatch):
        monkeypatch.setenv(session_events.WRITE_ENV, "1")
        assert session_events.append_live_event("../evil", {"role": "user", "content": "x",
                                                           "timestamp": "t"}) is False
        assert session_events.append_live_event("", {"role": "user", "content": "x",
                                                     "timestamp": "t"}) is False

    def test_清空会话同时清空事件(self, monkeypatch, manager):
        monkeypatch.setenv(session_events.WRITE_ENV, "1")
        session_id = manager.create_session()["id"]
        manager.add_message(session_id, "user", "a")
        assert len(_events(session_id)) == 1
        assert manager.clear_messages(session_id) is True
        assert _events(session_id) == []


# ════════════════════════════════════════════════════════════
#  三、读灰度 shadow：只对拍，不改输出
# ════════════════════════════════════════════════════════════

class TestShadowRead:
    def test_shadow_输出与_off_逐字节相同(self, monkeypatch, manager):
        monkeypatch.setenv(session_events.WRITE_ENV, "1")
        session_id = manager.create_session()["id"]
        manager.add_message(session_id, "user", "你好")
        manager.add_message(session_id, "assistant", "在")
        baseline = manager.get_messages(session_id)
        monkeypatch.setenv(session_events.READ_ENV, "shadow")
        shadow = manager.get_messages(session_id)
        assert shadow == baseline
        assert session_events.shadow_diff_count() == 0

    def test_shadow_缺失事件时如实计数(self, monkeypatch, manager):
        session_id = manager.create_session()["id"]
        manager.add_message(session_id, "user", "只有 messages")
        monkeypatch.setenv(session_events.READ_ENV, "shadow")
        assert manager.get_messages(session_id)[0]["content"] == "只有 messages"
        meta = session_events.shadow_compare(session_id, manager.get_messages(session_id))
        assert meta["available"] is False
        assert meta["events"] == 0

    def test_shadow_内容差异被检出(self, monkeypatch, manager):
        monkeypatch.setenv(session_events.WRITE_ENV, "1")
        session_id = manager.create_session()["id"]
        manager.add_message(session_id, "user", "原始")
        # 人为篡改事件里的内容（模拟漂移），对拍必须发现
        path = session_events.events_path(session_id)
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        rows[0]["payload"]["content"] = "被改过"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                        encoding="utf-8")
        messages = manager.get_messages(session_id)
        meta = session_events.shadow_compare(session_id, messages)
        assert meta["mismatched"] == 1
        assert meta["first_mismatch"]["reason"] == "content"
        # 输出仍然是 messages 的事实
        assert messages[0]["content"] == "原始"
