#!/usr/bin/env python3
"""scripts/check_scripts_coverage.py 单元测试草稿（批次 1 · 门禁类）

覆盖 main() 的全部判定分支（xml line-rate → 百分比 → 阈值比较）：
1. xml 解析失败（文件缺失 / 坏 xml / 缺 line-rate 属性）→ return 1 + ::error::
2. 缺口 ≥ warn-gap（低于红线 - warn_gap）→ return 1 + ::error::
3. 缺口 < warn-gap 但低于红线 → return 0 + ::warning::
4. 达标（≥ 红线）→ return 0
5. --fail-under / --warn-gap 命令行覆盖
"""

import pytest
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
import importlib.util


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_scripts_coverage",
        Path(__file__).resolve().parents[2] / "scripts" / "check_scripts_coverage.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CSC = _load()


def _write_xml(tmp_path, rate_percent: float) -> Path:
    """生成 line-rate 等于 rate_percent 的 coverage xml"""
    p = tmp_path / "coverage.xml"
    root = ET.Element("coverage", {"line-rate": f"{rate_percent / 100:.6f}"})
    ET.ElementTree(root).write(p, encoding="utf-8")
    return p


class TestParseErrors:
    def test_missing_file(self, tmp_path, capsys):
        with patch.object(sys, "argv", [
            "check_scripts_coverage.py", "--xml", str(tmp_path / "nope.xml"),
        ]):
            assert CSC.main() == 1
        assert "::error::无法解析覆盖率报告" in capsys.readouterr().out

    def test_bad_xml(self, tmp_path, capsys):
        p = tmp_path / "bad.xml"
        p.write_text("<not-closed>", encoding="utf-8")
        with patch.object(sys, "argv", [
            "check_scripts_coverage.py", "--xml", str(p),
        ]):
            assert CSC.main() == 1
        assert "::error::无法解析覆盖率报告" in capsys.readouterr().out

    def test_missing_line_rate_attr(self, tmp_path, capsys):
        p = tmp_path / "noline.xml"
        ET.ElementTree(ET.Element("coverage")).write(p, encoding="utf-8")
        with patch.object(sys, "argv", [
            "check_scripts_coverage.py", "--xml", str(p),
        ]):
            assert CSC.main() == 1
        assert "::error::无法解析覆盖率报告" in capsys.readouterr().out


class TestFailThreshold:
    def test_gap_exceeds_warn_gap(self, tmp_path, capsys):
        """6.9% vs 红线50%（缺43.1pp ≥ 5pp）→ exit 1"""
        p = _write_xml(tmp_path, 6.9)
        with patch.object(sys, "argv", [
            "check_scripts_coverage.py", "--xml", str(p),
        ]):
            assert CSC.main() == 1
        assert "::error::scripts 覆盖率缺口" in capsys.readouterr().out

    def test_gap_below_warn_gap_warning_only(self, tmp_path, capsys):
        """47% vs 红线50%（缺3pp < 5pp）→ exit 0 + ::warning::"""
        p = _write_xml(tmp_path, 47.0)
        with patch.object(sys, "argv", [
            "check_scripts_coverage.py", "--xml", str(p),
        ]):
            assert CSC.main() == 0
        out = capsys.readouterr().out
        assert "::warning::scripts 覆盖率低于红线" in out

    def test_exact_boundary_warn_gap(self, tmp_path, capsys):
        """45% vs 红线50% 且 warn-gap=5（缺口==5pp）：< 严格小于 → 不阻断，仅告警"""
        p = _write_xml(tmp_path, 45.0)
        with patch.object(sys, "argv", [
            "check_scripts_coverage.py", "--xml", str(p),
        ]):
            assert CSC.main() == 0
        assert "::warning::scripts 覆盖率低于红线" in capsys.readouterr().out


class TestPassThreshold:
    def test_above_red_line(self, tmp_path, capsys):
        p = _write_xml(tmp_path, 60.0)
        with patch.object(sys, "argv", [
            "check_scripts_coverage.py", "--xml", str(p),
        ]):
            assert CSC.main() == 0
        assert "✅ scripts 覆盖率达标" in capsys.readouterr().out

    def test_exactly_at_red_line(self, tmp_path):
        p = _write_xml(tmp_path, 50.0)
        with patch.object(sys, "argv", [
            "check_scripts_coverage.py", "--xml", str(p),
        ]):
            assert CSC.main() == 0


class TestCliOverrides:
    def test_custom_fail_under(self, tmp_path, capsys):
        """--fail-under 60：55% 视为未达标但缺口<5pp → warning"""
        p = _write_xml(tmp_path, 55.0)
        with patch.object(sys, "argv", [
            "check_scripts_coverage.py", "--xml", str(p),
            "--fail-under", "60",
        ]):
            assert CSC.main() == 0
        assert "红线 60%" in capsys.readouterr().out

    def test_custom_warn_gap_zero(self, tmp_path, capsys):
        """--warn-gap 0：任何低于红线都阻断"""
        p = _write_xml(tmp_path, 49.0)
        with patch.object(sys, "argv", [
            "check_scripts_coverage.py", "--xml", str(p),
            "--fail-under", "50", "--warn-gap", "0",
        ]):
            assert CSC.main() == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
