# -*- coding: utf-8 -*-
"""
visibility_report.py 单元测试（D7 项）

【测试目标】
补齐 scripts/visibility_report.py 的回归保护，覆盖以下维度（不含 coverage.xml 解析，
该部分由 test_visibility_report_coverage_parsing.py 专门覆盖）：
1. 阈值计算逻辑：Metric 达标 / 未达标 / 逆向指标覆盖
2. 看板计数：_count_dashboards 扫描 monitoring/grafana_dashboards/ 与 legacy 目录
3. 告警规则计数：_count_alert_rules 解析 alerts.yml
4. 健康检查端点计数：_count_health_endpoints 扫描 agent/
5. 契约测试计数：_count_contract_tests 扫描 tests/contract/contracts/
6. 依赖图 / 架构违规读取：_read_dependency_graph / _read_arch_violations
7. 配置加载：load_thresholds 从 config.yaml 读取四层阈值
8. 报告生成：generate_report 端到端 + Markdown / JSON 格式校验
9. 退出码逻辑：main() 全达标→0 / 有违规→1 / 异常→2
10. 降级报告：_generate_degraded_report 写入含错误信息的 Markdown

【可观测性约束】
- 边界显性化：测试命名反映业务意图
- 异常处理：所有 mock 隔离文件系统与子进程，避免污染真实仓库

【生成日志摘要】
- 生成时间：2026-06-26
- 版本：v1.0.0
- 内容：visibility_report.py 单元测试，覆盖率目标≥80%
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
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
    MetricCollector,
    ReportGenerator,
    generate_report,
    load_thresholds,
    main,
    _generate_degraded_report,
)


# ═══════════════════════════════════════════════════════════════
#  1. 阈值计算逻辑（Metric / LayerReport）
# ═══════════════════════════════════════════════════════════════

class TestThresholdCalculation:
    """阈值计算：Metric 达标 / 未达标 / 逆向指标覆盖判定"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_pass_when_value_above_threshold(self):
        """值高于阈值时应判定为通过"""
        m = Metric(name="cov", value=85.0, threshold=80.0, unit="%")
        assert m.passed is True
        assert m.status_icon == "✅"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_fail_when_value_below_threshold(self):
        """值低于阈值时应判定为未通过"""
        m = Metric(name="cov", value=70.0, threshold=80.0, unit="%")
        assert m.passed is False
        assert m.status_icon == "❌"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_pass_when_value_equals_threshold(self):
        """值等于阈值时应判定为通过（>=）"""
        m = Metric(name="cov", value=80.0, threshold=80.0, unit="%")
        assert m.passed is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_respect_explicit_passed_override_for_inverse_metric(self):
        """逆向指标（如违规数）应尊重显式传入的 passed，不被自动判定覆盖"""
        # 架构违规数：实际=3，期望=0，但 max_allowed=5 → 通过
        m = Metric(
            name="arch_violations",
            value=3,
            threshold=0,
            unit="个",
            passed=3 <= 5,  # 显式判定：实际≤max_allowed
        )
        assert m.passed is True
        # __post_init__ 不应覆盖显式传入的 passed
        assert m.passed is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_layer_report_overall_passed_updates_on_failure(self):
        """LayerReport 在添加未通过指标后 overall_passed 应变为 False"""
        layer = LayerReport(layer_name="测试层", description="测试")
        assert layer.overall_passed is True
        layer.add_metric(Metric(name="a", value=50, threshold=80))
        assert layer.overall_passed is False

    @pytest.mark.unit
    @pytest.mark.p1
    def test_layer_report_stays_passed_when_all_metrics_pass(self):
        """LayerReport 在所有指标通过时 overall_passed 保持 True"""
        layer = LayerReport(layer_name="测试层", description="测试")
        layer.add_metric(Metric(name="a", value=90, threshold=80))
        layer.add_metric(Metric(name="b", value=100, threshold=90))
        assert layer.overall_passed is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_metric_status_icon_unknown_when_passed_is_none(self):
        """passed 为 None 时 status_icon 应为未知符号"""
        m = Metric(name="x", value=1, threshold=None)
        # threshold=None 时 __post_init__ 不设置 passed
        assert m.passed is None
        assert m.status_icon == "➖"


# ═══════════════════════════════════════════════════════════════
#  2. 看板计数（_count_dashboards）
# ═══════════════════════════════════════════════════════════════

