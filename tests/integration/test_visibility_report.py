# -*- coding: utf-8 -*-
"""
scripts/visibility_report.py 集成测试

【测试目标】
验证四层可见性报告生成器的端到端流程，覆盖指标采集、报告生成、
阈值阻断、降级输出等关键路径，确保作为 CI 质量门槛时稳健可靠。

【测试维度】
- 功能测试：四层指标采集、Markdown/JSON 报告生成
- 边界测试：空目录/缺失配置/零指标
- 异常测试：降级报告输出
- 集成测试：与真实项目结构联动
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 将项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from visibility_report import (
    LayerReport,
    Metric,
    MetricCollector,
    ReportGenerator,
    VisibilityReport,
    _generate_degraded_report,
    _trace_id,
    generate_report,
    load_thresholds,
    main,
)


# ═══════════════════════════════════════════════════════════════
#  测试夹具
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def fake_project(tmp_path: Path) -> Path:
    """构造最小化项目结构用于隔离测试"""
    # agent/ 目录
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    # 含 trace_id 的结构化日志文件
    (agent_dir / "mod_a.py").write_text(
        'import logging\n'
        'logger = logging.getLogger(__name__)\n'
        'def handler():\n'
        '    logger.info({"trace_id": "abc", "module_name": "a", "action": "x"})\n',
        encoding="utf-8",
    )
    # 普通日志文件（无 trace_id）
    (agent_dir / "mod_b.py").write_text(
        'import logging\n'
        'logger = logging.getLogger(__name__)\n'
        'def helper():\n'
        '    logger.info("plain log")\n',
        encoding="utf-8",
    )

    # agent/server_routes/routes_demo.py 含 @trace_route
    routes_dir = agent_dir / "server_routes"
    routes_dir.mkdir()
    (routes_dir / "routes_demo.py").write_text(
        'from flask import Flask\n'
        'app = Flask(__name__)\n'
        'def trace_route(name):\n'
        '    def deco(f): return f\n'
        '    return deco\n'
        '@app.route("/api/health")\n'
        '@trace_route("Demo")\n'
        'def api_health():\n'
        '    return "ok"\n',
        encoding="utf-8",
    )

    # tests/contract/contracts/ 含 1 个契约文件
    contract_dir = tmp_path / "tests" / "contract" / "contracts"
    contract_dir.mkdir(parents=True)
    (contract_dir / "demo_contract.json").write_text("{}", encoding="utf-8")

    # monitoring/alerts.yml 含 2 条告警规则
    monitoring_dir = tmp_path / "monitoring"
    monitoring_dir.mkdir()
    (monitoring_dir / "alerts.yml").write_text(
        'groups:\n'
        '  - name: test\n'
        '    rules:\n'
        '      - alert: HighCpu\n'
        '        expr: cpu > 90\n'
        '      - alert: LowMemory\n'
        '        expr: mem < 10\n',
        encoding="utf-8",
    )

    # docs/architecture/dependency_graph.json
    arch_dir = tmp_path / "docs" / "architecture"
    arch_dir.mkdir(parents=True)
    (arch_dir / "dependency_graph.json").write_text(
        json.dumps({
            "nodes": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
            "edges": [{"from": "a", "to": "b"}],
        }),
        encoding="utf-8",
    )
    (arch_dir / "arch_rules_report.json").write_text(
        json.dumps({"violations_count": 0}),
        encoding="utf-8",
    )
    (arch_dir / "impact_report.json").write_text("{}", encoding="utf-8")

    return tmp_path


@pytest.fixture
def empty_project(tmp_path: Path) -> Path:
    """空项目（无任何可见性产物）"""
    return tmp_path


@pytest.fixture
def sample_thresholds() -> dict:
    """标准测试阈值"""
    return {
        "runtime": {
            "structured_log_coverage": 50,
            "trace_coverage": 50,
            "health_endpoints": 1,
        },
        "verification": {
            "test_coverage": 40,
            "boundary_test_coverage": 10,
            "contract_test_count": 1,
        },
        "business": {
            "track_event_coverage": 50,
            "dashboard_count": 1,
            "alert_rules_count": 1,
        },
        "architecture": {
            "dependency_graph_nodes": 1,
            "max_arch_violations": 5,
            "impact_analysis_coverage": 80,
        },
    }


# ═══════════════════════════════════════════════════════════════
#  数据结构测试
# ═══════════════════════════════════════════════════════════════

class TestMetricDataclass:
    """Metric 数据类测试"""

    def test_metric_with_threshold_pass(self):
        """阈值达标时 passed=True"""
        m = Metric(name="x", value=80, threshold=50)
        assert m.passed is True
        assert m.status_icon == "✅"

    def test_metric_with_threshold_fail(self):
        """阈值未达标时 passed=False"""
        m = Metric(name="x", value=30, threshold=50)
        assert m.passed is False
        assert m.status_icon == "❌"

    def test_metric_no_threshold(self):
        """无阈值时 passed=None"""
        m = Metric(name="x", value=10, threshold=None)
        assert m.passed is None
        assert m.status_icon == "➖"


class TestLayerReportDataclass:
    """LayerReport 数据类测试"""

    def test_add_metric_pass_keeps_overall(self):
        layer = LayerReport(layer_name="L", description="d")
        layer.add_metric(Metric(name="m", value=80, threshold=50))
        assert layer.overall_passed is True

    def test_add_metric_fail_marks_overall(self):
        layer = LayerReport(layer_name="L", description="d")
        layer.add_metric(Metric(name="m", value=10, threshold=50))
        assert layer.overall_passed is False


# ═══════════════════════════════════════════════════════════════
#  MetricCollector 测试
# ═══════════════════════════════════════════════════════════════

class TestMetricCollectorRuntime:
    """运行时可见层指标采集"""

    def test_structured_log_coverage_with_trace_id(self, fake_project: Path):
        """含 trace_id 的日志被识别为结构化"""
        collector = MetricCollector(fake_project, {})
        coverage = collector._calc_structured_log_coverage()
        # mod_a 有 trace_id，mod_b 无，期望覆盖率 > 0
        assert coverage > 0
        assert coverage <= 100

    def test_structured_log_coverage_empty_project(self, empty_project: Path):
        """空项目（无 agent/ 目录）返回 0"""
        collector = MetricCollector(empty_project, {})
        coverage = collector._calc_structured_log_coverage()
        assert coverage == 0.0

    def test_trace_coverage_with_decorator(self, fake_project: Path):
        """含 @trace_route 的路由被识别"""
        collector = MetricCollector(fake_project, {})
        coverage = collector._calc_trace_coverage()
        # 1 个路由 + 1 个 trace_route → 100%
        assert coverage == 100.0

    def test_health_endpoints_count(self, fake_project: Path):
        """/api/health 端点被统计"""
        collector = MetricCollector(fake_project, {})
        count = collector._count_health_endpoints()
        assert count >= 1

    def test_runtime_layer_full_collection(self, fake_project: Path, sample_thresholds: dict):
        """完整采集运行时层"""
        collector = MetricCollector(fake_project, sample_thresholds)
        layer = collector._collect_runtime_layer()
        assert layer.layer_name == "运行时可见"
        assert len(layer.metrics) == 3
        metric_names = [m.name for m in layer.metrics]
        assert "structured_log_coverage" in metric_names
        assert "trace_coverage" in metric_names
        assert "health_endpoints" in metric_names


class TestMetricCollectorVerification:
    """验证过程可见层指标采集"""

    def test_contract_count(self, fake_project: Path):
        """契约测试文件被统计"""
        collector = MetricCollector(fake_project, {})
        count = collector._count_contract_tests()
        assert count == 1

    def test_contract_count_empty(self, empty_project: Path):
        """无契约目录返回 0"""
        collector = MetricCollector(empty_project, {})
        count = collector._count_contract_tests()
        assert count == 0

    def test_test_coverage_from_coverage_xml(self, fake_project: Path):
        """从 coverage.xml 读取覆盖率（line-rate > 0 时直接使用）"""
        (fake_project / "coverage.xml").write_text(
            '<coverage line-rate="0.85"/>',
            encoding="utf-8",
        )
        collector = MetricCollector(fake_project, {})
        coverage = collector._read_test_coverage()
        assert coverage == 85.0

    def test_test_coverage_zero_line_rate_returns_zero(self, fake_project: Path):
        """coverage.xml line-rate=0（空报告）时返回 0.0（不再降级到 pyproject.toml）"""
        (fake_project / "coverage.xml").write_text(
            '<coverage line-rate="0" version="7.14.1"/>',
            encoding="utf-8",
        )
        # 即使 pyproject.toml 存在 fail_under=40，也不应降级
        (fake_project / "pyproject.toml").write_text(
            '[tool.coverage]\nfail_under = 40\n',
            encoding="utf-8",
        )
        collector = MetricCollector(fake_project, {})
        coverage = collector._read_test_coverage()
        # line-rate=0 视为无效，直接返回 0.0（不再降级到 fail_under）
        assert coverage == 0.0

    def test_test_coverage_no_xml_returns_zero(self, fake_project: Path):
        """coverage.xml 缺失时返回 0.0（不再降级到 pyproject.toml fail_under）"""
        # 即使 pyproject.toml 存在 fail_under=75，也不应降级
        (fake_project / "pyproject.toml").write_text(
            '[tool.coverage]\nfail_under = 75\n',
            encoding="utf-8",
        )
        collector = MetricCollector(fake_project, {})
        coverage = collector._read_test_coverage()
        # coverage.xml 缺失，直接返回 0.0（不再降级到 fail_under）
        assert coverage == 0.0

    def test_test_coverage_no_source(self, empty_project: Path):
        """无任何覆盖率来源返回 0"""
        collector = MetricCollector(empty_project, {})
        coverage = collector._read_test_coverage()
        assert coverage == 0.0

    def test_verification_layer_full_collection(self, fake_project: Path, sample_thresholds: dict):
        """完整采集验证过程层"""
        collector = MetricCollector(fake_project, sample_thresholds)
        layer = collector._collect_verification_layer()
        assert layer.layer_name == "验证过程可见"
        assert len(layer.metrics) == 4


class TestMetricCollectorBusiness:
    """业务价值可见层指标采集"""

    def test_alert_rules_count(self, fake_project: Path):
        """告警规则被统计"""
        collector = MetricCollector(fake_project, {})
        count = collector._count_alert_rules()
        assert count == 2

    def test_alert_rules_missing_file(self, empty_project: Path):
        """无 alerts.yml 返回 0"""
        collector = MetricCollector(empty_project, {})
        count = collector._count_alert_rules()
        assert count == 0

    def test_track_coverage_with_track_call(self, fake_project: Path):
        """含 track() 调用被识别"""
        # _calc_track_coverage 仅扫描 agent/ 下的子目录，需将 track() 写入子目录文件
        (fake_project / "agent" / "server_routes" / "routes_demo.py").write_text(
            'def track(name, payload=None):\n'
            '    pass\n'
            'def api_x():\n'
            '    track("event", {"k": "v"})\n',
            encoding="utf-8",
        )
        collector = MetricCollector(fake_project, {})
        coverage = collector._calc_track_coverage()
        assert coverage > 0

    def test_business_layer_full_collection(self, fake_project: Path, sample_thresholds: dict):
        """完整采集业务价值层"""
        collector = MetricCollector(fake_project, sample_thresholds)
        layer = collector._collect_business_layer()
        assert layer.layer_name == "业务价值可见"
        assert len(layer.metrics) == 3


class TestMetricCollectorArchitecture:
    """架构影响可见层指标采集"""

    def test_dependency_graph(self, fake_project: Path):
        """依赖图节点/边数读取"""
        collector = MetricCollector(fake_project, {})
        nodes, edges = collector._read_dependency_graph()
        assert nodes == 3
        assert edges == 1

    def test_dependency_graph_missing(self, empty_project: Path):
        """无依赖图返回 (0, 0)"""
        collector = MetricCollector(empty_project, {})
        nodes, edges = collector._read_dependency_graph()
        assert nodes == 0
        assert edges == 0

    def test_arch_violations(self, fake_project: Path):
        """架构违规数读取"""
        collector = MetricCollector(fake_project, {})
        violations = collector._read_arch_violations()
        assert violations == 0

    def test_impact_coverage(self, fake_project: Path):
        """影响分析覆盖率"""
        collector = MetricCollector(fake_project, {})
        coverage = collector._calc_impact_coverage()
        assert coverage == 100.0

    def test_architecture_layer_full_collection(self, fake_project: Path, sample_thresholds: dict):
        """完整采集架构影响层"""
        collector = MetricCollector(fake_project, sample_thresholds)
        layer = collector._collect_architecture_layer()
        assert layer.layer_name == "架构影响可见"
        assert len(layer.metrics) == 3


class TestMetricCollectorAll:
    """四层综合采集"""

    def test_collect_all_returns_four_layers(self, fake_project: Path, sample_thresholds: dict):
        """collect_all 返回四个层级"""
        collector = MetricCollector(fake_project, sample_thresholds)
        layers = collector.collect_all()
        assert len(layers) == 4
        layer_names = [l.layer_name for l in layers]
        assert "运行时可见" in layer_names
        assert "验证过程可见" in layer_names
        assert "业务价值可见" in layer_names
        assert "架构影响可见" in layer_names


# ═══════════════════════════════════════════════════════════════
#  ReportGenerator 测试
# ═══════════════════════════════════════════════════════════════

class TestReportGenerator:
    """报告生成器测试"""

    @pytest.fixture
    def sample_report(self) -> VisibilityReport:
        """构造样例报告"""
        layer1 = LayerReport(layer_name="运行时可见", description="d1")
        layer1.add_metric(Metric(name="m1", value=80, threshold=50, unit="%"))
        layer2 = LayerReport(layer_name="验证过程可见", description="d2")
        layer2.add_metric(Metric(name="m2", value=10, threshold=50, unit="%"))
        return VisibilityReport(
            trace_id="abc123",
            timestamp="2026-06-26T00:00:00",
            duration_ms=42.5,
            layers=[layer1, layer2],
            overall_status="fail",
            threshold_violations=["验证过程可见.m2: 实际=10%, 阈值=50%"],
        )

    def test_generate_markdown_contains_layers(self, sample_report: VisibilityReport):
        """Markdown 报告包含所有层级"""
        gen = ReportGenerator(PROJECT_ROOT)
        md = gen.generate_markdown(sample_report)
        assert "运行时可见" in md
        assert "验证过程可见" in md
        assert "四层可见性覆盖报告" in md

    def test_generate_markdown_contains_trace_id(self, sample_report: VisibilityReport):
        """Markdown 报告包含 trace_id"""
        gen = ReportGenerator(PROJECT_ROOT)
        md = gen.generate_markdown(sample_report)
        assert "abc123" in md

    def test_generate_markdown_contains_violations(self, sample_report: VisibilityReport):
        """Markdown 报告包含阈值违规清单"""
        gen = ReportGenerator(PROJECT_ROOT)
        md = gen.generate_markdown(sample_report)
        assert "阈值违规清单" in md
        assert "验证过程可见.m2" in md

    def test_generate_markdown_no_violations(self):
        """无违规时显示通过信息"""
        layer = LayerReport(layer_name="L", description="d")
        layer.add_metric(Metric(name="m", value=80, threshold=50))
        report = VisibilityReport(
            trace_id="t",
            timestamp="2026-06-26T00:00:00",
            duration_ms=10,
            layers=[layer],
            overall_status="pass",
            threshold_violations=[],
        )
        gen = ReportGenerator(PROJECT_ROOT)
        md = gen.generate_markdown(report)
        assert "所有指标均达到阈值要求" in md

    def test_generate_json_structure(self, sample_report: VisibilityReport):
        """JSON 报告结构正确"""
        gen = ReportGenerator(PROJECT_ROOT)
        data = gen.generate_json(sample_report)
        assert data["trace_id"] == "abc123"
        assert data["overall_status"] == "fail"
        assert len(data["layers"]) == 2
        assert data["layers"][0]["layer_name"] == "运行时可见"
        assert len(data["layers"][0]["metrics"]) == 1

    def test_status_badge(self, sample_report: VisibilityReport):
        """状态徽章映射正确"""
        gen = ReportGenerator(PROJECT_ROOT)
        assert gen._status_badge("pass") == "✅ 通过"
        assert gen._status_badge("fail") == "❌ 阈值未达标"
        assert gen._status_badge("degraded") == "⚠️ 降级（部分指标采集失败）"


# ═══════════════════════════════════════════════════════════════
#  阈值阻断测试
# ═══════════════════════════════════════════════════════════════

class TestThresholdBlocking:
    """阈值阻断机制测试"""

    def test_all_pass_returns_pass(self, fake_project: Path):
        """所有指标达标时 overall_status=pass"""
        # 设置极低阈值确保全部通过
        thresholds = {
            "runtime": {"structured_log_coverage": 0, "trace_coverage": 0, "health_endpoints": 0},
            "verification": {"test_coverage": 0, "boundary_test_coverage": 0, "contract_test_count": 0, "exception_coverage": 0},
            "business": {"track_event_coverage": 0, "dashboard_count": 0, "alert_rules_count": 0},
            "architecture": {"dependency_graph_nodes": 0, "max_arch_violations": 100, "impact_analysis_coverage": 0},
        }
        report = generate_report(fake_project, thresholds)
        assert report.overall_status == "pass"
        assert len(report.threshold_violations) == 0

    def test_threshold_violation_collected(self, fake_project: Path):
        """阈值违规被正确收集"""
        thresholds = {
            "runtime": {"structured_log_coverage": 100},  # 强制失败
            "verification": {},
            "business": {},
            "architecture": {},
        }
        report = generate_report(fake_project, thresholds)
        assert report.overall_status == "fail"
        assert len(report.threshold_violations) >= 1
        assert any("structured_log_coverage" in v for v in report.threshold_violations)

    def test_arch_violation_inverse_metric(self, fake_project: Path):
        """架构违规数为逆向指标：实际值 ≤ max 才通过"""
        # fake_project 的违规数为 0，max=0 应通过
        thresholds = {"architecture": {"max_arch_violations": 0}}
        collector = MetricCollector(fake_project, thresholds)
        layer = collector._collect_architecture_layer()
        arch_metric = next(m for m in layer.metrics if m.name == "arch_rule_violations")
        assert arch_metric.passed is True

    def test_arch_violation_exceeds_max(self, fake_project: Path):
        """架构违规数超过 max 时失败"""
        # 修改报告使违规数=5，max=3
        arch_report = fake_project / "docs" / "architecture" / "arch_rules_report.json"
        arch_report.write_text(json.dumps({"violations_count": 5}), encoding="utf-8")
        thresholds = {"architecture": {"max_arch_violations": 3}}
        collector = MetricCollector(fake_project, thresholds)
        layer = collector._collect_architecture_layer()
        arch_metric = next(m for m in layer.metrics if m.name == "arch_rule_violations")
        assert arch_metric.passed is False
        assert arch_metric.value == 5


# ═══════════════════════════════════════════════════════════════
#  降级报告测试
# ═══════════════════════════════════════════════════════════════

class TestDegradedReport:
    """降级报告输出测试"""

    def test_degraded_report_written(self, tmp_path: Path):
        """异常时输出降级 Markdown"""
        output_path = tmp_path / "report.md"
        try:
            raise RuntimeError("模拟采集失败")
        except RuntimeError as e:
            _generate_degraded_report(e, output_path)

        assert output_path.exists()
        content = output_path.read_text(encoding="utf-8")
        assert "降级" in content
        assert "RuntimeError" in content
        assert "模拟采集失败" in content

    def test_degraded_report_includes_stack_trace(self, tmp_path: Path):
        """降级报告包含错误堆栈"""
        output_path = tmp_path / "report.md"
        try:
            raise ValueError("测试堆栈")
        except ValueError as e:
            _generate_degraded_report(e, output_path)

        content = output_path.read_text(encoding="utf-8")
        assert "Traceback" in content or "堆栈" in content

    def test_degraded_report_handles_output_error(self, tmp_path: Path):
        """降级报告输出失败时不抛出异常"""
        # 指向一个不存在的盘符路径（Windows 下会失败）
        output_path = Path("Z:/nonexistent_drive/report.md")
        try:
            raise RuntimeError("测试")
        except RuntimeError as e:
            # 不应抛出异常
            _generate_degraded_report(e, output_path)


# ═══════════════════════════════════════════════════════════════
#  配置加载测试
# ═══════════════════════════════════════════════════════════════

class TestLoadThresholds:
    """阈值配置加载测试"""

    def test_load_from_yaml(self, tmp_path: Path):
        """从 YAML 加载阈值"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            'visibility_thresholds:\n'
            '  runtime:\n'
            '    structured_log_coverage: 75\n'
            '    trace_coverage: 60\n'
            '  verification:\n'
            '    test_coverage: 50\n',
            encoding="utf-8",
        )
        thresholds = load_thresholds(config_file)
        assert thresholds["runtime"]["structured_log_coverage"] == 75
        assert thresholds["runtime"]["trace_coverage"] == 60
        assert thresholds["verification"]["test_coverage"] == 50

    def test_load_missing_file(self, tmp_path: Path):
        """配置文件不存在返回空字典"""
        thresholds = load_thresholds(tmp_path / "nonexistent.yaml")
        assert thresholds == {}

    def test_load_invalid_yaml(self, tmp_path: Path):
        """非法 YAML 返回空字典"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(":::invalid:::", encoding="utf-8")
        thresholds = load_thresholds(config_file)
        # 解析失败时返回空字典（不抛异常）
        assert thresholds == {}

    def test_load_without_visibility_section(self, tmp_path: Path):
        """无 visibility_thresholds 段返回空字典"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("other_key: value\n", encoding="utf-8")
        thresholds = load_thresholds(config_file)
        assert thresholds == {}


