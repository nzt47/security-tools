# -*- coding: utf-8 -*-
"""P3-1 质量门禁覆盖率口径回归测试

【不易】门禁覆盖率必须读取 full-coverage-report/coverage.xml（全项目 6-shard 合并口径），
不得再匹配 observability-unit-test-results 的局部覆盖率（历史 bug：3703bd7d/34f42cb6 两次误失败）。
【变易】覆盖三种场景：XML 通过 / XML 低于阈值 / 无 XML 回退 JSON。
"""
import json
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from observability_quality_gate import QualityGateChecker  # noqa: E402


def _write_coverage_xml(results_dir: Path, line_rate: float, subdir: str = "full-coverage-report") -> Path:
    """构造 coverage.py 风格的 coverage.xml（line-rate 为 0~1 小数）"""
    xml_path = results_dir / subdir / "coverage.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("coverage", {"line-rate": f"{line_rate:.6f}", "branch-rate": "0.5"})
    ET.ElementTree(root).write(xml_path, encoding="utf-8", xml_declaration=True)
    return xml_path


def _write_local_coverage_json(results_dir: Path, percent: float) -> Path:
    """构造可观测性子模块局部覆盖率 JSON（历史误匹配源，~22.8%）"""
    json_path = results_dir / "observability-unit-test-results-py3.11" / "coverage.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps({"totals": {"percent_covered": percent}}), encoding="utf-8")
    return json_path


def _run_check(results_dir: Path, min_coverage: float = 60.0) -> dict:
    checker = QualityGateChecker(results_dir=str(results_dir), min_coverage=min_coverage,
                                 min_unit_test_pass_rate=95.0, require_e2e_pass=False)
    checker.run_all_checks()
    return checker.results["checks"]["test_coverage"]


class TestQualityGateCoverageXmlPriority:
    """P3-1：coverage.xml 优先级与口径"""

    def test_full_coverage_report_xml_high_rate_passes(self):
        """全项目 coverage.xml line-rate=0.75（75%）且局部 JSON 22.8% 共存时，必须读 XML 并通过"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_coverage_xml(d, line_rate=0.75)
            _write_local_coverage_json(d, percent=22.8)
            check = _run_check(d, min_coverage=60.0)
            assert check["status"] == "passed", check
            assert check["details"]["coverage_source"] == "coverage.xml"
            assert check["details"]["coverage_percent"] == pytest.approx(75.0)

    def test_full_coverage_report_xml_low_rate_fails(self):
        """全项目 coverage.xml line-rate=0.4（40%）低于阈值 60%，即使局部 JSON 高也不应误过"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_coverage_xml(d, line_rate=0.4)
            _write_local_coverage_json(d, percent=95.0)
            check = _run_check(d, min_coverage=60.0)
            assert check["status"] == "failed", check
            assert "低于阈值" in check["error"]

    def test_no_xml_fallback_to_json(self):
        """无 coverage.xml 时回退 JSON 提取（兼容历史场景）"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_local_coverage_json(d, percent=80.0)
            check = _run_check(d, min_coverage=60.0)
            assert check["status"] == "passed", check
            assert check["details"]["coverage_source"] == "JSON 报告"

    def test_local_xml_not_mistaken_for_full(self):
        """仅存在局部（observability-unit-test）coverage.xml 时，也应能读取（回退任意 XML）"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_coverage_xml(d, line_rate=0.228, subdir="observability-unit-test-results-py3.11")
            check = _run_check(d, min_coverage=60.0)
            assert check["status"] == "failed", check
            assert check["details"]["coverage_source"] == "coverage.xml"

    def test_no_reports_skipped(self):
        """无任何覆盖率报告时 skip 不失败（保持原语义）"""
        with tempfile.TemporaryDirectory() as tmp:
            check = _run_check(Path(tmp), min_coverage=60.0)
            assert check["status"] == "skipped", check
