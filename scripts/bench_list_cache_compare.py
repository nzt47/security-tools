"""store.list() 内存缓存优化前后对比测试脚本。

验证对象（agent/knowledge/card.py 的 list 内存缓存）:
    A. list() 无缓存冷读 —— 优化前主路径（10 万卡约 69s，YAML 解析占 74%）
    B. list(use_cache=True) 首次加载（全量读盘 + 建缓存）
    C. list(use_cache=True) 缓存命中（指纹校验 + 内存过滤）—— 约 170ms
    D. 写后首查（本次优化核心）:
       - 优化前（失效重载）: create 后缓存失效 → 下次 list 全量重载
       - 优化后（增量同步）: create 后即时更新缓存与指纹 → 下次 list 命中
    E. 一致性: create/update/delete/批量删除 后，缓存结果与磁盘重读完全一致

用法:
    python scripts/bench_list_cache_compare.py [--cards 20000] [--show-cards]
"""
from __future__ import annotations

import argparse
import logging
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.knowledge.card import CardStore  # noqa: E402
from agent.knowledge.schema import Card  # noqa: E402

logging.disable(logging.CRITICAL)  # 关闭业务日志，避免日志 IO 干扰计时

random.seed(42)  # 确定性：一致性校验可复现


def build_store(root: Path, total: int) -> CardStore:
    """批量写卡建库（三类型分布，frontmatter 模板，绕过 create 校验提速）。"""
    store = CardStore(root / "wiki")
    for t in ("concepts", "entities", "insights"):
        (store._wiki_root / t).mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    for i in range(total):
        tdir = ("concepts", "entities", "insights")[i % 3]
        (store._wiki_root / tdir / f"c{i}.md").write_text(
            f"---\ntitle: c{i}\nslug: c{i}\nstatus: current\ntype: {tdir}\n"
            f"source: inbox/t.md\ndate: 2026-08-08\ntags: []\nlinks: []\n"
            f"contradictions: []\ninsight: 洞见\n---\n正文 c{i}\n",
            encoding="utf-8",
        )
    print(f"[建库] {total} 张卡写入: {(time.perf_counter() - t0) * 1000:.0f}ms")
    return store


def make_card(slug: str, *, status: str = "current", type_: str = "concepts") -> Card:
    return Card(
        title=slug, slug=slug, status=status, type=type_,
        source="inbox/t.md", date="2026-08-08", insight="洞见", content=f"正文 {slug}",
    )


def bench_list_cold(store: CardStore) -> float:
    """A. 无缓存冷读（测 1 次，避免多次重复耗时）。返回耗时秒。"""
    t0 = time.perf_counter()
    store.list()
    return time.perf_counter() - t0


def bench_cache_hit(store: CardStore) -> float:
    """C. 缓存命中（预热后测 3 次取中位数）。返回耗时秒。"""
    store.list(use_cache=True)  # 预热（首次加载）
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        store.list(use_cache=True)
        times.append(time.perf_counter() - t0)
    return sorted(times)[1]  # 中位数


def bench_write_then_list_new(store: CardStore) -> float:
    """D-新行为: create 后增量同步 → 写后首查命中缓存。返回耗时秒。"""
    card = make_card("bench-new")
    store.create(card)
    t0 = time.perf_counter()
    store.list(use_cache=True)
    return time.perf_counter() - t0


def bench_write_then_list_old(store: CardStore) -> float:
    """D-旧行为模拟: create 后手动失效 → 写后首查全量重载。返回耗时秒。"""
    card = make_card("bench-old")
    store.create(card)
    store._invalidate_list_cache()  # 模拟优化前的失效重载语义
    t0 = time.perf_counter()
    store.list(use_cache=True)
    return time.perf_counter() - t0


def verify_consistency(store: CardStore, n: int) -> bool:
    """E. 一致性: 随机 create/update/delete 后，缓存结果 == 磁盘重读结果。

    delete_many 走整体失效路径（重载），断言其正确性同样通过。
    """
    for i in range(n):
        op = random.choice(("create", "update", "delete", "delete_many"))
        if op == "create":
            store.create(make_card(f"v{i}"))
        elif op == "update":
            s = f"c{i % 3}"
            if store.get(s) is not None:  # 可能已被 delete 删除，存在才更新
                store.update(make_card(s, status="draft", type_=("concepts", "entities", "insights")[i % 3]))
        elif op == "delete":
            store.delete(f"c{i % 3}")  # 孤立卡（links 为空）无入链，删除成功
        else:  # delete_many
            store.delete_many([f"c{i % 5}", f"c{(i + 1) % 5}"])
    cached = store.list(use_cache=True)
    disk = store.list()
    c_snapshot = [(c.slug, c.status, c.type) for c in cached]
    d_snapshot = [(c.slug, c.status, c.type) for c in disk]
    return c_snapshot == d_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="store.list() 内存缓存优化前后对比")
    parser.add_argument("--cards", type=int, default=20000, help="卡片总数（10 万级用 --cards 100000 彻底验证）")
    args = parser.parse_args()

    total = args.cards
    tmp = Path(tempfile.mkdtemp(prefix="listcmp_"))
    try:
        print(f"=== store.list() 内存缓存优化对比（{total} 卡） ===")
        store = build_store(tmp, total)

        t_cold = bench_list_cold(store)
        print(f"[A] 无缓存冷读 list():           {t_cold * 1000:8.0f}ms")

        t_first = bench_cache_hit(store)  # 内部已含首次加载，单独计时
        # 单独测首次加载
        store2 = build_store(tmp / "s2", total)
        t0 = time.perf_counter()
        store2.list(use_cache=True)
        t_first = time.perf_counter() - t0
        print(f"[B] use_cache 首次加载:          {t_first * 1000:8.0f}ms")

        t_hit = bench_cache_hit(store)
        speedup = t_cold / t_hit if t_hit > 0 else float("inf")
        print(f"[C] use_cache 缓存命中(中位数):  {t_hit * 1000:8.0f}ms  (≈{speedup:.0f}x vs A)")

        t_new = bench_write_then_list_new(store)
        t_old = bench_write_then_list_old(store)
        ratio = t_old / t_new if t_new > 0 else float("inf")
        print(f"[D] 写后首查 - 优化后(增量同步): {t_new * 1000:8.0f}ms")
        print(f"[D] 写后首查 - 优化前(失效重载): {t_old * 1000:8.0f}ms  (优化后 ≈{ratio:.0f}x)")

        ok = verify_consistency(store, 60)
        print(f"[E] 一致性校验(随机写60次后对比): {'PASS' if ok else 'FAIL'}")
        sys.exit(0 if ok else 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("=== 对比完成 ===")


if __name__ == "__main__":
    main()
