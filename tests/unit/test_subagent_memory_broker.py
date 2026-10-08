"""P3 分身记忆 brokered 档守卫 —— 只读注入、域越界、fail-soft、不新增工具

本文件锁的是**接线与边界**（纯逻辑在 agent/memory/broker.py）：

  1. 默认档 none：给 memory_scope / memory_provider **不改变任何行为**
     （constraints/manifest 逐字相等；recall 调用 == 0；无 subagent.memory.* 审计）。
  2. brokered：②约束恰多一条带来源标注的记忆行；③已有成果逐字不变；
     记忆正文**不进** system_prompt（对 ChannelInvocation 实参断言）。
  3. 只读：spy recall_fn 记录调用；绝不出现任何写/存调用。
  4. 域越界：scope.workspace_id 与委派 workspace 不一致 ⇒ 不注入且如实 degraded。
  5. fail-soft：recall_fn 抛异常 ⇒ 委派照常完成，outcome.memory.degraded 非空。
  6. 不新增工具：brokered 与 none 档的 SubAgentToolset 清单逐字相同。

【不易】用假通道捕获真入参、spy recall 捕获调用，不跑真模型、不碰真记忆库。
"""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest
from flask import Flask

from agent.memory.broker import (
    SCOPE_KEYS,
    assemble_brokered_context,
    render_constraint,
)
from agent.server_routes.routes_subagent import register_routes
from agent.subagent.channel import RawOutput
from agent.subagent.container import SubagentConfig
from agent.subagent.delegation import DelegationContext
from agent.subagent.executor import DelegationExecutor
from agent.subagent.memory_broker import (
    MEMORY_SCOPE_KEYS,
    attach_memory_metadata,
    memory_view,
    resolve_memory_config,
)
from agent.subagent.toolset import SubAgentToolset
from agent.tools.subagent_tools import _result_from_outcome

PROBE = "记忆探针_A7：仓库使用 tabs 缩进（历史偏好）"
WS = "ws_aaaa1111bbbb2222"
TENANT = "tenant_alpha"
DELEGATION_ID = "dlg-fixed-0001"


# ======================================================================
#  替身
# ======================================================================


class RecordingChannel:
    """假通道：捕获真的传进来的 ChannelInvocation"""

    def __init__(self, payload: Dict[str, Any] | None = None):
        self.invocations: list = []
        self.payload = payload or {"status": "done", "summary": "ok", "artifacts": []}

    def __call__(self, invocation):
        self.invocations.append(invocation)
        return RawOutput(stdout=json.dumps(self.payload), returncode=0, duration_ms=1.0)


class RecordingAudit:
    def __init__(self):
        self.events: list = []

    def record(self, action, actor=None, subject="", payload=None, status=""):
        self.events.append({"action": action, "payload": dict(payload or {}), "status": status})

    def actions(self):
        return [e["action"] for e in self.events]


class FakeEntry:
    def __init__(self, entry_id: str, content: str):
        self.id = entry_id
        self.content_redacted = content


