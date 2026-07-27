"""v6.5 Reranker 接口压测脚本

压测项（覆盖 §4.2 压测计划）:
    1. 单次 rerank 延迟（P50/P95/P99）
    2. 批量 rerank 吞吐（QPS）
    3. 模型加载内存占用（RSS）
    4. 并发安全（4 线程并发）
    5. 长尾延迟（P99.9）

使用真实 bge-reranker-v2-m3 模型（已验证可加载）。
"""
import os
import sys
import time
import json
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

# 离线模式（使用本地缓存，避免联网）
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

# 添加项目根目录到 path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.skills_mgmt.reranker import SkillReranker
from agent.skills_mgmt.loader import SkillMatch


def _fmt_ms(ms: float) -> str:
    return f"{ms:.2f}ms"


def _fmt_mb(mb: float) -> str:
    if mb < 1024:
        return f"{mb:.1f}MB"
    return f"{mb/1024:.2f}GB"


def _get_rss_mb() -> float:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    except ImportError:
        return 0.0


def _make_candidates(n: int = 20) -> List[SkillMatch]:
    """构造 mock 候选技能列表"""
    skills = [
        ("voice_interaction", "语音交互助手", "语音识别 语音转文字 语音合成", "interaction"),
        ("self_reflection", "自我反思", "复盘 改进建议 自我评估", "meta"),
        ("pdf_parser", "PDF 解析器", "PDF 文件解析 文档提取", "file"),
        ("memory_summary", "记忆摘要", "对话历史摘要 上下文压缩", "meta"),
        ("code_review", "代码审查", "代码质量审查 最佳实践", "dev"),
        ("data_analysis", "数据分析", "数据统计分析 可视化", "analytics"),
        ("web_search", "网络搜索", "联网检索 信息查询", "search"),
        ("file_manager", "文件管理", "文件操作 目录管理", "file"),
        ("task_planner", "任务规划", "任务分解 执行计划", "meta"),
        ("translation", "翻译助手", "多语言翻译 文本翻译", "language"),
    ]
    candidates = []
    for i in range(n):
        idx = i % len(skills)
        sid, name, desc, cat = skills[idx]
        candidates.append(SkillMatch(
            skill_id=f"{sid}_{i}",
            name=name,
            description=desc,
            score=0.5 - i * 0.01,
            estimated_tokens=100,
            category=cat,
            tags=desc.split(),
        ))
    return candidates


def _percentile(data: List[float], p: float) -> float:
    """计算分位数"""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    if f == c:
        return sorted_data[f]
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


def benchmark_single_rerank(reranker: SkillReranker, iterations: int = 50) -> Dict[str, Any]:
    """压测 1: 单次 rerank 延迟"""
    print(f"\n{'─'*60}")
    print(f"压测 1: 单次 rerank 延迟（{iterations} 次）")
    print(f"{'─'*60}")

    candidates = _make_candidates(20)
    query = "帮我识别语音并转成文字"
    latencies = []

    # 预热 3 次（避免首次 predict 冷启动）
    for _ in range(3):
        reranker.rerank(query, candidates, top_k=3)

    # 正式压测
    for i in range(iterations):
        t0 = time.time()
        result = reranker.rerank(query, candidates, top_k=3)
        elapsed_ms = (time.time() - t0) * 1000
        latencies.append(elapsed_ms)

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    mean = statistics.mean(latencies)
    min_ms = min(latencies)
    max_ms = max(latencies)

    print(f"  迭代次数: {iterations}")
    print(f"  候选数: 20 → top_k=3")
    print(f"  Min:  {_fmt_ms(min_ms)}")
    print(f"  Mean: {_fmt_ms(mean)}")
    print(f"  P50:  {_fmt_ms(p50)}")
    print(f"  P95:  {_fmt_ms(p95)}")
    print(f"  P99:  {_fmt_ms(p99)}")
    print(f"  Max:  {_fmt_ms(max_ms)}")
    print(f"  目标 P99 ≤ 500ms: {'✅ 通过' if p99 <= 500 else '❌ 未达标'}")

    return {
        "test": "single_rerank",
        "iterations": iterations,
        "min_ms": round(min_ms, 2),
        "mean_ms": round(mean, 2),
        "p50_ms": round(p50, 2),
        "p95_ms": round(p95, 2),
        "p99_ms": round(p99, 2),
        "max_ms": round(max_ms, 2),
        "target_p99_ms": 500,
        "passed": p99 <= 500,
    }