class TestDashboardCounting:
    """看板计数：扫描 monitoring/grafana_dashboards/ 与 legacy 目录"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_count_json_files_in_primary_dashboard_dir(self, tmp_path):
        """主目录 monitoring/grafana_dashboards/ 下的 JSON 应被计数"""
        dash_dir = tmp_path / "monitoring" / "grafana_dashboards"
        dash_dir.mkdir(parents=True)
        (dash_dir / "yunshu_chat_dashboard.json").write_text("{}", encoding="utf-8")
        (dash_dir / "yunshu_memory_dashboard.json").write_text("{}", encoding="utf-8")
        collector = MetricCollector(tmp_path, {})
        assert collector._count_dashboards() == 2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_count_json_in_both_primary_and_legacy_dirs(self, tmp_path):
        """主目录与 legacy 目录的 JSON 应合并计数"""
        primary = tmp_path / "monitoring" / "grafana_dashboards"
        legacy = tmp_path / "monitoring" / "grafana" / "dashboards"
        primary.mkdir(parents=True)
        legacy.mkdir(parents=True)
        (primary / "a.json").write_text("{}", encoding="utf-8")
        (legacy / "b.json").write_text("{}", encoding="utf-8")
        (legacy / "c.json").write_text("{}", encoding="utf-8")
        collector = MetricCollector(tmp_path, {})
        assert collector._count_dashboards() == 3

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_zero_when_no_dashboard_dirs_exist(self, tmp_path):
        """无看板目录时应返回 0"""
        collector = MetricCollector(tmp_path, {})
        assert collector._count_dashboards() == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_not_count_non_json_files(self, tmp_path):
        """非 JSON 文件不应被计数"""
        dash_dir = tmp_path / "monitoring" / "grafana_dashboards"
        dash_dir.mkdir(parents=True)
        (dash_dir / "dashboard.yml").write_text("key: val", encoding="utf-8")
        (dash_dir / "readme.md").write_text("# dashboards", encoding="utf-8")
        (dash_dir / "real.json").write_text("{}", encoding="utf-8")
        collector = MetricCollector(tmp_path, {})
        assert collector._count_dashboards() == 1


# ═══════════════════════════════════════════════════════════════
#  3. 告警规则计数（_count_alert_rules）
# ═══════════════════════════════════════════════════════════════

class TestAlertRulesCounting:
    """告警规则计数：解析 alerts.yml 中的 `- alert:` 条目"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_count_alert_entries_in_yaml(self, tmp_path):
        """应正确统计 alerts.yml 中 - alert: 条目数"""
        alerts_file = tmp_path / "monitoring" / "alerts.yml"
        alerts_file.parent.mkdir(parents=True)
        alerts_file.write_text(
            "groups:\n"
            "  - name: yunshu\n"
            "    rules:\n"
            "      - alert: HighErrorRate\n"
            "        expr: up == 0\n"
            "      - alert: LowCoverage\n"
            "        expr: cov < 10\n"
            "      - alert: TooManyViolations\n"
            "        expr: v > 5\n",
            encoding="utf-8",
        )
        collector = MetricCollector(tmp_path, {})
        assert collector._count_alert_rules() == 3

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_zero_when_alerts_file_missing(self, tmp_path):
        """alerts.yml 不存在时应返回 0"""
        collector = MetricCollector(tmp_path, {})
        assert collector._count_alert_rules() == 0


# ═══════════════════════════════════════════════════════════════
#  4. 健康检查端点计数（_count_health_endpoints）
# ═══════════════════════════════════════════════════════════════

class TestHealthEndpointCounting:
    """健康检查端点计数：扫描 agent/ 下 /health、/status 路由"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_count_health_and_status_endpoints(self, tmp_path):
        """应统计 /api/health、/health、/api/status、/status 路由"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "routes_health.py").write_text(
            '@app.route("/api/health")\n'
            'def health(): pass\n',
            encoding="utf-8",
        )
        (agent_dir / "routes_status.py").write_text(
            '@app.route("/status")\n'
            'def status(): pass\n',
            encoding="utf-8",
        )
        collector = MetricCollector(tmp_path, {})
        assert collector._count_health_endpoints() == 2

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_zero_when_agent_dir_missing(self, tmp_path):
        """agent/ 目录不存在时应返回 0"""
        collector = MetricCollector(tmp_path, {})
        assert collector._count_health_endpoints() == 0


# ═══════════════════════════════════════════════════════════════
#  5. 契约测试计数（_count_contract_tests）
# ═══════════════════════════════════════════════════════════════

