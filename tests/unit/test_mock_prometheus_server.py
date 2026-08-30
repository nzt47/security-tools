# -*- coding: utf-8 -*-
"""
mock_prometheus_server.py 回归测试（GitHub Issue #6 配套）

【背景】
Issue #6：observability-ci.yml `visibility-trend-mock-test` job 中
「验证 Mock 服务 query_range 端点」步骤失败（期望 status=success 且
points>=100）。历史根因是 workflow 侧 curl 未 URL-encode 特殊字符
（{success="true"}[7d]）导致 exit code 3；且 mock 服务此前没有任何
单元测试覆盖，回归无法被提前发现。

本测试把 CI 的验证契约固化为单元测试（不依赖真实 Prometheus/网络）：
1. /-/healthy 健康检查
2. query_range：workflow 同款查询（start=7 天前&end=now&step=1h）
   → status=success + 169 点（>=100）+ matrix + success="true" 标签
3. query_range：generate_visibility_trend.py 的全部 16 个 PromQL
   （weekly 7d/1h → 169 点，monthly 30d/6h → 121 点）
4. query_range：未知 metric → success + 空 result（不抛错）
5. query_range：非法 step（0h）→ 返回合法 JSON（防除零 → 非 JSON 500）
6. /api/v1/query 瞬时查询 → vector + 最新值

【可观测性约束】
- 本测试通过回环 HTTP 直连 mock 服务，无外部网络依赖。
- 全部断言与 observability-ci.yml visibility-trend-mock-test job 对齐。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

# 将 scripts 目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from generate_visibility_trend import TREND_QUERIES  # noqa: E402
from mock_prometheus_server import (  # noqa: E402
    MOCK_METRIC_VALUES,
    MockPrometheusHandler,
    ThreadingHTTPServer,
)

# 生成器实际使用的 16 个 PromQL（与 CI「验证 JSON 元数据」的 series_count=16 对齐）
TREND_PROMQLS: list[str] = [q["promql"] for q in TREND_QUERIES]
assert len(TREND_PROMQLS) == 16, f"TREND_QUERIES 应有 16 个查询，实际 {len(TREND_PROMQLS)}"

WORKFLOW_QUERY = 'max_over_time(yunshu_visibility_runtime_structured_log_coverage{success="true"}[7d])'


@pytest.fixture(scope="module")
def mock_server_url() -> str:
    """在回环地址启动 mock 服务（端口 0 = 系统分配空闲端口），返回 base URL"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockPrometheusHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _http_get_json(url: str, params: dict) -> dict:
    """GET 请求并解析 JSON（模拟 curl -G --data-urlencode 的编码语义）"""
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(full_url, timeout=10) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def _query_range(mock_server_url: str, query: str, days: int, step: str) -> dict:
    """构造与 CI 一致的 query_range 请求（start=days 天前&end=now&step=step）"""
    end = int(time.time())
    start = end - days * 24 * 3600
    return _http_get_json(f"{mock_server_url}/api/v1/query_range", {
        "query": query,
        "start": str(start),
        "end": str(end),
        "step": step,
    })


# ═══════════════════════════════════════════════════════════════
#  1. 健康检查
# ═══════════════════════════════════════════════════════════════

