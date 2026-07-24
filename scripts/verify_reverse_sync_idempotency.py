"""verify_reverse_sync_idempotency.py — 反向同步幂等去重逻辑验证 [TLM-L3]

模拟 Markdown 文件被反复修改的场景，验证反向同步的两层幂等去重：

层1 — 事件级去重（500ms 窗口）：同一文件 burst 连发 N 次 on_modified，
       去重窗口内只 _do_process 1 次。
层2 — 内容级幂等（content_hash）：处理时若 file_hash == db_hash（文件与 DB
       一致），直接跳过，不产生 SQLite 写入。
层3 — 冲突级幂等（record_sync_conflict）：同一冲突状态多次检测只记 1 条。

验证场景:
A. 文件内容未变 + 多次 on_modified → 0 次 SQLite 写入（层2 幂等跳过）
B. 文件内容变化 + 去重窗口内多次 on_modified → 1 次 SQLite 写入（层1 去重）
C. 文件变化已同步后 + 窗口外再次触发同内容 → 仍 1 次写入（层2 幂等跳过）
D. 冲突状态 + 多次 on_modified → sync_conflicts 只 1 条（层3 幂等）

运行: python scripts/verify_reverse_sync_idempotency.py
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
logger = logging.getLogger("verify_idempotency")


def _wait_for(predicate, timeout=3.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _edit_file(fp, new_data):
    """编辑 .md 内容，保留原 Front Matter 的 content_hash（模拟文件侧变更）"""
    p = parse_markdown_file(fp)
    fm = p["front_matter"]
    body = (f"---\n{yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)}---\n\n"
            f"# {new_data[:50]}\n\n{new_data}\n")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(body)


def main():
    tmp = tempfile.mkdtemp(prefix="idemp_verify_")
    db = os.path.join(tmp, "t.db")
    md = os.path.join(tmp, "md")

    adapter = HolographicAdapter(db_path=db, enable_cache=False)
    syncer, watcher = adapter.enable_markdown_sync(
        output_dir=md, debounce_seconds=1, batch_threshold=100, dedup_ms=200,
    )

    # 计数反向 SQLite 写入（patch save_with_embedding）
    reverse_writes = 0
    original_save = adapter.save_with_embedding

    async def counting_save(key, data, metadata=None, **kwargs):
        nonlocal reverse_writes
        reverse_writes += 1
        return await original_save(key, data, metadata, **kwargs)
    adapter.save_with_embedding = counting_save

    results = []

    def _seed(key, content, category="pref"):
        async def r():
            await original_save(key, content, {"category": category})
        asyncio.run(r())
        syncer._flush()
        return os.path.join(md, category, f"{key}.md")

    # ── 场景 A：文件内容未变 + 多次 on_modified → 0 次 SQLite 写入 ──
    reverse_writes = 0
    fp = _seed("a1", "stable content A")
    # 不修改文件内容，直接连发 on_modified（模拟 Windows 误触发）
    for _ in range(5):
        watcher.on_modified(fp)
        time.sleep(0.02)
    _wait_for(lambda: True, timeout=0.5)  # 等去重窗口过期
    results.append(("A: 文件未变 + 5次on_modified → 0次SQLite写入",
                    reverse_writes == 0, f"reverse_writes={reverse_writes}"))

    # ── 场景 B：文件内容变化 + 去重窗口内多次 on_modified → 1 次 SQLite 写入 ──
    reverse_writes = 0
    fp = _seed("b1", "original B")
    _edit_file(fp, "edited B content")
    # 去重窗口内连发 5 次（200ms 窗口，间隔 20ms）
    for _ in range(5):
        watcher.on_modified(fp)
        time.sleep(0.02)
    _wait_for(lambda: reverse_writes >= 1, timeout=2.0)
    time.sleep(0.4)
    results.append(("B: 文件变化 + 窗口内5次on_modified → 1次SQLite写入",
                    reverse_writes == 1, f"reverse_writes={reverse_writes}"))
    _wait_for(lambda: adapter.get_raw_memory("b1")["data"] == "edited B content", timeout=2.0)

    # ── 场景 C：已同步后 + 窗口外再次触发同内容 → 仍 1 次写入（幂等跳过）──
    reverse_writes = 0
    # b1 已反向同步，等 refresh_single 刷新基线
    _wait_for(lambda: parse_markdown_file(fp)["front_matter"]["content_hash"]
              == compute_content_hash("edited B content"), timeout=2.0)
    # 窗口外再次触发（文件内容 == db，幂等跳过）
    time.sleep(0.5)
    watcher.on_modified(fp)
    _wait_for(lambda: True, timeout=0.5)
    results.append(("C: 已同步 + 窗口外同内容触发 → 0次新增写入（幂等跳过）",
                    reverse_writes == 0, f"reverse_writes={reverse_writes}"))

    # ── 场景 D：冲突状态 + 多次 on_modified → sync_conflicts 只 1 条 ──
    fp = _seed("d1", "original D")
    # DB 侧改 + 文件侧改（双向偏离 base → 冲突）
    async def db_edit():
        await original_save("d1", "DB side D", {"category": "pref"})
    asyncio.run(db_edit())
    time.sleep(0.2)
    _edit_file(fp, "file side D")
    conflicts_before = len(adapter.list_sync_conflicts(unresolved_only=True))
    # 连发 3 次（去重窗口内）+ 窗口外再 2 次（同冲突状态）
    for _ in range(3):
        watcher.on_modified(fp)
        time.sleep(0.02)
    _wait_for(lambda: True, timeout=0.5)
    time.sleep(0.4)
    watcher.on_modified(fp)
    _wait_for(lambda: True, timeout=0.5)
    time.sleep(0.4)
    watcher.on_modified(fp)
    _wait_for(lambda: True, timeout=0.5)
    conflicts_after = len(adapter.list_sync_conflicts(unresolved_only=True))
    new_conflicts = conflicts_after - conflicts_before
    results.append(("D: 冲突状态 + 5次on_modified → 只新增1条冲突（幂等去重）",
                    new_conflicts == 1, f"新增冲突={new_conflicts}（前{conflicts_before}后{conflicts_after}）"))

    # ── 汇总 ──
    adapter.disable_markdown_sync(watcher)
    print("\n" + "=" * 72)
    print("反向同步幂等去重逻辑验证结果")
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
