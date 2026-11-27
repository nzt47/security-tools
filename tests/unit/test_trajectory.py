"""E4-5 Inspector：会话轨迹只读投影（纯函数）契约测试。

判据见 docs/plan/2026-11-28-e4-5-inspector-design.md §6：
  - 顺序 = 存储顺序；截断 = 硬上限（正文 2000 / step 800 / 40 steps / 200 条）；
  - 白名单字段（entry: index/role/ts/chars/body/body_truncated/steps；
    step: id/title/status/detail/detail_truncated/at）；
  - limit/offset 夹取，total 是窗口外总数，truncated 只表示"还有更多"。
"""
from __future__ import annotations

from agent.context import trajectory as T


def _msg(**kw):
    base = {"role": "user", "content": "hi", "timestamp": "2026-11-28T10:00:00"}
    base.update(kw)
    return base


class TestWindow:
    def test_顺序与总数(self):
        msgs = [_msg(content=str(i)) for i in range(3)]
        doc = T.build(msgs)
        assert doc["total"] == 3
        assert [e["index"] for e in doc["entries"]] == [0, 1, 2]
        assert doc["truncated"] is False

    def test_limit_offset(self):
        msgs = [_msg(content=str(i)) for i in range(10)]
        doc = T.build(msgs, limit=3, offset=4)
        assert [e["index"] for e in doc["entries"]] == [4, 5, 6]
        assert doc["offset"] == 4 and doc["limit"] == 3
        assert doc["truncated"] is True

    def test_limit_夹到_1_200(self):
        msgs = [_msg() for _ in range(3)]
        assert T.build(msgs, limit=0)["limit"] == 1
        assert T.build(msgs, limit=9999)["limit"] == T.MAX_ENTRIES
        assert T.build(msgs, limit="abc")["limit"] == T.DEFAULT_LIMIT

    def test_offset_越界与非法(self):
        msgs = [_msg() for _ in range(3)]
        doc = T.build(msgs, offset=99)
        assert doc["entries"] == [] and doc["truncated"] is False
        assert T.build(msgs, offset="abc")["offset"] == 0
        assert T.build(msgs, offset=-5)["offset"] == 0

    def test_空会话(self):
        doc = T.build([])
        assert doc == {"total": 0, "offset": 0, "limit": T.DEFAULT_LIMIT,
                       "entries": [], "truncated": False}


class TestWhitelistAndTruncation:
    def test_entry_字段白名单(self):
        entry = T.build([_msg(content="abc", extra_secret="x")])["entries"][0]
        assert set(entry) == {"index", "role", "ts", "chars", "body",
                              "body_truncated", "steps"}
        assert "extra_secret" not in entry

    def test_正文超长截断并标记(self):
        entry = T.build([_msg(content="字" * (T.MAX_BODY_CHARS + 5))])["entries"][0]
        assert len(entry["body"]) == T.MAX_BODY_CHARS
        assert entry["body_truncated"] is True
        assert entry["chars"] == T.MAX_BODY_CHARS + 5

    def test_step_字段白名单与截断(self):
        msg = _msg(steps=[{"id": "s1", "title": "工具调用：read_file",
                           "status": "done", "at": "1",
                           "detail": "x" * (T.MAX_STEP_DETAIL_CHARS + 9),
                           "raw": "SECRET"}])
        step = T.build([msg])["entries"][0]["steps"][0]
        assert set(step) == {"id", "title", "status", "detail", "at",
                             "detail_truncated"}
        assert len(step["detail"]) == T.MAX_STEP_DETAIL_CHARS
        assert step["detail_truncated"] is True
        assert "raw" not in step

    def test_单条消息_step_数上限(self):
        msg = _msg(steps=[{"title": f"t{i}", "detail": ""}
                          for i in range(T.MAX_STEPS_PER_MESSAGE + 10)])
        steps = T.build([msg])["entries"][0]["steps"]
        assert len(steps) == T.MAX_STEPS_PER_MESSAGE

    def test_非字典_step_跳过(self):
        msg = _msg(steps=[{"title": "ok"}, "noise", None, 42])
        steps = T.build([msg])["entries"][0]["steps"]
        assert len(steps) == 1 and steps[0]["title"] == "ok"

    def test_非字典消息跳过(self):
        doc = T.build([_msg(), "noise", None, _msg(role="assistant")])
        assert doc["total"] == 2
        assert [e["role"] for e in doc["entries"]] == ["user", "assistant"]

    def test_缺字段不抛(self):
        doc = T.build([{}])
        entry = doc["entries"][0]
        assert entry["role"] == "" and entry["body"] == "" and entry["steps"] == []
