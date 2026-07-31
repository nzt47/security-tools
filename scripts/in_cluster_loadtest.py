"""集群内压测脚本 — 消除 kubectl port-forward 代理开销

【不易】直接访问 Service ClusterIP DNS，测量真实端到端延迟（无 port-forward 干扰）
【变易】aiohttp 异步并发，模拟 k6 的 VU 模型
【简易】单文件，输出 P50/P90/P95/P99/Max + HPA 触发率

用法: kubectl exec -n production <pod> -- python /tmp/in_cluster_loadtest.py <scenario>
场景: baseline(20vu/60s) | burst(40vu/60s) | stress(50vu/120s)
"""
import asyncio
import aiohttp
import time
import sys
import json
import statistics

# Service ClusterIP DNS — 集群内直接访问，无 port-forward 开销
ENDPOINT = "http://skill-retrieval-service.production.svc.cluster.local:8080/match"

SCENARIOS = {
    "baseline": {"vus": 20, "duration": 60, "target_qps": 100},
    "burst":    {"vus": 40, "duration": 60, "target_qps": 200},
    "stress":   {"vus": 50, "duration": 120, "target_qps": 250},
}

HPA_THRESHOLD_MS = 40.0  # HPA 扩容阈值


async def send_request(session, latencies, over_threshold):
    """发送单个请求并记录延迟"""
    payload = {"query": "PDF解析测试", "top_k": 5}
    start = time.perf_counter()
    try:
        async with session.post(ENDPOINT, json=payload) as resp:
            await resp.read()
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)
            if elapsed_ms > HPA_THRESHOLD_MS:
                over_threshold[0] += 1
    except Exception as e:
        latencies.append(-1)  # 标记错误


async def vu_worker(session, latencies, over_threshold, duration_s, target_qps_per_vu):
    """单个 VU 工作循环"""
    end_time = time.time() + duration_s
    interval = 1.0 / target_qps_per_vu if target_qps_per_vu > 0 else 0
    while time.time() < end_time:
        await send_request(session, latencies, over_threshold)
        if interval > 0:
            await asyncio.sleep(interval)


async def run_scenario(scenario_name):
    """运行单个压测场景"""
    config = SCENARIOS[scenario_name]
    vus = config["vus"]
    duration = config["duration"]
    target_qps = config["target_qps"]
    qps_per_vu = target_qps / vus

    print(f"\n{'='*60}")
    print(f"  场景: {scenario_name} | {vus} VU × {duration}s | 目标 {target_qps} QPS")
    print(f"  端点: {ENDPOINT}")
    print(f"{'='*60}")

    latencies = []
    over_threshold = [0]  # 用 list 包装以便闭包修改

    # TCP connector 限制并发连接数，避免端口耗尽
    connector = aiohttp.TCPConnector(limit=vus * 2, limit_per_host=vus * 2)
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # 预热请求
        try:
            async with session.post(ENDPOINT, json={"query": "warmup", "top_k": 1}) as r:
                await r.read()
            print(f"  [OK] 预热请求成功")
        except Exception as e:
            print(f"  [ERR] 预热失败: {e}")
            return

        print(f"  开始压测... ({duration}s)")
        start_time = time.time()

        # 启动 VU workers
        workers = [vu_worker(session, latencies, over_threshold, duration, qps_per_vu)
                   for _ in range(vus)]
        await asyncio.gather(*workers)

        actual_duration = time.time() - start_time

    # 过滤错误请求
    valid_latencies = [l for l in latencies if l > 0]
    errors = len([l for l in latencies if l < 0])
    total = len(latencies)

    if not valid_latencies:
        print(f"  [ERR] 无有效请求")
        return

    # 计算百分位
    valid_latencies.sort()
    n = len(valid_latencies)

    def percentile(p):
        idx = int(n * p / 100)
        if idx >= n:
            idx = n - 1
        return valid_latencies[idx]

    p50 = percentile(50)
    p90 = percentile(90)
    p95 = percentile(95)
    p99 = percentile(99)
    avg = statistics.mean(valid_latencies)
    mx = max(valid_latencies)
    actual_qps = total / actual_duration
    error_rate = errors / total if total > 0 else 0
    hpa_rate = over_threshold[0] / total if total > 0 else 0

    # 判定
    p99_pass = p99 < HPA_THRESHOLD_MS
    verdict = "PASS ✓" if p99_pass else "FAIL ✗"

    print(f"\n  ── 结果 ──")
    print(f"  总请求:       {total}")
    print(f"  实际 QPS:     {actual_qps:.1f}")
    print(f"  延迟 avg:     {avg:.2f}ms")
    print(f"  延迟 p50:     {p50:.2f}ms")
    print(f"  延迟 p90:     {p90:.2f}ms")
    print(f"  延迟 p95:     {p95:.2f}ms")
    print(f"  延迟 p99:     {p99:.2f}ms (阈值 <{HPA_THRESHOLD_MS}ms) {'✓' if p99_pass else '✗'}")
    print(f"  延迟 max:     {mx:.2f}ms")
    print(f"  错误率:       {error_rate*100:.2f}%")
    print(f"  HPA 触发率:   {hpa_rate*100:.2f}% ({over_threshold[0]} 请求超 {HPA_THRESHOLD_MS}ms)")
    print(f"  总判定:       {verdict}")
    print(f"{'='*60}")

    return {
        "scenario": scenario_name,
        "total_requests": total,
        "actual_qps": round(actual_qps, 1),
        "latency_avg": round(avg, 2),
        "latency_p50": round(p50, 2),
        "latency_p90": round(p90, 2),
        "latency_p95": round(p95, 2),
        "latency_p99": round(p99, 2),
        "latency_max": round(mx, 2),
        "error_rate": round(error_rate * 100, 2),
        "hpa_threshold_exceeded_rate": round(hpa_rate * 100, 2),
        "hpa_threshold_exceeded_count": over_threshold[0],
        "verdict": verdict,
    }


async def main():
    scenarios = sys.argv[1:] if len(sys.argv) > 1 else ["baseline", "burst", "stress"]
    results = []

    for scenario in scenarios:
        if scenario not in SCENARIOS:
            print(f"  [WARN] 未知场景: {scenario}, 跳过")
            continue
        result = await run_scenario(scenario)
        if result:
            results.append(result)
        # 场景间等待 HPA 稳定
        if scenario != scenarios[-1]:
            print(f"\n  等待 30s HPA 稳定...")
            await asyncio.sleep(30)

    # 汇总
    print(f"\n\n{'='*60}")
    print(f"  压测汇总（集群内直连，无 port-forward 开销）")
    print(f"{'='*60}")
    print(f"  {'场景':<12} {'VU':>4} {'QPS':>8} {'P50':>8} {'P95':>8} {'P99':>8} {'Max':>8} {'HPA%':>6} {'判定':>6}")
    print(f"  {'-'*72}")
    for r in results:
        cfg = SCENARIOS[r["scenario"]]
        print(f"  {r['scenario']:<12} {cfg['vus']:>4} {r['actual_qps']:>7.1f} "
              f"{r['latency_p50']:>7.2f} {r['latency_p95']:>7.2f} "
              f"{r['latency_p99']:>7.2f} {r['latency_max']:>7.2f} "
              f"{r['hpa_threshold_exceeded_rate']:>5.2f} {r['verdict']:>6}")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
