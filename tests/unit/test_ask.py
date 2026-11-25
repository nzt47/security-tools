"""E1-3a 结构化提问：存储 / 工具 / SSE 提升（2026-11-25）

钉死设计页 `docs/plan/2026-11-25-e1-3-interaction-design.md`：
  · 待答问题落 data/ask/<sha256(sid)[:32]>/pending.json，答案 append 到 answers.jsonl；
  · 同会话只保留最近 1 条未答（新问题把旧问题标 superseded）；
  · 答案校验：id 必须匹配、单选/多选/自定义按问题声明、空答案拒绝；
  · 工具返回 USER_INPUT_REQUIRED（非错误）+ guidance；present 只做字符串校验、不读文件；
  · chat 的 tool_side_events 把 ask/present 提升为独立 SSE 事件。
"""
from __future__ import annotations

import json

import pytest

from agent.context import ask as A
from agent.context import session_bind


@pytest.fixture(autouse=True)
def _root(tmp_path):
    A.set_root(str(tmp_path / "ask"))
    yield
    A.set_root(None)


SID = "ask-sess-1"


class TestStore:
    def test_创建并读取待答问题(self):
        doc = A.create_question(SID, "用哪种库？", options=["SQLite", "PG"])
        assert doc["question_id"].startswith("q-") and len(doc["question_id"]) == 14
        pending = A.get_pending(SID)
        assert pending["question"] == "用哪种库？"
        assert [o["label"] for o in pending["options"]] == ["SQLite", "PG"]

    def test_问题为空被拒(self):
        with pytest.raises(A.AskError) as ei:
            A.create_question(SID, "  ")
        assert ei.value.code == "empty_question"

    def test_选项过多被拒(self):
        with pytest.raises(A.AskError) as ei:
            A.create_question(SID, "x", options=["o%d" % i for i in range(9)])
        assert ei.value.code == "too_many_options"

    def test_新问题替代旧问题(self):
        first = A.create_question(SID, "旧问题")
        second = A.create_question(SID, "新问题")
        assert A.get_pending(SID)["question"] == "新问题"
        events = [json.loads(l) for l in
                  open(A._answers_path(A.session_key(SID)), encoding="utf-8")]
        assert events[0]["type"] == "superseded"
        assert events[0]["question_id"] == first["question_id"]
        assert second["question_id"] != first["question_id"]

    def test_回答并清空待答(self):
        doc = A.create_question(SID, "选一个", options=[{"id": "a", "label": "A"}])
        rec = A.answer(SID, doc["question_id"], answers=["a"], custom="补充")
        assert rec["answers"] == ["a"] and rec["custom"] == "补充"
        assert A.get_pending(SID) is None

    def test_单选问题只允许一个选项(self):
        doc = A.create_question(SID, "单选", options=["A", "B"], allow_multiple=False)
        with pytest.raises(A.AskError) as ei:
            A.answer(SID, doc["question_id"], answers=["o1", "o2"])
        assert ei.value.code == "single_choice_only"

    def test_多选问题允许多个(self):
        doc = A.create_question(SID, "多选", options=["A", "B"], allow_multiple=True)
        rec = A.answer(SID, doc["question_id"], answers=["o1", "o2"])
        assert rec["answers"] == ["o1", "o2"]

    def test_未知选项被拒(self):
        doc = A.create_question(SID, "选", options=["A"])
        with pytest.raises(A.AskError) as ei:
            A.answer(SID, doc["question_id"], answers=["zzz"])
        assert ei.value.code == "invalid_option_id"

    def test_空答案被拒(self):
        doc = A.create_question(SID, "选", options=["A"])
        with pytest.raises(A.AskError) as ei:
            A.answer(SID, doc["question_id"], answers=[], custom="  ")
        assert ei.value.code == "empty_answer"

    def test_不接受自定义时拒绝文本(self):
        doc = A.create_question(SID, "选", options=["A"], allow_custom=False)
        with pytest.raises(A.AskError) as ei:
            A.answer(SID, doc["question_id"], custom="自己填")
        assert ei.value.code == "custom_not_allowed"

    def test_没有待答问题时回答被拒(self):
        with pytest.raises(A.AskError) as ei:
            A.answer(SID, "q-000000000000", answers=["o1"])
        assert ei.value.code == "no_pending"

    def test_旧问题_id_被拒(self):
        old = A.create_question(SID, "旧")
        A.create_question(SID, "新")
        with pytest.raises(A.AskError) as ei:
            A.answer(SID, old["question_id"], answers=["o1"])
        assert ei.value.code == "stale_question"

    def test_过期问题被惰性清理(self, monkeypatch):
        doc = A.create_question(SID, "旧问题")
        # 把 pending 的 created_at 改到 25 小时前
        path = A._pending_path(A.session_key(SID))
        raw = json.loads(open(path, encoding="utf-8").read())
        from datetime import datetime, timedelta, timezone
        raw["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        A._atomic_write(path, json.dumps(raw, ensure_ascii=False))
        assert A.get_pending(SID) is None
        assert doc["question_id"]

    def test_会话键安全(self):
        key = A.session_key("../../etc/passwd")
        assert "/" not in key and ".." not in key and len(key) == 32
        with pytest.raises(A.AskError):
            A.session_key("  ")

    def test_删除会话提问目录(self):
        A.create_question(SID, "x")
        assert A.delete_ask(SID) is True
        assert A.get_pending(SID) is None
        assert A.delete_ask(SID) is False


class TestTools:
    @pytest.fixture(autouse=True)
    def _registered(self):
        from agent.tools import ask_tools
        ask_tools.register_all(None)
        yield

    def test_ask_返回_USER_INPUT_REQUIRED(self):
        from agent.tools import ask_tools
        r = ask_tools.run_ask(SID, "选哪个？", options=["A", "B"], context="上下文")
        assert r["ok"] is False and r["error_code"] == "USER_INPUT_REQUIRED"
        assert r["question_id"].startswith("q-")
        assert "等待回答" in r["guidance"]
        assert A.get_pending(SID)["question"] == "选哪个？"

    def test_ask_无会话失败(self):
        from agent.tools import ask_tools
        r = ask_tools.run_ask("", "x")
        assert r["ok"] is False and "会话上下文" in r["error"]

    def test_present_合法文件(self):
        from agent.tools import ask_tools
        r = ask_tools.run_present(SID, ["docs/a.md", {"path": "src/b.py", "title": "B"}],
                                 note="交付")
        assert r["ok"] is True and r["count"] == 2
        assert r["presented"][1]["title"] == "B"

    @pytest.mark.parametrize("bad", ["../x", "/etc/passwd", "C:/x", "a//b", "a/./b", ""])
    def test_present_非法路径(self, bad):
        from agent.tools import ask_tools
        assert ask_tools.run_present(SID, [bad])["ok"] is False

    def test_present_数量上限(self):
        from agent.tools import ask_tools
        r = ask_tools.run_present(SID, ["f%d.md" % i for i in range(21)])
        assert r["ok"] is False and "最多" in r["error"]

    def test_工具已注册(self):
        from agent.tools import list_tools
        names = [t.get("name") for t in list_tools()]
        assert "ask_user_question" in names and "present" in names


class TestSideEvents:
    def test_ask_事件(self):
        from plugins.chat import tool_side_events
        payload = json.dumps({"ok": False, "error_code": "USER_INPUT_REQUIRED",
                              "question_id": "q-1", "question": "选？",
                              "options": [{"id": "o1", "label": "A"}],
                              "allow_multiple": False, "allow_custom": True})
        evts = tool_side_events(payload)
        assert len(evts) == 1 and evts[0]["type"] == "ask"
        assert evts[0]["ask"]["question_id"] == "q-1"

    def test_present_事件(self):
        from plugins.chat import tool_side_events
        payload = json.dumps({"ok": True, "presented": [{"path": "a.md"}],
                              "count": 1, "note": "交付"})
        evts = tool_side_events(payload)
        assert evts[0]["type"] == "present"
        assert evts[0]["files"][0]["path"] == "a.md"

    def test_普通结果无事件(self):
        from plugins.chat import tool_side_events
        assert tool_side_events('{"ok": true, "content": "hi"}') == []
        assert tool_side_events("plain text") == []
