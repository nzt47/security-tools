"""E1-1 计划模式 × 工作台 SSE（2026-11-25）

钉死三件最容易做错的事：
  1. ``/plan`` 命令**不进 LLM**（纯状态操作，确定性）；
  2. 计划模式下每次助手回复收尾时，``plan`` 事件必须**先于** ``done``
     （前端收到 done 就 break，顺序反了卡片永远不出现）；
  3. 计划提示只在计划模式激活时进 system prompt，退出后立即消失。
"""
from __future__ import annotations

import json
import sys
import types

import pytest

import memory.llm_service as llm_module
import plugins.chat as chat_module
from agent.context import plan_mode


class FakeLLM:
    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.seen = {}
        FakeLLM.instances.append(self)

    def chat_stream(self, messages, system_prompt="", max_tokens=1024, temperature=0.7,
                    on_tool_call=None, tools=None, on_reasoning=None):
        self.seen = {"messages": list(messages), "system_prompt": system_prompt}
        yield "计划"
        yield "正文"


class _Counter:
    def count(self, text):
        return len(text or "")


class _SessionMgr:
    def __init__(self):
        self.by_session = {}

    def get_session(self, sid):
        return self.by_session.get(sid)

    def create_session(self, session_id=None, title=""):
        self.by_session[session_id] = {"id": session_id, "messages": []}
        return {"id": session_id}

    def get_messages(self, sid, limit=50):
        msgs = list(self.by_session.get(sid, {}).get("messages", []))
        return msgs if limit <= 0 else msgs[-limit:]

    def add_message(self, sid, role, content, **kw):
        entry = {"role": role, "content": content}
        entry.update(kw)
        self.by_session.setdefault(sid, {"id": sid}).setdefault("messages", []).append(entry)
        return entry

    def set_current(self, sid):
        return True


class _Yunshu:
    def __init__(self):
        self._memory = types.SimpleNamespace(add_message=lambda *a, **k: "ts")
        self.chat_calls = []

    def chat(self, question, *, session_id=None, session_mgr=None):
        self.chat_calls.append(question)
        return "完整链路回答"

    def last_response_metadata(self, session_id=None):
        return {}

    def _set_turn_state(self, session_id=None, **kw):
        pass


@pytest.fixture(autouse=True)
def _root(tmp_path):
    plan_mode.set_root(str(tmp_path / "plan_mode"))
    yield
    plan_mode.set_root(None)


@pytest.fixture()
def env(monkeypatch):
    from flask import Flask
    from plugins.chat import bp

    FakeLLM.instances = []
    monkeypatch.setattr(llm_module, "LLMService", lambda **kw: FakeLLM(**kw), raising=True)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "sk-" + "x" * 40)
    monkeypatch.setenv("LLM_MODEL", "deepseek-flash")

    fake = types.ModuleType("app_server")
    fake._Yunshu = _Yunshu()
    fake._session_mgr = _SessionMgr()
    fake._get_current_session_id = lambda: "sess-x"
    fake._get_token_counter = lambda: _Counter()
    fake._cfg = types.SimpleNamespace(get=lambda s, k, default=None: default)
    fake.logger = types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None,
        error=lambda *a, **k: None, debug=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "app_server", fake)

    app = Flask("wb_plan_mode")
    app.config.update(TESTING=True)
    app.register_blueprint(bp)
    return app.test_client(), fake


def _events(resp) -> list:
    body = b"".join(resp.response).decode("utf-8", errors="replace")
    out = []
    for line in body.splitlines():
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload:
                out.append(json.loads(payload))
    return out


def _post(client, message, sid="s1"):
    return client.post("/api/chat/stream", json={"message": message, "session_id": sid})


