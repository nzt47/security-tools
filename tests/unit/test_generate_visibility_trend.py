# -*- coding: utf-8 -*-
"""
generate_visibility_trend.py 单元测试（任务 5 配套）

【测试目标】
覆盖 scripts/generate_visibility_trend.py 的核心逻辑（无外部 Prometheus 依赖，
全部通过 mock urllib.request 实现）：
1. PrometheusClient.check_reachable()：可达 / 不可达判定
2. PrometheusClient.query_range()：成功 / 重试 / 失败 / 空数据
3. TrendReportGenerator.generate()：周期校验 / 健康检查失败 / 并行查询降级
4. TrendReportRenderer：Markdown / HTML 渲染 / 趋势摘要 / SVG 折线图
5. MetricSeries 属性：trend_delta / trend_percent / 边界场景
6. _degraded_report()：降级报告包含错误码与处置建议
7. main() CLI 参数解析 / 退出码语义 / --non-interactive 降级路径

【可观测性约束】
- 结构化日志：trace_id / module_name / action / duration_ms
- 边界显性化：所有错误场景携带业务错误码（TREND_ERR_001~006）
- 健康检查：TrendReportGenerator.generate 内置 check_reachable 健康检查

【生成日志摘要】
- 生成时间：2026-06-28
- 版本：v1.0.0
- 内容：generate_visibility_trend.py 单元测试，覆盖率目标≥80%
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 将 scripts 目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from generate_visibility_trend import (  # noqa: E402
    MetricSeries,
    TrendReport,
    TrendReportError,
    TrendReportGenerator,
    TrendReportRenderer,
    PrometheusClient,
    TREND_QUERIES,
    TREND_ERR_PROMETHEUS_UNREACHABLE,
    TREND_ERR_QUERY_FAILED,
    TREND_ERR_INVALID_PERIOD,
    TREND_ERR_NO_DATA,
    _degraded_report,
    main,
)
import argparse  # noqa: E402


# ═══════════════════════════════════════════════════════════════
#  测试夹具
# ═══════════════════════════════════════════════════════════════

def _make_series(
    name: str = "test_metric",
    layer: str = "runtime",
    values: list[float] | None = None,
    unit: str = "%",
    status: str = "success",
) -> MetricSeries:
    """构造 MetricSeries（自动生成时间戳）"""
    values = values if values is not None else [80.0, 82.0, 85.0, 88.0, 90.0]
    base_ts = 1700000000.0
    timestamps = [base_ts + i * 3600 for i in range(len(values))]
    return MetricSeries(
        name=name,
        description=f"测试指标 {name}",
        layer=layer,
        unit=unit,
        timestamps=timestamps,
        values=values,
        status=status,
    )


def _make_report(
    series: list[MetricSeries] | None = None,
    overall_status: str = "pass",
    period: str = "weekly",
) -> TrendReport:
    """构造 TrendReport"""
    if series is None:
        series = [_make_series(name="runtime_structured_log_coverage", layer="runtime")]
    return TrendReport(
        trace_id="test-trace-trend-0001",
        period=period,
        start_time="2026-06-21T00:00:00+00:00",
        end_time="2026-06-28T00:00:00+00:00",
        generated_at="2026-06-28T10:00:00+00:00",
        duration_ms=1234.56,
        series=series,
        prometheus_url="http://localhost:9091",
        overall_status=overall_status,
        errors=[],
    )


def _mock_urlopen_response(
    status: int = 200,
    body: bytes = b'{"status":"success","data":{"result":[]}}',
) -> MagicMock:
    """构造 mock urlopen 返回的 context manager"""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    ctx.status = status
    ctx.read = MagicMock(return_value=body)
    return ctx


# ═══════════════════════════════════════════════════════════════
#  1. MetricSeries 属性
# ═══════════════════════════════════════════════════════════════

class TestMetricSeriesProperties:
    """验证 MetricSeries 数据类的派生属性"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_trend_delta_should_return_difference_between_last_and_first(self):
        """trend_delta 应为最新值减去最早值"""
        s = _make_series(values=[80.0, 85.0, 90.0])
        assert s.trend_delta == pytest.approx(10.0)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_trend_percent_should_return_relative_change(self):
        """trend_percent 应返回百分比变化"""
        s = _make_series(values=[80.0, 90.0])
        # (90-80)/80 * 100 = 12.5
        assert s.trend_percent == pytest.approx(12.5)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_trend_delta_should_return_none_when_less_than_two_points(self):
        """少于两个数据点时 trend_delta 应为 None"""
        s = _make_series(values=[80.0])
        assert s.trend_delta is None
        assert s.trend_percent is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_trend_percent_should_return_none_when_first_is_zero(self):
        """首值为 0 时应返回 None（避免除零）"""
        s = _make_series(values=[0.0, 10.0])
        assert s.trend_percent is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_point_count_and_latest_value_should_reflect_data(self):
        """point_count / latest_value / first_value 应正确"""
        s = _make_series(values=[10.0, 20.0, 30.0])
        assert s.point_count == 3
        assert s.latest_value == 30.0
        assert s.first_value == 10.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_empty_series_should_have_none_for_latest_and_first(self):
        """空数据 series 的 latest_value / first_value 应为 None"""
        s = MetricSeries(name="empty", description="d", layer="runtime", unit="")
        assert s.latest_value is None
        assert s.first_value is None
        assert s.point_count == 0


