"""P5 / E5-1 Phase C 第二批：events 读档（白名单闸）+ 每日零差异报告。

判据：
  · events 档只对白名单内会话生效，名单外回落 off（计数可见）；
  · events 为空而 messages 非空 ⇒ 回落 messages 并计数（宁可退化也不丢对话）；
  · shadow 每轮对拍累进「每日零差异报告」，报告可回答「连续几天零差异」。
"""
from __future__ import annotations

import json

import pytest

from agent import session_events


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(session_events, "EVENTS_DIR", tmp_path / "session_events")
    monkeypatch.delenv(session_events.READ_ENV, raising=False)
    monkeypatch.delenv(session_events.READ_SESSIONS_ENV, raising=False)
    monkeypatch.delenv(session_events.WRITE_ENV, raising=False)
    session_events.reset_for_tests()
    yield
    session_events.reset_for_tests()


@pytest.fixture
def manager(tmp_path):
    from agent.session_manager import SessionManager

    return SessionManager(sessions_dir=str(tmp_path / "sessions"))


def _digest(sid: str) -> str:
    import hashlib

    return hashlib.sha256(sid.encode("utf-8")).hexdigest()[:16]


class TestWhitelist:
    def test_空名单(self, monkeypatch):
        monkeypatch.setenv(session_events.READ_SESSIONS_ENV, "  ")
        assert session_events.read_whitelist() == ()

    def test_逗号分隔去重与非法_id(self, monkeypatch):
        monkeypatch.setenv(session_events.READ_SESSIONS_ENV,
                           " sess_a , sess_b,,sess_a, ../etc/passwd , sess-c ")
        assert session_events.read_whitelist() == ("sess_a", "sess_b", "sess-c")

    def test_上限截断(self, monkeypatch):
        monkeypatch.setattr(session_events, "MAX_READ_WHITELIST", 2)
        monkeypatch.setenv(session_events.READ_SESSIONS_ENV, "s1,s2,s3,s4")
        assert session_events.read_whitelist() == ("s1", "s2")


class TestModeFor:
    def test_off_与_shadow_原样返回(self, monkeypatch):
        assert session_events.read_mode_for("sess_x") == "off"
        monkeypatch.setenv(session_events.READ_ENV, "shadow")
        assert session_events.read_mode_for("sess_x") == "shadow"

    def test_events_只对白名单生效(self, monkeypatch):
        monkeypatch.setenv(session_events.READ_ENV, "events")
        monkeypatch.setenv(session_events.READ_SESSIONS_ENV, "sess_a")
        assert session_events.read_mode_for("sess_a") == "events"
        assert session_events.read_mode_for("sess_b") == "off"
        assert session_events.events_read_denied_count() == 1

    def test_非法值仍回落_off(self, monkeypatch):
        monkeypatch.setenv(session_events.READ_ENV, "events2")
        assert session_events.read_mode_for("sess_a") == "off"


class TestMessagesFromEvents:
    def test_按_seq_排序并跳过坏_payload(self):
        path = session_events.events_path("sess_evt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join([
            json.dumps({"seq": 2, "payload": {"role": "assistant", "content": "b"}}),
            json.dumps({"seq": 1, "payload": {"role": "user", "content": "a"}}),
            json.dumps({"seq": 3, "payload": "not-a-dict"}),
        ]) + "\n", encoding="utf-8")
        rows = session_events.messages_from_events("sess_evt")
        assert [row["content"] for row in rows] == ["a", "b"]

    def test_白名单内_以事件为准(self, monkeypatch, manager):
        monkeypatch.setenv(session_events.WRITE_ENV, "1")
        monkeypatch.setenv(session_events.READ_ENV, "events")
        session_id = manager.create_session()["id"]
        monkeypatch.setenv(session_events.READ_SESSIONS_ENV, session_id)
        manager.add_message(session_id, "user", "第一句")
        manager.add_message(session_id, "assistant", "第二句")
        # 把 messages.jsonl 清空：若还读得到两条，证明来源是 events
        (manager._sessions_dir / session_id / "messages.jsonl").write_text("", encoding="utf-8")
        rows = manager.get_messages(session_id)
        assert [row["content"] for row in rows] == ["第一句", "第二句"]

    def test_名单外_仍读_messages(self, monkeypatch, manager):
        monkeypatch.setenv(session_events.WRITE_ENV, "1")
        monkeypatch.setenv(session_events.READ_ENV, "events")
        session_id = manager.create_session()["id"]
        manager.add_message(session_id, "user", "只此一句")
        rows = manager.get_messages(session_id)
        assert [row["content"] for row in rows] == ["只此一句"]

    def test_事件为空_回落_messages_并计数(self, monkeypatch, manager):
        monkeypatch.setenv(session_events.READ_ENV, "events")
        session_id = manager.create_session()["id"]
        monkeypatch.setenv(session_events.READ_SESSIONS_ENV, session_id)
        manager.add_message(session_id, "user", "没有事件")
        rows = manager.get_messages(session_id)
        assert [row["content"] for row in rows] == ["没有事件"]
        assert session_events.events_read_fallback_count() == 1