def benchmark_throughput(reranker: SkillReranker, iterations: int = 100) -> Dict[str, Any]:
    """压测 2: 批量 rerank 吞吐"""
    print(f"\n{'─'*60}")
    print(f"压测 2: 批量 rerank 吞吐（{iterations} 次连续）")
    print(f"{'─'*60}")

    candidates = _make_candidates(20)
    queries = [
        "帮我识别语音并转成文字",
        "反思刚才的回答",
        "解析这个 PDF 文件",
        "总结对话历史",
        "审查代码质量",
    ]

    t0 = time.time()
    for i in range(iterations):
        q = queries[i % len(queries)]
        reranker.rerank(q, candidates, top_k=3)
    total_s = time.time() - t0

    qps = iterations / total_s
    avg_latency = (total_s / iterations) * 1000

    print(f"  总次数: {iterations}")
    print(f"  总耗时: {total_s:.2f}s")
    print(f"  QPS:    {qps:.2f}")
    print(f"  平均延迟: {_fmt_ms(avg_latency)}")
    print(f"  目标 QPS ≥ 10: {'✅ 通过' if qps >= 10 else '❌ 未达标'}")

    return {
        "test": "throughput",
        "iterations": iterations,
        "total_s": round(total_s, 2),
        "qps": round(qps, 2),
        "avg_latency_ms": round(avg_latency, 2),
        "target_qps": 10,
        "passed": qps >= 10,
    }


def benchmark_concurrency(reranker: SkillReranker, threads: int = 4, per_thread: int = 25) -> Dict[str, Any]:
    """压测 3: 并发安全"""
    print(f"\n{'─'*60}")
    print(f"压测 3: 并发安全（{threads} 线程 × {per_thread} 次/线程）")
    print(f"{'─'*60}")

    candidates = _make_candidates(20)
    queries = [
        "帮我识别语音并转成文字",
        "反思刚才的回答",
        "解析这个 PDF 文件",
        "总结对话历史",
    ]

    results = []
    errors = []
    barrier = threading.Barrier(threads)  # 确保所有线程同时开始

    def worker(tid: int):
        try:
            barrier.wait()
            for i in range(per_thread):
                q = queries[(tid + i) % len(queries)]
                t0 = time.time()
                result = reranker.rerank(q, candidates, top_k=3)
                elapsed_ms = (time.time() - t0) * 1000
                results.append((tid, i, elapsed_ms, len(result)))
        except Exception as e:
            errors.append(f"thread-{tid}: {type(e).__name__}: {str(e)[:200]}")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = [executor.submit(worker, tid) for tid in range(threads)]
        for f in as_completed(futures):
            f.result()
    total_s = time.time() - t0

    latencies = [r[2] for r in results]
    total_requests = threads * per_thread
    success_count = len(results)
    error_count = len(errors)

    print(f"  线程数: {threads}")
    print(f"  每线程请求数: {per_thread}")
    print(f"  总请求数: {total_requests}")
    print(f"  成功: {success_count}")
    print(f"  失败: {error_count}")
    print(f"  总耗时: {total_s:.2f}s")
    if latencies:
        print(f"  P50: {_fmt_ms(_percentile(latencies, 50))}")
        print(f"  P99: {_fmt_ms(_percentile(latencies, 99))}")
    if errors:
        print(f"  错误详情（前 3 条）:")
        for e in errors[:3]:
            print(f"    {e}")
    print(f"  目标 0 崩溃 + 全部成功: {'✅ 通过' if error_count == 0 else '❌ 未达标'}")

    return {
        "test": "concurrency",
        "threads": threads,
        "per_thread": per_thread,
        "total_requests": total_requests,
        "success_count": success_count,
        "error_count": error_count,
        "total_s": round(total_s, 2),
        "p50_ms": round(_percentile(latencies, 50), 2) if latencies else 0,
        "p99_ms": round(_percentile(latencies, 99), 2) if latencies else 0,
        "target_zero_error": True,
        "passed": error_count == 0,
    }