class TestContractTestCounting:
    """契约测试计数：扫描 tests/contract/contracts/ 下 *_contract.json"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_count_contract_json_files(self, tmp_path):
        """应统计 *_contract.json 文件数"""
        contracts_dir = tmp_path / "tests" / "contract" / "contracts"
        contracts_dir.mkdir(parents=True)
        (contracts_dir / "chat_contract.json").write_text("{}", encoding="utf-8")
        (contracts_dir / "memory_contract.json").write_text("{}", encoding="utf-8")
        (contracts_dir / "tool_contract.json").write_text("{}", encoding="utf-8")
        collector = MetricCollector(tmp_path, {})
        assert collector._count_contract_tests() == 3

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_zero_when_contract_dir_missing(self, tmp_path):
        """契约测试目录不存在时应返回 0"""
        collector = MetricCollector(tmp_path, {})
        assert collector._count_contract_tests() == 0


# ═══════════════════════════════════════════════════════════════
#  6. 依赖图 / 架构违规读取
# ═══════════════════════════════════════════════════════════════

class TestDependencyGraphReading:
    """依赖图节点 / 边数读取"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_read_nodes_and_edges_from_json(self, tmp_path):
        """应从 dependency_graph.json 读取节点与边数"""
        graph_file = tmp_path / "docs" / "architecture" / "dependency_graph.json"
        graph_file.parent.mkdir(parents=True)
        graph_file.write_text(json.dumps({
            "nodes": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
            "edges": [{"from": "a", "to": "b"}],
        }), encoding="utf-8")
        collector = MetricCollector(tmp_path, {})
        nodes, edges = collector._read_dependency_graph()
        assert nodes == 3
        assert edges == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_zero_when_graph_file_missing(self, tmp_path):
        """依赖图文件不存在时应返回 (0, 0)"""
        collector = MetricCollector(tmp_path, {})
        assert collector._read_dependency_graph() == (0, 0)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_zero_when_graph_json_malformed(self, tmp_path):
        """依赖图 JSON 损坏时应返回 (0, 0)"""
        graph_file = tmp_path / "docs" / "architecture" / "dependency_graph.json"
        graph_file.parent.mkdir(parents=True)
        graph_file.write_text("not valid json {{{", encoding="utf-8")
        collector = MetricCollector(tmp_path, {})
        assert collector._read_dependency_graph() == (0, 0)


class TestArchViolationsReading:
    """架构规则违规数读取"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_read_violations_count(self, tmp_path):
        """应从 arch_rules_report.json 读取违规数"""
        report_file = tmp_path / "docs" / "architecture" / "arch_rules_report.json"
        report_file.parent.mkdir(parents=True)
        report_file.write_text(json.dumps({"violations_count": 7}), encoding="utf-8")
        collector = MetricCollector(tmp_path, {})
        assert collector._read_arch_violations() == 7

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_fallback_to_total_violations_key(self, tmp_path):
        """无 violations_count 时应回退到 total_violations"""
        report_file = tmp_path / "docs" / "architecture" / "arch_rules_report.json"
        report_file.parent.mkdir(parents=True)
        report_file.write_text(json.dumps({"total_violations": 4}), encoding="utf-8")
        collector = MetricCollector(tmp_path, {})
        assert collector._read_arch_violations() == 4

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_zero_when_report_missing(self, tmp_path):
        """架构报告不存在时应返回 0"""
        collector = MetricCollector(tmp_path, {})
        assert collector._read_arch_violations() == 0


# ═══════════════════════════════════════════════════════════════
#  7. 配置加载（load_thresholds）
# ═══════════════════════════════════════════════════════════════

class TestLoadThresholds:
    """从 config.yaml 读取四层可见性阈值"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_load_four_layer_thresholds_from_yaml(self, tmp_path):
        """应正确从 config.yaml 读取四层阈值配置"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "visibility_thresholds:\n"
            "  runtime:\n"
            "    structured_log_coverage: 30\n"
            "    trace_coverage: 30\n"
            "    health_endpoints: 1\n"
            "  verification:\n"
            "    test_coverage: 40\n"
            "    boundary_test_coverage: 5\n"
            "    contract_test_count: 3\n"
            "  business:\n"
            "    track_event_coverage: 30\n"
            "    dashboard_count: 3\n"
            "    alert_rules_count: 5\n"
            "  architecture:\n"
            "    dependency_graph_nodes: 10\n"
            "    max_arch_violations: 5\n"
            "    impact_analysis_coverage: 80\n",
            encoding="utf-8",
        )
        thresholds = load_thresholds(config_file)
        assert thresholds["runtime"]["structured_log_coverage"] == 30
        assert thresholds["verification"]["test_coverage"] == 40
        assert thresholds["business"]["dashboard_count"] == 3
        assert thresholds["architecture"]["max_arch_violations"] == 5

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_empty_dict_when_config_missing(self, tmp_path):
        """配置文件不存在时应返回空字典（使用默认阈值）"""
        thresholds = load_thresholds(tmp_path / "nonexistent.yaml")
        assert thresholds == {}

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_empty_dict_when_no_visibility_section(self, tmp_path):
        """配置文件无 visibility_thresholds 段时应返回空字典"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("other:\n  key: value\n", encoding="utf-8")
        thresholds = load_thresholds(config_file)
        assert thresholds == {}


