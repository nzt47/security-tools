"""E1-4a 会话标题：纯函数口径 + LLM/回退/不覆盖人工标题（含线程排入）。

全部用假会话管理器 + 假 LLM（不发真请求、不依赖 .env）。
"""
from __future__ import annotations

import pytest

from agent.context import session_title as T


class _Mgr:
    def __init__(self, messages, meta=None):
        self.messages = list(messages)
        self.meta = dict(meta or {})
        self.set_calls = []

    def get_messages(self, session_id, limit=50):
        return list(self.messages)

    def get_session_metadata(self, session_id):
        return dict(self.meta)

    def set_title(self, session_id, title, source="auto"):
        self.set_calls.append((session_id, title, source))
        self.meta["title"] = title
        self.meta["title_source"] = source
        return True


class _LLM:
    def __init__(self, reply="", exc=None):
        self.reply = reply
        self.exc = exc
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.exc:
            raise self.exc
        return self.reply


def _msgs(n_user=2, text="请帮我看一下检查点功能"):
    rows = []
    for i in range(n_user):
        rows.append({"role": "user", "content": "%s（%d）" % (text, i + 1)})
        rows.append({"role": "assistant", "content": "好的"})
    return rows


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv(T.ENABLED_ENV, raising=False)
    yield


# ── 纯函数 ──────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ('"检查点与变更卡"', "检查点与变更卡"),
    ("标题：检查点与变更卡。", "检查点与变更卡。"),
    ("「会话标题: 修复 CI」", "修复 CI"),
    ("  \n 多行\n第二行 ", "多行"),
    ("", ""),
])
def test_sanitize_title(raw, expected):
    assert T.sanitize_title(raw) == expected


def test_sanitize_title_截断到上限():
    raw = "很" * 100
    assert len(T.sanitize_title(raw)) == T.MAX_TITLE_CHARS


def test_sanitize_title_可指定更短上限():
    assert T.sanitize_title("一二三四五六", limit=3) == "一二三"


def test_fallback_title_取首条用户消息():
    assert T.fallback_title(_msgs(1, "帮我修一下登录")) .startswith("帮我修一下登录")


def test_fallback_title_无用户消息返回空():
    assert T.fallback_title([{"role": "assistant", "content": "你好"}]) == ""


def test_build_messages_裁到上限且最后一条是要求():
    rows = T.build_messages(_msgs(10, "长" * 400))
    assert rows[-1]["role"] == "user" and "标题" in rows[-1]["content"]
    assert len(rows) <= T.MAX_CONTEXT_MESSAGES + 1
    assert sum(len(r["content"]) for r in rows) <= T.MAX_INPUT_CHARS + 40


def test_build_messages_空会话返回空():
    assert T.build_messages([]) == []


# ── 是否该生成 ──────────────────────────────────────────────


@pytest.mark.parametrize("source,expected", [
    ("", True), ("placeholder", True), ("fallback", True),
    ("manual", False), ("auto", False),
])
def test_should_generate_按来源判定(source, expected):
    meta = {} if source == "" else {"title_source": source}
    assert T.should_generate(meta, _msgs(2)) is expected


def test_should_generate_一轮不够():
    assert T.should_generate({"title_source": "placeholder"}, _msgs(1)) is False


def test_开关关闭则永不生成(monkeypatch):
    monkeypatch.setenv(T.ENABLED_ENV, "0")
    assert T.enabled() is False
    assert T.should_generate({"title_source": "placeholder"}, _msgs(3)) is False


# ── generate ────────────────────────────────────────────────


def test_generate_成功写_auto_标题():
    mgr = _Mgr(_msgs(2), {"title_source": "placeholder"})
    llm = _LLM("检查点与变更卡")
    out = T.generate("s1", mgr=mgr, llm=llm)
    assert out == {"title": "检查点与变更卡", "source": "auto"}
    assert mgr.set_calls == [("s1", "检查点与变更卡", "auto")]
    # 关 thinking（口径 §6）
    assert llm.calls[0][1]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_generate_LLM_失败回退首条消息_且来源记_fallback():
    mgr = _Mgr(_msgs(2, "帮我修一下登录"), {"title_source": "placeholder"})
    out = T.generate("s1", mgr=mgr, llm=_LLM(exc=RuntimeError("boom")))
    assert out["source"] == "fallback"
    assert out["title"].startswith("帮我修一下登录")
    assert mgr.set_calls[0][2] == "fallback"


def test_generate_人工标题不覆盖():
    mgr = _Mgr(_msgs(3), {"title_source": "manual", "title": "我自己起的"})
    out = T.generate("s1", mgr=mgr, llm=_LLM("不该出现"))
    assert out == {} and mgr.set_calls == []


def test_generate_已自动生成的不重复():
    mgr = _Mgr(_msgs(3), {"title_source": "auto", "title": "旧标题"})
    assert T.generate("s1", mgr=mgr, llm=_LLM("新标题")) == {}
    assert mgr.set_calls == []


def test_generate_一轮不生成():
    mgr = _Mgr(_msgs(1), {"title_source": "placeholder"})
    assert T.generate("s1", mgr=mgr, llm=_LLM("x")) == {}


def test_generate_写盘失败返回空():
    class _Bad(_Mgr):
        def set_title(self, *a, **k):
            return False

    mgr = _Bad(_msgs(2), {"title_source": "placeholder"})
    assert T.generate("s1", mgr=mgr, llm=_LLM("标题")) == {}


def test_generate_没有管理器返回空且不抛(monkeypatch):
    monkeypatch.setattr(T, "_default_manager", lambda: None)
    assert T.generate("s1", llm=_LLM("x")) == {}


# ── 排入线程 ────────────────────────────────────────────────


def test_maybe_schedule_条件不满足不排():
    mgr = _Mgr(_msgs(1), {"title_source": "placeholder"})
    assert T.maybe_schedule("s1", mgr=mgr) is False


def test_maybe_schedule_满足则排入并最终写标题(monkeypatch):
    mgr = _Mgr(_msgs(2), {"title_source": "placeholder"})
    written = {}

    def _fake_generate(session_id, mgr=None, llm=None):
        written.update({"sid": session_id})
        return {"title": "t", "source": "auto"}

    monkeypatch.setattr(T, "generate", _fake_generate)
    assert T.maybe_schedule("s1", mgr=mgr) is True
    import time
    for _ in range(50):
        if written:
            break
        time.sleep(0.02)
    assert written.get("sid") == "s1"
