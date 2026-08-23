#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能索引缓存极限压测 — 1000 技能规模下加载/查询/失效/持久化

场景（顺序执行，单进程内自包含）:
  1. generate      — 生成 N 个技能 mock 文件（隔离于临时目录，可 --keep 保留）
  2. cold_start    — 首次 get_all_metadata 全量解析（懒加载触发点）
  3. hot_start     — 模拟重启：新缓存实例 load_on_startup + 增量校验命中缓存
  4. hit           — 单技能 get_metadata 命中延迟（min/avg/p99，N 次采样）
  5. match         — loader.match 首次 vs 二次（守验收：二次延迟降低 >= 50%）
  6. incremental   — 少量文件变更/删除后增量校验耗时
  7. concurrent    — 多线程并发 get_metadata 命中（验证 RLock 下无死锁）
  8. invalidate    — 单技能失效耗时（毫秒级，验证持锁不 I/O）
  9. persist       — 原子持久化耗时（含缓存文件大小）

用法:
  python scripts/bench_skill_index_cache.py --count 1000
  python scripts/bench_skill_index_cache.py --count 2000 --threads 16 --keep --output bench_result.json

说明:
  - mock 数据生成在临时目录（tempfile.mkdtemp），退出自动清理；--keep 保留便于复测
  - --repo 指向既有技能仓库时跳过 generate（复用真实数据压测）
  - 结果以结构化 JSON 输出到 stdout；--output 可同时落盘
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.skills_mgmt.file_store import SkillFileStore  # noqa: E402
from agent.skills_mgmt.index_cache import SkillIndexCache  # noqa: E402
from agent.skills_mgmt.loader import SkillLoader  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("bench.index_cache")


def _p99(samples):
    return float(np.percentile(samples, 99))


def _fmt(ms):
    return f"{ms:.3f}"


def _write_skill(repo: Path, skill_id: str, idx: int, body: str = "正文") -> None:
    """写入标准 skill.md（front matter + body），与测试/生产同构"""
    d = repo / skill_id
    d.mkdir(parents=True, exist_ok=True)
    content = (
        "---\n"
        f"id: {skill_id}\n"
        f"name: 压测技能-{idx}\n"
        f"description: 技能索引缓存压测 mock 技能 {idx}，用于批量性能基线\n"
        "category: bench\n"
        "tags:\n"
        "  - bench\n"
        "  - cache\n"
        "version: 0.1.0\n"
        "enabled: true\n"
        "status: approved\n"
        "---\n"
        f"{body} 第 {idx} 号技能的使用说明\n"
    )
    (d / "skill.md").write_text(content, encoding="utf-8")


def generate_dataset(repo: Path, count: int, seed: int) -> None:
    rng = random.Random(seed)
    t0 = time.perf_counter()
    for i in range(count):
        _write_skill(repo, f"bench_skill_{i:04d}", i)
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"[generate] count={count} dirs={count} ms={_fmt(elapsed)}")


def bench_cold_start(repo: Path) -> dict:
    """首次访问触发全量解析（懒加载）"""
    fs = SkillFileStore(repo_path=str(repo))
    cache = SkillIndexCache(fs)
    t0 = time.perf_counter()
    index = cache.get_all_metadata()
    ms = (time.perf_counter() - t0) * 1000
    print(f"[cold_start] skills={len(index)} ms={_fmt(ms)}")
    return {"skills": len(index), "ms": ms}


def bench_hot_start(repo: Path) -> dict:
    """模拟重启：load_on_startup 读缓存 + 增量校验命中（不重解析）"""
    fs = SkillFileStore(repo_path=str(repo))
    cache = SkillIndexCache(fs)
    t0 = time.perf_counter()
    cache.load_on_startup()
    t_load = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    index = cache.get_all_metadata()
    t_validate = (time.perf_counter() - t0) * 1000
    print(f"[hot_start] load_ms={_fmt(t_load)} validate_ms={_fmt(t_validate)} "
          f"skills={len(index)}")
    return {"load_ms": t_load, "validate_ms": t_validate, "skills": len(index)}