class SpyStore:
    """假的只读记忆源：记录 recall 调用；任何写/存调用即失败"""

    def __init__(self, entries=None, exc: Exception | None = None):
        self.entries = list(entries or [])
        self.exc = exc
        self.calls: list = []
        self.writes = 0

    def recall(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.exc is not None:
            raise self.exc
        return list(self.entries)

    def write(self, *a, **k):
        self.writes += 1
        raise AssertionError("brokered 档不得写记忆")

    def save(self, *a, **k):
        self.writes += 1
        raise AssertionError("brokered 档不得 save")


def _broker_from_store(store: SpyStore):
    def _broker(query, *, scope=None, expected_tenant_id="", expected_workspace_id="",
                expected_subject_id="", **kwargs):
        return assemble_brokered_context(
            query, scope=scope, recall_fn=store.recall,
            expected_tenant_id=expected_tenant_id,
            expected_workspace_id=expected_workspace_id,
            expected_subject_id=expected_subject_id)
    return _broker


def _ctx(metadata=None, tenant_id: str = TENANT) -> DelegationContext:
    return DelegationContext(
        goal="一个足够长的目标任务", constraints=["只读"],
        prior_artifacts=["ref://a.md"], prohibitions=["不得联网"],
        artifact_format="json", budget_tokens=100, timeout_seconds=10,
        callback_url="ui://sync", tenant_id=tenant_id,
        delegation_id=DELEGATION_ID, delegate_actor="sub_agent:fixed",
        metadata=dict(metadata or {}))


def _read_task_file(invocation) -> Dict[str, Any]:
    with open(invocation.task_file, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ======================================================================
#  ① 默认档 none：逐字不变
# ======================================================================


class TestDefaultNoneUnchanged:
    def test_默认档_给scope与provider不改变任何行为(self):
        store = SpyStore([FakeEntry("m1", PROBE)])
        audit = RecordingAudit()

        ch0 = RecordingChannel()
        ex0 = DelegationExecutor(channel=ch0, audit=audit, trusted=True,
                                 memory_broker=_broker_from_store(store))
        out0 = ex0.execute(_ctx(), tools=["read_file"], authorized_capabilities=["read_file"])
        tf0 = _read_task_file(ch0.invocations[0])

        meta = {"memory_mode": "none",
                "memory_scope": {"tenant_id": TENANT, "workspace_id": WS},
                "memory_provider": "provider-x"}
        ch1 = RecordingChannel()
        ex1 = DelegationExecutor(channel=ch1, audit=audit, trusted=True,
                                 memory_broker=_broker_from_store(store))
        out1 = ex1.execute(_ctx(metadata=meta), tools=["read_file"],
                           authorized_capabilities=["read_file"])
        tf1 = _read_task_file(ch1.invocations[0])

        assert tf0["constraints"] == tf1["constraints"] == ["只读"]
        assert out0.toolset == out1.toolset
        assert store.calls == [], "默认档绝不触达记忆取用"
        assert not [a for a in audit.actions() if a.startswith("subagent.memory.")]
        assert out1.memory == {}
        assert "memory" not in _result_from_outcome(out1, _ctx(metadata=meta))


# ======================================================================
#  ② brokered 注入：恰一条约束、正文不进 system prompt
# ======================================================================


class TestBrokeredInjection:
    def test_brokered_约束恰多一条且正文不进systemprompt(self):
        store = SpyStore([FakeEntry("m1", PROBE)])
        audit = RecordingAudit()
        meta = {"memory_mode": "brokered",
                "memory_scope": {"tenant_id": TENANT, "workspace_id": WS}}
        ch = RecordingChannel()
        ex = DelegationExecutor(channel=ch, audit=audit, trusted=True,
                                memory_broker=_broker_from_store(store))
        ctx = _ctx(metadata=meta)
        out = ex.execute(ctx, tools=["read_file"], authorized_capabilities=["read_file"])

        assert out.ok is True
        # 原 ctx 不被就地改（frozen + replace）
        assert list(ctx.constraints) == ["只读"]
        tf = _read_task_file(ch.invocations[0])
        assert len(tf["constraints"]) == 2, "brokered 只追加恰一条记忆行"
        assert tf["constraints"][0] == "只读"
        memory_line = tf["constraints"][1]
        assert memory_line.startswith("[记忆 域=")
        assert PROBE in memory_line
        # ③已有成果逐字不变
        assert tf["prior_artifacts"] == ["ref://a.md"]
        # 记忆正文**不进** system prompt（对实参断言）
        assert ch.invocations[0].system_prompt == ""
        assert PROBE not in ch.invocations[0].system_prompt
        # id / tenancy 只进 metadata
        assert tf["metadata"]["memory_ids"] == ["m1"]
        assert tf["metadata"]["memory_tenancy"]["workspace_id"] == WS
        # outcome.memory 回投且**不含正文**
        assert out.memory["mode"] == "brokered"
        assert out.memory["constraint_chars"] == len(memory_line)
        assert out.memory["memory_count"] == 1
        assert out.memory["degraded"] == ""
        assert PROBE not in json.dumps(out.memory, ensure_ascii=False)
        # 显式启用 = 安全姿态变更 ⇒ 必有一次审计（默认档没有）
        assert "subagent.memory.brokered" in audit.actions()
        mapped = _result_from_outcome(out, ctx)
        assert mapped["memory"]["mode"] == "brokered"
        assert PROBE not in json.dumps(mapped["memory"], ensure_ascii=False)


# ======================================================================
#  ③ 只读
# ======================================================================


class TestBrokeredReadOnly:
    def test_brokered_只读_没有任何写调用(self):
        store = SpyStore([FakeEntry("m1", PROBE)])
        bc = assemble_brokered_context(
            "q", scope={"tenant_id": TENANT, "workspace_id": WS},
            recall_fn=store.recall, expected_workspace_id=WS)
        assert bc.ok is True
        assert len(store.calls) == 1
        assert store.writes == 0

        store2 = SpyStore([FakeEntry("m2", "另一条记忆")])
        meta = {"memory_mode": "brokered",
                "memory_scope": {"tenant_id": TENANT, "workspace_id": WS}}
        ex = DelegationExecutor(channel=RecordingChannel(), trusted=True,
                                memory_broker=_broker_from_store(store2))
        ex.execute(_ctx(metadata=meta))
        assert len(store2.calls) == 1
        assert store2.writes == 0, "经执行器也只读"


# ======================================================================
#  ④ 域越界
# ======================================================================


class TestScopeGuard:
    def test_域越界_不注入且如实degraded(self):
        store = SpyStore([FakeEntry("m1", PROBE)])
        bc = assemble_brokered_context(
            "q", scope={"tenant_id": TENANT, "workspace_id": "ws_other"},
            recall_fn=store.recall, expected_workspace_id=WS)
        assert bc.text == ""
        assert "scope_workspace_mismatch" in bc.degraded
        assert store.calls == [], "域越界不得触达取用"

        meta = {"memory_mode": "brokered",
                "memory_scope": {"tenant_id": TENANT, "workspace_id": "ws_other"},
                "workspace_id": WS}
        ch = RecordingChannel()
        ex = DelegationExecutor(channel=ch, trusted=True,
                                memory_broker=_broker_from_store(store))
        out = ex.execute(_ctx(metadata=meta))
        assert out.ok is True
        tf = _read_task_file(ch.invocations[0])
        assert tf["constraints"] == ["只读"], "域越界 ⇒ ctx 原样不动"
        assert "scope_workspace_mismatch" in out.memory["degraded"]
        assert out.memory["constraint_chars"] == 0
        assert store.calls == []


# ======================================================================
#  ⑤ fail-soft
# ======================================================================


class TestFailSoft:
    def test_recall抛异常_委派照常完成且degraded非空(self):
        store = SpyStore(exc=RuntimeError("recall boom"))
        meta = {"memory_mode": "brokered",
                "memory_scope": {"tenant_id": TENANT, "workspace_id": WS}}
        ch = RecordingChannel()
        ex = DelegationExecutor(channel=ch, trusted=True,
                                memory_broker=_broker_from_store(store))
        out = ex.execute(_ctx(metadata=meta))
        assert out.ok is True, "记忆取不到绝不阻断委派"
        assert out.memory["degraded"]
        assert "recall_failed" in out.memory["degraded"]
        assert _read_task_file(ch.invocations[0])["constraints"] == ["只读"]
        mapped = _result_from_outcome(out, _ctx(metadata=meta))
        assert mapped["memory"]["degraded"]

    def test_broker本身抛异常_委派照常完成(self):
        def _boom(*a, **k):
            raise RuntimeError("broker down")

        meta = {"memory_mode": "brokered",
                "memory_scope": {"tenant_id": TENANT, "workspace_id": WS}}
        out = DelegationExecutor(channel=RecordingChannel(), trusted=True,
                                 memory_broker=_boom).execute(_ctx(metadata=meta))
        assert out.ok is True
        assert "broker_failed" in out.memory["degraded"]


# ======================================================================
#  ⑥ 不新增工具
# ======================================================================


class TestNoNewTools:
    def test_brokered不新增任何工具_与none逐字相同(self):
        tools = ["read_file", "search_files"]
        base = SubAgentToolset.build(tools, tools, actor="sub_agent:fixed",
                                     tenant_id=TENANT).as_manifest()
        store = SpyStore([FakeEntry("m1", PROBE)])
        meta = {"memory_mode": "brokered",
                "memory_scope": {"tenant_id": TENANT, "workspace_id": WS}}
        out_none = DelegationExecutor(channel=RecordingChannel(), trusted=True,
                                      memory_broker=_broker_from_store(store)).execute(
            _ctx(), tools=tools, authorized_capabilities=tools)
        out_brokered = DelegationExecutor(channel=RecordingChannel(), trusted=True,
                                          memory_broker=_broker_from_store(store)).execute(
            _ctx(metadata=meta), tools=tools, authorized_capabilities=tools)
        assert out_brokered.toolset == out_none.toolset
        assert out_brokered.toolset == base
        assert all("memory" not in str(t).lower() for t in out_brokered.toolset["tools"])


# ======================================================================
#  ⑦ 档位词表 / scope 键单一口径
# ======================================================================


class TestVocabulary:
    def test_scope键两处同口径(self):
        assert MEMORY_SCOPE_KEYS == SCOPE_KEYS

    def test_scoped与本批未实现(self):
        plan = resolve_memory_config("scoped", {"tenant_id": TENANT})
        assert not plan.ok and not plan.implemented
        assert "scoped" in plan.error

    def test_none配非空域即报错(self):
        plan = resolve_memory_config("none", {"tenant_id": TENANT})
        assert not plan.ok

    def test_render_未取到返回None(self):
        assert render_constraint(None) is None


# ======================================================================
#  ⑧ 路由：非法档位 400 E_MEMORY_CONFIG
# ======================================================================


class _FakeOutcome:
    ok = True
    delegation_id = "dlg-x"
    tier = "jsonl"
    duration_ms = 1.0
    trace_id = "t"
    output_text = "ok"
    error_code = ""
    error = ""
    sub_reason = ""
    artifacts = ()
    role_audit: Dict[str, Any] = {}
    memory: Dict[str, Any] = {}


class _RecordingManager:
    def __init__(self, container):
        self.container = container
        self.delegate_calls: list = []

    def get(self, name):
        return self.container

    def list(self):
        return []

    def delegate(self, config, ctx, **kw):
        self.delegate_calls.append({"config": config, "ctx": ctx, **kw})
        return _FakeOutcome()


class _FakeContainer:
    def __init__(self, config):
        self.config = config

    def get_status(self):
        return {"name": self.config.name, "memory": memory_view(self.config)}

    def run_delegation(self, ctx, **kw):  # pragma: no cover - 不应被调用
        raise AssertionError("非法记忆配置不得带病委派")


class _FakeYunshu:
    def __init__(self, container=None, llm: Any = "fake-llm"):
        self._subagent_mgr = _RecordingManager(container)
        self._llm = llm
        self.created: list = []
        self.reloaded: dict = {}

    def list_subagents(self):
        return []

    def create_subagent(self, config):
        self.created.append(config)
        cfg = SubagentConfig(**config) if isinstance(config, dict) else config
        return _FakeContainer(cfg)

    def get_subagent(self, name):
        return {"name": name, "model_id": "", "memory_provider": "holographic",
                "tool_sources": [], "permissions": ["read"], "context_window": 4096,
                "tags": [], "ttl_seconds": 0, "llm_temperature": None}

    def hot_reload_subagent(self, name, new_config):
        self.reloaded = {"name": name, "config": new_config}


@pytest.fixture
def make_client():
    def _make(container=None, llm: Any = "fake-llm"):
        yunshu = _FakeYunshu(container, llm)
        state = type("S", (), {"Yunshu": yunshu})()
        app = Flask(__name__)
        app.config.update(TESTING=True)
        register_routes(app, state)
        return app.test_client(), yunshu
    return _make


class TestRouteMemoryValidation:
    def test_create_未知档位_400(self, make_client):
        client, yunshu = make_client()
        r = client.post("/api/subagent/create", json={
            "name": "sa-x", "memory_provider": "holographic", "memory_mode": "bogus"})
        assert r.status_code == 400, r.get_data(as_text=True)
        assert r.get_json()["error_code"] == "E_MEMORY_CONFIG"
        assert yunshu.created == [], "非法档位不得建容器"

    def test_create_默认档配非空域_400(self, make_client):
        client, yunshu = make_client()
        r = client.post("/api/subagent/create", json={
            "name": "sa-x", "memory_provider": "holographic",
            "memory_scope": {"tenant_id": TENANT}})
        assert r.status_code == 400, r.get_data(as_text=True)
        assert r.get_json()["error_code"] == "E_MEMORY_CONFIG"
        assert yunshu.created == []

    def test_create_scoped未实现_400(self, make_client):
        client, yunshu = make_client()
        r = client.post("/api/subagent/create", json={
            "name": "sa-x", "memory_provider": "holographic",
            "memory_mode": "scoped", "memory_scope": {"tenant_id": TENANT}})
        assert r.status_code == 400, r.get_data(as_text=True)
        assert r.get_json()["error_code"] == "E_MEMORY_CONFIG"
        assert yunshu.created == []

    def test_create_brokered_回显memory段(self, make_client):
        client, yunshu = make_client()
        r = client.post("/api/subagent/create", json={
            "name": "sa-x", "memory_provider": "holographic", "memory_mode": "brokered",
            "memory_scope": {"tenant_id": TENANT, "workspace_id": WS}})
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        assert body["subagent"]["memory"]["mode"] == "brokered"
        assert body["subagent"]["memory"]["scope"]["workspace_id"] == WS
        assert yunshu.created[0]["memory_mode"] == "brokered"

    def test_named_delegate_带病配置_400(self, make_client):
        container = _FakeContainer(SubagentConfig(name="sa-1", model_id="",
                                                  memory_mode="bogus"))
        client, _ = make_client(container)
        r = client.post("/api/subagent/sa-1/delegate",
                        json={"task": "一个足够长的目标任务"})
        assert r.status_code == 400, r.get_data(as_text=True)
        assert r.get_json()["error_code"] == "E_MEMORY_CONFIG"

    def test_ephemeral_delegate_未知档位_400(self, make_client):
        client, yunshu = make_client()
        r = client.post("/api/subagent/delegate",
                        json={"task": "一个足够长的目标任务", "memory_mode": "bogus"})
        assert r.status_code == 400, r.get_data(as_text=True)
        assert r.get_json()["error_code"] == "E_MEMORY_CONFIG"
        assert yunshu._subagent_mgr.delegate_calls == []

    def test_reload_未知档位_400(self, make_client):
        client, yunshu = make_client()
        r = client.post("/api/subagent/sa-1/reload", json={"memory_mode": "bogus"})
        assert r.status_code == 400, r.get_data(as_text=True)
        assert r.get_json()["error_code"] == "E_MEMORY_CONFIG"
        assert yunshu.reloaded == {}

    def test_reload_brokered_接字段(self, make_client):
        client, yunshu = make_client()
        r = client.post("/api/subagent/sa-1/reload", json={
            "memory_mode": "brokered", "memory_scope": {"tenant_id": TENANT}})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert yunshu.reloaded["config"]["memory_mode"] == "brokered"
        assert yunshu.reloaded["config"]["memory_scope"] == {"tenant_id": TENANT}


# ======================================================================
#  ⑨ config→ctx 桥
# ======================================================================


class TestAttachBridge:
    def test_none档不碰ctx(self):
        ctx = _ctx(metadata={"keep": 1})
        cfg = SubagentConfig(name="sa", model_id="", memory_mode="none")
        assert attach_memory_metadata(ctx, cfg) is ctx
        assert ctx.metadata == {"keep": 1}

    def test_brokered写入标识不写正文(self):
        ctx = _ctx(metadata={"keep": 1})
        cfg = SubagentConfig(name="sa", model_id="", memory_mode="brokered",
                             memory_scope={"tenant_id": TENANT, "workspace_id": WS})
        attach_memory_metadata(ctx, cfg)
        assert ctx.metadata["memory_mode"] == "brokered"
        assert ctx.metadata["memory_scope"]["workspace_id"] == WS
        assert PROBE not in json.dumps(ctx.metadata, ensure_ascii=False)
