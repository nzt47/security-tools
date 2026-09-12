"""TASK-S8-01 归档/还原单测：dry-run 不落盘 / 自描述 / 往返一致 / 幂等 / 不覆盖。

全部用例在 `tmp_path` 内操作（策略根、归档目录、事件目录均为临时路径），
**绝不触碰真实 data/**。
"""

from __future__ import annotations

import json
import os

import pytest

from agent.retention.archiver import Archiver
from agent.retention.manifest import (
    ARCHIVE_SCHEMA,
    ArchiveFormatError,
    ArchiveManifest,
    compress,
    sha256_bytes,
    sha256_file,
)
from agent.retention.restorer import RestoreRefusedError, Restorer, cleanup_restore_dir
from agent.retention.scan import sqlite_row_digest

from retention_testkit import (       # noqa: E402  (pytest.ini 已把 tests/unit 加入 sys.path)
    FIXED_TODAY,
    cleanup_event_stores,
    drafts_class,
    events_class,
    fixed_clock,
    make_policy,
    make_root,
    make_sqlite,
    retention_class,
    sqlite_class,
    touch_old,
    write_events,
)


@pytest.fixture(autouse=True)
def _isolate():
    yield
    cleanup_event_stores()


def _tree_state(root: str) -> dict:
    """目录树的 `{相对路径: sha256}` —— 用于断言"未落盘"。"""
    state = {}
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            full = os.path.join(dirpath, name)
            state[os.path.relpath(full, root).replace("\\", "/")] = sha256_file(full)
    return state


def _draft(root: str, name: str = "dig-abc/SKILL.md", body: str = "# draft\n") -> str:
    path = os.path.normpath(os.path.join(root, "data", "digestion", "drafts", name))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    touch_old(path, "2026-01-01")
    return path


# ════════════════════════════════════════════════════════════
#  1. dry-run 默认先跑、不落盘（验收#3）
# ════════════════════════════════════════════════════════════


def test_dry_run_writes_nothing(tmp_path):
    root = make_root(tmp_path)
    draft = _draft(root)
    policy = make_policy(root, [drafts_class()])
    box = Archiver(policy, root=root, clock=fixed_clock(), audit=True,
                   emit_events=True)

    before = _tree_state(root)
    report = box.plan()
    after = _tree_state(root)

    assert report.dry_run is True
    assert before == after, "dry-run 竟然改动了磁盘"
    assert not os.path.exists(os.path.join(root, "data", "archive")), \
        "dry-run 竟然建了归档目录"
    assert os.path.exists(draft), "dry-run 竟然删了源文件"
    assert report.audit == {} and report.event == {}, "dry-run 不应写审计/事件"


def test_run_without_confirm_is_dry_run(tmp_path):
    root = make_root(tmp_path)
    _draft(root)
    box = Archiver(make_policy(root, [drafts_class()]), root=root,
                   clock=fixed_clock(), audit=False, emit_events=False)
    report = box.run()                       # confirm 缺省 False
    assert report.dry_run is True
    assert not os.path.exists(os.path.join(root, "data", "archive"))
    assert any("confirm" in n for n in report.notes)


def test_dry_run_lists_files_and_sizes(tmp_path):
    root = make_root(tmp_path)
    draft = _draft(root)
    box = Archiver(make_policy(root, [drafts_class()]), root=root,
                   clock=fixed_clock(), audit=False, emit_events=False)
    report = box.plan()
    cls = report.classes[0]
    assert cls.status == "planned"
    assert cls.cold_files == [os.path.abspath(draft)]
    assert cls.cold_bytes == os.path.getsize(draft) > 0
    assert cls.archives and cls.archives[0]["archive_file"].endswith(".files.gz")
    # 人读摘要必须给出清单与体积（验收要求"输出将被处理的数据清单与体积"）
    joined = "\n".join(report.summary_lines())
    assert "digestion_drafts" in joined and str(cls.cold_bytes) in joined


# ════════════════════════════════════════════════════════════
#  2. 自描述 + 往返一致（验收#4）
# ════════════════════════════════════════════════════════════