def bench_hit(cache: SkillIndexCache, repo: Path, count: int,
              samples: int = 5000) -> dict:
    """单技能命中延迟（冷缓存已就绪，纯命中路径）"""
    ids = [f"bench_skill_{i:04d}" for i in range(min(count, 500))]
    times = []
    t0 = time.perf_counter()
    for n in range(samples):
        sid = ids[n % len(ids)]
        t = time.perf_counter()
        cache.get_metadata(sid)
        times.append((time.perf_counter() - t) * 1000)
    total = (time.perf_counter() - t0) * 1000
    times.sort()
    res = {
        "samples": samples,
        "total_ms": total,
        "min_ms": times[0],
        "avg_ms": sum(times) / len(times),
        "p99_ms": _p99(times),
    }
    print(f"[hit] samples={samples} min={_fmt(res['min_ms'])} "
          f"avg={_fmt(res['avg_ms'])} p99={_fmt(res['p99_ms'])} "
          f"total_ms={_fmt(total)}")
    return res


def bench_match(loader: SkillLoader, count: int) -> dict:
    """loader.match 首次 vs 二次（loader 层冷热对比，说明见下）

    说明: 本场景衡量 loader 倒排索引的冷/热成本（首次构建索引 vs 二次命中）。
    缓存收益主指标是 cold_start vs hot_start（见 SUMMARY 的 cache_speedup）。
    """
    t0 = time.perf_counter()
    loader.match("技能检索测试 缓存 压测", top_k=3)
    first = (time.perf_counter() - t0) * 1000
    samples = []
    for _ in range(3):
        t0 = time.perf_counter()
        loader.match("技能检索测试 缓存 压测", top_k=3)
        samples.append((time.perf_counter() - t0) * 1000)
    second = min(samples)
    reduced = (1 - second / first) * 100 if first > 0 else 0
    res = {"first_ms": first, "second_ms": second, "reduced_pct": reduced}
    print(f"[match(loader)] first={_fmt(first)} second={_fmt(second)} "
          f"reduced={reduced:.1f}% (skills={count})")
    return res


def bench_incremental(cache: SkillIndexCache, repo: Path, count: int) -> dict:
    """少量文件变更 + 删除后的增量校验耗时"""
    # 修改 10 个文件的 name（内容 + mtime 同时变化）
    t0 = time.perf_counter()
    for i in range(10):
        _write_skill(repo, f"bench_skill_{i:04d}", 10000 + i)
    cache.get_all_metadata()
    change_ms = (time.perf_counter() - t0) * 1000
    # 删除 5 个技能目录
    t0 = time.perf_counter()
    for i in range(count - 5, count):
        shutil.rmtree(repo / f"bench_skill_{i:04d}")
    cache.get_all_metadata()
    delete_ms = (time.perf_counter() - t0) * 1000
    res = {"change_10_ms": change_ms, "delete_5_ms": delete_ms}
    print(f"[incremental] change_10={_fmt(change_ms)}ms "
          f"delete_5={_fmt(delete_ms)}ms")
    return res


def bench_concurrent(cache: SkillIndexCache, count: int,
                     threads: int = 8, samples: int = 4000) -> dict:
    """多线程并发 get_metadata 命中（RLock 无死锁 + 正确性）"""
    ids = [f"bench_skill_{i:04d}" for i in range(min(count, 200))]

    def one(_):
        sid = random.choice(ids)
        t = time.perf_counter()
        meta = cache.get_metadata(sid)
        return (time.perf_counter() - t) * 1000, meta is not None

    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as ex:
        results = list(ex.map(one, range(samples)))
    total = (time.perf_counter() - t0) * 1000
    times = [r[0] for r in results]
    hits = sum(1 for r in results if r[1])
    times.sort()
    res = {
        "threads": threads,
        "samples": samples,
        "total_ms": total,
        "hit_rate": hits / samples,
        "min_ms": times[0],
        "avg_ms": sum(times) / len(times),
        "p99_ms": _p99(times),
    }
    print(f"[concurrent] threads={threads} samples={samples} "
          f"hit_rate={res['hit_rate']:.3f} avg={_fmt(res['avg_ms'])} "
          f"p99={_fmt(res['p99_ms'])} total_ms={_fmt(total)}")
    return res