# ═══════════════════════════════════════════════════════════════
#  8. 报告生成（generate_report + Markdown / JSON 格式）
# ═══════════════════════════════════════════════════════════════

class TestReportGeneration:
    """报告生成端到端：阈值违规收集 + 总体状态判定"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_pass_status_when_all_metrics_above_threshold(self, tmp_path):
        """全部指标达标时 overall_status 应为 pass"""
        # generate_report 内部新建 MetricCollector 实例，需在类级别 patch
        passing_layer = LayerReport(layer_name="测试层", description="测试")
        passing_layer.add_metric(Metric(name="a", value=90, threshold=80))
        with patch.object(MetricCollector, "collect_all", return_value=[passing_layer]):
            report = generate_report(tmp_path, {})
        assert report.overall_status == "pass"
        assert report.threshold_violations == []

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_fail_status_and_collect_violations_when_below_threshold(self, tmp_path):
        """有指标未达标时 overall_status 应为 fail 且收集违规清单"""
        failing_layer = LayerReport(layer_name="运行时可见", description="测试")
        failing_layer.add_metric(Metric(name="structured_log_coverage", value=20, threshold=30, unit="%"))
        with patch.object(MetricCollector, "collect_all", return_value=[failing_layer]):
            report = generate_report(tmp_path, {})
        assert report.overall_status == "fail"
        assert len(report.threshold_violations) == 1
        assert "structured_log_coverage" in report.threshold_violations[0]
        assert "20%" in report.threshold_violations[0]
        assert "30%" in report.threshold_violations[0]


class TestMarkdownReportFormat:
    """Markdown 报告格式校验：12 子项表格、达标状态、改进建议"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_markdown_should_contain_four_layer_overview_table(self, tmp_path):
        """Markdown 应包含四层概览表"""
        layer = LayerReport(layer_name="运行时可见", description="测试层")
        layer.add_metric(Metric(name="m1", value=90, threshold=80, unit="%"))
        report = VisibilityReport(
            trace_id="abc123",
            timestamp="2026-06-26T00:00:00",
            duration_ms=10.0,
            layers=[layer],
            overall_status="pass",
            threshold_violations=[],
        )
        gen = ReportGenerator(tmp_path)
        md = gen.generate_markdown(report)
        assert "# 四层可见性覆盖报告" in md
        assert "## 四层概览" in md
        assert "运行时可见" in md
        assert "✅ 通过" in md

    @pytest.mark.unit
    @pytest.mark.p0
    def test_markdown_should_contain_metric_table_with_threshold_and_status(self, tmp_path):
        """Markdown 应包含指标表格（数值 / 阈值 / 状态 / 说明）"""
        layer = LayerReport(layer_name="业务价值可见", description="测试")
        layer.add_metric(Metric(
            name="dashboard_count", value=3, threshold=3, unit="个",
            description="监控看板数量",
        ))
        report = VisibilityReport(
            trace_id="t1", timestamp="2026-06-26", duration_ms=5.0,
            layers=[layer], overall_status="pass", threshold_violations=[],
        )
        gen = ReportGenerator(tmp_path)
        md = gen.generate_markdown(report)
        assert "dashboard_count" in md
        assert "3个" in md
        assert "≥ 3" in md
        assert "✅" in md
        assert "监控看板数量" in md

    @pytest.mark.unit
    @pytest.mark.p1
    def test_markdown_should_list_violations_when_status_is_fail(self, tmp_path):
        """状态为 fail 时 Markdown 应包含阈值违规清单与 CI 阻断提示"""
        layer = LayerReport(layer_name="运行时可见", description="测试")
        layer.add_metric(Metric(name="cov", value=10, threshold=80, unit="%"))
        report = VisibilityReport(
            trace_id="t2", timestamp="2026-06-26", duration_ms=5.0,
            layers=[layer], overall_status="fail",
            threshold_violations=["运行时可见.cov: 实际=10%, 阈值=80%"],
        )
        gen = ReportGenerator(tmp_path)
        md = gen.generate_markdown(report)
        assert "## 阈值违规清单" in md
        assert "运行时可见.cov" in md
        assert "CI 阻断" in md

    @pytest.mark.unit
    @pytest.mark.p1
    def test_markdown_should_show_all_passed_when_no_violations(self, tmp_path):
        """无违规时 Markdown 应显示全部达标"""
        report = VisibilityReport(
            trace_id="t3", timestamp="2026-06-26", duration_ms=5.0,
            layers=[], overall_status="pass", threshold_violations=[],
        )
        gen = ReportGenerator(tmp_path)
        md = gen.generate_markdown(report)
        assert "所有指标均达到阈值要求" in md