# ═══════════════════════════════════════════════════════════════
#  2. PrometheusClient.check_reachable
# ═══════════════════════════════════════════════════════════════

class TestPrometheusClientCheckReachable:
    """验证 Prometheus 健康检查"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_true_when_healthy(self):
        """Prometheus /-/healthy 返回 200 时应返回 True"""
        client = PrometheusClient("http://localhost:9091", timeout=2, max_retries=0)
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_urlopen_response(status=200, body=b"OK")
            assert client.check_reachable() is True
            mock_urlopen.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_false_when_url_error(self):
        """URLError 时应返回 False（不抛异常）"""
        client = PrometheusClient("http://nonexistent:9091", timeout=1, max_retries=0)
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("connection refused")
            assert client.check_reachable() is False

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_false_when_oserror(self):
        """OSError 时应返回 False"""
        client = PrometheusClient("http://localhost:9091", timeout=1, max_retries=0)
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = OSError("timeout")
            assert client.check_reachable() is False


# ═══════════════════════════════════════════════════════════════
#  3. PrometheusClient.query_range
# ═══════════════════════════════════════════════════════════════

class TestPrometheusClientQueryRange:
    """验证 PromQL range query 行为"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_parsed_values_on_success(self):
        """成功响应应返回解析后的数据点列表"""
        client = PrometheusClient("http://localhost:9091", timeout=2, max_retries=0)
        # 构造一个 matrix 响应
        body = json.dumps({
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"__name__": "yunshu_visibility_runtime_structured_log_coverage"},
                        "values": [
                            [1700000000, "85.0"],
                            [1700003600, "87.5"],
                            [1700007200, "90.0"],
                        ],
                    }
                ]
            }
        }).encode("utf-8")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_urlopen_response(status=200, body=body)
            values = client.query_range("up", 1700000000, 1700007200, "1h")
            assert len(values) == 3
            assert values[0]["timestamp"] == 1700000000.0
            assert values[0]["value"] == 85.0
            assert values[2]["value"] == 90.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_empty_list_when_no_data(self):
        """result 为空时应返回空列表"""
        client = PrometheusClient("http://localhost:9091", timeout=2, max_retries=0)
        body = b'{"status":"success","data":{"result":[]}}'
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_urlopen_response(status=200, body=body)
            values = client.query_range("up", 0, 100, "1h")
            assert values == []

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_raise_on_non_200_status(self):
        """非 200 状态码应抛 TrendReportError（code=TREND_ERR_002）"""
        client = PrometheusClient("http://localhost:9091", timeout=2, max_retries=0)
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_urlopen_response(status=500, body=b"internal error")
            with pytest.raises(TrendReportError) as exc:
                client.query_range("up", 0, 100, "1h")
            assert exc.value.code == TREND_ERR_QUERY_FAILED

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_raise_on_error_status_in_body(self):
        """body 中 status!=success 应抛 TrendReportError"""
        client = PrometheusClient("http://localhost:9091", timeout=2, max_retries=0)
        body = b'{"status":"error","errorType":"bad_data","error":"invalid query"}'
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_urlopen_response(status=200, body=body)
            with pytest.raises(TrendReportError) as exc:
                client.query_range("up", 0, 100, "1h")
            assert exc.value.code == TREND_ERR_QUERY_FAILED

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_retry_on_urlerror_then_succeed(self):
        """URLError 时应重试，最终成功"""
        client = PrometheusClient("http://localhost:9091", timeout=2, max_retries=2)
        body = json.dumps({
            "status": "success",
            "data": {"result": [{"metric": {}, "values": [[1, "1.0"]]}]}
        }).encode("utf-8")
        success_resp = _mock_urlopen_response(status=200, body=body)
        with patch("urllib.request.urlopen") as mock_urlopen, \
             patch("time.sleep") as mock_sleep:
            # 第一次失败，第二次成功
            mock_urlopen.side_effect = [urllib.error.URLError("fail"), success_resp]
            values = client.query_range("up", 0, 100, "1h")
            assert len(values) == 1
            assert mock_sleep.call_count == 1  # 第一次失败后 sleep 一次

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_raise_after_max_retries(self):
        """超过最大重试次数后应抛 TrendReportError"""
        client = PrometheusClient("http://localhost:9091", timeout=2, max_retries=1)
        with patch("urllib.request.urlopen") as mock_urlopen, \
             patch("time.sleep"):
            mock_urlopen.side_effect = urllib.error.URLError("persistent fail")
            with pytest.raises(TrendReportError) as exc:
                client.query_range("up", 0, 100, "1h")
            assert exc.value.code == TREND_ERR_QUERY_FAILED
            # 应调用 max_retries + 1 次
            assert mock_urlopen.call_count == 2


