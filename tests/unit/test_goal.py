"""E1-2a Goal：事件溯源 / 投影 / 命令族（2026-11-25）

钉死设计页 `docs/plan/2026-11-25-e1-2-goal-design.md` 的硬约束：
  · events.jsonl 是唯一事实源：删掉 state.json 后必须能重放出同样状态；
  · 单目标：create 自动清旧目标（事件里可见 goal_cleared），事件永不删；
  · complete 必须带 evidence、block 必须带 code+reason；max_rounds 1–1000；
  · record_round 幂等（同 turn_id 不重复计数），到上限写 blocked(code=…budget)；
  · `/goal` 命令族是纯状态操作（不调用模型），回复里的 goal 事件先于 done；
  · 目录名 = sha256(session_id) 前 32 位（CodeQL 三铁律），会话删除可清理。
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from agent.context import goal as G


@pytest.fixture(autouse=True)
def _root(tmp_path):
    G.set_root(str(tmp_path / "goals"))
    yield
    G.set_root(None)


SID = "sess-goal-1"


class TestStateMachine:
    def test_默认态_idle_且不落文件(self):
        st = G.get_state(SID)
        assert st["state"] == "idle" and st["goal_id"] == "" and st["round"] == 0

    def test_创建目标(self):
        st = G.create(SID, "把 E1-2 做完", why="Phase 1", acceptance=["a", "b"])
        assert st["state"] == "active"
        assert st["title"] == "把 E1-2 做完"
        assert st["acceptance"] == ["a", "b"]
        assert st["round"] == 0 and st["max_rounds"] == G.DEFAULT_MAX_ROUNDS
        assert len(st["goal_id"]) == 12

    def test_单目标_再次创建自动清旧(self):
        first = G.create(SID, "旧目标")
        second = G.create(SID, "新目标")
        assert second["goal_id"] != first["goal_id"]
        events = [e["type"] for e in G._read_events(G.session_key(SID))]
        assert "goal_cleared" in events
        assert events[-2:] == ["goal_cleared", "goal_created"], events

    def test_空标题被拒(self):
        with pytest.raises(G.GoalError) as ei:
            G.create(SID, "   ")
        assert ei.value.code == "empty_title"

    @pytest.mark.parametrize("rounds", [0, 1001, -3])
    def test_轮次上限越界被拒(self, rounds):
        with pytest.raises(G.GoalError) as ei:
            G.create(SID, "x", max_rounds=rounds)
        assert ei.value.code == "invalid_max_rounds"

    def test_暂停_恢复(self):
        G.create(SID, "目标")
        assert G.pause(SID, "等用户").get("state") == "paused"
        st = G.resume(SID)
        assert st["state"] == "active" and st["blocked"] is None

    def test_无目标时暂停被拒(self):
        with pytest.raises(G.GoalError) as ei:
            G.pause(SID)
        assert ei.value.code == "no_goal"

    def test_完成必须有证据(self):
        G.create(SID, "目标")
        with pytest.raises(G.GoalError) as ei:
            G.complete(SID, "  ")
        assert ei.value.code == "missing_evidence"
        st = G.complete(SID, "doc.md 已提交，测试 20 passed")
        assert st["state"] == "completed"
        assert "doc.md" in st["evidence"]

    def test_阻塞必须合法_code_与原因(self):
        G.create(SID, "目标")
        with pytest.raises(G.GoalError) as ei:
            G.block(SID, "whatever", "原因")
        assert ei.value.code == "invalid_block_code"
        with pytest.raises(G.GoalError) as ei2:
            G.block(SID, "need_user", "")
        assert ei2.value.code == "missing_reason"
        st = G.block(SID, "need_user", "需要用户确认口径")
        assert st["state"] == "blocked"
        assert st["blocked"]["code"] == "need_user"

    def test_轮次递增与同_turn_幂等(self):
        G.create(SID, "目标", max_rounds=5)
        st1 = G.record_round(SID, "turn-1")
        assert st1["round"] == 1
        st2 = G.record_round(SID, "turn-1")
        assert st2["round"] == 1, "同一 turn_id 不得重复计数"
        st3 = G.record_round(SID, "turn-2")
        assert st3["round"] == 2

    def test_轮次上限触发_budget_阻塞(self):
        G.create(SID, "目标", max_rounds=2)
        G.record_round(SID, "t1")
        st = G.record_round(SID, "t2")
        assert st["state"] == "blocked"
        assert "budget" in st["blocked"]["reason"]

    def test_暂停时不计轮次(self):
        G.create(SID, "目标")
        G.pause(SID)
        st = G.record_round(SID, "t1")
        assert st["round"] == 0 and st["state"] == "paused"

    def test_补充验收条件(self):
        G.create(SID, "目标", acceptance=["a"])
        st = G.accept(SID, "b")
        assert st["acceptance"] == ["a", "b"]
        st2 = G.accept(SID, "b")
        assert st2["acceptance"] == ["a", "b"], "重复条目不得重复追加"

    def test_清除后_idle(self):
        G.create(SID, "目标")
        st = G.clear(SID)
        assert st["state"] == "idle" and st["goal_id"] == ""
        # 事件流保留（可回放）
        assert len(G._read_events(G.session_key(SID))) >= 2

    def test_删掉投影后由事件流重放(self):
        G.create(SID, "目标", acceptance=["x"], max_rounds=9)
        G.record_round(SID, "t1")
        key = G.session_key(SID)
        import os
        os.remove(G._state_path(key))
        st = G.get_state(SID)
        assert st["title"] == "目标" and st["round"] == 1 and st["max_rounds"] == 9
        assert st["acceptance"] == ["x"]


class TestDirectiveAndPaths:
    def test_系统提示只在_active_时出现(self):
        assert G.system_directive(SID) == ""
        G.create(SID, "目标A", acceptance=["可以验收"])
        text = G.system_directive(SID)
        assert "目标A" in text and "可以验收" in text
        G.pause(SID)
        assert G.system_directive(SID) == ""

    def test_续跑提示只在_active_时出现(self):
        assert G.next_prompt(SID) is None
        G.create(SID, "目标B")
        prompt = G.next_prompt(SID)
        assert "目标B" in prompt and "1/" in prompt

    def test_会话键哈希且不穿透(self):
        key = G.session_key("../../etc/passwd")
        assert "/" not in key and ".." not in key and len(key) == 32
        assert G.session_key("sess-goal-1") == G.session_key("sess-goal-1")
        with pytest.raises(G.GoalError):
            G.session_key("  ")

    def test_删除目标目录(self):
        G.create(SID, "目标")
        assert G.delete_goal(SID) is True
        assert G.get_state(SID)["state"] == "idle"
        assert G.delete_goal(SID) is False


# ════════════════════════════════════════════════════════════
#  /goal 命令族（SSE）
# ════════════════════════════════════════════════════════════

class FakeLLM:
    instances: list = []

    def __init__(self, **kwargs):
        FakeLLM.instances.append(self)


class _SessionMgr:
    def __init__(self):
        self.by_session = {}

    def get_session(self, sid):
        return self.by_session.get(sid)

    def create_session(self, session_id=None, title=""):
        self.by_session[session_id] = {"id": session_id, "messages": []}
        return {"id": session_id}

    def get_messages(self, sid, limit=50):
        return list(self.by_session.get(sid, {}).get("messages", []))

    def add_message(self, sid, role, content, **kw):
        entry = {"role": role, "content": content}
        entry.update(kw)
        self.by_session.setdefault(sid, {"id": sid}).setdefault("messages", []).append(entry)
        return entry

    def set_current(self, sid):
        return True


@pytest.fixture()
def client(monkeypatch):
    from flask import Flask
    from plugins.chat import bp
    import memory.llm_service as llm_module

    FakeLLM.instances = []
    monkeypatch.setattr(llm_module, "LLMService", lambda **kw: FakeLLM(**kw), raising=True)
    fake = types.ModuleType("app_server")
    fake._Yunshu = types.SimpleNamespace()
    fake._session_mgr = _SessionMgr()
    fake._get_current_session_id = lambda: SID
    fake._get_token_counter = lambda: None
    fake._cfg = types.SimpleNamespace(get=lambda s, k, default=None: default)
    fake.logger = types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None,
        error=lambda *a, **k: None, debug=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "app_server", fake)
    app = Flask("goal_chat")
    app.config.update(TESTING=True)
    app.register_blueprint(bp)
    return app.test_client()


def _events(resp):
    body = b"".join(resp.response).decode("utf-8", "replace")
    out = []
    for line in body.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                out.append(json.loads(payload))
    return out


def _post(client, message):
    return client.post("/api/chat/stream", json={"message": message, "session_id": SID})


class TestGoalCommands:
    def test_无目标时状态查询(self, client):
        events = _events(_post(client, "/goal"))
        text = "".join(e.get("text", "") for e in events if e["type"] == "chunk")
        assert "没有目标" in text
        assert FakeLLM.instances == [], "纯状态命令不得调用模型"

    def test_set_创建并回显(self, client):
        events = _events(_post(client, "/goal set 把目标系统做完"))
        text = "".join(e.get("text", "") for e in events if e["type"] == "chunk")
        assert "已创建目标" in text and "把目标系统做完" in text
        goal_evt = [e for e in events if e["type"] == "goal"][0]["goal"]
        assert goal_evt["state"] == "active" and goal_evt["title"] == "把目标系统做完"
        assert FakeLLM.instances == []

    def test_goal_事件先于_done(self, client):
        events = _events(_post(client, "/goal set X"))
        kinds = [e["type"] for e in events]
        assert kinds.index("goal") < kinds.index("done")

    def test_accept_pause_resume_clear(self, client):
        _events(_post(client, "/goal set A"))
        events = _events(_post(client, "/goal accept 验收1"))
        assert "验收1" in "".join(e.get("text", "") for e in events if e["type"] == "chunk")
        events = _events(_post(client, "/goal pause 等确认"))
        assert "暂停" in "".join(e.get("text", "") for e in events if e["type"] == "chunk")
        assert G.get_state(SID)["state"] == "paused"
        _events(_post(client, "/goal resume"))
        assert G.get_state(SID)["state"] == "active"
        _events(_post(client, "/goal clear"))
        assert G.get_state(SID)["state"] == "idle"

    def test_未知子命令只回提示(self, client):
        events = _events(_post(client, "/goal 乱写"))
        text = "".join(e.get("text", "") for e in events if e["type"] == "chunk")
        assert "未知目标命令" in text
        assert FakeLLM.instances == []

    def test_命令回复落盘(self, client):
        _events(_post(client, "/goal set 落盘检查"))
        msgs = client.application  # noqa: F841 仅为保持 fixture 引用
        from app_server import _session_mgr
        assert [m["role"] for m in _session_mgr.get_messages(SID)] == ["user", "assistant"]
