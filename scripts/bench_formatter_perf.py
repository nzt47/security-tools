"""StructuredLogFormatter 性能基准测试

分析高频调用场景下的 CPU 耗时和内存占用，识别性能瓶颈。

用法：
    python scripts/bench_formatter_perf.py
"""

import sys
import time
import json
import logging
import tracemalloc
from pathlib import Path
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.struct_log_formatter import StructuredLogFormatter


# ── 测试数据 ──────────────────────────────────────────────────

# 小型 JSON 日志（常见场景：保存配置）
SMALL_JSON = json.dumps({
    "trace_id": "abc12345",
    "module_name": "network_config",
    "action": "network_config._save.self",
    "duration_ms": 5,
    "message": "已保存到文件",
}, ensure_ascii=False)

# 中型 JSON 日志（搜索实例操作）
MEDIUM_JSON = json.dumps({
    "trace_id": "abc12345",
    "module_name": "app_server",
    "action": "api_search_instance_add.done",
    "duration_ms": 45,
    "message": "搜索实例已新增",
    "instance_id": "550e8400-e29b-41d4-a716-446655440000",
    "instance_name": "Tavily",
    "engine_type": "custom",
    "priority_before": ["uuid-aaa"],
    "priority_after": ["uuid-aaa", "uuid-bbb", "uuid-ccc", "uuid-ddd"],
    "priority_changed": True,
}, ensure_ascii=False)

# 大型 JSON 日志（含大量额外字段）
LARGE_JSON = json.dumps({
    "trace_id": "abc12345",
    "module_name": "app_server",
    "action": "api_network_config_update.done",
    "duration_ms": 152,
    "message": "网络配置已更新",
    "priority_before": ["uuid-aaa", "uuid-bbb", "uuid-ccc"],
    "priority_after": ["uuid-bbb", "uuid-aaa", "uuid-ccc", "uuid-ddd", "uuid-eee"],
    "priority_changed": True,
    "default_engine": "uuid-bbb",
    "default_before": "uuid-aaa",
    "default_after": "uuid-bbb",
    "instance_id": "550e8400",
    "instance_name": "Tavily Pro",
    "engine_type": "custom",
    "updated_fields": ["name", "api_endpoint", "timeout", "auth_header", "results_path"],
    "config_keys": ["llm", "search", "network", "mcp", "browser", "sync", "external_services"],
    "extra_field_1": "x" * 200,
    "extra_field_2": list(range(50)),
    "extra_field_3": {"nested": {"deep": {"data": list(range(20))}}},
}, ensure_ascii=False)

# 非 JSON 日志（回退路径）
PLAIN_MSG = "[OK] 这是一个非 JSON 格式的传统日志消息"

# 极短日志
TINY_JSON = json.dumps({
    "trace_id": "abc12345",
    "module_name": "digital_life",
    "action": "tick",
    "duration_ms": 0,
    "message": "ok",
}, ensure_ascii=False)


# ── 辅助函数 ──────────────────────────────────────────────────

def _make_record(msg: str, level: int = logging.INFO) -> logging.LogRecord:
    """构造一个 LogRecord（模拟 logger.info() 的产物）"""
    return logging.LogRecord(
        name="bench.module",
        level=level,
        pathname="bench.py",
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )


def _bench_single(msg: str, iterations: int) -> float:
    """测量单条日志格式化 N 次的总耗时（秒）"""
    formatter = StructuredLogFormatter()
    record = _make_record(msg)
    # 预热（触发任何延迟初始化）
    formatter.format(record)
    start = time.perf_counter()
    for _ in range(iterations):
        formatter.format(record)
    elapsed = time.perf_counter() - start
    return elapsed


def _bench_mixed(iterations: int) -> float:
    """混合负载：模拟真实场景（70% 小型 + 20% 中型 + 5% 大型 + 5% 非 JSON）"""
    formatter = StructuredLogFormatter()
    records = [
        _make_record(SMALL_JSON),
        _make_record(SMALL_JSON),
        _make_record(SMALL_JSON),
        _make_record(SMALL_JSON),
        _make_record(SMALL_JSON),
        _make_record(SMALL_JSON),
        _make_record(SMALL_JSON),
        _make_record(MEDIUM_JSON),
        _make_record(MEDIUM_JSON),
        _make_record(LARGE_JSON),
        _make_record(PLAIN_MSG),
        _make_record(TINY_JSON),
    ]
    # 预热
    for r in records:
        formatter.format(r)
    start = time.perf_counter()
    for i in range(iterations):
        r = records[i % len(records)]
        formatter.format(r)
    elapsed = time.perf_counter() - start
    return elapsed


