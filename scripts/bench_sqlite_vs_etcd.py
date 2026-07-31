#!/usr/bin/env python3
"""SQLite vs etcd 配置读取延迟性能对比（5000 并发）

对比维度:
  1. SQLite: _load_semantic_layer_config()（含 mtime 缓存 + API override）
  2. etcd:   load_config_from_etcd()（模拟网络延迟 1-5ms）

注: etcd3 未安装，用 mock 模拟网络延迟（基于 etcd 公开基准数据）
    etcd 单次 GET: 1-5ms（本地），2-10ms（跨网络）
"""
import sys
import os
import time
import random
import statistics
import threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.orchestrator.orchestrator import Orchestrator


def bench_sqlite(n=5000, concurrency=10):
    """SQLite 方案: 5000 次 _load_semantic_layer_config() 并发读取"""
    # 准备: 写入测试配置
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

    # 预热（触发首次 SQLite 加载）
    Orchestrator._load_semantic_layer_config()

    latencies = []
    lock = threading.Lock()
    barrier = threading.Barrier(concurrency)

    def worker(count):
        barrier.wait()  # 所有线程同时开始
        for _ in range(count):
            t0 = time.perf_counter()
            Orchestrator._load_semantic_layer_config()
            t1 = time.perf_counter()
            with lock:
                latencies.append((t1 - t0) * 1000)

    per_thread = n // concurrency
    threads = [threading.Thread(target=worker, args=(per_thread,)) for _ in range(concurrency)]
    t_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    t_total = time.perf_counter() - t_start

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

    return latencies, t_total


def bench_etcd_mock(n=5000, concurrency=10):
    """etcd 方案: 模拟 5000 次 load_config_from_etcd()（网络延迟 1-5ms）

    基于 etcd 公开基准:
      - 本地 etcd GET: 1-5ms
      - 跨网络 etcd GET: 2-10ms
    """
    # 模拟 etcd 客户端（每次 GET 有 1-5ms 网络延迟）
    ETCD_PREFIX = "/orchestrator/semantic_layer/"
    mock_config = {"min_score": 0.7, "enabled": True, "top_k": 5}

    def mock_etcd_get_prefix(prefix):
        """模拟 etcd get_prefix 网络延迟"""
        delay = random.uniform(1.0, 5.0) / 1000  # 1-5ms
        time.sleep(delay)
        # 返回模拟数据
        results = []
        for key, value in mock_config.items():
            mock_value = type('MockBytes', (), {'decode': lambda self, enc: str(value)})()
            mock_meta = type('MockMeta', (), {'key': type('MockBytes', (), {'decode': lambda self, enc: ETCD_PREFIX + key})()})()
            results.append((mock_value, mock_meta))
        return results

    latencies = []
    lock = threading.Lock()
    barrier = threading.Barrier(concurrency)

    def worker(count):
        barrier.wait()
        for _ in range(count):
            t0 = time.perf_counter()
            # 模拟 load_config_from_etcd 逻辑
            results = mock_etcd_get_prefix(ETCD_PREFIX)
            overrides = {}
            for value, meta in results:
                key = meta.key.decode('utf-8').replace(ETCD_PREFIX, '')
                try:
                    overrides[key] = float(value.decode('utf-8'))
                except (ValueError, TypeError):
                    overrides[key] = value.decode('utf-8')
            t1 = time.perf_counter()
            with lock:
                latencies.append((t1 - t0) * 1000)

    per_thread = n // concurrency
    threads = [threading.Thread(target=worker, args=(per_thread,)) for _ in range(concurrency)]
    t_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    t_total = time.perf_counter() - t_start

    return latencies, t_total