class TestMockHealth:
    """验证 /-/healthy 端点"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_healthy_endpoint_returns_200(self, mock_server_url):
        with urllib.request.urlopen(f"{mock_server_url}/-/healthy", timeout=10) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
        assert "Healthy" in body


# ═══════════════════════════════════════════════════════════════
#  2. query_range 端点（Issue #6 验证契约）
# ═══════════════════════════════════════════════════════════════

class TestMockQueryRange:
    """验证 /api/v1/query_range 端点"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_workflow_query_7d_1h_returns_success_with_169_points(self, mock_server_url):
        """workflow 同款查询：start=7天前&end=now&step=1h → success + 169 点"""
        data = _query_range(mock_server_url, WORKFLOW_QUERY, days=7, step="1h")
        assert data["status"] == "success", f"期望 status=success，实际 {data.get('status')}"
        # matrix 格式
        assert data["data"]["resultType"] == "matrix"
        result = data["data"]["result"]
        assert len(result) == 1
        assert result[0]["metric"].get("success") == "true"
        points = len(result[0]["values"])
        # 真实 Prometheus 语义：(end-start)/step + 1 = 7*24*3600/3600 + 1 = 169
        assert points == 169, f"期望 169 点，实际 {points}"
        assert points >= 100, "点数不足 100"
        # 数据点格式 [ts, value]，且首点对应基准数据首日值
        ts, val = result[0]["values"][0]
        assert float(ts) > 0
        assert float(val) == pytest.approx(MOCK_METRIC_VALUES[
            "yunshu_visibility_runtime_structured_log_coverage"][0])

    @pytest.mark.unit
    @pytest.mark.p0
    def test_all_trend_queries_return_expected_point_counts(self, mock_server_url):
        """generate_visibility_trend.py 全部 16 个 PromQL：weekly 169 点 / monthly 121 点"""
        for promql in TREND_PROMQLS:
            # weekly: 7d / 1h → 169 点
            weekly = _query_range(mock_server_url, promql.replace("{__range}", "7d"), days=7, step="1h")
            assert weekly["status"] == "success", f"weekly 查询失败: {promql}"
            r = weekly["data"]["result"]
            assert r, f"weekly 查询返回空: {promql}"
            assert len(r[0]["values"]) == 169, f"weekly 点数 != 169: {promql} ({len(r[0]['values'])})"
            # monthly: 30d / 6h → 121 点
            monthly = _query_range(mock_server_url, promql.replace("{__range}", "30d"), days=30, step="6h")
            assert monthly["status"] == "success", f"monthly 查询失败: {promql}"
            r2 = monthly["data"]["result"]
            assert r2, f"monthly 查询返回空: {promql}"
            assert len(r2[0]["values"]) == 121, f"monthly 点数 != 121: {promql} ({len(r2[0]['values'])})"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_unknown_metric_returns_empty_result(self, mock_server_url):
        """未知 metric → status=success + 空 result（不抛错）"""
        data = _query_range(mock_server_url, "max_over_time(unknown_metric[7d])", days=7, step="1h")
        assert data["status"] == "success"
        assert data["data"]["resultType"] == "matrix"
        assert data["data"]["result"] == []

    @pytest.mark.unit
    @pytest.mark.p1
    def test_unrecognized_query_returns_empty_result(self, mock_server_url):
        """无法提取 metric 的查询（如 sum(up)）→ success + 空 result"""
        data = _query_range(mock_server_url, "sum(up)", days=7, step="1h")
        assert data["status"] == "success"
        assert data["data"]["result"] == []

    @pytest.mark.unit
    @pytest.mark.p1
    def test_zero_step_returns_valid_json_not_500(self, mock_server_url):
        """回归：非法 step=0h 不得触发除零 → 非 JSON 500，仍返回合法 JSON"""
        data = _query_range(
            mock_server_url,
            "max_over_time(yunshu_visibility_runtime_structured_log_coverage[7d])",
            days=7,
            step="0h",
        )
        assert "status" in data, "step=0h 时必须返回 JSON 而非 500 页面"
        assert data["status"] == "success"
        assert data["data"]["result"][0]["values"]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_missing_start_end_uses_defaults(self, mock_server_url):
        """缺省 start/end 时回退默认 7 天窗口，仍返回 >=100 点"""
        end = int(time.time())
        data = _http_get_json(f"{mock_server_url}/api/v1/query_range", {
            "query": WORKFLOW_QUERY,
            "step": "1h",
        })
        assert data["status"] == "success"
        assert len(data["data"]["result"][0]["values"]) >= 100


# ═══════════════════════════════════════════════════════════════
#  3. /api/v1/query 瞬时查询端点
# ═══════════════════════════════════════════════════════════════

class TestMockQuery:
    """验证 /api/v1/query 端点"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_instant_query_returns_latest_value(self, mock_server_url):
        data = _http_get_json(f"{mock_server_url}/api/v1/query", {
            "query": 'yunshu_visibility_runtime_structured_log_coverage{success="true"}',
        })
        assert data["status"] == "success"
        assert data["data"]["resultType"] == "vector"
        result = data["data"]["result"]
        assert len(result) == 1
        assert result[0]["metric"].get("success") == "true"
        latest = MOCK_METRIC_VALUES["yunshu_visibility_runtime_structured_log_coverage"][-1]
        assert float(result[0]["value"][1]) == pytest.approx(latest)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_unknown_metric_instant_query_returns_empty(self, mock_server_url):
        data = _http_get_json(f"{mock_server_url}/api/v1/query", {"query": "unknown_metric"})
        assert data["status"] == "success"
        assert data["data"]["result"] == []
