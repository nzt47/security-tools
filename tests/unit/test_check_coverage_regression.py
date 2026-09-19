#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scripts/check_coverage_regression.py` 行为测试（D1 交付物 · 2026-09-19）

覆盖 TASK-04 需要的"覆盖率不得下降"断言的**全部判定分支**，重点是负例：
人为把覆盖率调低 ⇒ 必须非零退出（否则这个断言只是一句摆设）。

Why 用 `main(argv)` 而不是 subprocess：本沙箱受限模式下管道会假挂死
（subprocess 的 capture_output 会卡在 communicate 的 join 上），
而 main() 返回的正是退出码，语义等价且不引入进程开销。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NINE = ["agent", "cognitive", "core", "lifetrace", "memory", "persona", "planning", "sensor", "utils"]


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_coverage_regression_under_test",
        REPO_ROOT / "scripts" / "check_coverage_regression.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_coverage_regression_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


CCR = _load()


def _write(path: Path, rate: float, packages=NINE, branches: int = 0, valid: int = 135272) -> Path:
    path.write_text(
        json.dumps(
            {
                "packages": list(packages),
                "line_rate": rate,
                "lines_covered": int(round(valid * rate)),
                "lines_valid": valid,
                "branch_rate": 0.0,
                "branches_valid": branches,
                "scope_label": "full-9-packages" if len(packages) > 1 else "subset-single-package",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


class TestExitCodes:
    """退出码契约：0 通过 / 1 回归 / 2 用法错误 / 3 口径不一致"""

    def test_no_change_passes(self, tmp_path, capsys):
        b = _write(tmp_path / "b.json", 0.7000)
        c = _write(tmp_path / "c.json", 0.7000)
        assert CCR.main(["--baseline", str(b), "--current", str(c)]) == 0

    def test_coverage_increase_passes(self, tmp_path):
        b = _write(tmp_path / "b.json", 0.7000)
        c = _write(tmp_path / "c.json", 0.7010)
        assert CCR.main(["--baseline", str(b), "--current", str(c)]) == 0

    def test_negative_case_artificially_lowered_coverage_exits_nonzero(self, tmp_path):
        """**负例**：人为降低覆盖率 ⇒ 必须非零退出。这是本脚本存在的理由。"""
        b = _write(tmp_path / "b.json", 0.7000)
        c = _write(tmp_path / "c.json", 0.6500)
        assert CCR.main(["--baseline", str(b), "--current", str(c)]) == 1

    def test_tiny_drop_below_zero_tolerance_fails(self, tmp_path):
        """默认容忍 0：哪怕只掉 0.01pp 也必须红（不给"慢慢烂下去"留口子）。"""
        b = _write(tmp_path / "b.json", 0.7000)
        c = _write(tmp_path / "c.json", 0.6999)
        assert CCR.main(["--baseline", str(b), "--current", str(c)]) == 1

    def test_drop_within_tolerance_passes(self, tmp_path):
        b = _write(tmp_path / "b.json", 0.7000)
        c = _write(tmp_path / "c.json", 0.6950)
        assert CCR.main(["--baseline", str(b), "--current", str(c), "--tolerance", "1"]) == 0

    def test_scope_mismatch_exits_3_not_0(self, tmp_path):
        """口径不同（9 包 vs 1 包）必须拒绝比较，而不是给出一个"看似合理"的差值。

        Why 这条最重要：历史 49.08%（9 包）与 77.20%（1 包）就是这样被误比的。
        """
        b = _write(tmp_path / "b.json", 0.7000, NINE)
        c = _write(tmp_path / "c.json", 0.8000, ["agent"])
        assert CCR.main(["--baseline", str(b), "--current", str(c)]) == 3

    def test_branch_toggle_exits_3(self, tmp_path):
        b = _write(tmp_path / "b.json", 0.7000, NINE, branches=0)
        c = _write(tmp_path / "c.json", 0.7000, NINE, branches=500)
        assert CCR.main(["--baseline", str(b), "--current", str(c)]) == 3

    def test_unknown_scope_exits_3(self, tmp_path):
        b = tmp_path / "b.json"
        b.write_text(json.dumps({"line_rate": 0.7, "lines_covered": 1, "lines_valid": 2}), encoding="utf-8")
        c = _write(tmp_path / "c.json", 0.7)
        assert CCR.main(["--baseline", str(b), "--current", str(c)]) == 3

    def test_missing_baseline_exits_2(self, tmp_path):
        c = _write(tmp_path / "c.json", 0.7)
        assert CCR.main(["--baseline", str(tmp_path / "nope.json"), "--current", str(c)]) == 2


class TestFailUnderFloor:
    """绝对下限（与 CI 的 --fail-under 同口径时才可用）"""

    def test_below_floor_fails_even_without_regression(self, tmp_path):
        b = _write(tmp_path / "b.json", 0.5000)
        c = _write(tmp_path / "c.json", 0.5000)
        assert CCR.main(["--baseline", str(b), "--current", str(c), "--fail-under", "60"]) == 1

    def test_above_floor_passes(self, tmp_path):
        b = _write(tmp_path / "b.json", 0.7000)
        c = _write(tmp_path / "c.json", 0.7000)
        assert CCR.main(["--baseline", str(b), "--current", str(c), "--fail-under", "40"]) == 0


class TestXmlScopeIsRefusedByDefault:
    """从 coverage.xml 的 <sources> 推断口径必须被默认拒绝"""

    def test_xml_baseline_exits_3_without_flag(self, tmp_path):
        xml = tmp_path / "cov.xml"
        xml.write_text(
            '<coverage version="7.15.4" lines-valid="100" lines-covered="80" line-rate="0.8" '
            'branches-covered="0" branches-valid="0" branch-rate="0"><sources>'
            + "".join(f"<source>C:/repo/{p}</source>" for p in NINE)
            + "</sources></coverage>",
            encoding="utf-8",
        )
        c = _write(tmp_path / "c.json", 0.8)
        assert CCR.main(["--baseline", str(xml), "--current", str(c)]) == 3

    def test_xml_with_explicit_allow_flag_compares(self, tmp_path):
        xml = tmp_path / "cov.xml"
        xml.write_text(
            '<coverage version="7.15.4" lines-valid="100" lines-covered="80" line-rate="0.8" '
            'branches-covered="0" branches-valid="0" branch-rate="0"><sources>'
            + "".join(f"<source>C:/repo/{p}</source>" for p in NINE)
            + "</sources></coverage>",
            encoding="utf-8",
        )
        c = _write(tmp_path / "c.json", 0.8)
        rc = CCR.main(["--baseline", str(xml), "--current", str(c), "--allow-inferred-scope"])
        assert rc == 0


class TestReportArtifact:
    """--report 必须落盘一份可机读判定（供 CI 归档 / 后续审计）"""

    def test_report_written_with_verdict(self, tmp_path):
        b = _write(tmp_path / "b.json", 0.7000)
        c = _write(tmp_path / "c.json", 0.6500)
        out = tmp_path / "report.json"
        rc = CCR.main(["--baseline", str(b), "--current", str(c), "--report", str(out)])
        assert rc == 1
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["verdict"] == "regression"
        assert data["scope_consistent"] is True
        assert data["delta_pp"] == pytest.approx(-5.0, abs=0.01)
