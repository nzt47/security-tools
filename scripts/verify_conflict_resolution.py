"""verify_conflict_resolution.py — 双向同时编辑冲突解决逻辑验证 [TLM-L3]

模拟 SQLite 与 Markdown 文件被同时编辑（双向都偏离上次同步基线 content_hash），
验证冲突解决逻辑按预期将记录写入 sync_conflicts 表。

验证项:
1. 双向同时编辑 → sync_conflicts 表写入一条记录
2. 冲突记录字段完整：sqlite_id / db_hash / file_hash / detected_at / resolution /
   resolved_at（未解决时为 NULL）
3. 冲突不自动覆盖任何一方（守不易：DB 与文件内容均保持各自编辑值）
4. 多次双向冲突累积记录（不覆盖、不丢失）
5. resolve_sync_conflict 标记后 resolved_at 非空、resolution 更新
6. enable_markdown_sync 集成入口可用（串联 refresh_single + 冲突处理）

运行: python scripts/verify_conflict_resolution.py
退出码: 0 全部通过, 1 有失败
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import yaml

from agent.memory.adapters.holographic_adapter import HolographicAdapter
from agent.memory.markdown_syncer import compute_content_hash, parse_markdown_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    encoding="utf-8",
    force=True,
)
logging.getLogger("watchdog").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logger = logging.getLogger("verify_conflict")


def _wait_for(predicate, timeout=3.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _edit_file(fp, new_data):
    """编辑 .md 内容但保留原 Front Matter 的 content_hash（模拟文件侧偏离 base）"""
    p = parse_markdown_file(fp)
    fm = p["front_matter"]
    body = (f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\n"
            f"# {new_data[:50]}\n\n{new_data}\n")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(body)


def main():
    tmp = tempfile.mkdtemp(prefix="conflict_verify_")
    db = os.path.join(tmp, "t.db")
    md = os.path.join(tmp, "md")

    # 用例 6：通过 enable_markdown_sync 集成入口启用（串联 refresh_single + 冲突处理）
    adapter = HolographicAdapter(db_path=db, enable_cache=False)
    syncer, watcher = adapter.enable_markdown_sync(
        output_dir=md, debounce_seconds=1, batch_threshold=100, dedup_ms=200,
    )
    integration_ok = watcher is not None and watcher._started
    results = [("用例6: enable_markdown_sync 集成入口可用",
                integration_ok, f"watcher started={integration_ok}")]

    # ── 准备：写 2 条记忆并 flush（建立 base = file = db）──
    async def seed():
        await adapter.save_with_embedding("mem_a", "原始内容 A", {"category": "pref"})
        await adapter.save_with_embedding("mem_b", "原始内容 B", {"category": "note"})
    asyncio.run(seed())
    syncer._flush()
    fp_a = os.path.join(md, "pref", "mem_a.md")
    fp_b = os.path.join(md, "note", "mem_b.md")
    assert os.path.exists(fp_a) and os.path.exists(fp_b), "forward flush 未生成文件"

    # ── 用例 1+2：双向同时编辑 → sync_conflicts 写入且字段完整 ──
    # DB 侧改 mem_a
    async def db_edit():
        await adapter.save("mem_a", "DB 侧修改的内容 A", {"category": "pref"})
    asyncio.run(db_edit())
    time.sleep(0.2)
    # 文件侧改 mem_a（保留旧 base hash）→ 双向都偏离 base → 冲突
    _edit_file(fp_a, "文件侧修改的内容 A")
    watcher._do_process(fp_a)
    time.sleep(0.3)

    conflicts = adapter.list_sync_conflicts()
    c = conflicts[0] if conflicts else {}
    field_complete = (
        len(conflicts) >= 1
        and c.get("sqlite_id") == "mem_a"
        and c.get("db_hash") is not None and c.get("db_hash") != ""
        and c.get("file_hash") is not None and c.get("file_hash") != ""
        and c.get("db_hash") != c.get("file_hash")
        and c.get("detected_at") is not None
        and c.get("resolution") == "unresolved"
        and c.get("resolved_at") is None
    )
    results.append(("用例1+2: 双向冲突写入 + 字段完整",
                    field_complete,
                    f"count={len(conflicts)} db={c.get('db_hash')} file={c.get('file_hash')} "
                    f"resolution={c.get('resolution')} resolved_at={c.get('resolved_at')}"))

    # ── 用例 3：冲突不自动覆盖任何一方 ──
    db_kept = adapter.get_raw_memory("mem_a")["data"] == "DB 侧修改的内容 A"
    file_kept = parse_markdown_file(fp_a)["data"] == "文件侧修改的内容 A"
    results.append(("用例3: 冲突不自动覆盖（DB 与文件各自保留）",
                    db_kept and file_kept,
                    f"db_kept={db_kept} file_kept={file_kept}"))

    # ── 用例 4：第二个冲突累积（不覆盖第一条）──
    async def db_edit_b():
        await adapter.save("mem_b", "DB 侧修改的内容 B", {"category": "note"})
    asyncio.run(db_edit_b())
    time.sleep(0.2)
    _edit_file(fp_b, "文件侧修改的内容 B")
    watcher._do_process(fp_b)
    time.sleep(0.3)

    all_conflicts = adapter.list_sync_conflicts(unresolved_only=True)
    has_two = len(all_conflicts) == 2  # 幂等去重后各恰好 1 条
    ids = {c["sqlite_id"] for c in all_conflicts}
    results.append(("用例4: 多次冲突累积 + 幂等去重（mem_a + mem_b 各 1 条）",
                    has_two and "mem_a" in ids and "mem_b" in ids,
                    f"count={len(all_conflicts)} ids={ids}"))

    # ── 用例 5：resolve_sync_conflict 标记解决 ──
    first_id = all_conflicts[0]["id"]
    adapter.resolve_sync_conflict(first_id, "manual_merge")
    after_resolve = adapter.list_sync_conflicts(unresolved_only=True)
    resolved_row = next(
        (c for c in adapter.list_sync_conflicts(unresolved_only=False) if c["id"] == first_id),
        {},
    )
    resolved_ok = (
        len(after_resolve) == len(all_conflicts) - 1  # 未解决少一条
        and resolved_row.get("resolved_at") is not None
        and resolved_row.get("resolution") == "manual_merge"
    )
    results.append(("用例5: resolve_sync_conflict 标记解决",
                    resolved_ok,
                    f"resolved_at={resolved_row.get('resolved_at')} "
                    f"resolution={resolved_row.get('resolution')} "
                    f"remaining_unresolved={len(after_resolve)}"))

    # ── 用例 7：单向编辑（仅文件）不产生冲突 ──
    async def seed_c():
        await adapter.save_with_embedding("mem_c", "原始内容 C", {"category": "pref"})
    asyncio.run(seed_c())
    syncer._flush()
    fp_c = os.path.join(md, "pref", "mem_c.md")
    _edit_file(fp_c, "仅文件侧修改 C")
    conflicts_before_c = len(adapter.list_sync_conflicts(unresolved_only=True))
    watcher._do_process(fp_c)
    _wait_for(lambda: adapter.get_raw_memory("mem_c")["data"] == "仅文件侧修改 C", timeout=2.0)
    conflicts_after_c = len(adapter.list_sync_conflicts(unresolved_only=True))
    results.append(("用例7: 单向文件编辑不产生冲突（正常反向更新）",
                    conflicts_after_c == conflicts_before_c,
                    f"before={conflicts_before_c} after={conflicts_after_c}"))

    # ── 汇总 ──
    adapter.disable_markdown_sync(watcher)
    print("\n" + "=" * 72)
    print("双向同时编辑冲突解决逻辑验证结果")
    print("=" * 72)
    all_pass = True
    for name, ok, detail in results:
        flag = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  [{flag}] {name}")
        print(f"         {detail}")
    print("=" * 72)
    print("全部通过" if all_pass else "存在失败")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
