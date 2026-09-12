"""TASK-S8-01 普查/选片/还原面单测（`agent/retention/scan.py` 与 `restorer.py` 的边角）。

普查层是"文档数字与归档选片共用同一套口径"的地方，它出错会让策略文档与真实磁盘对不上，
故除主路径外，**容错分支**（坏 JSON、缺表、超限截断、坏清单、越界类型）必须有用例。
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime

import pytest

from agent.retention.manifest import (
    ArchiveFormatError,
    ArchiveManifest,
    ArchivedFile,
    build_payload,
)
from agent.retention.policy import (
    ARCHIVE_NONE,
    KIND_JSONL,
    KIND_SQLITE,
    KIND_TREE,
    RetentionClass,
)
from agent.retention.restorer import (
    RestoreRefusedError,
    Restorer,
    cleanup_restore_dir,
)
from agent.retention.scan import (
    class_base,
    class_footprint,
    cold_files,
    count_lines,
    expand,
    file_bytes,
    file_day,
    file_records,
    file_time_range,
    footprint_totals,
    read_ts,
    scan_all,
    sqlite_consistent_backup,
    sqlite_row_count,
    sqlite_row_digest,
    sqlite_tables,
    warm_plan,
)

from retention_testkit import (       # noqa: E402
    drafts_class,
    events_class,
    fixed_clock,
    make_policy,
    make_root,
    make_sqlite,
    retention_class,
    touch_old,
    write_events,
)


def _write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# ════════════════════════════════════════════════════════════
#  1. 单文件度量与容错
# ════════════════════════════════════════════════════════════


def test_read_ts_tolerates_garbage():
    assert read_ts("") == ""
    assert read_ts("not json") == ""
    assert read_ts("[1,2]") == ""          # 非对象
    assert read_ts('{"a": 1}') == ""       # 无 ts
    assert read_ts('{"ts": "2026-01-01T00:00:00Z"}') == "2026-01-01T00:00:00Z"


def test_count_lines_skips_blank_lines(tmp_path):
    path = _write(str(tmp_path / "a.jsonl"), "{}\n\n{}\n   \n{}\n")
    assert count_lines(path) == 3
    assert count_lines(str(tmp_path / "missing.jsonl")) == 0


def test_file_time_range_reports_first_last_and_lines(tmp_path):
    path = _write(str(tmp_path / "a.jsonl"), "\n".join([
        json.dumps({"ts": "2026-01-02T00:00:00Z"}),
        "garbage",
        json.dumps({"ts": "2026-01-05T00:00:00Z"}),
    ]) + "\n")
    first, last, lines = file_time_range(path)
    assert (first, last, lines) == ("2026-01-02T00:00:00Z", "2026-01-05T00:00:00Z", 3)
    assert file_time_range(str(tmp_path / "nope.jsonl")) == ("", "", 0)


def test_file_time_range_truncates_huge_files(tmp_path):
    path = _write(str(tmp_path / "big.jsonl"),
                  "".join(json.dumps({"ts": f"2026-01-{i:02d}T00:00:00Z",
                                      "pad": "x" * 200}) + "\n"
                          for i in range(1, 20)))
    first, last, lines = file_time_range(path, max_bytes=300)
    assert first.startswith("2026-01-01")
    assert lines < 19, "超限时应如实截断而不是假装全量"


def test_file_bytes_missing_is_zero(tmp_path):
    assert file_bytes(str(tmp_path / "nope")) == 0


# ════════════════════════════════════════════════════════════
#  2. SQLite 度量与一致性备份
# ════════════════════════════════════════════════════════════


def test_sqlite_row_count_and_tables(tmp_path):
    db = make_sqlite(str(tmp_path), "db/x.db", table="unified_traces", rows=4)
    assert sqlite_tables(db) == ["unified_traces"]
    assert sqlite_row_count(db, "unified_traces") == 4
    assert sqlite_row_count(db) == 4                     # 表名缺省 → 全库
    assert sqlite_row_count(db, "no_such_table") == 0
    assert sqlite_row_count(str(tmp_path / "db" / "nope.db")) == 0
    assert sqlite_tables(str(tmp_path / "db" / "nope.db")) == []


def test_sqlite_row_digest_is_deterministic_and_change_sensitive(tmp_path):
    db = make_sqlite(str(tmp_path), "db/x.db", rows=3)
    first = sqlite_row_digest(db, "unified_traces")
    assert first and first == sqlite_row_digest(db, "unified_traces")
    assert sqlite_row_digest(db) == first               # 单表时全库口径一致
    conn = sqlite3.connect(db)
    conn.execute('INSERT INTO "unified_traces" (seq, name, n) VALUES (99, ?, ?)',
                 ("x", 1))
    conn.commit()
    conn.close()
    assert sqlite_row_digest(db, "unified_traces") != first
    assert sqlite_row_digest(str(tmp_path / "nope.db")) == ""


def test_sqlite_consistent_backup_produces_equal_rows(tmp_path):
    db = make_sqlite(str(tmp_path), "db/x.db", rows=5)
    dest = str(tmp_path / "copy" / "x.db")
    assert sqlite_consistent_backup(db, dest) is True
    assert sqlite_row_count(dest, "unified_traces") == 5
    assert sqlite_row_digest(dest, "unified_traces") == \
        sqlite_row_digest(db, "unified_traces")
    assert sqlite_consistent_backup(str(tmp_path / "nope.db"), dest) is False


def test_file_records_by_kind(tmp_path):
    db = make_sqlite(str(tmp_path), "db/x.db", rows=2)
    jl = _write(str(tmp_path / "a.jsonl"), "{}\n{}\n")
    assert file_records(RetentionClass("x", "t", KIND_SQLITE, ("db/x.db",),
                                       sqlite_table="unified_traces"), db) == 2
    assert file_records(RetentionClass("x", "t", KIND_JSONL, ("a.jsonl",)), jl) == 2
    assert file_records(RetentionClass("x", "t", KIND_TREE, ("a.jsonl",)), jl) == 1


# ════════════════════════════════════════════════════════════
#  3. 路径展开与类级普查
# ════════════════════════════════════════════════════════════


def test_expand_dedupes_and_skips_directories(tmp_path):
    root = make_root(str(tmp_path))
    _write(os.path.join(root, "data", "x", "b.jsonl"), "{}\n")
    _write(os.path.join(root, "data", "x", "a.jsonl"), "{}\n")
    os.makedirs(os.path.join(root, "data", "x", "sub.jsonl"), exist_ok=True)
    cls = retention_class("x", globs=("data/x/*.jsonl", "data/x/*.jsonl"))
    found = expand(cls, root)
    assert [os.path.basename(p) for p in found] == ["a.jsonl", "b.jsonl"]


def test_class_base_prefers_external_root(tmp_path):
    external = str(tmp_path / "outside")
    os.makedirs(external, exist_ok=True)
    cls = RetentionClass("m", "t", KIND_TREE, ("**/*.json",),
                         external_root=external)
    assert cls.external is True
    assert class_base(cls, "/somewhere/else") == os.path.abspath(external)


def test_class_footprint_covers_three_kinds(tmp_path):
    root = make_root(str(tmp_path))
    make_sqlite(root, "agent/data/tool_trace.db", rows=3)
    write_events(root, "2026-09-10", count=2)
    _write(os.path.join(root, "data", "digestion", "drafts", "d", "SKILL.md"), "# d\n")

    traces = class_footprint(RetentionClass(
        "unified_traces", "轨迹", KIND_SQLITE, ("agent/data/tool_trace.db",),
        sqlite_table="unified_traces"), root)
    assert traces["file_count"] == 1 and traces["records"] == 3
    assert traces["kind"] == KIND_SQLITE and traces["globs"]

    events = class_footprint(events_class(), root)
    assert events["file_count"] == 1 and events["records"] == 2
    assert events["first_ts"].startswith("2026-09-10")

    drafts = class_footprint(drafts_class(), root)
    assert drafts["file_count"] == 1 and drafts["records"] == 1
    assert drafts["can_purge"] is True


def test_class_footprint_external_and_missing(tmp_path):
    external = str(tmp_path / "vault")
    os.makedirs(os.path.join(external, "20260101"), exist_ok=True)
    _write(os.path.join(external, "20260101", "snap.json"), "{}")
    cls = RetentionClass("memory_snapshots", "快照", KIND_TREE, ("**/*.json",),
                         external_root=external)
    fp = class_footprint(cls, str(tmp_path / "irrelevant"))
    assert fp["external"] is True and fp["file_count"] == 1

    missing = class_footprint(retention_class("gone", globs=("data/gone/*.jsonl",)),
                              str(tmp_path))
    assert missing["file_count"] == 0 and missing["records"] == 0
    assert missing["exists"] is False


def test_scan_all_and_totals_follow_policy_order(tmp_path):
    root = make_root(str(tmp_path))
    write_events(root, "2026-09-10", count=1)
    policy = make_policy(root, [events_class(), drafts_class()])
    rows = scan_all(policy, root)
    assert [r["class_id"] for r in rows] == ["events", "digestion_drafts"]
    totals = footprint_totals(rows)
    assert totals["classes"] == 2 and totals["classes_present"] == 1
    assert totals["classes_missing"] == 1
    assert totals["files"] == 1
    assert totals["redline_classes"] == []
    assert totals["deletable_classes"] == ["digestion_drafts"]


# ════════════════════════════════════════════════════════════
#  4. 归属日与分层选片
# ════════════════════════════════════════════════════════════


def test_file_day_prefers_filename_then_mtime(tmp_path):
    assert file_day(str(tmp_path / "events-2026-09-01.jsonl")) == "2026-09-01"
    assert file_day(str(tmp_path / "decisions.2026-08-10.jsonl")) == "2026-08-10"
    assert file_day(str(tmp_path / "audit_20260810.jsonl")) == "2026-08-10"
    # 文件名无日期 → 退回 mtime
    plain = _write(str(tmp_path / "plain.jsonl"), "{}\n")
    touch_old(plain, "2026-02-03")
    assert file_day(plain) == "2026-02-03"
    # 都不行 → 空串（保守：不冷）
    assert file_day(str(tmp_path / "does_not_exist.jsonl")) == ""


def test_cold_files_splits_by_threshold(tmp_path):
    root = make_root(str(tmp_path))
    old = _write(os.path.join(root, "data", "x", "old.jsonl"), "{}\n")
    new = _write(os.path.join(root, "data", "x", "new.jsonl"), "{}\n")
    touch_old(old, "2026-01-01")
    touch_old(new, "2026-09-13")
    cls = retention_class("x", globs=("data/x/*.jsonl",), cold_days=30)
    cold, hot = cold_files(cls, root, now=datetime(2026, 9, 13))
    assert cold == [os.path.abspath(old)]
    assert hot == [os.path.abspath(new)]


def test_warm_plan_reports_reasons_for_every_refusal(tmp_path):
    root = make_root(str(tmp_path))
    write_events(root, "2026-09-10", count=2)

    not_aware = warm_plan(events_class(reader_shard_aware=False), root,
                          now=datetime(2026, 9, 13))
    assert not_aware["enabled"] is False and "非分片感知" in not_aware["reason"]

    off = warm_plan(events_class(warm_days=None), root, now=datetime(2026, 9, 13))
    assert off["enabled"] is False and off["reason"]

    noarchive = warm_plan(events_class(warm_days=0, archive_mode=ARCHIVE_NONE),
                          root, now=datetime(2026, 9, 13))
    assert noarchive["enabled"] is False

    windowed = warm_plan(events_class(warm_days=7), root, now=datetime(2026, 9, 13))
    assert windowed["enabled"] is False
    assert "log_archiver" in windowed["reason"]


def test_warm_plan_counts_history_lines_and_skips_shards(tmp_path):
    root = make_root(str(tmp_path))
    write_events(root, "2026-09-10", count=3)
    write_events(root, "2026-09-13", count=2)      # 今日行留在活动文件
    _write(os.path.join(root, "data", "events", "events-2026-09-01.jsonl"), "{}\n")

    plan = warm_plan(events_class(), root, now=datetime(2026, 9, 13))
    assert plan["enabled"] is True
    assert plan["lines"] == 3
    assert plan["buckets"] == {"2026-09-10": 3}
    assert plan["bytes"] > 0
    assert all("events-2026-09-01" not in p for p in plan["targets"]), \
        "已是分片的文件不应再进温层"


# ════════════════════════════════════════════════════════════
#  5. 载荷构造与清单边角
# ════════════════════════════════════════════════════════════


def test_build_payload_offsets_are_contiguous(tmp_path):
    root = make_root(str(tmp_path))
    a = _write(os.path.join(root, "data", "x", "a.jsonl"), '{"ts":"2026-01-01T00:00:00Z"}\n')
    b = _write(os.path.join(root, "data", "x", "b.jsonl"), "{}\n{}\n")
    payload, entries = build_payload([a, b], base_root=root, kind=KIND_JSONL)
    assert sum(e.bytes for e in entries) == len(payload)
    assert entries[0].offset == 0
    assert entries[1].offset == entries[0].bytes
    assert entries[0].rel_path == "data/x/a.jsonl"
    assert entries[1].line_count == 2
    assert entries[0].records == 1


def test_manifest_validate_rejects_broken_shapes(tmp_path):
    base = ArchiveManifest(class_id="c", archive_file=str(tmp_path / "p.gz"),
                           files=[ArchivedFile(path="a", offset=0, bytes=4,
                                               sha256="x")])
    base.validate()                                    # 正常形状可通过

    bad_offset = ArchiveManifest(
        class_id="c", archive_file="p", files=[
            ArchivedFile(path="a", offset=0, bytes=4, sha256="x"),
            ArchivedFile(path="b", offset=9, bytes=1, sha256="y")])
    with pytest.raises(ArchiveFormatError):
        bad_offset.validate()

    no_hash = ArchiveManifest(class_id="c", archive_file="p",
                              files=[ArchivedFile(path="a", offset=0, bytes=1)])
    with pytest.raises(ArchiveFormatError):
        no_hash.validate()

    with pytest.raises(ArchiveFormatError):
        ArchiveManifest(archive_file="p").validate()        # 缺 class_id
    with pytest.raises(ArchiveFormatError):
        ArchiveManifest(class_id="c").validate()            # 缺 archive_file

    mismatch = ArchiveManifest(class_id="c", archive_file="p", payload_bytes=99,
                               files=[ArchivedFile(path="a", offset=0, bytes=1,
                                                   sha256="x")])
    with pytest.raises(ArchiveFormatError):
        mismatch.validate()

    count = ArchiveManifest(class_id="c", archive_file="p", file_count=5,
                            files=[ArchivedFile(path="a", offset=0, bytes=1,
                                                sha256="x")])
    with pytest.raises(ArchiveFormatError):
        count.validate()


def test_manifest_read_rejects_missing_and_unparseable(tmp_path):
    with pytest.raises(ArchiveFormatError):
        ArchiveManifest.read(str(tmp_path / "nope.json"))
    bad = _write(str(tmp_path / "bad.json"), "{not json")
    with pytest.raises(ArchiveFormatError):
        ArchiveManifest.read(bad)
    notobj = _write(str(tmp_path / "arr.json"), "[1,2]")
    with pytest.raises(ArchiveFormatError):
        ArchiveManifest.read(notobj)


def test_manifest_read_rejects_unknown_codec(tmp_path):
    manifest = ArchiveManifest(class_id="c", archive_file=str(tmp_path / "p.gz"))
    path = manifest.write(str(tmp_path / "m.json"))
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    raw["codec"] = "bzip9"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(raw, fh)
    with pytest.raises(ArchiveFormatError):
        ArchiveManifest.read(path)


def test_manifest_roundtrip_through_json(tmp_path):
    # 【测试卫生】必须显式给清单路径：`write()` 缺省会落到 `archive_file + .manifest.json`
    # 的**当前工作目录**（S8-01 实测在仓库根留下过 `p.gz.manifest.json`）
    manifest = ArchiveManifest(class_id="c", archive_file="p.gz", period="2026-01-01",
                               files=[ArchivedFile(path="a", bytes=2, sha256="x")])
    path = manifest.write(str(tmp_path / "m.manifest.json"))
    assert path == str(tmp_path / "m.manifest.json")
    restored = ArchiveManifest.read(path)
    assert restored.files[0].path == "a"
    assert restored.payload_bytes == manifest.payload_bytes
    assert "time_range" in restored.to_dict()
    assert restored.summary()
    assert not os.path.exists("p.gz.manifest.json"), "清单不得写到工作目录"


# ════════════════════════════════════════════════════════════
#  6. 还原器边角
# ════════════════════════════════════════════════════════════


def _archive(tmp_path, root: str) -> str:
    from agent.retention.archiver import Archiver

    draft = _write(os.path.join(root, "data", "digestion", "drafts", "d", "SKILL.md"),
                   "# a\n")
    touch_old(draft, "2026-01-01")          # 冷层按"归属日"选片
    policy = make_policy(root, [drafts_class()])
    report = Archiver(policy, root=root, clock=fixed_clock(), audit=False,
                      emit_events=False).run(confirm=True)
    return report.classes[0].archives[0]["archive_file"]


def test_resolve_accepts_manifest_or_pack_path(tmp_path):
    root = make_root(str(tmp_path))
    archive = _archive(tmp_path, root)
    tool = Restorer(make_policy(root, [drafts_class()]), root=root)
    assert tool.resolve(archive)[0] == archive + ".manifest.json"
    assert tool.resolve(archive + ".manifest.json")[0] == archive + ".manifest.json"


def test_resolve_reports_missing_and_unaccompanied_pack(tmp_path):
    root = make_root(str(tmp_path))
    tool = Restorer(make_policy(root, [drafts_class()]), root=root)
    with pytest.raises(ArchiveFormatError):
        tool.resolve(str(tmp_path / "nope.gz"))
    lone = str(tmp_path / "lone.gz")
    with open(lone, "wb") as fh:
        fh.write(b"x")
    with pytest.raises(ArchiveFormatError):
        tool.resolve(lone)


def test_list_archives_reports_broken_manifests(tmp_path):
    root = make_root(str(tmp_path))
    archive = _archive(tmp_path, root)
    bad = os.path.join(os.path.dirname(archive), "broken.manifest.json")
    _write(bad, "{not json")
    rows = Restorer(make_policy(root, [drafts_class()]), root=root).list_archives(
        "digestion_drafts")
    assert rows, "应至少列出刚生成的归档件"
    assert any(r.get("verified") is True for r in rows)
    assert any("error" in r for r in rows)
    assert Restorer(make_policy(root, [drafts_class()]), root=root
                    ).list_archives("no_such_class") == []


def test_verify_only_does_not_restore(tmp_path):
    root = make_root(str(tmp_path))
    archive = _archive(tmp_path, root)
    tool = Restorer(make_policy(root, [drafts_class()]), root=root)
    info = tool.verify_only(archive)
    assert info["archive_verified"] is True and info["payload_verified"] is True
    assert info["record_count"] == 1 and info["sources"]


def test_restore_with_only_filter_that_matches_nothing(tmp_path):
    root = make_root(str(tmp_path))
    archive = _archive(tmp_path, root)
    tool = Restorer(make_policy(root, [drafts_class()]), root=root)
    report = tool.restore(archive, only=["no/such/file.md"])
    try:
        assert report.files == []
        assert report.notes and report.ok is False
    finally:
        cleanup_restore_dir(report)


def test_restore_requires_target_and_cleans_only_temporary(tmp_path):
    root = make_root(str(tmp_path))
    archive = _archive(tmp_path, root)
    tool = Restorer(make_policy(root, [drafts_class()]), root=root)

    temporary = tool.restore(archive)
    assert temporary.temporary is True and os.path.isdir(temporary.target_dir)
    assert cleanup_restore_dir(temporary) is True
    assert not os.path.exists(temporary.target_dir)

    explicit = tool.restore(archive, str(tmp_path / "explicit"))
    assert explicit.temporary is False
    assert cleanup_restore_dir(explicit) is False, "用户指定的目录绝不能被清掉"
    assert os.path.isdir(explicit.target_dir)


def test_restore_reports_refusal_for_missing_archive(tmp_path):
    root = make_root(str(tmp_path))
    tool = Restorer(make_policy(root, [drafts_class()]), root=root)
    with pytest.raises(ArchiveFormatError):
        tool.restore(str(tmp_path / "nope.gz"))


def test_restore_refuses_when_payload_is_corrupt(tmp_path):
    root = make_root(str(tmp_path))
    archive = _archive(tmp_path, root)
    manifest = ArchiveManifest.read(archive + ".manifest.json")
    # 载荷被替换成"能解压但内容不符"的流
    from agent.retention.manifest import compress

    with open(archive, "wb") as fh:
        fh.write(compress(b"\x00" * manifest.payload_bytes))
    tool = Restorer(make_policy(root, [drafts_class()]), root=root)
    with pytest.raises(RestoreRefusedError):
        tool.restore(archive)
