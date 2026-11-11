# -*- coding: utf-8 -*-
"""PromQL 子集求值（agent/alarms/promql.py）测试 —— 只覆盖本仓那 16 条规则用到的构造。

【为什么必须逐条测】如果求值器"看起来在跑"，却把 rate 算成 0、把 +Inf 桶算成具体值，
报警器就会安静地永远不响 —— 那比没有报警器更危险。所以每条表达式形态都钉一个
"会响"和"不会响"的样例。
"""
from __future__ import annotations

import pytest

from agent.alarms.promql import (
    STATUS_FIRING,
    STATUS_NO_DATA,
    STATUS_OK,
    STATUS_UNSUPPORTED,
    STATUS_WARMING,
    evaluate_expression,
)
from agent.alarms.samples import SampleStore, _series_key


def key(name: str, **labels) -> tuple:
    return _series_key(name, {k: str(v) for k, v in labels.items()})


def store_with(points) -> SampleStore:
    """points = [(ts, {key: value}), ...]（ts 是单调秒，窗口按差值算）"""
    store = SampleStore(window_sec=3600)
    for ts, values in points:
        store.record(values, now=ts)
    return store


class TestInstantSelectors:
    def test_阈值判真与判假(self):
        store = store_with([(100.0, {key("yunshu_memory_usage_percent"): 90.0})])
        out = evaluate_expression("yunshu_memory_usage_percent > 85", store)
        assert out["status"] == STATUS_FIRING and out["value"] == 90.0

        store = store_with([(100.0, {key("yunshu_memory_usage_percent"): 50.0})])
        assert evaluate_expression("yunshu_memory_usage_percent > 85", store)["status"] == STATUS_OK

    def test_无序列是_no_data_不是_ok(self):
        store = store_with([(100.0, {key("other_metric"): 1.0})])
        out = evaluate_expression("yunshu_cpu_usage_percent > 80", store)
        assert out["status"] == STATUS_NO_DATA
        assert "yunshu_cpu_usage_percent" in out["reason"]

    def test_等值比较(self):
        store = store_with([(100.0, {key("up", job="yunshu"): 0.0})])
        assert evaluate_expression('up{job="yunshu"} == 0', store)["status"] == STATUS_FIRING
        store = store_with([(100.0, {key("up", job="yunshu"): 1.0})])
        assert evaluate_expression('up{job="yunshu"} == 0', store)["status"] == STATUS_OK


