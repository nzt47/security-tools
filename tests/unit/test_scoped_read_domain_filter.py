"""scoped 读的域加固守卫（agent/memory/scoped_store.py::ScopedMemoryDomain.read）

堵 mem0 跨域串读 + 空域断言 + 成功留痕。每条可证伪（回退产品逻辑 ⇒ 红）：

  S1 空域不触达后端（归属不可判却放行 = 把"谁的记忆"交给后端决定）；
  S2 mem0 后置域过滤：异域/缺 metadata 条目剔除，同域保留，degraded=scope_filtered；
  S3 holographic 不做二次过滤（其 recall 已按 tenancy 过滤，且条目无 metadata）；
  S4 成功读留痕 AUDIT_SCOPED_READ（只记条数，不含正文）；后端异常 fail-soft。

不 import app_server；不触达真实后端/网络。
"""
from __future__ import annotations

from agent.memory.quota import AUDIT_SCOPED_DEGRADED, AUDIT_SCOPED_READ
from agent.memory.scoped_store import E_MEMORY_SCOPE_MISMATCH, ScopedMemoryDomain


class _Audit:
    def __init__(self):
        self.events = []

    def record(self, action, actor="", subject="", payload=None, status=""):
        self.events.append({"action": action, "payload": dict(payload or {}),
                            "status": status})


class _Result:
    """MemoryResult 替身（mem0 search 的形状：带 metadata）"""

    def __init__(self, metadata):
        self.metadata = metadata
        self.content = "x"


class _Store:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def read(self, query, scope, *, limit, memory_types):
        self.calls += 1
        return list(self.rows)


class _ExplodingStore:
    def __init__(self):
        self.touched = 0

    async def read(self, *a, **k):
        self.touched += 1
        raise AssertionError("空域不得触达后端")


def _domain(scope, *, store=None, audit=None, provider="holographic"):
    return ScopedMemoryDomain(provider, scope=scope, store=store, audit=audit)


class TestS1EmptyDomain:
    def test_空域不触达后端(self):
        store = _ExplodingStore()
        out = _domain({}, store=store, audit=_Audit()).read("q")
        assert out.ok is False
        assert out.degraded == "domain_missing"
        assert out.error_code == E_MEMORY_SCOPE_MISMATCH
        assert store.touched == 0, "空域读取触达了后端（归属不可判却放行）"


class TestS2Mem0ScopeFilter:
    def test_跨域条目被剔除(self):
        rows = [_Result({"tenant_id": "default"}), _Result({"tenant_id": "other"})]
        audit = _Audit()
        out = _domain({"tenant_id": "default"}, store=_Store(rows),
                      audit=audit, provider="mem0").read("q")
        assert out.ok is True
        assert len(out.entries) == 1, "异域条目泄漏 = mem0 跨域串读"
        assert out.degraded == "scope_filtered"
        assert any(e["action"] == AUDIT_SCOPED_DEGRADED
                   and e["payload"].get("reason") == "scope_filtered"
                   for e in audit.events)

    def test_缺metadata条目被剔除(self):
        rows = [_Result({}), _Result({"tenant_id": "default"})]
        out = _domain({"tenant_id": "default"}, store=_Store(rows),
                      audit=_Audit(), provider="mem0").read("q")
        assert len(out.entries) == 1, "缺 metadata 的条目不可判归属，必须剔除"

    def test_同域条目保留(self):
        rows = [_Result({"tenant_id": "default", "workspace_id": "w1"})]
        out = _domain({"tenant_id": "default", "workspace_id": "w1"},
                      store=_Store(rows), audit=_Audit(), provider="mem0").read("q")
        assert len(out.entries) == 1 and out.degraded == ""


class TestS3HolographicUntouched:
    def test_holographic_不做二次过滤(self):
        rows = [object(), object()]  # 无 metadata 的 dataclass 替身
        out = _domain({"tenant_id": "default"}, store=_Store(rows),
                      audit=_Audit(), provider="holographic").read("q")
        assert out.ok is True and len(out.entries) == 2
        assert out.degraded == "", "holographic（recall 已按 tenancy 过滤）不该被二次过滤"


class TestS4ReadAudit:
    def test_成功读留痕不含正文(self):
        audit = _Audit()
        _domain({"tenant_id": "default"},
                store=_Store([_Result({"tenant_id": "default"})]),
                audit=audit, provider="mem0").read("q")
        reads = [e for e in audit.events if e["action"] == AUDIT_SCOPED_READ]
        assert len(reads) == 1
        assert reads[0]["payload"].get("count") == 1
        assert "content" not in reads[0]["payload"]
        assert "x" not in str(reads[0]["payload"]), "审计不得含正文"

    def test_后端异常_fail_soft_并留降级(self):
        class _Boom:
            async def read(self, *a, **k):
                raise RuntimeError("boom")

        audit = _Audit()
        out = _domain({"tenant_id": "default"}, store=_Boom(), audit=audit,
                      provider="holographic").read("q")
        assert out.ok is False and out.degraded.startswith("read_failed")
        assert any(e["action"] == AUDIT_SCOPED_DEGRADED for e in audit.events)
