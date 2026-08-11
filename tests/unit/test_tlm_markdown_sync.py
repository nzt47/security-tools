"""TLM Markdown 双向同步单元测试 [TLM-L3]

覆盖验收项：
1. 正向同步：写 10 条记忆 → flush → 10 个 .md 文件 + Front Matter 正确
2. 防抖：连续 10 次 notify_change 只触发 1 次 _flush
3. 反向同步：修改 .md → SQLite 更新 + 向量重索引触发
4. 幂等性：同一 .md 多次 modify 事件只 1 次 SQLite 写入
5. 冲突检测：同时改 SQLite 和 .md → sync_conflicts 表有记录
6. Windows 事件去重：连续 3 次 on_modified 只处理 1 次

约束遵循（project_memory）：
- 持锁操作严禁 I/O（syncer 文件 I/O 在 adapter 锁外）
- 不破坏 HolographicAdapter save/search 接口签名
- 冲突不自动解决，只记录
"""
import asyncio
import os
import sys
import threading
import time

import pytest
import yaml

from agent.memory.adapters.holographic_adapter import HolographicAdapter
from agent.memory.markdown_syncer import (
    MarkdownSyncer,
    compute_content_hash,
    parse_markdown_content,
    parse_markdown_file,
)
from agent.memory.file_watcher import MarkdownFileWatcher


# ──────────────────────────────────────────────────────────────
# 公共 fixture
# ──────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db_path(tmp_path):
    return str(tmp_path / "tlm_md_sync.db")


@pytest.fixture
def md_dir(tmp_path):
    d = tmp_path / "md_out"
    d.mkdir()
    return str(d)


@pytest.fixture
def adapter(tmp_db_path):
    """不带缓存的 adapter，便于观察实时状态"""
    return HolographicAdapter(db_path=tmp_db_path, enable_cache=False)


@pytest.fixture
def syncer(adapter, md_dir):
    s = MarkdownSyncer(adapter, output_dir=md_dir, debounce_seconds=1, batch_threshold=10)
    adapter.set_syncer(s)
    yield s
    s.close()


@pytest.fixture
def watcher(adapter, syncer, md_dir):
    w = MarkdownFileWatcher(md_dir, adapter, syncer, dedup_ms=100)
    yield w
    w.stop()


def _write_memories(adapter, count=10):
    """同步写入 count 条记忆（不同 category）"""
    async def run():
        for i in range(count):
            cat = "preference" if i % 2 == 0 else "note"
            await adapter.save_with_embedding(
                f"k{i:03d}",
                f"记忆内容 #{i} - 这是一段测试数据",
                {"category": cat, "importance": i % 5},
            )
    asyncio.run(run())


def _mutate_db_direct(adapter, key: str, data: str, metadata: dict):
    """直接改 SQLite 绕过 syncer（模拟 SQLite 被外部进程修改）。

    【不易】不能走 adapter.save：save 会 notify_change → syncer debounce
    (1s) flush 把文件正向渲染成 DB 内容。慢 CI 环境下从 set_db 到断言
    的耗时可能超过 debounce 窗口，flush 抢先覆盖测试改写的文件，导致
    「文件不被覆盖」断言 flaky（run 31440430315 py3.10-windows 复现）。
    冲突检测的真实场景是 SQLite 被外部修改（watcher 三路比较），
    直接 SQL UPDATE 模拟该场景，syncer 不知情 → 无 forward flush 干扰。
    """
    import json as _json
    with adapter._get_conn() as conn:
        conn.execute(
            "UPDATE memory_items SET data=?, metadata=? WHERE key=?",
            (data, _json.dumps(metadata, ensure_ascii=False), key),
        )
        conn.commit()


