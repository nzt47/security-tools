#!/usr/bin/env python3
"""scripts/observability_quality_gate.py 单元测试草稿

【批次 1 · 门禁类】本测试覆盖 QualityGateChecker 的判定分支：
1. collect_reports — 目录存在/不存在、JSON 解析成功/失败
2. check_config_validation — 报告缺失(skip)/passed/failed
3. check_unit_tests — 报告缺失(skip)/found(passed)
4. check_coverage — 报告缺失(skip)/无百分比(skip)/达标(passed)/未达标(failed)
5. check_e2e_tests — require_e2e_pass=True 缺失(failed)/False 缺失(skip)/passed/failed
6. check_prometheus_integration — 缺失(skip)/passed/failed
7. run_all_checks — 全链路聚合与 overall_status
8. main() — argparse 入口与 exit code

待完善项（标注 TODO）：
- check_integration_tests 的 found 分支（当前为简化实现）
- print_summary 的输出断言（当前仅 smoke）
"""

import pytest
import json
import sys
from pathlib import Path
from unittest.mock import patch

# 从 scripts 目录导入目标模块
import importlib.util

def _load_quality_gate():
    """从 scripts/ 加载 QualityGateChecker（脚本非包内模块）"""
    spec = importlib.util.spec_from_file_location(
        "observability_quality_gate",
        Path(__file__).resolve().parents[2] / "scripts" / "observability_quality_gate.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

QG = _load_quality_gate()


# ──────────────────────────────────────────────────────────────────────────────
# 1. collect_reports
# ──────────────────────────────────────────────────────────────────────────────
class TestCollectReports:
    """collect_reports：收集 results_dir 下所有 JSON 报告"""

    def test_dir_not_exist_returns_empty(self, tmp_path):
        """目录不存在时返回空 dict 并打印警告"""
        checker = QG.QualityGateChecker(str(tmp_path / "missing"))
        assert checker.collect_reports() == {}
        # 目录不存在时提前返回，collected_reports 保持 __init__ 初始空值（源码 L70-72 提前 return）
        assert checker.results["collected_reports"] == {}

    def test_collects_valid_json(self, tmp_path):
        """正常收集目录下所有 JSON 文件"""
        (tmp_path / "a.json").write_text(json.dumps({"overall_status": "passed"}),
                                         encoding="utf-8")
        (tmp_path / "b.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
        (tmp_path / "ignore.txt").write_text("not json", encoding="utf-8")

        checker = QG.QualityGateChecker(str(tmp_path))
        reports = checker.collect_reports()
        assert len(reports) == 2
        assert "a.json" in reports

    def test_invalid_json_skipped(self, tmp_path):
        """解析失败的 JSON 文件被跳过并打印警告，不中断"""
        (tmp_path / "bad.json").write_text("{not valid json", encoding="utf-8")
        (tmp_path / "good.json").write_text(json.dumps({"ok": 1}), encoding="utf-8")

        checker = QG.QualityGateChecker(str(tmp_path))
        reports = checker.collect_reports()
        assert "good.json" in reports
        assert "bad.json" not in reports


# ──────────────────────────────────────────────────────────────────────────────
# 2. check_config_validation
# ──────────────────────────────────────────────────────────────────────────────
class TestCheckConfigValidation:
    """check_config_validation：配置验证报告判定"""

    def test_report_missing_skipped(self):
        """无配置报告时 skip 且不算失败"""
        checker = QG.QualityGateChecker("results")
        assert checker.check_config_validation({}) is True
        assert checker.results["checks"]["config_validation"]["status"] == "skipped"

    def test_passed_status(self):
        """overall_status=passed 时通过"""
        checker = QG.QualityGateChecker("results")
        reports = {"config_report.json": {"overall_status": "passed"}}
        assert checker.check_config_validation(reports) is True
        assert checker.results["checks"]["config_validation"]["status"] == "passed"

    def test_failed_status(self):
        """overall_status != passed 时失败"""
        checker = QG.QualityGateChecker("results")
        reports = {"config_report.json": {"overall_status": "failed"}}
        assert checker.check_config_validation(reports) is False
        assert checker.results["checks"]["config_validation"]["status"] == "failed"


# ──────────────────────────────────────────────────────────────────────────────
# 3. check_unit_tests
# ──────────────────────────────────────────────────────────────────────────────
class TestCheckUnitTests:
    """check_unit_tests：单元测试报告判定"""

    def test_report_missing_skipped(self):
        """无单元测试报告时 skip（当前简化：跳过不算失败）"""
        checker = QG.QualityGateChecker("results")
        assert checker.check_unit_tests({}) is True
        assert checker.results["checks"]["unit_tests"]["status"] == "skipped"

    def test_report_found_passed(self):
        """找到单元测试报告时 passed（当前简化实现）"""
        checker = QG.QualityGateChecker("results")
        reports = {"unit-test_report.json": {"passed": 10, "failed": 0}}
        assert checker.check_unit_tests(reports) is True
        assert checker.results["checks"]["unit_tests"]["status"] == "passed"


# ──────────────────────────────────────────────────────────────────────────────
# 4. check_coverage
# ──────────────────────────────────────────────────────────────────────────────
class TestCheckCoverage:
    """check_coverage：覆盖率报告判定（三个提取格式 + 阈值比较）"""

    def test_report_missing_skipped(self):
        """无覆盖率报告时 skip"""
        checker = QG.QualityGateChecker("results")
        assert checker.check_coverage({}) is True
        assert checker.results["checks"]["test_coverage"]["status"] == "skipped"

    def test_totals_format_above_threshold(self):
        """totals 格式且达标时 passed"""
        checker = QG.QualityGateChecker("results", min_coverage=60.0)
        reports = {"coverage_report.json": {"totals": {"percent_covered": 75.0}}}
        assert checker.check_coverage(reports) is True
        assert checker.results["checks"]["test_coverage"]["status"] == "passed"

    def test_totals_format_below_threshold(self):
        """totals 格式但低于阈值时 failed"""
        checker = QG.QualityGateChecker("results", min_coverage=60.0)
        reports = {"coverage_report.json": {"totals": {"percent_covered": 22.4}}}
        assert checker.check_coverage(reports) is False
        assert checker.results["checks"]["test_coverage"]["status"] == "failed"

    def test_coverage_key_format(self):
        """顶层 coverage 键格式"""
        checker = QG.QualityGateChecker("results", min_coverage=60.0)
        reports = {"cov.json": {"coverage": 80.0}}
        assert checker.check_coverage(reports) is True

    def test_common_key_format(self):
        """percent_covered 键格式"""
        checker = QG.QualityGateChecker("results", min_coverage=60.0)
        reports = {"cov.json": {"percent_covered": 90.0}}
        assert checker.check_coverage(reports) is True

    def test_no_percent_skipped(self):
        """报告中无可用百分比时 skip 而非 failed"""
        checker = QG.QualityGateChecker("results")
        reports = {"cov.json": {"totals": {}}}
        assert checker.check_coverage(reports) is True
        assert checker.results["checks"]["test_coverage"]["status"] == "skipped"


# ──────────────────────────────────────────────────────────────────────────────
# 5. check_e2e_tests
# ──────────────────────────────────────────────────────────────────────────────
class TestCheckE2ETests:
    """check_e2e_tests：E2E 报告判定（require_e2e_pass 分支）"""

    def test_missing_required_failed(self):
        """require_e2e_pass=True 且无报告时 failed"""
        checker = QG.QualityGateChecker("results", require_e2e_pass=True)
        assert checker.check_e2e_tests({}) is False
        assert checker.results["checks"]["e2e_tests"]["status"] == "failed"

    def test_missing_not_required_skipped(self):
        """require_e2e_pass=False 且无报告时 skipped"""
        checker = QG.QualityGateChecker("results", require_e2e_pass=False)
        assert checker.check_e2e_tests({}) is True
        assert checker.results["checks"]["e2e_tests"]["status"] == "skipped"

    def test_passed_status(self):
        """status=passed 时通过"""
        checker = QG.QualityGateChecker("results")
        reports = {"e2e_report.json": {"status": "passed"}}
        assert checker.check_e2e_tests(reports) is True

    def test_failed_status(self):
        """status=failed 时失败"""
        checker = QG.QualityGateChecker("results")
        reports = {"e2e_report.json": {"status": "failed"}}
        assert checker.check_e2e_tests(reports) is False


# ──────────────────────────────────────────────────────────────────────────────
# 6. check_prometheus_integration
# ──────────────────────────────────────────────────────────────────────────────
class TestCheckPrometheusIntegration:
    """check_prometheus_integration：Prometheus 报告判定"""

    def test_report_missing_skipped(self):
        """无 Prometheus 报告时 skip"""
        checker = QG.QualityGateChecker("results")
        assert checker.check_prometheus_integration({}) is True
        assert checker.results["checks"]["prometheus_integration"]["status"] == "skipped"

    def test_passed_status(self):
        """overall_status=passed 时通过"""
        checker = QG.QualityGateChecker("results")
        reports = {"prometheus_report.json": {"overall_status": "passed"}}
        assert checker.check_prometheus_integration(reports) is True

    def test_failed_status(self):
        """overall_status=failed 时失败"""
        checker = QG.QualityGateChecker("results")
        reports = {"prometheus_report.json": {"overall_status": "failed"}}
        assert checker.check_prometheus_integration(reports) is False


# ──────────────────────────────────────────────────────────────────────────────
# 7. run_all_checks 聚合
# ──────────────────────────────────────────────────────────────────────────────
class TestRunAllChecks:
    """run_all_checks：全链路聚合与 overall_status"""

    def test_all_pass(self, tmp_path):
        """全部通过时 overall_status=passed"""
        (tmp_path / "config.json").write_text(
            json.dumps({"overall_status": "passed"}), encoding="utf-8")
        (tmp_path / "e2e.json").write_text(
            json.dumps({"overall_status": "passed"}), encoding="utf-8")
        (tmp_path / "coverage.json").write_text(
            json.dumps({"totals": {"percent_covered": 80.0}}), encoding="utf-8")

        checker = QG.QualityGateChecker(str(tmp_path), output_file=str(tmp_path / "out.json"))
        result = checker.run_all_checks()
        assert result["overall_status"] == "passed"

    def test_e2e_failure_causes_failed(self, tmp_path):
        """E2E 失败时 overall_status=failed"""
        (tmp_path / "config.json").write_text(
            json.dumps({"overall_status": "passed"}), encoding="utf-8")
        (tmp_path / "e2e.json").write_text(
            json.dumps({"overall_status": "failed"}), encoding="utf-8")

        checker = QG.QualityGateChecker(str(tmp_path), output_file=str(tmp_path / "out.json"))
        result = checker.run_all_checks()
        assert result["overall_status"] == "failed"

    def test_save_report_writes_file(self, tmp_path):
        """报告文件被写入磁盘"""
        out_file = tmp_path / "gate.json"
        checker = QG.QualityGateChecker(str(tmp_path), output_file=str(out_file))
        checker.run_all_checks()
        assert out_file.exists()
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert "overall_status" in data


# ──────────────────────────────────────────────────────────────────────────────
# 8. main() 入口
# ──────────────────────────────────────────────────────────────────────────────
class TestMainEntry:
    """main()：argparse 入口与 exit code"""

    def test_main_exit_zero_on_pass(self, tmp_path, capsys):
        """全部通过时 main 返回 0"""
        # 需 config + e2e 均 passed（coverage 缺失为 skipped 不阻断）
        (tmp_path / "config.json").write_text(
            json.dumps({"overall_status": "passed"}), encoding="utf-8")
        (tmp_path / "e2e.json").write_text(
            json.dumps({"overall_status": "passed"}), encoding="utf-8")
        with patch.object(sys, "argv", [
            "observability_quality_gate.py",
            "--results-dir", str(tmp_path),
            "--output", str(tmp_path / "out.json"),
        ]):
            with pytest.raises(SystemExit) as exc:
                QG.main()
        assert exc.value.code == 0

    def test_main_exit_one_on_fail(self, tmp_path, capsys):
        """E2E 缺失且要求通过时 main 返回 1"""
        # 空目录：E2E 缺失 → failed → exit 1
        with patch.object(sys, "argv", [
            "observability_quality_gate.py",
            "--results-dir", str(tmp_path),
            "--output", str(tmp_path / "out.json"),
        ]):
            with pytest.raises(SystemExit) as exc:
                QG.main()
        assert exc.value.code == 1

    def test_require_e2e_flag_parsing(self, tmp_path):
        """--require-e2e-pass false 应解析为布尔 False"""
        with patch.object(sys, "argv", [
            "observability_quality_gate.py",
            "--results-dir", str(tmp_path),
            "--require-e2e-pass", "false",
        ]):
            # 空目录 + require_e2e_pass=False → E2E skipped → 其余也 skipped → passed → exit 0
            with pytest.raises(SystemExit) as exc:
                QG.main()
        assert exc.value.code == 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
