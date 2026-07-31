"""HPA 扩容压测流量生成器 — 集群内运行（Pod 内）

【不易】直接访问 Service ClusterIP DNS，消除 port-forward 干扰
【变易】100 VU 紧循环最大压力发送，QPS 由响应延迟自然限制（~59 QPS/VU = ~5900 QPS）
【简易】无 ramp-up（立即全压），最大化 CPU 触发 HPA 扩容

用法: kubectl exec -n production <pod> -- python -u /tmp/hpa_scale_test.py
"""
import asyncio
import aiohttp
import time
import sys

# Service ClusterIP DNS — 集群内直连
ENDPOINT = "http://skill-retrieval-service.production.svc.cluster.local:8080/match"

# 压测参数
VUS = 100              # 100 个并发 VU
DURATION_S = 120       # 总运行 120s（60s 扩容窗口 + 60s 观察期）


async def send_request(session, stats):
    """发送单个请求 — 紧循环，无 sleep"""
    payload = {"query": "HPA扩容压测", "top_k": 5}
    start = time.perf_counter()
    try:
        async with session.post(ENDPOINT, json=payload) as resp:
            await resp.read()
            elapsed = (time.perf_counter() - start) * 1000
            stats["success"] += 1
            stats["total_lat"] += elapsed
            if elapsed > 40:
                stats["over_40ms"] += 1
    except Exception as e:
        stats["errors"] += 1
        if stats["errors"] <= 3:
            print(f"  [ERROR] {e}", flush=True)


async def vu_worker(session, stats, end_time, worker_id):
    """单个 VU 工作循环 — 紧循环最大压力"""
    while time.time() < end_time:
        await send_request(session, stats)


async def main():
    print(f"=== HPA 扩容压测流量生成器 ===", flush=True)
    print(f"  VU: {VUS} | 持续: {DURATION_S}s | 模式: 紧循环最大压力", flush=True)
    print(f"  端点: {ENDPOINT}", flush=True)
    print(f"  [START] 流量突增开始", flush=True)

    stats = {"success": 0, "errors": 0, "total_lat": 0.0, "over_40ms": 0}
    connector = aiohttp.TCPConnector(limit=0, limit_per_host=0)  # 无连接数限制

    end_time = time.time() + DURATION_S

    async with aiohttp.ClientSession(connector=connector) as session:
        # 预热
        try:
            async with session.post(ENDPOINT, json={"query": "warmup", "top_k": 1}) as r:
                await r.read()
        except Exception:
            pass

        # 启动 VU workers — 紧循环无 sleep，最大化 QPS
        workers = [vu_worker(session, stats, end_time, i) for i in range(VUS)]

        # 每 10s 输出进度
        progress_task = asyncio.create_task(report_progress(stats, end_time))

        await asyncio.gather(*workers)
        progress_task.cancel()

    # 最终统计
    total = stats["success"] + stats["errors"]
    avg_lat = stats["total_lat"] / stats["success"] if stats["success"] > 0 else 0
    print(f"\n  [DONE] 总请求: {total} | 成功: {stats['success']} | 错误: {stats['errors']}", flush=True)
    print(f"  [DONE] 平均延迟: {avg_lat:.2f}ms | 超 40ms: {stats['over_40ms']} ({stats['over_40ms']/max(total,1)*100:.1f}%)", flush=True)


async def report_progress(stats, end_time):
    """每 10s 输出进度标记"""
    start = time.time()
    while time.time() < end_time:
        await asyncio.sleep(10)
        elapsed = int(time.time() - start)
        total = stats["success"] + stats["errors"]
        avg = stats["total_lat"] / max(stats["success"], 1)
        print(f"  [PROGRESS] {elapsed}s | req={total} ok={stats['success']} err={stats['errors']} avg={avg:.1f}ms", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
