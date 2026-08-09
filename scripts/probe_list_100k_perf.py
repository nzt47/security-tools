"""store.list() 10 万卡超大规模性能瓶颈探测脚本（一次性诊断工具）。

【不易】本脚本只做测量不改业务代码；建库直接写文件绕过 create 校验以提速，
数据结构与 test_knowledge_link_perf.py 保持一致（frontmatter 模板卡）。

测量项（全部打印 + 断言性提示，无自动失败）:
    1. 建库耗时（10 万文件写入）
    2. list() 无缓存冷读（读文件 + YAML 解析 + 排序）—— 用户反馈的"耗时过长"主路径
    3. list(use_cache=True) 首次加载（全量读盘 + 缓存）
    4. list(use_cache=True) 缓存命中（仅指纹扫描 + 内存过滤）
    5. _fingerprint() 单独扫描成本（os.scandir + stat）
    6. 瓶颈分解: 纯文件 IO(read_text) vs YAML 解析(safe_load) vs 排序

用法:
    python scripts/probe_list_100k_perf.py [--cards 100000]
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.knowledge.card import CardStore  # noqa: E402

logging.disable(logging.CRITICAL)  # 关闭业务日志，避免日志 IO 干扰计时


def build_store(root: Path, total: int) -> CardStore:
    """批量写卡建库（frontmatter 模板，绕过 create 校验提速）。

    结构：1000 张互链环卡 + 其余卡片少量互链（links 保持极简以贴近真实场景）。
    """
    store = CardStore(root / "wiki")
    t0 = time.perf_counter()
    # 三个 type 目录均创建，模拟真实三类型分布
    for t in ("concepts", "entities", "insights"):
        (store._wiki_root / t).mkdir(parents=True, exist_ok=True)

    def write(slug: str, links: list[str], tdir: str) -> None:
        link_yaml = str(links).replace("'", '"')
        (store._wiki_root / tdir / f"{slug}.md").write_text(
            f"---\ntitle: {slug}\nslug: {slug}\nstatus: current\ntype: {tdir}\n"
            f"source: inbox/t.md\ndate: 2026-08-08\ntags: []\nlinks: {link_yaml}\n"
            f"contradictions: []\ninsight: 洞见\n---\n正文 {slug}\n",
            encoding="utf-8",
        )

    for i in range(total):
        tdir = ("concepts", "entities", "insights")[i % 3]
        write(f"c{i}", [f"c{(i + 1) % total}"], tdir)
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"[建库] {total} 张卡写入耗时: {elapsed:.0f}ms")
    return store


def probe_list_cold(store: CardStore, rounds: int = 3) -> None:
    """无缓存 list()：读文件 + YAML 解析 + 排序（用户反馈的主路径）。"""
    times = []
    n = 0
    for _ in range(rounds):
        t0 = time.perf_counter()
        cards = store.list()
        times.append((time.perf_counter() - t0) * 1000)
        n = len(cards)
    print(f"[list 无缓存] 每次读盘 {n} 卡: {[f'{t:.0f}ms' for t in times]}")


def probe_list_cache(store: CardStore) -> None:
    """use_cache 路径：首次加载 vs 缓存命中（含指纹扫描成本）。"""
    t0 = time.perf_counter()
    cards = store.list(use_cache=True)
    first = (time.perf_counter() - t0) * 1000
    print(f"[list use_cache] 首次加载(全量读盘+缓存): {first:.0f}ms ({len(cards)} 卡)")

    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        store.list(use_cache=True)
        times.append((time.perf_counter() - t0) * 1000)
    print(f"[list use_cache] 缓存命中(指纹扫描+过滤): {[f'{t:.0f}ms' for t in times]}")


def probe_fingerprint(store: CardStore) -> None:
    """_fingerprint() 单独成本：os.scandir + stat（缓存命中路径的主要开销）。"""
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        fp = store._fingerprint()
        times.append((time.perf_counter() - t0) * 1000)
    print(f"[_fingerprint] 全量扫描+stat: {[f'{t:.0f}ms' for t in times]} ({len(fp)} 条目)")


def probe_bottleneck(store: CardStore, sample: int = 5000) -> None:
    """瓶颈分解：纯 IO(read_text) vs YAML 解析(safe_load) vs 排序。"""
    files = []
    for t in ("concepts", "entities", "insights"):
        files.extend(sorted((store._wiki_root / t).glob("*.md")))
    files = files[:sample]

    # 纯文件 IO
    t0 = time.perf_counter()
    texts = [p.read_text(encoding="utf-8") for p in files]
    io_ms = (time.perf_counter() - t0) * 1000
    total_bytes = sum(len(x) for x in texts)

    # YAML 解析（含 frontmatter 提取）
    t0 = time.perf_counter()
    for text in texts:
        m = text.split("---", 2)
        if len(m) >= 3:
            yaml.safe_load(m[1])
    yaml_ms = (time.perf_counter() - t0) * 1000

    # 字符串排序（list 最终按 slug 字典序）
    t0 = time.perf_counter()
    sorted(texts)
    sort_ms = (time.perf_counter() - t0) * 1000

    print(f"[瓶颈分解] 样本 {sample} 卡, {total_bytes / 1024 / 1024:.1f}MB")
    print(f"  纯文件 IO(read_text): {io_ms:.0f}ms  ({io_ms / sample:.3f}ms/卡)")
    print(f"  YAML 解析(safe_load): {yaml_ms:.0f}ms  ({yaml_ms / sample:.3f}ms/卡)")
    print(f"  排序(sorted):         {sort_ms:.0f}ms")
    print(f"  推算 100k 卡: IO≈{io_ms * 100000 / sample / 1000:.1f}s  YAML≈{yaml_ms * 100000 / sample / 1000:.1f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="store.list() 10 万卡性能瓶颈探测")
    parser.add_argument("--cards", type=int, default=100000, help="卡片总数")
    args = parser.parse_args()

    total = args.cards
    tmp = Path(tempfile.mkdtemp(prefix="list100k_"))
    try:
        print(f"=== store.list() {total} 卡性能探测 ===")
        store = build_store(tmp, total)
        probe_list_cold(store)
        probe_list_cache(store)
        probe_fingerprint(store)
        probe_bottleneck(store)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("=== 探测完成 ===")


if __name__ == "__main__":
    main()
