"""PR-F 守卫 —— scoped 分身侧主动 read/write（协议内声明式 + 母体守门器）

本文件把「子代理只能声明、母体执行并守域」这条边界钉死为可证伪守卫：

  1. 默认档 none / brokered 绝不触达：注入「一触即爆」的域（read/write 调用即抛），
     执行器走完整委派后调用计数恒为 0，且 outcome.memory 无 ops 键；
  2. scoped 未开启（toolset.scoped_memory=False）/ 空域 / domain=None ⇒ 守门器不启用；
  3. 越权声明（toolset 不放行记忆类工具）⇒ E_TOOL_NOT_AUTHORIZED 且**不触达底层**；
  4. 域越界写被 domain.check_domain 拒（scope_workspace_mismatch），不落库；
     声明里的 tenant/workspace/subject 被母体 scope **强制覆盖**；
  5. 读取侧后置域过滤：mem0 缺 metadata / 域不符剔除；holographic 无 metadata 行不误伤；
     已过滤行再滤 => 结果与计数不变（幂等，供 #1063 合入后的防御冗余）；
  6. fail-soft：域 read/write 抛错、审计 sink 抛错都不抛、不阻断委派；
  7. 审计承重：每次 read/write/拒绝都写 subagent.memory.scoped.gate（载荷不含正文）；
  8. 通道声明收口：非法声明丢弃并记 degraded，但委派**不失败**。

★ 可证伪探针（末尾 + 一次实跑回退）：去掉 emit / 去掉权限闸门 / 去掉域覆盖 =>
  对应守卫变红；真实实现（恢复后）绿。

【不易】不跑真模型 / 不碰真实记忆库：假通道回放 JSONL 载荷，注入后端 spy 计数。
"""

from __future__ import annotations

import json

from agent.memory.scoped_store import (
    E_MEMORY_DEGRADED,
    E_MEMORY_SCOPE_MISMATCH,
    ScopedMemoryDomain,
    ScopedMemoryScope,
    ScopedReadOutcome,
    ScopedWriteOutcome,
)
from agent.memory.taxonomy import project_scope
from agent.subagent.channel import (
    collect_memory_declarations,
    resolve_channel_output,
    RawOutput,
)
from agent.subagent.delegation import DelegationContext
from agent.subagent.executor import DelegationExecutor
from agent.subagent.scoped_memory_gate import (
    AUDIT_SCOPED_GATE,
    READ_TOOL_NAMES,
    WRITE_TOOL_NAMES,
    ScopedMemoryGate,
)
from agent.subagent.toolset import E_TOOL_NOT_AUTHORIZED, SubAgentToolset

TENANT = "tenant_alpha"
WS = "ws_aaaa1111bbbb2222"
OTHER_WS = "ws_cccc3333dddd4444"
SUBJECT = "user-1"
OTHER_SUBJECT = "user-2"
SCOPE = {"tenant_id": TENANT, "workspace_id": WS, "subject_id": SUBJECT}
DELEGATION_ID = "dlg-scoped-gate-0001"


# ════════════════════════════════════════════════════════════
#  替身
# ════════════════════════════════════════════════════════════


class RecordingAudit:
    def __init__(self):
        self.events = []

    def record(self, action, actor=None, subject="", payload=None, status=""):
        self.events.append({"action": action, "payload": dict(payload or {}),
                            "status": status, "subject": subject})

    def actions(self):
        return [e["action"] for e in self.events]


class BoomAudit:
    def record(self, *a, **k):
        raise RuntimeError("audit down")


class BombDomain:
    """一触即爆的域：read/write 一旦被调用立即抛（默认档绝不应触达）"""

    def __init__(self):
        self.reads = 0
        self.writes = 0
        self.scope = ScopedMemoryScope.from_mapping(SCOPE)
        self.provider = "holographic"

    def read(self, *a, **k):
        self.reads += 1
        raise AssertionError("默认档 / 未开启 scoped 不得读 scoped 域")

    def write(self, *a, **k):
        self.writes += 1
        raise AssertionError("默认档 / 未开启 scoped 不得写 scoped 域")


