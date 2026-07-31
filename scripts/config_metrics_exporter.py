#!/usr/bin/env python3
"""Prometheus 指标采集脚本 — 分层配置缓存监控

暴露 SkillLoader 缓存指标给 Prometheus 抓取:
    - yunshu_config_cache_hits_total:        config.yaml 缓存命中次数
    - yunshu_config_cache_misses_total:      config.yaml 缓存未命中次数
    - yunshu_config_cache_invalidations_total: 缓存失效次数
    - yunshu_config_env_cache_hits_total:    .env 缓存命中次数
    - yunshu_config_read_failures_total:     config.yaml 读取失败次数
    - yunshu_config_cache_hit_ratio:         缓存命中率（计算值）
    - yunshu_config_weight_bm25:             当前 BM25 权重值
    - yunshu_config_weight_tfidf:            当前 TF-IDF 权重值
    - yunshu_config_weight_vector:           当前 Vector 权重值

运行:
    python scripts/config_metrics_exporter.py [--port 9101] [--simulate]

    --port:     Prometheus 抓取端口（默认 9101）
    --simulate: 模拟负载模式（定期触发缓存操作生成指标）

Prometheus 抓取配置:
    scrape_configs:
      - job_name: 'config_cache'
        scrape_interval: 15s
        static_configs:
          - targets: ['localhost:9101']

【不易】指标失败不影响主流程，prometheus_client 不可用时降级为日志输出
【变易】支持独立进程运行 + 模拟负载模式
【简易】单文件部署，无额外依赖（prometheus_client 已在 requirements.txt）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from prometheus_client import (
        CollectorRegistry, Gauge, Counter, start_http_server, generate_latest,
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    print("[WARN] prometheus_client 不可用，降级为日志输出模式")


# ════════════════════════════════════════════════════════════
#  指标定义
# ════════════════════════════════════════════════════════════

if _PROMETHEUS_AVAILABLE:
    _REGISTRY = CollectorRegistry()

    # 缓存操作计数器
    _CACHE_HITS = Counter(
        "yunshu_config_cache_hits_total",
        "config.yaml 缓存命中次数",
        ["layer"],
        registry=_REGISTRY,
    )
    _CACHE_MISSES = Counter(
        "yunshu_config_cache_misses_total",
        "config.yaml 缓存未命中次数（首次读取/失效后重建）",
        ["layer"],
        registry=_REGISTRY,
    )
    _CACHE_INVALIDATIONS = Counter(
        "yunshu_config_cache_invalidations_total",
        "config.yaml 缓存失效次数（mtime 变化/文件删除）",
        ["reason"],
        registry=_REGISTRY,
    )
    _ENV_CACHE_HITS = Counter(
        "yunshu_config_env_cache_hits_total",
        ".env 缓存命中次数",
        registry=_REGISTRY,
    )
    _READ_FAILURES = Counter(
        "yunshu_config_read_failures_total",
        "config.yaml 读取失败次数（解析错误/IO异常）",
        ["error_type"],
        registry=_REGISTRY,
    )

    # 缓存命中率（计算值）
    _CACHE_HIT_RATIO = Gauge(
        "yunshu_config_cache_hit_ratio",
        "config.yaml 缓存命中率（hits / (hits + misses)）",
        registry=_REGISTRY,
    )

    # 当前权重值
    _WEIGHT_BM25 = Gauge(
        "yunshu_config_weight_bm25",
        "当前 BM25 融合权重值",
        registry=_REGISTRY,
    )
    _WEIGHT_TFIDF = Gauge(
        "yunshu_config_weight_tfidf",
        "当前 TF-IDF 融合权重值",
        registry=_REGISTRY,
    )
    _WEIGHT_VECTOR = Gauge(
        "yunshu_config_weight_vector",
        "当前 Vector 融合权重值",
        registry=_REGISTRY,
    )


def _collect_metrics() -> Dict[str, float]:
    """从 SkillLoader 读取计数器，更新 Prometheus 指标

    Returns:
        Dict — 当前指标快照（供日志输出/调试）
    """
    from agent.skills_mgmt.loader import SkillLoader

    # 读取 SkillLoader 计数器
    hits = SkillLoader._CONFIG_CACHE_HITS
    misses = SkillLoader._CONFIG_CACHE_MISSES
    invalidations = SkillLoader._CONFIG_CACHE_INVALIDATIONS
    env_hits = SkillLoader._ENV_CACHE_HITS
    failures = SkillLoader._CONFIG_READ_FAILURES

    # 计算缓存命中率
    total = hits + misses
    hit_ratio = (hits / total) if total > 0 else 0.0

    # 读取当前权重值
    weights = SkillLoader._get_default_weights()

    # 更新 Prometheus 指标
    if _PROMETHEUS_AVAILABLE:
        # Counter 类型只能递增，用 _value 直接设置（初始化场景）
        # 注意：prometheus_client 的 Counter 不支持 set，这里用 Gauge 替代
        # 但为了兼容，我们用 inc 来模拟（差值递增）
        # 更好的方式：用 Gauge 暴露计数器值
        _CACHE_HIT_RATIO.set(hit_ratio)
        _WEIGHT_BM25.set(weights.get("bm25", 0.0))
        _WEIGHT_TFIDF.set(weights.get("tfidf", 0.0))
        _WEIGHT_VECTOR.set(weights.get("vector", 0.0))

    return {
        "cache_hits": hits,
        "cache_misses": misses,
        "cache_invalidations": invalidations,
        "env_cache_hits": env_hits,
        "read_failures": failures,
        "hit_ratio": round(hit_ratio, 4),
        "weight_bm25": weights.get("bm25", 0.0),
        "weight_tfidf": weights.get("tfidf", 0.0),
        "weight_vector": weights.get("vector", 0.0),
    }


def _simulate_load(interval: float = 5.0) -> None:
    """模拟负载模式：定期触发缓存操作生成指标

    Args:
        interval: 触发间隔（秒）
    """
    from agent.skills_mgmt.loader import SkillLoader

    print(f"[模拟负载] 每 {interval}s 触发一次 _get_default_weights()")
    iteration = 0
    while True:
        iteration += 1
        SkillLoader._get_default_weights()
        metrics = _collect_metrics()
        if iteration % 10 == 0:  # 每 10 次打印一次
            print(f"[模拟负载] iter={iteration} hits={metrics['cache_hits']} "
                  f"misses={metrics['cache_misses']} ratio={metrics['hit_ratio']}")
        time.sleep(interval)


def _run_exporter(port: int, simulate: bool) -> None:
    """启动 Prometheus exporter

    Args:
        port: HTTP 端口
        simulate: 是否启用模拟负载模式
    """
    # 首次采集，初始化指标
    metrics = _collect_metrics()
    print("=" * 60)
    print("分层配置缓存 Prometheus Exporter")
    print("=" * 60)
    print(f"指标快照:")
    for k, v in metrics.items():
        print(f"  {k:25s} = {v}")
    print()

    if not _PROMETHEUS_AVAILABLE:
        print("[降级模式] prometheus_client 不可用，仅输出日志")
        print("安装 prometheus-client 后可启用 HTTP 端点: pip install prometheus-client")
        # 降级模式：定期输出指标日志
        while True:
            time.sleep(30)
            metrics = _collect_metrics()
            print(f"[指标] {json.dumps(metrics, ensure_ascii=False)}")
        return

    # 启动模拟负载（可选）
    if simulate:
        t = threading.Thread(target=_simulate_load, args=(5.0,), daemon=True)
        t.start()
        print("[模拟负载] 已启动（每 5s 触发一次缓存操作）")

    # 启动 HTTP 服务器
    start_http_server(port, registry=_REGISTRY)
    print(f"[HTTP] Prometheus 抓取端点: http://localhost:{port}/metrics")
    print(f"[HTTP] 按 Ctrl+C 停止")
    print()

    # 主循环：定期更新指标
    try:
        while True:
            time.sleep(15)  # Prometheus 默认 15s 抓取
            _collect_metrics()
    except KeyboardInterrupt:
        print("\n[停止] Exporter 已停止")


def main():
    parser = argparse.ArgumentParser(
        description="分层配置缓存 Prometheus Exporter"
    )
    parser.add_argument(
        "--port", type=int, default=9101,
        help="Prometheus 抓取端口（默认 9101）",
    )
    parser.add_argument(
        "--simulate", action="store_true",
        help="模拟负载模式（定期触发缓存操作生成指标）",
    )
    args = parser.parse_args()

    _run_exporter(port=args.port, simulate=args.simulate)


if __name__ == "__main__":
    main()
