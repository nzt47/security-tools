"""诊断页时延分位数投影（2026-11-10，条目 diagnostics-histograms-empty）。

背景：/api/diagnostics/metrics 的 histograms 长期是空对象 ——
  agent/monitoring/metrics.py 那个进程内注册表**没有任何写入点**；
  真实时延在 prometheus_flask_exporter 注册到默认 REGISTRY 的
  yunshu_http_request_duration_seconds（标签 method/endpoint/status/le）。

本组用例锁三件事：
  ① 分位数算法（桶内线性插值）与 Prometheus histogram_quantile 同法；
  ② 投影只报"够样本"的端点，且**不报 min/max**（桶边界取不到真 min/max）；
  ③ 注册表不可读 / 没有该族时如实返回 {}（页面显示不可用，而不是 0ms）。
"""
from __future__ import annotations

import math
import types

import pytest

from agent.monitoring import prometheus as pm


def _sample(name, labels, value):
    return types.SimpleNamespace(name=name, labels=labels, value=value)


def _family(name, samples):
    return types.SimpleNamespace(name=name, samples=samples)


def _endpoint_samples(endpoint, buckets, total_sum):
    """构造一个端点的 bucket/_sum/_count 样本（buckets: [(le, 累积), ...]）"""
    out = []
    for le, cum in buckets:
        out.append(_sample("yunshu_http_request_duration_seconds_bucket",
                           {"endpoint": endpoint, "method": "GET", "status": "200", "le": le},
                           cum))
    out.append(_sample("yunshu_http_request_duration_seconds_sum",
                       {"endpoint": endpoint, "method": "GET", "status": "200"}, total_sum))
    out.append(_sample("yunshu_http_request_duration_seconds_count",
                       {"endpoint": endpoint, "method": "GET", "status": "200"},
                       buckets[-1][1]))
    return out


def test_分位数按桶内线性插值():
    # 100 个样本：<=0.1s 有 50，<=0.2s 有 100
    series = [(0.1, 50), (0.2, 100), (math.inf, 100)]
    p50 = pm._quantile_from_buckets(series, 0.50)
    p95 = pm._quantile_from_buckets(series, 0.95)
    assert p50 == pytest.approx(0.1, abs=1e-6), "P50 正好落在桶边界"
    assert 0.1 < p95 < 0.2, "P95 应落在第二个桶内（不是边界上一个假精确值）"


def test_全落进_Inf_桶时不给假具体值():
    series = [(0.1, 0), (10.0, 3), (math.inf, 7)]
    p99 = pm._quantile_from_buckets(series, 0.99)
    assert p99 == pytest.approx(10.0), "只能说到「超过 10s」，不编一个更细的数"


def test_没有样本返回_None():
    assert pm._quantile_from_buckets([], 0.5) is None
    assert pm._quantile_from_buckets([(0.1, 0), (math.inf, 0)], 0.5) is None


def test_投影只报够样本的端点且不含_min_max(monkeypatch):
    samples = _endpoint_samples("api_health", [("0.005", 10), ("0.01", 80), ("0.05", 100), ("+Inf", 100)], 2.5)
    samples += _endpoint_samples("api_tiny", [("0.005", 1), ("+Inf", 1)], 0.004)
    fake = types.SimpleNamespace(collect=lambda: [_family("yunshu_http_request_duration_seconds", samples)])
    monkeypatch.setattr(pm, "REGISTRY", fake)

    out = pm.http_latency_histograms(min_samples=20)
    assert set(out) == {"http.api_health"}, "样本不足的端点不报分位（小样本 P99 是噪声）"
    row = out["http.api_health"]
    assert row["count"] == 100
    # rank=50 落在 (0.005, 0.01] 桶内：0.005 + 0.005*(50-10)/(80-10) ≈ 0.007857
    assert row["p50"] == pytest.approx(0.007857, abs=1e-4)
    assert "min" not in row and "max" not in row, "直方图取不到真 min/max，不许编"
    assert row["avg"] == pytest.approx(0.025, abs=1e-3)


def test_top_n_按样本量取前几名(monkeypatch):
    samples = []
    for i, n in enumerate((30, 100, 60)):
        samples += _endpoint_samples("ep%d" % i, [("0.01", n), ("+Inf", n)], 0.01 * n)
    fake = types.SimpleNamespace(collect=lambda: [_family("yunshu_http_request_duration_seconds", samples)])
    monkeypatch.setattr(pm, "REGISTRY", fake)
    out = pm.http_latency_histograms(top_n=2, min_samples=20)
    assert list(out) == ["http.ep1", "http.ep2"], "按样本量降序取前 N"


def test_没有该族时返回空字典(monkeypatch):
    fake = types.SimpleNamespace(collect=lambda: [_family("something_else", [])])
    monkeypatch.setattr(pm, "REGISTRY", fake)
    assert pm.http_latency_histograms() == {}


def test_注册表不可读时返回空字典(monkeypatch):
    def boom():
        raise RuntimeError("registry closed")

    monkeypatch.setattr(pm, "REGISTRY", types.SimpleNamespace(collect=boom))
    assert pm.http_latency_histograms() == {}