class RecordingStore:
    """假的 scoped 后端：async write/read + 调用计数 + 可注入异常"""

    def __init__(self, rows=(), read_exc=None, write_exc=None):
        self.rows = list(rows)
        self.read_exc = read_exc
        self.write_exc = write_exc
        self.writes = 0
        self.reads = 0
        self.last_entry = None
        self.last_scope = None

    async def write(self, entry, scope):
        self.writes += 1
        self.last_entry = entry
        self.last_scope = scope
        if self.write_exc is not None:
            raise self.write_exc
        return {"shard_path": "/fake/shard.db", "persisted": True}

    async def read(self, query, scope, *, limit, memory_types):
        self.reads += 1
        if self.read_exc is not None:
            raise self.read_exc
        return list(self.rows)


class FakeDomain:
    """守门器级替身域：可直接指定 provider / rows / 异常，不经过真实后端"""

    def __init__(self, provider="mem0", rows=(), read_exc=None):
        self.provider = provider
        self.scope = ScopedMemoryScope.from_mapping(SCOPE)
        self.rows = list(rows)
        self.read_exc = read_exc
        self.read_calls = []
        self.write_calls = []

    def read(self, query="", *, limit=None, memory_types=None):
        self.read_calls.append(query)
        if self.read_exc is not None:
            raise self.read_exc
        return ScopedReadOutcome(ok=True, entries=tuple(self.rows))

    def write(self, entry, **kw):
        self.write_calls.append(entry)
        return ScopedWriteOutcome(ok=True, bytes=len(str(entry.content).encode("utf-8")))


class RecordingChannel:
    def __init__(self, payload=None, returncode=0):
        self.invocations = []
        self.returncode = returncode
        self.payload = payload if payload is not None else {"status": "done"}

    def __call__(self, invocation):
        self.invocations.append(invocation)
        if self.returncode != 0:
            return RawOutput(stdout="", returncode=self.returncode, error="boom",
                             duration_ms=1.0)
        return RawOutput(stdout=json.dumps(self.payload), returncode=0, duration_ms=1.0)


def _ctx(metadata=None):
    return DelegationContext(
        goal="一个足够长的目标任务", constraints=["只读"],
        prior_artifacts=[], prohibitions=[], artifact_format="json",
        budget_tokens=100, timeout_seconds=10, callback_url="ui://sync",
        tenant_id=TENANT, subject_id=SUBJECT,
        delegation_id=DELEGATION_ID, delegate_actor="sub_agent:fixed",
        metadata=dict(metadata or {}))


def _meta(**over):
    meta = {"memory_mode": "scoped", "memory_provider": "holographic",
            "memory_scope": dict(SCOPE)}
    meta.update(over)
    return meta


def _ts(*, scoped=True, tools=("memory.read", "memory.write"), authorized=None):
    granted = list(tools)
    allowed = granted if authorized is None else list(authorized)
    return SubAgentToolset.build(granted, allowed, actor="sub_agent:x",
                                 scope=WS, scoped_memory=scoped)


def _holo_domain(store, audit=None):
    return ScopedMemoryDomain("holographic", scope=dict(SCOPE), store=store,
                              audit=audit)


def _gate(domain=None, *, toolset=None, audit=None, subject="scoped:user-1"):
    return ScopedMemoryGate(domain=(domain if domain is not None else FakeDomain()),
                            toolset=(toolset if toolset is not None else _ts()),
                            audit=audit, subject=subject)


def _row(workspace=WS, tenant=TENANT, subject=SUBJECT, content="fact"):
    return {"metadata": {"tenant_id": tenant, "workspace_id": workspace,
                         "subject_id": subject}, "content": content}


def _exec(meta, *, payload=None, domain=None, audit=None, broker=None,
          tools=("memory.read", "memory.write")):
    ch = RecordingChannel(payload if payload is not None else {"status": "done"})
    ex = DelegationExecutor(
        channel=ch, audit=audit, trusted=True,
        scoped_memory_domain=domain, memory_broker=broker)
    out = ex.execute(_ctx(metadata=meta), tools=list(tools),
                     authorized_capabilities=list(tools))
    return out, ch