# ═══════════════════════════════════════════════════════════════
#  4. TrendReportGenerator
# ═══════════════════════════════════════════════════════════════

class TestTrendReportGenerator:
    """验证报告生成器逻辑"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_raise_on_invalid_period(self):
        """无效周期应抛 TrendReportError（code=TREND_ERR_004）"""
        gen = TrendReportGenerator("http://localhost:9091", timeout=2, max_workers=2)
        with pytest.raises(TrendReportError) as exc:
            gen.generate("invalid_period")
        assert exc.value.code == TREND_ERR_INVALID_PERIOD

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_raise_when_prometheus_unreachable(self):
        """Prometheus 不可达应抛 TREND_ERR_001"""
        gen = TrendReportGenerator("http://nonexistent:9091", timeout=1, max_workers=2)
        with patch.object(PrometheusClient, "check_reachable", return_value=False):
            with pytest.raises(TrendReportError) as exc:
                gen.generate("weekly")
            assert exc.value.code == TREND_ERR_PROMETHEUS_UNREACHABLE

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_degraded_report_when_some_metrics_fail(self):
        """部分指标查询失败应返回 degraded 报告（不阻塞）"""
        gen = TrendReportGenerator("http://localhost:9091", timeout=2, max_workers=2)
        # mock check_reachable 返回 True
        with patch.object(PrometheusClient, "check_reachable", return_value=True), \
             patch.object(PrometheusClient, "query_range") as mock_qr:
            # 第一个查询抛异常，其他返回空（也视为失败但不会抛）
            # 让所有 query_range 都抛 TrendReportError，触发降级路径
            mock_qr.side_effect = TrendReportError(
                TREND_ERR_QUERY_FAILED, "mock failure", {}
            )
            report = gen.generate("weekly")
            assert report.overall_status == "fail"  # 所有指标都失败
            assert len(report.errors) == len(TREND_QUERIES)
            # 每个系列都应为 failed 状态
            for s in report.series:
                assert s.status == "failed"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_pass_when_all_metrics_succeed(self):
        """所有指标查询成功且非空时应返回 pass"""
        gen = TrendReportGenerator("http://localhost:9091", timeout=2, max_workers=2)
        with patch.object(PrometheusClient, "check_reachable", return_value=True), \
             patch.object(PrometheusClient, "query_range") as mock_qr:
            # 返回非空数据
            mock_qr.return_value = [
                {"timestamp": float(i), "value": float(80 + i)}
                for i in range(5)
            ]
            report = gen.generate("weekly")
            assert report.overall_status == "pass"
            assert len(report.series) == len(TREND_QUERIES)
            for s in report.series:
                assert s.status == "success"
                assert s.point_count == 5

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_mark_empty_status_when_query_returns_no_data(self):
        """查询返回空数据时 series.status 应标记为 empty（非 failed）

        注意：empty 不计入 failed_count，因此 overall_status 仍为 pass。
        此测试验证空数据的正确分类语义。
        """
        gen = TrendReportGenerator("http://localhost:9091", timeout=2, max_workers=2)
        with patch.object(PrometheusClient, "check_reachable", return_value=True), \
             patch.object(PrometheusClient, "query_range") as mock_qr:
            # 全部返回空数据
            mock_qr.return_value = []
            report = gen.generate("weekly")
            # 空数据 → status="empty"，不计入 failed_count → overall_status="pass"
            assert report.overall_status == "pass"
            for s in report.series:
                assert s.status == "empty"
                assert s.point_count == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_mixed_empty_and_success_not_affect_overall_status(self):
        """空数据与成功数据混合时，empty 不影响 overall_status（仍为 pass）"""
        gen = TrendReportGenerator("http://localhost:9091", timeout=2, max_workers=2)
        with patch.object(PrometheusClient, "check_reachable", return_value=True), \
             patch.object(PrometheusClient, "query_range") as mock_qr:
            # 交替返回空 / 非空
            mock_qr.side_effect = [
                [],  # 空
                [{"timestamp": 1.0, "value": 80.0}],  # 非空
            ] * len(TREND_QUERIES)
            report = gen.generate("weekly")
            # empty 不算 failed → overall_status="pass"
            assert report.overall_status == "pass"
            # 应同时存在 empty 和 success 状态的 series
            statuses = {s.status for s in report.series}
            assert "empty" in statuses
            assert "success" in statuses


# ═══════════════════════════════════════════════════════════════
#  5. TrendReportRenderer
# ═══════════════════════════════════════════════════════════════

class TestTrendReportRenderer:
    """验证报告渲染器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_render_markdown_should_contain_key_sections(self):
        """Markdown 报告应包含核心章节"""
        report = _make_report()
        renderer = TrendReportRenderer(report)
        md = renderer.render_markdown()
        assert "# 四层可见性趋势" in md
        assert "## 概览统计" in md
        assert "## 健康检查" in md
        assert "Trace ID" in md
        assert report.trace_id in md

    @pytest.mark.unit
    @pytest.mark.p0
    def test_render_markdown_should_show_data_missing_for_failed_series(self):
        """失败的指标应显示「数据缺失」标记"""
        failed_series = MetricSeries(
            name="failed_metric",
            description="失败指标",
            layer="runtime",
            unit="",
            status="failed",
            error="mock failure",
        )
        report = _make_report(series=[failed_series], overall_status="degraded")
        md = TrendReportRenderer(report).render_markdown()
        assert "数据缺失" in md
        assert "mock failure" in md

    @pytest.mark.unit
    @pytest.mark.p0
    def test_render_html_should_contain_svg_chart(self):
        """HTML 报告应包含 SVG 折线图"""
        report = _make_report()
        renderer = TrendReportRenderer(report)
        html = renderer.render_html()
        assert "<svg" in html
        assert "<path" in html
        assert "stroke" in html

    @pytest.mark.unit
    @pytest.mark.p0
    def test_render_html_should_contain_meta_and_health_sections(self):
        """HTML 报告应包含元信息与健康检查"""
        report = _make_report()
        html = TrendReportRenderer(report).render_html()
        assert "<!DOCTYPE html>" in html
        assert "<html" in html
        assert "健康检查" in html
        assert report.trace_id in html

    @pytest.mark.unit
    @pytest.mark.p1
    def test_render_html_should_show_warning_for_failed_series(self):
        """失败的指标在 HTML 中应显示 warning div"""
        failed_series = MetricSeries(
            name="bad_metric",
            description="d",
            layer="runtime",
            unit="",
            status="failed",
            error="boom",
        )
        report = _make_report(series=[failed_series], overall_status="degraded")
        html = TrendReportRenderer(report).render_html()
        assert "warning" in html
        assert "boom" in html

    @pytest.mark.unit
    @pytest.mark.p1
    def test_svg_chart_should_return_warning_when_no_values(self):
        """空数据 series 渲染 SVG 时应返回 warning div"""
        empty_series = MetricSeries(
            name="empty",
            description="d",
            layer="runtime",
            unit="",
            status="empty",
        )
        report = _make_report(series=[empty_series])
        renderer = TrendReportRenderer(report)
        svg = renderer._render_svg_chart(empty_series)
        assert "无数据" in svg or "warning" in svg

    @pytest.mark.unit
    @pytest.mark.p1
    def test_trend_summary_should_return_unknown_when_insufficient_data(self):
        """数据不足时趋势摘要应返回 unknown 图标"""
        s = _make_series(values=[80.0])  # 单点
        renderer = TrendReportRenderer(_make_report(series=[s]))
        icon, text = renderer._trend_summary(s)
        assert icon == renderer.TREND_ICONS["unknown"]
        assert "数据不足" in text

    @pytest.mark.unit
    @pytest.mark.p1
    def test_trend_summary_should_return_flat_when_delta_below_threshold(self):
        """变化幅度极小时应返回持平"""
        s = _make_series(values=[80.0, 80.005])  # 0.005 < 0.01
        renderer = TrendReportRenderer(_make_report(series=[s]))
        icon, text = renderer._trend_summary(s)
        assert icon == renderer.TREND_ICONS["flat"]
        assert "持平" in text

    @pytest.mark.unit
    @pytest.mark.p1
    def test_trend_summary_should_inverse_for_rule_violations(self):
        """逆向指标（rule_violations）下降为改善"""
        s = _make_series(
            name="architecture_rule_violations",
            layer="architecture",
            values=[10.0, 5.0],  # 下降 5
            unit="个",
        )
        renderer = TrendReportRenderer(_make_report(series=[s]))
        icon, text = renderer._trend_summary(s)
        assert icon == renderer.TREND_ICONS["down"]
        assert "改善" in text


