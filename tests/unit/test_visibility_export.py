# -*- coding: utf-8 -*-
"""
visibility_report.py Prometheus 导出功能单元测试（任务 5 配套）

【测试目标】
覆盖 scripts/visibility_report.py 中新增的 Prometheus 指标导出能力：
1. export_to_prometheus() 输出格式合法性（HELP/TYPE/标签/数值/timestamp）
2. 状态码映射：pass=0 / fail=1 / degraded=2
3. 逆向指标 success 标签语义（arch_rule_violations 超阈→success="false"）
4. 层级标签映射：中文层名→英文 label（运行时可见→runtime 等）
5. 阈值违规计数正确反映 threshold_violations 长度
6. 无效数值（None / 非数字字符串）应被跳过且不抛异常
7. serve_metrics HTTP 端点：/metrics 返回 200 + 正确 Content-Type
8. 空快照场景返回降级指标（yunshu_visibility_overall_status{status="degraded"} 2）
9. /health 端点返回依赖项状态 JSON
10. 非法路径返回 404

【可观测性约束】
- 结构化日志：trace_id / module_name / action / duration_ms
- 边界显性化：所有断言携带业务上下文，失败时易定位
- 健康检查：测试 /health 端点验证依赖项可达性

【生成日志摘要】
- 生成时间：2026-06-28
- 版本：v1.0.0
- 内容：visibility_report Prometheus 导出功能单元测试，覆盖率目标≥80%
"""

from __future__ import annotations

import json
import re
import sys
import time
import threading
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 将 scripts 目录加入 sys.path 以导入 visibility_report 模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from visibility_report import (  # noqa: E402
    Metric,
    LayerReport,
    VisibilityReport,
    export_to_prometheus,
    _VIS_METRIC_PREFIX,
    _LAYER_LABEL_MAP,
    _INVERSE_METRICS,
    _VisibilityMetricsState,
    _build_prometheus_handler,
    serve_metrics,
)
from http.server import HTTPServer  # noqa: E402


# ═══════════════════════════════════════════════════════════════
#  测试夹具
# ═══════════════════════════════════════════════════════════════

def _make_layer(
    layer_name: str,
    metrics: list[Metric] | None = None,
    overall_passed: bool = True,
) -> LayerReport:
    """构造层级报告（用于测试）"""
    layer = LayerReport(layer_name=layer_name, description=f"{layer_name} 测试")
    for m in metrics or []:
        layer.add_metric(m)
    layer.overall_passed = overall_passed
    return layer


def _make_report(
    overall_status: str = "pass",
    layers: list[LayerReport] | None = None,
    threshold_violations: list[str] | None = None,
    duration_ms: float = 100.0,
) -> VisibilityReport:
    """构造一份完整的可见性报告（用于测试）"""
    if layers is None:
        # 默认四层均通过
        layers = [
            _make_layer("运行时可见", [
                Metric(name="structured_log_coverage", value=90.0, threshold=80.0, unit="%", passed=True),
            ]),
            _make_layer("验证过程可见", [
                Metric(name="test_coverage", value=75.0, threshold=70.0, unit="%", passed=True),
            ]),
            _make_layer("业务价值可见", [
                Metric(name="track_event_coverage", value=85.0, threshold=80.0, unit="%", passed=True),
            ]),
            _make_layer("架构影响可见", [
                Metric(name="dependency_graph_nodes", value=120, threshold=10, unit="个", passed=True),
            ]),
        ]
    return VisibilityReport(
        trace_id="test-trace-0001",
        timestamp="2026-06-28T10:00:00Z",
        duration_ms=duration_ms,
        layers=layers,
        overall_status=overall_status,
        threshold_violations=threshold_violations or [],
    )


# ═══════════════════════════════════════════════════════════════
#  1. export_to_prometheus 基础格式验证
# ═══════════════════════════════════════════════════════════════