class TestJsonReportFormat:
    """JSON 报告格式校验"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_json_should_contain_required_top_level_fields(self, tmp_path):
        """JSON 报告应包含 trace_id / timestamp / overall_status / layers"""
        layer = LayerReport(layer_name="测试", description="d")
        layer.add_metric(Metric(name="m", value=1, threshold=1))
        report = VisibilityReport(
            trace_id="json1", timestamp="2026-06-26", duration_ms=1.0,
            layers=[layer], overall_status="pass", threshold_violations=[],
        )
        gen = ReportGenerator(tmp_path)
        data = gen.generate_json(report)
        assert data["trace_id"] == "json1"
        assert data["overall_status"] == "pass"
        assert len(data["layers"]) == 1
        assert data["layers"][0]["metrics"][0]["name"] == "m"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_json_should_include_threshold_and_passed_in_metrics(self, tmp_path):
        """JSON 报告每个 metric 应包含 threshold / passed 字段"""
        layer = LayerReport(layer_name="测试", description="d")
        layer.add_metric(Metric(name="m", value=50, threshold=80))
        report = VisibilityReport(
            trace_id="json2", timestamp="2026-06-26", duration_ms=1.0,
            layers=[layer], overall_status="fail",
            threshold_violations=["测试.m: 实际=50, 阈值=80"],
        )
        gen = ReportGenerator(tmp_path)
        data = gen.generate_json(report)
        metric = data["layers"][0]["metrics"][0]
        assert metric["threshold"] == 80
        assert metric["passed"] is False


# ═══════════════════════════════════════════════════════════════
#  9. 退出码逻辑（main）
# ═══════════════════════════════════════════════════════════════

class TestMainExitCode:
    """main() 退出码：0 通过 / 1 阈值未达标 / 2 异常"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_exit_zero_when_all_thresholds_passed(self, tmp_path):
        """全部达标时应退出 0"""
        passing_layer = LayerReport(layer_name="L", description="d")
        passing_layer.add_metric(Metric(name="m", value=100, threshold=80))
        passing_report = VisibilityReport(
            trace_id="t", timestamp="2026-06-26", duration_ms=1.0,
            layers=[passing_layer], overall_status="pass",
            threshold_violations=[],
        )
        config_file = tmp_path / "config.yaml"
        config_file.write_text("visibility_thresholds: {}\n", encoding="utf-8")
        output_file = tmp_path / "report.md"
        with patch("visibility_report.PROJECT_ROOT", tmp_path), \
             patch("visibility_report.generate_report", return_value=passing_report):
            exit_code = main(["--config", str(config_file), "--output", str(output_file)])
        assert exit_code == 0
        assert output_file.exists()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_exit_one_when_threshold_violations_exist(self, tmp_path):
        """有阈值违规时应退出 1"""
        failing_layer = LayerReport(layer_name="L", description="d")
        failing_layer.add_metric(Metric(name="m", value=10, threshold=80))
        failing_report = VisibilityReport(
            trace_id="t", timestamp="2026-06-26", duration_ms=1.0,
            layers=[failing_layer], overall_status="fail",
            threshold_violations=["L.m: 实际=10, 阈值=80"],
        )
        config_file = tmp_path / "config.yaml"
        config_file.write_text("visibility_thresholds: {}\n", encoding="utf-8")
        output_file = tmp_path / "report.md"
        with patch("visibility_report.PROJECT_ROOT", tmp_path), \
             patch("visibility_report.generate_report", return_value=failing_report):
            exit_code = main(["--config", str(config_file), "--output", str(output_file)])
        assert exit_code == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_exit_two_when_exception_raised(self, tmp_path):
        """报告生成异常时应退出 2 并输出降级报告"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("visibility_thresholds: {}\n", encoding="utf-8")
        output_file = tmp_path / "degraded.md"
        with patch("visibility_report.PROJECT_ROOT", tmp_path), \
             patch("visibility_report.generate_report", side_effect=RuntimeError("boom")):
            exit_code = main(["--config", str(config_file), "--output", str(output_file)])
        assert exit_code == 2
        # 降级报告应被写入
        assert output_file.exists()
        content = output_file.read_text(encoding="utf-8")
        assert "降级" in content
        assert "RuntimeError" in content

    @pytest.mark.unit
    @pytest.mark.p1
    def test_json_only_mode_should_output_json_and_return_exit_code(self, tmp_path):
        """--json-only 模式应输出 JSON 到 stdout 并返回退出码"""
        passing_report = VisibilityReport(
            trace_id="t", timestamp="2026-06-26", duration_ms=1.0,
            layers=[], overall_status="pass", threshold_violations=[],
        )
        config_file = tmp_path / "config.yaml"
        config_file.write_text("visibility_thresholds: {}\n", encoding="utf-8")
        with patch("visibility_report.PROJECT_ROOT", tmp_path), \
             patch("visibility_report.generate_report", return_value=passing_report), \
             patch("builtins.print") as mock_print:
            exit_code = main(["--config", str(config_file), "--json-only"])
        assert exit_code == 0
        # 应打印 JSON
        mock_print.assert_called()


# ═══════════════════════════════════════════════════════════════
#  10. 降级报告生成（_generate_degraded_report）
# ═══════════════════════════════════════════════════════════════

class TestDegradedReport:
    """降级报告：模拟指标采集失败，验证输出含错误信息"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_write_degraded_report_with_error_info(self, tmp_path):
        """降级报告应包含错误类型、错误信息与堆栈"""
        output = tmp_path / "degraded" / "report.md"
        try:
            raise ValueError("模拟采集失败")
        except ValueError as e:
            _generate_degraded_report(e, output)
        assert output.exists()
        content = output.read_text(encoding="utf-8")
        assert "降级" in content
        assert "ValueError" in content
        assert "模拟采集失败" in content
        assert "错误堆栈" in content
        assert "处置建议" in content

    @pytest.mark.unit
    @pytest.mark.p1
    def test_degraded_report_should_create_parent_dirs(self, tmp_path):
        """降级报告应自动创建父目录"""
        output = tmp_path / "deep" / "nested" / "path" / "report.md"
        _generate_degraded_report(RuntimeError("err"), output)
        assert output.exists()


