"""按分身的记忆主权操作守卫（agent/memory/scoped_store.py 的 export/erase/import）

【为什么有这份守卫（不这样会怎样）】
    记忆擦除是**不可逆**的，记忆导出/迁移又牵涉租户边界。这三类操作最容易出的事故：
      ① 不确认就删（脚本/接口一调，数据没了）；
      ② 删了但没快照（误删无法回灌）；
      ③ 迁移把**源域**的 scope/租户带过去（跨 workspace 越域）或反向（别人能借导入写进本域）。
    本守卫逐条钉死：
      M1 导出：只读；空域断言；失败返回明确 error_code 且**返回空**；
      M2 擦除：缺 confirm ⇒ 不触达后端；有 confirm ⇒ **先快照后删**；快照写失败 ⇒ 中止删除；
      M3 迁移：默认由**目标域**决定域（源 scope 不带），payload 里伪造的 tenant/workspace 不生效；
      M4 后端边界：mem0 无列出/删除 ⇒ fail-closed（E_MEMORY_OP_UNSUPPORTED），不假装支持。

不 import app_server；用 tmp_path 下的真实 LayeredMemoryStore（不碰仓库 data/）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent.memory.scoped_store import (AUDIT_SCOPED_ERASE, AUDIT_SCOPED_EXPORT,
                                       E_MEMORY_ERASE_NOT_CONFIRMED,
                                       E_MEMORY_OP_UNSUPPORTED,
                                       E_MEMORY_SCOPE_MISMATCH, ScopedMemoryDomain,
                                       ScopedMemoryEntry, scoped_entry_from_exported)


def _domain(root, *, tenant="t1", workspace="w1", subject="s1", provider="holographic"):
    return ScopedMemoryDomain(provider, root=str(root),
                              scope={"tenant_id": tenant, "workspace_id": workspace,
                                     "subject_id": subject, "limit": 10})


def _seed(domain, n=2):
    for i in range(n):
        out = domain.write(ScopedMemoryEntry(content="fact-%d tabs 缩进" % i,
                                             memory_type="fact", key="k%d" % i))
        assert out.ok, out.error_code


class TestM1Export:
    def test_导出只读且计数(self, tmp_path):
        d = _domain(tmp_path / "a")
        _seed(d, 2)
        exp = d.export_entries()
        assert exp.ok is True and len(exp.entries) == 2
        assert all("content_redacted" in row for row in exp.entries)
        assert exp.to_dict() == {"ok": True, "error_code": "", "degraded": "",
                                 "count": 2}
        # 只读：导出后条数不变
        assert len(d.export_entries().entries) == 2

    def test_空域拒绝且不触达后端(self, tmp_path):
        d = ScopedMemoryDomain("holographic", root=str(tmp_path / "b"), scope={})
        exp = d.export_entries()
        assert exp.ok is False and exp.error_code == E_MEMORY_SCOPE_MISMATCH
        assert exp.entries == ()


class TestM2Erase:
    def test_缺confirm不删(self, tmp_path):
        d = _domain(tmp_path / "c")
        _seed(d, 2)
        out = d.erase_entries()
        assert out.ok is False and out.error_code == E_MEMORY_ERASE_NOT_CONFIRMED
        assert out.deleted == 0
        assert len(d.export_entries().entries) == 2, "缺 confirm 却动了数据"

    def test_有confirm先快照后删(self, tmp_path):
        d = _domain(tmp_path / "d")
        _seed(d, 2)
        snap = tmp_path / "snap.json"
        out = d.erase_entries(confirm=True, snapshot_path=str(snap))
        assert out.ok is True and out.scanned == 2 and out.deleted == 2
        assert str(snap) == out.snapshot_path and snap.is_file()
        doc = json.loads(snap.read_text(encoding="utf-8"))
        assert doc["count"] == 2 and len(doc["entries"]) == 2
        assert len(d.export_entries().entries) == 0

    def test_快照写失败则中止删除(self, tmp_path, monkeypatch):
        d = _domain(tmp_path / "e")
        _seed(d, 2)

        def _boom(self, path, rows):
            raise OSError("disk full")

        monkeypatch.setattr(ScopedMemoryDomain, "_write_snapshot", _boom)
        out = d.erase_entries(confirm=True, snapshot_path=str(tmp_path / "x.json"))
        assert out.ok is False and out.deleted == 0
        assert out.degraded.startswith("snapshot_failed")
        assert len(d.export_entries().entries) == 2, "快照失败却仍然删了"


class TestM3Import:
    def test_迁移落在目标域(self, tmp_path):
        src = _domain(tmp_path / "src")
        _seed(src, 2)
        rows = [dict(r) for r in src.export_entries().entries]
        dst = _domain(tmp_path / "dst", tenant="t2", workspace="w2", subject="s2")
        out = dst.import_entries(rows)
        assert out.ok is True and out.imported == 2 and out.failed == 0
        landed = dst.export_entries().entries
        assert len(landed) == 2
        assert {r.get("tenant_id") for r in landed} == {"t2"}

    def test_payload伪造的域不生效(self, tmp_path):
        src = _domain(tmp_path / "src2")
        _seed(src, 1)
        forged = [dict(src.export_entries().entries[0],
                       tenant_id="HACK", workspace_id="HACK", subject_id="HACK")]
        dst = _domain(tmp_path / "dst2", tenant="t9", workspace="w9", subject="s9")
        assert dst.import_entries(forged).imported == 1
        landed = dst.export_entries().entries
        assert {r.get("tenant_id") for r in landed} == {"t9"}, "伪造域借导入生效了"

    def test_同域回灌保留scope(self, tmp_path):
        d = _domain(tmp_path / "same")
        _seed(d, 1)
        rows = [dict(r) for r in d.export_entries().entries]
        assert d.erase_entries(confirm=True, snapshot_path=str(tmp_path / "s.json")).ok
        assert d.import_entries(rows, keep_scope=True).imported == 1
        assert len(d.export_entries().entries) == 1

    def test_映射函数不带源域标识(self):
        entry = scoped_entry_from_exported(
            {"content_redacted": "x", "type": "fact", "id": "i1",
             "tenant_id": "T", "workspace_id": "W", "subject_id": "S",
             "scope": "project:W"})
        assert entry.key == "i1" and entry.scope == ""
        assert entry.tenant_id == "" and entry.workspace_id == ""


class TestM4BackendBoundary:
    def test_mem0_内置降级_导出擦除可用(self, tmp_path):
        # mem0 未装 ⇒ 内置降级存储；事实带 scope 元数据 ⇒ 导出/擦除按域可用
        d = ScopedMemoryDomain("mem0", root=str(tmp_path / "m"),
                               scope={"tenant_id": "t", "workspace_id": "w",
                                      "subject_id": "s"})
        assert d.export_entries().ok is True
        assert d.erase_entries(confirm=True).ok is True

    def test_mem0_引擎路径_导出擦除仍fail_closed(self, tmp_path, monkeypatch):
        d = ScopedMemoryDomain("mem0", root=str(tmp_path / "m2"),
                               scope={"tenant_id": "t", "workspace_id": "w",
                                      "subject_id": "s"})
        backend = d.select_store()
        # 引擎路径的 add/search 不接受域参数 ⇒ 无法保证域隔离 ⇒ 仍 fail-closed
        monkeypatch.setattr(type(backend._adapter), "uses_mem0_engine",
                            property(lambda self: True))
        assert d.export_entries().error_code == E_MEMORY_OP_UNSUPPORTED
        erased = d.erase_entries(confirm=True)
        assert erased.ok is False and erased.error_code == E_MEMORY_OP_UNSUPPORTED

    def test_审计动作名稳定(self):
        assert AUDIT_SCOPED_EXPORT == "subagent.memory.export"
        assert AUDIT_SCOPED_ERASE == "subagent.memory.erase"


class TestShardedTenants:
    """S5-2：scoped mem0 内置降级按 (tenant, workspace, subject) 物理分库"""

    def test_路径按域分片且互不相同(self, tmp_path):
        from agent.memory.scoped_store import ScopedMemoryScope, scoped_shard_path
        root = str(tmp_path)
        s1 = ScopedMemoryScope(tenant_id="t1", workspace_id="w", subject_id="s")
        s2 = ScopedMemoryScope(tenant_id="t2", workspace_id="w", subject_id="s")
        p1 = scoped_shard_path(root, s1)
        p2 = scoped_shard_path(root, s2)
        assert p1 != p2
        assert p1.endswith(os.path.join("t1", "w", "s.json"))
        assert p2.endswith(os.path.join("t2", "w", "s.json"))

    def test_不安全字符不折叠到同一路径(self, tmp_path):
        from agent.memory.scoped_store import ScopedMemoryScope, scoped_shard_path
        root = str(tmp_path)
        a = scoped_shard_path(root, ScopedMemoryScope(tenant_id="a/b"))
        b = scoped_shard_path(root, ScopedMemoryScope(tenant_id="a_b"))
        assert a != b, "不同租户被折叠到同一物理文件 = 串库"

    def test_两个租户物理隔离(self, tmp_path):
        root = tmp_path / "m"
        d1 = _domain(root, tenant="t1", workspace="w", subject="s", provider="mem0")
        d2 = _domain(root, tenant="t2", workspace="w", subject="s", provider="mem0")
        if getattr(d1.select_store()._adapter, "_has_mem0", False):
            pytest.skip("本机装了 mem0 引擎：内置降级分片不适用")
        _seed(d1, 1)
        _seed(d2, 1)
        from agent.memory.scoped_store import scoped_shard_path
        p1 = Path(scoped_shard_path(str(root), d1.scope))
        p2 = Path(scoped_shard_path(str(root), d2.scope))
        assert p1.exists() and p2.exists() and p1 != p2
        f1 = json.loads(p1.read_text(encoding="utf-8"))
        f2 = json.loads(p2.read_text(encoding="utf-8"))
        # 每个分片文件只含本租户的事实（物理上不可能串库）
        assert len(f1) == 1 and len(f2) == 1
        assert all(v.get("metadata", {}).get("tenant_id") == "t1" for v in f1.values())
        assert all(v.get("metadata", {}).get("tenant_id") == "t2" for v in f2.values())


class TestM5CLI:
    def test_cli_导出_擦除门槛_确认擦除(self, tmp_path):
        import subprocess
        import sys

        root = tmp_path / "cli"
        d = _domain(root)
        _seed(d, 2)
        script = Path(__file__).resolve().parents[2] / "scripts" / "subagent_memory_ops.py"
        base = [sys.executable, str(script)]
        scope = ["--tenant", "t1", "--workspace", "w1", "--subject", "s1",
                 "--root", str(root)]

        out_file = tmp_path / "facts.json"
        p = subprocess.run(base + ["export"] + scope + ["--out", str(out_file)],
                           capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, p.stderr[-800:]
        doc = json.loads(out_file.read_text(encoding="utf-8"))
        assert doc["count"] == 2

        p = subprocess.run(base + ["erase"] + scope, capture_output=True,
                           text=True, timeout=120)
        assert p.returncode == 3, "缺 --confirm 却允许擦除"

        snap = tmp_path / "erase.json"
        p = subprocess.run(base + ["erase"] + scope + ["--snapshot", str(snap),
                                                        "--confirm"],
                           capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, p.stderr[-800:]
        assert snap.is_file()
        assert len(d.export_entries().entries) == 0
