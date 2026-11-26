"""E2-3 delegate 的 fork 参数解析（不真的跑执行器）"""
from __future__ import annotations

from agent.tools import subagent_tools as st


def test_未开_fork_不读会话():
    meta, err = st.build_fork_metadata({})
    assert meta == {} and err is None
    meta2, err2 = st.build_fork_metadata({"fork": "false"})
    assert meta2 == {} and err2 is None


def test_fork_收集成功并去掉_ok_键():
    seen = {}

    def collect(sid, **kw):
        seen["sid"] = sid
        seen.update(kw)
        return {"ok": True, "from_session": sid, "messages": [{"role": "user", "content": "上一轮"}],
                "chars": 3, "rounds": 1, "truncated": False, "summarized": False, "note": "n"}

    meta, err = st.build_fork_metadata(
        {"fork": True, "fork_messages": 3, "fork_summary": "true"},
        resolve=lambda: "s-9", collect=collect)
    assert err is None
    assert seen == {"sid": "s-9", "rounds": 3, "summarize": True}
    fork = meta["fork"]
    assert "ok" not in fork and fork["from_session"] == "s-9"
    assert fork["messages"][0]["content"] == "上一轮"


def test_fork_拿不到会话时明确失败():
    def collect(sid, **kw):
        return {"ok": False, "code": "no_session", "error": "没有会话"}

    meta, err = st.build_fork_metadata({"fork": True}, resolve=lambda: "", collect=collect)
    assert meta == {}
    assert err["ok"] is False and err["error_code"] == "E_FORK_NO_CONTEXT"
    assert "没有会话" in err["error"]


def test_fork_默认六轮():
    seen = {}

    def collect(sid, **kw):
        seen.update(kw)
        return {"ok": True, "from_session": sid, "messages": [], "chars": 0,
                "rounds": 0, "truncated": False, "summarized": False, "note": "n"}

    st.build_fork_metadata({"fork": 1}, resolve=lambda: "s", collect=collect)
    assert seen["rounds"] == 6 and seen["summarize"] is False

def test_HTTP入口_fork写入metadata(monkeypatch):
    from agent.server_routes import routes_subagent as rr
    from agent.subagent import fork_context as fc

    seen = {}

    def fake(sid, **kw):
        seen["sid"] = sid
        seen.update(kw)
        return {"ok": True, "from_session": sid,
                "messages": [{"role": "user", "content": "上一轮"}],
                "chars": 3, "rounds": 1, "truncated": False, "summarized": False,
                "note": fc.FORK_NOTE}

    monkeypatch.setattr(fc, "collect_parent_context", fake)
    ctx = rr._build_context(
        {"task": "x", "fork": True, "session_id": "s-1", "fork_messages": 2,
         "fork_summary": "true"},
        "把交接文档补完")
    assert seen == {"sid": "s-1", "rounds": 2, "summarize": True}
    assert ctx.metadata["fork"]["from_session"] == "s-1"
    assert "ok" not in ctx.metadata["fork"]


def test_HTTP入口_fork缺session_id直接拒绝(monkeypatch):
    import pytest

    from agent.server_routes import routes_subagent as rr

    with pytest.raises(rr._FieldError) as ei:
        rr._build_context({"fork": True}, "把交接文档补完")
    assert ei.value.field == "session_id"


def test_HTTP入口_未开fork不碰会话(monkeypatch):
    from agent.server_routes import routes_subagent as rr
    from agent.subagent import fork_context as fc

    def boom(*a, **kw):
        raise AssertionError("不应读取父会话")

    monkeypatch.setattr(fc, "collect_parent_context", boom)
    ctx = rr._build_context({"task": "x"}, "把交接文档补完")
    assert ctx.metadata == {}

def test_fork_view_只回元信息不回正文():
    from agent.server_routes import routes_subagent as rr

    class Ctx:
        metadata = {"fork": {"from_session": "s-1", "rounds": 3, "chars": 120,
                             "truncated": False, "summarized": True,
                             "messages": [{"role": "user", "content": "秘密正文"}],
                             "note": "n"}}

    view = rr._fork_view(Ctx())
    assert view == {"from_session": "s-1", "rounds": 3, "chars": 120,
                    "truncated": False, "summarized": True}
    assert "秘密正文" not in str(view)


def test_fork_view_未开fork为空():
    from agent.server_routes import routes_subagent as rr

    class Ctx:
        metadata = {}

    assert rr._fork_view(Ctx()) == {}
    assert rr._fork_view(object()) == {}