def benchmark_tail_latency(reranker: SkillReranker, iterations: int = 200) -> Dict[str, Any]:
    """压测 4: 长尾延迟"""
    print(f"\n{'─'*60}")
    print(f"压测 4: 长尾延迟（{iterations} 次，统计 P99.9）")
    print(f"{'─'*60}")

    candidates = _make_candidates(20)
    queries = [
        "帮我识别语音并转成文字",
        "反思刚才的回答",
        "解析这个 PDF 文件",
        "总结对话历史",
        "审查代码质量",
        "数据分析统计",
        "联网检索信息",
        "翻译这段文本",
    ]

    latencies = []
    for i in range(iterations):
        q = queries[i % len(queries)]
        t0 = time.time()
        reranker.rerank(q, candidates, top_k=3)
        elapsed_ms = (time.time() - t0) * 1000
        latencies.append(elapsed_ms)

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    p999 = _percentile(latencies, 99.9)

    print(f"  迭代次数: {iterations}")
    print(f"  P50:    {_fmt_ms(p50)}")
    print(f"  P95:    {_fmt_ms(p95)}")
    print(f"  P99:    {_fmt_ms(p99)}")
    print(f"  P99.9:  {_fmt_ms(p999)}")
    print(f"  Max:    {_fmt_ms(max(latencies))}")
    print(f"  目标 P99.9 ≤ 2000ms: {'✅ 通过' if p999 <= 2000 else '❌ 未达标'}")

    return {
        "test": "tail_latency",
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
    print("  v6.5 Reranker 接口压测")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模型: {os.environ.get('SKILL_RERANKER_MODEL', 'BAAI/bge-reranker-v2-m3')}")
    print("=" * 60)

    rss_before = _get_rss_mb()
    print(f"\n压测前 RSS: {_fmt_mb(rss_before)}")

    # 初始化 Reranker
    print("\n初始化 SkillReranker...")
    t0 = time.time()
    reranker = SkillReranker()
    # 触发模型加载
    candidates = _make_candidates(5)
    reranker.rerank("预热查询", candidates, top_k=3)
    load_time = time.time() - t0
    rss_after_load = _get_rss_mb()
    print(f"模型加载耗时: {load_time:.2f}s")
    print(f"模型加载后 RSS: {_fmt_mb(rss_after_load)}")
    print(f"模型内存占用: {_fmt_mb(rss_after_load - rss_before)}")
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

    # 执行 4 项压测
    # 【变易】v2-m3 CPU 推理延迟 ~3.7s/次，减少迭代次数以控制总耗时
    results = [memory_result]
    results.append(benchmark_single_rerank(reranker, iterations=20))
    results.append(benchmark_throughput(reranker, iterations=30))
    results.append(benchmark_concurrency(reranker, threads=4, per_thread=5))
    results.append(benchmark_tail_latency(reranker, iterations=50))

    # 汇总
    print("\n" + "=" * 60)
    print("  压测结果汇总")
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

    # 保存结果到 JSON
    report_path = os.path.join(project_root, "docs", "v65_benchmark_result.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
            "model": os.environ.get('SKILL_RERANKER_MODEL', 'BAAI/bge-reranker-v2-m3'),
            "results": results,
            "all_passed": all_passed,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n压测结果已保存: {report_path}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