class TestWindowFunctions:
    def test_increase_按窗口差值(self):
        series = key("yunshu_http_request_total", status="500", method="GET", endpoint="/api/x")
        store = store_with([(0.0, {series: 10.0}), (60.0, {series: 40.0})])
        out = evaluate_expression(
            'sum(increase(yunshu_http_request_total{status=~"5.."}[1m])) > 10', store)
        assert out["status"] == STATUS_FIRING and out["value"] == pytest.approx(30.0)

    def test_标签正则只匹配_5xx(self):
        s5 = key("yunshu_http_request_total", status="500", method="GET", endpoint="/a")
        s2 = key("yunshu_http_request_total", status="200", method="GET", endpoint="/a")
        store = store_with([(0.0, {s5: 0.0, s2: 0.0}), (60.0, {s5: 4.0, s2: 96.0})])
        out = evaluate_expression(
            'sum(increase(yunshu_http_request_total{status=~"5.."}[1m])) > 10', store)
        assert out["status"] == STATUS_OK, "只有 4 条 5xx，不该触发"
        assert out["value"] == pytest.approx(4.0)

    def test_错误率比值(self):
        s5 = key("yunshu_http_request_total", status="500", method="GET", endpoint="/a")
        s2 = key("yunshu_http_request_total", status="200", method="GET", endpoint="/a")
        store = store_with([(0.0, {s5: 0.0, s2: 0.0}), (300.0, {s5: 10.0, s2: 90.0})])
        expr = ('sum(rate(yunshu_http_request_total{status=~"5.."}[5m]))'
                " / clamp_min(sum(rate(yunshu_http_request_total[5m])), 1e-9) > 0.05")
        out = evaluate_expression(expr, store)
        assert out["status"] == STATUS_FIRING
        assert out["value"] == pytest.approx(0.1, rel=1e-6)

    def test_工具失败率形态(self):
        ok = key("yunshu_tool_call_total", success="True")
        bad = key("yunshu_tool_call_total", success="False")
        store = store_with([(0.0, {ok: 0.0, bad: 0.0}), (300.0, {ok: 20.0, bad: 5.0})])
        expr = ('1 - ( sum(rate(yunshu_tool_call_total{success="True"}[5m]))'
                " / clamp_min(sum(rate(yunshu_tool_call_total[5m])), 1e-9) ) > 0.1")
        out = evaluate_expression(expr, store)
        assert out["status"] == STATUS_FIRING
        assert out["value"] == pytest.approx(0.2, rel=1e-6)

    def test_resets_计数回退(self):
        up = key("up", job="yunshu")
        points = [(float(i * 60), {up: (0.0 if i in (2, 4, 6, 8) else 1.0)}) for i in range(10)]
        store = store_with(points)
        out = evaluate_expression('resets(up{job="yunshu"}[1h]) > 3', store)
        assert out["status"] == STATUS_FIRING and out["value"] >= 4

    def test_窗口不足报_warming_而不是_0(self):
        series = key("yunshu_http_request_total", status="500")
        store = store_with([(0.0, {series: 10.0}), (30.0, {series: 20.0})])
        out = evaluate_expression(
            'sum(increase(yunshu_http_request_total{status=~"5.."}[5m])) > 10', store)
        assert out["status"] == STATUS_WARMING
        assert "样本不足" in out["reason"]


class TestHistogramQuantile:
    @staticmethod
    def _bucket_store(le_values):
        old, new = {}, {}
        for le, count in le_values.items():
            k = key("yunshu_http_request_duration_seconds_bucket", le=le)
            old[k] = 0.0
            new[k] = float(count)
        return store_with([(0.0, old), (300.0, new)])

    def test_全慢请求触发_p99(self):
        store = self._bucket_store({"0.5": 0, "1": 0, "3": 0, "5": 0, "+Inf": 100})
        out = evaluate_expression(
            "histogram_quantile(0.99, sum(rate("
            "yunshu_http_request_duration_seconds_bucket[5m])) by (le)) > 3", store)
        assert out["status"] == STATUS_FIRING
        assert out["value"] == pytest.approx(5.0), "落在 +Inf 桶时给最后一个有限上界，不编数"

    def test_全快请求不触发(self):
        store = self._bucket_store({"0.5": 100, "1": 100, "3": 100, "5": 100, "+Inf": 100})
        out = evaluate_expression(
            "histogram_quantile(0.99, sum(rate("
            "yunshu_http_request_duration_seconds_bucket[5m])) by (le)) > 3", store)
        assert out["status"] == STATUS_OK
        assert out["value"] < 1.0

    def test_无桶样本报_no_data(self):
        store = store_with([(0.0, {key("other"): 1.0}), (300.0, {key("other"): 2.0})])
        out = evaluate_expression(
            "histogram_quantile(0.99, sum(rate("
            "yunshu_http_request_duration_seconds_bucket[5m])) by (le)) > 3", store)
        assert out["status"] == STATUS_NO_DATA


class TestUnsupported:
    def test_未知函数报_unsupported(self):
        out = evaluate_expression("predict_linear(yunshu_memory_usage_percent[1h], 60) > 85")
        assert out["status"] == STATUS_UNSUPPORTED

    def test_语法错误报_unsupported_而不是假绿(self):
        out = evaluate_expression("yunshu_memory_usage_percent >")
        assert out["status"] == STATUS_UNSUPPORTED

    def test_除零不崩(self):
        store = store_with([(0.0, {key("a"): 1.0}), (300.0, {key("a"): 1.0})])
        out = evaluate_expression("sum(rate(a[5m])) / 0 > 1", store)
        assert out["status"] == STATUS_UNSUPPORTED
