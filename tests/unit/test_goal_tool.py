"""E1-2b `goal` 工具：注册 / 动作 / 会话解析 / 失败语义（2026-11-25）

钉死：
  · 工具在注册表里、schema 动作枚举与设计页一致；
  · 八个动作都走同一状态机（与 /goal 命令同源），complete 缺证据/block 缺 code 都拒绝；
  · 会话解析顺序：请求级绑定优先，拿不到 ⇒ ok=False（**绝不**落到共享默认键）；
  · 工具结果只回状态摘要（不把事件流塞回模型）。
"""
from __future__ import annotations

import pytest

from agent.context import goal as G
from agent.tools import goal_tools


@pytest.fixture(autouse=True)
def _root(tmp_path):
    G.set_root(str(tmp_path / "goals"))
    yield
    G.set_root(None)


@pytest.fixture(scope="module", autouse=True)
def _registered():
    goal_tools.register_all(None)
    yield


SID = "tool-sess-1"


@pytest.fixture()
def bound():
    token = G.bind_session(SID)
    yield SID
    G.reset_session(token)


class TestRegistration:
    def test_工具已注册且可被列出(self):
        from agent.tools import list_tools
        names = [t.get("name") for t in list_tools()] if isinstance(list_tools(), list) else None
        if names is None:  # 兼容返回 dict 的实现
            names = list(list_tools().get("tools", []))
        assert "goal" in names

    def test_schema_动作枚举与设计页一致(self):
        from agent.tools import get_tool_defs
        defs = get_tool_defs(whitelist=["goal"]) or []
        assert defs, "goal 应在可下发定义里"
        props = defs[0]["function"]["parameters"]["properties"]
        assert props["action"]["enum"] == list(goal_tools.ACTIONS)
        assert defs[0]["function"]["parameters"]["required"] == ["action"]


def _act(action, sid=SID, **kw):
    """直接调 handler：单元测试不经过限流/审批层（注册表 dispatch 另有专测）"""
    return goal_tools.run_goal_action(action, sid, **kw)


class TestActions:
    def test_create_status_via_registry(self, bound):
        # 只在这里走一次真实注册表 dispatch（验证接线），其余用 _act 避开限流
        from agent.tools import call
        r = call("goal", action="create", title="注册表目标", acceptance=["a", "b"])
        assert r["ok"] is True and r["state"] == "active"
        assert r["acceptance_count"] == 2

    def test_status(self, bound):
        _act("create", title="工具目标", acceptance=["a", "b"])
        r = _act("status")
        assert r["title"] == "工具目标" and r["round"] == 0

    def test_complete_必须有证据(self, bound):
        _act("create", title="目标")
        r = _act("complete")
        assert r["ok"] is False and r["code"] == "missing_evidence"
        r2 = _act("complete", evidence="tests 20 passed")
        assert r2["ok"] is True and r2["state"] == "completed"
        assert r2["evidence_provided"] is True

    def test_block_必须带合法_code(self, bound):
        _act("create", title="目标")
        assert _act("block", code="bad", reason="x")["ok"] is False
        assert _act("block", code="need_user")["ok"] is False
        r = _act("block", code="external", reason="等上游")
        assert r["ok"] is True and r["blocked_code"] == "external"

    def test_暂停恢复清除(self, bound):
        _act("create", title="目标")
        assert _act("pause", reason="等用户")["state"] == "paused"
        assert _act("resume")["state"] == "active"
        assert _act("clear")["state"] == "idle"

    def test_未知动作被拒(self, bound):
        r = _act("destroy")
        assert r["ok"] is False and "未知动作" in r["error"]

    def test_结果只回摘要不带事件流(self, bound):
        r = _act("create", title="目标", acceptance=["a"])
        assert "recent_events" not in r and "events" not in r
        assert set(("ok", "action", "state", "round", "max_rounds")) <= set(r)


class TestSessionResolution:
    def test_无会话绑定则失败且不写默认键(self):
        r = goal_tools.run_goal_action("create", "", title="无会话")
        assert r["ok"] is False and "会话上下文" in r["error"]
        assert G.get_state("__default__")["state"] == "idle"

    def test_请求级绑定优先于_trace(self):
        token = G.bind_session("bound-sid")
        try:
            assert goal_tools.resolve_session() == "bound-sid"
        finally:
            G.reset_session(token)

    def test_每个会话独立(self, bound):
        _act("create", title="会话A的目标")
        assert _act("status", sid="tool-sess-2")["state"] == "idle", "不同会话不得看到彼此的目标"
