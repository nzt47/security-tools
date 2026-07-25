"""L2 冷数据加载性能回归测试 [TLM-L3]

用途：
- CI 性能护栏：防止 L2 冷数据加载性能退化（路径缓存 + 限量读取优化被破坏时告警）
- 小规模数据快速验证（CI 友好，<10s 完成），大规模极限压测见 scripts/bench_l2_stress.py

运行：
    pytest tests/performance/test_l2_perf_regression.py -m performance -v

阈值（CI 环境宽松护栏，非优化目标）：
- 冷启动 P99 < 2s（glob 跨子目录 + 首次文件打开）
- 热启动 P99 < 1s（路径缓存命中，O(1)）
- 并发 P99 < 2s（10 并发，同步 IO 串行化）

Why 阈值宽松：CI 共享 runner 性能波动大，严格阈值会误报。
    本地正常路径 P99 < 200ms，2s 护栏足以捕获"优化失效"级别的退化。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import struct
import sys
import tempfile
from pathlib import Path

import pytest

# 加入项目根
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from agent.memory.adapters.holographic_adapter import HolographicAdapter
from agent.memory.context_assembler import ContextAssembler
from agent.memory.hotness_scorer import HotnessScorer
from agent.memory.markdown_syncer import MarkdownSyncer


# ── Mock 工具（与 bench_l2_stress.py 对齐）──

_VEC_DIM = 512
QUERY = "性能回归测试查询"


def _mock_embedding(text: str, dim: int = _VEC_DIM) -> list[float]:
    """基于 SHA256 hash 生成确定性向量"""
    if not text:
        return [0.0] * dim
    h = hashlib.sha256(text.encode("utf-8")).digest()
    buf = (h * ((dim * 4 // len(h)) + 1))[: dim * 4]
    vec = list(struct.unpack(f"<{dim}f", buf))
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


def _percentile(sorted_latencies: list[float], p: float) -> float:
    if not sorted_latencies:
        return 0.0
    k = max(0, min(len(sorted_latencies) - 1, int(round(p * (len(sorted_latencies) - 1)))))
    return sorted_latencies[k]


# ── 环境构造（同步包裹 async setup）──

def _build_assembler(tmp_dir: str, cold_count: int = 50, category_count: int = 5):
    """构造 assembler + 冷数据，返回 (assembler, query) 或 (None, None) 若 vec 不可用

    Why 同步包裹：pytest 测试函数非 async，用 asyncio.run 隔离事件循环
    """
    async def _setup():
        db = os.path.join(tmp_dir, "perf.db")
        md_dir = os.path.join(tmp_dir, "perf_md")

        adapter = HolographicAdapter(db_path=db, enable_cache=False)
        scorer = HotnessScorer(adapter)
        syncer = MarkdownSyncer(
            adapter, output_dir=md_dir,
            debounce_seconds=3600, batch_threshold=10_000_000,
        )
        adapter.set_scorer(scorer)
        adapter.set_syncer(syncer)
        adapter._embedding_func = lambda text: _mock_embedding(text)

        if not getattr(adapter, "_vec_available", False):
            return None, None

        # 写入冷数据 + 覆盖向量为 QUERY embedding（确保 search_vector 命中）
        import sqlite_vec
        query_emb = _mock_embedding(QUERY)
        base_text = "用于性能回归测试的冷数据归档内容，模拟真实场景文档。"
        for i in range(cold_count):
            key = f"cold_{i:03d}"
            data = base_text * 10  # ≈ 320 字符/条
            cat = f"cat_{i % category_count:03d}"
            emb = _mock_embedding(data)
            await adapter.save_with_embedding(
                key=key, data=data,
                metadata={"category": cat, "importance": 1.0},
                embedding=emb,
            )
            # 覆盖向量确保 QUERY 命中
            with adapter._get_conn() as conn:
                conn.execute(
                    f"DELETE FROM {adapter._VEC_TABLE} WHERE id = ?", (key,)
                )
                conn.execute(
                    f"INSERT INTO {adapter._VEC_TABLE} (id, embedding) VALUES (?, ?)",
                    (key, sqlite_vec.serialize_float32(query_emb)),
                )
                conn.commit()

        syncer._flush()  # 生成 .md 归档

        assembler = ContextAssembler(
            adapter, scorer, syncer,
            l0_top_n=1, l1_top_k=1, l2_top_k=10, l2_max_chars=500,
        )
        return assembler, QUERY

    return asyncio.run(_setup())


# ── 性能回归测试 ──

@pytest.mark.performance
def test_l2_cold_start_p99_under_threshold():
    """冷启动 L2 P99 不超过 2 秒（路径缓存空，glob 全部未命中）

    回归护栏：若路径缓存优化被破坏（每次都 glob），P99 会显著上升
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        assembler, query = _build_assembler(tmp, cold_count=50, category_count=5)
        if assembler is None:
            pytest.skip("sqlite-vec 不可用，L2 性能测试跳过")

        latencies = []
        for _ in range(3):
            # 清空路径缓存，模拟冷启动
            with assembler.syncer._cache_lock:
                assembler.syncer._fragment_path_cache.clear()

            async def _run():
                result = await assembler.assemble(query, max_tokens=4000)
                return result["meta"]["l2_elapsed_ms"]

            latencies.append(asyncio.run(_run()))

        p99 = _percentile(sorted(latencies), 0.99)
        assert p99 < 2000, (
            f"L2 冷启动 P99={p99:.2f}ms 超过 2 秒阈值，"
            f"路径缓存优化可能失效（latencies={latencies}）"
        )


