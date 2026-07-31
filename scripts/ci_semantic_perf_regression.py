#!/usr/bin/env python3
"""CI/CD 语义层配置读取性能回归检测

在每次代码提交后自动运行 5000 并发压力测试，对比 SQLite 和 etcd 方案的 P99 延迟，
防止性能回归。

使用方式:
  # CI/CD 中调用（性能回归时返回非零退出码，阻止合并）
  python scripts/ci_semantic_perf_regression.py

  # 本地运行（更新基线）
  python scripts/ci_semantic_perf_regression.py --update-baseline

基线文件: scripts/perf_baseline.json
报告文件: scripts/perf_report_latest.json

回归判定:
  - SQLite P99 > 5ms（基线 5 倍）→ 回归
  - etcd P99 > 40ms（SLA 阈值）→ 回归
  - 退出码 0 = 通过, 1 = 回归
"""
import sys
import os
import json
import time
import random
import statistics
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 基线文件路径
BASELINE_FILE = Path(__file__).parent / "perf_baseline.json"
REPORT_FILE = Path(__file__).parent / "perf_report_latest.json"

# 回归阈值
SQLITE_P99_REGRESSION_MS = 5.0    # SQLite P99 > 5ms 判定回归（基线 ~1ms × 5）
ETCD_P99_SLA_MS = 40.0            # etcd P99 > 40ms 判定回归（SLA 阈值）


