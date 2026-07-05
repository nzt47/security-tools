"""agent.p6 包单元测试

覆盖：
- frequency.py: SnapshotFrequencyController / _safe_call
- performance.py: SnapshotPerformanceMonitor / PerformanceMetrics / _safe_call
- observability.py: trackEvent / _emit_structured_log / _trace_id
- snapshot.py: SnapshotResult / SnapshotInfo / ModuleState / StateSnapshot / StateSnapshotManager 基础方法

状态同步机制：使用 tmp_path 隔离快照目录，Mock DigitalLife 实例和外部依赖。
"""
import time
import json
from datetime import datetime
from unittest import mock

import pytest

# ── frequency.py 测试 ──

from agent.p6.frequency import SnapshotFrequencyController, _safe_call as freq_safe_call


class TestSnapshotFrequencyController:
    """SnapshotFrequencyController 频率控制"""

    def test_initial_state(self):
        ctrl = SnapshotFrequencyController(min_interval_seconds=300, max_snapshots=5)
        assert ctrl.min_interval_seconds == 300
        assert ctrl.max_snapshots == 5
        assert ctrl.last_save_time == 0.0
        assert ctrl.save_count == 0

    def test_can_save_initially(self):
        ctrl = SnapshotFrequencyController(min_interval_seconds=300)
        assert ctrl.can_save() is True

    def test_can_save_force(self):
        ctrl = SnapshotFrequencyController(min_interval_seconds=300)
        ctrl.on_save_success()
        assert ctrl.can_save(force=True) is True

    def test_cannot_save_within_interval(self):
        ctrl = SnapshotFrequencyController(min_interval_seconds=300)
        ctrl.on_save_success()
        assert ctrl.can_save() is False

    def test_can_save_after_interval(self):
        ctrl = SnapshotFrequencyController(min_interval_seconds=0.1)
        ctrl.on_save_success()
        time.sleep(0.15)
        assert ctrl.can_save() is True

    def test_on_save_success_updates_state(self):
        ctrl = SnapshotFrequencyController(min_interval_seconds=300)
        ctrl.on_save_success()
        assert ctrl.last_save_time > 0
        assert ctrl.save_count == 1

    def test_on_save_success_increments_count(self):
        ctrl = SnapshotFrequencyController(min_interval_seconds=0)
        for _ in range(5):
            ctrl.on_save_success()
        assert ctrl.save_count == 5

    def test_custom_parameters(self):
        ctrl = SnapshotFrequencyController(min_interval_seconds=60, max_snapshots=10)
        assert ctrl.min_interval_seconds == 60
        assert ctrl.max_snapshots == 10

    def test_zero_interval_always_allowed(self):
        ctrl = SnapshotFrequencyController(min_interval_seconds=0)
        ctrl.on_save_success()
        assert ctrl.can_save() is True


class TestFreqSafeCall:
    """frequency._safe_call 安全调用包装器"""

    def test_returns_result_on_success(self):
        assert freq_safe_call(lambda x: x * 2, 5) == 10

    def test_reraises_on_exception(self):
        with pytest.raises(ValueError):
            freq_safe_call(lambda: (_ for _ in ()).throw(ValueError("test")))

    def test_logs_error_on_exception(self, caplog):
        with caplog.at_level("ERROR", logger="agent.p6.frequency"):
            with pytest.raises(ZeroDivisionError):
                freq_safe_call(lambda: 1 / 0, action="divide")
        assert any("divide.failed" in r.message for r in caplog.records)


# ── performance.py 测试 ──

from agent.p6.performance import (
    PerformanceMetrics,
    SnapshotPerformanceMonitor,
    _safe_call as perf_safe_call,
)


class TestPerformanceMetrics:
    """PerformanceMetrics 数据类"""

    def test_default_values(self):
        m = PerformanceMetrics()
        assert m.total_saves == 0
        assert m.total_loads == 0
        assert m.total_save_time_ms == 0.0
        assert m.avg_save_time_ms == 0.0
        assert m.module_serialize_times == {}

    def test_custom_values(self):
        m = PerformanceMetrics(total_saves=10, total_loads=5, avg_save_time_ms=42.0)
        assert m.total_saves == 10
        assert m.total_loads == 5
        assert m.avg_save_time_ms == 42.0

    def test_module_dicts_are_independent(self):
        m1 = PerformanceMetrics()
        m2 = PerformanceMetrics()
        m1.module_serialize_times["a"] = 1.0
        assert "a" not in m2.module_serialize_times