# ════════════════════════════════════════════════════════════
#  1. 默认档 / 未开启 scoped 绝不触达
# ════════════════════════════════════════════════════════════


class TestNeverReached:
    def test_none档_执行器绝不触达scoped域(self):
        bomb = BombDomain()
        out, _ = _exec({"memory_mode": "none", "memory_provider": "holographic",
                        "memory_scope": dict(SCOPE)}, domain=bomb)
        assert out.ok is True
        assert out.memory == {}, "none 档 outcome.memory 必须保持空 dict"
        assert (bomb.reads, bomb.writes) == (0, 0), "none 档绝不触达 scoped 域"

    def test_brokered档_执行器绝不触达scoped域(self):
        from agent.memory.broker import BrokeredContext

        bomb = BombDomain()
        out, _ = _exec({"memory_mode": "brokered", "memory_scope": dict(SCOPE)},
                       domain=bomb,
                       broker=lambda *a, **k: BrokeredContext(degraded="empty_recall"))
        assert out.ok is True
        assert out.memory.get("mode") == "brokered"
        assert "ops" not in out.memory, "brokered 绝不产生 ops"
        assert (bomb.reads, bomb.writes) == (0, 0)

    def test_toolset未开scoped_不启用守门器(self):
        bomb = BombDomain()
        gate = ScopedMemoryGate.from_context(
            _meta(), domain=bomb, toolset=_ts(scoped=False), audit=None)
        assert gate is None
        assert (bomb.reads, bomb.writes) == (0, 0)

    def test_空域_不启用守门器(self):
        bomb = BombDomain()
        bomb.scope = ScopedMemoryScope()  # has_domain False
        assert ScopedMemoryGate.from_context(
            _meta(), domain=bomb, toolset=_ts(), audit=None) is None
        assert (bomb.reads, bomb.writes) == (0, 0)

    def test_domain为None_不启用守门器(self):
        assert ScopedMemoryGate.from_context(
            _meta(), domain=None, toolset=_ts(), audit=None) is None

    def test_scoped已开启_才返回守门器(self):
        gate = ScopedMemoryGate.from_context(
            _meta(), domain=BombDomain(), toolset=_ts(), audit=None)
        assert gate is not None and gate.subject == "scoped:" + SUBJECT

    def test_mode非scoped_不启用守门器(self):
        for mode in ("none", "brokered", "", "SCOPED_X"):
            assert ScopedMemoryGate.from_context(
                _meta(memory_mode=mode), domain=BombDomain(),
                toolset=_ts(), audit=None) is None


# ════════════════════════════════════════════════════════════
#  2. 权限闸门（越权不触达底层）
# ════════════════════════════════════════════════════════════


class TestAuthorization:
    def test_越权写_E_TOOL_NOT_AUTHORIZED_且不触达底层(self):
        domain = FakeDomain(provider="holographic")
        gate = _gate(domain, toolset=_ts(authorized=[]))
        result = gate.write_declared([{"content": "越权事实"}])
        assert result["ok"] is False
        assert result["error_code"] == E_TOOL_NOT_AUTHORIZED
        assert result["degraded"] == "tool_not_authorized"
        assert domain.write_calls == [], "越权声明不得触达域写入"

    def test_越权读_E_TOOL_NOT_AUTHORIZED_且不触达底层(self):
        domain = FakeDomain(provider="holographic")
        gate = _gate(domain, toolset=_ts(authorized=[]))
        result = gate.read("q")
        assert result["ok"] is False
        assert result["error_code"] == E_TOOL_NOT_AUTHORIZED
        assert domain.read_calls == [], "越权声明不得触达域读取"

    def test_授权后读写都执行(self):
        domain = FakeDomain(provider="holographic")
        gate = _gate(domain)
        read_result = gate.read("q")
        write_result = gate.write_declared([{"content": "合法事实"}])
        assert read_result["ok"] is True and domain.read_calls == ["q"]
        assert write_result["ok"] is True and len(domain.write_calls) == 1

    def test_记忆类工具名集合覆盖读写(self):
        # 口径自检：读/写名集合各自覆盖 §5.7 机制 3 点名的记忆读写类别
        assert "memory.read" in READ_TOOL_NAMES and "search_memory" in READ_TOOL_NAMES
        assert "memory.write" in WRITE_TOOL_NAMES and "remember" in WRITE_TOOL_NAMES


