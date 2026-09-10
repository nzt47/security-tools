"""TASK-S2-02 存量 JSONL 只读归档 + 双写过渡单测（`agent/audit/migration.py`）

覆盖：盘点 / 只读归档（不删除、不追溯）/ 镜像副本 / 清单 / 只读读取 /
双写一致性 / 回滚窗口 / 旧轨退役 / AuditLogger 双写兼容。
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

from agent.audit.chain import AuditChain, reset_audit_chains
from agent.audit.facade import AuditFacade
from agent.audit.migration import (
    MODE_MIRROR,
    MODE_READONLY,
    LegacyTarget,
    LegacyTrack,
    archive_legacy_files,
    inventory_legacy_files,
    is_readonly,
    legacy_write_allowed,
    read_legacy_records,
    record_migration_event,
)


# ════════════════════════════════════════════════════════════
#  夹具
# ════════════════════════════════════════════════════════════


def _write_jsonl(path: pathlib.Path, records: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


@pytest.fixture
def legacy_root(tmp_path):
    """构造仿真的存量写入面（旧日审计分片 + 今日分片 + 审批状态库 + 谱系库）"""
    _write_jsonl(tmp_path / "data" / "audit" / "audit_20260101.jsonl",
                 [{"timestamp": "2026-01-01T00:00:00+00:00", "action": "old.action"},
                  {"timestamp": "2026-01-01T00:01:00+00:00", "action": "old.action2"}])
    _write_jsonl(tmp_path / "data" / "audit" / "audit_20260102.jsonl",
                 [{"timestamp": "2026-01-02T00:00:00+00:00", "action": "old2"}])
    _write_jsonl(tmp_path / "data" / "approval_records.jsonl",
                 [{"record_id": "appr-1", "state": "approved",
                   "updated_at": "2026-01-01T00:00:00"}])
    _write_jsonl(tmp_path / "data" / "evolution_archive.jsonl",
                 [{"record_id": "evt-1", "object_id": "x",
                   "created_at": "2026-01-01T00:00:00"}])
    return tmp_path


@pytest.fixture
def chain(tmp_path):
    reset_audit_chains()
    c = AuditChain(str(tmp_path / "chain" / "audit_chain.db"),
                   roots_path=str(tmp_path / "chain" / "roots.jsonl"),
                   signing_key_path=str(tmp_path / "chain" / "k.pem"), auto_seal=False)
    yield c
    c.close(timeout=2.0)
    reset_audit_chains()


@pytest.fixture
def facade(chain):
    return AuditFacade(chain=chain, enabled=True, db_path=chain.db_path,
                       roots_path=chain.roots_path)


# ════════════════════════════════════════════════════════════
#  1. 盘点
# ════════════════════════════════════════════════════════════


class TestInventory:
    def test_inventory_reports_size_lines_and_hashes(self, legacy_root):
        infos = inventory_legacy_files(str(legacy_root))
        by_name = {os.path.basename(i.path): i for i in infos if i.exists}
        old = by_name["audit_20260101.jsonl"]
        assert old.line_count == 2
        assert old.size_bytes > 0 and len(old.sha256) == 64
        assert old.first_ts.startswith("2026-01-01")
        assert old.last_ts.startswith("2026-01-01")

    def test_inventory_marks_missing_targets(self, legacy_root):
        infos = inventory_legacy_files(str(legacy_root))
        missing = [i for i in infos if not i.exists]
        assert missing and all(i.exists is False for i in missing)

    def test_inventory_custom_targets_only(self, legacy_root):
        infos = inventory_legacy_files(
            str(legacy_root),
            targets=[LegacyTarget("only_approval", "data/approval_records.jsonl",
                                  MODE_MIRROR, "state")])
        assert len(infos) == 1
        assert infos[0].name == "only_approval" and infos[0].exists

    def test_inventory_does_not_modify_files(self, legacy_root):
        target = legacy_root / "data" / "audit" / "audit_20260101.jsonl"
        before = target.read_text(encoding="utf-8")
        inventory_legacy_files(str(legacy_root))
        assert target.read_text(encoding="utf-8") == before
        assert is_readonly(str(target)) is False

    def test_inventory_survives_corrupt_lines(self, tmp_path):
        p = tmp_path / "data" / "audit" / "audit_20260101.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json}\n" + json.dumps({"timestamp": "2026-01-01"}) + "\n",
                     encoding="utf-8")
        infos = [i for i in inventory_legacy_files(str(tmp_path))
                 if i.path.endswith("audit_20260101.jsonl")]
        assert infos[0].line_count == 2   # 坏行计入行数，不炸


# ════════════════════════════════════════════════════════════
#  2. 只读归档（不删除、不追溯）
# ════════════════════════════════════════════════════════════


class TestArchive:
    def test_old_audit_shards_become_readonly(self, legacy_root):
        rep = archive_legacy_files(str(legacy_root))
        assert rep.readonly_count >= 2
        assert is_readonly(str(legacy_root / "data" / "audit" / "audit_20260101.jsonl"))
        assert rep.deleted == 0 and rep.retraced == 0

    def test_original_content_preserved(self, legacy_root):
        path = legacy_root / "data" / "audit" / "audit_20260101.jsonl"
        before = path.read_text(encoding="utf-8")
        archive_legacy_files(str(legacy_root))
        assert path.read_text(encoding="utf-8") == before
        os.chmod(str(path), 0o644)

    def test_state_files_are_mirrored_not_frozen(self, legacy_root):
        rep = archive_legacy_files(str(legacy_root))
        approval = legacy_root / "data" / "approval_records.jsonl"
        assert is_readonly(str(approval)) is False          # 系统记录本体仍可写
        copies = [i for i in rep.files if i.archived_copy]
        assert copies
        for info in copies:
            assert os.path.exists(info.archived_copy)
            assert is_readonly(info.archived_copy) is True
        assert rep.mirror_count >= 2

    def test_active_shard_not_frozen(self, tmp_path):
        """今日分片仍在写 → 跳过冻结（避免冻结正在写的文件）"""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date().isoformat().replace("-", "")
        _write_jsonl(tmp_path / "data" / "audit" / f"audit_{today}.jsonl",
                     [{"timestamp": "2026-01-01T00:00:00", "action": "live"}])
        rep = archive_legacy_files(str(tmp_path))
        live = tmp_path / "data" / "audit" / f"audit_{today}.jsonl"
        assert is_readonly(str(live)) is False
        assert any("活动分片跳过冻结" in i.note for i in rep.files)

    def test_manifest_written_with_all_files(self, legacy_root):
        rep = archive_legacy_files(str(legacy_root))
        manifest = pathlib.Path(rep.manifest_path)
        assert manifest.exists()
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert data["deleted"] == 0 and data["retraced"] == 0
        assert len(data["files"]) == len(rep.files)
        assert data["archived_at"]

    def test_dry_run_changes_nothing(self, legacy_root):
        path = legacy_root / "data" / "audit" / "audit_20260101.jsonl"
        rep = archive_legacy_files(str(legacy_root), dry_run=True)
        assert rep.readonly_count >= 2
        assert is_readonly(str(path)) is False
        assert not os.path.exists(rep.manifest_path)

    def test_archive_is_idempotent(self, legacy_root):
        archive_legacy_files(str(legacy_root))
        rep2 = archive_legacy_files(str(legacy_root))
        assert rep2.errors == []
        assert is_readonly(str(legacy_root / "data" / "audit" / "audit_20260102.jsonl"))
        for i in rep2.files:
            if i.path.endswith("audit_20260102.jsonl"):
                os.chmod(i.path, 0o644)


# ════════════════════════════════════════════════════════════
#  3. 只读读取 / 退役窗口判定
# ════════════════════════════════════════════════════════════


class TestReadOnlyAccess:
    def test_read_legacy_records(self, legacy_root):
        p = str(legacy_root / "data" / "audit" / "audit_20260101.jsonl")
        recs = read_legacy_records(p)
        assert [r["action"] for r in recs] == ["old.action", "old.action2"]

    def test_read_legacy_records_limit_offset(self, legacy_root):
        p = str(legacy_root / "data" / "audit" / "audit_20260101.jsonl")
        assert len(read_legacy_records(p, limit=1)) == 1
        assert read_legacy_records(p, offset=1)[0]["action"] == "old.action2"

    def test_read_missing_file_returns_empty(self, tmp_path):
        assert read_legacy_records(str(tmp_path / "nope.jsonl")) == []

    def test_read_skips_corrupt_lines(self, tmp_path):
        p = tmp_path / "x.jsonl"
        p.write_text("{bad}\n" + json.dumps({"a": 1}) + "\n", encoding="utf-8")
        assert read_legacy_records(str(p)) == [{"a": 1}]

    def test_is_readonly_false_for_missing_file(self, tmp_path):
        assert is_readonly(str(tmp_path / "nope")) is False

    def test_legacy_write_allowed_during_transition(self, tmp_path):
        p = tmp_path / "live.jsonl"
        p.write_text("", encoding="utf-8")
        assert legacy_write_allowed(str(p)) is True

    def test_legacy_write_blocked_after_retire_date(self, tmp_path):
        p = tmp_path / "live.jsonl"
        p.write_text("", encoding="utf-8")
        assert legacy_write_allowed(str(p), retire_after="2020-01-01",
                                    now="2026-09-10") is False
        assert legacy_write_allowed(str(p), retire_after="2030-01-01",
                                    now="2026-09-10") is True

    def test_legacy_write_blocked_when_readonly(self, tmp_path):
        p = tmp_path / "frozen.jsonl"
        p.write_text("{}\n", encoding="utf-8")
        os.chmod(str(p), 0o444)
        assert legacy_write_allowed(str(p)) is False
        os.chmod(str(p), 0o644)


# ════════════════════════════════════════════════════════════
#  4. 双写过渡与一致性
# ════════════════════════════════════════════════════════════


class TestDualWrite:
    def _track(self, tmp_path, facade, **kw):
        path = str(tmp_path / "legacy" / "audit_20260910.jsonl")
        return LegacyTrack(path, facade=facade, **kw), path

    def test_emit_writes_both_tracks(self, tmp_path, facade, chain):
        track, path = self._track(tmp_path, facade)
        res = track.emit("skill.delete", actor="admin", subject="skill:a",
                         payload={"k": 1},
                         legacy_record={"action": "skill.delete", "actor": "admin"})
        assert res.legacy_written and res.chain_written
        assert res.chain_seq == 1 and len(res.chain_self_hash) == 64
        assert os.path.exists(path)
        assert json.loads(pathlib.Path(path).read_text(encoding="utf-8").strip())["audit_ref"] \
            == res.audit_ref

    def test_consistency_full_match(self, tmp_path, facade, chain):
        track, _ = self._track(tmp_path, facade)
        for i in range(5):
            track.emit("act", actor="a", subject=f"s{i}",
                       legacy_record={"action": "act", "i": i})
        chain.flush()
        rep = track.verify_consistency()
        assert rep.consistent and rep.matched == 5 and rep.match_rate == 1.0
        assert "一致" in rep.summary()

    def test_consistency_detects_legacy_only_record(self, tmp_path, facade, chain):
        track, path = self._track(tmp_path, facade)
        track.emit("act", actor="a", legacy_record={"action": "act"})
        with open(path, "a", encoding="utf-8") as f:      # 手工追加旧轨（模拟漏写新链）
            f.write(json.dumps({"action": "act", "audit_ref": "manual-ref"}) + "\n")
        rep = track.verify_consistency()
        assert not rep.consistent
        assert rep.only_legacy == ["manual-ref"]

    def test_consistency_detects_chain_only_record(self, tmp_path, facade, chain):
        track, path = self._track(tmp_path, facade)
        track.emit("act", actor="a", legacy_record={"action": "act"})
        chain.flush()
        os.remove(path)                                   # 旧轨整文件丢失
        rep = track.verify_consistency()
        assert not rep.consistent and rep.only_chain

    def test_consistency_filters_other_tracks(self, tmp_path, facade, chain):
        track_a, _ = self._track(tmp_path / "a", facade)
        track_b, _ = self._track(tmp_path / "b", facade)
        track_a.emit("act", actor="a", legacy_record={"action": "a"})
        track_b.emit("act", actor="b", legacy_record={"action": "b"})
        chain.flush()
        assert track_a.verify_consistency().consistent
        assert track_b.verify_consistency().consistent

    def test_correlation_key_not_mangled_by_redaction(self, tmp_path, facade, chain):
        """回归：关联键经 technical 通道入链，不被脱敏启发式部分掩码"""
        track, path = self._track(tmp_path, facade)
        res = track.emit("act", actor="a", legacy_record={"action": "act"})
        chain.flush()
        entry = chain.entries()[0]
        assert entry.payload["audit_ref"] == res.audit_ref
        assert "*" not in entry.payload["audit_ref"]
        assert entry.payload["audit_track"] == track.name

    def test_rollback_window_legacy_only(self, tmp_path, chain):
        f = AuditFacade(chain=chain, enabled=False)   # 新链停写 → 回滚窗口
        track, path = self._track(tmp_path, f)
        res = track.emit("act", actor="a", legacy_record={"action": "act"})
        assert res.legacy_written is True and res.chain_written is False
        assert res.rollback is True
        assert track.in_rollback_window is True
        assert os.path.exists(path)

    def test_retired_legacy_writes_chain_only(self, tmp_path, facade, chain):
        track, path = self._track(tmp_path, facade, legacy_enabled=False)
        res = track.emit("act", actor="a", legacy_record={"action": "act"})
        assert res.legacy_written is False and res.chain_written is True
        assert not os.path.exists(path)          # 旧轨已退役：停写
        assert track.legacy_enabled is False

    def test_env_switch_disables_legacy_track(self, tmp_path, facade, monkeypatch):
        monkeypatch.setenv("AUDIT_LEGACY_WRITE", "0")
        track, path = self._track(tmp_path, facade)
        track.emit("act", actor="a", legacy_record={"action": "act"})
        assert not os.path.exists(path)

    def test_explicit_audit_ref_reused(self, tmp_path, facade, chain):
        track, path = self._track(tmp_path, facade)
        res = track.emit("act", actor="a", audit_ref="fixed-ref",
                         legacy_record={"action": "act"})
        assert res.audit_ref == "fixed-ref"
        chain.flush()
        assert chain.entries()[0].payload["audit_ref"] == "fixed-ref"

    def test_default_audit_ref_is_unique(self, tmp_path, facade, chain):
        track, path = self._track(tmp_path, facade)
        refs = {track.emit("act", actor="a", legacy_record={"action": "a"}).audit_ref
                for _ in range(20)}
        assert len(refs) == 20

    def test_migration_event_recorded_in_chain(self, tmp_path, chain):
        reset_audit_chains()
        seq = record_migration_event("audit.legacy_archive", actor="migration-bot",
                                     subject="data/audit", payload={"files": 3},
                                     db_path=chain.db_path)
        assert seq >= 1
        chain.flush()
        entry = chain.get(seq)
        assert entry.source == "migration" and entry.actor == "migration-bot"


# ════════════════════════════════════════════════════════════
#  5. AuditLogger 双写兼容（旧格式逐字保留）
# ════════════════════════════════════════════════════════════


class TestAuditLoggerDualWrite:
    def test_jsonl_and_chain_both_written(self, tmp_path):
        from agent.audit.logger import AuditLogger
        lg = AuditLogger(log_dir=str(tmp_path), dual_write=True)
        lg.log("test.action", "in", "out", metadata={"k": "v"})
        lg.flush()
        lines = lg._current_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert len(lg.query_chain()) == 1
        lg.close()

    def test_legacy_record_fields_unchanged(self, tmp_path):
        """S2-02 之前字段逐个仍在（additive：仅新增 audit_ref）"""
        from agent.audit.logger import AuditLogger
        lg = AuditLogger(log_dir=str(tmp_path))
        lg.log("test.action", "in", "out", status="error", metadata={"k": "v"})
        rec = json.loads(lg._current_file.read_text(encoding="utf-8").strip())
        for field in ("timestamp", "trace_id", "action", "input_hash", "output_hash",
                      "stack_depth", "status", "metadata"):
            assert field in rec
        assert rec["status"] == "error" and rec["metadata"] == {"k": "v"}
        assert rec["audit_ref"]
        lg.close()

    def test_dual_write_consistency_100_percent(self, tmp_path):
        from agent.audit.logger import AuditLogger
        lg = AuditLogger(log_dir=str(tmp_path))
        for i in range(6):
            lg.log(f"act{i}", "in", "out")
        lg.flush()
        rep = lg.track.verify_consistency()
        assert rep.consistent and rep.matched == 6
        lg.close()

    def test_plain_path_has_no_audit_ref(self, tmp_path):
        """AUDIT_DUAL_WRITE=0（回滚）→ 与 S2-02 之前逐字一致，且不建链式台账"""
        from agent.audit.logger import AuditLogger
        lg = AuditLogger(log_dir=str(tmp_path), dual_write=False)
        lg.log("act", "in", "out")
        rec = json.loads(lg._current_file.read_text(encoding="utf-8").strip())
        assert "audit_ref" not in rec
        assert set(rec) == {"timestamp", "trace_id", "action", "input_hash",
                            "output_hash", "stack_depth", "status", "metadata"}
        assert not os.path.exists(str(tmp_path / "audit_chain.db"))

    def test_env_rollback_switch(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIT_DUAL_WRITE", "0")
        from agent.audit.logger import AuditLogger
        lg = AuditLogger(log_dir=str(tmp_path))
        assert lg._dual_write is False
        lg.log("act")
        assert "audit_ref" not in lg._current_file.read_text(encoding="utf-8")

    def test_secret_in_metadata_not_in_chain(self, tmp_path):
        from agent.audit.logger import AuditLogger
        secret = "sk-test-LOGGER-LEAK-CHECK"
        lg = AuditLogger(log_dir=str(tmp_path))
        lg.log("act", metadata={"api_key": secret})
        lg.flush()
        raw = json.dumps([e.payload for e in lg.query_chain()], ensure_ascii=False)
        assert secret not in raw
        lg.close()

    def test_chain_track_verifies(self, tmp_path):
        from agent.audit.logger import AuditLogger
        lg = AuditLogger(log_dir=str(tmp_path))
        for i in range(3):
            lg.log(f"act{i}")
        v = lg.verify_chain()
        assert v is not None and v.ok is True
        lg.close()

    def test_query_reads_legacy_track(self, tmp_path):
        from agent.audit.logger import AuditLogger
        lg = AuditLogger(log_dir=str(tmp_path))
        lg.log("find.me", "in", "out")
        assert [r["action"] for r in lg.query(action="find.me")] == ["find.me"]
        lg.close()

    def test_cross_day_shard_followed(self, tmp_path, monkeypatch):
        """跨日分片：track 自动跟随新的按日文件"""
        from agent.audit.logger import AuditLogger
        lg = AuditLogger(log_dir=str(tmp_path))
        lg.log("act1")
        first = lg.track
        lg._current_file = tmp_path / "audit_20990101.jsonl"
        second = lg.track
        assert second is not first
        assert second.path.endswith("audit_20990101.jsonl")
        lg.close()