class TestSnapshotPerformanceMonitor:
    """SnapshotPerformanceMonitor 性能监控"""

    def test_init(self):
        mon = SnapshotPerformanceMonitor()
        assert mon.metrics.total_saves == 0
        assert mon.metrics.total_loads == 0
        assert mon.start_time > 0

    def test_record_save(self):
        mon = SnapshotPerformanceMonitor()
        mon.record_save(elapsed_ms=100.0, space_saved=1024)
        assert mon.metrics.total_saves == 1
        assert mon.metrics.total_save_time_ms == 100.0
        assert mon.metrics.avg_save_time_ms == 100.0
        assert mon.metrics.last_save_time_ms == 100.0
        assert mon.metrics.total_space_saved_bytes == 1024
        assert mon.metrics.snapshot_count == 1

    def test_record_save_multiple(self):
        mon = SnapshotPerformanceMonitor()
        mon.record_save(100.0, 500)
        mon.record_save(200.0, 300)
        assert mon.metrics.total_saves == 2
        assert mon.metrics.total_save_time_ms == 300.0
        assert mon.metrics.avg_save_time_ms == 150.0
        assert mon.metrics.total_space_saved_bytes == 800

    def test_record_load(self):
        mon = SnapshotPerformanceMonitor()
        mon.record_load(50.0)
        assert mon.metrics.total_loads == 1
        assert mon.metrics.total_load_time_ms == 50.0
        assert mon.metrics.avg_load_time_ms == 50.0
        assert mon.metrics.last_load_time_ms == 50.0

    def test_record_load_multiple(self):
        mon = SnapshotPerformanceMonitor()
        mon.record_load(100.0)
        mon.record_load(200.0)
        assert mon.metrics.total_loads == 2
        assert mon.metrics.avg_load_time_ms == 150.0

    def test_record_module_serialize(self):
        mon = SnapshotPerformanceMonitor()
        mon.record_module_serialize("memory", 10.0, 4096)
        assert mon.metrics.module_serialize_times["memory"] == 10.0
        assert mon.metrics.module_data_sizes["memory"] == 4096

    def test_record_module_deserialize(self):
        mon = SnapshotPerformanceMonitor()
        mon.record_module_deserialize("memory", 8.0)
        assert mon.metrics.module_deserialize_times["memory"] == 8.0

    def test_get_performance_summary(self):
        mon = SnapshotPerformanceMonitor()
        mon.record_save(100.0, 1024)
        mon.record_load(50.0)
        mon.record_module_serialize("core", 5.0, 2048)
        summary = mon.get_performance_summary()
        assert summary["total_saves"] == 1
        assert summary["total_loads"] == 1
        assert summary["avg_save_ms"] == 100.0
        assert summary["total_space_saved_bytes"] == 1024
        assert "core" in summary["module_stats"]
        assert summary["module_stats"]["core"]["serialize_ms"] == 5.0
        assert summary["module_stats"]["core"]["size_bytes"] == 2048

    def test_get_performance_summary_empty(self):
        mon = SnapshotPerformanceMonitor()
        summary = mon.get_performance_summary()
        assert summary["total_saves"] == 0
        assert summary["total_loads"] == 0
        assert summary["module_stats"] == {}

    def test_print_performance_panel(self, capsys):
        mon = SnapshotPerformanceMonitor()
        mon.record_save(100.0, 1024)
        mon.print_performance_panel()
        captured = capsys.readouterr()
        assert "P6" in captured.out
        assert "性能" in captured.out


class TestPerfSafeCall:
    """performance._safe_call"""

    def test_returns_result(self):
        assert perf_safe_call(lambda: 42) == 42

    def test_reraises_exception(self):
        with pytest.raises(ValueError):
            perf_safe_call(lambda: int("abc"))


# ── observability.py 测试 ──

from agent.p6 import observability as p6_obs


