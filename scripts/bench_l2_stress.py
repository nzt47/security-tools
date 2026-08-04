"""L2 冷数据加载极限性能压测脚本 [TLM-L3]

用途：
- 构造大规模冷数据 + 高并发读，触发 MarkdownSyncer.read_fragment 极限瓶颈
- 采集 P50/P99/Max/QPS，定位 L2 冷数据加载性能拐点

压测场景：
- 场景 A 冷启动：路径缓存空，首次 assemble 触发 N 个 glob 未命中（O(子目录数)）
- 场景 B 热启动：路径缓存满，第二次 assemble 全部 O(1) 命中
- 场景 C 高并发：K 个 assemble 并发（_cache_lock 竞争 + 同步 IO 阻塞事件循环）
- 场景 D 大 fragment：单条 data 20KB+，验证限量读取（max_chars*4 字节）效果

运行：
    python scripts/bench_l2_stress.py
    python scripts/bench_l2_stress.py --cold-count 1000 --category-count 50 --concurrency 20
    python scripts/bench_l2_stress.py --fragment-size large

退出码：0 全部场景完成；1 环境依赖缺失
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import statistics
import struct
import sys
import tempfile
import time
from pathlib import Path

# 加入项目根（便于直接 python scripts/xxx.py 运行）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from agent.memory.adapters.holographic_adapter import HolographicAdapter
from agent.memory.context_assembler import ContextAssembler
from agent.memory.hotness_scorer import HotnessScorer
from agent.memory.markdown_syncer import MarkdownSyncer


# ── Mock embedding（与 verify_tlm_three_layers.py 对齐）──

_VEC_DIM = 512


def mock_embedding(text: str, dim: int = _VEC_DIM) -> list[float]:
    """基于 SHA256 hash 生成确定性向量（相同 text → 相同向量）"""
    if not text:
        return [0.0] * dim
    h = hashlib.sha256(text.encode("utf-8")).digest()
    buf = (h * ((dim * 4 // len(h)) + 1))[: dim * 4]
    vec = list(struct.unpack(f"<{dim}f", buf))
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


# ── 锁竞争统计包装器（非侵入式采集 _cache_lock 等待/持锁时长）──

class LockStatsWrapper:
    """包装 threading.Lock，采集 acquire 等待时长与持锁时长

    Why: 高并发场景下 _cache_lock 竞争是潜在瓶颈，
         用数据验证锁等待是否贡献了 P99 抖动。
         非侵入式——不修改 MarkdownSyncer 源码，仅替换 _cache_lock 实例。
    """

    def __init__(self, lock):
        self._lock = lock
        self._wait_ms: list[float] = []  # acquire 等待时长（ms）
        self._hold_ms: list[float] = []  # 持锁时长（ms）
        self._acquired_t: float | None = None

    def acquire(self, blocking=True, timeout=-1):
        t0 = time.time()
        ok = self._lock.acquire(blocking, timeout)
        if ok:
            wait = (time.time() - t0) * 1000.0
            self._wait_ms.append(wait)
            self._acquired_t = time.time()
        return ok

    def release(self):
        if self._acquired_t is not None:
            hold = (time.time() - self._acquired_t) * 1000.0
            self._hold_ms.append(hold)
            self._acquired_t = None
        return self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()

    def reset(self):
        """清空统计（用于场景间隔离）"""
        self._wait_ms.clear()
        self._hold_ms.clear()
        self._acquired_t = None

    def stats(self) -> dict:
        return {
            "acquire_count": len(self._wait_ms),
            "wait_ms": list(self._wait_ms),
            "hold_ms": list(self._hold_ms),
        }


# ── 极限 Mock 数据构造 ──

# fragment 大小档位：small/medium/large 对应单条 data 字符数
# Why: .md 文件 = front matter(≈200B) + data，data 越大文件越大，read 开销越高
FRAGMENT_SIZES = {
    "small": 200,    # 真实场景：短笔记
    "medium": 2000,  # 真实场景：中等文档
    "large": 20000,  # 真实场景：长归档（触发限量读取效果差异）
}

QUERY = "极限压测查询词"


def build_cold_records(count: int, category_count: int, fragment_size: int) -> list[dict]:
    """构造大规模冷数据

    策略：
    - count 条记录分散到 category_count 个子目录（触发 glob 跨目录开销）
    - 每条 data 长度 = fragment_size（控制 .md 文件大小）
    - access_count=0 确保不进 L0/L1（仅 L2 向量检索命中）
    """
    records = []
    # 用重复文本填充到目标长度（模拟真实长文档）
    base_text = "这是用于压测的冷数据归档内容，模拟真实场景下的长文档片段。"
    padding = base_text * (fragment_size // len(base_text) + 1)
    for i in range(count):
        cat_idx = i % max(1, category_count)
        records.append({
            "key": f"cold_{i:05d}",
            "data": padding[:fragment_size],
            "category": f"cat_{cat_idx:03d}",  # 分散到多个子目录
            "importance": 1.0,
        })
    return records


async def setup_cold_data(
    adapter: HolographicAdapter,
    syncer: MarkdownSyncer,
    records: list[dict],
) -> int:
    """写入冷数据 + 覆盖向量（确保 search_vector 命中）+ 触发 flush 生成 .md

    Returns: 成功写入的记录数
    """
    query_emb = mock_embedding(QUERY)
    ok_count = 0
    for rec in records:
        emb = mock_embedding(rec["data"])
        meta = {"category": rec["category"], "importance": rec["importance"]}
        ok = await adapter.save_with_embedding(
            key=rec["key"], data=rec["data"], metadata=meta, embedding=emb,
        )
        if ok:
            ok_count += 1

    # 覆盖 cold 记录的向量为 QUERY embedding，确保 search_vector 全部命中
    if getattr(adapter, "_vec_available", False):
        import sqlite_vec
        with adapter._get_conn() as conn:
            for rec in records:
                try:
                    conn.execute(
                        f"DELETE FROM {adapter._VEC_TABLE} WHERE id = ?",
                        (rec["key"],)
                    )
                    conn.execute(
                        f"INSERT INTO {adapter._VEC_TABLE} (id, embedding) VALUES (?, ?)",
                        (rec["key"], sqlite_vec.serialize_float32(query_emb))
                    )
                except Exception:
                    pass
            conn.commit()

    # 触发 flush 生成 .md 归档（L2 懒加载依赖 .md 文件存在）
    syncer._flush()
    return ok_count


# ── 性能指标采集 ──

def percentile(sorted_latencies: list[float], p: float) -> float:
    """计算 P 分位数（sorted_latencies 已升序）"""
    if not sorted_latencies:
        return 0.0
    k = max(0, min(len(sorted_latencies) - 1, int(round(p * (len(sorted_latencies) - 1)))))
    return sorted_latencies[k]


def report(title: str, latencies_ms: list[float], total_ms: float) -> None:
    """输出性能报告"""
    if not latencies_ms:
        print(f"  [{title}] 无样本")
        return
    s = sorted(latencies_ms)
    p50 = percentile(s, 0.50)
    p99 = percentile(s, 0.99)
    pmax = s[-1]
    qps = len(latencies_ms) / (total_ms / 1000.0) if total_ms > 0 else 0
    print(f"  [{title}]")
    print(f"    样本数: {len(latencies_ms)}")
    print(f"    P50:    {p50:.2f}ms")
    print(f"    P99:    {p99:.2f}ms")
    print(f"    Max:    {pmax:.2f}ms")
    print(f"    总耗时: {total_ms:.2f}ms")
    print(f"    吞吐:   {qps:.1f} ops/s")


# ── 压测场景 ──

async def scenario_a_cold_start(assembler: ContextAssembler, query: str) -> tuple[float, float]:
    """场景 A：冷启动（路径缓存空，首次 assemble 全部 glob 未命中）

    Returns: (l2_elapsed_ms, total_elapsed_ms)
    """
    # 清空路径缓存，模拟冷启动
    with assembler.syncer._cache_lock:
        assembler.syncer._fragment_path_cache.clear()
    t0 = time.time()
    result = await assembler.assemble(query, max_tokens=4000)
    total = (time.time() - t0) * 1000.0
    return result["meta"]["l2_elapsed_ms"], total


async def scenario_b_warm_start(assembler: ContextAssembler, query: str) -> tuple[float, float]:
    """场景 B：热启动（路径缓存满，全部 O(1) 命中）"""
    t0 = time.time()
    result = await assembler.assemble(query, max_tokens=4000)
    total = (time.time() - t0) * 1000.0
    return result["meta"]["l2_elapsed_ms"], total


async def scenario_c_concurrent(
    assembler: ContextAssembler, query: str, concurrency: int,
) -> tuple[list[float], float]:
    """场景 C：高并发（K 个 assemble 并发，_cache_lock 竞争 + IO 阻塞）

    Returns: (各任务 L2 耗时列表, 总耗时)
    """
    # 清空缓存，让所有并发任务都面临首次缓存竞争
    with assembler.syncer._cache_lock:
        assembler.syncer._fragment_path_cache.clear()

    async def _one_task() -> float:
        result = await assembler.assemble(query, max_tokens=4000)
        return result["meta"]["l2_elapsed_ms"]

    t0 = time.time()
    latencies = await asyncio.gather(*[_one_task() for _ in range(concurrency)])
    total = (time.time() - t0) * 1000.0
    return list(latencies), total


# ── 主流程 ──

async def main(args: argparse.Namespace) -> int:
    # 配置日志：仅 WARNING 以上，避免压测日志干扰性能数据
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    fragment_size = FRAGMENT_SIZES[args.fragment_size]
    records = build_cold_records(args.cold_count, args.category_count, fragment_size)

    print("=" * 72)
    print("【L2 冷数据加载极限性能压测】")
    print("=" * 72)
    print(f"冷数据量:     {args.cold_count} 条")
    print(f"category 数:  {args.category_count} 个子目录")
    print(f"fragment 大小: {args.fragment_size} ({fragment_size} 字符/条)")
    print(f"l2_top_k:     {args.l2_top_k}（单次 assemble 读取的 fragment 数）")
    print(f"并发度:       {args.concurrency}")
    print(f"压测轮次:     {args.rounds}")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = os.path.join(tmp, "stress.db")
        md_dir = os.path.join(tmp, "stress_md")

        adapter = HolographicAdapter(db_path=db, enable_cache=False)
        scorer = HotnessScorer(adapter)
        syncer = MarkdownSyncer(
            adapter, output_dir=md_dir,
            debounce_seconds=3600, batch_threshold=10_000_000,
        )
        adapter.set_scorer(scorer)
        adapter.set_syncer(syncer)
        adapter._embedding_func = lambda text: mock_embedding(text)

        vec_available = getattr(adapter, "_vec_available", False)
        if not vec_available:
            print("\n[!] sqlite-vec 不可用，L2 向量检索无法压测，退出")
            return 1

        print("\n【步骤 1】写入冷数据并生成 .md 归档...")
        t0 = time.time()
        ok = await setup_cold_data(adapter, syncer, records)
        print(f"  写入完成: {ok}/{args.cold_count} 条，耗时 {(time.time()-t0)*1000:.0f}ms")
        md_files = list(Path(md_dir).rglob("*.md"))
        print(f"  .md 归档文件数: {len(md_files)}")

        # 构造 assembler，l2_top_k 调大以触发更多 read_fragment
        assembler = ContextAssembler(
            adapter, scorer, syncer,
            l0_top_n=1, l1_top_k=1, l2_top_k=args.l2_top_k,
            l2_max_chars=500,
        )

        # 包装 _cache_lock，采集锁等待统计（非侵入式，仅替换实例）
        lock_stats = LockStatsWrapper(syncer._cache_lock)
        syncer._cache_lock = lock_stats

        # 预热向量检索（避免首次 search_vector 的扩展加载开销计入压测）
        # Why: search_vector 期望向量而非 query 字符串；传字符串会被当成字符序列导致维度不匹配
        await adapter.search_vector(mock_embedding(QUERY), top_k=1)

        # ── 场景 A：冷启动 ──
        print("\n【场景 A】冷启动（路径缓存空，glob 全部未命中）")
        cold_l2_list, cold_total_list = [], []
        for r in range(args.rounds):
            l2, total = await scenario_a_cold_start(assembler, QUERY)
            cold_l2_list.append(l2)
            cold_total_list.append(total)
        report(f"rounds={args.rounds}", cold_l2_list, sum(cold_total_list))

        # ── 场景 B：热启动 ──
        print("\n【场景 B】热启动（路径缓存满，O(1) 命中）")
        # 先跑一次填充缓存
        await scenario_b_warm_start(assembler, QUERY)
        warm_l2_list, warm_total_list = [], []
        for r in range(args.rounds):
            l2, total = await scenario_b_warm_start(assembler, QUERY)
            warm_l2_list.append(l2)
            warm_total_list.append(total)
        report(f"rounds={args.rounds}", warm_l2_list, sum(warm_total_list))

        # 缓存命中率对比
        if warm_l2_list and cold_l2_list:
            avg_cold = statistics.mean(cold_l2_list)
            avg_warm = statistics.mean(warm_l2_list)
            speedup = (avg_cold / avg_warm) if avg_warm > 0 else float("inf")
            print(f"\n  [缓存加速比] 冷启动 {avg_cold:.2f}ms → 热启动 {avg_warm:.2f}ms "
                  f"(x{speedup:.1f})")

        # ── 场景 C：高并发 ──
        print(f"\n【场景 C】高并发（{args.concurrency} 个 assemble 并发，同步 IO）")
        lock_stats.reset()  # 隔离场景 C 的锁统计（排除场景 A/B 的清缓存操作）
        conc_l2_list, conc_total = await scenario_c_concurrent(
            assembler, QUERY, args.concurrency,
        )
        report(f"concurrency={args.concurrency}", conc_l2_list, conc_total)

        # 锁竞争统计：验证 _cache_lock 是否为高并发瓶颈
        ls = lock_stats.stats()
        if ls["wait_ms"]:
            print(f"\n【锁竞争统计】_cache_lock（场景 C, 并发度={args.concurrency}）")
            print(f"  acquire 次数: {ls['acquire_count']}")
            report("锁等待时长", ls["wait_ms"], sum(ls["wait_ms"]))
            report("持锁时长", ls["hold_ms"], sum(ls["hold_ms"]))
            # 锁等待占 L2 总耗时的比例（判断锁是否为瓶颈）
            total_wait = sum(ls["wait_ms"])
            total_l2 = sum(conc_l2_list)
            ratio = (total_wait / total_l2 * 100) if total_l2 > 0 else 0
            print(f"  锁等待占 L2 总耗时比: {ratio:.1f}% "
                  f"(wait={total_wait:.2f}ms / l2={total_l2:.2f}ms)")
            if ratio > 30:
                print(f"  [!] 锁等待占比超 30%，_cache_lock 是高并发瓶颈，建议异步化或减小锁粒度")
            else:
                print(f"  [✓] 锁等待占比低，瓶颈在别处（同步 IO 阻塞事件循环 / 磁盘抖动）")

        # ── 场景 D：大 fragment 限量读取效果（仅 large 档位有意义）──
        if args.fragment_size == "large":
            print("\n【场景 D】大 fragment 限量读取验证（data=20KB, max_chars=500）")
            print(f"  单条 .md 文件大小 ≈ {fragment_size + 200} 字节")
            print(f"  read_fragment 读取量 = max_chars*4 = {500*4} 字节（仅全文的 "
                  f"{500*4/(fragment_size+200)*100:.1f}%）")
            with assembler.syncer._cache_lock:
                assembler.syncer._fragment_path_cache.clear()
            t0 = time.time()
            result = await assembler.assemble(QUERY, max_tokens=4000)
            total = (time.time() - t0) * 1000.0
            print(f"  L2 耗时: {result['meta']['l2_elapsed_ms']:.2f}ms "
                  f"(读取 {result['meta']['l2_count']} 个 fragment)")
            print(f"  总耗时: {total:.2f}ms")

        # ── 结论 ──
        print("\n" + "=" * 72)
        print("【结论】")
        print("=" * 72)
        if cold_l2_list and warm_l2_list:
            avg_cold = statistics.mean(cold_l2_list)
            avg_warm = statistics.mean(warm_l2_list)
            p99_cold = percentile(sorted(cold_l2_list), 0.99)
            p99_warm = percentile(sorted(warm_l2_list), 0.99)
            print(f"  路径缓存效果: 冷启动 P99={p99_cold:.2f}ms → 热启动 P99={p99_warm:.2f}ms")
            if p99_cold > 1000:
                print(f"  [!] 冷启动 P99 超过 1 秒（{p99_cold:.2f}ms），建议检查 category 子目录数")
            if p99_warm > 1000:
                print(f"  [!] 热启动 P99 超过 1 秒（{p99_warm:.2f}ms），建议检查磁盘 IO 或并发度")
        if conc_l2_list:
            p99_conc = percentile(sorted(conc_l2_list), 0.99)
            print(f"  高并发 P99（同步 IO）: {p99_conc:.2f}ms (并发度={args.concurrency})")
            if p99_conc > 1000:
                print(f"  [!] 高并发 P99 超过 1 秒，L2 成为瓶颈，建议异步化 read_fragment")
        print("\n  压测完成。")

    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="L2 冷数据加载极限性能压测")
    p.add_argument("--cold-count", type=int, default=200,
                   help="冷数据记录数（默认 200）")
    p.add_argument("--category-count", type=int, default=20,
                   help="category 子目录数（默认 20，触发 glob 跨目录开销）")
    p.add_argument("--fragment-size", choices=list(FRAGMENT_SIZES.keys()),
                   default="medium",
                   help="单条 data 字符数档位：small(200)/medium(2000)/large(20000)")
    p.add_argument("--l2-top-k", type=int, default=20,
                   help="单次 assemble 读取的 fragment 数（默认 20）")
    p.add_argument("--concurrency", type=int, default=10,
                   help="高并发场景的并发度（默认 10）")
    p.add_argument("--rounds", type=int, default=3,
                   help="场景 A/B 的压测轮次（默认 3）")
    return p.parse_args()


if __name__ == "__main__":
    try:
        rc = asyncio.run(main(parse_args()))
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)
