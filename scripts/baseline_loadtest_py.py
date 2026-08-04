"""Python 基线压测脚本 — k6 baseline_skill_match.js 的等价实现

【不易】对齐 k6 baseline_skill_match.js:
    - 20 VU × 60s × 5 QPS/VU = 100 QPS
    - thresholds: p99<40ms / p95<25ms / 错误率<1% / 总请求>5400
    - 8 个测试查询（完全复制 k6 TEST_QUERIES）
    - POST /match，校验 status 200 + matches/match_count
    - 输出 baseline_report.json + 控制台摘要

【变易】k6 未安装环境的等价替代:
    - urllib 标准库（无 requests 依赖）
    - threading 实现 VU 并发
    - 纯 Python 百分位计算（无 numpy 依赖）
    - 内置监控线程：压测中实时抓取 Yunshu_active_connections，验证指标上报链路

【简易】单文件自包含，环境变量参数化:
    - ENDPOINT (默认 http://127.0.0.1:8080/match)
    - METRICS_URL (默认 http://127.0.0.1:9091/metrics)
    - DURATION (默认 60，秒)
    - VUS (默认 20)

运行:
    python scripts/baseline_loadtest_py.py
    DURATION=15 python scripts/baseline_loadtest_py.py  # 快速验证

注: 用 127.0.0.1 而非 localhost，避免 Windows IPv6 (::1) fallback 延迟；
    VU 用 http.client.HTTPConnection 复用 TCP 连接，避免短连接建立开销。
"""
from __future__ import annotations

import http.client
import json
import os
import random
import threading
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse

# ═══════════════════════════════════════════════════════════════════
#  配置（对齐 k6 baseline_skill_match.js options）
# ═══════════════════════════════════════════════════════════════════

ENDPOINT = os.environ.get("ENDPOINT", "http://127.0.0.1:8080/match")
METRICS_URL = os.environ.get("METRICS_URL", "http://127.0.0.1:9091/metrics")
_TARGET = urlparse(ENDPOINT)
_TARGET_HOST = _TARGET.hostname or "127.0.0.1"
_TARGET_PORT = _TARGET.port or 80
_TARGET_PATH = _TARGET.path or "/match"
VUS = int(os.environ.get("VUS", "20"))
DURATION = int(os.environ.get("DURATION", "60"))
SLEEP_PER_REQ = 0.2  # 5 QPS/VU → 100 QPS 总

# 【不易】完全复制 k6 TEST_QUERIES
TEST_QUERIES = [
    {"query": "帮我解析PDF文件并提取表格数据", "top_k": 5},
    {"query": "生成一份市场分析报告", "top_k": 5},
    {"query": "创建一个Python脚本自动化任务", "top_k": 5},
    {"query": "帮我反思刚才的回答质量", "top_k": 3},
    {"query": "翻译这段英文到中文", "top_k": 3},
    {"query": "总结会议记录要点", "top_k": 5},
    {"query": "调试JavaScript运行时错误", "top_k": 5},
    {"query": "部署应用到Kubernetes集群", "top_k": 5},
]

# thresholds（对齐 HPA 触发阈值）
TH_P99_MS = 40.0
TH_P95_MS = 25.0
TH_ERROR_RATE = 0.01
TH_MIN_REQUESTS = 5400  # 100 QPS × 60s × 90%

# ═══════════════════════════════════════════════════════════════════
#  线程安全统计
# ═══════════════════════════════════════════════════════════════════

_lock = threading.Lock()
_latencies: list[float] = []  # ms
_success = 0
_failure = 0
_status_counts: Counter = Counter()


def _record(latency_ms: float, ok: bool, status: int):
    global _success, _failure
    with _lock:
        _latencies.append(latency_ms)
        _status_counts[status] += 1
        if ok:
            _success += 1
        else:
            _failure += 1


# ═══════════════════════════════════════════════════════════════════
#  VU 工作线程 — 每线程 5 QPS
# ═══════════════════════════════════════════════════════════════════


def _vu_worker(stop_at: float, vu_id: int):
    """单个 VU：循环发请求直到 stop_at，每请求后 sleep 0.2s

    【简易】复用单个 HTTPConnection（keep-alive），避免短连接建立开销。
    首版用 urllib 每请求新建连接，在 Windows 上受 localhost IPv6 fallback +
    代理探测影响，单次连接建立 ~2s，导致 20 VU 实际仅 8.7 QPS。改用
    http.client 持久连接后，延迟回归 mock 设计的 5-35ms 区间。
    """
    conn = http.client.HTTPConnection(_TARGET_HOST, _TARGET_PORT, timeout=5)
    try:
        while time.time() < stop_at:
            q = random.choice(TEST_QUERIES)
            payload = json.dumps({"query": q["query"], "top_k": q["top_k"]})
            headers = {"Content-Type": "application/json"}
            start = time.perf_counter()
            ok = False
            status = 0
            try:
                conn.request("POST", _TARGET_PATH, body=payload, headers=headers)
                resp = conn.getresponse()
                status = resp.status
                body = json.loads(resp.read().decode("utf-8"))
                ok = status == 200 and ("matches" in body or "match_count" in body)
            except Exception:  # noqa: BLE001  连接错误计为失败并重连
                status = 0
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = http.client.HTTPConnection(_TARGET_HOST, _TARGET_PORT, timeout=5)
            elapsed_ms = (time.perf_counter() - start) * 1000
            _record(elapsed_ms, ok, status)
            time.sleep(SLEEP_PER_REQ)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


