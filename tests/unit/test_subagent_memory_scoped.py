"""P3 scoped 档守卫 —— 显式开启的受控私人记忆域（默认档逐字不变）

本文件锁的是 P3 scoped 档的九条边界（纯逻辑 + 注入点，不跑真模型 / 真记忆库）：

  1. 默认矩阵逐字不变：`scoped_memory_enabled=False` 下 (全部 operation × 全部
     actor) 的 `decide()` 结果与改动前基线**指纹相同**；工具集清单在 none/brokered
     下哈希不变。
  2. scoped 未开启时给 provider/scope 不改变任何行为（工具/装配/约束逐字相等）。
  3. scoped 开启且三要素齐全：view.memory / memory.write 对 sub_agent 变 allowed
     且带 scope 限制；矩阵 / toolset / assembly / capability_exposure 四处结论一致。
  4. scoped 缺 scope 或 provider ⇒ 400，且不建容器。
  5. 配额超限：写入被拒且错误码 E_MEMORY_QUOTA_EXCEEDED；不静默丢。
  6. 熔断：连续拒绝达阈值 ⇒ E_MEMORY_BREAKER_OPEN，且熔断后仍拒绝。
  7. 域越界：跨 workspace 写入被 tenancy 拒（复用 scope_workspace_mismatch）。
  8. 审计：开启/拒绝/写入各产生 subagent.memory.scoped.* 事件（假审计 sink）。
  9. 矩阵文档一致性：MATRIX_DOC / MATRIX_DOC_ROWS 组集一致，scoped 说明另存不污染默认行。

【不易】基线指纹是**改动前 master 上实算**的常量（见提交说明），不是同义反复。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

import pytest
from flask import Flask

from agent.guardrails import capability_exposure as expo
from agent.memory.taxonomy import project_scope
from agent.memory.tenancy import (
    TenancyContext,
    TenancyPolicy,
    WriteDisposition,
)
from agent.security import actor_matrix as M
from agent.server_routes.routes_subagent import register_routes
from agent.subagent import assembly as _assembly
from agent.subagent.container import SubagentConfig
from agent.subagent.delegation import DelegationContext
from agent.subagent.memory_broker import (
    attach_memory_metadata,
    memory_view,
    resolve_memory_config,
    scoped_memory_enabled,
)
from agent.subagent.memory_quota import (
    AUDIT_SCOPED_BREAKER_OPEN,
    AUDIT_SCOPED_ENABLED,
    AUDIT_SCOPED_REJECT,
    AUDIT_SCOPED_WRITE,
    E_MEMORY_BREAKER_OPEN,
    E_MEMORY_QUOTA_EXCEEDED,
    MemoryQuotaGuard,
)
from agent.subagent.toolset import SubAgentToolset

TENANT = "tenant_alpha"
WS = "ws_aaaa1111bbbb2222"
OTHER_WS = "ws_cccc3333dddd4444"
SUBJECT = "user-1"

#: 改动前基线（master 1ace9c69 上实算）：(全部 op × 全部 actor) decide() 指纹
MATRIX_BASELINE_FP = "e8f01be82d895a1a8428d4aa0a9d8c29aab073f2a88700b7773ba0b08dea71e3"

#: 改动前基线：none/brokered 工具集清单指纹（规范工具集，见 TOOLSET_PROBE）
TOOLSET_BASELINE_FP = "d7345c124b6756423d968bcac26dc60eb74cff6f4e8b0bd78c9d8d78552f693d"
TOOLSET_PROBE = [
    "read_file", "search_memory", "remember", "memory.write", "memory.read",
    "approval.approve", "write_file", "kb_search", "search_lifetrace", "grep",
]


# ════════════════════════════════════════════════════════════
#  替身
# ════════════════════════════════════════════════════════════


class RecordingAudit:
    def __init__(self):
        self.events: list = []

    def record(self, action, actor=None, subject="", payload=None, status=""):
        self.events.append({"action": action, "payload": dict(payload or {}),
                            "status": status, "subject": subject})

    def actions(self):
        return [e["action"] for e in self.events]


def scoped_config(**overrides) -> SubagentConfig:
    base = dict(name="sa-scoped", model_id="", memory_mode="scoped",
                memory_provider="holographic",
                memory_scope={"tenant_id": TENANT, "workspace_id": WS,
                              "subject_id": SUBJECT})
    base.update(overrides)
    return SubagentConfig(**base)


def _ctx(metadata=None, tenant_id: str = TENANT, subject_id: str = SUBJECT):
    return DelegationContext(
        goal="一个足够长的目标任务", constraints=["只读"],
        prior_artifacts=[], prohibitions=[], artifact_format="json",
        budget_tokens=100, timeout_seconds=10, callback_url="ui://sync",
        tenant_id=tenant_id, subject_id=subject_id,
        delegation_id="dlg-scoped-0001", delegate_actor="sub_agent:fixed",
        metadata=dict(metadata or {}))


def _matrix_view(flag: bool, scope: str = WS) -> Dict[str, Any]:
    ctx = M.PermissionContext(actor="sub_agent:x", actor_type=M.ACTOR_SUB_AGENT,
                              scope=scope, scoped_memory_enabled=flag)
    out = {}
    for op in (M.OP_VIEW_MEMORY, M.OP_WRITE_MEMORY):
        out[op] = M.decide(op, ctx, target_scope=scope).to_dict()
    return out


# ════════════════════════════════════════════════════════════
#  1. 默认档逐字不变
# ════════════════════════════════════════════════════════════


class TestDefaultUnchanged:
    def test_默认矩阵指纹与改动前一致(self):
        rows = []
        for op in M.OPERATIONS:
            for at in M.ACTOR_TYPES:
                ctx = M.PermissionContext(actor="probe", actor_type=at)
                d = M.decide(op, ctx, object_type="o", object_id="x",
                             target_scope="s", memory_layer="working",
                             reason="r", second_factor_ok=True)
                rows.append([op, at, d.allowed, d.scope, d.requires_second_factor,
                             d.requires_reason, d.alert_on_deny, d.matrix_hit, d.reason])
        blob = json.dumps(rows, ensure_ascii=False, sort_keys=True)
        assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == MATRIX_BASELINE_FP
        assert len(rows) == len(M.OPERATIONS) * len(M.ACTOR_TYPES)

    def test_默认上下文scoped标志为False(self):
        assert M.PermissionContext(actor="x", actor_type=M.ACTOR_HUMAN)             .scoped_memory_enabled is False

    def test_工具集清单指纹不变(self):
        manifest = SubAgentToolset.build(
            TOOLSET_PROBE, TOOLSET_PROBE, actor="sub_agent:probe",
            tenant_id="t1").as_manifest()
        blob = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == TOOLSET_BASELINE_FP

    def test_默认档未开启scoped(self):
        assert scoped_memory_enabled(SubagentConfig(name="a", model_id="")) is False
        assert scoped_memory_enabled(SubagentConfig(
            name="a", model_id="", memory_mode="brokered",
            memory_scope={"tenant_id": TENANT})) is False


# ════════════════════════════════════════════════════════════
#  2. scoped 未开启 ⇒ provider/scope 不改变任何行为
# ════════════════════════════════════════════════════════════


class TestScopedOffUnchanged:
    def test_工具集逐字相等(self):
        base = SubAgentToolset.build(TOOLSET_PROBE, TOOLSET_PROBE,
                                     actor="sub_agent:probe").as_manifest()
        with_scope = SubAgentToolset.build(
            TOOLSET_PROBE, TOOLSET_PROBE, actor="sub_agent:probe",
            scope=WS, tenant_id=TENANT, scoped_memory=False).as_manifest()
        assert base == with_scope

    def test_装配剔除逐字相等(self):
        kept0, dropped0 = _assembly._drop_hard_denied(TOOLSET_PROBE)
        kept1, dropped1 = _assembly._drop_hard_denied(TOOLSET_PROBE,
                                                      scoped_memory=False)
        assert (kept0, dropped0) == (kept1, dropped1)
        assert "memory.write" in dropped0

    def test_能力暴露逐字相等(self):
        base = expo.is_exposed("memory.write", authorized=["memory.write"]).to_dict()
        off = expo.is_exposed("memory.write", authorized=["memory.write"],
                              scoped_memory=False).to_dict()
        assert base == off
        assert base["exposed"] is False

    def test_brokered带provider与scope键_约束逐字不变(self):
        cfg = SubagentConfig(name="a", model_id="", memory_mode="brokered",
                             memory_provider="provider-x",
                             memory_scope={"tenant_id": TENANT})
        ctx1 = _ctx(metadata={})
        attach_memory_metadata(ctx1, cfg)
        ctx2 = _ctx(metadata={})
        attach_memory_metadata(ctx2, SubagentConfig(
            name="a", model_id="", memory_mode="brokered",
            memory_scope={"tenant_id": TENANT}))
        assert ctx1.metadata == ctx2.metadata
        assert "scoped" not in memory_view(cfg)


# ════════════════════════════════════════════════════════════
#  3. scoped 开启：四处判定同源
# ════════════════════════════════════════════════════════════


class TestScopedEnabledFourWay:
    def test_矩阵两格allow_with_scope(self):
        view = _matrix_view(True)
        for op in (M.OP_VIEW_MEMORY, M.OP_WRITE_MEMORY):
            assert view[op]["allowed"] is True, op
            assert view[op]["scope"] == M.SCOPE_SCOPED_MEMORY
        # 越域（target_scope 不等于自身域）⇒ 拒
        ctx = M.PermissionContext(actor="sub_agent:x", actor_type=M.ACTOR_SUB_AGENT,
                                  scope=WS, scoped_memory_enabled=True)
        assert M.decide(M.OP_WRITE_MEMORY, ctx, target_scope=OTHER_WS).allowed is False

    def test_矩阵默认仍拒(self):
        view = _matrix_view(False)
        for op in (M.OP_VIEW_MEMORY, M.OP_WRITE_MEMORY):
            assert view[op]["allowed"] is False

    def test_toolset_scoped放行记忆工具(self):
        ts = SubAgentToolset.build(
            ["memory.write", "search_memory", "approval.approve"],
            ["memory.write", "search_memory", "approval.approve"],
            actor="sub_agent:x", scope=WS, scoped_memory=True)
        assert ts.allows("memory.write") is True
        assert ts.allows("search_memory") is True
        assert ts.allows("approval.approve") is False, "审批权在 scoped 下仍绝对禁止"

    def test_assembly_scoped保留记忆工具(self):
        kept, dropped = _assembly._drop_hard_denied(TOOLSET_PROBE, scoped_memory=True)
        assert "memory.write" in kept and "search_memory" in kept
        assert set(dropped) == {"approval.approve"}
        # 核心/治理仍被剔除
        kept2, dropped2 = _assembly._drop_hard_denied(
            ["core.rewrite", "governance.modify_policy"], scoped_memory=True)
        assert kept2 == [] and len(dropped2) == 2

    def test_capability_exposure_scoped放行记忆类(self):
        assert expo.classify_capability("memory.write", scoped_memory=True) == []
        assert expo.is_exposed("memory.write", authorized=["memory.write"],
                               scoped_memory=True).exposed is True
        # 审批权在 scoped 下仍是绝对禁项
        assert expo.is_exposed("approval.approve", authorized=["approval.approve"],
                               scoped_memory=True).exposed is False

    def test_四处结论一致(self):
        matrix_ok = _matrix_view(True)[M.OP_WRITE_MEMORY]["allowed"]
        tool_ok = SubAgentToolset.build(
            ["memory.write"], ["memory.write"], actor="sub_agent:x",
            scope=WS, scoped_memory=True).allows("memory.write")
        asm_kept, _ = _assembly._drop_hard_denied(["memory.write"],
                                                  scoped_memory=True)
        expo_ok = expo.is_exposed("memory.write", authorized=["memory.write"],
                                  scoped_memory=True).exposed
        assert (matrix_ok, tool_ok, "memory.write" in asm_kept, expo_ok) ==             (True, True, True, True)


# ════════════════════════════════════════════════════════════
#  4. 缺 scope / provider ⇒ 400 且不建容器
# ════════════════════════════════════════════════════════════


class _FakeContainer:
    def __init__(self, config):
        self.config = config

    def get_status(self):
        return {"name": self.config.name, "memory": memory_view(self.config)}


class _RecordingManager:
    def __init__(self):
        self.delegate_calls: list = []

    def get(self, name):
        return None

    def list(self):
        return []

    def delegate(self, config, ctx, **kw):  # pragma: no cover
        self.delegate_calls.append((config, ctx, kw))
        raise AssertionError("非法 scoped 配置不得带病委派")


class _FakeYunshu:
    def __init__(self):
        self._subagent_mgr = _RecordingManager()
        self._llm = "fake-llm"
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
    def _make():
        yunshu = _FakeYunshu()
        state = type("S", (), {"Yunshu": yunshu})()
        app = Flask(__name__)
        app.config.update(TESTING=True)
        register_routes(app, state)
        return app.test_client(), yunshu
    return _make


class TestScopedRouteValidation:
    def test_缺workspace与subject_400不建容器(self, make_client):
        client, yunshu = make_client()
        r = client.post("/api/subagent/create", json={
            "name": "sa-x", "memory_provider": "holographic",
            "memory_mode": "scoped", "memory_scope": {"tenant_id": TENANT}})
        assert r.status_code == 400, r.get_data(as_text=True)
        assert r.get_json()["error_code"] == "E_MEMORY_CONFIG"
        assert yunshu.created == []

    def test_空provider_400不建容器(self, make_client):
        client, yunshu = make_client()
        r = client.post("/api/subagent/create", json={
            "name": "sa-x", "memory_provider": "",
            "memory_mode": "scoped",
            "memory_scope": {"tenant_id": TENANT, "workspace_id": WS,
                             "subject_id": SUBJECT}})
        assert r.status_code == 400
        assert r.get_json()["error_code"] == "E_MEMORY_CONFIG"
        assert yunshu.created == []

    def test_非scoped给配额_400(self, make_client):
        client, yunshu = make_client()
        r = client.post("/api/subagent/create", json={
            "name": "sa-x", "memory_provider": "holographic",
            "memory_mode": "brokered", "memory_scope": {"tenant_id": TENANT},
            "memory_quota": {"max_entries": 1}})
        assert r.status_code == 400
        assert r.get_json()["error_code"] == "E_MEMORY_CONFIG"
        assert yunshu.created == []

    def test_齐全_回显quota与breaker(self, make_client):
        client, yunshu = make_client()
        r = client.post("/api/subagent/create", json={
            "name": "sa-x", "memory_provider": "holographic",
            "memory_mode": "scoped",
            "memory_scope": {"tenant_id": TENANT, "workspace_id": WS,
                             "subject_id": SUBJECT},
            "memory_quota": {"max_entries": 7}})
        assert r.status_code == 200, r.get_data(as_text=True)
        mem = r.get_json()["subagent"]["memory"]
        assert mem["mode"] == "scoped" and mem["scoped"] is True
        assert mem["provider"] == "holographic"
        assert mem["quota"]["max_entries"] == 7
        assert mem["breaker"]["breaker_open"] is False


# ════════════════════════════════════════════════════════════
#  5/6. 配额与熔断
# ════════════════════════════════════════════════════════════


class TestQuotaAndBreaker:
    def test_条数超限明确错误码_不静默丢(self):
        audit = RecordingAudit()
        g = MemoryQuotaGuard(max_entries=1, max_bytes=1000,
                             consecutive_reject_limit=99, audit=audit)
        assert g.admit(size_bytes=5).allowed is True
        dec = g.admit(size_bytes=5)
        assert dec.allowed is False
        assert dec.code == E_MEMORY_QUOTA_EXCEEDED
        assert dec.entries == 1, "被拒写入不得改变计数（不静默丢）"
        assert "subagent.memory.scoped.reject" in audit.actions()

    def test_字节超限明确错误码(self):
        g = MemoryQuotaGuard(max_entries=10, max_bytes=4,
                             consecutive_reject_limit=99)
        assert g.admit(size_bytes=4).allowed is True
        dec = g.admit(size_bytes=1)
        assert dec.code == E_MEMORY_QUOTA_EXCEEDED

    def test_连续拒绝达阈值熔断_且之后仍拒(self):
        audit = RecordingAudit()
        g = MemoryQuotaGuard(max_entries=1, max_bytes=1000,
                             consecutive_reject_limit=2, audit=audit)
        g.admit(size_bytes=1)          # ok
        first = g.admit(size_bytes=1)  # 拒绝 1
        assert first.code == E_MEMORY_QUOTA_EXCEEDED
        trip = g.admit(size_bytes=1)   # 拒绝 2 ⇒ 熔断
        assert trip.code == E_MEMORY_BREAKER_OPEN
        assert g.breaker_open is True
        after = g.admit(size_bytes=1)  # 熔断后仍拒
        assert after.allowed is False and after.code == E_MEMORY_BREAKER_OPEN
        assert AUDIT_SCOPED_BREAKER_OPEN in audit.actions()

    def test_成功写入清零连续拒绝(self):
        g = MemoryQuotaGuard(max_entries=10, max_bytes=1000,
                             consecutive_reject_limit=3)
        g.admit(size_bytes=1)
        g.admit(size_bytes=10_000)  # 拒绝 1
        assert g.consecutive_rejects == 1
        g.admit(size_bytes=1)       # 成功 ⇒ 清零
        assert g.consecutive_rejects == 0


# ════════════════════════════════════════════════════════════
#  7. 域越界（复用 tenancy scope_workspace_mismatch）
# ════════════════════════════════════════════════════════════


class TestDomainTenancy:
    def test_跨workspace写入被tenancy拒(self):
        policy = TenancyPolicy()
        ctx = TenancyContext(tenant_id=TENANT, workspace_id=WS, subject_id=SUBJECT)
        decision = policy.decide_write("fact", ctx, scope=project_scope(OTHER_WS))
        assert decision.disposition is WriteDisposition.REJECT
        assert decision.reason == "scope_workspace_mismatch"

    def test_同workspace放行(self):
        policy = TenancyPolicy()
        ctx = TenancyContext(tenant_id=TENANT, workspace_id=WS, subject_id=SUBJECT)
        decision = policy.decide_write("fact", ctx, scope=project_scope(WS))
        assert decision.disposition is WriteDisposition.ACCEPT


# ════════════════════════════════════════════════════════════
#  8. 审计
# ════════════════════════════════════════════════════════════


class TestScopedAudit:
    def test_开启写enabled审计(self):
        audit = RecordingAudit()
        ctx = _ctx(metadata={})
        attach_memory_metadata(ctx, scoped_config(), audit=audit)
        assert audit.actions() == [AUDIT_SCOPED_ENABLED]
        assert ctx.metadata["memory_mode"] == "scoped"
        assert ctx.metadata["memory_scope"]["workspace_id"] == WS

    def test_默认档不写审计(self):
        audit = RecordingAudit()
        ctx = _ctx(metadata={})
        attach_memory_metadata(ctx, SubagentConfig(name="a", model_id=""),
                               audit=audit)
        assert audit.events == []

    def test_写入与拒绝各留审计(self):
        audit = RecordingAudit()
        g = MemoryQuotaGuard(max_entries=1, max_bytes=1000,
                             consecutive_reject_limit=99, audit=audit)
        g.admit(size_bytes=1)
        g.admit(size_bytes=1)
        assert AUDIT_SCOPED_WRITE in audit.actions()
        assert AUDIT_SCOPED_REJECT in audit.actions()
        # 审计失败不得阻断判定（fail-soft）
        class Boom:
            def record(self, *a, **k):
                raise RuntimeError("audit down")
        g2 = MemoryQuotaGuard(max_entries=1, consecutive_reject_limit=99,
                              audit=Boom())
        assert g2.admit(size_bytes=1).allowed is True


# ════════════════════════════════════════════════════════════
#  9. 矩阵文档一致性
# ════════════════════════════════════════════════════════════


class TestMatrixDocConsistency:
    def test_文档矩阵与行列组集一致(self):
        assert set(M.MATRIX_DOC) == set(M.MATRIX_DOC_ROWS)

    def test_默认文档行未被scoped污染(self):
        for group, cells in M.MATRIX_DOC.items():
            assert cells[M.ACTOR_SUB_AGENT] in ("deny", "authorized_subset"), group

    def test_scoped说明另存且与实现同值(self):
        assert set(M.MATRIX_DOC_SCOPED) == {M.OP_VIEW_MEMORY, M.OP_WRITE_MEMORY}
        for op in (M.OP_VIEW_MEMORY, M.OP_WRITE_MEMORY):
            assert M.MATRIX_DOC_SCOPED[op][M.ACTOR_SUB_AGENT] == M.SCOPE_SCOPED_MEMORY

    def test_每格可查(self):
        for group, cells in M.MATRIX_DOC.items():
            for operation in M.MATRIX_DOC_ROWS[group]:
                for actor_type in M.ACTOR_TYPES:
                    assert M.rule_for(operation, actor_type) is not None
                    assert actor_type in cells