# ═══════════════════════════════════════════════════════════════
#  11. 文件系统扫描类采集方法（补充覆盖率）
# ═══════════════════════════════════════════════════════════════

class TestStructuredLogCoverage:
    """结构化日志覆盖率扫描：统计含 trace_id 的 logger 调用占比"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_100_when_no_log_calls(self, tmp_path):
        """无 logger 调用时应返回 100（视为通过）"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "empty.py").write_text("# no logs here\n", encoding="utf-8")
        collector = MetricCollector(tmp_path, {})
        assert collector._calc_structured_log_coverage() == 100.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_calculate_ratio_of_structured_logs(self, tmp_path):
        """应正确计算含 trace_id 的 logger 调用占比"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "mod.py").write_text(
            'import logging\n'
            'logger = logging.getLogger(__name__)\n'
            'logger.info("plain log")\n'              # 非结构化
            'logger.info(json.dumps({"trace_id": "x"}))\n'  # 结构化
            'logger.error("another plain")\n',         # 非结构化
            encoding="utf-8",
        )
        collector = MetricCollector(tmp_path, {})
        result = collector._calc_structured_log_coverage()
        # 3 条 log，1 条含 trace_id/json.dumps → 33.3%
        assert result == 33.3

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_zero_when_agent_dir_missing(self, tmp_path):
        """agent/ 不存在时应返回 0"""
        collector = MetricCollector(tmp_path, {})
        assert collector._calc_structured_log_coverage() == 0.0


class TestTraceCoverage:
    """链路追踪覆盖率：统计 @trace_route 装饰器占比"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_calculate_traced_routes_ratio(self, tmp_path):
        """应正确计算 @trace_route 占 @app.route 的比例"""
        routes_dir = tmp_path / "agent" / "server_routes"
        routes_dir.mkdir(parents=True)
        (routes_dir / "routes_chat.py").write_text(
            '@app.route("/api/chat")\n'
            '@trace_route\n'
            'def chat(): pass\n'
            '@app.route("/api/chat/list")\n'
            'def list(): pass\n',
            encoding="utf-8",
        )
        collector = MetricCollector(tmp_path, {})
        # 2 routes, 1 traced → 50.0
        assert collector._calc_trace_coverage() == 50.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_100_when_no_routes(self, tmp_path):
        """无路由时应返回 100"""
        routes_dir = tmp_path / "agent" / "server_routes"
        routes_dir.mkdir(parents=True)
        (routes_dir / "empty.py").write_text("# no routes\n", encoding="utf-8")
        collector = MetricCollector(tmp_path, {})
        assert collector._calc_trace_coverage() == 100.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_zero_when_routes_dir_missing(self, tmp_path):
        """server_routes/ 不存在时应返回 0"""
        collector = MetricCollector(tmp_path, {})
        assert collector._calc_trace_coverage() == 0.0