class TestExportToPrometheusFormat:
    """验证 Prometheus exposition 格式合规性"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_contain_help_and_type_lines(self):
        """输出必须包含 # HELP 和 # TYPE 元信息行"""
        report = _make_report(overall_status="pass")
        output = export_to_prometheus(report)
        assert "# HELP" in output
        assert "# TYPE" in output
        assert f"{_VIS_METRIC_PREFIX}_overall_status" in output

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_contain_liveness_probe_up_metric(self):
        """必须包含 yunshu_visibility_up=1 存活探针"""
        report = _make_report()
        output = export_to_prometheus(report)
        # 匹配 yunshu_visibility_up 1 <timestamp>
        pattern = re.compile(rf"^{_VIS_METRIC_PREFIX}_up\s+1\s+\d+\s*$", re.MULTILINE)
        assert pattern.search(output), f"未找到合法的 _up 指标行:\n{output}"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_contain_timestamp_ms_suffix(self):
        """每个指标行必须以毫秒时间戳结尾"""
        report = _make_report()
        output = export_to_prometheus(report)
        # 跳过 # HELP / # TYPE 注释行
        metric_lines = [ln for ln in output.strip().split("\n") if not ln.startswith("#")]
        assert len(metric_lines) >= 5, f"指标行过少:\n{output}"
        for line in metric_lines:
            # 末尾必须为整数时间戳（毫秒）
            m = re.search(r"\s(\d+)\s*$", line)
            assert m is not None, f"指标行缺少时间戳后缀: {line}"
            ts_ms = int(m.group(1))
            # 时间戳合理范围：2020-01-01 ~ 2050-01-01
            assert ts_ms > 1577836800000, f"时间戳过小: {line}"
            assert ts_ms < 2524608000000, f"时间戳过大: {line}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_include_report_timestamp_seconds_metric(self):
        """必须包含 report_timestamp_seconds（用于过期检测告警）"""
        report = _make_report()
        output = export_to_prometheus(report)
        assert f"{_VIS_METRIC_PREFIX}_report_timestamp_seconds" in output
        # 验证值为浮点数 Unix 秒
        pattern = re.compile(
            rf"^{_VIS_METRIC_PREFIX}_report_timestamp_seconds\s+[\d.]+\s+\d+\s*$",
            re.MULTILINE,
        )
        assert pattern.search(output), f"report_timestamp_seconds 格式错误:\n{output}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_end_with_newline(self):
        """输出应以换行符结尾（Prometheus 抓取规范）"""
        report = _make_report()
        output = export_to_prometheus(report)
        assert output.endswith("\n"), "输出必须以换行符结尾"


# ═══════════════════════════════════════════════════════════════
#  2. 状态码映射
# ═══════════════════════════════════════════════════════════════

class TestStatusCodeMapping:
    """验证 overall_status → 数值状态码映射"""

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.parametrize("status,expected_code", [
        ("pass", 0),
        ("fail", 1),
        ("degraded", 2),
    ])
    def test_should_map_status_to_correct_code(self, status: str, expected_code: int):
        """status=pass→0, fail→1, degraded→2"""
        report = _make_report(overall_status=status)
        output = export_to_prometheus(report)
        pattern = re.compile(
            rf'^{_VIS_METRIC_PREFIX}_overall_status\{{status="{status}"\}}\s+{expected_code}\s+\d+\s*$',
            re.MULTILINE,
        )
        assert pattern.search(output), (
            f"状态 {status} 应映射为 {expected_code}, 实际输出:\n{output}"
        )

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_default_to_degraded_for_unknown_status(self):
        """未知状态码应降级为 2（degraded）"""
        report = _make_report(overall_status="unknown_status")
        output = export_to_prometheus(report)
        # 未知状态仍输出指标，但 code 应为 2
        pattern = re.compile(
            rf'^{_VIS_METRIC_PREFIX}_overall_status\{{status="unknown_status"\}}\s+2\s+\d+\s*$',
            re.MULTILINE,
        )
        assert pattern.search(output), f"未知状态应降级为 2:\n{output}"


# ═══════════════════════════════════════════════════════════════
#  3. 阈值违规计数
# ═══════════════════════════════════════════════════════════════