class TestDailyReport:
    def test_干净对拍记一天(self, monkeypatch, tmp_path):
        monkeypatch.setattr(session_events, "_today", lambda: "2026-11-28")
        messages = [{"role": "user", "content": "x", "timestamp": "t"}]
        path = session_events.events_path("sess_clean")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"seq": 1, "payload": messages[0]}) + "\n", encoding="utf-8")
        session_events.shadow_compare("sess_clean", messages)
        raw = json.loads((tmp_path / "session_events" / "_shadow_daily.json")
                         .read_text(encoding="utf-8"))
        bucket = raw["days"]["2026-11-28"]
        assert bucket["compared"][_digest("sess_clean")] == 1
        assert bucket["diff"] == {}

    def test_差异也记(self, monkeypatch, tmp_path):
        monkeypatch.setattr(session_events, "_today", lambda: "2026-11-28")
        path = session_events.events_path("sess_diff")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"seq": 1, "payload": {"role": "user", "content": "a"}}) + "\n",
                        encoding="utf-8")
        session_events.shadow_compare("sess_diff", [{"role": "user", "content": "b"}])
        raw = json.loads((tmp_path / "session_events" / "_shadow_daily.json")
                         .read_text(encoding="utf-8"))
        assert raw["days"]["2026-11-28"]["diff"][_digest("sess_diff")] == 1

    def test_汇总与连续天数(self, monkeypatch, tmp_path):
        daily = tmp_path / "session_events" / "_shadow_daily.json"
        daily.parent.mkdir(parents=True, exist_ok=True)
        days = {
            "2026-11-25": {"compared": {"d1": 2}, "diff": {}},
            "2026-11-26": {"compared": {"d1": 3}, "diff": {"d1": 1}},
            "2026-11-27": {"compared": {"d1": 1}, "diff": {}},
            "2026-11-28": {"compared": {"d1": 4}, "diff": {}},
        }
        daily.write_text(json.dumps({"version": 1, "days": days}), encoding="utf-8")
        report = session_events.shadow_daily(days=30)
        assert report["total_days"] == 4 and report["diff_days"] == 1
        assert report["consecutive_clean_days"] == 2
        assert report["sessions"]["d1"] == {"compared": 10, "diff": 1}

    def test_只保留最近_60_天(self, monkeypatch, tmp_path):
        monkeypatch.setattr(session_events, "_today", lambda: "2026-11-28")
        monkeypatch.setattr(session_events, "SHADOW_KEEP_DAYS", 3)
        messages = [{"role": "user", "content": "x", "timestamp": "t"}]
        path = session_events.events_path("sess_keep")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"seq": 1, "payload": messages[0]}) + "\n", encoding="utf-8")
        daily = tmp_path / "session_events" / "_shadow_daily.json"
        daily.write_text(json.dumps({"version": 1, "days": {
            "2026-11-20": {"compared": {"old": 1}, "diff": {}},
            "2026-11-21": {"compared": {"old": 1}, "diff": {}},
            "2026-11-22": {"compared": {"old": 1}, "diff": {}},
        }}), encoding="utf-8")
        session_events.shadow_compare("sess_keep", messages)
        raw = json.loads(daily.read_text(encoding="utf-8"))
        # 保留最近 3 天：最旧的 11-20 被淘汰，剩下 11-21/11-22 + 今天
        assert sorted(raw["days"]) == ["2026-11-21", "2026-11-22", "2026-11-28"]

    def test_没有对拍就不建文件(self, tmp_path):
        session_events.shadow_compare("sess_empty", [])
        assert not (tmp_path / "session_events" / "_shadow_daily.json").exists()