class TestP6Observability:
    """p6.observability 埋点模块"""

    def test_trace_id_length(self):
        tid = p6_obs._trace_id()
        assert isinstance(tid, str)
        assert len(tid) == 16

    def test_trace_id_unique(self):
        ids = {p6_obs._trace_id() for _ in range(100)}
        assert len(ids) == 100

    def test_emit_structured_log(self, caplog):
        with caplog.at_level("INFO", logger="agent.p6"):
            p6_obs._emit_structured_log("snapshot_save", duration_ms=42.5)
        assert any("snapshot_save" in r.message for r in caplog.records)
        assert any("p6" in r.message for r in caplog.records)

    def test_emit_structured_log_with_trace_id(self, caplog):
        with caplog.at_level("INFO", logger="agent.p6"):
            p6_obs._emit_structured_log("act", trace_id="fixed-tid", duration_ms=10)
        assert any("fixed-tid" in r.message for r in caplog.records)

    def test_emit_structured_log_warning_level(self, caplog):
        with caplog.at_level("WARNING", logger="agent.p6"):
            p6_obs._emit_structured_log("warn_act", level="warning")
        assert any("warn_act" in r.message for r in caplog.records)

    def test_track_event_basic(self, caplog):
        with caplog.at_level("INFO", logger="agent.p6"):
            p6_obs.trackEvent("snapshot_created", {"snapshot_id": "snap001"})
        assert any("track.snapshot_created" in r.message for r in caplog.records)

    def test_track_event_no_payload(self, caplog):
        with caplog.at_level("INFO", logger="agent.p6"):
            p6_obs.trackEvent("simple")
        assert any("track.simple" in r.message for r in caplog.records)

    def test_track_event_reserved_keys_filtered(self, caplog):
        with caplog.at_level("INFO", logger="agent.p6"):
            p6_obs.trackEvent("evt", {
                "action": "filtered",
                "module_name": "filtered",
                "custom": "kept",
            })
        msgs = " ".join(r.message for r in caplog.records)
        assert "kept" in msgs
        assert "filtered" not in msgs

    def test_track_event_no_raise_on_error(self):
        with mock.patch.object(p6_obs, "_emit_structured_log", side_effect=Exception("boom")):
            p6_obs.trackEvent("fail_test")


# ── snapshot.py dataclass 测试 ──

from agent.p6.snapshot import (
    SnapshotResult,
    SnapshotInfo,
    ModuleState,
    StateSnapshot,
    StateSnapshotManager,
)


class TestSnapshotResult:
    """SnapshotResult 数据类"""

    def test_default_values(self):
        r = SnapshotResult(success=True)
        assert r.success is True
        assert r.snapshot_id is None
        assert r.version == ""
        assert r.elapsed_ms == 0.0
        assert r.error_message is None
        assert r.is_incremental is False
        assert r.base_snapshot_id is None
        assert r.space_saved_bytes == 0
        assert r.file_size == 0
        assert r.created_at is None

    def test_custom_values(self):
        now = datetime.now()
        r = SnapshotResult(
            success=True,
            snapshot_id="snap_001",
            version="p6.2.0",
            elapsed_ms=42.5,
            is_incremental=True,
            base_snapshot_id="snap_000",
            space_saved_bytes=1024,
            file_size=2048,
            created_at=now,
        )
        assert r.snapshot_id == "snap_001"
        assert r.is_incremental is True
        assert r.created_at == now

    def test_failure_result(self):
        r = SnapshotResult(success=False, error_message="IOError")
        assert r.success is False
        assert r.error_message == "IOError"


class TestSnapshotInfo:
    """SnapshotInfo 数据类"""

    def test_creation(self):
        now = datetime.now()
        info = SnapshotInfo(
            snapshot_id="snap_001",
            created_at=now,
            version="p6.2.0",
            file_size=4096,
        )
        assert info.snapshot_id == "snap_001"
        assert info.created_at == now
        assert info.version == "p6.2.0"
        assert info.file_size == 4096
        assert info.is_incremental is False
        assert info.base_snapshot_id is None

    def test_incremental_info(self):
        info = SnapshotInfo(
            snapshot_id="snap_002",
            created_at=datetime.now(),
            version="p6.2.0",
            file_size=1024,
            is_incremental=True,
            base_snapshot_id="snap_001",
        )
        assert info.is_incremental is True
        assert info.base_snapshot_id == "snap_001"