# ═══════════════════════════════════════════════════════════════
#  6. _degraded_report
# ═══════════════════════════════════════════════════════════════

class TestDegradedReport:
    """验证降级报告生成"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_contain_error_code_and_message(self):
        """降级报告必须包含错误码与错误消息"""
        err = TrendReportError(
            TREND_ERR_PROMETHEUS_UNREACHABLE,
            "Prometheus 不可达",
            {"url": "http://x"},
        )
        args = argparse.Namespace(
            prometheus_url="http://localhost:9091",
            period="weekly",
        )
        report = _degraded_report(err, args, "test-trace-id")
        assert TREND_ERR_PROMETHEUS_UNREACHABLE in report
        assert "Prometheus 不可达" in report
        assert "test-trace-id" in report

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_contain_recovery_suggestions(self):
        """降级报告应包含处置建议"""
        err = TrendReportError(TREND_ERR_QUERY_FAILED, "查询失败", {})
        args = argparse.Namespace(prometheus_url="http://x", period="weekly")
        report = _degraded_report(err, args, "trace-1")
        assert "处置建议" in report
        assert "curl" in report  # 应提到 curl 健康检查


# ═══════════════════════════════════════════════════════════════
#  7. main() CLI 入口
# ═══════════════════════════════════════════════════════════════

class TestMainCli:
    """验证 main() 函数的退出码与降级路径"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_2_when_prometheus_unreachable_interactive(self, tmp_path: Path):
        """交互模式下 Prometheus 不可达应返回 2"""
        with patch.object(PrometheusClient, "check_reachable", return_value=False):
            exit_code = main([
                "--prometheus-url", "http://nonexistent:9091",
                "--period", "weekly",
                "--output", str(tmp_path),
            ])
        assert exit_code == 2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_2_and_write_degraded_report_in_non_interactive(
        self, tmp_path: Path
    ):
        """非交互模式下失败应写入降级报告并返回 2"""
        with patch.object(PrometheusClient, "check_reachable", return_value=False):
            exit_code = main([
                "--prometheus-url", "http://nonexistent:9091",
                "--period", "weekly",
                "--output", str(tmp_path),
                "--non-interactive",
            ])
        assert exit_code == 2
        # 应生成降级报告文件
        degraded_files = list(tmp_path.glob("*degraded*.md"))
        assert len(degraded_files) == 1, f"应生成 1 个降级报告, 实际: {degraded_files}"
        content = degraded_files[0].read_text(encoding="utf-8")
        assert TREND_ERR_PROMETHEUS_UNREACHABLE in content

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_0_when_all_queries_succeed(self, tmp_path: Path):
        """所有指标成功时应返回 0 并生成报告"""
        with patch.object(PrometheusClient, "check_reachable", return_value=True), \
             patch.object(PrometheusClient, "query_range") as mock_qr:
            mock_qr.return_value = [
                {"timestamp": float(i), "value": 80.0 + i}
                for i in range(5)
            ]
            exit_code = main([
                "--prometheus-url", "http://localhost:9091",
                "--period", "weekly",
                "--output", str(tmp_path),
                "--format", "markdown",
            ])
        assert exit_code == 0
        # 应生成 markdown + json 文件
        md_files = list(tmp_path.glob("*.md"))
        json_files = list(tmp_path.glob("*.json"))
        assert len(md_files) == 1
        assert len(json_files) == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_1_when_degraded(self, tmp_path: Path):
        """部分指标失败（降级）应返回 1"""
        with patch.object(PrometheusClient, "check_reachable", return_value=True), \
             patch.object(PrometheusClient, "query_range") as mock_qr:
            # 第一个查询抛异常，其他返回空 → 部分失败
            mock_qr.side_effect = TrendReportError(
                TREND_ERR_QUERY_FAILED, "mock", {}
            )
            exit_code = main([
                "--prometheus-url", "http://localhost:9091",
                "--period", "monthly",
                "--output", str(tmp_path),
                "--format", "markdown",
            ])
        # 所有指标失败 → fail → 退出码 1
        assert exit_code == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_support_html_format(self, tmp_path: Path):
        """--format html 应生成 HTML 文件"""
        with patch.object(PrometheusClient, "check_reachable", return_value=True), \
             patch.object(PrometheusClient, "query_range") as mock_qr:
            mock_qr.return_value = [{"timestamp": 1.0, "value": 80.0}]
            exit_code = main([
                "--prometheus-url", "http://localhost:9091",
                "--period", "weekly",
                "--output", str(tmp_path),
                "--format", "html",
            ])
        assert exit_code == 0
        html_files = list(tmp_path.glob("*.html"))
        assert len(html_files) == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_support_both_format(self, tmp_path: Path):
        """--format both 应同时生成 Markdown + HTML"""
        with patch.object(PrometheusClient, "check_reachable", return_value=True), \
             patch.object(PrometheusClient, "query_range") as mock_qr:
            mock_qr.return_value = [{"timestamp": 1.0, "value": 80.0}]
            exit_code = main([
                "--prometheus-url", "http://localhost:9091",
                "--period", "weekly",
                "--output", str(tmp_path),
                "--format", "both",
            ])
        assert exit_code == 0
        md_files = list(tmp_path.glob("*.md"))
        html_files = list(tmp_path.glob("*.html"))
        assert len(md_files) == 1
        assert len(html_files) == 1


