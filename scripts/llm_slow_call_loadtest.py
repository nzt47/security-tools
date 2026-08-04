#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 服务慢调用专项压测 — 模拟并测量 Latency > 2s 的慢调用

背景：
  orchestrator 通过 memory/llm_service.py 调用 OpenAI 兼容 /chat/completions，
  埋点记录 llm_duration_ms（orchestrator.process.llm.confidence）。
  本脚本针对 LLM 服务延迟劣化场景做专项压测，验证：
    1. 慢调用（Latency > 2s）占比与分布（P50/P90/P95/P99/Max）
    2. 慢调用是否可被 llm-error-threshold 告警体系感知
    3. 客户端超时行为（aiohttp timeout 与 LLMService timeout 对齐）

【不易】延迟基准 2s（SLOW_THRESHOLD_MS）为业务侧慢调用判定阈值，与
        llm_error 告警阈值配套使用
【变易】--mock 内嵌延迟可注入的 mock LLM 服务（无需真实 API Key）；
        否则直连 OpenAI 兼容端点（需 --endpoint + --api-key）
【简易】单文件零外部服务依赖（aiohttp 为标准库外唯一依赖，集群镜像已内置）

用法：
  # 1) mock 模式（推荐，无需 API Key）：启动内置慢服务并压测
  python scripts/llm_slow_call_loadtest.py --mock --delay-ms 2500 --vus 8 --duration 30

  # 2) 真实端点（慢调用由真实服务延迟决定）
  python scripts/llm_slow_call_loadtest.py \
      --endpoint http://<llm-svc>:8000/v1/chat/completions \
      --api-key sk-xxx --model gpt-4

  # 3) 集群内 Pod 执行（复用 skill-retrieval:local 镜像）
  kubectl run llm-slow-test --rm -i --restart=Never --image=skill-retrieval:local \
      -- python /dev/stdin < scripts/llm_slow_call_loadtest.py --mock --delay-ms 2500
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

# 【不易】慢调用阈值：Latency > 2s 判定为慢调用
SLOW_THRESHOLD_MS = 2000.0

# 默认 mock LLM 服务端口
MOCK_PORT = 18080


# ═══════════════════════════════════════════════════════════════════
#  Mock LLM 服务：OpenAI 兼容 /v1/chat/completions，延迟可注入
# ═══════════════════════════════════════════════════════════════════

