"""TASK-S7-04 子项 A 单测 —— 性能预算补测脚本（`scripts/measure_perf_budget.py`）

验收对应（任务书 §四 子项 A 逐条）：

- 复测脚本**可重复运行**，输出**原始采样**（非估算/非引用历史）—— 断言 JSON 里有
  ``samples_ms`` 原文，而不是只有汇总值；
- 输出结构与**单位**正确（毫秒；``clock`` 口径字段显式声明）；
- **不可测项如实标注**：注入量未达阈值时不产生数字（``status="unmeasured"``、
  ``min/p50/p95/max`` 全 ``None``），**绝不填估算值**；
- 未删除任何既有指标项：脚本只覆盖 ``PERF_BUDGET_REBASED.md`` §五 仍标 ❓ 的两项。

【为什么用短注入量】真实补测用 3 s 持锁 × 20 次（见验收报告的原始数据）；
单测里把受控注入量压到毫秒级（``hold_ms=10`` / ``hold_seconds=0.05``），
用例断言的是**口径与结构**而非墙钟绝对值 —— 避免 CI 高负载下硬编码墙钟误判
（S3-01 §4.8 的实证教训）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "measure_perf_budget.py"


def _load():
    spec = importlib.util.spec_from_file_location("measure_perf_budget", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


MPB = _load()

#: JSON 采样的最小结构契约（每个测量块的汇总字段）
SUMMARY_FIELDS = ("n", "samples_ms", "min", "p50", "p95", "max", "mean")


# ════════════════════════════════════════════════════════════
#  1. 参数归一与统计工具
# ════════════════════════════════════════════════════════════


class TestItemsAndStats:
    def test_resolve_items_all_expands_every_measurement(self):
        assert MPB.resolve_items("all") == list(MPB.ITEMS)

    def test_resolve_items_watchdog_group_expands_to_two(self):
        assert MPB.resolve_items("watchdog") == ["watchdog-hold",
                                                 "watchdog-liveness"]

    def test_resolve_items_dedupes_and_preserves_order(self):
        assert MPB.resolve_items("circuit-breaker,circuit-breaker") == \
            ["circuit-breaker"]

    def test_resolve_items_rejects_unknown_explicitly(self):
        """未知项**显式报错**（不静默忽略掉一个没跑的指标）"""
        with pytest.raises(SystemExit) as exc:
            MPB.resolve_items("watchdog,does-not-exist")
        assert "未知项" in str(exc.value)

    def test_summarize_reports_raw_samples_and_percentiles(self):
        summary = MPB.summarize([3.0, 1.0, 2.0, 4.0])
        assert set(summary) == set(SUMMARY_FIELDS)
        assert summary["n"] == 4
        assert summary["samples_ms"] == [3.0, 1.0, 2.0, 4.0]   # **原始采样**保留原文
        assert summary["min"] == 1.0 and summary["max"] == 4.0
        assert summary["mean"] == 2.5

    def test_summarize_of_empty_is_all_none_not_zero(self):
        """无采样 ⇒ ``None``（**不以 0 冒充**，与 S5-02/S6-01 口径纪律一致）"""
        summary = MPB.summarize([])
        assert summary["n"] == 0 and summary["samples_ms"] == []
        for key in ("min", "p50", "p95", "max", "mean"):
            assert summary[key] is None

    def test_percentile_nearest_rank(self):
        values = [float(i) for i in range(1, 101)]
        assert MPB.percentile(values, 50) == 51.0
        assert MPB.percentile(values, 95) == 96.0
        assert MPB.percentile([], 95) == 0.0

    def test_environment_note_declares_clock_and_isolation(self):
        env = MPB.environment_note()
        assert env["clock"] == MPB.CLOCK_NOTE
        assert "perf_counter" in env["clock"]
        assert "不 kill 生产进程" in env["isolation"]
        assert env["python"] and env["platform"]

    def test_budget_lines_come_from_the_document(self):
        assert MPB.BUDGET_WATCHDOG_MS == 10_000.0     # §11.2 Watchdog <10s（硬性）
        assert MPB.BUDGET_CIRCUIT_MS == 3_000.0       # §11.2 熔断 <3s


# ════════════════════════════════════════════════════════════
#  2. 熔断「达到阈值 → 生效」
# ════════════════════════════════════════════════════════════


class TestCircuitBreakerMeasurement:
    def test_threshold_to_block_is_measured_and_actually_blocks(self):
        block = MPB.measure_circuit_breaker(repeat=3)
        assert block["status"] == "measured"
        assert block["budget_ms"] == MPB.BUDGET_CIRCUIT_MS
        assert block["clock"] == MPB.CLOCK_NOTE
        assert block["passed"] is True

        # 每次采样都真的"达阈值 → 状态 OPEN → 下一次 outbound 被拒"
        for run in block["runs"]:
            assert run["status"] == "measured"
            assert run["failures_until_open"] == 5      # min_requests 默认 5
            assert run["state_after_threshold"] == "open"
            assert run["outbound_actually_blocked"] is True
            assert run["outbound_block_latency_ms"] is not None
            assert run["outbound_block_latency_ms"] >= 0

        summary = block["outbound_block_latency"]
        assert summary["n"] == 3 and len(summary["samples_ms"]) == 3
        assert summary["max"] < MPB.BUDGET_CIRCUIT_MS
        # 状态跃迁与"阻断下一次调用"是两个语义，分开披露
        assert set(block["threshold_to_open"]) == set(SUMMARY_FIELDS)

    def test_min_requests_is_configurable(self):
        block = MPB.measure_circuit_breaker(repeat=1, min_requests=3,
                                            failure_threshold=1.0)
        assert block["runs"][0]["failures_until_open"] == 3

    def test_boundary_note_names_the_unmeasurable_part(self):
        """跨进程/集群下发时延**单机测不出** ⇒ 如实写明，不并入本数字"""
        block = MPB.measure_circuit_breaker(repeat=1)
        assert "集群环境" in block["boundary"]
        assert "单机测不出" in block["boundary"]


# ════════════════════════════════════════════════════════════
#  3. Watchdog 持锁超时感知（§五 指定方法）
# ════════════════════════════════════════════════════════════


class TestWatchdogHoldMeasurement:
    def test_hold_beyond_threshold_fires_and_is_timed(self):
        block = MPB.measure_watchdog_hold(repeat=3, hold_seconds=0.05, hold_ms=10)
        assert block["status"] == "measured"
        assert block["injected"] == {"hold_seconds": 0.05, "hold_threshold_ms": 10}
        for run in block["runs"]:
            assert run["alert_fired"] is True
            assert run["hold_to_alert_ms"] is not None
            assert run["release_to_alert_ms"] is not None
            # 端到端 ≥ 实际持锁时长（释放点判定的必然结果，如实记录）
            assert run["hold_to_alert_ms"] >= run["observed_hold_ms"] * 0.99
        assert block["hold_to_alert"]["n"] == 3
        assert block["passed"] is True

    def test_hold_below_threshold_yields_unmeasured_not_a_number(self):
        """**不编造**：注入量未达阈值 ⇒ 没有告警就**没有数字**"""
        block = MPB.measure_watchdog_hold(repeat=2, hold_seconds=0.01, hold_ms=10_000)
        assert block["status"] == "unmeasured"
        assert block["passed"] is False
        assert block["hold_to_alert"]["n"] == 0
        assert block["hold_to_alert"]["p95"] is None
        assert all(run["alert_fired"] is False for run in block["runs"])

    def test_boundary_discloses_release_time_detection(self):
        """口径边界必须显式：**永不释放**的持锁不会触发告警"""
        block = MPB.measure_watchdog_hold(repeat=1, hold_seconds=0.05, hold_ms=10)
        assert "释放点" in block["boundary"]
        assert "W-2" in block["boundary"]

    def test_overhead_is_measured_separately_from_hold_duration(self):
        block = MPB.measure_watchdog_hold(repeat=2, hold_seconds=0.05, hold_ms=10)
        # 纯判定开销 ≪ 持锁时长（否则说明我们把两件事混成了一个数字）
        assert block["release_to_alert_overhead"]["max"] < 100.0
        assert block["hold_to_alert"]["min"] >= 40.0


# ════════════════════════════════════════════════════════════
#  4. Watchdog 主进程失联感知（桩子进程；**不 kill 生产进程**）
# ════════════════════════════════════════════════════════════


class TestWatchdogLivenessMeasurement:
    @pytest.mark.timeout(180)
    def test_stub_child_loss_is_detected_and_timed(self):
        block = MPB.measure_watchdog_liveness(repeat=2, poll_interval_ms=50.0,
                                              timeout_seconds=20.0)
        assert block["status"] == "measured"
        assert block["budget_ms"] == MPB.BUDGET_WATCHDOG_MS
        for run in block["runs"]:
            assert run["status"] == "measured"
            assert run["detect_ms"] is not None and run["detect_ms"] >= 0
            # 关键对照：失联**之前**必须是"有活跃持有者"（否则测量没有意义）
            assert run["holder_alive_before_kill"] is True
        assert block["passed"] is True

    @pytest.mark.timeout(180)
    def test_stub_is_our_own_child_process_and_never_a_production_pid(self):
        """只在受控 tmp 目录建锁文件；被终止的是本脚本自起的桩进程"""
        import tempfile
        import os as _os
        workdir = tempfile.mkdtemp(prefix="cp_s704_test_stub_")
        run = MPB._measure_liveness_detection_once(
            poll_interval_ms=50.0, timeout_seconds=20.0, workdir=workdir)
        assert run["status"] == "measured"
        assert _os.path.exists(_os.path.join(workdir, "watchdog_probe.lock"))
        assert str(run["stub_pid"]).isdigit()

    @pytest.mark.timeout(180)
    def test_boundary_discloses_explicit_polling_semantics(self):
        """口径边界必须显式：`is_stale()` 是**显式调用**，不是"自动发现" """
        result = MPB.measure_watchdog_liveness(repeat=1, poll_interval_ms=50.0,
                                               timeout_seconds=20.0)
        assert "显式调用" in result["boundary"]
        assert "感知时间" in result["boundary"]
        assert result["injected"]["stub"].startswith("本脚本自起的桩子进程")


# ════════════════════════════════════════════════════════════
#  5. 报告编队 / 落盘 / 渲染
# ════════════════════════════════════════════════════════════


class TestReportAssembly:
    def test_run_measurement_shape_and_metadata(self):
        report = MPB.run_measurement(["circuit-breaker"], repeat=2,
                                     hold_seconds=0.05, poll_interval_ms=50.0,
                                     timeout_seconds=20.0)
        assert report["task"] == "TASK-S7-04" and report["subtask"].startswith("A ")
        assert report["clock"] == MPB.CLOCK_NOTE
        assert report["repeat"] == 2
        assert set(report["environment"]) >= {"platform", "python", "cpu_count",
                                              "clock", "isolation"}
        assert set(report["results"]) == {"circuit-breaker"}

    def test_run_measurement_rejects_unknown_item(self):
        with pytest.raises(SystemExit):
            MPB.run_measurement(["nope"], repeat=1, hold_seconds=0.05,
                                poll_interval_ms=50.0, timeout_seconds=20.0)

    @pytest.mark.timeout(180)
    def test_run_measurement_covers_all_three_items(self):
        report = MPB.run_measurement(list(MPB.ITEMS), repeat=1, hold_seconds=0.05,
                                     poll_interval_ms=50.0, timeout_seconds=20.0,
                                     hold_ms=10)
        assert set(report["results"]) == set(MPB.ITEMS)
        for name, block in report["results"].items():
            assert block["status"] == "measured", name
            assert block["clock"] == MPB.CLOCK_NOTE

    def test_render_text_marks_pass_and_fail(self):
        report = MPB.run_measurement(["circuit-breaker"], repeat=1,
                                     hold_seconds=0.05, poll_interval_ms=50.0,
                                     timeout_seconds=20.0)
        text = MPB.render_text(report)
        assert "TASK-S7-04" in text and "达标" in text
        assert "outbound 实际阻断" in text

    def test_main_writes_json_and_returns_zero(self, tmp_path, capsys):
        out = tmp_path / "perf_budget_probe.json"
        code = MPB.main(["--items", "circuit-breaker", "--repeat", "2",
                         "--out", str(out), "--json"])
        assert code == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["results"]["circuit-breaker"]["status"] == "measured"
        # --json 只输出 JSON（stdout 可被机器消费）
        stdout = capsys.readouterr().out
        assert json.loads(stdout)["task"] == "TASK-S7-04"

    def test_main_text_mode_points_at_raw_samples(self, tmp_path, capsys):
        out = tmp_path / "probe.json"
        assert MPB.main(["--items", "circuit-breaker", "--repeat", "1",
                         "--out", str(out)]) == 0
        text = capsys.readouterr().out
        assert "原始采样 JSON" in text
        assert out.exists()

    def test_stub_holder_is_internal_only(self):
        """桩模式是内部实现细节：`--stub-holder` 不出现在常规用法说明中"""
        source = _SCRIPT.read_text(encoding="utf-8")
        assert "argparse.SUPPRESS" in source       # 该参数对 --help 隐藏
        assert callable(MPB._stub_holder)
