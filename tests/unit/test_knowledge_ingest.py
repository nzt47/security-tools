"""素材层 Ingest 管道单元测试（任务1）。

验收线（不易）：
- raw/inbox 内源文件字节不变（hash 前后一致）
- 同一文件重复 ingest 不产生重复 log 行（幂等）
- 敏感素材 meta 标记 sensitive=true，且不阻断入库
- 并发 ingest 10 个文件无日志损坏/丢失
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent.knowledge import ingest as ingest_module
from agent.knowledge.ingest import (
    LOG_MARKER,
    IngestError,
    KnowledgeWatcher,
    _append_log_line,
    _sha256_file,
    get_knowledge_root,
    ingest_file,
    list_inbox,
    list_layer,
    list_raw,
    main,
)

LOG_LINE_RE = re.compile(r"^## \[\d{4}-\d{2}-\d{2}\] ingest \| (.+) \| (.+)$")


@pytest.fixture
def kb(tmp_path):
    """独立的 knowledge 根目录（不触碰仓库真实 knowledge/）。"""
    return tmp_path / "kb"


def _src(tmp_path, name="note.md", content=b"# thought\n\ncontent\n"):
    p = tmp_path / name
    p.write_bytes(content)
    return p


def _read_log(root):
    p = root / "log.md"
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8")


def _ingest_lines(root):
    return [l for l in _read_log(root).splitlines() if l.startswith("## [")]


def _count_ingest(root, slug, source_type="unknown"):
    """log.md 中匹配 <slug> 的 ingest 行数（子串计数，兼容全行/前缀差异）。"""
    return sum(1 for l in _ingest_lines(root) if f"| {slug} | {source_type}" in l)


# ════════════════════════════════════════════════════════════
#  复制只读性（【不易】验收线）
# ════════════════════════════════════════════════════════════

def test_ingest_copy_preserves_bytes_source_unchanged(tmp_path, kb):
    src = _src(tmp_path, "article.md", b"raw bytes \x00\xff\x01 unchanged")
    before = src.read_bytes()
    digest_before = _sha256_file(src)

    result = ingest_file(str(src), dest_layer="raw", source_type="article", knowledge_root=str(kb))

    # 源文件字节不变
    assert src.read_bytes() == before
    assert _sha256_file(src) == digest_before
    # 入库文件字节与源一致（复制非移动）
    dest = kb / "raw" / "article.md"
    assert dest.read_bytes() == before
    assert _sha256_file(dest) == digest_before
    assert result.sha256 == digest_before


def test_ingest_is_copy_not_move(tmp_path, kb):
    src = _src(tmp_path, "clip.md")
    ingest_file(str(src), dest_layer="inbox", source_type="clip", knowledge_root=str(kb))
    assert src.exists(), "入库必须复制而非移动，源文件应保留"


def test_ingest_hash_verify_failure(tmp_path, kb, monkeypatch):
    """复制后 hash 不一致（只读性校验失败）→ 抛 IngestError。"""
    src = _src(tmp_path, "note.md")
    import agent.knowledge.ingest as ingest_mod

    real_copy = ingest_mod.shutil.copy2

    def _corrupt_copy(src_, dest_):
        real_copy(src_, dest_)
        with open(dest_, "ab") as f:
            f.write(b"\x00corrupt")

    monkeypatch.setattr(ingest_mod.shutil, "copy2", _corrupt_copy)
    with pytest.raises(IngestError):
        ingest_file(str(src), dest_layer="inbox", knowledge_root=str(kb))


# ════════════════════════════════════════════════════════════
#  meta 生成
# ════════════════════════════════════════════════════════════

def test_meta_generated_fields(tmp_path, kb):
    src = _src(tmp_path, "meeting.md")
    result = ingest_file(str(src), dest_layer="inbox", source_type="transcript", knowledge_root=str(kb))

    meta_path = kb / "inbox" / "meeting.md.meta.json"
    assert result.meta_path == str(meta_path)
    assert meta_path.is_file()

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["source_path"] == str(src.resolve())
    assert meta["source_type"] == "transcript"
    assert meta["captured_at"]
    assert meta["sha256"] == _sha256_file(src)
    assert meta["sensitive"] is False
    assert meta["sensitive_patterns"] == []
    assert meta["layer"] == "inbox"
    assert meta["filename"] == "meeting.md"
    assert meta["slug"] == "meeting"


# ════════════════════════════════════════════════════════════
#  log.md 追加（顶部 + 幂等）
# ════════════════════════════════════════════════════════════

def test_log_line_inserted_after_marker(tmp_path, kb):
    log = kb / "log.md"
    log.parent.mkdir(parents=True)
    log.write_text("# 操作时间线日志\n\n<!-- 新记录追加到此行下方（顶部） -->",
                   encoding="utf-8", newline="")

    ingest_file(str(_src(tmp_path, "a.md")), dest_layer="inbox", source_type="article",
                knowledge_root=str(kb))
    ingest_file(str(_src(tmp_path, "b.md")), dest_layer="inbox", source_type="article",
                knowledge_root=str(kb))

    text = _read_log(kb)
    # 最新记录紧跟标记行（顶部），旧记录在其下方
    assert f"{LOG_MARKER}\n## [" in text
    assert "ingest | b | article" in text
    assert "ingest | a | article" in text
    assert text.index("ingest | b") < text.index("ingest | a")
    assert text.count("ingest |") == 2


def test_same_file_ingest_twice_idempotent(tmp_path, kb):
    src = _src(tmp_path, "note.md", b"same content")
    r1 = ingest_file(str(src), dest_layer="inbox", source_type="thought", knowledge_root=str(kb))
    r2 = ingest_file(str(src), dest_layer="inbox", source_type="thought", knowledge_root=str(kb))

    assert r1.idempotent is False
    assert r2.idempotent is True
    assert r2.log_appended is False
    assert r2.meta_written is False
    # 无重复 log 行、无重复副本
    assert _count_ingest(kb, "note", "thought") == 1
    assert len(list((kb / "inbox").glob("note.md"))) == 1


def test_log_line_direct_idempotency(tmp_path, kb):
    log = kb / "log.md"
    log.parent.mkdir(parents=True)
    line = "## [2026-08-06] ingest | note | thought"
    assert _append_log_line(log, line) is True
    assert _append_log_line(log, line) is False
    assert _read_log(kb).count("ingest | note | thought") == 1


class _BoomLock:
    """进入即抛异常的假文件锁：若判重短路未命中（仍进锁路径）测试即失败。"""

    def __enter__(self):
        raise AssertionError("短路未命中：不该进入文件锁路径")

    def __exit__(self, *exc):
        return False


def test_log_shortcut_skips_lock_on_repeat(tmp_path, kb, monkeypatch):
    """P0-1.1 判重短路：同路径同记录重复写入时跳过「锁 + 全量读改写」（O(1)）。"""
    log = kb / "log.md"
    log.parent.mkdir(parents=True)
    line = "## [2026-08-06] ingest | shortcut | thought"
    assert _append_log_line(log, line) is True

    # 第二次调用应命中进程内短路，不再进入 _FileLock（进入即抛异常）
    monkeypatch.setattr(ingest_module, "_FileLock", _BoomLock)
    assert _append_log_line(log, line) is False
    assert _read_log(kb).count("ingest | shortcut | thought") == 1


def test_unknown_source_type_log_detail(tmp_path, kb):
    ingest_file(str(_src(tmp_path, "u.md")), dest_layer="inbox", knowledge_root=str(kb))
    lines = _ingest_lines(kb)
    assert len(lines) == 1
    assert lines[0].endswith("| u | unknown")


# ════════════════════════════════════════════════════════════
#  并发入库（无日志损坏/丢失）
# ════════════════════════════════════════════════════════════

def test_concurrent_ingest_10_files_no_log_loss(tmp_path, kb):
    srcs = []
    for i in range(10):
        p = tmp_path / f"doc-{i}.md"
        p.write_bytes(f"文档 {i} 内容\n".encode("utf-8") * 20)
        srcs.append(p)

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(
            lambda p: ingest_file(str(p), dest_layer="inbox", knowledge_root=str(kb)),
            srcs,
        ))

    assert len(results) == 10
    assert all(r.log_appended for r in results)

    dest_files = sorted(p.name for p in (kb / "inbox").iterdir()
                        if not p.name.endswith(".meta.json") and not p.name.startswith("."))
    assert len(dest_files) == 10

    metas = sorted(p.name for p in (kb / "inbox").iterdir() if p.name.endswith(".meta.json"))
    assert len(metas) == 10

    lines = _ingest_lines(kb)
    assert len(lines) == 10
    assert len(set(lines)) == 10
    for l in lines:
        assert LOG_LINE_RE.match(l), f"log 行损坏: {l!r}"


# ════════════════════════════════════════════════════════════
#  敏感信息标记（不阻断）
# ════════════════════════════════════════════════════════════

def test_sensitive_marked_but_not_blocked(tmp_path, kb):
    src = _src(tmp_path, "contact.md", "联系人手机号：13812345678，请及时联系。\n".encode("utf-8"))
    result = ingest_file(str(src), dest_layer="inbox", source_type="thought", knowledge_root=str(kb))

    assert result.sensitive is True
    assert "phone_cn" in result.sensitive_patterns
    # 素材仍入库、内容原样保留
    assert (kb / "inbox" / "contact.md").read_bytes() == src.read_bytes()

    meta = json.loads((kb / "inbox" / "contact.md.meta.json").read_text(encoding="utf-8"))
    assert meta["sensitive"] is True
    assert "phone_cn" in meta["sensitive_patterns"]


def test_non_sensitive_no_mark(tmp_path, kb):
    src = _src(tmp_path, "plain.md", "今天天气很好，适合散步。\n".encode("utf-8"))
    result = ingest_file(str(src), dest_layer="inbox", knowledge_root=str(kb))
    assert result.sensitive is False
    assert result.sensitive_patterns == []


# ════════════════════════════════════════════════════════════
#  list_inbox / list_raw
# ════════════════════════════════════════════════════════════

def test_list_inbox_and_raw(tmp_path, kb):
    ingest_file(str(_src(tmp_path, "one.md", b"one")), dest_layer="inbox", source_type="clip", knowledge_root=str(kb))
    ingest_file(str(_src(tmp_path, "two.md", b"two")), dest_layer="inbox", knowledge_root=str(kb))
    ingest_file(str(_src(tmp_path, "three.md", b"three")), dest_layer="raw", source_type="article", knowledge_root=str(kb))

    inbox = list_inbox(str(kb))
    raw = list_raw(str(kb))
    assert [e["filename"] for e in inbox] == ["one.md", "two.md"]
    assert [e["filename"] for e in raw] == ["three.md"]
    # meta 文件自身不出现
    assert all(not e["filename"].endswith(".meta.json") for e in inbox + raw)
    # 关键摘要字段可回溯
    assert inbox[0]["source_type"] == "clip"
    assert inbox[0]["sensitive"] is False
    assert inbox[0]["has_meta"] is True
    assert inbox[0]["sha256"] == _sha256_file(kb / "inbox" / "one.md")
    # 未登记文件（无 meta）也能列出
    orphan = kb / "inbox" / "orphan.md"
    orphan.write_text("no meta", encoding="utf-8")
    names = [e["filename"] for e in list_layer("inbox", str(kb))]
    assert "orphan.md" in names
    assert any(e["has_meta"] is False for e in list_layer("inbox", str(kb)))


# ════════════════════════════════════════════════════════════
#  异常与边界
# ════════════════════════════════════════════════════════════

def test_invalid_layer_raises(tmp_path, kb):
    with pytest.raises(ValueError, match="非法目标层"):
        ingest_file(str(_src(tmp_path, "x.md")), dest_layer="processed", knowledge_root=str(kb))


def test_missing_source_raises(tmp_path, kb):
    with pytest.raises(FileNotFoundError):
        ingest_file(str(tmp_path / "nope.md"), dest_layer="inbox", knowledge_root=str(kb))


def test_ingest_meta_file_rejected(tmp_path, kb):
    meta_file = tmp_path / "x.md.meta.json"
    meta_file.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="元数据"):
        ingest_file(str(meta_file), dest_layer="inbox", knowledge_root=str(kb))


def test_name_collision_dedup_no_overwrite(tmp_path, kb):
    a = tmp_path / "src-a" / "note.md"
    a.parent.mkdir(parents=True)
    a.write_bytes(b"version A")
    b = tmp_path / "src-b" / "note.md"  # 同名不同内容（来自不同源）
    b.parent.mkdir(parents=True)
    b.write_bytes(b"version B")
    r1 = ingest_file(str(a), dest_layer="inbox", knowledge_root=str(kb))
    r2 = ingest_file(str(b), dest_layer="inbox", knowledge_root=str(kb))

    assert r1.dest_path.endswith("note.md")
    assert r2.dest_path.endswith("note-2.md")
    # 既有素材不被改写（不易）
    assert (kb / "inbox" / "note.md").read_bytes() == b"version A"
    assert (kb / "inbox" / "note-2.md").read_bytes() == b"version B"
    assert len(_ingest_lines(kb)) == 2


def test_get_knowledge_root_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(tmp_path))
    assert get_knowledge_root() == tmp_path
    # 显式参数优先于环境变量
    assert get_knowledge_root(str(tmp_path / "other")) == tmp_path / "other"


# ════════════════════════════════════════════════════════════
#  文件监听（KnowledgeWatcher）
# ════════════════════════════════════════════════════════════

def test_watcher_handler_registers_inbox_file(tmp_path, kb):
    watcher = KnowledgeWatcher(str(kb))
    inbox_file = kb / "inbox" / "drop.md"
    inbox_file.parent.mkdir(parents=True, exist_ok=True)
    inbox_file.write_text("直接落入 inbox 的素材", encoding="utf-8")

    r1 = watcher.handle_path(str(inbox_file))
    assert r1 is not None
    assert r1.log_appended is True
    assert (kb / "inbox" / "drop.md.meta.json").is_file()

    # 再次登记 → 幂等，log 不重复
    r2 = watcher.handle_path(str(inbox_file))
    assert r2.idempotent is True
    assert _count_ingest(kb, "drop") == 1


def test_watcher_ignores_meta_tmp_hidden_missing(tmp_path, kb):
    watcher = KnowledgeWatcher(str(kb))
    meta = kb / "inbox" / "x.md.meta.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text("{}", encoding="utf-8")
    tmp = kb / "inbox" / "x.tmp"
    tmp.write_text("t", encoding="utf-8")
    hidden = kb / "inbox" / ".gitkeep"
    hidden.write_text("", encoding="utf-8")

    assert watcher.handle_path(str(meta)) is None
    assert watcher.handle_path(str(tmp)) is None
    assert watcher.handle_path(str(hidden)) is None
    assert watcher.handle_path(str(kb / "inbox" / "missing.md")) is None
    assert watcher.handle_path(str(kb / "outside.md")) is None  # 不在监听层内


def test_watcher_on_event_filter(tmp_path, kb):
    """回调层只处理 created 文件事件。"""
    from sensor.sensor_reading import Category, SensorReading

    watcher = KnowledgeWatcher(str(kb))
    handled = []
    watcher.on_ingest = lambda r: handled.append(r)

    # 非 created（modified）→ 忽略
    watcher._on_event(SensorReading(
        "file_modified", str(kb / "inbox" / "m.md"), "",
        "", Category.FILE, None,
        {"event_type": "modified", "is_directory": False, "src_path": str(kb / "inbox" / "m.md")},
    ))
    assert handled == []

    # created 文件 → 登记
    created = kb / "inbox" / "created.md"
    created.parent.mkdir(parents=True, exist_ok=True)
    created.write_text("hello", encoding="utf-8")
    watcher._on_event(SensorReading(
        "file_created", str(created), "", "", Category.FILE, None,
        {"event_type": "created", "is_directory": False, "src_path": str(created)},
    ))
    assert len(handled) == 1
    assert _count_ingest(kb, "created") == 1


def test_watcher_start_stop_real(tmp_path, kb):
    """真实 FileWatcher 集成：文件落入 inbox → 自动登记 log.md。"""
    watcher = KnowledgeWatcher(str(kb))
    watcher.start()
    try:
        assert watcher.is_running
        drop = kb / "inbox" / "auto.md"
        drop.parent.mkdir(parents=True, exist_ok=True)
        drop.write_text("auto registered", encoding="utf-8")

        deadline = 8.0
        while "ingest | auto |" not in _read_log(kb):
            assert deadline > 0, "监听 8s 内未自动登记 log.md"
            import time
            time.sleep(0.2)
            deadline -= 0.2
        assert (kb / "inbox" / "auto.md.meta.json").is_file()
    finally:
        watcher.stop()
    assert not watcher.is_running


# ════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════

def test_main_cli_ingest(tmp_path, kb, monkeypatch, capsys):
    src = _src(tmp_path, "cli.md", b"cli content")
    monkeypatch.setattr("sys.argv", ["agent.knowledge.ingest", str(src),
                                     "--layer", "inbox", "--source-type", "clip",
                                     "--root", str(kb)])
    assert main() == 0
    out = capsys.readouterr().out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["slug"] == "cli"
    assert payload["source_type"] == "clip"
    assert payload["sha256"] == _sha256_file(src)
    assert (kb / "inbox" / "cli.md").is_file()


def test_main_cli_failure_missing_file(tmp_path, kb, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["agent.knowledge.ingest", str(tmp_path / "gone.md"),
                                     "--root", str(kb)])
    assert main() == 1
    err = capsys.readouterr().err
    assert "失败" in err


def test_main_cli_list(tmp_path, kb, monkeypatch, capsys):
    ingest_file(str(_src(tmp_path, "listed.md")), dest_layer="inbox", source_type="article",
                knowledge_root=str(kb))
    monkeypatch.setattr("sys.argv", ["agent.knowledge.ingest", "--list", "--root", str(kb)])
    assert main() == 0
    out = capsys.readouterr().out
    assert "== inbox ==" in out
    assert "listed.md" in out


def test_main_cli_no_args_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["agent.knowledge.ingest"])
    with pytest.raises(SystemExit):
        main()


# ════════════════════════════════════════════════════════════
#  写盘稳定探测（created 竞态加固）
# ════════════════════════════════════════════════════════════

def test_wait_file_stable_stable_file(tmp_path, kb):
    """已写完的文件立即判定稳定。"""
    watcher = KnowledgeWatcher(str(kb))
    f = kb / "inbox" / "stable.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("content", encoding="utf-8")
    assert watcher._wait_file_stable(str(f), interval=0.05) is True


def test_wait_file_stable_missing_file(tmp_path, kb):
    """缺失文件立即返回 False（不等待超时）。"""
    watcher = KnowledgeWatcher(str(kb))
    missing = kb / "inbox" / "nope.md"
    assert watcher._wait_file_stable(str(missing), interval=0.05) is False


def test_wait_file_stable_growing_file(tmp_path, kb):
    """写入中的文件：大小变化期间不判定稳定，稳定后返回 True。"""
    watcher = KnowledgeWatcher(str(kb))
    f = kb / "inbox" / "growing.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("a", encoding="utf-8")

    import threading

    def _grow():
        time.sleep(0.2)
        f.write_text("a" * 1024, encoding="utf-8")

    t = threading.Thread(target=_grow)
    t.start()
    # 探测期间文件仍在增长 → 应在第二次增长后判定稳定
    assert watcher._wait_file_stable(str(f), interval=0.1, timeout=5.0) is True
    t.join()

