"""bge-reranker-base 回归压测（验证 500ms SLO）

bge-reranker-base 是 BAAI 系列中较小的 Cross-Encoder（XLM-RoBERTa-base），
比 jina-v2（XLM-RoBERTa-large）计算量小，是 CPU 环境下满足 SLO 的最后希望。

对比基准:
    - v2-m3: P99 4641ms ❌
    - jina-v2: P99 7960ms ❌
    - bge-base: 待测（XLM-RoBERTa-base，预期更小）

执行:
    python scripts/benchmark_v65_bge_base_reranker.py
"""
import os
import sys
import time
import json
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor
import logging

# 行缓冲
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# 启用 Reranker + 指定 bge-base 模型
os.environ["SKILL_RERANKER_ENABLED"] = "true"
_BGE_BASE_PATH = "C:/Users/Administrator/.cache/huggingface/hub/models--BAAI--bge-reranker-base"
os.environ.setdefault("SKILL_RERANKER_MODEL", _BGE_BASE_PATH)
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.skills_mgmt.reranker import SkillReranker
from agent.skills_mgmt.loader import SkillMatch


def _percentile(data, p):
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    if f == c:
        return sorted_data[f]
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


def _fmt(ms):
    if ms < 1:
        return f"{ms*1000:.1f}μs"
    if ms < 1000:
        return f"{ms:.2f}ms"
    return f"{ms/1000:.2f}s"


