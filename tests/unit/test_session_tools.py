"""E1-4d 会话检索工具（session_search / session_trace）契约测试。

覆盖：查询归一与空查询、limit 钳制、片段剥高亮标记、索引不可用降级、
trace 的会话解析/轮次选取/摘要截断/三种降级、以及两个工具真的注册进 registry
（名字 + description 与 YAML 逐字一致由 `test_tool_definitions_yaml` 另测）。
"""
from __future__ import annotations

import pytest

from agent.context import session_index as S
from agent.tools import session_tools as ST


@pytest.fixture(autouse=True)
def _db(monkeypatch, tmp_path):
    monkeypatch.delenv(S.ENABLED_ENV, raising=False)
    monkeypatch.setenv("CP_SESSION_INDEX_PATH", str(tmp_path / "idx.db"))
    yield


MSGS = [
    {"role": "user", "content": "确认检查点的截断口径", "timestamp": "t1"},
    {"role": "assistant", "content": "20k/200k。", "timestamp": "t2",
     "steps": [{"title": "工具调用：read_file", "detail": "参数: {path: a.py}", "status": "done"},
               {"title": "思考过程", "detail": "略", "status": "done"},
               {"title": "工具调用：edit", "detail": "结果: ok", "status": "done"}]},
    {"role": "user", "content": "导出呢", "timestamp": "t3"},
    {"role": "assistant", "content": "只读会话消息。", "timestamp": "t4"},
]


class _Mgr:
    def __init__(self, messages):
        self.messages = messages

    def get_messages(self, sid, limit=50):
        return list(self.messages)


# ── session_search ──────────────────────────────────────────


def test_search_空查询被拒():
    out = ST.run_session_search("   ")
    assert out["ok"] is False and out["code"] == "empty_query"


def test_search_命中并剥掉高亮标记():
    S.index_message("sess_a", "user", "确认检查点的截断口径")
    out = ST.run_session_search("检查点")
    assert out["ok"] is True and out["count"] == 1
    hit = out["hits"][0]
    assert hit["session_id"] == "sess_a" and hit["role"] == "user"
    assert "[" not in hit["snippet"] and "]" not in hit["snippet"]
    assert "检查点" in hit["snippet"]


def test_search_limit_被钳到工具上限():
    for i in range(30):
        S.index_message("sess_a", "user", "关键词内容 %d" % i)
    out = ST.run_session_search("关键词内容", limit=999)
    assert out["count"] <= ST.MAX_SEARCH_LIMIT


def test_search_索引关闭时降级():
    import os

    os.environ[S.ENABLED_ENV] = "0"
    try:
        out = ST.run_session_search("检查点")
    finally:
        os.environ.pop(S.ENABLED_ENV, None)
    assert out["ok"] is False and out["code"] == "index_unavailable"


def test_search_片段截断到上限():
    S.index_message("sess_a", "user", "长" * 500)
    out = ST.run_session_search("长长长")
    assert len(out["hits"][0]["snippet"]) <= ST.MAX_SNIPPET_CHARS + 1


# ── session_trace ───────────────────────────────────────────


def test_trace_拿不到会话时降级(monkeypatch):
    monkeypatch.setattr(ST, "resolve_session", lambda: "")
    out = ST.run_session_trace("", 0)
    assert out["ok"] is False and out["code"] == "no_session"


def test_trace_指定轮次返回工具轨迹(monkeypatch):
    monkeypatch.setattr(ST, "_manager", lambda: _Mgr(MSGS))
    out = ST.run_session_trace("sess_a", 1)
    assert out["ok"] is True and out["returned"] == 1
    row = out["rounds"][0]
    assert row["round"] == 1
    assert row["tools"] == ["read_file", "edit"]
    titles = [s["step"] for s in row["steps"]]
    assert titles == ["工具调用：read_file", "工具调用：edit"]
    assert all(len(s["detail"]) <= ST.MAX_SNIPPET_CHARS + 1 for s in row["steps"])


def test_trace_缺省回最近三轮(monkeypatch):
    monkeypatch.setattr(ST, "_manager", lambda: _Mgr(MSGS))
    out = ST.run_session_trace("sess_a", 0)
    assert out["returned"] == 2  # 这个会话只有 2 轮


def test_trace_轮次不存在(monkeypatch):
    monkeypatch.setattr(ST, "_manager", lambda: _Mgr(MSGS))
    out = ST.run_session_trace("sess_a", 9)
    assert out["ok"] is False and out["code"] == "round_not_found"


def test_trace_会话没有消息(monkeypatch):
    monkeypatch.setattr(ST, "_manager", lambda: _Mgr([]))
    out = ST.run_session_trace("sess_a", 0)
    assert out["ok"] is False and out["code"] == "not_found"


def test_trace_没有管理器(monkeypatch):
    monkeypatch.setattr(ST, "_manager", lambda: None)
    out = ST.run_session_trace("sess_a", 0)
    assert out["ok"] is False and out["code"] == "no_session_manager"


# ── 注册 ────────────────────────────────────────────────────


def test_两个工具注册进_registry():
    from agent import tools as _tools

    ST.register_all()
    for name in ("session_search", "session_trace"):
        assert name in _tools._registry
        assert _tools._registry[name].get("description")