class TestModuleState:
    """ModuleState 数据类"""

    def test_default_values(self):
        state = ModuleState(
            module_name="memory",
            initialized=True,
            state_data=b"\x80\x04\x95",
        )
        assert state.module_name == "memory"
        assert state.initialized is True
        assert state.state_data == b"\x80\x04\x95"
        assert state.restore_priority == 0
        assert state.checksum == ""
        assert state.changed is True

    def test_custom_values(self):
        state = ModuleState(
            module_name="core",
            initialized=False,
            state_data=b"",
            restore_priority=5,
            checksum="abc123",
            changed=False,
        )
        assert state.restore_priority == 5
        assert state.checksum == "abc123"
        assert state.changed is False


class TestStateSnapshot:
    """StateSnapshot 数据类"""

    def test_default_values(self):
        snap = StateSnapshot(
            snapshot_id="snap_001",
            created_at=datetime.now(),
        )
        assert snap.snapshot_id == "snap_001"
        assert snap.version == "p6.2.0"
        assert snap.config == {}
        assert snap.module_states == {}
        assert snap.lazy_cache == {}
        assert snap.performance_stats == {}
        assert snap.is_incremental is False
        assert snap.base_snapshot_id is None

    def test_compute_checksum(self):
        snap = StateSnapshot(
            snapshot_id="snap_001",
            created_at=datetime.now(),
            config={"key": "value"},
        )
        checksum = snap.compute_checksum()
        assert isinstance(checksum, str)
        assert len(checksum) == 64  # SHA-256 hex

    def test_compute_checksum_deterministic(self):
        snap = StateSnapshot(
            snapshot_id="snap_001",
            created_at=datetime(2026, 1, 1),
            version="1.0",
            config={"a": 1},
        )
        c1 = snap.compute_checksum()
        c2 = snap.compute_checksum()
        assert c1 == c2

    def test_compute_checksum_differs_for_different_data(self):
        snap1 = StateSnapshot(
            snapshot_id="s1",
            created_at=datetime(2026, 1, 1),
            config={"a": 1},
        )
        snap2 = StateSnapshot(
            snapshot_id="s2",
            created_at=datetime(2026, 1, 1),
            config={"a": 2},
        )
        assert snap1.compute_checksum() != snap2.compute_checksum()


class TestStateSnapshotManager:
    """StateSnapshotManager 快照管理器"""

    def test_init_creates_dir(self, tmp_path):
        snap_dir = str(tmp_path / "snapshots")
        mgr = StateSnapshotManager(snapshot_dir=snap_dir)
        assert os.path.exists(snap_dir)

    def test_init_default_settings(self, tmp_path):
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snaps"))
        assert mgr.enable_compression is True
        assert mgr.current_snapshot is None
        assert mgr.last_module_checksums == {}

    def test_generate_snapshot_id_unique(self, tmp_path):
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snaps"))
        id1 = mgr._generate_snapshot_id()
        time.sleep(0.001)
        id2 = mgr._generate_snapshot_id()
        assert id1 != id2
        assert id1.startswith("snap_")

    def test_get_snapshot_path(self, tmp_path):
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snaps"))
        path = mgr._get_snapshot_path("snap_001")
        assert "snap_001" in str(path)

    def test_get_snapshot_path_incremental(self, tmp_path):
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snaps"))
        path = mgr._get_snapshot_path("snap_002", is_incremental=True)
        assert "incremental" in str(path)

    def test_ensure_snapshot_dir(self, tmp_path):
        nested = str(tmp_path / "a" / "b" / "c")
        mgr = StateSnapshotManager(snapshot_dir=nested)
        assert os.path.exists(nested)

    def test_list_snapshots_empty(self, tmp_path):
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snaps"))
        snapshots = mgr.list_snapshots()
        assert snapshots == []

    def test_cleanup_snapshots_empty(self, tmp_path):
        mgr = StateSnapshotManager(snapshot_dir=str(tmp_path / "snaps"))
        result = mgr.cleanup_snapshots(keep_count=3)
        assert isinstance(result, int)


import os
