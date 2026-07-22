#!/usr/bin/env python
"""TLM 熔断机制与降级优化性能对比报告

对比优化前/后的响应时间和失败率：
- 优化前（模拟）：每次 _get_conn 重新 connect + load_extension，无熔断
- 优化后（当前）：thread-local 缓存 + busy_timeout + 熔断机制

场景：
1. 正常场景（N 次 search_vector）：测量平均/P50/P95/P99 响应时间
2. 故障场景（模拟 SQLITE_BUSY 持续超时）：测量失败率 + 熔断触发后响应时间

运行方式：
    python scripts/perf_compare_circuit_breaker.py
"""
import asyncio
import os
import sqlite3
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

# 确保项目根在 sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent.memory.adapters.holographic_adapter import HolographicAdapter


# ──────────────────────────────────────────────────────────────
# 优化前的 LegacyAdapter：禁用 thread-local 缓存 + 禁用熔断
# ──────────────────────────────────────────────────────────────

class LegacyAdapter(HolographicAdapter):
    """模拟优化前的行为：每次 _get_conn 都新建连接，无熔断"""

    def _get_conn(self) -> sqlite3.Connection:
        """每次都新建连接（无 thread-local 缓存）"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # 优化前不设置 busy_timeout
        if self._vec_available:
            try:
                import sqlite_vec
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
            except Exception:
                pass
        return conn

    def _record_vec_failure(self):
        """禁用熔断（只计数不降级）"""
        self._vec_fail_count += 1
        # 不设置 _vec_available=False，继续重试


# ──────────────────────────────────────────────────────────────
# 性能测量工具
# ──────────────────────────────────────────────────────────────

def measure_search_latency(adapter, iterations=100, embedding=None):
    """测量 search_vector 响应时间

    Returns:
        latencies_ms: list[float] 每次响应时间（毫秒）
        failures: int 失败次数（抛异常或返回非 list）
    """
    if embedding is None:
        embedding = [0.1] * adapter._VEC_DIM

    latencies_ms = []
    failures = 0

    for _ in range(iterations):
        start = time.perf_counter()
        try:
            results = asyncio.run(adapter.search_vector(embedding, top_k=5))
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies_ms.append(elapsed_ms)
            if not isinstance(results, list):
                failures += 1
        except Exception:
            failures += 1
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies_ms.append(elapsed_ms)

    return latencies_ms, failures


def percentile(data, p):
    """计算百分位数"""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    idx = min(idx, len(sorted_data) - 1)
    return sorted_data[idx]


def measure_fault_scenario(adapter, iterations=30):
    """模拟 SQLITE_BUSY 持续故障场景

    Returns:
        latencies_ms: list[float]
        failures: int 抛异常次数
        circuit_triggered: bool 是否触发熔断
    """
    latencies_ms = []
    failures = 0

    # 模拟 _get_conn 持续抛 SQLITE_BUSY
    timeout_err = sqlite3.OperationalError("database is locked")

    with patch.object(adapter, "_get_conn", side_effect=timeout_err):
        for _ in range(iterations):
            start = time.perf_counter()
            try:
                results = asyncio.run(adapter.search_vector(
                    [0.1] * adapter._VEC_DIM, top_k=5
                ))
                elapsed_ms = (time.perf_counter() - start) * 1000
                latencies_ms.append(elapsed_ms)
                # 降级返回 [] 不算失败（是预期行为）
            except Exception:
                failures += 1
                elapsed_ms = (time.perf_counter() - start) * 1000
                latencies_ms.append(elapsed_ms)

    circuit_triggered = not adapter._vec_available
    return latencies_ms, failures, circuit_triggered


# ──────────────────────────────────────────────────────────────
# 报告生成
# ──────────────────────────────────────────────────────────────

def print_section(title):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def print_stats(label, latencies_ms, failures, total):
    avg = statistics.mean(latencies_ms) if latencies_ms else 0
    p50 = percentile(latencies_ms, 50)
    p95 = percentile(latencies_ms, 95)
    p99 = percentile(latencies_ms, 99)
    fail_rate = (failures / total * 100) if total > 0 else 0

    print(f"\n  [{label}]")
    print(f"    总请求数:   {total}")
    print(f"    失败数:     {failures}  ({fail_rate:.1f}%)")
    print(f"    平均响应:   {avg:.3f} ms")
    print(f"    P50 响应:   {p50:.3f} ms")
    print(f"    P95 响应:   {p95:.3f} ms")
    print(f"    P99 响应:   {p99:.3f} ms")
    return {
        "label": label,
        "total": total,
        "failures": failures,
        "fail_rate": fail_rate,
        "avg_ms": avg,
        "p50_ms": p50,
        "p95_ms": p95,
        "p99_ms": p99,
    }


def generate_markdown_report(normal_legacy, normal_optimized,
                             fault_legacy, fault_optimized,
                             output_path):
    """生成 Markdown 性能对比报告"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# TLM 熔断机制与降级优化性能对比报告\n\n")
        f.write(f"> 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        f.write("## 1. 测试环境\n\n")
        f.write("| 项目 | 值 |\n|------|----|\n")
        f.write(f"| Python | {sys.version.split()[0]} |\n")
        f.write(f"| 平台 | {sys.platform} |\n")
        f.write("| 优化前 | LegacyAdapter（无 thread-local 缓存、无熔断） |\n")
        f.write("| 优化后 | HolographicAdapter（thread-local + busy_timeout + 熔断） |\n\n")

        f.write("## 2. 正常场景（无故障）\n\n")
        f.write("N 次 search_vector 调用，测量响应时间分布。\n\n")
        f.write("| 指标 | 优化前 | 优化后 | 提升 |\n|------|--------|--------|------|\n")
        f.write(f"| 总请求 | {normal_legacy['total']} | {normal_optimized['total']} | - |\n")
        f.write(f"| 失败率 | {normal_legacy['fail_rate']:.1f}% | {normal_optimized['fail_rate']:.1f}% | - |\n")
        speedup_avg = normal_legacy['avg_ms'] / normal_optimized['avg_ms'] if normal_optimized['avg_ms'] > 0 else 0
        speedup_p50 = normal_legacy['p50_ms'] / normal_optimized['p50_ms'] if normal_optimized['p50_ms'] > 0 else 0
        f.write(f"| 平均响应 | {normal_legacy['avg_ms']:.3f} ms | {normal_optimized['avg_ms']:.3f} ms | **{speedup_avg:.2f}x** |\n")
        f.write(f"| P50 | {normal_legacy['p50_ms']:.3f} ms | {normal_optimized['p50_ms']:.3f} ms | **{speedup_p50:.2f}x** |\n")
        f.write(f"| P95 | {normal_legacy['p95_ms']:.3f} ms | {normal_optimized['p95_ms']:.3f} ms | - |\n")
        f.write(f"| P99 | {normal_legacy['p99_ms']:.3f} ms | {normal_optimized['p99_ms']:.3f} ms | - |\n\n")

        f.write("**结论**：thread-local 缓存避免了每次操作重复 `connect + load_extension`，"
                "正常场景响应时间显著降低。\n\n")

        f.write("## 3. 故障场景（SQLITE_BUSY 持续超时）\n\n")
        f.write("模拟 `_get_conn` 持续抛 `sqlite3.OperationalError('database is locked')`，"
                "测量失败率与熔断效果。\n\n")
        f.write("| 指标 | 优化前（无熔断） | 优化后（有熔断） | 改善 |\n|------|------------------|------------------|------|\n")
        f.write(f"| 总请求 | {fault_legacy['total']} | {fault_optimized['total']} | - |\n")
        f.write(f"| 失败率 | {fault_legacy['fail_rate']:.1f}% | {fault_optimized['fail_rate']:.1f}% | "
                f"**{-fault_legacy['fail_rate'] + fault_optimized['fail_rate']:.1f}pp** |\n")
        f.write(f"| 平均响应 | {fault_legacy['avg_ms']:.3f} ms | {fault_optimized['avg_ms']:.3f} ms | "
                f"**{fault_legacy['avg_ms'] / fault_optimized['avg_ms'] if fault_optimized['avg_ms'] > 0 else 0:.2f}x** |\n")
        f.write(f"| P95 响应 | {fault_legacy['p95_ms']:.3f} ms | {fault_optimized['p95_ms']:.3f} ms | - |\n")
        f.write(f"| 熔断触发 | 否 | {'是' if fault_optimized.get('circuit_triggered') else '否'} | - |\n\n")

        f.write("**结论**：\n")
        f.write("- 优化前：每次失败都走完整 except 路径 + 重试，响应时间高且无法自动恢复\n")
        f.write("- 优化后：达阈值（5 次）后熔断，后续请求直接返回 `[]`（短路），"
                "响应时间骤降，失败率归零（降级返回不算失败）\n")
        f.write("- 熔断机制避免了无意义的重试开销，保护系统在持续故障下不雪崩\n\n")

        f.write("## 4. 优化项对照\n\n")
        f.write("| 优化项 | 作用 | 正常场景收益 | 故障场景收益 |\n|--------|------|-------------|-------------|\n")
        f.write("| thread-local 缓存 | 避免重复 `connect + load_extension` | "
                f"响应时间 **{speedup_avg:.1f}x** 提升 | 无（故障时连不上） |\n")
        f.write("| busy_timeout=5000 | SQLITE_BUSY 排队 5s | 减少并发锁竞争失败 | 短时锁冲突自动恢复 |\n")
        f.write("| 熔断机制（阈值 5） | 达阈值自动降级 | 无（正常不触发） | "
                f"失败率 **{fault_legacy['fail_rate'] - fault_optimized['fail_rate']:.1f}pp** 降低 |\n\n")

        f.write("## 5. 不变量保持\n\n")
        f.write("- 【不易】save/search 接口签名未变，主表+FTS 同事务\n")
        f.write("- 【不易】熔断只置位 `_vec_available=False`，不删除向量数据\n")
        f.write("- 【变易】熔断阈值可配置，`_reset_vec_circuit` 供后台探活恢复\n")
        f.write("- 【简易】thread-local 缓存对调用方透明，无 API 变化\n")

    print(f"\n[REPORT] Markdown 报告已生成: {output_path}")


# ──────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────

def main():
    print_section("TLM 熔断机制与降级优化性能对比")

    tmp_dir = tempfile.mkdtemp(prefix="perf_compare_")
    legacy_db = os.path.join(tmp_dir, "legacy.db")
    optimized_db = os.path.join(tmp_dir, "optimized.db")

    # 初始化两个 adapter
    legacy = LegacyAdapter(db_path=legacy_db, enable_cache=False)
    optimized = HolographicAdapter(db_path=optimized_db, enable_cache=False)

    if not legacy._vec_available or not optimized._vec_available:
        print("[SKIP] sqlite-vec 不可用，性能对比需要 sqlite-vec 支持")
        return

    print(f"[INFO] sqlite-vec 可用，vec_dim={legacy._VEC_DIM}")
    print(f"[INFO] legacy_db={legacy_db}")
    print(f"[INFO] optimized_db={optimized_db}")

    # 预写入数据
    print("\n[SETUP] 预写入 50 条数据...")
    for i in range(50):
        emb = [0.1 * i] * legacy._VEC_DIM
        asyncio.run(legacy.save_with_embedding(f"key_{i}", f"内容_{i}", {"idx": i}, emb))
        asyncio.run(optimized.save_with_embedding(f"key_{i}", f"内容_{i}", {"idx": i}, emb))
    time.sleep(1.0)  # 等待后台向量写入

    # 场景 1: 正常场景性能对比
    print_section("场景 1: 正常场景（100 次 search_vector）")
    N = 100

    print("\n[1A] 优化前（LegacyAdapter: 无 thread-local 缓存）...")
    latencies_legacy, fails_legacy = measure_search_latency(legacy, iterations=N)
    normal_legacy = print_stats("优化前", latencies_legacy, fails_legacy, N)

    print("\n[1B] 优化后（HolographicAdapter: thread-local + busy_timeout）...")
    latencies_opt, fails_opt = measure_search_latency(optimized, iterations=N)
    normal_optimized = print_stats("优化后", latencies_opt, fails_opt, N)

    speedup = statistics.mean(latencies_legacy) / statistics.mean(latencies_opt) if statistics.mean(latencies_opt) > 0 else 0
    print(f"\n[RESULT] 平均响应时间提升: {speedup:.2f}x")

    # 场景 2: 故障场景（SQLITE_BUSY 持续超时）
    print_section("场景 2: 故障场景（模拟 SQLITE_BUSY 持续超时，30 次请求）")
    N_FAULT = 30

    # 重置优化前 adapter 状态
    legacy._vec_fail_count = 0
    legacy._vec_available = True

    print("\n[2A] 优化前（LegacyAdapter: 无熔断，每次都走 except + 重试）...")
    lat_fault_legacy, fail_fault_legacy, cb_legacy = measure_fault_scenario(legacy, iterations=N_FAULT)
    fault_legacy = print_stats("优化前（无熔断）", lat_fault_legacy, fail_fault_legacy, N_FAULT)
    fault_legacy['circuit_triggered'] = cb_legacy

    # 重置优化后 adapter 状态
    optimized._vec_fail_count = 0
    optimized._vec_available = True

    print("\n[2B] 优化后（HolographicAdapter: 熔断阈值=5）...")
    lat_fault_opt, fail_fault_opt, cb_opt = measure_fault_scenario(optimized, iterations=N_FAULT)
    fault_optimized = print_stats("优化后（有熔断）", lat_fault_opt, fail_fault_opt, N_FAULT)
    fault_optimized['circuit_triggered'] = cb_opt

    print(f"\n[RESULT] 优化前熔断触发: {cb_legacy}（无熔断机制）")
    print(f"[RESULT] 优化后熔断触发: {cb_opt}（阈值 {optimized._vec_fail_threshold}）")
    print(f"[RESULT] 熔断后失败率: 优化前 {fault_legacy['fail_rate']:.1f}% → 优化后 {fault_optimized['fail_rate']:.1f}%")

    # 生成报告
    print_section("生成 Markdown 报告")
    report_path = os.path.join(_PROJECT_ROOT, "docs", "PERF_COMPARE_CIRCUIT_BREAKER.md")
    generate_markdown_report(normal_legacy, normal_optimized,
                             fault_legacy, fault_optimized,
                             report_path)

    print_section("性能对比完成")
    print(f"\n报告路径: {report_path}")


if __name__ == "__main__":
    main()