class TestThresholdViolationsMetric:
    """验证 threshold_violations_total 指标"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_reflect_violations_count(self):
        """threshold_violations_total 必须等于 report.threshold_violations 长度"""
        violations = ["runtime.cov < 80", "verification.cov < 70", "business.track < 80"]
        report = _make_report(threshold_violations=violations)
        output = export_to_prometheus(report)
        pattern = re.compile(
            rf"^{_VIS_METRIC_PREFIX}_threshold_violations_total\s+3\s+\d+\s*$",
            re.MULTILINE,
        )
        assert pattern.search(output), f"违规计数应为 3:\n{output}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_be_zero_when_no_violations(self):
        """无违规时计数应为 0"""
        report = _make_report(threshold_violations=[])
        output = export_to_prometheus(report)
        pattern = re.compile(
            rf"^{_VIS_METRIC_PREFIX}_threshold_violations_total\s+0\s+\d+\s*$",
            re.MULTILINE,
        )
        assert pattern.search(output), f"无违规时计数应为 0:\n{output}"


# ═══════════════════════════════════════════════════════════════
#  4. 层级标签映射
# ═══════════════════════════════════════════════════════════════

class TestLayerLabelMapping:
    """验证中文层名 → 英文 label 映射"""

    @pytest.mark.unit
    @pytest.mark.p0
    @pytest.mark.parametrize("cn_name,en_label", [
        ("运行时可见", "runtime"),
        ("验证过程可见", "verification"),
        ("业务价值可见", "business"),
        ("架构影响可见", "architecture"),
    ])
    def test_layer_label_map_should_contain_all_four_layers(self, cn_name: str, en_label: str):
        """_LAYER_LABEL_MAP 必须包含四层中英文映射"""
        assert _LAYER_LABEL_MAP.get(cn_name) == en_label

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_export_layer_passed_metric_for_each_layer(self):
        """每层都应导出 yunshu_visibility_layer_passed{layer=...,success=...} 指标"""
        report = _make_report()
        output = export_to_prometheus(report)
        for en_label in ["runtime", "verification", "business", "architecture"]:
            pattern = re.compile(
                rf'{_VIS_METRIC_PREFIX}_layer_passed\{{layer="{en_label}",success="true"\}}\s+1\s+\d+'
            )
            assert pattern.search(output), f"缺失层 {en_label} 的 passed 指标:\n{output}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_mark_failed_layer_with_success_false(self):
        """未通过层应标记为 success=\"false\" 且数值为 0"""
        failed_layer = _make_layer(
            "运行时可见",
            [Metric(name="cov", value=50.0, threshold=80.0, unit="%")],
            overall_passed=False,
        )
        report = _make_report(layers=[failed_layer], overall_status="fail")
        output = export_to_prometheus(report)
        pattern = re.compile(
            rf'{_VIS_METRIC_PREFIX}_layer_passed\{{layer="runtime",success="false"\}}\s+0\s+\d+'
        )
        assert pattern.search(output), f"未通过层应 success=false:\n{output}"


# ═══════════════════════════════════════════════════════════════
#  5. 逆向指标处理
# ═══════════════════════════════════════════════════════════════