class TestTrackEventCoverage:
    """埋点覆盖率：统计含 trackEvent/BusinessMetricsCollector 的模块占比"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_calculate_tracked_modules_ratio(self, tmp_path):
        """应正确计算含埋点的模块占比"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        # 模块 A 有埋点
        mod_a = agent_dir / "module_a"
        mod_a.mkdir()
        (mod_a / "a.py").write_text(
            'BusinessMetricsCollector.track("event")\n', encoding="utf-8")
        # 模块 B 无埋点
        mod_b = agent_dir / "module_b"
        mod_b.mkdir()
        (mod_b / "b.py").write_text('print("no tracking")\n', encoding="utf-8")
        collector = MetricCollector(tmp_path, {})
        # 2 模块，1 有埋点 → 50.0
        assert collector._calc_track_coverage() == 50.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_100_when_no_modules(self, tmp_path):
        """无子模块时应返回 100"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        collector = MetricCollector(tmp_path, {})
        assert collector._calc_track_coverage() == 100.0


class TestBoundaryCoverage:
    """边界测试覆盖率：调用 check_boundary_coverage.py 子进程"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_parse_boundary_coverage_from_subprocess(self, tmp_path):
        """应从子进程 JSON 输出解析边界覆盖率"""
        script = tmp_path / "scripts" / "check_boundary_coverage.py"
        script.parent.mkdir(parents=True)
        script.write_text("# placeholder\n", encoding="utf-8")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"total_tests": 100, "total_boundary_tests": 15})
        mock_result.stderr = ""
        collector = MetricCollector(tmp_path, {})
        with patch("visibility_report.subprocess.run", return_value=mock_result):
            result = collector._calc_boundary_coverage()
        assert result == 15.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_zero_when_script_missing(self, tmp_path):
        """边界扫描脚本不存在时应返回 0"""
        collector = MetricCollector(tmp_path, {})
        assert collector._calc_boundary_coverage() == 0.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_zero_when_subprocess_fails(self, tmp_path):
        """子进程异常时应返回 0（不抛出）"""
        script = tmp_path / "scripts" / "check_boundary_coverage.py"
        script.parent.mkdir(parents=True)
        script.write_text("# placeholder\n", encoding="utf-8")
        collector = MetricCollector(tmp_path, {})
        with patch("visibility_report.subprocess.run", side_effect=subprocess.SubprocessError("timeout")):
            assert collector._calc_boundary_coverage() == 0.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_zero_when_no_tests(self, tmp_path):
        """total_tests=0 时应返回 0"""
        script = tmp_path / "scripts" / "check_boundary_coverage.py"
        script.parent.mkdir(parents=True)
        script.write_text("# placeholder\n", encoding="utf-8")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"total_tests": 0, "total_boundary_tests": 0})
        mock_result.stderr = ""
        collector = MetricCollector(tmp_path, {})
        with patch("visibility_report.subprocess.run", return_value=mock_result):
            assert collector._calc_boundary_coverage() == 0.0


class TestExceptionCoverage:
    """异常处理覆盖率：AST 扫描 agent/ 目录下含 try/except/raise 的文件占比"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_count_files_with_try_except(self, tmp_path):
        """含 try/except 的文件应被计入异常处理覆盖"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        # 文件1：含 try/except
        (agent_dir / "mod_a.py").write_text(
            "def f():\n    try:\n        return 1\n    except Exception:\n        return 0\n",
            encoding="utf-8",
        )
        # 文件2：含 raise
        (agent_dir / "mod_b.py").write_text(
            "def g():\n    raise ValueError('err')\n",
            encoding="utf-8",
        )
        # 文件3：无异常处理
        (agent_dir / "mod_c.py").write_text(
            "def h():\n    return 42\n",
            encoding="utf-8",
        )
        collector = MetricCollector(tmp_path, {})
        result = collector._calc_exception_coverage()
        # 2/3 ≈ 66.7%
        assert result == 66.7

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_zero_when_agent_dir_missing(self, tmp_path):
        """agent/ 目录不存在时应返回 0.0"""
        collector = MetricCollector(tmp_path, {})
        assert collector._calc_exception_coverage() == 0.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_skip_dunder_files(self, tmp_path):
        """__init__.py 等 dunder 文件应被跳过，不计入分母"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "__init__.py").write_text(
            "try:\n    pass\nexcept Exception:\n    pass\n",
            encoding="utf-8",
        )
        (agent_dir / "mod.py").write_text(
            "def f():\n    try:\n        return 1\n    except Exception:\n        return 0\n",
            encoding="utf-8",
        )
        collector = MetricCollector(tmp_path, {})
        result = collector._calc_exception_coverage()
        # 只有 mod.py 被扫描，1/1 = 100%
        assert result == 100.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_skip_files_with_syntax_error(self, tmp_path):
        """AST 解析失败的文件应被跳过，不计入分母"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        # 语法错误文件
        (agent_dir / "broken.py").write_text("def (: invalid syntax\n", encoding="utf-8")
        # 正常文件含异常处理
        (agent_dir / "good.py").write_text(
            "try:\n    pass\nexcept Exception:\n    pass\n",
            encoding="utf-8",
        )
        collector = MetricCollector(tmp_path, {})
        result = collector._calc_exception_coverage()
        # broken.py 被跳过，只有 good.py，1/1 = 100%
        assert result == 100.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_zero_for_empty_agent_dir(self, tmp_path):
        """agent/ 目录下无 .py 文件时应返回 0.0"""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        collector = MetricCollector(tmp_path, {})
        assert collector._calc_exception_coverage() == 0.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_scan_subdirectories(self, tmp_path):
        """应递归扫描 agent/ 子目录下的 .py 文件"""
        agent_dir = tmp_path / "agent"
        sub_dir = agent_dir / "submodule"
        sub_dir.mkdir(parents=True)
        (agent_dir / "top.py").write_text(
            "try:\n    pass\nexcept Exception:\n    pass\n",
            encoding="utf-8",
        )
        (sub_dir / "deep.py").write_text("x = 1\n", encoding="utf-8")
        collector = MetricCollector(tmp_path, {})
        result = collector._calc_exception_coverage()
        # 1/2 = 50%
        assert result == 50.0


