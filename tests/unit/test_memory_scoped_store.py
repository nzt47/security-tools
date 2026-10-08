"""S3 scoped 真实记忆域守卫 —— provider 选择、真实写回、配额/域/审计真实强制

本文件把 scoped 档从"能力面 + 组件自证"推进到"真实调用点"的可证伪守卫：

  1. 默认（none/brokered）逐字不变：无写回、无底层 store 调用、工具指纹与改动前一致；
  2. scoped 成功写回：spy 记录调用顺序 admit -> store.write，且域标识与 scope 一致；
  3. 超配额：admit 拒绝 => 底层 write 调用次数为 0，错误码 E_MEMORY_QUOTA_EXCEEDED，
     record_reject 被调；连续拒绝达阈值 => E_MEMORY_BREAKER_OPEN；
  4. 域越界：scope.workspace 与 entry 域不一致 => 被 tenancy 拒（scope_workspace_mismatch），
     且底层未被放行；
  5. 未知 provider => resolve 明确 error（路由 400），domain 显式异常，不静默回退；
  6. 异步不可用 / store 抛错 => 明确 degraded、委派仍 ok、outcome.memory.write.ok=False；
  7. 写回内容不含密钥形态（复用 credentials 的密钥闸门）；
  8. 审计：写回/拒绝/降级各产生审计事件（假 sink）。

★ 可证伪探针 2 条（末尾）：把 admit 挪到 store.write 之后 => 顺序守卫红；
  去掉域校验 => 越域守卫红。探针证明"守卫本身是承重的"，不是同义反复。

【不易】不用真模型 / 不碰真实记忆库：假通道捕获真入参，注入后端 spy 记录调用顺序。
"""

from __future__ import annotations

import hashlib
import json

import pytest
from flask import Flask

from agent.memory.scoped_store import (
    E_MEMORY_DEGRADED,
    E_MEMORY_SCOPE_MISMATCH,
    E_MEMORY_SECRET_BLOCKED,
    SUPPORTED_MEMORY_PROVIDERS,
    ScopedMemoryDomain,
    ScopedMemoryEntry,
    ScopedMemoryScope,
    UnknownMemoryProviderError,
    normalize_provider,
    provider_runtime_view,
)
from agent.memory.taxonomy import project_scope
from agent.server_routes.routes_subagent import register_routes
from agent.subagent.channel import RawOutput
from agent.subagent.container import SubagentConfig
from agent.subagent.delegation import DelegationContext
from agent.subagent.executor import DelegationExecutor
from agent.subagent.memory_broker import (
    memory_view,
    resolve_memory_config,
    scoped_memory_domain,
)
from agent.subagent.memory_quota import (
    AUDIT_SCOPED_DEGRADED,
    AUDIT_SCOPED_PERSIST,
    AUDIT_SCOPED_REJECT,
    AUDIT_SCOPED_WRITE,
    E_MEMORY_BREAKER_OPEN,
    E_MEMORY_QUOTA_EXCEEDED,
    MemoryQuotaGuard,
)

TENANT = "tenant_alpha"
WS = "ws_aaaa1111bbbb2222"
OTHER_WS = "ws_cccc3333dddd4444"
SUBJECT = "user-1"
SCOPE = {"tenant_id": TENANT, "workspace_id": WS, "subject_id": SUBJECT}
DELEGATION_ID = "dlg-scoped-0001"

#: 改动前基线（master 上实算）：none/brokered 工具集清单指纹
TOOLSET_BASELINE_FP = "d7345c124b6756423d968bcac26dc60eb74cff6f4e8b0bd78c9d8d78552f693d"
TOOLSET_PROBE = [
    "read_file", "search_memory", "remember", "memory.write", "memory.read",
    "approval.approve", "write_file", "kb_search", "search_lifetrace", "grep",
]

#: 形态上确定会被 credentials 闸门命中的密钥样例（sk- + 20 位）
SECRET = "sk-" + "A1b2C3d4E5f6G7h8I9j0"


# ======================================================================
#  替身
# ======================================================================


class RecordingAudit:
    def __init__(self):
        self.events = []

    def record(self, action, actor=None, subject="", payload=None, status=""):
        self.events.append({"action": action, "payload": dict(payload or {}),
                            "status": status, "subject": subject})

    def actions(self):
        return [e["action"] for e in self.events]