class _MockLLMHandler(BaseHTTPRequestHandler):
    delay_ms = 2000  # 模块级，由 mock server 启动时设置

    def do_POST(self):
        if self.path not in ("/v1/chat/completions", "/chat/completions"):
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        # 注入慢延迟（模拟 LLM 慢调用）
        time.sleep(self.delay_ms / 1000.0)
        body = json.dumps({
            "id": "mock-chat-completion",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {
                "role": "assistant",
                "content": "这是 mock LLM 的模拟响应（慢调用延迟 %.0fms）。" % self.delay_ms,
            }, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # 静默访问日志，避免刷屏
        pass


def _start_mock_llm(port: int, delay_ms: int) -> ThreadingHTTPServer:
    _MockLLMHandler.delay_ms = delay_ms
    server = ThreadingHTTPServer(("0.0.0.0", port), _MockLLMHandler)
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    print("  [mock] LLM mock 服务已启动: http://127.0.0.1:%d/v1/chat/completions (延迟 %.0fms)"
          % (port, delay_ms))
    # 健康检查
    for _ in range(10):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=1):
                break
        except Exception:
            time.sleep(0.1)
    return server


# ═══════════════════════════════════════════════════════════════════
#  压测引擎
# ═══════════════════════════════════════════════════════════════════

async def _send_chat(session, url: str, api_key: str, model: str,
                     latencies: list, slow_count: list, error_count: list):
    """发送单个 chat.completions 请求并记录延迟"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "请简要说明意图层语义检索的降级策略"}],
        "max_tokens": 256,
        "temperature": 0.7,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer %s" % api_key
    start = time.perf_counter()
    try:
        async with session.post(url, json=payload, headers=headers) as resp:
            await resp.read()
            elapsed_ms = (time.perf_counter() - start) * 1000
            if resp.status != 200:
                error_count[0] += 1
                latencies.append(-1)
                return
            latencies.append(elapsed_ms)
            if elapsed_ms > SLOW_THRESHOLD_MS:
                slow_count[0] += 1
    except Exception:
        error_count[0] += 1
        latencies.append(-1)


async def _vu_worker(session, url: str, api_key: str, model: str,
                     latencies: list, slow_count: list, error_count: list,
                     duration_s: float, qps_per_vu: float):
    end = time.time() + duration_s
    interval = 1.0 / qps_per_vu if qps_per_vu > 0 else 0
    while time.time() < end:
        await _send_chat(session, url, api_key, model, latencies, slow_count, error_count)
        if interval > 0:
            await asyncio.sleep(interval)


async def _run_loadtest(url: str, api_key: str, model: str,
                        vus: int, duration_s: float, target_qps: float) -> dict:
    qps_per_vu = target_qps / vus
    print("\n" + "=" * 64)
    print("  慢调用专项压测: %d VU × %ds | 目标 %.0f QPS" % (vus, duration_s, target_qps))
    print("  慢调用阈值: Latency > %.0fms (%.1fs)" % (SLOW_THRESHOLD_MS, SLOW_THRESHOLD_MS / 1000))
    print("  端点: %s" % url)
    print("=" * 64)

    latencies: list = []
    slow_count = [0]
    error_count = [0]

    import aiohttp
    connector = aiohttp.TCPConnector(limit=vus * 2, limit_per_host=vus * 2)
    timeout = aiohttp.ClientTimeout(total=60)  # 允许慢调用返回（不触发客户端超时）
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # 预热
        try:
            async with session.post(url, json={
                "model": model, "messages": [{"role": "user", "content": "warmup"}],
            }, headers={"Content-Type": "application/json",
                        "Authorization": "Bearer %s" % api_key} if api_key else {"Content-Type": "application/json"}) as r:
                await r.read()
            print("  [OK] 预热请求成功")
        except Exception as e:
            print("  [ERR] 预热失败: %s" % e)
            return {}

        print("  开始压测 (%ds)..." % duration_s)
        start_ts = time.time()
        workers = [_vu_worker(session, url, api_key, model, latencies, slow_count, error_count,
                              duration_s, qps_per_vu) for _ in range(vus)]
        await asyncio.gather(*workers)
        actual_duration = time.time() - start_ts

    valid = [l for l in latencies if l > 0]
    total = len(latencies)
    if not valid:
        print("  [ERR] 无有效请求")
        return {}

    valid.sort()
    n = len(valid)

    def pct(p):
        idx = int(n * p / 100)
        return valid[min(idx, n - 1)]

    p50, p90, p95, p99 = pct(50), pct(90), pct(95), pct(99)
    avg = statistics.mean(valid)
    mx = max(valid)
    slow_rate = slow_count[0] / total if total else 0
    err_rate = error_count[0] / total if total else 0
    verdict = "SLOW-CALL" if slow_rate > 0.05 else "NORMAL"

    print("\n  ── 结果（LLM 慢调用专项）──")
    print("  总请求:       %d" % total)
    print("  实际 QPS:     %.1f" % (total / actual_duration))
    print("  延迟 avg:     %.1fms" % avg)
    print("  延迟 p50:     %.1fms" % p50)
    print("  延迟 p90:     %.1fms" % p90)
    print("  延迟 p95:     %.1fms" % p95)
    print("  延迟 p99:     %.1fms" % p99)
    print("  延迟 max:     %.1fms" % mx)
    print("  慢调用率(>%.0fms): %.2f%% (%d 请求)" % (SLOW_THRESHOLD_MS, slow_rate * 100, slow_count[0]))
    print("  错误率:       %.2f%% (%d 请求)" % (err_rate * 100, error_count[0]))
    print("  判定:         %s" % verdict)
    print("=" * 64)

    return {
        "total_requests": total,
        "actual_qps": round(total / actual_duration, 1),
        "latency_avg_ms": round(avg, 1),
        "latency_p50_ms": round(p50, 1),
        "latency_p90_ms": round(p90, 1),
        "latency_p95_ms": round(p95, 1),
        "latency_p99_ms": round(p99, 1),
        "latency_max_ms": round(mx, 1),
        "slow_call_rate": round(slow_rate * 100, 2),
        "slow_call_count": slow_count[0],
        "error_rate": round(err_rate * 100, 2),
        "verdict": verdict,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM 慢调用专项压测（Latency > 2s）")
    parser.add_argument("--mock", action="store_true",
                        help="使用内置 mock LLM 服务（无需 API Key，推荐）")
    parser.add_argument("--delay-ms", type=int, default=2500,
                        help="mock 模式注入的延迟（默认 2500ms，制造 >2s 慢调用）")
    parser.add_argument("--endpoint", default="",
                        help="OpenAI 兼容端点（非 mock 模式必填）")
    parser.add_argument("--api-key", default="", help="API Key（真实端点需要）")
    parser.add_argument("--model", default="gpt-4", help="模型名（默认 gpt-4）")
    parser.add_argument("--vus", type=int, default=8, help="并发 VU 数（默认 8）")
    parser.add_argument("--duration", type=int, default=30, help="压测时长秒（默认 30）")
    parser.add_argument("--qps", type=int, default=20, help="目标总 QPS（默认 20）")
    args = parser.parse_args()

    url = args.endpoint
    server = None
    if args.mock:
        url = "http://127.0.0.1:%d/v1/chat/completions" % MOCK_PORT
        server = _start_mock_llm(MOCK_PORT, args.delay_ms)
    elif not url:
        print("[ERR] 请指定 --endpoint（真实端点）或使用 --mock")
        return 1

    try:
        result = asyncio.run(_run_loadtest(url, args.api_key, args.model,
                                           args.vus, args.duration, args.qps))
    finally:
        if server is not None:
            server.shutdown()
            print("  [mock] mock LLM 服务已关闭")

    if not result:
        return 1

    print("\n  场景结论: %s" % ("模拟出慢调用（>2s 占比显著），可观察告警 llm-error-threshold 关联日志"
                               if result["verdict"] == "SLOW-CALL"
                               else "未产生显著慢调用（低于 5% 阈值）"))
    print("  关联埋点: orchestrator.process.llm.confidence 的 llm_duration_ms 字段")
    return 0


if __name__ == "__main__":
    sys.exit(main())