class TestPlanCommand:
    def test_进入计划模式_不调用模型(self, env):
        client, fake = env
        events = _events(_post(client, "/plan"))
        assert FakeLLM.instances == [], "/plan 是纯状态命令，不应调用模型"
        kinds = [e["type"] for e in events]
        assert "plan" in kinds and kinds[-1] == "done"
        plan_evt = [e for e in events if e["type"] == "plan"][0]
        assert plan_evt["plan"]["plan_mode"] is True
        assert plan_evt["plan"]["status"] == "planning"
        # 一轮问答已落盘（命令在历史里可见）
        msgs = fake._session_mgr.get_messages("s1", limit=0)
        assert [m["role"] for m in msgs] == ["user", "assistant"]

    def test_退出计划模式(self, env):
        client, fake = env
        plan_mode.enter("s1")
        events = _events(_post(client, "/plan exit"))
        plan_evt = [e for e in events if e["type"] == "plan"][0]
        assert plan_evt["plan"]["plan_mode"] is False

    def test_状态查询(self, env):
        client, _fake = env
        plan_mode.enter("s1")
        plan_mode.record_turn_plan("s1", "计划正文")
        events = _events(_post(client, "/plan status"))
        text = "".join(e.get("text", "") for e in events if e["type"] == "chunk")
        assert "pending_approval" in text or "待批准" in text

    def test_未知子命令只回提示不调模型(self, env):
        client, _fake = env
        events = _events(_post(client, "/plan 乱写"))
        assert FakeLLM.instances == []
        text = "".join(e.get("text", "") for e in events if e["type"] == "chunk")
        assert "未知计划命令" in text

    def test_回滚命令生成新版本且保留计划模式(self, env):
        client, _fake = env
        plan_mode.enter("s1")
        plan_mode.record_turn_plan("s1", "第一版内容")
        plan_mode.record_turn_plan("s1", "第二版内容")
        events = _events(_post(client, "/plan rollback 1"))
        assert FakeLLM.instances == [], "回滚是纯状态操作，不应调用模型"
        plan = [e for e in events if e["type"] == "plan"][-1]["plan"]
        assert plan["version"] == 3
        assert "第一版内容" in plan["plan"]
        assert plan["plan_mode"] is True and plan["status"] == "pending_approval"

    def test_回滚缺版本号只回用法提示(self, env):
        client, _fake = env
        plan_mode.enter("s1")
        events = _events(_post(client, "/plan rollback"))
        text = "".join(e.get("text", "") for e in events if e["type"] == "chunk")
        assert "用法" in text and "rollback" in text
        assert FakeLLM.instances == []


class TestPlanTurn:
    def test_计划模式下回复收尾产生计划事件_且事件先于_done(self, env):
        client, fake = env
        plan_mode.enter("s1")
        events = _events(_post(client, "帮我规划一下"))
        kinds = [e["type"] for e in events]
        assert "plan" in kinds
        assert kinds.index("plan") < kinds.index("done"), "plan 事件必须先于 done"
        plan_evt = [e for e in events if e["type"] == "plan"][0]["plan"]
        assert plan_evt["version"] == 1
        assert plan_evt["status"] == "pending_approval"
        assert "计划" in plan_evt["plan"] and "正文" in plan_evt["plan"]
        # 计划提示进了 system prompt
        seen = FakeLLM.instances[0].seen
        assert "[计划模式]" in seen["system_prompt"]

    def test_非计划模式不产生计划事件_提示也不进_system(self, env):
        client, fake = env
        events = _events(_post(client, "普通问题"))
        assert "plan" not in [e["type"] for e in events]
        assert "[计划模式]" not in FakeLLM.instances[0].seen["system_prompt"]
        assert plan_mode.get_state("s1")["version"] == 0

    def test_退出后提示立即消失(self, env):
        client, fake = env
        plan_mode.enter("s1")
        _events(_post(client, "第一轮"))
        plan_mode.exit("s1")
        _events(_post(client, "第二轮"))
        assert "[计划模式]" not in FakeLLM.instances[-1].seen["system_prompt"]

    def test_批准命令改写成执行指令并带出批准态(self, env):
        client, fake = env
        plan_mode.enter("s1")
        plan_mode.record_turn_plan("s1", "步骤一")
        events = _events(_post(client, "/plan approve"))
        # 先发批准态（卡片据此收起），再跑一轮执行
        plan_evt = [e for e in events if e["type"] == "plan"][0]["plan"]
        assert plan_evt["status"] == "approved" and plan_evt["plan_mode"] is False
        assert FakeLLM.instances, "批准后应继续执行（同一对话链路）"
        assert "开始按计划执行" in FakeLLM.instances[-1].seen["messages"][-1]["content"]
        assert plan_mode.get_state("s1")["plan_mode"] is False

    def test_拒绝命令保留计划模式并继续规划(self, env):
        client, fake = env
        plan_mode.enter("s1")
        plan_mode.record_turn_plan("s1", "步骤一")
        events = _events(_post(client, "/plan reject 先补测试"))
        first_plan = [e for e in events if e["type"] == "plan"][0]["plan"]
        assert first_plan["plan_mode"] is True
        assert "先补测试" in FakeLLM.instances[-1].seen["messages"][-1]["content"]
        plan_events = [e for e in events if e["type"] == "plan"]
        assert len(plan_events) == 2, "收尾时应有新版本的计划事件"
        assert plan_events[-1]["plan"]["version"] == 2
        assert plan_events[-1]["plan"]["status"] == "pending_approval"

    def test_提交上一条回复为计划(self, env):
        client, fake = env
        _events(_post(client, "先正常回答一轮"))
        events = _events(_post(client, "/plan submit"))
        assert FakeLLM.instances and len(FakeLLM.instances) == 1, "submit 不调用模型"
        plan_evt = [e for e in events if e["type"] == "plan"][-1]["plan"]
        assert plan_evt["version"] == 1
        assert plan_evt["status"] == "pending_approval"
        assert "计划正文" in plan_evt["plan"], "应取上一条助手回复作为计划"
