"""知识库链接检测性能基准：对比不同数据量下 孤儿/断链/lint_all 的耗时。

用法:
    python scripts/bench_knowledge_links.py                # 默认 100/500/1000/5000 卡
    python scripts/bench_knowledge_links.py --sizes 200,2000
    python scripts/bench_knowledge_links.py --runs 5       # 每组取中位数

构造模型（模拟真实知识库混合形态）:
    - 25% 孤儿卡（无入链，各带 1 条指向幽灵目标的断链）
    - 25% 断链引用卡（互链环避免孤儿 + 各带 1 条断链）
    - 50% 健康互链卡
    ghost 目标池固定 200 个（模拟"多卡指向同一批失效目标"的真实形态）。

输出:
    数据量 | 孤儿 | 断链 | find_orphans(ms) | find_broken_links(ms) | lint_all(ms)
"""
from __future__ import annotations

import argparse
import logging
import statistics
import tempfile
import time
from pathlib import Path

from agent.knowledge.card import CardStore
from agent.knowledge.links import find_broken_links, find_orphans
from agent.knowledge.lint import lint_all
from agent.knowledge.schema import Card

logging.disable(logging.CRITICAL)  # 基准时不输出日志（避免日志 I/O 干扰计时）

GHOST_POOL_SIZE = 200


def build_store(total: int) -> CardStore:
    """构造 total 张卡的临时知识库（25% 孤儿 + 25% 断链引用 + 50% 互链）。"""
    root = Path(tempfile.mkdtemp())
    store = CardStore(root / "wiki")

    def mk(slug: str, links) -> None:
        c = Card(title=slug, slug=slug, status="current", type="concepts",
                 source="inbox/t.md", date="2026-08-08", tags=[],
                 links=list(links), contradictions=[], insight="洞见")
        c.content = f"# {slug}\n" + "".join(f"[[{l}]] " for l in links)
        store.create(c)

    n_orphan = total // 4
    n_broken = total // 4
    for i in range(n_orphan):
        mk(f"o{i}", (f"幽灵{i % GHOST_POOL_SIZE}",))
    for i in range(n_broken):
        mk(f"b{i}", (f"b{(i + 1) % n_broken}", f"幽灵{i % GHOST_POOL_SIZE}"))
    for i in range(total - n_orphan - n_broken):
        mk(f"g{i}", (f"g{(i + 1) % (total - n_orphan - n_broken)}",))
    return store


def median_ms(fn, *args, runs: int, **kwargs) -> float:
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn(*args, **kwargs)
        samples.append((time.perf_counter() - t0) * 1000)
    return round(statistics.median(samples), 2)


def main() -> None:
    parser = argparse.ArgumentParser(description="知识库链接检测性能基准")
    parser.add_argument("--sizes", default="100,500,1000,5000",
                        help="数据量列表（逗号分隔）")
    parser.add_argument("--runs", type=int, default=3,
                        help="每组重复次数，取中位数")
    args = parser.parse_args()
    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]

    print(f"{'数据量':>6} | {'孤儿':>5} | {'断链':>5} | "
          f"{'find_orphans(ms)':>16} | {'find_broken_links(ms)':>20} | {'lint_all(ms)':>12}")
    print("-" * 78)
    for n in sizes:
        store = build_store(n)
        cards = list(store.list())
        orphans = len(find_orphans(cards))
        broken = len(find_broken_links(cards, store))
        t_orphan = median_ms(find_orphans, cards, runs=args.runs)
        t_broken = median_ms(find_broken_links, cards, store, runs=args.runs)
        t_lint = median_ms(lint_all, store, index_path=str(store._wiki_root.parent / "index.md"),
                           runs=args.runs)
        print(f"{n:>6} | {orphans:>5} | {broken:>5} | {t_orphan:>16} | {t_broken:>20} | {t_lint:>12}")


if __name__ == "__main__":
    main()