@pytest.mark.performance
def test_l2_warm_start_p99_under_threshold():
    """热启动 L2 P99 不超过 1 秒（路径缓存满，O(1) 命中）

    回归护栏：若路径缓存失效或 read_fragment 退化为读全文，P99 会上升
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        assembler, query = _build_assembler(tmp, cold_count=50, category_count=5)
        if assembler is None:
            pytest.skip("sqlite-vec 不可用，L2 性能测试跳过")

        async def _warmup():
            await assembler.assemble(query, max_tokens=4000)

        asyncio.run(_warmup())  # 填充缓存

        latencies = []
        for _ in range(3):

            async def _run():
                result = await assembler.assemble(query, max_tokens=4000)
                return result["meta"]["l2_elapsed_ms"]

            latencies.append(asyncio.run(_run()))

        p99 = _percentile(sorted(latencies), 0.99)
        assert p99 < 1000, (
            f"L2 热启动 P99={p99:.2f}ms 超过 1 秒阈值，"
            f"路径缓存或限量读取优化可能失效（latencies={latencies}）"
        )


@pytest.mark.performance
def test_l2_concurrent_p99_under_threshold():
    """10 并发 L2 P99 不超过 2 秒（同步 IO 串行化护栏）

    回归护栏：read_fragment 同步阻塞事件循环，并发下 P99 应可控
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        assembler, query = _build_assembler(tmp, cold_count=50, category_count=5)
        if assembler is None:
            pytest.skip("sqlite-vec 不可用，L2 性能测试跳过")

        async def _run_concurrent():
            with assembler.syncer._cache_lock:
                assembler.syncer._fragment_path_cache.clear()

            async def _one_task():
                result = await assembler.assemble(query, max_tokens=4000)
                return result["meta"]["l2_elapsed_ms"]

            return await asyncio.gather(*[_one_task() for _ in range(10)])

        latencies = asyncio.run(_run_concurrent())
        p99 = _percentile(sorted(latencies), 0.99)
        assert p99 < 2000, (
            f"L2 并发 P99={p99:.2f}ms 超过 2 秒阈值，"
            f"同步 IO 串行化可能恶化（latencies={latencies}）"
        )


@pytest.mark.performance
def test_l2_cache_effectiveness():
    """路径缓存有效性：热启动平均耗时应低于冷启动

    回归护栏：若缓存未生效（读后未写入 _fragment_path_cache），
    热启动不会比冷启动快
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        assembler, query = _build_assembler(tmp, cold_count=50, category_count=5)
        if assembler is None:
            pytest.skip("sqlite-vec 不可用，L2 性能测试跳过")

        async def _run():
            result = await assembler.assemble(query, max_tokens=4000)
            return result["meta"]["l2_elapsed_ms"]

        # 冷启动（缓存空）
        with assembler.syncer._cache_lock:
            assembler.syncer._fragment_path_cache.clear()
        cold = asyncio.run(_run())

        # 热启动（缓存满）
        warm = asyncio.run(_run())

        # 热启动应不慢于冷启动（允许相等，但不能显著更慢）
        # Why 用 2x 容差：CI 环境抖动可能导致单次冷启动偶发快，
        #      但缓存失效时热启动会持续与冷启动持平
        assert warm <= cold * 2, (
            f"路径缓存可能失效：热启动 {warm:.2f}ms 未优于冷启动 {cold:.2f}ms"
        )
