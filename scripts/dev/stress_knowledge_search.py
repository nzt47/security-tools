"""知识检索高并发稳定性压测脚本（任务4 验证加固轮 · search 并发 / 读写锁 / 耗时日志）。

验证目标（对应并发排查结论）：
1. 多线程同时调用 search / search_async：不抛异常、无状态污染、结果确定性
   （同一 query 各线程 hit slug 序列与单线程基准一致）。
2. 读写混跑：写线程并发 create/update/delete，读线程并发 search
   （search 热路径 _link_recall→resolve_link 会读 CardStore.get），验证
   CardStore 读写锁（_RWLock）：写串行化、读不打断多步写、无死锁、库最终一致。
3. 采集 search_stage_timing 结构化日志，统计各阶段耗时（bm25/vector/link/
   rrf/rerank/assemble/total）min/avg/p95/max，定位性能瓶颈。

复用 scripts/dev/verify_knowledge_search.py 的复杂双链数据集 + fake 向量路：
query=「机器学习」→ 模型训练 BM25+向量两路累加 0.667 居首（RRF 多路累加），
结果确定可断言；写线程压测卡不入索引快照，不影响读线程结果确定性。

用法（Windows PowerShell）：
    $env:PYTHONIOENCODING="utf-8"
    python scripts/dev/stress_knowledge_search.py [--threads 16] [--iterations 20] [--writers 4] [--writer-ops 20]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import statistics
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import scripts.dev.verify_knowledge_search as vks  # noqa: E402  复用复杂数据集 + fake 向量路

from agent.knowledge import CardStore, KnowledgeHit, KnowledgeSearch  # noqa: E402

_STAGES = ("bm25", "vector", "link", "rrf", "rerank", "assemble", "total")
_QUERIES = ["机器学习", "烘焙", "调参技巧", "机器学习", "深度学习", "机器学习"]
_TIMEOUT_S = 180  # 整体超时兜底：线程卡死（死锁/无限等待）时报失败


class _TimingHandler(logging.Handler):
    """收集 search_stage_timing 结构化日志（挂到 search logger，控制台不刷屏）。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.timings: dict[str, list[float]] = {s: [] for s in _STAGES}

    def emit(self, record) -> None:
        try:
            msg = record.getMessage()
            if '"action": "search_stage_timing"' not in msg:
                return
            data = json.loads(msg)
            for stage, ms in data.get("ms", {}).items():
                if stage in self.timings:
                    self.timings[stage].append(float(ms))
        except Exception:
            pass  # 收集失败不影响压测主流程（日志仅为观测项）


def _check_hits(hits: list[KnowledgeHit]) -> None:
    """校验 hit 结构完整（并发下字段缺漏 = 数据竞争信号）。"""
    for h in hits:
        assert h.slug and h.title and h.source_ref and h.snippet, f"hit 字段缺失: {h}"
        assert isinstance(h.score, float) and 0.0 <= h.score <= 1.0, f"score 越界: {h.score}"
        assert isinstance(h.rerank_score, float), f"rerank_score 类型异常: {h.rerank_score}"


def _reader_task(searcher, tid, iterations, rng, baseline, use_async):
    """单个读线程：反复 search，校验结构 + 确定性（use_async 走 search_async 路径）。"""
    if use_async:
        async def loop():
            for _ in range(iterations):
                q = rng.choice(_QUERIES)
                hits = await searcher.search_async(q, 5)
                _check_hits(hits)
                if q == "机器学习":
                    seq = [h.slug for h in hits[:3]]
                    if seq != baseline[:3]:
                        raise AssertionError(f"结果漂移 tid={tid}: {seq} != {baseline[:3]}")
        asyncio.run(loop())
    else:
        for _ in range(iterations):
            q = rng.choice(_QUERIES)
            hits = searcher.search(q, 5)
            _check_hits(hits)
            if q == "机器学习":
                seq = [h.slug for h in hits[:3]]
                if seq != baseline[:3]:
                    raise AssertionError(f"结果漂移 tid={tid}: {seq} != {baseline[:3]}")
    return tid


def _writer_task(store, tid, ops):
    """单个写线程：create→get→update→delete 唯一 slug 卡（无入链必可删），验证读写锁。

    Why 卡名用「压测卡{tid}号{i}」：schema 校验要求 slug == slugify(title)，
    中文幂等；含 `-` 时 slugify 会剥离导致校验失败（verify._card 的 slug=title 契约）。
    """
    for i in range(ops):
        name = f"压测卡{tid}号{i}"
        store.create(vks._card(name, "压测写入卡片（测试环境占位）"))
        assert store.get(name) is not None, f"create 后 get 应返回卡片: {name}"
        store.update(vks._card(name, "压测写入卡片内容已更新"))
        assert store.delete(name) is True, f"delete 应成功: {name}"
        assert store.get(name) is None, f"delete 后 get 应为 None: {name}"
    return tid