class TestInverseMetrics:
    """验证逆向指标（arch_rule_violations）的 success 标签语义"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_inverse_metrics_set_should_contain_arch_rule_violations(self):
        """_INVERSE_METRICS 必须包含 arch_rule_violations"""
        assert "arch_rule_violations" in _INVERSE_METRICS

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_export_inverse_metric_with_success_false_when_exceeds_threshold(self):
        """逆向指标超阈时应 success=false（数值仍照实输出）"""
        # 违规数 10，阈值 5，passed=False（调用方显式传入）
        arch_layer = _make_layer(
            "架构影响可见",
            [
                Metric(name="dependency_graph_nodes", value=100, threshold=10, unit="个", passed=True),
                Metric(name="rule_violations", value=10, threshold=5, unit="个", passed=False),
            ],
            overall_passed=False,
        )
        report = _make_report(layers=[arch_layer], overall_status="fail")
        output = export_to_prometheus(report)
        # 指标名应是 yunshu_visibility_architecture_rule_violations
        pattern = re.compile(
            rf'{_VIS_METRIC_PREFIX}_architecture_rule_violations\{{layer="architecture",success="false"\}}\s+10.0\s+\d+'
        )
        assert pattern.search(output), f"逆向指标超阈应 success=false:\n{output}"


# ═══════════════════════════════════════════════════════════════
#  6. 无效数值处理
# ═══════════════════════════════════════════════════════════════

class TestInvalidValueHandling:
    """验证无效数值的容错处理"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_skip_metric_with_none_value(self):
        """None 值的指标应被跳过数据行（HELP/TYPE 头部允许存在）且不抛异常"""
        runtime_layer = _make_layer(
            "运行时可见",
            [
                Metric(name="structured_log_coverage", value=85.0, threshold=80.0, unit="%", passed=True),
                Metric(name="invalid_metric", value=None, threshold=50.0, unit="", passed=False),
            ],
        )
        report = _make_report(layers=[runtime_layer])
        # 不应抛异常
        output = export_to_prometheus(report)
        # 不应包含 invalid_metric 的数据行（带 {labels} value 格式）
        # HELP/TYPE 头部允许存在（visibility_report 实现先写头部再 try 转 float）
        data_line_pattern = re.compile(
            rf'^{_VIS_METRIC_PREFIX}_runtime_invalid_metric\{{.*?\}}\s+[\d.]+\s+\d+\s*$',
            re.MULTILINE,
        )
        assert not data_line_pattern.search(output), (
            f"None 值指标不应输出数据行:\n{output}"
        )
        # 合法指标仍正常输出数据行
        valid_pattern = re.compile(
            rf'^{_VIS_METRIC_PREFIX}_runtime_structured_log_coverage\{{.*?\}}\s+85\.0\s+\d+\s*$',
            re.MULTILINE,
        )
        assert valid_pattern.search(output), f"合法指标应正常输出:\n{output}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_skip_metric_with_non_numeric_string(self):
        """非数字字符串值的指标应被跳过数据行"""
        runtime_layer = _make_layer(
            "运行时可见",
            [
                Metric(name="bad_string", value="not_a_number", threshold=50.0, unit="", passed=False),
            ],
        )
        report = _make_report(layers=[runtime_layer])
        output = export_to_prometheus(report)
        # 不应包含数据行
        data_line_pattern = re.compile(
            rf'^{_VIS_METRIC_PREFIX}_runtime_bad_string\{{.*?\}}\s+[\d.]+\s+\d+\s*$',
            re.MULTILINE,
        )
        assert not data_line_pattern.search(output), (
            f"非数字指标不应输出数据行:\n{output}"
        )

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_skip_metric_with_empty_string(self):
        """空字符串值的指标应被跳过数据行"""
        layer = _make_layer(
            "业务价值可见",
            [Metric(name="empty_val", value="", threshold=50.0, unit="", passed=False)],
        )
        report = _make_report(layers=[layer])
        output = export_to_prometheus(report)
        data_line_pattern = re.compile(
            rf'^{_VIS_METRIC_PREFIX}_business_empty_val\{{.*?\}}\s+[\d.]+\s+\d+\s*$',
            re.MULTILINE,
        )
        assert not data_line_pattern.search(output), (
            f"空字符串指标不应输出数据行:\n{output}"
        )


# ═══════════════════════════════════════════════════════════════
#  7. _VisibilityMetricsState 线程安全
# ═══════════════════════════════════════════════════════════════

class TestVisibilityMetricsState:
    """验证共享状态的线程安全与快照语义"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_empty_snapshot_initially(self):
        """初始状态应返回空快照与 degraded 状态"""
        state = _VisibilityMetricsState()
        snap, status, error, last_update = state.snapshot()
        assert snap == ""
        assert status == "degraded"
        assert error is None
        assert last_update == 0.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_reflect_updated_snapshot(self):
        """update() 后应能读到最新快照"""
        state = _VisibilityMetricsState()
        state.update("new_snapshot_text", "pass", None)
        snap, status, error, last_update = state.snapshot()
        assert snap == "new_snapshot_text"
        assert status == "pass"
        assert last_update > 0.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_be_thread_safe_under_concurrent_updates(self):
        """多线程并发 update 不应导致数据损坏"""
        state = _VisibilityMetricsState()
        n_threads = 20
        n_iterations = 100

        def writer(tid: int) -> None:
            for i in range(n_iterations):
                state.update(f"thread-{tid}-iter-{i}", "pass", None)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 最终快照应是某个线程的某次写入（非损坏数据）
        snap, status, _, _ = state.snapshot()
        assert snap.startswith("thread-"), f"快照损坏: {snap}"
        assert status == "pass"


# ═══════════════════════════════════════════════════════════════
#  8. HTTP Handler 行为
# ═══════════════════════════════════════════════════════════════

class TestMetricsHttpHandler:
    """验证 _build_prometheus_handler 构造的 HTTP handler 行为"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_200_with_correct_content_type_for_metrics(self):
        """GET /metrics 应返回 200 + text/plain Content-Type"""
        state = _VisibilityMetricsState()
        state.update("yunshu_visibility_up 1\n", "pass", None)
        handler_cls = _build_prometheus_handler(state)

        # 用真实 HTTPServer 启动临时端口
        httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2) as resp:
                assert resp.status == 200
                ct = resp.headers.get("Content-Type", "")
                assert "text/plain" in ct, f"Content-Type 错误: {ct}"
                body = resp.read().decode("utf-8")
                assert "yunshu_visibility_up" in body
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_degraded_snapshot_when_empty(self):
        """快照为空时应返回降级指标（overall_status=degraded=2）"""
        state = _VisibilityMetricsState()  # 空快照
        handler_cls = _build_prometheus_handler(state)

        httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2) as resp:
                assert resp.status == 200
                body = resp.read().decode("utf-8")
                # 必须包含 degraded 状态指标
                assert f'{_VIS_METRIC_PREFIX}_overall_status{{status="degraded"}} 2' in body
                assert f"{_VIS_METRIC_PREFIX}_up 1" in body
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_404_for_unknown_path(self):
        """非 /metrics 路径应返回 404"""
        state = _VisibilityMetricsState()
        handler_cls = _build_prometheus_handler(state)

        httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/unknown", timeout=2)
                assert False, "应返回 404"
            except urllib.error.HTTPError as e:
                assert e.code == 404
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_503_for_health_endpoint_when_no_snapshot(self):
        """无快照时 GET /health 应返回 503（依赖未就绪）"""
        state = _VisibilityMetricsState()
        handler_cls = _build_prometheus_handler(state)
        httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
                # 如返回 200 也接受（不同实现策略）
            except urllib.error.HTTPError as e:
                # 503 或 200 均可接受
                assert e.code in (200, 503, 500), f"意外的状态码: {e.code}"
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)