# ════════════════════════════════════════════════════════════
#  3. 守域闸门（母体 scope 强制覆盖 + check_domain 拒越界）
# ════════════════════════════════════════════════════════════


class TestDomainGuard:
    def test_声明域标识被母体scope强制覆盖(self):
        store = RecordingStore()
        domain = _holo_domain(store)
        gate = _gate(domain)
        result = gate.write_declared([{
            "content": "项目使用 tabs 缩进",
            "tenant_id": "tenant_evil", "workspace_id": OTHER_WS,
            "subject_id": OTHER_SUBJECT, "memory_type": "fact"}])
        assert result["ok"] is True and store.writes == 1
        entry = store.last_entry
        # 声明里的域标识一律被丢弃，落库域 == 母体 scope
        assert (entry.tenant_id, entry.workspace_id, entry.subject_id) == (
            TENANT, WS, SUBJECT)
        assert store.last_scope.as_dict() == SCOPE

    def test_显式scope越界被check_domain拒(self):
        store = RecordingStore()
        domain = _holo_domain(store)
        gate = _gate(domain)
        result = gate.write_declared([{
            "content": "越界事实", "scope": project_scope(OTHER_WS)}])
        assert result["written"] == 0
        item = result["items"][0]
        assert item["ok"] is False
        assert item["error_code"] == E_MEMORY_SCOPE_MISMATCH
        assert item["degraded"] == "scope_workspace_mismatch"
        assert store.writes == 0, "越域不得落库"
        assert domain.guard.status()["entries"] == 0, "被拒写入不得吃掉配额"

    def test_同域显式scope放行(self):
        store = RecordingStore()
        domain = _holo_domain(store)
        gate = _gate(domain)
        result = gate.write_declared([{
            "content": "同域事实", "scope": project_scope(WS)}])
        assert result["ok"] is True and store.writes == 1


# ════════════════════════════════════════════════════════════
#  4. 读取侧后置域过滤（显式镜像 scoped_store 的 mem0 规则；幂等）
# ════════════════════════════════════════════════════════════


class TestReadFilter:
    def test_mem0_域不符剔除(self):
        domain = FakeDomain(provider="mem0", rows=[
            _row(workspace=WS), _row(workspace=OTHER_WS), _row(tenant="other")])
        gate = _gate(domain)
        result = gate.read("q")
        assert result["ok"] is True
        assert result["count"] == 1
        assert result["dropped"] == 2
        assert result["degraded"] == "scope_filtered"

    def test_mem0_缺metadata一律剔除(self):
        domain = FakeDomain(provider="mem0", rows=[
            _row(workspace=WS), {"content": "no metadata"}])
        gate = _gate(domain)
        result = gate.read("q")
        assert result["count"] == 1 and result["dropped"] == 1

    def test_holographic_无metadata行不误伤(self):
        class _Row:
            content = "holographic row"

        domain = FakeDomain(provider="holographic", rows=[_Row(), _Row()])
        gate = _gate(domain)
        result = gate.read("q")
        assert result["count"] == 2 and result["dropped"] == 0

    def test_holographic_带metadata且不符仍剔除(self):
        domain = FakeDomain(provider="holographic", rows=[
            _row(workspace=WS), _row(workspace=OTHER_WS)])
        gate = _gate(domain)
        result = gate.read("q")
        assert result["count"] == 1 and result["dropped"] == 1

    def test_幂等_已过滤行再滤结果与计数不变(self):
        rows = [_row(workspace=WS)]
        domain = FakeDomain(provider="mem0", rows=rows)
        gate = _gate(domain)
        first = gate.read("q")
        assert (first["count"], first["dropped"]) == (1, 0)
        # 模拟 #1063 合入后 domain.read 已过滤：同一批行再经守门器过滤，结果不变
        kept, dropped = gate._filter_rows(rows)
        assert (len(kept), dropped) == (first["count"], first["dropped"])
        again = gate.read("q")
        assert (again["count"], again["dropped"]) == (first["count"], first["dropped"])


