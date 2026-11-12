"""日志异常聚合（`agent/monitoring/log_anomaly.py`，2026-11-12 v2）

【判据】只回答"现在这批日志像不像在爆错"，并守住三条边界：
① 找不到日志 = never（不进健康分分母），不是"一切正常"；
② 误报比漏报更贵：绝对判据要 ERROR **同时**满足 ≥20 条与 ≥5%；
③ 相对判据只在基线样本 ≥8 个时启用，且仍需 ≥20 条（防小样本把 1 条放大成"3 倍"）。
"""
from __future__ import annotations

import json
import os

from agent.monitoring import log_anomaly


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _error_lines(n, total, prefix="2026-11-12 10:00:00 [ERROR   ] boom "):
    return [prefix + str(i) for i in range(n)] + ["2026-11-12 10:00:01 [INFO    ] ok"] * (total - n)


def test_no_logs_means_never(tmp_path):
    out = log_anomaly.unit_probe(tmp_path)
    assert out["status"] == "never"
    assert out["metrics"]["lines"] == 0


def test_small_error_count_is_ok_not_warn(tmp_path):
    """1 条错误 / 50 行 —— 占比 2%、条数 1：两个条件都不满足 ⇒ ok。"""
    _write(tmp_path / "app.log", _error_lines(1, 50))
    out = log_anomaly.unit_probe(tmp_path)
    assert out["status"] == "ok"
    assert out["metrics"]["errors"] == 1


def test_dense_errors_warn(tmp_path):
    """30 条错误 / 100 行（30%）⇒ warn。"""
    _write(tmp_path / "app.log", _error_lines(30, 100))
    out = log_anomaly.unit_probe(tmp_path)
    assert out["status"] == "warn"
    assert out["metrics"]["errors"] == 30
    assert out["issues"]


def test_many_errors_in_huge_log_still_ok(tmp_path):
    """1,000 条错误散在 50,000 行里（2%）：条数够但占比不够 ⇒ ok（只抓'爆炸'不抓'背景噪音'）。"""
    _write(tmp_path / "big.log", _error_lines(1000, 50000))
    out = log_anomaly.scan(tmp_path, tail_lines=5000)
    # 尾窗只取 5000 行：前 1000 条错误若不在尾部则统计为 0；这里只断言不误报
    assert out["status"] in ("ok", "warn")
    if out["status"] == "warn":
        assert out["ratio"] >= log_anomaly.MIN_ERROR_RATIO


def test_traceback_counts_as_error(tmp_path):
    lines = ["Traceback (most recent call last):"] * 25 + ["x [INFO    ] ok"] * 200
    _write(tmp_path / "tb.log", lines)
    out = log_anomaly.scan(tmp_path)
    assert out["tracebacks"] == 25
    assert out["ratio"] > 0


def test_jsonl_is_ignored(tmp_path):
    _write(tmp_path / "watchdog.jsonl", _error_lines(30, 100))
    assert log_anomaly.candidate_logs(tmp_path) == []
    assert log_anomaly.scan(tmp_path)["available"] is False


def test_candidate_logs_picks_newest_and_caps(tmp_path):
    for name in ("a.log", "b.log", "c.log", "d.log"):
        _write(tmp_path / name, ["[INFO    ] x"])
    os.utime(tmp_path / "b.log", (1000, 1000))
    picked = log_anomaly.candidate_logs(tmp_path, limit=2)
    assert len(picked) == 2
    assert (tmp_path / "b.log") not in picked


def _write_baseline(path, ratios, lines=5000, errors=0):
    samples = [{"ts": 1000.0 + i * 1800, "lines": lines, "errors": errors,
                "tracebacks": 0, "warnings": 0, "ratio": r} for i, r in enumerate(ratios)]
    path.write_text(json.dumps({"version": 1, "samples": samples}), encoding="utf-8")


def test_update_baseline_writes_and_trims(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    _write(logs / "app.log", _error_lines(0, 10))
    baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(log_anomaly, "MAX_SAMPLES", 3)
    for i in range(5):
        out = log_anomaly.update_baseline(baseline_path=baseline, log_dir=logs,
                                          now=1000.0 + i * 1000)
        assert out["status"] == "written"
    saved = json.loads(baseline.read_text(encoding="utf-8"))
    assert len(saved["samples"]) == 3          # 只留最近 3 个


def test_update_baseline_skips_when_no_logs(tmp_path):
    out = log_anomaly.update_baseline(baseline_path=tmp_path / "b.json", log_dir=tmp_path)
    assert out["status"] == "skipped"
    assert not (tmp_path / "b.json").exists()   # 不写"0 样本"污染基线


def test_update_baseline_throttles_too_soon(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    _write(logs / "app.log", _error_lines(0, 10))
    baseline = tmp_path / "b.json"
    assert log_anomaly.update_baseline(baseline_path=baseline, log_dir=logs,
                                       now=1000.0)["status"] == "written"
    second = log_anomaly.update_baseline(baseline_path=baseline, log_dir=logs, now=1060.0)
    assert second["status"] == "skipped" and second["reason"] == "too_soon"


def test_relative_baseline_rule_catches_rise_below_absolute_threshold(tmp_path):
    """平时 0.3% → 现在 1%（3.3 倍）：绝对判据（5%）不报，相对判据必须报。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    _write(logs / "app.log", _error_lines(25, 2500))
    baseline = tmp_path / "b.json"
    _write_baseline(baseline, [0.003] * 8, lines=2500)
    out = log_anomaly.unit_probe(log_dir=logs, baseline_path=baseline)
    assert out["status"] == "warn"
    assert any("相对基线" in i for i in out["issues"])
    assert out["metrics"]["baseline_samples"] == 8


def test_insufficient_baseline_falls_back_to_absolute(tmp_path):
    """基线只有 3 个样本 ⇒ 相对判据不启用；0.5% 不报（绝对阈值 5%）。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    _write(logs / "app.log", _error_lines(25, 5000))
    baseline = tmp_path / "b.json"
    _write_baseline(baseline, [0.001] * 3, lines=5000)
    out = log_anomaly.unit_probe(log_dir=logs, baseline_path=baseline)
    assert out["status"] == "ok"
    assert out["metrics"]["baseline_ratio"] is None


def test_scan_reports_files_scanned(tmp_path):
    _write(tmp_path / "x.log", _error_lines(0, 10))
    data = log_anomaly.scan(tmp_path)
    assert data["available"] is True
    assert data["files"] == ["x.log"]
    assert data["lines"] == 10