def bench_sqlite(n=5000, concurrency=10):
    """SQLite 方案: n 次 _load_semantic_layer_config() 并发读取"""
    from agent.orchestrator.orchestrator import Orchestrator

    # 准备: 写入测试配置 + 清理状态
    Orchestrator._SEM_API_OVERRIDE = None
    Orchestrator._SEM_DB_LOADED = False
    Orchestrator._clear_semantic_config_cache()
    try:
        conn = Orchestrator._get_semantic_db_conn()
        conn.execute("DELETE FROM semantic_config_overrides")
        conn.execute("INSERT INTO semantic_config_overrides (key, value, updated_at) VALUES (?, ?, ?)",
                     ("min_score", "0.7", "2026-08-01T00:00:00"))
        conn.commit()
    except Exception:
        pass

    # 预热
    Orchestrator._load_semantic_layer_config()

    latencies = []
    lock = threading.Lock()
    barrier = threading.Barrier(concurrency)

    def worker(count):
        barrier.wait()
        for _ in range(count):
            t0 = time.perf_counter()
            Orchestrator._load_semantic_layer_config()
            t1 = time.perf_counter()
            with lock:
                latencies.append((t1 - t0) * 1000)

    per_thread = n // concurrency
    threads = [threading.Thread(target=worker, args=(per_thread,)) for _ in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 清理
    Orchestrator._SEM_API_OVERRIDE = None
    Orchestrator._SEM_DB_LOADED = False
    Orchestrator._clear_semantic_config_cache()
    try:
        conn = Orchestrator._get_semantic_db_conn()
        conn.execute("DELETE FROM semantic_config_overrides")
        conn.commit()
    except Exception:
        pass

    return latencies


def bench_etcd_mock(n=5000, concurrency=10):
    """etcd 方案: 模拟 n 次读取（网络延迟 1-5ms）"""
    ETCD_PREFIX = "/orchestrator/semantic_layer/"
    mock_config = {"min_score": 0.7, "enabled": True, "top_k": 5}

    def mock_etcd_get_prefix(prefix):
        delay = random.uniform(1.0, 5.0) / 1000
        time.sleep(delay)
        results = []
        for key, value in mock_config.items():
            mock_value = type('MB', (), {'decode': lambda self, e: str(value)})()
            mock_meta = type('MM', (), {'key': type('MB', (), {'decode': lambda self, e: ETCD_PREFIX + key})()})()
            results.append((mock_value, mock_meta))
        return results

    latencies = []
    lock = threading.Lock()
    barrier = threading.Barrier(concurrency)

    def worker(count):
        barrier.wait()
        for _ in range(count):
            t0 = time.perf_counter()
            results = mock_etcd_get_prefix(ETCD_PREFIX)
            t1 = time.perf_counter()
            with lock:
                latencies.append((t1 - t0) * 1000)

    per_thread = n // concurrency
    threads = [threading.Thread(target=worker, args=(per_thread,)) for _ in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return latencies


def calc_stats(latencies):
    """计算百分位数统计"""
    latencies.sort()
    n = len(latencies)
    return {
        "p50": round(statistics.median(latencies), 3),
        "p95": round(latencies[int(n * 0.95)], 3),
        "p99": round(latencies[int(n * 0.99)], 3),
        "avg": round(statistics.mean(latencies), 3),
        "max": round(max(latencies), 3),
        "count": n,
    }


def load_baseline():
    """加载基线数据"""
    if BASELINE_FILE.exists():
        with open(BASELINE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_baseline(sqlite_stats, etcd_stats):
    """保存基线数据"""
    baseline = {
        "sqlite": sqlite_stats,
        "etcd": etcd_stats,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with open(BASELINE_FILE, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2, ensure_ascii=False)
    print("[基线] 已保存到 %s" % BASELINE_FILE)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="CI/CD 语义层性能回归检测")
    parser.add_argument("--update-baseline", action="store_true", help="更新基线（首次运行或基线丢失时使用）")
    parser.add_argument("--n", type=int, default=5000, help="测试样本数（默认 5000）")
    parser.add_argument("--concurrency", type=int, default=10, help="并发线程数（默认 10）")
    args = parser.parse_args()

    n = args.n
    concurrency = args.concurrency

    print("=" * 60)
    print(" CI/CD 性能回归检测: 语义层配置读取")
    print("=" * 60)
    print(" 参数: n=%d, concurrency=%d" % (n, concurrency))

    # 运行基准测试
    print("\n[1/2] SQLite 基准测试...")
    sqlite_lat = bench_sqlite(n, concurrency)
    sqlite_stats = calc_stats(sqlite_lat)
    print("  P50=%.3fms P95=%.3fms P99=%.3fms" % (
        sqlite_stats["p50"], sqlite_stats["p95"], sqlite_stats["p99"]))

    print("\n[2/2] etcd 模拟测试...")
    etcd_lat = bench_etcd_mock(n, concurrency)
    etcd_stats = calc_stats(etcd_lat)
    print("  P50=%.3fms P95=%.3fms P99=%.3fms" % (
        etcd_stats["p50"], etcd_stats["p95"], etcd_stats["p99"]))

    # 保存报告
    report = {
        "sqlite": sqlite_stats,
        "etcd": etcd_stats,
        "test_params": {"n": n, "concurrency": concurrency},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # 更新基线模式
    if args.update_baseline:
        save_baseline(sqlite_stats, etcd_stats)
        print("\n✅ 基线已更新，退出")
        return 0

    # 回归检测
    baseline = load_baseline()
    print("\n" + "-" * 60)
    print(" 回归检测")
    print("-" * 60)

    regressions = []

    # SQLite 回归检测
    sqlite_p99 = sqlite_stats["p99"]
    if sqlite_p99 > SQLITE_P99_REGRESSION_MS:
        regressions.append("SQLite P99=%.3fms 超过回归阈值 %.1fms" % (
            sqlite_p99, SQLITE_P99_REGRESSION_MS))
        print("  ❌ SQLite P99=%.3fms > 阈值 %.1fms（回归）" % (
            sqlite_p99, SQLITE_P99_REGRESSION_MS))
    else:
        print("  ✅ SQLite P99=%.3fms <= 阈值 %.1fms（通过）" % (
            sqlite_p99, SQLITE_P99_REGRESSION_MS))

    # etcd 回归检测
    etcd_p99 = etcd_stats["p99"]
    if etcd_p99 > ETCD_P99_SLA_MS:
        regressions.append("etcd P99=%.3fms 超过 SLA 阈值 %.1fms" % (
            etcd_p99, ETCD_P99_SLA_MS))
        print("  ❌ etcd P99=%.3fms > SLA %.1fms（回归）" % (
            etcd_p99, ETCD_P99_SLA_MS))
    else:
        print("  ✅ etcd P99=%.3fms <= SLA %.1fms（通过）" % (
            etcd_p99, ETCD_P99_SLA_MS))

    # 与基线对比（如果存在）
    if baseline:
        base_sqlite_p99 = baseline.get("sqlite", {}).get("p99", 0)
        base_etcd_p99 = baseline.get("etcd", {}).get("p99", 0)
        sqlite_delta = ((sqlite_p99 - base_sqlite_p99) / base_sqlite_p99 * 100) if base_sqlite_p99 > 0 else 0
        etcd_delta = ((etcd_p99 - base_etcd_p99) / base_etcd_p99 * 100) if base_etcd_p99 > 0 else 0
        print("\n  基线对比:")
        print("    SQLite P99: %.3fms → %.3fms (%s%.1f%%)" % (
            base_sqlite_p99, sqlite_p99, "+" if sqlite_delta >= 0 else "", sqlite_delta))
        print("    etcd P99:   %.3fms → %.3fms (%s%.1f%%)" % (
            base_etcd_p99, etcd_p99, "+" if etcd_delta >= 0 else "", etcd_delta))

        # 基线回归: SQLite P99 比基线差 50% 以上（实际代码，基线稳定）
        if sqlite_delta > 50:
            regressions.append("SQLite P99 比基线慢 %.1f%%" % sqlite_delta)
        # etcd 为 mock 模拟（random.uniform 随机延迟），P99 波动大，仅检查 SLA 阈值，不做基线偏移回归
    else:
        print("\n  ⚠️ 无基线数据，首次运行请用 --update-baseline 建立基线")
        print("     python scripts/ci_semantic_perf_regression.py --update-baseline")

    # 结论
    print("\n" + "=" * 60)
    if regressions:
        print(" ❌ 性能回归: %d 项" % len(regressions))
        for r in regressions:
            print("    - %s" % r)
        print(" 报告: %s" % REPORT_FILE)
        print("=" * 60)
        return 1  # 回归，阻止合并
    else:
        print(" ✅ 性能回归检测通过")
        print(" 报告: %s" % REPORT_FILE)
        print("=" * 60)
        return 0  # 通过


if __name__ == "__main__":
    sys.exit(main())
