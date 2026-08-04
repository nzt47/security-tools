"""v6.5 RRF 降级模式压测（对比 Reranker 启用模式）

目的:
    1. 验证 SKILL_RERANKER_ENABLED=false 后主流程恢复正常
    2. 获取降级模式的 P99 延迟和 QPS 数据
    3. 与 Reranker 启用模式对比（基准数据来自 v65_benchmark_result.json）

执行:
    python scripts/benchmark_v65_rrf_degraded.py
"""
import os
import sys
import time
import json
import statistics
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

# 【关键】降级模式：禁用 Reranker
os.environ["SKILL_RERANKER_ENABLED"] = "false"
# 离线模式（避免联网）
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
    return f"{ms:.2f}ms"


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


def verify_main_flow() -> bool:
    """验证主流程恢复正常（降级后 rerank 应返回原序）"""
    print(f"\n{'─'*60}")
    print(f"验证 0: 主流程恢复正常（降级模式）")
    print(f"{'─'*60}")

    reranker = SkillReranker()
    candidates = _make_candidates(5)

    # 验证 1: Reranker 已禁用
    enabled = reranker._is_enabled()
    print(f"  SKILL_RERANKER_ENABLED: {os.environ.get('SKILL_RERANKER_ENABLED')}")
    print(f"  _is_enabled(): {enabled}")
    if enabled:
        print(f"  ❌ Reranker 仍启用，降级失败")
        return False
    print(f"  ✅ Reranker 已禁用")

    # 验证 2: rerank 返回原序（不触发模型加载）
    t0 = time.time()
    result = reranker.rerank("测试查询", candidates, top_k=3)
    elapsed_ms = (time.time() - t0) * 1000

    print(f"  rerank() 耗时: {_fmt(elapsed_ms)}")
    print(f"  返回候选数: {len(result)}")
    print(f"  首候选 ID: {result[0].skill_id if result else 'N/A'}")
    print(f"  首候选分数: {result[0].score if result else 'N/A'}")

    if elapsed_ms > 100:
        print(f"  ❌ 降级延迟过高（{elapsed_ms:.2f}ms > 100ms）")
        return False
    if len(result) != 3:
        print(f"  ❌ 返回候选数异常（{len(result)} != 3）")
        return False
    # 验证返回原序（candidates[0] 应等于 result[0]）
    if result[0].skill_id != candidates[0].skill_id:
        print(f"  ❌ 未返回原序（{result[0].skill_id} != {candidates[0].skill_id}）")
        return False

    print(f"  ✅ 返回原序，降级正常")
    print(f"  ✅ 主流程恢复正常（降级延迟 sub-ms）")
    return True


def benchmark_degraded_single(iterations=200) -> dict:
    """测试 B: 降级模式单次延迟"""
    print(f"\n{'─'*60}")
    print(f"测试 B: RRF 降级单次延迟（{iterations} 次）")
    print(f"{'─'*60}")

    reranker = SkillReranker()
    candidates = _make_candidates(20)
    query = "帮我识别语音并转成文字"

    # 预热 5 次
    for _ in range(5):
        reranker.rerank(query, candidates, top_k=3)

    latencies = []
    for i in range(iterations):
        t0 = time.time()
        reranker.rerank(query, candidates, top_k=3)
        latencies.append((time.time() - t0) * 1000)

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    p999 = _percentile(latencies, 99.9)
    mean = statistics.mean(latencies)
    qps = 1000 / mean

    print(f"  迭代: {iterations}")
    print(f"  Min:   {_fmt(min(latencies))}")
    print(f"  Mean:  {_fmt(mean)}")
    print(f"  P50:   {_fmt(p50)}")
    print(f"  P95:   {_fmt(p95)}")
    print(f"  P99:   {_fmt(p99)}")
    print(f"  P99.9: {_fmt(p999)}")
    print(f"  Max:   {_fmt(max(latencies))}")
    print(f"  QPS:   {qps:.1f}")
    print(f"  目标 P99 ≤ 100ms: {'✅ 通过' if p99 <= 100 else '❌ 未达标'}")
    print(f"  目标 QPS ≥ 50:    {'✅ 通过' if qps >= 50 else '❌ 未达标'}")

    return {
        "test": "degraded_single",
        "iterations": iterations,
        "min_ms": round(min(latencies), 3),
        "mean_ms": round(mean, 3),
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
        "p99_ms": round(p99, 3),
        "p999_ms": round(p999, 3),
        "max_ms": round(max(latencies), 3),
        "qps": round(qps, 1),
        "target_p99_ms": 100,
        "target_qps": 50,
        "passed": p99 <= 100 and qps >= 50,
    }


def benchmark_degraded_concurrency(threads=4, per_thread=25) -> dict:
    """测试 C: 降级模式并发"""
    print(f"\n{'─'*60}")
    print(f"测试 C: RRF 降级并发（{threads} 线程 × {per_thread} 次）")
    print(f"{'─'*60}")

    reranker = SkillReranker()
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

    p50 = _percentile(results, 50)
    p99 = _percentile(results, 99)
    qps = len(results) / total

    print(f"  总请求: {len(results)}")
    print(f"  失败: {len(errors)}")
    print(f"  总耗时: {total:.2f}s")
    print(f"  P50: {_fmt(p50)}")
    print(f"  P99: {_fmt(p99)}")
    print(f"  QPS: {qps:.1f}")
    if errors:
        print(f"  错误（前 3 条）:")
        for e in errors[:3]:
            print(f"    {e}")
    print(f"  目标 0 错误: {'✅ 通过' if len(errors) == 0 else '❌ 未达标'}")

    return {
        "test": "degraded_concurrency",
        "threads": threads,
        "per_thread": per_thread,
        "total_requests": len(results),
        "error_count": len(errors),
        "total_s": round(total, 2),
        "p50_ms": round(p50, 3),
        "p99_ms": round(p99, 3),
        "qps": round(qps, 1),
        "target_zero_error": True,
        "passed": len(errors) == 0,
    }