# ════════════════════════════════════════════════════════════
#  5. fail-soft
# ════════════════════════════════════════════════════════════


class TestFailSoft:
    def test_read异常_降级不抛(self):
        domain = FakeDomain(provider="mem0", read_exc=RuntimeError("read down"))
        result = _gate(domain).read("q")
        assert result["ok"] is False
        assert result["error_code"] == E_MEMORY_DEGRADED
        assert result["degraded"].startswith("gate_read_failed")

    def test_write异常_降级不抛(self):
        class _BoomWrite(FakeDomain):
            def write(self, entry, **kw):
                raise RuntimeError("write down")

        result = _gate(_BoomWrite()).write_declared([{"content": "x"}])
        assert result["ok"] is False and result["written"] == 0
        item = result["items"][0]
        assert item["ok"] is False
        assert item["error_code"] == E_MEMORY_DEGRADED
        assert item["degraded"].startswith("write_failed")

    def test_审计sink抛错_failsoft(self):
        audit = BoomAudit()
        domain = FakeDomain(provider="holographic")
        gate = _gate(domain, audit=audit)
        assert gate.read("q")["ok"] is True
        assert gate.write_declared([{"content": "x"}])["ok"] is True

    def test_executor_域写异常_委派仍ok(self):
        store = RecordingStore(write_exc=RuntimeError("store down"))
        out, _ = _exec(_meta(), payload={
            "status": "done", "memory_writes": [{"content": "事实"}]},
            domain=_holo_domain(store))
        assert out.ok is True, "记忆操作失败绝不阻断委派"
        assert out.memory["ops"]["ok"] is False
        assert out.memory["ops"]["writes"]["attempted"] is True

    def test_executor_域读异常_委派仍ok(self):
        store = RecordingStore(read_exc=RuntimeError("read down"))
        out, _ = _exec(_meta(), payload={"status": "done", "memory_reads": ["q"]},
                       domain=_holo_domain(store))
        assert out.ok is True
        assert out.memory["ops"]["reads"][0]["ok"] is False


# ════════════════════════════════════════════════════════════
#  6. 审计（承重）
# ════════════════════════════════════════════════════════════


class TestAudit:
    def test_读写拒绝各留gate审计且不含正文(self):
        audit = RecordingAudit()
        gate = _gate(FakeDomain(provider="holographic"), audit=audit)
        assert gate.read("q")["ok"] is True
        assert gate.write_declared([{"content": "秘密正文ABC"}])["ok"] is True
        denied = _gate(FakeDomain(provider="holographic"),
                       toolset=_ts(authorized=[]), audit=audit)
        assert denied.write_declared([{"content": "越权"}])["ok"] is False
        actions = audit.actions()
        assert actions.count(AUDIT_SCOPED_GATE) >= 3
        assert "秘密正文ABC" not in json.dumps(audit.events, ensure_ascii=False)

    def test_审计payload带op与计数(self):
        audit = RecordingAudit()
        gate = _gate(FakeDomain(provider="mem0", rows=[_row(workspace=WS)]),
                     audit=audit)
        gate.read("q")
        payload = audit.events[-1]["payload"]
        assert payload["op"] == "read" and payload["count"] == 1
        assert "query" not in payload


# ════════════════════════════════════════════════════════════
#  7. 通道声明收口（非法只丢不杀）
# ════════════════════════════════════════════════════════════


