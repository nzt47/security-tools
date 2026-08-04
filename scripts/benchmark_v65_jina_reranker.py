"""jina-reranker-v2 回归压测（验证 500ms SLO）

启用 jina-reranker-v2-base-multilingual 后的性能验证。
对比基准: v2-m3（P99 4641ms ❌）vs jina（预期 P99 ~300ms ✅）

执行:
    python scripts/benchmark_v65_jina_reranker.py
"""
import os
import sys
import time
import json
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor

# 行缓冲：后台运行时 stdout 默认块缓冲，导致看不到进度
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# 启用 Reranker + 指定 jina 模型
os.environ["SKILL_RERANKER_ENABLED"] = "true"
# modelscope 下载为扁平结构，离线模式下用仓库 ID 加载会失败，必须用本地完整路径
_JINA_MODEL_PATH = "C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual"
os.environ.setdefault("SKILL_RERANKER_MODEL", _JINA_MODEL_PATH)
# 离线模式（使用 modelscope 下载的缓存）
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
    """验证排序正确性"""
    print(f"\n{'─'*60}")
    print(f"验证 0: 排序正确性")
    print(f"{'─'*60}")

    candidates = [
        SkillMatch("voice_interaction", "语音交互助手", "语音识别 语音转文字", 0.5, 100, "interaction"),
        SkillMatch("pdf_parser", "PDF 解析器", "PDF 文件解析 文档提取", 0.5, 100, "file"),
        SkillMatch("self_reflection", "自我反思", "复盘 改进建议", 0.5, 100, "meta"),
    ]

    # 语音查询：语音相关应排第一
    result = reranker.rerank("帮我识别语音", candidates, top_k=3)
    print(f"  查询: '帮我识别语音'")
    print(f"  排序: {[r.skill_id for r in result]}")
    print(f"  期望首位: voice_interaction")
    voice_ok = result[0].skill_id == "voice_interaction"
    print(f"  结果: {'✅ 正确' if voice_ok else '❌ 错误'}")

    # PDF 查询：PDF 相关应排第一
    result2 = reranker.rerank("解析这个 PDF 文件", candidates, top_k=3)
    print(f"  查询: '解析这个 PDF 文件'")
    print(f"  排序: {[r.skill_id for r in result2]}")
    print(f"  期望首位: pdf_parser")
    pdf_ok = result2[0].skill_id == "pdf_parser"
    print(f"  结果: {'✅ 正确' if pdf_ok else '❌ 错误'}")

    return voice_ok and pdf_ok


def benchmark_jina_single(reranker, iterations=50) -> dict:
    """测试 B: jina 单次延迟"""
    print(f"\n{'─'*60}")
    print(f"测试 B: jina-reranker-v2 单次延迟（{iterations} 次）")
    print(f"{'─'*60}")

    candidates = _make_candidates(20)
    query = "帮我识别语音并转成文字"

    # 预热 3 次
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
    qps = 1000 / mean

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
        "test": "jina_single",
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


def benchmark_jina_throughput(reranker, iterations=100) -> dict:
    """测试 C: jina 吞吐"""
    print(f"\n{'─'*60}")
    print(f"测试 C: jina-reranker-v2 吞吐（{iterations} 次）")
    print(f"{'─'*60}")

    candidates = _make_candidates(20)
    queries = ["语音识别", "反思回答", "解析PDF", "总结历史", "代码审查"]

    t0 = time.time()
    for i in range(iterations):
        q = queries[i % len(queries)]
        reranker.rerank(q, candidates, top_k=3)
    total = time.time() - t0

    qps = iterations / total
    avg = (total / iterations) * 1000

    print(f"  总次数: {iterations}")
    print(f"  总耗时: {total:.2f}s")
    print(f"  QPS: {qps:.2f}")
    print(f"  平均延迟: {_fmt(avg)}")
    print(f"  目标 QPS ≥ 3: {'✅ 通过' if qps >= 3 else '❌ 未达标'}")

    return {
        "test": "jina_throughput",
        "iterations": iterations,
        "total_s": round(total, 2),
        "qps": round(qps, 2),
        "avg_latency_ms": round(avg, 2),
        "target_qps": 3,
        "passed": qps >= 3,
    }


def benchmark_jina_concurrency(reranker, threads=4, per_thread=10) -> dict:
    """测试 D: jina 并发"""
    print(f"\n{'─'*60}")
    print(f"测试 D: jina-reranker-v2 并发（{threads} 线程 × {per_thread} 次）")
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
    if errors:
        print(f"  错误（前 3 条）:")
        for e in errors[:3]:
            print(f"    {e}")
    print(f"  目标 0 错误: {'✅ 通过' if len(errors) == 0 else '❌ 未达标'}")

    return {
        "test": "jina_concurrency",
        "threads": threads,
        "per_thread": per_thread,
        "total_requests": len(results),
        "error_count": len(errors),
        "total_s": round(total, 2),
        "p99_ms": round(p99, 2),
        "qps": round(qps, 2),
        "passed": len(errors) == 0,
    }