class TimeoutLogCapture(logging.Handler):
    """捕获 predict.timeout 日志，用于 benchmark 末尾超时降级判断

    【简易】扫描 WARNING 级日志 message 中的 'predict.timeout'，统计超时次数
    【变易】observability logger propagate=True，root handler 能捕获 reranker 日志
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.timeout_count = 0
        self.timeout_entries = []

    def emit(self, record):
        try:
            msg = record.getMessage()
            if "predict.timeout" in msg:
                self.timeout_count += 1
                if len(self.timeout_entries) < 5:
                    self.timeout_entries.append(msg)
        except Exception:
            # 捕获逻辑失败不影响 benchmark 主流程
            pass


def _install_timeout_capture() -> TimeoutLogCapture:
    """安装超时日志捕获 handler 到 root logger

    【不易】仅追加 handler，不破坏现有日志配置
    Returns:
        TimeoutLogCapture 实例
    """
    capture = TimeoutLogCapture()
    logging.getLogger().addHandler(capture)
    return capture


def _print_timeout_conclusion(
    capture: TimeoutLogCapture, p99_ms: float, rerank_timeout: float
):
    """输出超时降级检测结论

    【简易】根据 timeout_count 给出明确判定，区分"真实 P99"vs"超时截断延迟"
    """
    print(f"\n{'─'*60}")
    print(f"超时降级检测")
    print(f"{'─'*60}")
    print(f"  RERANK_TIMEOUT: {rerank_timeout}s")
    print(f"  benchmark P99: {_fmt(p99_ms)}")
    print(f"  predict.timeout 日志数: {capture.timeout_count}")

    if capture.timeout_count == 0:
        print(f"  结论: ✅ 未触发超时降级")
        print(f"  P99={_fmt(p99_ms)} 是真实推理延迟")
        if p99_ms <= 500:
            print(f"  判定: ✅ 达标（≤500ms SLO）")
        else:
            print(f"  判定: ⚠️ 未达 500ms SLO，但未超时（无需调大 RERANK_TIMEOUT）")
    else:
        print(f"  超时样本（前 {len(capture.timeout_entries)} 条）:")
        for i, entry in enumerate(capture.timeout_entries, 1):
            print(f"    {i}. {entry[:300]}")
        print(f"  结论: ⚠️ 触发超时降级 {capture.timeout_count} 次")
        print(f"  P99={_fmt(p99_ms)} 含超时等待（~{rerank_timeout}s），非真实延迟")
        print(f"  真实 P99 > {rerank_timeout}s，需调大 RERANK_TIMEOUT 或转 ONNX 量化")


def _get_rss_mb():
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    except ImportError:
        return 0.0


def _make_candidates(n=20):
    skills = [
        ("voice_interaction", "语音交互助手", "语音识别 语音转文字 语音合成", "interaction"),
        ("self_reflection", "自我反思", "复盘 改进建议 自我评估", "meta"),
        ("pdf_parser", "PDF 解析器", "PDF 文件解析 文档提取", "file"),
        ("memory_summary", "记忆摘要", "对话历史摘要 上下文压缩", "meta"),
        ("code_review", "代码审查", "代码质量审查 最佳实践", "dev"),
        ("data_analysis", "数据分析", "数据统计分析 可视化", "analytics"),
        ("web_search", "网络搜索", "联网检索 信息查询", "search"),
        ("task_planner", "任务规划", "任务分解 执行计划", "meta"),
    ]
    candidates = []
    for i in range(n):
        idx = i % len(skills)
        sid, name, desc, cat = skills[idx]
        candidates.append(SkillMatch(
            skill_id=f"{sid}_{i}", name=name, description=desc,
            score=0.5 - i * 0.01, estimated_tokens=100,
            category=cat, tags=desc.split(),
        ))
    return candidates


def verify_sort_correctness(reranker) -> bool:
    print(f"\n{'─'*60}")
    print(f"验证 0: 排序正确性")
    print(f"{'─'*60}")

    candidates = [
        SkillMatch("voice_interaction", "语音交互助手", "语音识别 语音转文字", 0.5, 100, "interaction"),
        SkillMatch("pdf_parser", "PDF 解析器", "PDF 文件解析 文档提取", 0.5, 100, "file"),
        SkillMatch("self_reflection", "自我反思", "复盘 改进建议", 0.5, 100, "meta"),
    ]

    result = reranker.rerank("帮我识别语音", candidates, top_k=3)
    print(f"  查询: '帮我识别语音'")
    print(f"  排序: {[r.skill_id for r in result]}")
    voice_ok = result[0].skill_id == "voice_interaction"
    print(f"  结果: {'✅ 正确' if voice_ok else '❌ 错误'}")

    result2 = reranker.rerank("解析这个 PDF 文件", candidates, top_k=3)
    print(f"  查询: '解析这个 PDF 文件'")
    print(f"  排序: {[r.skill_id for r in result2]}")
    pdf_ok = result2[0].skill_id == "pdf_parser"
    print(f"  结果: {'✅ 正确' if pdf_ok else '❌ 错误'}")

    return voice_ok and pdf_ok


def benchmark_single(reranker, iterations=20) -> dict:
    print(f"\n{'─'*60}")
    print(f"测试 B: bge-reranker-base 单次延迟（{iterations} 次）")
    print(f"{'─'*60}")

    candidates = _make_candidates(20)
    query = "帮我识别语音并转成文字"

    for _ in range(3):
        reranker.rerank(query, candidates, top_k=3)

    latencies = []
    for i in range(iterations):
        t0 = time.time()
        reranker.rerank(query, candidates, top_k=3)
        latencies.append((time.time() - t0) * 1000)

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    mean = statistics.mean(latencies)
    qps = 1000 / mean if mean > 0 else 0

    print(f"  迭代: {iterations}")
    print(f"  Min:  {_fmt(min(latencies))}")
    print(f"  Mean: {_fmt(mean)}")
    print(f"  P50:  {_fmt(p50)}")
    print(f"  P95:  {_fmt(p95)}")
    print(f"  P99:  {_fmt(p99)}")
    print(f"  Max:  {_fmt(max(latencies))}")
    print(f"  QPS:  {qps:.2f}")
    print(f"  目标 P99 ≤ 500ms: {'✅ 通过' if p99 <= 500 else '❌ 未达标'}")

    return {
        "test": "bge_base_single",
        "iterations": iterations,
        "min_ms": round(min(latencies), 2),
        "mean_ms": round(mean, 2),
        "p50_ms": round(p50, 2),
        "p95_ms": round(p95, 2),
        "p99_ms": round(p99, 2),
        "max_ms": round(max(latencies), 2),
        "qps": round(qps, 2),
        "target_p99_ms": 500,
        "passed": p99 <= 500,
    }


def benchmark_concurrency(reranker, threads=4, per_thread=5) -> dict:
    print(f"\n{'─'*60}")
    print(f"测试 D: bge-reranker-base 并发（{threads} 线程 × {per_thread} 次）")
    print(f"{'─'*60}")

    candidates = _make_candidates(20)
    queries = ["语音识别", "反思回答", "解析PDF", "总结历史"]
    results = []
    errors = []
    barrier = threading.Barrier(threads)

    def worker(tid):
        try:
            barrier.wait()
            for i in range(per_thread):
                q = queries[tid % len(queries)]
                t0 = time.time()
                reranker.rerank(q, candidates, top_k=3)
                results.append((time.time() - t0) * 1000)
        except Exception as e:
            errors.append(f"thread-{tid}: {type(e).__name__}: {str(e)[:200]}")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=threads) as ex:
        list(ex.map(worker, range(threads)))
    total = time.time() - t0

    p99 = _percentile(results, 99) if results else 0
    qps = len(results) / total if total > 0 else 0

    print(f"  总请求: {len(results)}")
    print(f"  失败: {len(errors)}")
    print(f"  总耗时: {total:.2f}s")
    print(f"  P99: {_fmt(p99)}")
    print(f"  QPS: {qps:.2f}")
    print(f"  目标 0 错误: {'✅ 通过' if len(errors) == 0 else '❌ 未达标'}")

    return {
        "test": "bge_base_concurrency",
        "threads": threads,
        "per_thread": per_thread,
        "total_requests": len(results),
        "error_count": len(errors),
        "total_s": round(total, 2),
        "p99_ms": round(p99, 2),
        "qps": round(qps, 2),
        "passed": len(errors) == 0,
    }


def main() -> int:
    # 【变易】安装超时日志捕获，末尾自动判断是否触发降级
    timeout_capture = _install_timeout_capture()

    print("=" * 60)
    print("  bge-reranker-base 回归压测")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模型: {os.environ.get('SKILL_RERANKER_MODEL')}")
    print("=" * 60)

    rss_before = _get_rss_mb()
    print(f"\n压测前 RSS: {rss_before:.1f}MB")

    print("\n初始化 SkillReranker 并加载 bge-base 模型...")
    reranker = SkillReranker()
    candidates = _make_candidates(5)

    t0 = time.time()
    reranker.rerank("预热查询", candidates, top_k=3)
    load_time = time.time() - t0
    rss_after_load = _get_rss_mb()
    print(f"模型加载耗时: {load_time:.2f}s")
    print(f"加载后 RSS: {rss_after_load:.1f}MB")
    print(f"模型内存占用: {rss_after_load - rss_before:.1f}MB")
    print(f"目标 RSS ≤ 1.5GB: {'✅ 通过' if rss_after_load <= 1536 else '❌ 未达标'}")

    memory_result = {
        "test": "memory",
        "load_time_s": round(load_time, 2),
        "rss_before_mb": round(rss_before, 1),
        "rss_after_load_mb": round(rss_after_load, 1),
        "model_memory_mb": round(rss_after_load - rss_before, 1),
        "target_rss_mb": 1536,
        "passed": rss_after_load <= 1536,
    }

    results = [memory_result]
    sort_ok = verify_sort_correctness(reranker)
    results.append({"test": "sort_correctness", "passed": sort_ok})

    results.append(benchmark_single(reranker, iterations=20))
    results.append(benchmark_concurrency(reranker, threads=4, per_thread=5))

    # 【变易】超时降级检测：区分真实 P99 vs 超时截断延迟
    # results 顺序: [memory, sort_correctness, benchmark_single, benchmark_concurrency]
    # benchmark_single 在倒数第二个位置
    single_p99 = results[-2]["p99_ms"]
    _print_timeout_conclusion(
        timeout_capture, single_p99, reranker._rerank_timeout
    )

    print("\n" + "=" * 60)
    print("  bge-reranker-base 压测结果汇总")
    print("=" * 60)
    all_passed = True
    for r in results:
        test_name = r["test"]
        passed = r.get("passed", False)
        status = "✅ 通过" if passed else "❌ 未达标"
        print(f"  {test_name}: {status}")
        if not passed:
            all_passed = False

    print(f"\n{'✅ 全部压测通过' if all_passed else '❌ 部分压测未达标'}")

    report_path = os.path.join(project_root, "docs", "v65_bge_base_benchmark.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
            "model": "BAAI/bge-reranker-base",
            "results": results,
            "all_passed": all_passed,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {report_path}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