def _new_env() -> tuple[CardStore, KnowledgeSearch]:
    """构造隔离的临时复杂知识库 + 检索器（含 fake 向量路，RRF 多路累加）。"""
    tmp = tempfile.mkdtemp(prefix="kb-stress-")
    wiki = Path(tmp) / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    store = vks.build_complex_wiki(wiki)
    vector = vks._KeywordVectorStore(store.list())
    searcher = KnowledgeSearch(store, vector_store=vector, min_score=0.3)
    return store, searcher


def _run_read_stress(searcher, threads, iterations) -> None:
    """场景 A：纯读并发。全部线程通过即无异常 + 结果确定。"""
    baseline = [h.slug for h in searcher.search("机器学习")]
    assert baseline, "基准结果为空，无法校验确定性"
    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [
            pool.submit(
                _reader_task, searcher, tid, iterations,
                random.Random(20_260_807 + tid), baseline, tid % 2 == 0,
            )
            for tid in range(threads)
        ]
        for fut in as_completed(futures):
            fut.result(timeout=_TIMEOUT_S)  # 超时/异常 → 场景失败


def _run_rw_mix(store, searcher, threads, iterations, writers, writer_ops) -> None:
    """场景 B：读写混跑。写线程验证 CRUD 全链路，结束后校验库最终一致。"""
    baseline = [h.slug for h in searcher.search("机器学习")]
    assert baseline
    initial = len(store.list())
    with ThreadPoolExecutor(max_workers=threads + writers) as pool:
        futures = [
            pool.submit(
                _reader_task, searcher, tid, iterations,
                random.Random(1_000 + tid), baseline, tid % 2 == 0,
            )
            for tid in range(threads)
        ]
        futures += [
            pool.submit(_writer_task, store, tid, writer_ops) for tid in range(writers)
        ]
        for fut in as_completed(futures):
            fut.result(timeout=_TIMEOUT_S)
    final = len(store.list())
    assert final == initial, f"读写混跑后库不一致: {initial} → {final}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="知识检索高并发稳定性压测")
    parser.add_argument("--threads", type=int, default=16, help="读线程数（默认 16）")
    parser.add_argument("--iterations", type=int, default=20, help="每读线程 search 次数（默认 20）")
    parser.add_argument("--writers", type=int, default=4, help="写线程数（默认 4）")
    parser.add_argument("--writer-ops", type=int, default=20, help="每写线程 CRUD 组数（默认 20）")
    args = parser.parse_args(argv)

    # 挂 timing handler：search logger 独立（propagate=False），控制台不刷 INFO 日志
    timing = _TimingHandler()
    sl = logging.getLogger("agent.knowledge.search")
    sl.setLevel(logging.INFO)
    sl.propagate = False
    sl.addHandler(timing)
    # 抑制写路径/双链解析的 INFO 日志（import verify 时 basicConfig 已把 root 设为 INFO，
    # 不抑制会刷屏；search 计时日志已由独立 handler 收集，不受影响）
    for _name in ("agent.knowledge.links", "agent.knowledge.card",
                  "agent.knowledge.index", "agent.knowledge.logbook"):
        logging.getLogger(_name).setLevel(logging.WARNING)

    try:
        print("════ 场景 A：纯读并发 ════")
        _, searcher = _new_env()
        _run_read_stress(searcher, args.threads, args.iterations)
        total_a = args.threads * args.iterations
        print(f"[OK] 读调用 {total_a} 次全部通过（{args.threads} 线程并发，"
              f"半数为 search_async 路径，无异常、结果确定性一致）")

        print("\n════ 场景 B：读写混跑 ════")
        store, searcher = _new_env()
        _run_rw_mix(store, searcher, args.threads, args.iterations,
                    args.writers, args.writer_ops)
        print(f"[OK] 读写混跑通过：{args.threads} 读线程 × {args.iterations} 次 + "
              f"{args.writers} 写线程 × {args.writer_ops} 组 CRUD 全部成功，库最终一致")
    except Exception as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1

    print("\n════ search_stage_timing 耗时统计（ms，含场景 A+B 全部调用）════")
    counts = [len(v) for v in timing.timings.values()]
    n = min(counts) if counts else 0
    print(f"样本数: {n} 次 search 调用")
    if n == 0:
        print("[WARN] 未采集到 search_stage_timing 日志（耗时观测缺失，其余验证不受影响）")
        return 0
    print(f"{'stage':<10}{'min':>10}{'avg':>10}{'p95':>10}{'max':>10}")
    for s in _STAGES:
        vals = sorted(timing.timings[s])
        if not vals:
            continue
        p95 = vals[min(len(vals) - 1, int(0.95 * len(vals)))]
        print(f"{s:<10}{vals[0]:>10.3f}{statistics.mean(vals):>10.3f}"
              f"{p95:>10.3f}{vals[-1]:>10.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