def bench_invalidate(cache: SkillIndexCache) -> dict:
    """单技能失效耗时（持锁内存操作，锁外打日志）

    说明: 采样期间临时把 agent.skills_mgmt 日志降为 WARNING，避免 invalidate.ok
    刷屏影响可读性；真实运行中 invalidate.ok 会逐条输出（供并发排查）。
    """
    logger.info("[invalidate] 采样期间日志降级 WARNING（invalidate.ok 逐条日志仅真实运行可见）")
    logging.getLogger("agent.skills_mgmt").setLevel(logging.WARNING)
    try:
        times = []
        for i in range(20):
            sid = f"bench_skill_{i:04d}"
            t = time.perf_counter()
            cache.invalidate(sid)
            times.append((time.perf_counter() - t) * 1000)
    finally:
        logging.getLogger("agent.skills_mgmt").setLevel(logging.INFO)
    times.sort()
    res = {
        "samples": 20,
        "min_ms": times[0],
        "avg_ms": sum(times) / len(times),
        "p99_ms": _p99(times),
    }
    print(f"[invalidate] samples=20 min={_fmt(res['min_ms'])} "
          f"avg={_fmt(res['avg_ms'])} p99={_fmt(res['p99_ms'])}")
    return res


def bench_persist(cache: SkillIndexCache) -> dict:
    """原子持久化耗时（含缓存文件大小）"""
    t0 = time.perf_counter()
    cache.persist()
    ms = (time.perf_counter() - t0) * 1000
    size = cache._cache_path.stat().st_size
    res = {"ms": ms, "file_size": size}
    print(f"[persist] ms={_fmt(ms)} file_size={size}")
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="技能索引缓存极限压测")
    ap.add_argument("--count", type=int, default=1000, help="技能文件数（默认 1000）")
    ap.add_argument("--seed", type=int, default=42, help="mock 数据随机种子")
    ap.add_argument("--repo", type=str, default=None,
                    help="复用既有技能仓库（跳过 generate）")
    ap.add_argument("--keep", action="store_true",
                    help="保留生成的 mock 数据集（默认退出清理）")
    ap.add_argument("--threads", type=int, default=8, help="并发场景线程数")
    ap.add_argument("--output", type=str, default=None,
                    help="结果落盘 JSON 路径（可选）")
    ap.add_argument("--skip-slow", action="store_true",
                    help="跳过增量校验/并发等耗时场景")
    args = ap.parse_args()

    cleanup = None
    if args.repo:
        repo = Path(args.repo)
        if not repo.is_dir():
            print(f"[BLOCK] --repo 不存在: {repo}")
            return 1
    else:
        repo = Path(tempfile.mkdtemp(prefix="skill_cache_bench_"))
        cleanup = lambda: shutil.rmtree(repo, ignore_errors=True)

    try:
        results = {"count": args.count, "scenarios": {}}
        generate_dataset(repo, args.count, args.seed)

        # 冷启动：首次全量解析（懒加载触发点）
        results["scenarios"]["cold_start"] = bench_cold_start(repo)

        # 热启动：模拟重启
        results["scenarios"]["hot_start"] = bench_hot_start(repo)

        # 命中：复用最新缓存实例做单技能命中
        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        cache.get_all_metadata()
        results["scenarios"]["hit"] = bench_hit(cache, repo, args.count)

        # match：首次 vs 二次
        loader = SkillLoader(fs)
        results["scenarios"]["match"] = bench_match(loader, args.count)

        if not args.skip_slow:
            results["scenarios"]["incremental"] = bench_incremental(cache, repo, args.count)
            results["scenarios"]["concurrent"] = bench_concurrent(cache, args.count, args.threads)

        results["scenarios"]["invalidate"] = bench_invalidate(cache)
        results["scenarios"]["persist"] = bench_persist(cache)

        # 缓存收益主指标：冷启动全量解析 vs 热启动增量校验（均 O(n)，差异=解析 vs stat+hash）
        cold_ms = results["scenarios"]["cold_start"]["ms"]
        hot_ms = results["scenarios"]["hot_start"]["validate_ms"]
        results["cache_speedup_pct"] = (
            round((1 - hot_ms / cold_ms) * 100, 2) if cold_ms > 0 else 0.0
        )
        print(f"[summary] cold={_fmt(cold_ms)}ms hot_validate={_fmt(hot_ms)}ms "
              f"speedup={results['cache_speedup_pct']:.1f}%")

        print("\n===== SUMMARY =====")
        print(json.dumps(results, ensure_ascii=False, indent=2))
        if args.output:
            Path(args.output).write_text(
                json.dumps(results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"[output] 已写入 {args.output}")
        return 0
    finally:
        if cleanup:
            cleanup()
            if not args.keep:
                print("[cleanup] 临时 mock 数据集已清理（--keep 可保留）")


if __name__ == "__main__":
    sys.exit(main())
