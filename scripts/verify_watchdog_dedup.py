"""verify_watchdog_dedup.py — Windows watchdog 500ms 去重逻辑验证脚本 [TLM-L3]

模拟 Windows 下 watchdog 对同一 .md 文件快速连续触发 on_modified（已知 Windows
会因文件系统事件聚合对单次保存连发 2~5 次 on_modified），验证：

1. 同一文件 burst 连发 N 次 → 去重窗口内只 _do_process 1 次
2. 不同文件各自 burst → per-path 独立去重，各自 1 次
3. 去重窗口外再次触发 → 不被旧窗口合并，正常再次处理（窗口正确失效）
4. .tmp 临时文件事件被过滤

运行: python scripts/verify_watchdog_dedup.py
退出码: 0 全部通过, 1 有失败
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import time
from unittest.mock import patch

# 让脚本可独立运行
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import yaml

from agent.memory.adapters.holographic_adapter import HolographicAdapter
from agent.memory.markdown_syncer import MarkdownSyncer, parse_markdown_file
from agent.memory.file_watcher import MarkdownFileWatcher

# 开启 debug 日志，便于排查同步延迟/冲突
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    encoding="utf-8",
    force=True,
)
# 收敛第三方噪音
logging.getLogger("watchdog").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger("verify_dedup")


def _wait_for(predicate, timeout=3.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def main():
    tmp = tempfile.mkdtemp(prefix="dedup_verify_")
    db = os.path.join(tmp, "t.db")
    md = os.path.join(tmp, "md")
    adapter = HolographicAdapter(db_path=db, enable_cache=False)
    syncer = MarkdownSyncer(adapter, output_dir=md, debounce_seconds=1, batch_threshold=100)
    adapter.set_syncer(syncer)
    # dedup_ms=300 便于观察（生产默认 500ms；本脚本验证机制）
    watcher = MarkdownFileWatcher(md, adapter, syncer, dedup_ms=300)

    results = []

    # 计数 _do_process 调用
    process_calls = []
    original_do = watcher._do_process

    def counting_do(path):
        process_calls.append(path)
        original_do(path)

    watcher._do_process = counting_do

    def _seed_and_get(key, content, category):
        """写一条记忆并 flush，返回对应 .md 路径（base==file==db 稳定态）"""
        async def _r():
            await adapter.save_with_embedding(key, content, {"category": category})
        asyncio.run(_r())
        syncer._flush()
        return os.path.join(md, category, f"{key}.md")

    def _edit_file(fp, new_data):
        """编辑 .md 内容（保留原 Front Matter 的 base hash）"""
        p = parse_markdown_file(fp)
        f = p["front_matter"]
        b = (f"---\n{yaml.safe_dump(f, allow_unicode=True, sort_keys=False)}---\n\n"
             f"# {new_data[:50]}\n\n{new_data}\n")
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(b)

    # ── 用例 1：同一文件 burst 连发 5 次 → 只 _do_process 1 次 ──
    process_calls.clear()
    fp1 = _seed_and_get("c1k1", "content one", "pref")
    _edit_file(fp1, "content one EDITED burst")
    for _ in range(5):
        watcher.on_modified(fp1)
        time.sleep(0.02)
    _wait_for(lambda: len(process_calls) >= 1, timeout=1.0)
    time.sleep(0.5)
    results.append(("用例1: 同文件 burst 5 次 → 1 次处理",
                    len(process_calls) == 1, f"实际 {len(process_calls)}"))
    _wait_for(lambda: adapter.get_raw_memory("c1k1")["data"] == "content one EDITED burst", timeout=2.0)

    # ── 用例 2：不同文件各自 burst → per-path 独立去重 ──
    process_calls.clear()
    fp_a = _seed_and_get("c2a", "alpha content", "pref")
    fp_b = _seed_and_get("c2b", "beta content", "note")
    _edit_file(fp_a, "alpha edited")
    _edit_file(fp_b, "beta edited")
    # 两文件交错连发（模拟 Windows 对多文件同时触发）
    for _ in range(4):
        watcher.on_modified(fp_a)
        watcher.on_modified(fp_b)
        time.sleep(0.02)
    _wait_for(lambda: len(process_calls) >= 2, timeout=1.0)
    time.sleep(0.5)
    distinct = set(process_calls)
    results.append(("用例2: 两文件交错 burst → 各 1 次（per-path 独立）",
                    len(process_calls) == 2 and len(distinct) == 2,
                    f"实际 {len(process_calls)} 次, 路径 {len(distinct)} 个"))

    # ── 用例 3：去重窗口外再次触发 → 正常再次处理（窗口正确失效）──
    process_calls.clear()
    fp3 = _seed_and_get("c3k", "stable content", "pref")
    _edit_file(fp3, "first edit in window")
    watcher.on_modified(fp3)
    _wait_for(lambda: len(process_calls) >= 1, timeout=1.0)
    _wait_for(lambda: adapter.get_raw_memory("c3k")["data"] == "first edit in window", timeout=2.0)
    # 重新 flush 让 base 对齐（避免误判冲突），再触发窗口外第二次
    syncer._flush()
    _edit_file(fp3, "second edit after window")
    time.sleep(0.5)  # 确保已超过去重窗口
    watcher.on_modified(fp3)
    _wait_for(lambda: len(process_calls) >= 2, timeout=1.5)
    results.append(("用例3: 窗口外再次触发 → 正常再次处理",
                    len(process_calls) == 2, f"实际 {len(process_calls)} 次（期望 2）"))

    # ── 用例 4：.tmp 临时文件事件被过滤 ──
    process_calls.clear()
    tmp_fp = fp3 + ".tmp"
    with open(tmp_fp, "w", encoding="utf-8") as fh:
        fh.write("tmp content")
    watcher.on_modified(tmp_fp)
    time.sleep(0.5)
    results.append(("用例4: .tmp 事件被过滤",
                    len(process_calls) == 0 and len(watcher._dedup_timers) == 0,
                    f"process={len(process_calls)}, timers={len(watcher._dedup_timers)}"))

    # ── 汇总 ──
    watcher.stop()
    syncer.close()
    print("\n" + "=" * 70)
    print("Windows watchdog 500ms 去重验证结果")
    print("=" * 70)
    all_pass = True
    for name, ok, detail in results:
        flag = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  [{flag}] {name}  ({detail})")
    print("=" * 70)
    print("全部通过" if all_pass else "存在失败")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
