"""自包含 Mock 技能检索服务 — K8s 压测专用

【不易】指标名严格对齐 agent/monitoring/prometheus.py 定义:
    - skill_match_latency_ms (Histogram, buckets 含 40ms HPA 阈值)
    - skill_match_count_total (Counter)
    - Yunshu_active_connections (Gauge)
    Prometheus Adapter 的 seriesQuery 依赖这些指标名，不可更改。

【变易】aiohttp 异步框架 — 彻底解决 ThreadingHTTPServer + GIL 争用导致的长尾
    旧实现: ThreadingHTTPServer + time.sleep → 20 VU 并发 max=1070ms（理论 35ms）
    新实现: aiohttp + asyncio.sleep → 单线程异步，无 GIL 争用，P99≈理论值

【简易】单文件，/match /health /ready /metrics 全在 8080 端口

端点:
    POST /match    — 技能检索（模拟 3-35ms 对数正态延迟）
    GET  /health   — 存活检查
    GET  /ready    — 就绪检查
    GET  /metrics  — Prometheus 指标（prometheus_client 原生格式）
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import time

from aiohttp import web
from prometheus_client import (
    Histogram,
    Counter,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

# ═══════════════════════════════════════════════════════════════════
#  指标定义 — 严格对齐 prometheus.py L644-657
#  【不易】buckets 必须包含 40（HPA 阈值），否则 histogram_quantile 无法计算
# ═══════════════════════════════════════════════════════════════════
skill_match_latency_ms = Histogram(
    "skill_match_latency_ms",
    "Skill match latency in milliseconds",
    ["layer", "method", "success"],
    buckets=[1, 5, 10, 20, 30, 40, 50, 75, 100, 200, 500, 1000],
)

skill_match_count_total = Counter(
    "skill_match_count_total",
    "Total skill match count",
    ["layer", "method", "success"],
)

# 活跃连接数（对齐 Yunshu_active_connections）
active_connections = Gauge("Yunshu_active_connections", "Number of active connections")


# ═══════════════════════════════════════════════════════════════════
#  延迟模型参数 — 对数正态分布（削减并发长尾）
#
#  【变易】从 uniform(5,35) 改为 lognormal，更贴近真实检索延迟分布:
#    - mu=2.7, sigma=0.3 → 中位数 ~15ms, P95 ~26ms, P99 ~30ms
#    - 钳制到 [LATENCY_MIN, LATENCY_MAX] 避免极端值
#  【简易】环境变量参数化，压测时可调参验证不同分布
#
#  环境变量:
#    MOCK_LATENCY_MU     — 对数均值（默认 2.7，即中位数 e^2.7≈15ms）
#    MOCK_LATENCY_SIGMA  — 对数标准差（默认 0.3，sigma 越小长尾越轻）
#    MOCK_LATENCY_MIN_MS — 最小延迟钳制（默认 3ms）
#    MOCK_LATENCY_MAX_MS — 最大延迟钳制（默认 35ms）
# ═══════════════════════════════════════════════════════════════════
LATENCY_MU = float(os.environ.get("MOCK_LATENCY_MU", "2.7"))
LATENCY_SIGMA = float(os.environ.get("MOCK_LATENCY_SIGMA", "0.3"))
LATENCY_MIN_MS = float(os.environ.get("MOCK_LATENCY_MIN_MS", "3"))
LATENCY_MAX_MS = float(os.environ.get("MOCK_LATENCY_MAX_MS", "35"))


def _sample_latency_ms() -> float:
    """【变易】对数正态采样 — 削减并发下长尾。

    旧实现 random.uniform(5,35) 在 40+ VU 并发下实测 max=3.81s（理论 max=35ms），
    根因是 ThreadingHTTPServer 线程调度抖动放大。对数正态分布将 P99 钳制到
    ~30ms，配合 LATENCY_MAX_MS 硬上限，消除秒级长尾。
    """
    latency = math.exp(random.gauss(LATENCY_MU, LATENCY_SIGMA))
    return min(max(latency, LATENCY_MIN_MS), LATENCY_MAX_MS)


# 模拟技能库
MOCK_SKILLS = [
    {"skill_id": "pdf_parser", "name": "PDF解析", "description": "解析PDF文件并提取内容"},
    {"skill_id": "report_gen", "name": "报告生成", "description": "生成市场分析报告"},
    {"skill_id": "code_review", "name": "代码审查", "description": "审查代码质量"},
    {"skill_id": "translation", "name": "翻译", "description": "多语言翻译"},
    {"skill_id": "debug_helper", "name": "调试助手", "description": "调试运行时错误"},
]


# ═══════════════════════════════════════════════════════════════════
#  活跃连接跟踪中间件
#  【变易】aiohttp 中间件替代 threading.Lock — 单线程异步无需锁
# ═══════════════════════════════════════════════════════════════════
@web.middleware
async def connection_tracker(request, handler):
    """跟踪活跃连接数（对齐 Yunshu_active_connections 指标）"""
    active_connections.inc()
    try:
        response = await handler(request)
        return response
    finally:
        active_connections.dec()


# ═══════════════════════════════════════════════════════════════════
#  HTTP Handlers
# ═══════════════════════════════════════════════════════════════════

async def handle_health(request):
    """存活检查"""
    return web.json_response({"status": "ok", "timestamp": time.time()})


async def handle_ready(request):
    """就绪检查"""
    return web.json_response({"status": "ready"})


async def handle_metrics(request):
    """【不易】Prometheus 指标端点 — K8s scrape 注解指向 8080/metrics"""
    # 【变易】aiohttp content_type 参数不允许含 charset，改用 headers 直设完整 Content-Type
    # CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"（Prometheus 期望的完整值）
    return web.Response(body=generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


async def handle_match(request):
    """技能检索 — 模拟对数正态延迟"""
    start = time.perf_counter()

    # 读取请求体
    try:
        payload = await request.json()
    except Exception:
        payload = {"query": ""}

    top_k = payload.get("top_k", 5)

    # 【变易】asyncio.sleep 不阻塞事件循环 — 彻底解决 GIL 争用长尾
    # 旧 time.sleep 在 ThreadingHTTPServer 20 VU 并发下 max=1070ms
    # 新 asyncio.sleep 在 aiohttp 单线程下精度 ~1ms，无线程切换开销
    latency_ms = _sample_latency_ms()
    await asyncio.sleep(latency_ms / 1000)

    elapsed = (time.perf_counter() - start) * 1000

    # 模拟检索结果
    matches = []
    for i in range(min(top_k, len(MOCK_SKILLS))):
        skill = MOCK_SKILLS[i]
        matches.append({
            "skill_id": skill["skill_id"],
            "name": skill["name"],
            "description": skill["description"],
            "score": round(0.9 - i * 0.15, 4),
        })

    # 【不易】记录 prometheus 指标（指标名对齐 prometheus.py 定义）
    method = "rrf"
    success = "true"
    skill_match_latency_ms.labels(layer="1", method=method, success=success).observe(elapsed)
    skill_match_count_total.labels(layer="1", method=method, success=success).inc()

    return web.json_response({
        "matches": matches,
        "match_count": len(matches),
        "elapsed_ms": round(elapsed, 2),
        "retrieval_method": method,
    })


def create_app():
    """创建 aiohttp 应用"""
    app = web.Application()
    app.middlewares.append(connection_tracker)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/ready", handle_ready)
    app.router.add_get("/metrics", handle_metrics)
    app.router.add_post("/match", handle_match)
    return app


def main():
    active_connections.set(0)
    app = create_app()
    print("[OK] Mock 技能检索服务 (aiohttp): http://0.0.0.0:8080/match")
    print("[OK] 健康检查: http://0.0.0.0:8080/health")
    print("[OK] Prometheus 指标: http://0.0.0.0:8080/metrics")
    # 【变易】打印延迟模型参数，便于压测时确认分布配置
    median_ms = math.exp(LATENCY_MU)
    print(f"[OK] 延迟模型: lognormal(mu={LATENCY_MU}, sigma={LATENCY_SIGMA}) "
          f"→ 中位数≈{median_ms:.1f}ms, 钳制[{LATENCY_MIN_MS}, {LATENCY_MAX_MS}]ms")
    print("[OK] 异步框架: aiohttp + asyncio.sleep（无 GIL 争用）")
    print("[OK] 按 Ctrl+C 停止服务")
    print()
    # 【简易】access_log=None 静默访问日志，print=None 禁用默认启动输出
    web.run_app(app, host="0.0.0.0", port=8080, access_log=None, print=None)


if __name__ == "__main__":
    main()