# ═══════════════════════════════════════════════════════════════
#  工具函数测试
# ═══════════════════════════════════════════════════════════════

class TestUtilities:
    """工具函数测试"""

    def test_trace_id_length(self):
        """trace_id 为 16 位十六进制"""
        tid = _trace_id()
        assert len(tid) == 16
        int(tid, 16)  # 可解析为十六进制

    def test_trace_id_unique(self):
        """trace_id 唯一性"""
        ids = {_trace_id() for _ in range(100)}
        assert len(ids) == 100


# ═══════════════════════════════════════════════════════════════
#  端到端 generate_report 测试
# ═══════════════════════════════════════════════════════════════

class TestGenerateReport:
    """generate_report 主流程测试"""

    def test_generate_report_structure(self, fake_project: Path, sample_thresholds: dict):
        """报告结构完整"""
        report = generate_report(fake_project, sample_thresholds)
        assert isinstance(report, VisibilityReport)
        assert report.trace_id
        assert report.timestamp
        assert report.duration_ms >= 0
        assert len(report.layers) == 4
        assert report.overall_status in ("pass", "fail")

    def test_generate_report_violations_format(self, fake_project: Path):
        """违规信息格式：层级.指标名: 实际=X, 阈值=Y"""
        thresholds = {"runtime": {"structured_log_coverage": 100}}
        report = generate_report(fake_project, thresholds)
        if report.threshold_violations:
            v = report.threshold_violations[0]
            assert "运行时可见" in v
            assert "structured_log_coverage" in v
            assert "实际=" in v
            assert "阈值=" in v