# ═══════════════════════════════════════════════════════════════════
#  监控线程 — 压测中实时抓取 Yunshu_active_connections
#  【变易】任务3：验证 active_connections 指标实时上报到 Prometheus
# ═══════════════════════════════════════════════════════════════════

_monitor_samples: list[dict] = []
_monitor_lock = threading.Lock()


def _parse_metric(text: str, name: str) -> float | None:
    """从 /metrics 文本中解析某指标值（取第一个非注释行）"""
    for line in text.splitlines():
        if line.startswith(name + " ") or line.startswith(name + "{"):
            # 取行末尾的数值（最后一个 token）
            try:
                return float(line.split()[-1])
            except (ValueError, IndexError):
                return None
    return None


def _monitor_worker(stop_at: float):
    """每 0.5s 抓取一次 active_connections + skill_match_count_total"""
    while time.time() < stop_at:
        try:
            with urllib.request.urlopen(METRICS_URL, timeout=2) as resp:
                text = resp.read().decode("utf-8")
            active = _parse_metric(text, "Yunshu_active_connections")
            total = _parse_metric(text, "skill_match_count_total")
            ts = time.time()
            with _monitor_lock:
                _monitor_samples.append({
                    "t": round(ts, 3),
                    "active_connections": active,
                    "skill_match_count_total": total,
                })
        except Exception:  # noqa: BLE001  监控失败不影响压测
            pass
        time.sleep(0.5)


# ═══════════════════════════════════════════════════════════════════
#  百分位计算（纯 Python，无 numpy 依赖）
# ═══════════════════════════════════════════════════════════════════