class TestChannelDeclarations:
    def test_合法声明被投影(self):
        reads, writes, degraded = collect_memory_declarations({
            "memory_reads": ["缩进偏好"],
            "memory_writes": [{"content": "项目使用 tabs", "memory_type": "fact"}]})
        assert reads == ("缩进偏好",)
        assert writes == ({"content": "项目使用 tabs", "memory_type": "fact"},)
        assert degraded == ""

    def test_声明里的域标识被剥离(self):
        _, writes, _ = collect_memory_declarations({
            "memory_writes": [{"content": "x", "tenant_id": "t",
                               "workspace_id": "w", "subject_id": "s"}]})
        assert "tenant_id" not in writes[0]
        assert "workspace_id" not in writes[0]
        assert "subject_id" not in writes[0]

    def test_非法声明丢弃并记degraded(self):
        reads, writes, degraded = collect_memory_declarations({
            "memory_reads": "nope",
            "memory_writes": [{"no_content": 1}, "x", {"content": "合法"}]})
        assert reads == ()
        assert writes == ({"content": "合法"},)
        assert "memory_reads_not_list" in degraded
        assert "memory_writes_dropped" in degraded

    def test_超出条数截断(self):
        _, writes, degraded = collect_memory_declarations({
            "memory_writes": [{"content": "n%d" % i} for i in range(20)]})
        assert len(writes) == 8
        assert "memory_writes_truncated" in degraded

    def test_to_dict不回正文(self):
        invoker = lambda: RawOutput(
            stdout=json.dumps({"status": "done",
                               "memory_writes": [{"content": "机密正文XYZ"}]}),
            returncode=0, duration_ms=1.0)
        out = resolve_channel_output(invoker)
        blob = json.dumps(out.to_dict(), ensure_ascii=False)
        assert "机密正文XYZ" not in blob
        assert out.to_dict()["memory_declarations"]["writes"][0]["content_chars"] > 0

    def test_非法声明_委派不失败且ops记degraded(self):
        store = RecordingStore()
        out, _ = _exec(_meta(), payload={
            "status": "done",
            "memory_reads": "nope",
            "memory_writes": [{"no_content": 1}, {"content": "合法事实"}]},
            domain=_holo_domain(store))
        assert out.ok is True, "非法声明绝不能杀死委派"
        ops = out.memory["ops"]
        assert "memory_reads_not_list" in ops["declared"]["degraded"]
        assert "memory_writes_dropped" in ops["declared"]["degraded"]
        assert ops["declared"]["writes"] == 1, "合法的那条仍应执行"
        assert store.writes == 1

    def test_默认档声明不被执行(self):
        bomb = BombDomain()
        out, _ = _exec({"memory_mode": "none", "memory_scope": dict(SCOPE)},
                       payload={"status": "done", "memory_reads": ["q"],
                                "memory_writes": [{"content": "x"}]},
                       domain=bomb)
        assert out.ok is True and out.memory == {}
        assert (bomb.reads, bomb.writes) == (0, 0)


# ════════════════════════════════════════════════════════════
#  8. 执行器集成（声明 -> 母体执行）
# ════════════════════════════════════════════════════════════


class TestExecutorIntegration:
    def test_scoped声明读写被执行且无正文(self):
        store = RecordingStore()
        out, _ = _exec(_meta(), payload={
            "status": "done",
            "memory_reads": ["缩进偏好"],
            "memory_writes": [{"content": "项目使用 tabs 缩进"}]},
            domain=_holo_domain(store))
        assert out.ok is True
        ops = out.memory["ops"]
        assert ops["attempted"] is True and ops["ok"] is True
        assert ops["reads"][0]["count"] == 0
        assert ops["writes"]["written"] == 1 and store.writes == 1
        assert "项目使用 tabs 缩进" not in json.dumps(out.memory, ensure_ascii=False)

    def test_scoped无声明_ops记no_declared_ops_且不触达(self):
        store = RecordingStore()
        out, _ = _exec(_meta(), payload={"status": "done"},
                       domain=_holo_domain(store))
        assert out.ok is True
        assert out.memory["ops"]["degraded"] == "no_declared_ops"
        assert out.memory["ops"]["attempted"] is False
        assert (store.reads, store.writes) == (0, 0)

    def test_委派失败_不执行声明(self):
        # 通道解析成功但工具闸门判委派失败：此时声明已解析出来，仍必须**不执行**
        store = RecordingStore()
        bad, _ = _exec(_meta(), payload={
            "status": "done",
            "memory_writes": [{"content": "越界事实"}],
            "tool_calls": [{"name": "approval.approve"}]},
            domain=_holo_domain(store), tools=("memory.write",))
        assert bad.ok is False and bad.error_code == E_TOOL_NOT_AUTHORIZED
        assert bad.memory["ops"]["degraded"] == "delegation_not_ok"
        assert (store.reads, store.writes) == (0, 0), "委派失败绝不执行声明"

    def test_越权声明写_ops拒但委派ok(self):
        store = RecordingStore()
        # 授权子集不含 memory.write => toolset 不放行
        out, _ = _exec(_meta(), payload={
            "status": "done", "memory_writes": [{"content": "越权"}]},
            domain=_holo_domain(store), tools=("memory.read",))
        assert out.ok is True
        assert out.memory["ops"]["writes"]["error_code"] == E_TOOL_NOT_AUTHORIZED
        assert store.writes == 0