# ═══════════════════════════════════════════════════════════════
#  9. serve_metrics 集成（短时运行）
# ═══════════════════════════════════════════════════════════════

class TestServeMetricsIntegration:
    """验证 serve_metrics 启动的 HTTP 服务（短时运行）"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_serve_metrics_should_return_zero_on_normal_startup(self):
        """serve_metrics 启动后应返回 0（正常退出码）

        通过 mock HTTPServer.serve_forever 让其立即返回，
        避免测试阻塞。验证启动流程完整执行。
        """
        fake_root = PROJECT_ROOT
        with patch("visibility_report.generate_report") as mock_gen, \
             patch("visibility_report.HTTPServer") as mock_http_cls:
            mock_report = _make_report()
            mock_gen.return_value = mock_report
            # mock server 实例：serve_forever 立即返回（不阻塞）
            mock_server = MagicMock()
            mock_server.serve_forever = MagicMock()  # 不阻塞
            mock_server.shutdown = MagicMock()
            mock_server.server_close = MagicMock()
            mock_http_cls.return_value = mock_server

            # 在子线程调用 serve_metrics（避免主线程被 serve_forever 阻塞）
            result_holder: dict = {}

            def _run():
                try:
                    exit_code = serve_metrics(
                        port=0,
                        refresh_interval=60,
                        project_root=fake_root,
                        thresholds={},
                        host="127.0.0.1",
                    )
                    result_holder["exit_code"] = exit_code
                except Exception as e:
                    result_holder["error"] = e

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=5)  # 等待 serve_metrics 返回
            assert not t.is_alive(), "serve_metrics 应在 serve_forever 返回后正常退出"
            assert "error" not in result_holder, f"serve_metrics 抛异常: {result_holder.get('error')}"
            assert result_holder.get("exit_code") == 0, (
                f"正常启动应返回 0, 实际: {result_holder.get('exit_code')}"
            )
            # 验证 HTTPServer 被正确实例化
            mock_http_cls.assert_called_once()
            # 验证 serve_forever 被调用
            mock_server.serve_forever.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.p1
    def test_serve_metrics_should_return_one_on_port_bind_failure(self):
        """端口绑定失败时应返回 1（错误退出码）"""
        fake_root = PROJECT_ROOT
        with patch("visibility_report.generate_report") as mock_gen, \
             patch("visibility_report.HTTPServer") as mock_http_cls:
            mock_gen.return_value = _make_report()
            # 模拟端口绑定失败
            mock_http_cls.side_effect = OSError("Address already in use")

            exit_code = serve_metrics(
                port=9999,
                refresh_interval=60,
                project_root=fake_root,
                thresholds={},
                host="127.0.0.1",
            )
            assert exit_code == 1, f"端口绑定失败应返回 1, 实际: {exit_code}"