class TestImpactCoverage:
    """变更影响分析覆盖率"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_100_when_impact_report_exists(self, tmp_path):
        """存在 impact_report.json 时应返回 100"""
        impact_file = tmp_path / "docs" / "architecture" / "impact_report.json"
        impact_file.parent.mkdir(parents=True)
        impact_file.write_text("{}", encoding="utf-8")
        collector = MetricCollector(tmp_path, {})
        assert collector._calc_impact_coverage() == 100.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_100_when_impact_script_exists(self, tmp_path):
        """存在 impact_analysis.py 脚本时应返回 100"""
        script = tmp_path / "scripts" / "impact_analysis.py"
        script.parent.mkdir(parents=True)
        script.write_text("# script\n", encoding="utf-8")
        collector = MetricCollector(tmp_path, {})
        assert collector._calc_impact_coverage() == 100.0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_zero_when_nothing_exists(self, tmp_path):
        """无影响分析文件时应返回 0"""
        collector = MetricCollector(tmp_path, {})
        assert collector._calc_impact_coverage() == 0.0


class TestCollectAllLayers:
    """collect_all 编排方法：应返回四层报告"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_four_layers(self, tmp_path):
        """collect_all 应返回 4 个 LayerReport"""
        collector = MetricCollector(tmp_path, {})
        with patch.object(MetricCollector, "_calc_structured_log_coverage", return_value=100.0), \
             patch.object(MetricCollector, "_calc_trace_coverage", return_value=100.0), \
             patch.object(MetricCollector, "_count_health_endpoints", return_value=1), \
             patch.object(MetricCollector, "_read_test_coverage", return_value=80.0), \
             patch.object(MetricCollector, "_calc_boundary_coverage", return_value=20.0), \
             patch.object(MetricCollector, "_count_contract_tests", return_value=5), \
             patch.object(MetricCollector, "_calc_exception_coverage", return_value=75.0), \
             patch.object(MetricCollector, "_calc_track_coverage", return_value=80.0), \
             patch.object(MetricCollector, "_count_dashboards", return_value=3), \
             patch.object(MetricCollector, "_count_alert_rules", return_value=5), \
             patch.object(MetricCollector, "_read_dependency_graph", return_value=(15, 20)), \
             patch.object(MetricCollector, "_read_arch_violations", return_value=0), \
             patch.object(MetricCollector, "_calc_impact_coverage", return_value=100.0):
            layers = collector.collect_all()
        assert len(layers) == 4
        assert layers[0].layer_name == "运行时可见"
        assert layers[1].layer_name == "验证过程可见"
        assert layers[2].layer_name == "业务价值可见"
        assert layers[3].layer_name == "架构影响可见"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_business_layer_dashboard_metric_should_pass_when_count_above_threshold(self, tmp_path):
        """业务层 dashboard_count 达标时该指标应通过"""
        collector = MetricCollector(tmp_path, {
            "business": {"dashboard_count": 3, "track_event_coverage": 30, "alert_rules_count": 1}
        })
        with patch.object(MetricCollector, "_calc_track_coverage", return_value=50.0), \
             patch.object(MetricCollector, "_count_dashboards", return_value=6), \
             patch.object(MetricCollector, "_count_alert_rules", return_value=5):
            layer = collector._collect_business_layer()
        dashboard_metric = [m for m in layer.metrics if m.name == "dashboard_count"][0]
        assert dashboard_metric.passed is True
        assert dashboard_metric.value == 6