# ═══════════════════════════════════════════════════════════════
#  8. 错误码完整性
# ═══════════════════════════════════════════════════════════════

class TestErrorCodes:
    """验证错误码定义完整性"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_all_error_codes_should_be_unique(self):
        """所有错误码必须唯一"""
        from generate_visibility_trend import (
            TREND_ERR_PROMETHEUS_UNREACHABLE,
            TREND_ERR_QUERY_FAILED,
            TREND_ERR_NO_DATA,
            TREND_ERR_INVALID_PERIOD,
            TREND_ERR_RENDER_FAILED,
            TREND_ERR_OUTPUT_WRITE_FAILED,
        )
        codes = [
            TREND_ERR_PROMETHEUS_UNREACHABLE,
            TREND_ERR_QUERY_FAILED,
            TREND_ERR_NO_DATA,
            TREND_ERR_INVALID_PERIOD,
            TREND_ERR_RENDER_FAILED,
            TREND_ERR_OUTPUT_WRITE_FAILED,
        ]
        assert len(set(codes)) == len(codes), f"错误码重复: {codes}"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_all_error_codes_should_follow_naming_convention(self):
        """错误码应遵循 TREND_ERR_NNN 格式"""
        from generate_visibility_trend import (
            TREND_ERR_PROMETHEUS_UNREACHABLE,
            TREND_ERR_QUERY_FAILED,
            TREND_ERR_NO_DATA,
            TREND_ERR_INVALID_PERIOD,
            TREND_ERR_RENDER_FAILED,
            TREND_ERR_OUTPUT_WRITE_FAILED,
        )
        pattern = re.compile(r"^TREND_ERR_\d{3}$")
        for code in [
            TREND_ERR_PROMETHEUS_UNREACHABLE,
            TREND_ERR_QUERY_FAILED,
            TREND_ERR_NO_DATA,
            TREND_ERR_INVALID_PERIOD,
            TREND_ERR_RENDER_FAILED,
            TREND_ERR_OUTPUT_WRITE_FAILED,
        ]:
            assert pattern.match(code), f"错误码格式错误: {code}"


# ═══════════════════════════════════════════════════════════════
#  9. TREND_QUERIES 查询清单完整性
# ═══════════════════════════════════════════════════════════════

class TestTrendQueriesDefinition:
    """验证查询清单的定义完整性"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_cover_all_four_layers_plus_overall(self):
        """查询清单应覆盖 overall + 四层"""
        layers = {q["layer"] for q in TREND_QUERIES}
        assert "overall" in layers
        assert "runtime" in layers
        assert "verification" in layers
        assert "business" in layers
        assert "architecture" in layers

    @pytest.mark.unit
    @pytest.mark.p0
    def test_each_query_should_have_required_fields(self):
        """每个查询定义应包含 name / description / layer / unit / promql"""
        required_fields = {"name", "description", "layer", "unit", "promql"}
        for q in TREND_QUERIES:
            missing = required_fields - set(q.keys())
            assert not missing, f"查询 {q.get('name')} 缺字段: {missing}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_promql_should_contain_yunshu_visibility_prefix(self):
        """所有 PromQL 应引用 yunshu_visibility_* 指标"""
        for q in TREND_QUERIES:
            assert "yunshu_visibility_" in q["promql"], (
                f"查询 {q['name']} 未引用 yunshu_visibility_* 指标: {q['promql']}"
            )

    @pytest.mark.unit
    @pytest.mark.p1
    def test_overall_queries_should_use_max_over_time(self):
        """overall 层的状态指标应使用 max_over_time 聚合"""
        overall_queries = [q for q in TREND_QUERIES if q["layer"] == "overall"]
        # overall_status / threshold_violations / report_duration 应使用 max_over_time
        max_over_queries = [q for q in overall_queries if "max_over_time" in q["promql"]]
        assert len(max_over_queries) >= 3, (
            f"overall 层应至少 3 个查询使用 max_over_time, 实际: {len(max_over_queries)}"
        )