class RecordingStore:
    """假的 scoped 后端：async write/read + 调用计数 + 共享顺序表"""

    def __init__(self, order=None, exc=None):
        self.order = order
        self.exc = exc
        self.writes = 0
        self.reads = 0
        self.last_entry = None
        self.last_scope = None

    async def write(self, entry, scope):
        self.writes += 1
        self.last_entry = entry
        self.last_scope = scope
        if self.order is not None:
            self.order.append("store.write")
        if self.exc is not None:
            raise self.exc
        return {"shard_path": "/fake/shard.db", "persisted": True}

    async def read(self, query, scope, *, limit, memory_types):
        self.reads += 1
        if self.exc is not None:
            raise self.exc
        return []


class OrderGuard(MemoryQuotaGuard):
    """记录 admit 调用时刻的守卫（与 store.write 共用一张顺序表）"""

    def __init__(self, order, **kwargs):
        super().__init__(**kwargs)
        self.order = order

    def admit(self, **kwargs):
        self.order.append("admit")
        return super().admit(**kwargs)


class RejectSpyGuard(MemoryQuotaGuard):
    """记录 record_reject 调用（配额拒绝也应经此收口）"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rejects = []

    def record_reject(self, code, reason, **kwargs):
        self.rejects.append((str(code), str(reason)))
        return super().record_reject(code, reason, **kwargs)


class RecordingChannel:
    def __init__(self, payload=None, returncode=0):
        self.invocations = []
        self.returncode = returncode
        self.payload = payload or {"status": "done", "summary": "ok", "artifacts": []}

    def __call__(self, invocation):
        self.invocations.append(invocation)
        if self.returncode != 0:
            return RawOutput(stdout="", returncode=self.returncode, error="boom",
                             duration_ms=1.0)
        return RawOutput(stdout=json.dumps(self.payload), returncode=0, duration_ms=1.0)


def _ctx(metadata=None, tenant_id=TENANT, subject_id=SUBJECT):
    return DelegationContext(
        goal="一个足够长的目标任务", constraints=["只读"],
        prior_artifacts=[], prohibitions=[], artifact_format="json",
        budget_tokens=100, timeout_seconds=10, callback_url="ui://sync",
        tenant_id=tenant_id, subject_id=subject_id,
        delegation_id=DELEGATION_ID, delegate_actor="sub_agent:fixed",
        metadata=dict(metadata or {}))


def _scoped_meta(**overrides):
    meta = {"memory_mode": "scoped", "memory_provider": "holographic",
            "memory_scope": dict(SCOPE)}
    meta.update(overrides)
    return meta


def _domain(store, guard=None, audit=None, quota=None, scope=None):
    return ScopedMemoryDomain(
        "holographic", scope=dict(scope or SCOPE), store=store, guard=guard,
        audit=audit, quota=quota)


def _entry(content="项目使用 tabs 缩进", **kwargs):
    return ScopedMemoryEntry(content=content, **kwargs)


def _assert_admit_before_store(order):
    assert order[:2] == ["admit", "store.write"], (
        "写回必须 admit 先于底层 store.write，实际顺序: %r" % (order,))


# ======================================================================
#  ① 默认档逐字不变
# ======================================================================


class TestDefaultUnchanged:
    def test_none档_无写回无store调用(self):
        store = RecordingStore()
        audit = RecordingAudit()
        ch = RecordingChannel()
        ex = DelegationExecutor(channel=ch, audit=audit, trusted=True,
                                scoped_memory_domain=_domain(store))
        meta = {"memory_mode": "none", "memory_provider": "provider-x",
                "memory_scope": dict(SCOPE)}
        out = ex.execute(_ctx(metadata=meta), tools=["read_file"],
                         authorized_capabilities=["read_file"])
        assert out.ok is True
        assert out.memory == {}, "none 档 outcome.memory 必须保持空 dict"
        assert store.writes == 0, "none 档绝不触达 scoped 后端"
        assert not [a for a in audit.actions() if "scoped" in a]

    def test_brokered档_无写回(self):
        from agent.memory.broker import BrokeredContext

        store = RecordingStore()
        ch = RecordingChannel()
        ex = DelegationExecutor(
            channel=ch, trusted=True, scoped_memory_domain=_domain(store),
            memory_broker=lambda *a, **k: BrokeredContext(degraded="empty_recall"))
        out = ex.execute(_ctx(metadata={"memory_mode": "brokered",
                                        "memory_scope": dict(SCOPE)}),
                         tools=["read_file"], authorized_capabilities=["read_file"])
        assert out.ok is True
        assert out.memory.get("mode") == "brokered"
        assert "write" not in out.memory, "brokered 绝不写回"
        assert store.writes == 0

    def test_工具指纹与改动前一致(self):
        manifest = SubAgentToolsetBuildProbe()
        blob = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == TOOLSET_BASELINE_FP


def SubAgentToolsetBuildProbe():
    from agent.subagent.toolset import SubAgentToolset

    return SubAgentToolset.build(TOOLSET_PROBE, TOOLSET_PROBE,
                                 actor="sub_agent:probe", tenant_id="t1").as_manifest()


# ======================================================================
#  ② scoped 成功写回：顺序 + 域一致
# ======================================================================


class TestScopedWriteBack:
    def test_成功写回_顺序admit先于storewrite(self):
        order = []
        store = RecordingStore(order=order)
        guard = OrderGuard(order, max_entries=10, max_bytes=100000,
                           consecutive_reject_limit=5)
        audit = RecordingAudit()
        domain = _domain(store, guard=guard, audit=audit)
        payload = {"status": "done",
                   "summary": "项目使用 tabs 缩进",
                   "facts": ["仓库只读", "运行 pytest"]}
        ch = RecordingChannel(payload)
        ex = DelegationExecutor(channel=ch, audit=audit, trusted=True,
                                scoped_memory_domain=domain)
        out = ex.execute(_ctx(metadata=_scoped_meta()), tools=["read_file"],
                         authorized_capabilities=["read_file"])

        assert out.ok is True
        assert store.writes == 1, "scoped 成功委派应恰写回一次"
        _assert_admit_before_store(order)
        assert store.last_scope.as_dict() == {
            "tenant_id": TENANT, "workspace_id": WS, "subject_id": SUBJECT}
        assert store.last_entry.memory_type == "fact"
        assert "tabs" in store.last_entry.content
        assert out.memory["write"]["ok"] is True
        # outcome.memory 不含正文
        assert "tabs" not in json.dumps(out.memory, ensure_ascii=False)

    def test_委派失败不写回(self):
        store = RecordingStore()
        ch = RecordingChannel(returncode=1)
        ex = DelegationExecutor(channel=ch, trusted=True,
                                scoped_memory_domain=_domain(store))
        out = ex.execute(_ctx(metadata=_scoped_meta()), tools=["read_file"],
                         authorized_capabilities=["read_file"])
        assert out.ok is False
        assert store.writes == 0
        assert out.memory["write"]["ok"] is False
        assert out.memory["write"]["degraded"] == "delegation_not_ok"


# ======================================================================
#  ③ 配额与熔断
# ======================================================================


class TestQuota:
    def test_超配额_不触达底层且record_reject被调(self):
        store = RecordingStore()
        guard = RejectSpyGuard(max_entries=10, max_bytes=4,
                               consecutive_reject_limit=99)
        domain = _domain(store, guard=guard)
        out = domain.write(_entry("超过四个字节的内容"))

        assert out.ok is False
        assert out.error_code == E_MEMORY_QUOTA_EXCEEDED
        assert store.writes == 0, "admit 拒绝时 domain.write 调用次数必须为 0"
        assert guard.rejects, "配额拒绝必须经 record_reject 收口"
        assert guard.rejects[0][0] == E_MEMORY_QUOTA_EXCEEDED

    def test_连续拒绝达阈值熔断(self):
        store = RecordingStore()
        guard = MemoryQuotaGuard(max_entries=1, max_bytes=100000,
                                 consecutive_reject_limit=2)
        domain = _domain(store, guard=guard)
        assert domain.write(_entry("第一条")).ok is True
        first = domain.write(_entry("第二条"))
        assert first.error_code == E_MEMORY_QUOTA_EXCEEDED
        second = domain.write(_entry("第三条"))
        assert second.error_code == E_MEMORY_BREAKER_OPEN
        third = domain.write(_entry("第四条"))
        assert third.error_code == E_MEMORY_BREAKER_OPEN
        assert store.writes == 1, "熔断后绝不触达底层"


# ======================================================================
#  ④ 域越界
# ======================================================================


class TestDomainGuard:
    def test_entry_workspace越界被拒(self):
        store = RecordingStore()
        domain = _domain(store)
        out = domain.write(_entry(workspace_id=OTHER_WS))
        assert out.ok is False
        assert out.error_code == E_MEMORY_SCOPE_MISMATCH
        assert out.degraded == "scope_workspace_mismatch"
        assert store.writes == 0, "越域不得放行到底层"
        assert domain.guard.status()["entries"] == 0, "被拒写入不得吃掉配额"

    def test_entry_scope越界被tenancy拒(self):
        store = RecordingStore()
        domain = _domain(store)
        out = domain.write(_entry(scope=project_scope(OTHER_WS)))
        assert out.ok is False
        assert out.degraded == "scope_workspace_mismatch"
        assert store.writes == 0

    def test_同域放行(self):
        store = RecordingStore()
        domain = _domain(store)
        out = domain.write(_entry(workspace_id=WS, subject_id=SUBJECT,
                                  tenant_id=TENANT))
        assert out.ok is True
        assert store.writes == 1


# ======================================================================
#  ⑤ 未知 provider
# ======================================================================


class TestProviderSelection:
    def test_未知provider_resolve报错不静默回退(self):
        plan = resolve_memory_config("scoped", dict(SCOPE), "bogus")
        assert plan.ok is False
        assert "未接线" in plan.error
        assert "bogus" in plan.error

    def test_未知provider_domain显式异常(self):
        with pytest.raises(UnknownMemoryProviderError):
            ScopedMemoryDomain("bogus", scope=dict(SCOPE), store=RecordingStore())
        with pytest.raises(UnknownMemoryProviderError):
            normalize_provider("bogus")

    def test_词表内provider_归一化与视图(self):
        assert normalize_provider("holo") == "holographic"
        assert normalize_provider("MEM0") == "mem0"
        assert set(SUPPORTED_MEMORY_PROVIDERS) == {"holographic", "mem0"}
        view = provider_runtime_view("holographic")
        assert view["store"] == "layered_sqlite_fts5"
        assert view["degraded"] == ""

    def test_memory_view_scoped回显store与degraded(self):
        cfg = SubagentConfig(name="sa", model_id="", memory_mode="scoped",
                             memory_provider="holographic",
                             memory_scope=dict(SCOPE))
        view = memory_view(cfg)
        assert view["scoped"] is True
        assert view["provider"] == "holographic"
        assert view["store"] == "layered_sqlite_fts5"
        assert "breaker" in view and view["breaker"]["breaker_open"] is False


class _FakeContainer:
    def __init__(self, config):
        self.config = config

    def get_status(self):
        return {"name": self.config.name, "memory": memory_view(self.config)}


class _FakeYunshu:
    def __init__(self):
        self.created = []
        self._llm = "fake-llm"

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
        pass


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


class TestRouteProviderValidation:
    def test_create_未知provider_400不建容器(self, make_client):
        client, yunshu = make_client()
        r = client.post("/api/subagent/create", json={
            "name": "sa-x", "memory_provider": "bogus", "memory_mode": "scoped",
            "memory_scope": dict(SCOPE)})
        assert r.status_code == 400, r.get_data(as_text=True)
        assert r.get_json()["error_code"] == "E_MEMORY_CONFIG"
        assert yunshu.created == []


# ======================================================================
#  ⑥ 降级（异步不可用 / store 抛错）
# ======================================================================


class TestDegraded:
    def test_store抛错_明确degraded且不伪造成功(self):
        store = RecordingStore(exc=RuntimeError("store down"))
        domain = _domain(store)
        out = domain.write(_entry())
        assert out.ok is False
        assert out.error_code == E_MEMORY_DEGRADED
        assert out.degraded.startswith("write_failed")
        # 降级释放配额占位（不永久吃掉配额）
        assert domain.guard.status()["entries"] == 0

    def test_store抛错_委派仍ok且write_ok为False(self):
        store = RecordingStore(exc=RuntimeError("store down"))
        ch = RecordingChannel({"status": "done", "summary": "事实摘要"})
        ex = DelegationExecutor(channel=ch, trusted=True,
                                scoped_memory_domain=_domain(store))
        out = ex.execute(_ctx(metadata=_scoped_meta()), tools=["read_file"],
                         authorized_capabilities=["read_file"])
        assert out.ok is True, "记忆写回失败绝不阻断委派"
        assert out.memory["write"]["ok"] is False
        assert out.memory["write"]["degraded"].startswith("write_failed")

    def test_异步不可用_明确degraded(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("当前线程已有运行中的事件循环")

        monkeypatch.setattr("agent.memory.scoped_store.run_sync", _boom)
        store = RecordingStore()
        domain = _domain(store)
        out = domain.write(_entry())
        assert out.ok is False
        assert out.error_code == E_MEMORY_DEGRADED
        assert out.degraded.startswith("write_failed")
        assert store.writes == 0


# ======================================================================
#  ⑦ 密钥形态不落库
# ======================================================================


class TestSecretGate:
    def test_密钥形态不写回(self):
        from agent.subagent.credentials import find_manifest_secrets

        store = RecordingStore()
        domain = _domain(store)
        out = domain.write(_entry("部署密钥 " + SECRET))
        assert out.ok is False
        assert out.error_code == E_MEMORY_SECRET_BLOCKED
        assert out.degraded == "secret_detected"
        assert store.writes == 0
        assert find_manifest_secrets({"content": SECRET}), "样例必须确实命中闸门"

    def test_正常内容过闸门(self):
        from agent.subagent.credentials import find_manifest_secrets

        store = RecordingStore()
        domain = _domain(store)
        out = domain.write(_entry("项目使用 tabs 缩进"))
        assert out.ok is True
        assert find_manifest_secrets({"content": store.last_entry.content}) == []

    def test_委派写回不落密钥(self):
        store = RecordingStore()
        ch = RecordingChannel({"status": "done", "summary": "token " + SECRET})
        ex = DelegationExecutor(channel=ch, trusted=True,
                                scoped_memory_domain=_domain(store))
        out = ex.execute(_ctx(metadata=_scoped_meta()), tools=["read_file"],
                         authorized_capabilities=["read_file"])
        assert out.ok is True
        assert store.writes == 0, "含密钥形态的摘要不得落库"
        assert out.memory["write"]["ok"] is False
        assert out.memory["write"]["error_code"] == E_MEMORY_SECRET_BLOCKED


# ======================================================================
#  ⑧ 审计
# ======================================================================


class TestAudit:
    def test_写回_拒绝_降级各留审计(self):
        audit = RecordingAudit()
        # 成功
        domain = _domain(RecordingStore(), audit=audit,
                         quota={"max_entries": 10, "max_bytes": 100000})
        assert domain.write(_entry("成功一条")).ok is True
        # 拒绝（配额）
        quota_domain = _domain(RecordingStore(), audit=audit,
                               quota={"max_entries": 0 + 1, "max_bytes": 4})
        assert quota_domain.write(_entry("超字节")).ok is False
        # 降级（store 抛错）
        bad_domain = _domain(RecordingStore(exc=RuntimeError("boom")), audit=audit)
        assert bad_domain.write(_entry("降级一条")).ok is False

        actions = audit.actions()
        assert AUDIT_SCOPED_WRITE in actions
        assert AUDIT_SCOPED_PERSIST in actions
        assert AUDIT_SCOPED_REJECT in actions
        assert AUDIT_SCOPED_DEGRADED in actions
        # 审计载荷不含正文
        assert "成功一条" not in json.dumps(audit.events, ensure_ascii=False)

    def test_域越界留reject与degraded审计(self):
        audit = RecordingAudit()
        domain = _domain(RecordingStore(), audit=audit)
        assert domain.write(_entry(workspace_id=OTHER_WS)).ok is False
        actions = audit.actions()
        assert AUDIT_SCOPED_REJECT in actions
        assert AUDIT_SCOPED_DEGRADED in actions


# ======================================================================
#  可证伪探针（证明守卫承重）
# ======================================================================


def test_探针_把admit挪到storewrite之后_顺序守卫变红():
    from agent.memory.broker import run_sync

    order = []
    store = RecordingStore(order=order)
    guard = OrderGuard(order, max_entries=10, max_bytes=1000)
    scope = ScopedMemoryScope.from_mapping(SCOPE)
    # 错误顺序（模拟回归）：先落库，再准入
    run_sync(lambda: store.write(ScopedMemoryEntry(content="x"), scope))
    guard.admit(size_bytes=1)
    with pytest.raises(AssertionError):
        _assert_admit_before_store(order)
    # 正确顺序（真实实现）不红
    order.clear()
    good = ScopedMemoryDomain("holographic", scope=dict(SCOPE), store=store,
                              guard=OrderGuard(order, max_entries=10, max_bytes=1000))
    assert good.write(_entry("x")).ok is True
    _assert_admit_before_store(order)


def test_探针_去掉域校验_越域守卫变红():
    class _NoDomainCheck(ScopedMemoryDomain):
        def check_domain(self, entry):
            return ""

    store = RecordingStore()
    real = _domain(store)
    assert real.write(_entry(workspace_id=OTHER_WS)).ok is False
    assert store.writes == 0

    no_check_store = RecordingStore()
    buggy = _NoDomainCheck("holographic", scope=dict(SCOPE), store=no_check_store)
    out = buggy.write(_entry(workspace_id=OTHER_WS))
    assert out.ok is True and no_check_store.writes == 1, (
        "去掉域校验后越域会被放行——真实实现的拒绝是承重的")