def benchmark_degraded_tail(iterations=500) -> dict:
    """测试 D: 降级模式长尾延迟"""
    print(f"\n{'─'*60}")
    print(f"测试 D: RRF 降级长尾延迟（{iterations} 次）")
    print(f"{'─'*60}")

    reranker = SkillReranker()
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
    print(f"  目标 P99.9 ≤ 200ms: {'✅ 通过' if p999 <= 200 else '❌ 未达标'}")

    return {
        "test": "degraded_tail",
        "iterations": iterations,
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
        "p99_ms": round(p99, 3),
        "p999_ms": round(p999, 3),
        "max_ms": round(max(latencies), 3),
        "target_p999_ms": 200,
        "passed": p999 <= 200,
    }


def main() -> int:
    print("=" * 60)
    print("  v6.5 RRF 降级模式对比压测")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  SKILL_RERANKER_ENABLED={os.environ.get('SKILL_RERANKER_ENABLED')}")
    print("=" * 60)

    rss_before = _get_rss_mb()
    print(f"\n压测前 RSS: {rss_before:.1f}MB")

    results = []

    # 验证 0: 主流程恢复
    if not verify_main_flow():
        print("\n❌ 主流程验证失败，终止压测")
        return 1
    results.append({
        "test": "main_flow_verify",
        "passed": True,
        "rss_mb": round(_get_rss_mb(), 1),
    })

    # 测试 B: 单次延迟
    results.append(benchmark_degraded_single(iterations=200))

    # 测试 C: 并发
    results.append(benchmark_degraded_concurrency(threads=4, per_thread=25))

    # 测试 D: 长尾
    results.append(benchmark_degraded_tail(iterations=500))

    # 汇总
    print("\n" + "=" * 60)
    print("  降级模式压测结果汇总")
    print("=" * 60)
    all_passed = True
    for r in results:
        test_name = r["test"]
        passed = r.get("passed", False)
        status = "✅ 通过" if passed else "❌ 未达标"
        print(f"  {test_name}: {status}")
        if not passed:
            all_passed = False

    # 对比基准（Reranker 启用模式）
    print("\n" + "=" * 60)
    print("  与 Reranker 启用模式对比")
    print("=" * 60)
    # 从 v65_benchmark_result.json 读取基准数据
    baseline_path = os.path.join(project_root, "docs", "v65_benchmark_result.json")
    baseline = None
    if os.path.exists(baseline_path):
        with open(baseline_path, "r", encoding="utf-8") as f:
            baseline = json.load(f)

    if baseline:
        # 找到 Reranker 启用模式的数据
        b_single = next((r for r in baseline["results"] if r["test"] == "single_rerank"), None)
        b_throughput = next((r for r in baseline["results"] if r["test"] == "throughput"), None)
        b_concurrency = next((r for r in baseline["results"] if r["test"] == "concurrency"), None)
        b_tail = next((r for r in baseline["results"] if r["test"] == "tail_latency"), None)

        d_single = next((r for r in results if r["test"] == "degraded_single"), None)
        d_concurrency = next((r for r in results if r["test"] == "degraded_concurrency"), None)
        d_tail = next((r for r in results if r["test"] == "degraded_tail"), None)

        print(f"\n  {'指标':<20} {'Reranker启用':<18} {'RRF降级':<18} {'改善倍数':<10}")
        print(f"  {'─'*65}")
        if b_single and d_single:
            print(f"  {'单次 P99 延迟':<20} {b_single['p99_ms']}ms{'':<10} {d_single['p99_ms']}ms{'':<10} {b_single['p99_ms']/max(d_single['p99_ms'],0.001):.0f}x")
        if b_throughput and d_single:
            print(f"  {'单次 QPS':<20} {b_throughput['qps']}{'':<14} {d_single['qps']}{'':<14} {d_single['qps']/max(b_throughput['qps'],0.001):.0f}x")
        if b_concurrency and d_concurrency:
            print(f"  {'并发 P99 延迟':<20} {b_concurrency['p99_ms']}ms{'':<10} {d_concurrency['p99_ms']}ms{'':<10} {b_concurrency['p99_ms']/max(d_concurrency['p99_ms'],0.001):.0f}x")
        if b_tail and d_tail:
            print(f"  {'长尾 P99.9 延迟':<20} {b_tail['p999_ms']}ms{'':<10} {d_tail['p999_ms']}ms{'':<10} {b_tail['p999_ms']/max(d_tail['p999_ms'],0.001):.0f}x")
    else:
        print(f"  ⚠️ 基准数据文件不存在: {baseline_path}")

    rss_after = _get_rss_mb()
    print(f"\n  内存占用: {rss_before:.1f}MB → {rss_after:.1f}MB（增量 {rss_after-rss_before:.1f}MB）")

    print(f"\n{'✅ 全部压测通过' if all_passed else '❌ 部分压测未达标'}")

    # 保存结果
    report_path = os.path.join(project_root, "docs", "v65_rrf_degraded_benchmark.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
            "mode": "rrf_degraded",
            "skill_reranker_enabled": False,
            "results": results,
            "all_passed": all_passed,
            "rss_before_mb": round(rss_before, 1),
            "rss_after_mb": round(rss_after, 1),
        }, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {report_path}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