def benchmark_jina_tail(reranker, iterations=200) -> dict:
    """测试 E: jina 长尾延迟"""
    print(f"\n{'─'*60}")
    print(f"测试 E: jina-reranker-v2 长尾延迟（{iterations} 次）")
    print(f"{'─'*60}")

    candidates = _make_candidates(20)
    queries = ["语音识别", "反思回答", "解析PDF", "总结历史", "代码审查", "数据分析"]

    latencies = []
    for i in range(iterations):
        q = queries[i % len(queries)]
        t0 = time.time()
        reranker.rerank(q, candidates, top_k=3)
        latencies.append((time.time() - t0) * 1000)

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    p999 = _percentile(latencies, 99.9)

    print(f"  迭代: {iterations}")
    print(f"  P50:   {_fmt(p50)}")
    print(f"  P95:   {_fmt(p95)}")
    print(f"  P99:   {_fmt(p99)}")
    print(f"  P99.9: {_fmt(p999)}")
    print(f"  Max:   {_fmt(max(latencies))}")
    print(f"  目标 P99.9 ≤ 2000ms: {'✅ 通过' if p999 <= 2000 else '❌ 未达标'}")

    return {
        "test": "jina_tail",
        "iterations": iterations,
        "p50_ms": round(p50, 2),
        "p95_ms": round(p95, 2),
        "p99_ms": round(p99, 2),
        "p999_ms": round(p999, 2),
        "max_ms": round(max(latencies), 2),
        "target_p999_ms": 2000,
        "passed": p999 <= 2000,
    }


def main() -> int:
    print("=" * 60)
    print("  jina-reranker-v2 回归压测")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模型: {os.environ.get('SKILL_RERANKER_MODEL')}")
    print("=" * 60)

    rss_before = _get_rss_mb()
    print(f"\n压测前 RSS: {rss_before:.1f}MB")

    # 加载模型
    print("\n初始化 SkillReranker 并加载 jina 模型...")
    reranker = SkillReranker()
    candidates = _make_candidates(5)

    t0 = time.time()
    reranker.rerank("预热查询", candidates, top_k=3)  # 触发加载
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

    # 验证排序正确性
    sort_ok = verify_sort_correctness(reranker)
    results.append({"test": "sort_correctness", "passed": sort_ok})

    # 执行 4 项压测（迭代次数已优化以快速验证 SLO）
    results.append(benchmark_jina_single(reranker, iterations=20))
    results.append(benchmark_jina_throughput(reranker, iterations=30))
    results.append(benchmark_jina_concurrency(reranker, threads=4, per_thread=5))
    results.append(benchmark_jina_tail(reranker, iterations=30))

    # 汇总
    print("\n" + "=" * 60)
    print("  jina-reranker-v2 压测结果汇总")
    print("=" * 60)
    all_passed = True
    for r in results:
        test_name = r["test"]
        passed = r.get("passed", False)
        status = "✅ 通过" if passed else "❌ 未达标"
        print(f"  {test_name}: {status}")
        if not passed:
            all_passed = False

    # 与 v2-m3 对比
    print("\n" + "=" * 60)
    print("  与 v2-m3 对比")
    print("=" * 60)
    baseline_path = os.path.join(project_root, "docs", "v65_benchmark_result.json")
    if os.path.exists(baseline_path):
        with open(baseline_path, "r", encoding="utf-8") as f:
            baseline = json.load(f)
        b_single = next((r for r in baseline["results"] if r["test"] == "single_rerank"), None)
        d_single = next((r for r in results if r["test"] == "jina_single"), None)
        if b_single and d_single:
            print(f"  {'指标':<20} {'v2-m3':<15} {'jina':<15} {'改善':<10}")
            print(f"  {'─'*60}")
            print(f"  {'P99 延迟':<20} {b_single['p99_ms']}ms{'':<8} {d_single['p99_ms']}ms{'':<8} {b_single['p99_ms']/max(d_single['p99_ms'],0.001):.1f}x")
            print(f"  {'内存':<20} {'1.92GB':<15} {rss_after_load/1024:.2f}GB{'':<7} {1920/max(rss_after_load,1):.1f}x")
    else:
        print(f"  ⚠️ v2-m3 基准数据不存在")

    print(f"\n{'✅ 全部压测通过' if all_passed else '❌ 部分压测未达标'}")

    # 保存结果
    report_path = os.path.join(project_root, "docs", "v65_jina_benchmark.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
            "model": "jinaai/jina-reranker-v2-base-multilingual",
            "results": results,
            "all_passed": all_passed,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {report_path}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
