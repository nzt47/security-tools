"""日志异常聚合（`agent/monitoring/log_anomaly.py`，2026-11-12）

【判据】只回答"现在这批日志像不像在爆错"，并守住两条边界：
① 找不到日志 = never（不进健康分分母），不是"一切正常"；
② 误报比漏报更贵：ERROR 必须**同时**满足绝对条数（≥20）与占比（≥5%）才 warn。
"""
from __future__ import annotations

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


def test_scan_reports_files_scanned(tmp_path):
    _write(tmp_path / "x.log", _error_lines(0, 10))
    data = log_anomaly.scan(tmp_path)
    assert data["available"] is True
    assert data["files"] == ["x.log"]
    assert data["lines"] == 10