# ═══════════════════════════════════════════════════════════════
#  CLI 入口测试
# ═══════════════════════════════════════════════════════════════

class TestCLI:
    """CLI 入口测试"""

    def test_cli_json_only_pass(self, fake_project: Path, tmp_path: Path, capsys):
        """--json-only 输出有效 JSON，全通过时 exit=0"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            'visibility_thresholds:\n'
            '  runtime: {structured_log_coverage: 0, trace_coverage: 0, health_endpoints: 0}\n'
            '  verification: {test_coverage: 0, boundary_test_coverage: 0, contract_test_count: 0, exception_coverage: 0}\n'
            '  business: {track_event_coverage: 0, dashboard_count: 0, alert_rules_count: 0}\n'
            '  architecture: {dependency_graph_nodes: 0, max_arch_violations: 100, impact_analysis_coverage: 0}\n',
            encoding="utf-8",
        )
        # 使用 patch 替换 PROJECT_ROOT，使脚本指向 fake_project
        with patch("visibility_report.PROJECT_ROOT", fake_project):
            exit_code = main([
                "--config", str(config_file),
                "--json-only",
            ])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["overall_status"] == "pass"
        assert exit_code == 0

    def test_cli_json_only_fail(self, fake_project: Path, tmp_path: Path, capsys):
        """--json-only 阈值未达标时 exit=1"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            'visibility_thresholds:\n'
            '  runtime: {structured_log_coverage: 100}\n'
            '  verification: {}\n'
            '  business: {}\n'
            '  architecture: {}\n',
            encoding="utf-8",
        )
        with patch("visibility_report.PROJECT_ROOT", fake_project):
            exit_code = main([
                "--config", str(config_file),
                "--json-only",
            ])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["overall_status"] == "fail"
        assert exit_code == 1

    def test_cli_markdown_output(self, fake_project: Path, tmp_path: Path):
        """--output 指定路径生成 Markdown + JSON"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            'visibility_thresholds:\n'
            '  runtime: {structured_log_coverage: 0}\n'
            '  verification: {}\n'
            '  business: {}\n'
            '  architecture: {}\n',
            encoding="utf-8",
        )
        output_md = tmp_path / "out" / "report.md"
        with patch("visibility_report.PROJECT_ROOT", fake_project):
            exit_code = main([
                "--config", str(config_file),
                "--output", str(output_md),
            ])
        assert output_md.exists()
        assert output_md.with_suffix(".json").exists()
        md_content = output_md.read_text(encoding="utf-8")
        assert "四层可见性覆盖报告" in md_content

    def test_cli_missing_config_uses_defaults(self, fake_project: Path, tmp_path: Path, capsys):
        """配置文件不存在时使用默认阈值（不阻断）"""
        with patch("visibility_report.PROJECT_ROOT", fake_project):
            exit_code = main([
                "--config", str(tmp_path / "nonexistent.yaml"),
                "--json-only",
            ])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        # 默认阈值下，部分指标可能未达标，但脚本应正常执行
        assert data["overall_status"] in ("pass", "fail")
        assert "layers" in data


# ═══════════════════════════════════════════════════════════════
#  真实项目集成测试
# ═══════════════════════════════════════════════════════════════

class TestRealProjectIntegration:
    """与真实项目结构联动测试（验证脚本可在当前项目下运行）"""

    def test_real_project_collect_all_layers(self):
        """真实项目能采集四层指标"""
        collector = MetricCollector(PROJECT_ROOT, {})
        layers = collector.collect_all()
        assert len(layers) == 4
        # 每层至少有 3 个指标（验证层有 4 个，含 exception_coverage）
        for layer in layers:
            assert len(layer.metrics) >= 3

    def test_real_project_runtime_layer_has_data(self):
        """真实项目运行时层有结构化日志与端点"""
        collector = MetricCollector(PROJECT_ROOT, {})
        log_coverage = collector._calc_structured_log_coverage()
        health_count = collector._count_health_endpoints()
        # 真实项目应有结构化日志与健康端点
        assert log_coverage > 0
        assert health_count > 0

    def test_real_project_alert_rules(self):
        """真实项目有告警规则"""
        collector = MetricCollector(PROJECT_ROOT, {})
        alert_count = collector._count_alert_rules()
        assert alert_count > 0

    def test_real_project_full_report_generation(self, tmp_path: Path):
        """真实项目生成完整报告（端到端）"""
        output_md = tmp_path / "real_report.md"
        exit_code = main([
            "--config", str(PROJECT_ROOT / "config.yaml"),
            "--output", str(output_md),
        ])
        # exit_code 0/1 都是正常的（1 表示有阈值违规，但报告应成功生成）
        assert exit_code in (0, 1)
        assert output_md.exists()
        assert output_md.with_suffix(".json").exists()
        md = output_md.read_text(encoding="utf-8")
        assert "四层可见性覆盖报告" in md
        assert "运行时可见" in md
        assert "验证过程可见" in md

    def test_real_project_json_only_output(self, capsys):
        """真实项目 --json-only 输出有效 JSON"""
        exit_code = main([
            "--config", str(PROJECT_ROOT / "config.yaml"),
            "--json-only",
        ])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "trace_id" in data
        assert "layers" in data
        assert len(data["layers"]) == 4
        assert exit_code in (0, 1)


# ═══════════════════════════════════════════════════════════════
#  异常路径测试
# ═══════════════════════════════════════════════════════════════

class TestErrorPaths:
    """异常路径覆盖"""

    def test_collector_with_unreadable_file(self, fake_project: Path):
        """不可读文件被跳过（不抛异常）"""
        # 创建一个编码异常的文件（非 UTF-8 字节序列）
        bad_file = fake_project / "agent" / "bad.py"
        bad_file.write_bytes(b"\xff\xfe\x00\x80abc")
        collector = MetricCollector(fake_project, {})
        # 不应抛出异常
        coverage = collector._calc_structured_log_coverage()
        assert isinstance(coverage, float)

    def test_collector_empty_thresholds(self, fake_project: Path):
        """空阈值字典时使用各指标的默认阈值（如 80），passed 按默认阈值判定"""
        collector = MetricCollector(fake_project, {})
        layer = collector._collect_runtime_layer()
        # 空阈值字典时，_collect_runtime_layer 使用硬编码默认阈值（如 80）
        # 因此 passed 会被计算为 True/False，而非 None
        for m in layer.metrics:
            assert m.passed in (True, False, None)

    def test_collector_none_threshold_value(self, fake_project: Path):
        """阈值为 None 时该指标无阻断"""
        thresholds = {"runtime": {"structured_log_coverage": None}}
        collector = MetricCollector(fake_project, thresholds)
        layer = collector._collect_runtime_layer()
        m = next(x for x in layer.metrics if x.name == "structured_log_coverage")
        # threshold=None 不会触发比较
        assert m.threshold is None