def _percentile(sorted_data: list[float], p: float) -> float:
    """线性插值百分位（对齐 numpy 默认 linear 方法）"""
    if not sorted_data:
        return 0.0
    if len(sorted_data) == 1:
        return sorted_data[0]
    k = (len(sorted_data) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


# ═══════════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════════


def main():
    print("=" * 70)
    print("  Python 基线压测 — k6 baseline_skill_match.js 等价实现")
    print("=" * 70)
    print(f"  Endpoint:    {ENDPOINT}")
    print(f"  Metrics:     {METRICS_URL}")
    print(f"  VUs:         {VUS}")
    print(f"  Duration:    {DURATION}s")
    print(f"  Target QPS:  {VUS * 5} ({VUS} VU × 5 QPS/VU)")
    print(f"  Thresholds:  p99<{TH_P99_MS}ms  p95<{TH_P95_MS}ms  "
          f"err<{TH_ERROR_RATE*100}%  reqs>{TH_MIN_REQUESTS}")
    print("=" * 70)
    print()

    # 预检：/health
    try:
        with urllib.request.urlopen(
            ENDPOINT.replace("/match", "/health"), timeout=3
        ) as r:
            print(f"[PREFLIGHT] /health: {r.read().decode()}")
    except Exception as e:  # noqa: BLE001
        print(f"[PREFLIGHT][FAIL] /health 不可达: {e}")
        return

    print()
    print(f"[START] 压测开始 — {VUS} VU × {DURATION}s")
    t0 = time.time()
    stop_at = t0 + DURATION

    # 启动 VU 线程
    vu_threads = []
    for i in range(VUS):
        t = threading.Thread(target=_vu_worker, args=(stop_at, i), daemon=True)
        t.start()
        vu_threads.append(t)

    # 启动监控线程（任务3：验证 active_connections 实时上报）
    mon_thread = threading.Thread(target=_monitor_worker, args=(stop_at,), daemon=True)
    mon_thread.start()

    # 实时进度（每 10s 输出一次）
    while time.time() < stop_at:
        time.sleep(10)
        with _lock:
            done = len(_latencies)
            cur_active = None
            with _monitor_lock:
                if _monitor_samples:
                    cur_active = _monitor_samples[-1].get("active_connections")
        elapsed = time.time() - t0
        print(f"  [progress] {elapsed:.0f}s | reqs={done} | "
              f"active_connections={cur_active}")

    # 等待线程收尾（给在途请求 2s 缓冲）
    for t in vu_threads:
        t.join(timeout=2)
    mon_thread.join(timeout=1)

    wall = time.time() - t0
    print()
    print("[DONE] 压测结束，生成报告...")

    # ── 计算统计 ──
    with _lock:
        lat_sorted = sorted(_latencies)
        total = len(lat_sorted)
        p50 = _percentile(lat_sorted, 50)
        p95 = _percentile(lat_sorted, 95)
        p99 = _percentile(lat_sorted, 99)
        succ = _success
        fail = _failure
        statuses = dict(_status_counts)

    actual_qps = total / wall if wall > 0 else 0
    error_rate = fail / total if total > 0 else 1.0

    # thresholds 判定
    th_results = {
        f"p99_latency_ms < {TH_P99_MS}": p99 < TH_P99_MS,
        f"p95_latency_ms < {TH_P95_MS}": p95 < TH_P95_MS,
        f"error_rate < {TH_ERROR_RATE*100}%": error_rate < TH_ERROR_RATE,
        f"total_requests > {TH_MIN_REQUESTS}": total > TH_MIN_REQUESTS,
    }
    all_pass = all(th_results.values())

    # 监控摘要（任务3）
    with _monitor_lock:
        active_values = [s["active_connections"] for s in _monitor_samples
                         if s["active_connections"] is not None]
        count_values = [s["skill_match_count_total"] for s in _monitor_samples
                        if s["skill_match_count_total"] is not None]
    active_max = max(active_values) if active_values else 0
    active_samples = len(active_values)
    count_first = count_values[0] if count_values else None
    count_last = count_values[-1] if count_values else None

    # ── 控制台摘要 ──
    print()
    print("=" * 70)
    print("  基线压测报告 — 5000 技能量级（Python 等价实现）")
    print("=" * 70)
    print(f"  总请求:        {total}")
    print(f"  实际 QPS:      {actual_qps:.1f} (目标 {VUS*5})")
    print(f"  墙钟耗时:      {wall:.2f}s")
    print(f"  延迟 p50:      {p50:.2f}ms")
    print(f"  延迟 p95:      {p95:.2f}ms (阈值 <{TH_P95_MS}ms)")
    print(f"  延迟 p99:      {p99:.2f}ms (阈值 <{TH_P99_MS}ms)")
    print(f"  错误率:        {error_rate*100:.2f}% (阈值 <{TH_ERROR_RATE*100}%)")
    print(f"  成功/失败:     {succ} / {fail}")
    print(f"  状态码分布:    {statuses}")
    print()
    print("  Thresholds:")
    for k, v in th_results.items():
        print(f"    [{'✓' if v else '✗'}] {k}")
    print(f"  总判定:        {'PASS ✓' if all_pass else 'FAIL ✗'}")
    print()
    print("  ── 任务3: Yunshu_active_connections 实时上报验证 ──")
    print(f"  监控样本数:    {active_samples}")
    print(f"  active_connections 峰值: {active_max}")
    print(f"  skill_match_count_total: {count_first} → {count_last} "
          f"(增量 {((count_last - count_first) if (count_first is not None and count_last is not None) else 'N/A')})")
    active_reported = active_max > 0 or (count_last is not None and count_last > 0)
    print(f"  指标实时上报:  {'✓ 已上报到 Prometheus' if active_reported else '✗ 未上报'}")
    if active_samples > 0 and active_max > 0:
        print(f"  结论: 压测期间 active_connections 峰值={active_max}，"
              f"证明 Gauge 在并发请求时被实时更新并暴露到 /metrics")
    print("=" * 70)

    # ── 写 JSON 报告 ──
    report = {
        "test_name": "baseline_skill_match_py",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": "python_baseline_loadtest (k6 equivalent)",
        "config": {
            "vus": VUS,
            "duration_s": DURATION,
            "target_qps": VUS * 5,
            "endpoint": ENDPOINT,
        },
        "results": {
            "total_requests": total,
            "actual_qps": round(actual_qps, 2),
            "wall_time_s": round(wall, 2),
            "latency_p50_ms": round(p50, 2),
            "latency_p95_ms": round(p95, 2),
            "latency_p99_ms": round(p99, 2),
            "error_rate": round(error_rate, 4),
            "match_success": succ,
            "match_failure": fail,
            "status_codes": statuses,
        },
        "thresholds": th_results,
        "thresholds_all_passed": all_pass,
        "active_connections_monitoring": {
            "samples": active_samples,
            "peak_active_connections": active_max,
            "skill_match_count_total_first": count_first,
            "skill_match_count_total_last": count_last,
            "metric_reported_to_prometheus": active_reported,
            "sample_series": _monitor_samples[:10],  # 前 10 个样本供检视
        },
    }
    report_path = os.path.join(os.path.dirname(__file__), "baseline_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[REPORT] JSON 报告已写入: {report_path}")


if __name__ == "__main__":
    main()