# ════════════════════════════════════════════════════════════
#  9. 可证伪探针（证明守卫承重）
# ════════════════════════════════════════════════════════════


class _NoEmitGate(ScopedMemoryGate):
    def _emit(self, result, *, status):
        return False


class _AlwaysAllowGate(ScopedMemoryGate):
    def _authorize(self, names):
        return {"allowed": True, "tool": str(names[0] if names else "")}


class _NoOverrideGate(ScopedMemoryGate):
    """错误实现：声明里的域标识直接采信（不强制覆盖母体 scope）"""

    def _write_one(self, spec):
        from agent.memory.scoped_store import ScopedMemoryEntry

        data = spec if isinstance(spec, dict) else {}
        entry = ScopedMemoryEntry(
            content=str(data.get("content") or ""),
            tenant_id=str(data.get("tenant_id") or ""),
            workspace_id=str(data.get("workspace_id") or ""),
            subject_id=str(data.get("subject_id") or ""))
        outcome = self.domain.write(entry)
        return {"ok": bool(getattr(outcome, "ok", False)),
                "error_code": str(getattr(outcome, "error_code", "") or ""),
                "degraded": str(getattr(outcome, "degraded", "") or "")}


def test_探针_去掉emit_审计守卫变红():
    audit = RecordingAudit()
    buggy = _NoEmitGate(domain=FakeDomain(provider="holographic"),
                        toolset=_ts(), audit=audit)
    assert buggy.read("q")["ok"] is True
    assert AUDIT_SCOPED_GATE not in audit.actions(), "去掉 emit 后审计守卫变红"
    real = _gate(FakeDomain(provider="holographic"), audit=audit)
    assert real.read("q")["ok"] is True
    assert AUDIT_SCOPED_GATE in audit.actions()


def test_探针_去掉权限闸门_越权守卫变红():
    domain = FakeDomain(provider="holographic")
    buggy = _AlwaysAllowGate(domain=domain, toolset=_ts(authorized=[]))
    assert buggy.write_declared([{"content": "越权"}])["ok"] is True
    assert len(domain.write_calls) == 1, "去掉权限闸门后越权被放行"
    guarded = _gate(FakeDomain(provider="holographic"),
                    toolset=_ts(authorized=[]))
    assert guarded.write_declared([{"content": "越权"}])["ok"] is False


def test_探针_去掉域覆盖_越界守卫变红():
    store = RecordingStore()
    domain = _holo_domain(store)
    buggy = _NoOverrideGate(domain=domain, toolset=_ts())
    result = buggy.write_declared([{"content": "越界", "workspace_id": OTHER_WS}])
    assert result["ok"] is False, "不覆盖时越域确实被 check_domain 拒"
    # 真实实现：覆盖成母体 scope ⇒ 同一声明成功落库
    store2 = RecordingStore()
    real = _gate(_holo_domain(store2))
    assert real.write_declared([
        {"content": "越界", "workspace_id": OTHER_WS}])["ok"] is True
    assert store2.last_entry.workspace_id == WS