def _measure_memory(msg: str, iterations: int) -> dict:
    """测量格式化 N 次的内存分配"""
    formatter = StructuredLogFormatter()
    record = _make_record(msg)
    # 预热
    formatter.format(record)

    tracemalloc.start()
    snapshot_before = tracemalloc.take_snapshot()

    for _ in range(iterations):
        formatter.format(record)

    snapshot_after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    stats = snapshot_after.compare_to(snapshot_before, "lineno")
    total_alloc = sum(s.size_diff for s in stats if s.size_diff > 0)
    total_count = sum(s.count_diff for s in stats if s.count_diff > 0)

    return {
        "total_alloc_bytes": total_alloc,
        "total_alloc_kb": round(total_alloc / 1024, 1),
        "per_call_bytes": round(total_alloc / iterations) if iterations > 0 else 0,
        "alloc_blocks": total_count,
    }


def _profile_bottlenecks(msg: str, iterations: int):
    """用 cProfile 分析瓶颈"""
    import cProfile
    import io as _io
    import pstats

    formatter = StructuredLogFormatter()
    record = _make_record(msg)

    pr = cProfile.Profile()
    pr.enable()
    for _ in range(iterations):
        formatter.format(record)
    pr.disable()

    s = _io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(15)
    return s.getvalue()


# ── 主流程 ────────────────────────────────────────────────────

def main():
    ITERATIONS = 10000
    MEM_ITERATIONS = 1000

    print("=" * 70)
    print("  StructuredLogFormatter 性能基准测试")
    print(f"  迭代次数: CPU={ITERATIONS}, 内存={MEM_ITERATIONS}")
    print("=" * 70)

    # ── 1. CPU 耗时 ──
    print(f"\n{'─'*70}")
    print("  1. CPU 耗时分析")
    print(f"{'─'*70}\n")

    tests = [
        ("极短 JSON (5 字段)", TINY_JSON),
        ("小型 JSON (5 字段)", SMALL_JSON),
        ("中型 JSON (11 字段)", MEDIUM_JSON),
        ("大型 JSON (18 字段+嵌套)", LARGE_JSON),
        ("非 JSON (回退路径)", PLAIN_MSG),
    ]

    print(f"{'场景':<28} {'总耗时':>10} {'单次耗时':>12} {'吞吐量':>12}")
    print("-" * 66)

    for name, msg in tests:
        elapsed = _bench_single(msg, ITERATIONS)
        per_call_us = (elapsed / ITERATIONS) * 1e6
        throughput = ITERATIONS / elapsed if elapsed > 0 else float("inf")
        print(f"{name:<28} {elapsed:>8.3f}s {per_call_us:>8.1f}μs {throughput:>8.0f}/s")

    # 混合负载
    print("-" * 66)
    elapsed = _bench_mixed(ITERATIONS)
    per_call_us = (elapsed / ITERATIONS) * 1e6
    throughput = ITERATIONS / elapsed if elapsed > 0 else float("inf")
    print(f"{'混合负载 (真实场景)':<28} {elapsed:>8.3f}s {per_call_us:>8.1f}μs {throughput:>8.0f}/s")

    # ── 2. 内存占用 ──
    print(f"\n{'─'*70}")
    print("  2. 内存占用分析")
    print(f"{'─'*70}\n")

    print(f"{'场景':<28} {'总分配':>10} {'单次分配':>12} {'分配块数':>10}")
    print("-" * 64)

    for name, msg in tests:
        mem = _measure_memory(msg, MEM_ITERATIONS)
        print(f"{name:<28} {mem['total_alloc_kb']:>6.1f}KB {mem['per_call_bytes']:>8}B {mem['alloc_blocks']:>8}")

    # ── 3. 瓶颈分析 ──
    print(f"\n{'─'*70}")
    print("  3. cProfile 瓶颈分析（中型 JSON, 5000 次）")
    print(f"{'─'*70}\n")

    profile_output = _profile_bottlenecks(MEDIUM_JSON, 5000)
    # 只打印前 20 行
    for line in profile_output.split("\n")[:25]:
        print(line)

    # ── 4. 结论 ──
    print(f"\n{'─'*70}")
    print("  4. 分析结论")
    print(f"{'─'*70}\n")

    # 计算关键指标
    small_elapsed = _bench_single(SMALL_JSON, 1000)
    large_elapsed = _bench_single(LARGE_JSON, 1000)
    plain_elapsed = _bench_single(PLAIN_MSG, 1000)

    print(f"  • JSON 解析开销: json.loads + json.dumps 占主要 CPU 时间")
    print(f"  • 小型 JSON 单次: {(small_elapsed/1000)*1e6:.1f}μs")
    print(f"  • 大型 JSON 单次: {(large_elapsed/1000)*1e6:.1f}μs")
    print(f"  • 非 JSON 单次:   {(plain_elapsed/1000)*1e6:.1f}μs")
    print(f"  • JSON 解析倍率:  {((small_elapsed/1000)/((plain_elapsed/1000) or 0.001)):.1f}x (相比非 JSON)")
    print(f"  • 大/小型倍率:    {((large_elapsed/1000)/(small_elapsed/1000 or 0.001)):.1f}x (字段数增加的影响)")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
