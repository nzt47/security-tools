# -*- coding: utf-8 -*-
"""`scripts/check_baseline_regression.py` 的契约测试（2026-09-28 新增）

【本文件证明什么】
  1. **跑完才算数**：pytest 被 timeout/OOM 打断时，日志里没有结束摘要 ⇒ 脚本必须**拒绝判定**
     （退出码 2），而不是把「空失败集合」当成「零失败 ⇒ 全绿」。这是本机实测踩到的假绿路径：
     `python -m pytest tests/unit --timeout=300` 在 76% 处被全仓 AST 扫描类用例超时打断，
     pytest-timeout 的 thread 方法 dump 栈后 `os._exit`，既不写摘要也不写 junitxml。
  2. 集合语义不变：新失败 ⇒ 退出码 1；基线内失败 ⇒ 0；`--update` 拒绝把新失败写进基线。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "check_baseline_regression", _ROOT / "scripts" / "check_baseline_regression.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


#: 被 pytest-timeout 打断时的真实日志形态（栈 + Timeout 横幅，**没有**结束摘要）
_KILLED_LOG = "\n".join([
    "============================= test session starts =============================",
    "collected 22637 items",
    "",
    "tests/unit/test_settings_registry.py F",
    "  File tests/unit/test_settings_registry.py, line 159, in scan",
    "    return _production_scan()",
    "  File scripts/scan_settings.py, line 396, in _assign_targets",
    "    for node in ast.walk(tree):",
    "+++++++++++++++++++++++++++++++++++ Timeout +++++++++++++++++++++++++++++++++++",
    "",
])

#: 正常跑完的日志形态（短摘要 + 结束摘要都在）
_COMPLETE_LOG = "\n".join([
    "============================= test session starts =============================",
    "collected 3 items",
    "",
    "tests/unit/test_a.py .",
    "=========================== short test summary info ============================",
    "FAILED tests/unit/test_a.py::test_b - AssertionError: assert 1 == 2",
    "========================= 2 passed, 1 failed, 9 warnings in 12.34s ============",
    "",
])


def test_没有结束摘要时拒绝判定(tmp_path, capsys):
    """被 timeout 打断的日志 ⇒ 退出码 2，且**不得**报成「无新增失败」"""
    mod = _load_module()
    log = tmp_path / "killed.txt"
    log.write_text(_KILLED_LOG, encoding="utf-8")
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("# 空基线\n", encoding="utf-8")

    rc = mod.main(["--pytest-log", str(log), "--baseline", str(baseline)])

    out = capsys.readouterr().out
    assert rc == 2, "没跑完的日志必须拒绝判定，实际退出码 %d" % rc
    assert "没有结束摘要" in out
    assert "拒绝判定" in out
    assert "无新增失败" not in out, "不得把「没跑完」报成「无新增失败」"


def test_正常日志里新失败退出码为1(tmp_path, capsys):
    mod = _load_module()
    log = tmp_path / "run.txt"
    log.write_text(_COMPLETE_LOG, encoding="utf-8")
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("# 空基线\n", encoding="utf-8")

    rc = mod.main(["--pytest-log", str(log), "--baseline", str(baseline)])
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "test_a.py::test_b" in out


def test_基线内的失败退出码为0(tmp_path):
    mod = _load_module()
    log = tmp_path / "run.txt"
    log.write_text(_COMPLETE_LOG, encoding="utf-8")
    baseline = tmp_path / "baseline.txt"
    baseline.write_text(
        "FAILED tests/unit/test_a.py::test_b - AssertionError\n", encoding="utf-8")

    assert mod.main(["--pytest-log", str(log), "--baseline", str(baseline)]) == 0


def test_update拒绝把新失败写进基线(tmp_path):
    """--update 只允许收缩：有新增失败时必须拒绝（退出码 1）且不改基线文件"""
    mod = _load_module()
    log = tmp_path / "run.txt"
    log.write_text(_COMPLETE_LOG, encoding="utf-8")
    baseline = tmp_path / "baseline.txt"
    original = "# 空基线\n"
    baseline.write_text(original, encoding="utf-8")

    rc = mod.main(["--pytest-log", str(log), "--baseline", str(baseline), "--update"])
    assert rc == 1
    assert baseline.read_text(encoding="utf-8") == original, "拒绝时不得改写基线"


def test_没有结束摘要时即使给了update也拒绝(tmp_path):
    """没跑完的日志连 --update 都不能用（否则会把「空集合」写成新基线）"""
    mod = _load_module()
    log = tmp_path / "killed.txt"
    log.write_text(_KILLED_LOG, encoding="utf-8")
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("# 空基线\n", encoding="utf-8")

    assert mod.main(["--pytest-log", str(log), "--baseline", str(baseline),
                     "--update"]) == 2
    assert baseline.read_text(encoding="utf-8") == "# 空基线\n"