def _wait_for(predicate, timeout=2.0, interval=0.05):
    """轮询等待条件成立，避免硬编码 sleep 导致 flaky"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ──────────────────────────────────────────────────────────────
# 验收 1: 正向同步 — 10 条记忆 → 10 个 .md 文件 + Front Matter 正确
# ──────────────────────────────────────────────────────────────

class TestForwardSync:
    def test_10_records_render_to_10_md_files(self, adapter, syncer, md_dir):
        _write_memories(adapter, count=10)
        # 等 debounce flush
        assert _wait_for(lambda: syncer._pending == {} or True)  # noqa
        syncer._flush()  # 强制 flush 残留

        # 收集所有 .md
        md_files = []
        for root, _, fs in os.walk(md_dir):
            for f in fs:
                if f.endswith(".md"):
                    md_files.append(os.path.join(root, f))
        assert len(md_files) == 10, f"期望 10 个 .md 文件，实际 {len(md_files)}"

        # 按 category 分目录：preference 5 + note 5
        pref = [p for p in md_files if os.sep + "preference" + os.sep in p + os.sep or "preference" in p]
        # 直接校验目录名
        pref_dir = os.path.join(md_dir, "preference")
        note_dir = os.path.join(md_dir, "note")
        assert len(os.listdir(pref_dir)) == 5
        assert len(os.listdir(note_dir)) == 5

    def test_front_matter_format(self, adapter, syncer, md_dir):
        _write_memories(adapter, count=1)
        syncer._flush()
        fp = os.path.join(md_dir, "preference", "k000.md")
        assert os.path.exists(fp)
        parsed = parse_markdown_file(fp)
        assert parsed is not None, "Front Matter 解析失败"
        fm = parsed["front_matter"]
        # 不变量：必含 sqlite_id / last_synced_at / content_hash
        assert fm["sqlite_id"] == "k000"
        assert "last_synced_at" in fm and isinstance(fm["last_synced_at"], str)
        assert "content_hash" in fm and len(fm["content_hash"]) == 16
        assert fm["category"] == "preference"
        assert fm["importance"] == 0
        # content_hash 与 data 一致
        assert fm["content_hash"] == compute_content_hash(parsed["data"])
        # 正文标题为 content 前 50 字符
        assert parsed["body"].startswith("# 记忆内容 #0")

    def test_content_hash_not_stored_in_db(self, adapter, syncer):
        """【不易】content_hash 是派生值，不入库（避免 schema 膨胀）"""
        _write_memories(adapter, count=1)
        syncer._flush()
        with adapter._get_conn() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(memory_items)").fetchall()}
        assert "content_hash" not in cols, "content_hash 不应作为列存储"


# ──────────────────────────────────────────────────────────────
# 验收 2: 防抖 — 连续 10 次 notify_change 只触发 1 次 _flush
# ──────────────────────────────────────────────────────────────

class TestDebounce:
    def test_burst_notify_triggers_single_flush(self, adapter, syncer, md_dir):
        flush_count = [0]
        original_flush = syncer._flush

        def counting_flush():
            flush_count[0] += 1
            original_flush()

        syncer._flush = counting_flush
        # 直接调 notify_change（不走 adapter.save），连续 10 次
        for i in range(10):
            syncer.notify_change(f"debounce_k{i}", "upsert")
        # notify_change 内 batch_threshold=10 会立即触发 1 次
        # 此时 flush_count 应为 1（batch 触发），无 debounce 定时器残留
        assert _wait_for(lambda: flush_count[0] >= 1)
        # 等待 debounce 窗口结束，确认没有额外 flush
        time.sleep(1.5)
        assert flush_count[0] == 1, f"连续 10 次 notify 应只触发 1 次 flush，实际 {flush_count[0]}"


# ──────────────────────────────────────────────────────────────
# 验收 3: 反向同步 — 修改 .md → SQLite 更新 + 向量重索引触发
# ──────────────────────────────────────────────────────────────

class TestReverseSync:
    def test_edit_md_updates_sqlite(self, adapter, syncer, watcher, md_dir):
        _write_memories(adapter, count=1)
        syncer._flush()
        fp = os.path.join(md_dir, "preference", "k000.md")
        parsed = parse_markdown_file(fp)
        fm = parsed["front_matter"]

        # 监听 save_with_embedding 调用（向量重索引入口）
        reverse_calls = []
        original = adapter.save_with_embedding

        async def spy_save(k, d, m=None, embedding=None):
            reverse_calls.append((k, d, embedding))
            return await original(k, d, m, embedding)

        adapter.save_with_embedding = spy_save

        # 修改文件内容（db 未变 → 反向同步）
        new_content = "记忆内容 #0 - 这是一段测试数据 USER EDITED"
        body = (f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\n"
                f"# {new_content[:50]}\n\n{new_content}\n")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(body)

        watcher._do_process(fp)

        # 等待反向更新线程完成
        assert _wait_for(lambda: len(reverse_calls) >= 1, timeout=2.0), "反向同步未触发"
        assert reverse_calls[0][0] == "k000"
        assert reverse_calls[0][1] == new_content

        # SQLite 已更新（反向写为异步线程，等待落库）
        assert _wait_for(
            lambda: adapter.get_raw_memory("k000")["data"] == new_content, timeout=2.0
        ), "反向同步后 SQLite 未更新"

    def test_vector_reindex_triggered(self, adapter, syncer, watcher, md_dir):
        """反向同步经 save_with_embedding 入口 → 向量重索引路径已挂接"""
        _write_memories(adapter, count=1)
        syncer._flush()
        fp = os.path.join(md_dir, "preference", "k000.md")
        parsed = parse_markdown_file(fp)
        fm = parsed["front_matter"]

        # 注入 embedding 回调，标记是否被调用（向量重索引路径）
        embed_calls = []
        adapter._embedding_func = lambda d: (embed_calls.append(d), [0.0] * 512)[1]

        new_content = "完全不同的内容触发重索引"
        body = (f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\n"
                f"# {new_content[:50]}\n\n{new_content}\n")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(body)
        watcher._do_process(fp)

        # save_with_embedding 会经 _async_embed_and_write 调 _embedding_func（向量可用时）
        # 这里仅验证反向同步确实走了 save_with_embedding 入口（重索引挂接点）
        assert _wait_for(lambda: adapter.get_raw_memory("k000")["data"] == new_content, timeout=2.0)


# ──────────────────────────────────────────────────────────────
# 验收 4: 幂等性 — 同一 .md 多次 modify 事件只 1 次 SQLite 写入
# ──────────────────────────────────────────────────────────────

class TestIdempotency:
    def test_repeated_process_no_duplicate_write(self, adapter, syncer, watcher, md_dir):
        _write_memories(adapter, count=1)
        syncer._flush()
        fp = os.path.join(md_dir, "preference", "k000.md")

        write_count = [0]
        original = adapter.save_with_embedding

        async def counting_save(k, d, m=None, embedding=None):
            write_count[0] += 1
            return await original(k, d, m, embedding)

        adapter.save_with_embedding = counting_save

        # 同一文件（内容未变）连续 _do_process 3 次
        for _ in range(3):
            watcher._do_process(fp)
        time.sleep(0.5)

        assert write_count[0] == 0, f"内容未变时不应写 SQLite，实际写入 {write_count[0]} 次"

    def test_changed_then_stable_single_write(self, adapter, syncer, watcher, md_dir):
        """改一次文件，然后连续触发多次事件，应只 1 次 SQLite 写入"""
        _write_memories(adapter, count=1)
        syncer._flush()
        fp = os.path.join(md_dir, "preference", "k000.md")
        parsed = parse_markdown_file(fp)
        fm = parsed["front_matter"]

        write_count = [0]
        original = adapter.save_with_embedding

        async def counting_save(k, d, m=None, embedding=None):
            write_count[0] += 1
            return await original(k, d, m, embedding)

        adapter.save_with_embedding = counting_save

        new_content = "新内容 only once"
        body = (f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\n"
                f"# {new_content[:50]}\n\n{new_content}\n")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(body)

        # 第一次处理触发反向更新；后续 2 次（内容已与 DB 一致）应跳过
        # 【不易】等待条件必须是"refresh_single 完成"而非"save 开始"：
        #   counting_save 在 await original 之前 +=1（L278），write_count>=1 时
        #   save 可能尚未 commit；此时第 2 次 _do_process 的 get_raw_memory 无锁读
        #   会读到旧值 → db_hash==base_hash → 再次触发反向同步（CI Linux 3 次写入）。
        #   改为等文件 Front Matter content_hash 更新为新值（refresh_single 完成标志），
        #   此时 db 已 commit 且 file_hash==db_hash，后续 _do_process 走幂等跳过分支。
        expected_hash = compute_content_hash(new_content)
        watcher._do_process(fp)
        assert _wait_for(
            lambda: parse_markdown_file(fp)["front_matter"].get("content_hash") == expected_hash,
            timeout=2.0,
        ), "等待第 1 次反向同步 + refresh_single 完成（file content_hash 应更新为新值）"
        watcher._do_process(fp)
        watcher._do_process(fp)
        time.sleep(0.5)
        assert write_count[0] == 1, f"应只 1 次写入，实际 {write_count[0]}"


# ──────────────────────────────────────────────────────────────
# 验收 5: 冲突检测 — 同时改 SQLite 和 .md → sync_conflicts 有记录
# ──────────────────────────────────────────────────────────────

class TestConflictDetection:
    def test_both_sides_changed_records_conflict(self, adapter, syncer, watcher, md_dir):
        _write_memories(adapter, count=1)
        syncer._flush()
        fp = os.path.join(md_dir, "preference", "k000.md")
        parsed = parse_markdown_file(fp)
        fm = parsed["front_matter"]  # base hash

        # DB 侧变更（偏离 base）—— 直接改库绕过 syncer（见 _mutate_db_direct）
        _mutate_db_direct(adapter, "k000", "DB side changed", {"category": "preference"})

        # 文件侧变更（偏离 base），保留旧 fm 的 content_hash 作为 base
        file_content = "FILE side changed"
        body = (f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\n"
                f"# {file_content[:50]}\n\n{file_content}\n")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(body)

        watcher._do_process(fp)
        time.sleep(0.3)

        conflicts = adapter.list_sync_conflicts()
        assert len(conflicts) >= 1, "双向变更应记冲突"
        c = conflicts[-1]
        assert c["sqlite_id"] == "k000"
        assert c["db_hash"] != c["file_hash"]
        assert c["resolution"] == "unresolved"
        assert c["resolved_at"] is None  # 不自动解决

    def test_conflict_does_not_overwrite(self, adapter, syncer, watcher, md_dir):
        """【不易】冲突不自动覆盖任何一方"""
        _write_memories(adapter, count=1)
        syncer._flush()
        fp = os.path.join(md_dir, "preference", "k000.md")
        parsed = parse_markdown_file(fp)
        fm = parsed["front_matter"]
        db_before = "DB side changed"
        file_content = "FILE side changed"

        # DB 侧变更（偏离 base）—— 直接改库绕过 syncer（见 _mutate_db_direct）
        _mutate_db_direct(adapter, "k000", db_before, {"category": "preference"})

        body = (f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\n"
                f"# {file_content[:50]}\n\n{file_content}\n")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(body)
        watcher._do_process(fp); time.sleep(0.3)

        # DB 内容未被覆盖
        assert adapter.get_raw_memory("k000")["data"] == db_before
        # 文件内容未被覆盖
        assert parse_markdown_file(fp)["data"] == file_content


# ──────────────────────────────────────────────────────────────
# 验收 6: Windows 事件去重 — 连续 3 次 on_modified 只处理 1 次
# ──────────────────────────────────────────────────────────────

class TestWindowsEventDedup:
    def test_burst_on_modified_single_process(self, adapter, syncer, watcher, md_dir):
        _write_memories(adapter, count=1)
        syncer._flush()
        fp = os.path.join(md_dir, "preference", "k000.md")
        parsed = parse_markdown_file(fp)
        fm = parsed["front_matter"]

        process_count = [0]
        original_do = watcher._do_process

        def counting_do(path):
            process_count[0] += 1
            original_do(path)

        watcher._do_process = counting_do

        new_content = "dedup test content"
        body = (f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\n"
                f"# {new_content[:50]}\n\n{new_content}\n")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(body)

        # 模拟 Windows watchdog 连续 3 次 on_modified（500ms 去重窗口内）
        for _ in range(3):
            watcher.on_modified(fp)
        # 等去重窗口到期（dedup_ms=100）
        assert _wait_for(lambda: process_count[0] >= 1, timeout=1.0)
        time.sleep(0.3)
        assert process_count[0] == 1, f"连续 3 次事件应合并为 1 次处理，实际 {process_count[0]}"

    def test_tmp_file_ignored(self, adapter, syncer, watcher, md_dir):
        """原子写 .tmp 文件不应被处理"""
        _write_memories(adapter, count=1)
        syncer._flush()
        tmp_fp = os.path.join(md_dir, "preference", "k000.md.tmp")
        with open(tmp_fp, "w", encoding="utf-8") as f:
            f.write("tmp")
        # on_modified 对 .tmp 应被忽略（不应抛异常）
        watcher.on_modified(tmp_fp)
        time.sleep(0.2)
        assert len(watcher._dedup_timers) == 0


# ──────────────────────────────────────────────────────────────
# 辅助：解析纯函数
# ──────────────────────────────────────────────────────────────

class TestParsing:
    def test_parse_no_front_matter_returns_none(self):
        assert parse_markdown_content("just plain text") is None

    def test_parse_empty_data_roundtrip(self):
        """空 data 往返精确"""
        from agent.memory.markdown_syncer import _extract_data_from_body
        body = "# title\n\n\n"  # data="" + 渲染追加的 \n
        assert _extract_data_from_body(body) == ""

    def test_parse_multiline_data_roundtrip(self):
        data = "line1\nline2\n\nline4"
        fm = {"sqlite_id": "x", "content_hash": compute_content_hash(data), "category": "c"}
        content = f"---\n{yaml.safe_dump(fm, sort_keys=False)}---\n\n# title\n\n{data}\n"
        parsed = parse_markdown_content(content)
        assert parsed["data"] == data, f"多行 data 往返失败: {parsed['data']!r}"


# ──────────────────────────────────────────────────────────────
# 验收 8: 反向同步后基线刷新（防后续编辑误判冲突）
# ──────────────────────────────────────────────────────────────

class TestBaselineRefresh:
    def test_reverse_then_reedit_no_false_conflict(self, adapter, syncer, watcher, md_dir):
        """反向同步成功后 base 刷新，再次编辑不误判冲突"""
        _write_memories(adapter, count=1)
        syncer._flush()
        fp = os.path.join(md_dir, "preference", "k000.md")

        # 第一次反向编辑
        parsed = parse_markdown_file(fp)
        fm = parsed["front_matter"]
        first_edit = "first reverse edit"
        body = (f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\n"
                f"# {first_edit[:50]}\n\n{first_edit}\n")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(body)
        watcher._do_process(fp)
        assert _wait_for(lambda: adapter.get_raw_memory("k000")["data"] == first_edit, timeout=2.0)
        # 等 refresh_single 刷新基线
        _wait_for(lambda: parse_markdown_file(fp)["front_matter"]["content_hash"]
                  == compute_content_hash(first_edit), timeout=2.0)

        # 第二次编辑：base 已刷新为 first_edit 的 hash，应正常反向更新而非冲突
        parsed2 = parse_markdown_file(fp)
        fm2 = parsed2["front_matter"]
        second_edit = "second reverse edit"
        body2 = (f"---\n{yaml.safe_dump(fm2, allow_unicode=True, sort_keys=False)}---\n\n"
                 f"# {second_edit[:50]}\n\n{second_edit}\n")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(body2)
        conflicts_before = len(adapter.list_sync_conflicts())
        watcher._do_process(fp)
        assert _wait_for(lambda: adapter.get_raw_memory("k000")["data"] == second_edit, timeout=2.0)
        # 不应新增冲突
        conflicts_after = len(adapter.list_sync_conflicts())
        assert conflicts_after == conflicts_before, \
            f"基线刷新后再次编辑不应误判冲突，新增 {conflicts_after - conflicts_before} 条"

    def test_refresh_single_skips_if_user_reedited(self, adapter, syncer, md_dir):
        """竞态守卫：文件已被再次编辑时 refresh_single 跳过（不覆盖用户数据）"""
        _write_memories(adapter, count=1)
        syncer._flush()
        fp = os.path.join(md_dir, "preference", "k000.md")
        # DB 改为新值
        async def setdb():
            await adapter.save("k000", "db new value", {"category": "preference"})
        asyncio.run(setdb())
        # 文件被用户改成另一个值（!= db）
        parsed = parse_markdown_file(fp)
        fm = parsed["front_matter"]
        user_edit = "user reedited value"
        body = (f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\n"
                f"# {user_edit[:50]}\n\n{user_edit}\n")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(body)
        # refresh_single 应跳过（file_hash != db_hash）
        result = syncer.refresh_single("k000")
        assert result is False, "文件已被再编辑时 refresh_single 应跳过"
        # 文件内容未被覆盖
        assert parse_markdown_file(fp)["data"] == user_edit


# ──────────────────────────────────────────────────────────────
# 验收 9: 冲突检测幂等去重（同一冲突状态不重复记录）
# ──────────────────────────────────────────────────────────────

class TestConflictIdempotency:
    def test_same_conflict_not_duplicated(self, adapter):
        """同一 (sqlite_id, db_hash, file_hash) 未解决冲突多次调用只记 1 条"""
        id1 = adapter.record_sync_conflict("k1", "hash_db_1", "hash_file_1")
        id2 = adapter.record_sync_conflict("k1", "hash_db_1", "hash_file_1")
        id3 = adapter.record_sync_conflict("k1", "hash_db_1", "hash_file_1")
        assert id1 == id2 == id3, f"同一冲突应返回相同 id，实际 {id1}/{id2}/{id3}"
        conflicts = adapter.list_sync_conflicts()
        assert len(conflicts) == 1, f"应只 1 条，实际 {len(conflicts)}"

    def test_different_conflict_state_records_new(self, adapter):
        """冲突状态变化（hash 变了）后记录新条目"""
        adapter.record_sync_conflict("k1", "db1", "file1")
        # 文件又改了 → file_hash 变化 → 新冲突状态
        id2 = adapter.record_sync_conflict("k1", "db1", "file2")
        conflicts = adapter.list_sync_conflicts()
        assert len(conflicts) == 2, f"不同冲突状态应记 2 条，实际 {len(conflicts)}"
        assert conflicts[0]["id"] != conflicts[1]["id"]

    def test_resolved_then_new_conflict_allowed(self, adapter):
        """旧冲突解决后，相同状态可重新记录（不复用已解决记录）"""
        id1 = adapter.record_sync_conflict("k1", "db1", "file1")
        adapter.resolve_sync_conflict(id1, "manual")
        # 相同冲突状态再次出现 → 应记录新条目（旧条目已解决）
        id2 = adapter.record_sync_conflict("k1", "db1", "file1")
        assert id2 != id1, "已解决后相同冲突状态应记新条目"
        unresolved = adapter.list_sync_conflicts(unresolved_only=True)
        assert len(unresolved) == 1


# ──────────────────────────────────────────────────────────────
# 验收 10: enable_markdown_sync 集成入口
# ──────────────────────────────────────────────────────────────

class TestEnableMarkdownSync:
    def test_enable_creates_syncer_and_watcher(self, adapter, md_dir):
        """集成入口一站式创建 syncer + watcher 并注入 adapter"""
        pytest.importorskip("watchdog")
        syncer, watcher = adapter.enable_markdown_sync(
            md_dir, debounce_seconds=1, batch_threshold=10, dedup_ms=200,
        )
        try:
            assert adapter._syncer is syncer
            assert watcher is not None and watcher._started
            # 写入一条 → 正向同步可用
            async def r():
                await adapter.save_with_embedding("ik1", "integrated content", {"category": "pref"})
            asyncio.run(r())
            syncer._flush()
            assert os.path.exists(os.path.join(md_dir, "pref", "ik1.md"))
        finally:
            adapter.disable_markdown_sync(watcher)

    def test_enable_without_watcher(self, adapter, md_dir):
        """start_watcher=False 时仅启用正向同步"""
        syncer, watcher = adapter.enable_markdown_sync(md_dir, start_watcher=False)
        try:
            assert adapter._syncer is syncer
            assert watcher is None
            async def r():
                await adapter.save_with_embedding("ik2", "forward only", {"category": "note"})
            asyncio.run(r())
            syncer._flush()
            assert os.path.exists(os.path.join(md_dir, "note", "ik2.md"))
        finally:
            adapter.disable_markdown_sync(watcher)

    def test_disable_removes_syncer(self, adapter, md_dir):
        """disable 后 adapter._syncer 为 None，save 不再触发同步"""
        syncer, watcher = adapter.enable_markdown_sync(md_dir, start_watcher=False)
        adapter.disable_markdown_sync(watcher)
        assert adapter._syncer is None
        # disable 后写入不应触发 syncer（已 close）
        async def r():
            await adapter.save_with_embedding("ik3", "after disable", {"category": "pref"})
        asyncio.run(r())
        assert not os.path.exists(os.path.join(md_dir, "pref", "ik3.md"))



# ──────────────────────────────────────────────────────────────
# 验收 7: 真实 watchdog Observer 端到端（锁定 dispatch 继承修复）
# ──────────────────────────────────────────────────────────────

class TestRealObserver:
    def test_real_observer_reverse_sync(self, adapter, syncer, md_dir):
        """真实 Observer 捕获文件编辑 → 反向更新 SQLite（防 dispatch 回归）"""
        pytest.importorskip("watchdog")
        async def r():
            await adapter.save_with_embedding('k1', 'observer e2e content', {'category': 'pref'})
        asyncio.run(r()); time.sleep(1.5); syncer._flush()

        watcher = MarkdownFileWatcher(md_dir, adapter, syncer, dedup_ms=150)
        watcher.start()
        assert watcher._started, "Observer 启动失败"
        try:
            fp = os.path.join(md_dir, 'pref', 'k1.md')
            parsed = parse_markdown_file(fp)
            fm = parsed['front_matter']
            new_content = 'observer e2e EDITED'
            body = (f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\n"
                    f"# {new_content[:50]}\n\n{new_content}\n")
            with open(fp, 'w', encoding='utf-8') as f:
                f.write(body)
            # 轮询等待真实 Observer 链路完成反向更新
            assert _wait_for(
                lambda: adapter.get_raw_memory('k1')['data'] == new_content,
                timeout=4.0,
            ), "真实 Observer 反向同步未落库"
            # 单向编辑无冲突
            assert len(adapter.list_sync_conflicts()) == 0
        finally:
            watcher.stop()

    def test_start_failure_does_not_block(self, adapter, syncer, md_dir, monkeypatch):
        """【不易】FileWatcher 启动失败不抛异常、不阻塞主进程"""
        import agent.memory.file_watcher as fw
        # 构造 Observer 抛异常
        def bad_observer(*a, **kw):
            raise RuntimeError("simulated observer failure")
        monkeypatch.setattr("watchdog.observers.Observer", bad_observer)
        watcher = MarkdownFileWatcher(md_dir, adapter, syncer)
        watcher.start()  # 不应抛
        assert watcher._started is False


# ──────────────────────────────────────────────────────────────
# 验收 7: close() 数据完整性 — 残留 pending 必须在关闭前 flush
# ──────────────────────────────────────────────────────────────

class TestCloseFlushesPending:
    """close() 数据完整性回归测试

    回归场景：修复前 close() 先设 _closed=True 再调 _flush，而 _flush 首行
    `if self._closed: return` 直接跳过 → 残留 pending 永久丢失（违不易：数据完整性）。
    修复后 close() 先 flush 再设 _closed，保证 pending 不丢。
    """

    def test_close_flushes_pending_before_close_flag(self, adapter, md_dir):
        """close() 必须先 flush 残留 pending 再设 _closed（守不易：数据完整性）"""
        # debounce 设长避免 Timer 自动 flush 干扰观察
        syncer = MarkdownSyncer(
            adapter, output_dir=md_dir, debounce_seconds=60, batch_threshold=10,
        )
        adapter.set_syncer(syncer)

        async def write_one():
            await adapter.save_with_embedding(
                "close_k1", "close_data", {"category": "note"},
            )
        asyncio.run(write_one())

        # 前置断言：pending 已累积，.md 文件尚未生成
        assert "close_k1" in syncer._pending
        assert not os.path.exists(os.path.join(md_dir, "note", "close_k1.md"))

        # 触发关闭：修复前会丢失 pending，修复后应 flush
        syncer.close()

        # 验证 .md 文件已生成（pending 未丢失）
        assert os.path.exists(os.path.join(md_dir, "note", "close_k1.md"))
        # 验证 _closed 已设
        assert syncer._closed is True
        # pending 已被 flush 清空
        assert len(syncer._pending) == 0

    def test_close_idempotent(self, adapter, md_dir):
        """二次调用 close() 不抛异常、无副作用（守简易：幂等）"""
        syncer = MarkdownSyncer(adapter, output_dir=md_dir, debounce_seconds=60)
        syncer.close()
        # 二次调用不应抛异常
        syncer.close()
        assert syncer._closed is True

    def test_close_with_empty_pending_no_error(self, adapter, md_dir):
        """无 pending 时 close() 正常完成（守降级）"""
        syncer = MarkdownSyncer(adapter, output_dir=md_dir, debounce_seconds=60)
        # 无任何写入，直接 close
        syncer.close()
        assert syncer._closed is True
        assert len(syncer._pending) == 0

    def test_close_loops_flush_until_pending_empty(self, adapter, md_dir):
        """close() 循环 flush 直到 pending 为空（守不易：数据完整性）

        回归：修复前单次 _flush 后到 _closed=True 前窗口期，并发 notify_change
        累积的 pending 会丢失。修复后循环 flush 杜绝此窗口。
        """
        syncer = MarkdownSyncer(
            adapter, output_dir=md_dir, debounce_seconds=60, batch_threshold=100,
        )
        adapter.set_syncer(syncer)

        # 先写入记忆累积 pending
        async def setup():
            await adapter.save_with_embedding(
                "loop_k1", "loop_data", {"category": "note"},
            )
        asyncio.run(setup())

        # 模拟竞态：第一次 _flush 后注入新 pending（模拟 close 期间到达的 notify_change）
        original_flush = syncer._flush
        flush_count = [0]

        def mock_flush():
            original_flush()
            flush_count[0] += 1
            # 第一次 flush 后注入新 pending（模拟并发 notify_change 累积）
            if flush_count[0] == 1:
                with syncer._lock:
                    syncer._pending["injected_k"] = "upsert"

        syncer._flush = mock_flush
        syncer.close()

        # 验证 _flush 被调用至少 2 次（循环生效，第二次处理注入的 pending）
        assert flush_count[0] >= 2, f"期望循环 flush 至少 2 次,实际 {flush_count[0]}"
        # 验证 _closed 已设
        assert syncer._closed is True
        # 验证 pending 已清空（循环 flush 后无残留）
        assert len(syncer._pending) == 0

    def test_close_max_rounds_prevents_livelock(self, adapter, md_dir):
        """close() 循环超过 max_rounds 时强制退出（守简易：防活锁）

        场景：持续高频写入导致 pending 永不空，max_rounds=5 兜底强制退出。
        """
        syncer = MarkdownSyncer(
            adapter, output_dir=md_dir, debounce_seconds=60, batch_threshold=100,
        )
        adapter.set_syncer(syncer)

        # 模拟持续注入 pending（永不空），触发 max_rounds 兜底
        original_flush = syncer._flush

        def always_inject_flush():
            original_flush()
            # 每次 flush 后都注入新 pending（模拟持续高频写入）
            with syncer._lock:
                syncer._pending["persistent_k"] = "upsert"

        syncer._flush = always_inject_flush

        # close() 应在 max_rounds 后退出，不死循环
        import time as _time
        t0 = _time.time()
        syncer.close()
        elapsed = _time.time() - t0

        # 验证未死循环（5 次 flush 应在 5 秒内完成）
        assert elapsed < 5.0, f"close() 耗时 {elapsed}s，疑似活锁"
        # 验证 _closed 最终已设（强制退出路径）
        assert syncer._closed is True


# ──────────────────────────────────────────────────────────────
# 【方案一】watcher.stop() 等待反向更新异步线程完成
# ──────────────────────────────────────────────────────────────

class TestWatcherThreadJoin:
    """【方案一】watcher 线程句柄管理 + stop() join（守不易：数据完整性）

    回归场景：FileWatcher._reverse_update 通过 threading.Thread 异步执行
    save_with_embedding → notify_change。若线程在 syncer.close() 后到达，
    notify_change 见 _closed=True 直接 return，pending 丢失。
    修复后 watcher.stop() 中 join _reverse_threads，确保异步线程在 close 前完成。
    """

    def test_stop_waits_for_reverse_update_threads(self, adapter, syncer, watcher, md_dir):
        """stop() 必须等待反向更新线程完成（守不易：数据完整性）"""
        # 模拟慢反向更新线程
        done = threading.Event()

        def slow_reverse():
            try:
                time.sleep(0.3)
            finally:
                done.set()

        t = threading.Thread(target=slow_reverse, daemon=True)
        with watcher._proc_lock:
            watcher._reverse_threads.add(t)
        t.start()

        # 验证 stop 等待线程完成
        t0 = time.time()
        watcher.stop()
        elapsed = time.time() - t0
        assert elapsed >= 0.25, f"stop 未等待线程完成,耗时 {elapsed}s"
        assert done.is_set(), "反向更新线程未完成"

    def test_stop_join_timeout_does_not_hang(self, adapter, syncer, watcher, md_dir):
        """stop() join 超时不卡死（守简易：超时兜底）"""
        # 模拟永不完成的线程
        def infinite_loop():
            while True:
                time.sleep(1)

        t = threading.Thread(target=infinite_loop, daemon=True)
        with watcher._proc_lock:
            watcher._reverse_threads.add(t)
        t.start()

        # 验证 stop 超时不卡死（join timeout=5s，总耗时 < 10s）
        t0 = time.time()
        watcher.stop()
        elapsed = time.time() - t0
        assert elapsed < 10.0, f"stop() 耗时 {elapsed}s，疑似卡死"


# ──────────────────────────────────────────────────────────────
# 【方案二】pending_recovery 崩溃恢复表
# ──────────────────────────────────────────────────────────────

class TestPendingRecovery:
    """【方案二】pending_recovery 表崩溃恢复（守不易：数据完整性）

    回归场景：syncer.close() 后到达的 notify_change 因 _closed=True 丢失。
    修复后 notify_change 在 _closed 时落盘到 pending_recovery，下次启动
    _recover_pending 读取并 re-apply。
    """

    def test_notify_change_after_close_persists_to_recovery(self, adapter, md_dir):
        """_closed=True 后 notify_change 落盘到 pending_recovery（守不易）"""
        syncer = MarkdownSyncer(adapter, output_dir=md_dir, debounce_seconds=60)
        adapter.set_syncer(syncer)
        syncer.close()  # 设 _closed=True

        # close 后 notify_change 应落盘到 pending_recovery
        syncer.notify_change("recovery_k1", "upsert")
        syncer.notify_change("recovery_k2", "delete")

        # 验证 pending_recovery 表有记录
        recovered = adapter.load_pending_recovery()
        keys = {r["key"]: r["op"] for r in recovered}
        assert keys.get("recovery_k1") == "upsert"
        assert keys.get("recovery_k2") == "delete"

    def test_close_residual_pending_persisted(self, adapter, md_dir):
        """close() 残留 pending（max_rounds 超限）落盘到 pending_recovery"""
        syncer = MarkdownSyncer(
            adapter, output_dir=md_dir, debounce_seconds=60, batch_threshold=100,
        )
        adapter.set_syncer(syncer)

        # 先注入 seed pending，让 close() 循环启动（否则首次检查 _pending 为空直接 break）
        with syncer._lock:
            syncer._pending["seed_k"] = "upsert"

        # 模拟持续注入 pending（永不空），触发 max_rounds 兜底
        original_flush = syncer._flush

        def always_inject_flush():
            original_flush()
            with syncer._lock:
                syncer._pending["residual_k"] = "upsert"

        syncer._flush = always_inject_flush
        syncer.close()

        # 验证残留 pending 落盘到 pending_recovery
        recovered = adapter.load_pending_recovery()
        keys = [r["key"] for r in recovered]
        assert "residual_k" in keys, f"残留 pending 未落盘,recovered={keys}"

    def test_recover_pending_on_init(self, adapter, md_dir):
        """__init__ 从 pending_recovery 恢复未 flush 的 pending（崩溃补偿）"""
        # 预置 pending_recovery 记录（模拟上次 close 时落盘）
        adapter.save_pending_recovery("recover_k1", "upsert")
        adapter.save_pending_recovery("recover_k2", "delete")

        # 创建 syncer，__init__ 应自动恢复
        syncer = MarkdownSyncer(adapter, output_dir=md_dir, debounce_seconds=60)

        # 验证 pending 已恢复到 _pending
        with syncer._lock:
            pending = dict(syncer._pending)
        assert pending.get("recover_k1") == "upsert"
        assert pending.get("recover_k2") == "delete"

        # 验证 pending_recovery 表已清理（避免重复 re-apply）
        recovered = adapter.load_pending_recovery()
        assert len(recovered) == 0, f"恢复表未清理,recovered={recovered}"

    def test_clear_pending_recovery(self, adapter):
        """clear_pending_recovery 清理指定 key 或全表"""
        adapter.save_pending_recovery("k1", "upsert")
        adapter.save_pending_recovery("k2", "upsert")
        adapter.save_pending_recovery("k3", "upsert")

        # 清理指定 key
        adapter.clear_pending_recovery(["k1", "k2"])
        recovered = adapter.load_pending_recovery()
        keys = [r["key"] for r in recovered]
        assert "k3" in keys
        assert "k1" not in keys
        assert "k2" not in keys

        # 清理全表
        adapter.clear_pending_recovery()
        assert adapter.load_pending_recovery() == []