def generate_report(sqlite_result, etcd_result, n=5000, concurrency=10):
    """生成性能对比报告"""
    sqlite_lat, sqlite_total = sqlite_result
    etcd_lat, etcd_total = etcd_result

    sqlite_lat.sort()
    etcd_lat.sort()

    def stats(lat):
        return {
            "p50": statistics.median(lat),
            "p95": lat[int(len(lat) * 0.95)],
            "p99": lat[int(len(lat) * 0.99)],
            "avg": statistics.mean(lat),
            "max": max(lat),
            "qps": len(lat) / (sum(lat) / 1000),
        }

    s = stats(sqlite_lat)
    e = stats(etcd_lat)

    print("\n" + "=" * 70)
    print(" SQLite vs etcd 配置读取延迟对比报告")
    print("=" * 70)
    print(" 测试参数: n=%d, concurrency=%d" % (n, concurrency))
    print("-" * 70)
    print(" %-20s %15s %15s" % ("指标", "SQLite", "etcd(mock)"))
    print("-" * 70)
    print(" %-20s %12.3f ms %12.3f ms" % ("P50 延迟", s["p50"], e["p50"]))
    print(" %-20s %12.3f ms %12.3f ms" % ("P95 延迟", s["p95"], e["p95"]))
    print(" %-20s %12.3f ms %12.3f ms" % ("P99 延迟", s["p99"], e["p99"]))
    print(" %-20s %12.3f ms %12.3f ms" % ("平均延迟", s["avg"], e["avg"]))
    print(" %-20s %12.3f ms %12.3f ms" % ("最大延迟", s["max"], e["max"]))
    print(" %-20s %12.0f/s %15.0f/s" % ("QPS (单线程)", s["qps"], e["qps"]))
    print(" %-20s %12.2f s %15.2f s" % ("总耗时", sqlite_total, etcd_total))
    print("-" * 70)
    print(" 延迟对比 (etcd/SQLite 倍数):")
    print("   P50: %.1fx" % (e["p50"] / s["p50"] if s["p50"] > 0 else 0))
    print("   P99: %.1fx" % (e["p99"] / s["p99"] if s["p99"] > 0 else 0))
    print("   平均: %.1fx" % (e["avg"] / s["avg"] if s["avg"] > 0 else 0))
    print("=" * 70)
    print(" 分析:")
    print("   SQLite 优势: 本地文件读取，无网络开销，mtime 缓存命中后 ~0.01ms")
    print("   etcd 劣势:   每次读取有 1-5ms 网络延迟（即使本地部署）")
    print("   适用场景:")
    print("     SQLite: 单机/小规模部署，配置读取频率高（每次 process() 调用）")
    print("     etcd:   多副本/K8s 部署，需全局共享配置（watch 推送而非轮询）")
    print("   优化建议:")
    print("     etcd 方案应配合 watch 推送 + 本地缓存（避免每次读取都走网络）")
    print("     当前 _SEM_API_OVERRIDE 已是内存读取（~0.01ms），etcd watch 只在变更时更新")
    print("=" * 70)

    # 保存报告到文件
    report_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "PERFORMANCE_REPORT_sqlite_vs_etcd.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# SQLite vs etcd 配置读取延迟对比报告\n\n")
        f.write("## 测试参数\n\n")
        f.write("- 样本数: %d\n- 并发线程: %d\n- etcd 延迟模拟: 1-5ms（本地网络）\n\n" % (n, concurrency))
        f.write("## 结果数据\n\n")
        f.write("| 指标 | SQLite | etcd(mock) | 倍数 |\n")
        f.write("|------|--------|------------|------|\n")
        f.write("| P50 延迟 | %.3f ms | %.3f ms | %.1fx |\n" % (s["p50"], e["p50"], e["p50"]/s["p50"] if s["p50"]>0 else 0))
        f.write("| P95 延迟 | %.3f ms | %.3f ms | %.1fx |\n" % (s["p95"], e["p95"], e["p95"]/s["p95"] if s["p95"]>0 else 0))
        f.write("| P99 延迟 | %.3f ms | %.3f ms | %.1fx |\n" % (s["p99"], e["p99"], e["p99"]/s["p99"] if s["p99"]>0 else 0))
        f.write("| 平均延迟 | %.3f ms | %.3f ms | %.1fx |\n" % (s["avg"], e["avg"], e["avg"]/s["avg"] if s["avg"]>0 else 0))
        f.write("| 最大延迟 | %.3f ms | %.3f ms | %.1fx |\n" % (s["max"], e["max"], e["max"]/s["max"] if s["max"]>0 else 0))
        f.write("| QPS | %.0f/s | %.0f/s | - |\n" % (s["qps"], e["qps"]))
        f.write("| 总耗时 | %.2f s | %.2f s | - |\n\n" % (sqlite_total, etcd_total))
        f.write("## 结论\n\n")
        f.write("- **SQLite** 延迟极低（mtime 缓存命中后 ~0.01ms），适合高频配置读取\n")
        f.write("- **etcd** 每次读取有 1-5ms 网络延迟，适合低频配置同步（watch 推送）\n")
        f.write("- **推荐**: etcd 方案配合 watch 推送 + 本地内存缓存（_SEM_API_OVERRIDE），")
        f.write("运行时配置读取走内存（~0.01ms），etcd 仅在变更时推送\n")

    print("\n 报告已保存: %s" % report_path)
    return report_path


def main():
    n = 5000
    concurrency = 10

    print("=" * 70)
    print(" 性能对比: SQLite vs etcd（n=%d, concurrency=%d）" % (n, concurrency))
    print("=" * 70)

    print("\n[1/2] SQLite 基准测试...")
    sqlite_result = bench_sqlite(n, concurrency)
    print("  完成: %d 次读取, 耗时 %.2fs" % (n, sqlite_result[1]))

    print("\n[2/2] etcd 模拟测试（网络延迟 1-5ms）...")
    etcd_result = bench_etcd_mock(n, concurrency)
    print("  完成: %d 次读取, 耗时 %.2fs" % (n, etcd_result[1]))

    report_path = generate_report(sqlite_result, etcd_result, n, concurrency)
    return 0


if __name__ == "__main__":
    sys.exit(main())
