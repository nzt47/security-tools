#!/usr/bin/env python3
"""
向量重生成性能基准：ThreadPoolExecutor 配置对比

目的：分析 migrate_to_tlm.py regenerate_vectors 的性能瓶颈，
对比 workers × encode_batch 组合的吞吐量，确定最优配置。

方法：
- 构造 N 条中文文本（模拟记忆内容）
- 加载本地缓存模型（优先 bge-small-zh-v1.5，回退 bge-m3）模拟 CPU 推理
- 对每个 (workers, encode_batch) 组合测速
- 输出对比表 + 结论

用法：
    python scripts/benchmark_tlm_regen.py --n 200
    python scripts/benchmark_tlm_regen.py --n 200 --workers "1 2 4" --batches "1 16 64"

说明：
- 用 ThreadPoolExecutor 包装 encode_batch（与 migrate_to_tlm.py 相同的并行模型）
- encode_batch=1 表示逐条编码（旧行为），>1 表示批量推理
- 结论以相对加速比为准（不同模型绝对耗时不同，加速规律一致）
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 确保项目根目录在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def make_texts(n: int) -> list[str]:
    """构造 N 条中文模拟记忆文本"""
    templates = [
        "用户询问了关于天气的信息，需要查询今日天气预报",
        "系统记录了用户的偏好设置：偏好中文交互",
        "任务提醒：明天下午三点有项目评审会议",
        "用户反馈了一个 bug：界面在移动端显示异常",
        "知识条目：Python 的 GIL 限制了多线程性能",
        "配置变更：启用了 TLM 三表统一记忆层",
        "错误日志：sqlite-vec 扩展加载失败，降级为纯 FTS5",
    ]
    return [f"[{i:04d}] {templates[i % len(templates)]} (序号 {i})" for i in range(n)]


def load_encoder() -> object:
    """加载本地编码器（离线模式，优先本地缓存的 bge 模型）

    HF_HUB_OFFLINE=1 强制离线，避免无网络时卡在 huggingface 重试（默认 5 次 x 指数退避）。
    """
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("[ERROR] sentence_transformers 未安装")
        sys.exit(1)

    # 本地缓存优先（bge-small-zh-v1.5 若已缓存则用它，否则用 bge-m3）
    # 结论以相对加速比为准：同架构 CPU 推理下批量/多线程加速规律一致
    candidates = ["BAAI/bge-small-zh-v1.5", "BAAI/bge-m3"]
    for name in candidates:
        try:
            print(f"加载模型: {name} ...")
            t0 = time.perf_counter()
            model = SentenceTransformer(name)
            dim = model.get_embedding_dimension()
            print(f"  模型 {name} 加载成功, dim={dim}, 耗时={time.perf_counter()-t0:.1f}s")
            return model
        except Exception as e:
            print(f"  {name} 加载失败: {type(e).__name__}: {e}（可能未缓存）")

    print("[ERROR] 无可用本地模型，请先缓存 bge-small-zh-v1.5 或 bge-m3")
    sys.exit(1)


def bench_combo(
    model: object, texts: list[str], workers: int, encode_batch: int
) -> tuple[float, float]:
    """测单个组合：返回 (耗时s, 吞吐量 items/s)"""
    # 预热（batch 推理初始化）
    model.encode(texts[:4], normalize_embeddings=True)

    # 均分 chunks（与 migrate_to_tlm.py regenerate_vectors 相同的分片策略）
    chunks = [texts[i::workers] for i in range(workers) if texts[i::workers]]

    def worker_fn(chunk: list[str]) -> int:
        total = 0
        for i in range(0, len(chunk), encode_batch):
            sub = chunk[i:i + encode_batch]
            model.encode(sub, normalize_embeddings=True)
            total += len(sub)
        return total

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker_fn, c) for c in chunks]
        done = sum(f.result() for f in futures)
    elapsed = time.perf_counter() - t0

    throughput = done / elapsed if elapsed > 0 else 0
    return (elapsed, throughput)


def main() -> int:
    parser = argparse.ArgumentParser(description="向量重生成性能基准")
    parser.add_argument("--n", type=int, default=200, help="文本条数（默认 200）")
    parser.add_argument("--workers", default="1 2 4", help="workers 组合，空格分隔")
    parser.add_argument("--batches", default="1 16 64", help="encode_batch 组合，空格分隔")
    args = parser.parse_args()

    texts = make_texts(args.n)
    model = load_encoder()

    workers_list = [int(x) for x in args.workers.split()]
    batch_list = [int(x) for x in args.batches.split()]

    print(f"\n{'='*76}")
    print(f"基准: {len(texts)} 条文本, torch 线程数 6")
    print(f"{'='*76}")

    results = []
    for w in workers_list:
        for b in batch_list:
            elapsed, tput = bench_combo(model, texts, w, b)
            results.append((w, b, elapsed, tput))
            print(f"workers={w:<2} encode_batch={b:<3} 耗时={elapsed:6.2f}s 吞吐={tput:8.1f} items/s")

    # 对比基线：workers=1, encode_batch=1
    base = next(r for r in results if r[0] == 1 and r[1] == 1)
    base_tput = base[3]

    print(f"\n{'='*76}")
    print("加速比（相对 workers=1, encode_batch=1）:")
    for w, b, elapsed, tput in results:
        ratio = tput / base_tput if base_tput > 0 else 0
        print(f"  workers={w:<2} encode_batch={b:<3} 加速比={ratio:5.2f}x")
    print(f"{'='*76}")

    # 结论：最优组合
    best = max(results, key=lambda r: r[3])
    print(f"\n结论: 最优组合 workers={best[0]}, encode_batch={best[1]}, "
          f"吞吐 {best[3]:.1f} items/s（加速 {best[3]/base_tput:.2f}x）")
    print("注: 若 encode_batch=1 时多 worker 无加速甚至变慢，说明瓶颈是 torch 底层线程争抢，")
    print("    应优先增大 encode_batch（批量推理），而非增大 workers。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