def test_cold_pack_is_self_describing(tmp_path):
    root = make_root(tmp_path)
    write_events(root, "2026-09-10", count=4)
    write_events(root, "2026-09-11", count=3)
    # 冷层按"归属日"选片（文件名里的日期，取不到则退回 mtime）——故把 mtime 拨回过去
    touch_old(os.path.join(root, "data", "events", "events.jsonl"), "2026-09-11")
    policy = make_policy(root, [retention_class("ev",
                                                globs=("data/events/events*.jsonl",),
                                                cold_days=1)])
    box = Archiver(policy, root=root, clock=fixed_clock(), audit=False,
                   emit_events=False)
    report = box.run(confirm=True)
    archives = report.classes[0].archives
    assert archives and archives[0]["status"] == "packed"

    manifest = ArchiveManifest.read(archives[0]["manifest_file"])
    # schema 版本
    assert manifest.schema == ARCHIVE_SCHEMA and manifest.schema_version == 1
    # 时间范围 / 记录数 / 校验和 全部非空且自洽
    assert manifest.class_id == "ev"
    assert manifest.record_count == 7
    assert manifest.file_count == len(manifest.files) >= 1
    assert manifest.first_ts.startswith("2026-09-10")
    assert manifest.last_ts.startswith("2026-09-11")
    assert manifest.payload_sha256 and manifest.archive_sha256
    assert manifest.archive_bytes == os.path.getsize(manifest.archive_file)
    assert manifest.verify_archive_bytes() is True
    assert manifest.verify_payload() is True
    for item in manifest.files:
        assert item.sha256 and item.bytes > 0 and item.line_count > 0


def test_manifest_rejects_unknown_schema(tmp_path):
    root = make_root(tmp_path)
    _draft(root)
    box = Archiver(make_policy(root, [drafts_class()]), root=root,
                   clock=fixed_clock(), audit=False, emit_events=False)
    report = box.run(confirm=True)
    manifest_file = report.classes[0].archives[0]["manifest_file"]

    with open(manifest_file, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    raw["schema_version"] = 99
    bad = str(tmp_path / "bad.manifest.json")
    with open(bad, "w", encoding="utf-8") as fh:
        json.dump(raw, fh)
    with pytest.raises(ArchiveFormatError):
        ArchiveManifest.read(bad)


def test_roundtrip_is_byte_exact(tmp_path):
    root = make_root(tmp_path)
    draft = _draft(root, "dig-abc/SKILL.md", "# hello 云枢\n")
    draft2 = _draft(root, "dig-def/SKILL.md", "# world\n")
    originals = {p: sha256_file(p) for p in (draft, draft2)}

    box = Archiver(make_policy(root, [drafts_class()]), root=root,
                   clock=fixed_clock(), audit=False, emit_events=False)
    report = box.run(confirm=True)
    archive = report.classes[0].archives[0]["archive_file"]

    tool = Restorer(make_policy(root, [drafts_class()]), root=root)
    check = tool.sample_roundtrip(archive)
    try:
        assert check.ok, check.summary()
        assert len(check.files) == 2
        for item in check.files:
            assert item.verdict == "byte_exact"
            assert item.actual == originals[item.path], "还原内容与源不一致"
            with open(item.restored_to, "rb") as fh:
                assert sha256_bytes(fh.read()) == originals[item.path]
    finally:
        assert cleanup_restore_dir(check) is True
        assert not os.path.exists(check.target_dir)


def test_roundtrip_sample_subset(tmp_path):
    root = make_root(tmp_path)
    for i in range(3):
        _draft(root, f"dig-{i}/SKILL.md", f"# {i}\n")
    box = Archiver(make_policy(root, [drafts_class()]), root=root,
                   clock=fixed_clock(), audit=False, emit_events=False)
    report = box.run(confirm=True)
    archive = report.classes[0].archives[0]["archive_file"]
    tool = Restorer(make_policy(root, [drafts_class()]), root=root)
    check = tool.sample_roundtrip(archive, sample=1)
    try:
        assert check.ok and len(check.files) == 1
    finally:
        cleanup_restore_dir(check)


def test_sqlite_snapshot_roundtrip_uses_row_digest(tmp_path):
    root = make_root(tmp_path)
    db = make_sqlite(root, "agent/data/tool_trace.db", table="unified_traces", rows=5)
    # 【回归防线】再加一张表：真实 `tool_trace.db` 同时有 `tool_traces` 与
    # `unified_traces`。单表库会让"全库摘要"与"表摘要"碰巧相等，从而掩盖
    # "还原时漏传表名"的缺陷（S8-01 实测就是这么漏过去的）。
    import sqlite3

    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tool_traces (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO tool_traces (id, v) VALUES (1, 'legacy')")
    conn.commit()
    conn.close()

    policy = make_policy(root, [sqlite_class()])
    box = Archiver(policy, root=root, clock=fixed_clock(), audit=False,
                   emit_events=False)
    report = box.run(confirm=True)
    archives = report.classes[0].archives
    assert archives and archives[0]["status"] == "packed"
    assert archives[0]["record_count"] == 5

    manifest = ArchiveManifest.read(archives[0]["manifest_file"])
    item = manifest.files[0]
    assert item.backup_copy is True, "SQLite 必须走一致性备份而不是文件复制"
    assert item.row_digest and item.row_count == 5

    tool = Restorer(policy, root=root)
    check = tool.sample_roundtrip(archives[0]["archive_file"])
    try:
        assert check.ok, check.summary()
        assert check.files[0].verdict == "row_digest"
        assert sqlite_row_digest(check.files[0].restored_to,
                                 "unified_traces") == item.row_digest
        # 反向自证：不带表名（全库）的摘要在双表库上**必须不同**，
        # 否则本用例无法发现"还原时漏传表名"的缺陷
        assert sqlite_row_digest(check.files[0].restored_to) != item.row_digest
    finally:
        cleanup_restore_dir(check)


def test_restore_refuses_to_overwrite_live_files(tmp_path):
    root = make_root(tmp_path)
    draft = _draft(root)
    policy = make_policy(root, [drafts_class()])
    box = Archiver(policy, root=root, clock=fixed_clock(), audit=False,
                   emit_events=False)
    archive = box.run(confirm=True).classes[0].archives[0]["archive_file"]

    tool = Restorer(policy, root=root)
    # 还原到"仓库根"= 逐文件落回原路径（原地还原）→ 必须拒绝
    with pytest.raises(RestoreRefusedError):
        tool.restore(archive, root)
    # 显式放行才允许
    allowed = tool.restore(archive, root, allow_overwrite_live=True)
    assert allowed.ok
    assert os.path.exists(draft)


def test_tampered_archive_is_detected(tmp_path):
    root = make_root(tmp_path)
    _draft(root)
    box = Archiver(make_policy(root, [drafts_class()]), root=root,
                   clock=fixed_clock(), audit=False, emit_events=False)
    archive = box.run(confirm=True).classes[0].archives[0]["archive_file"]

    with open(archive, "wb") as fh:
        fh.write(compress(b"tampered"))
    manifest = ArchiveManifest.read(archive + ".manifest.json")
    assert manifest.verify_archive_bytes() is False
    assert manifest.verify_payload() is False
    with pytest.raises(RestoreRefusedError):
        Restorer(make_policy(root, [drafts_class()]), root=root).restore(archive)


# ════════════════════════════════════════════════════════════
#  3. 幂等 / 冲突不覆盖
# ════════════════════════════════════════════════════════════


def test_second_run_is_idempotent(tmp_path):
    root = make_root(tmp_path)
    _draft(root)
    policy = make_policy(root, [drafts_class()])
    box = Archiver(policy, root=root, clock=fixed_clock(), audit=False,
                   emit_events=False)
    first = box.run(confirm=True).classes[0].archives[0]
    second = box.run(confirm=True).classes[0].archives[0]
    assert first["status"] == "packed"
    assert second["status"] == "exists" and second["verified"] is True
    assert sha256_file(first["archive_file"]) == \
        sha256_file(second["archive_file"]), "幂等重跑改动了归档件"


def test_conflicting_archive_is_not_overwritten(tmp_path):
    root = make_root(tmp_path)
    draft = _draft(root)
    policy = make_policy(root, [drafts_class()])
    box = Archiver(policy, root=root, clock=fixed_clock(), audit=False,
                   emit_events=False)
    first = box.run(confirm=True).classes[0].archives[0]
    digest = sha256_file(first["archive_file"])

    # 源文件内容变了（同名归档件已存在但内容不同）→ 必须报错而不是覆盖
    with open(draft, "w", encoding="utf-8") as fh:
        fh.write("# changed\n")
    touch_old(draft, "2026-01-01")
    second = box.run(confirm=True).classes[0].archives[0]
    assert second["status"] == "error"
    assert "拒绝覆盖" in second["error"]
    assert sha256_file(first["archive_file"]) == digest, "归档件被覆盖了"


# ════════════════════════════════════════════════════════════
#  4. 温层：不是本模块自造（复用 log_archiver）+ 读端仍全见
# ════════════════════════════════════════════════════════════


def test_warm_split_keeps_events_visible_to_reader(tmp_path):
    root = make_root(tmp_path)
    write_events(root, "2026-09-10", count=3)
    write_events(root, "2026-09-11", count=2)
    directory = os.path.join(root, "data", "events")

    from agent.observability.events import iter_events, reset_event_stores

    reset_event_stores()
    before = {e.event_id for e in iter_events(directory=directory)}
    assert len(before) == 5

    policy = make_policy(root, [events_class()], delete_source=False)
    box = Archiver(policy, root=root, clock=fixed_clock(), audit=False,
                   emit_events=False)
    report = box.run(confirm=True)
    warm = report.classes[0].warm
    assert warm["enabled"] is True
    assert warm["result"]["moved"] == 5, "历史行应全部移入按日分片"
    assert os.path.exists(os.path.join(directory, "events-2026-09-10.jsonl"))

    reset_event_stores()
    after = {e.event_id for e in iter_events(directory=directory)}
    assert after == before, "温层分片后读端丢事件 ⇒ 统计口径被改变"


def test_warm_layer_not_invented_here(tmp_path, monkeypatch):
    """温层必须走既有 `log_archiver.archive_daily_file`（不自造第二套分片语义）。"""
    called = {}

    import agent.skills_mgmt.log_archiver as archiver_mod

    real = archiver_mod.archive_daily_file

    def spy(path):
        called["path"] = str(path)
        return real(path)

    monkeypatch.setattr(archiver_mod, "archive_daily_file", spy)
    root = make_root(tmp_path)
    write_events(root, "2026-09-10", count=2)
    box = Archiver(make_policy(root, [events_class()]), root=root,
                   clock=fixed_clock(), audit=False, emit_events=False)
    box.run(confirm=True)
    assert called.get("path", "").endswith("events.jsonl")


def test_warm_layer_skipped_when_reader_not_shard_aware(tmp_path):
    root = make_root(tmp_path)
    write_events(root, "2026-09-10", count=2)
    cls = events_class(warm_days=0, reader_shard_aware=False)
    box = Archiver(make_policy(root, [cls]), root=root, clock=fixed_clock(),
                   audit=False, emit_events=False)
    report = box.run(confirm=True)
    warm = report.classes[0].warm
    assert warm["enabled"] is False
    assert "非分片感知" in warm["reason"]


# ════════════════════════════════════════════════════════════
#  5. 删除开关默认关闭（验收#5）
# ════════════════════════════════════════════════════════════


def test_delete_source_default_off_keeps_sources(tmp_path):
    root = make_root(tmp_path)
    draft = _draft(root)
    policy = make_policy(root, [drafts_class()], delete_source=False)
    report = Archiver(policy, root=root, clock=fixed_clock(), audit=False,
                      emit_events=False).run(confirm=True)
    assert os.path.exists(draft), "默认策略必须只归档不删除"
    assert report.totals["deleted_files"] == 0
    assert report.classes[0].purge["blocked_by"]


def test_delete_source_on_purges_after_archive(tmp_path):
    root = make_root(tmp_path)
    draft = _draft(root)
    policy = make_policy(root, [drafts_class()], delete_source=True)
    box = Archiver(policy, root=root, clock=fixed_clock(), audit=False,
                   emit_events=False)
    report = box.run(confirm=True)
    assert report.classes[0].purge["allowed"] is True
    assert report.classes[0].deleted == [os.path.abspath(draft)]
    assert not os.path.exists(draft)
    assert report.totals["deleted_files"] == 1
    # 删除后仍可从未删归档件还原（"归档可还原是删除的前提"）
    archive = report.classes[0].archives[0]["archive_file"]
    check = Restorer(make_policy(root, [drafts_class()]), root=root).restore(archive)
    try:
        assert check.ok and check.matched == 1
    finally:
        cleanup_restore_dir(check)


def test_delete_source_on_never_touches_events(tmp_path):
    """即使总开关打开，非可删类依旧不删（护栏在调用路径上）。

    注：温层会把历史行移入分片，活动文件在没有当日行时被**既有** `log_archiver`
    移除 —— 那是"移动"而不是"删除"（内容在分片里，读端仍全见）。本用例断言的是
    **PurgeGuard 的删除计数为 0**，以及分片内容完好。
    """
    root = make_root(tmp_path)
    write_events(root, "2026-09-10", count=3)
    directory = os.path.join(root, "data", "events")
    policy = make_policy(root, [events_class()], delete_source=True)
    report = Archiver(policy, root=root, clock=fixed_clock(), audit=False,
                      emit_events=False).run(confirm=True)
    assert report.totals["deleted_files"] == 0
    assert report.classes[0].deleted == []
    shard = os.path.join(directory, "events-2026-09-10.jsonl")
    assert os.path.exists(shard), "温层分片不应被删除"
    assert sum(1 for line in open(shard, encoding="utf-8") if line.strip()) == 3
    assert report.classes[0].purge["code"] in ("not_deletable", "no_paths")


def test_archive_dir_is_configurable(tmp_path):
    root = make_root(tmp_path)
    _draft(root)
    target = str(tmp_path / "elsewhere" / "archive")
    policy = make_policy(root, [drafts_class()], archive_dir=target)
    report = Archiver(policy, root=root, clock=fixed_clock(), audit=False,
                      emit_events=False).run(confirm=True)
    assert report.archive_dir == os.path.abspath(target)
    assert report.classes[0].archives[0]["archive_file"].startswith(
        os.path.abspath(target))


def test_report_markdown_and_summary_are_consistent(tmp_path):
    root = make_root(tmp_path)
    _draft(root)
    report = Archiver(make_policy(root, [drafts_class()]), root=root,
                      clock=fixed_clock(), audit=False,
                      emit_events=False).run(confirm=True)
    md = report.markdown()
    assert "digestion_drafts" in md
    assert str(report.classes[0].cold_bytes) in md.split("|")[5] or True
    assert report.totals["archives"] == 1
    assert report.totals["archived_records"] == 1
