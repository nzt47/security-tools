"""`sensor/change_detector.py` 的分片快照（2026-11-12）

【为什么要有分片】首版每 30 分钟全量采一次，实测阻塞调度线程 25–29s（task_history 里
43499ms 峰值），同线程的「系统心跳」被顶成 90s 一次。分片让"便宜组每 30 分钟、贵组每小时"
成为可能 —— 这里钉住三件事：
① 只采指定分片时，其它分片沿用上次值（不会伪造"没变/变了"）；
② 未知分片名**直接抛 ValueError**（漏采等于悄悄伪造"没有变化"）；
③ 只给分片子集时，未被要求的分片方法**根本不会被调用**（证明真的省了那几秒）。
"""
from __future__ import annotations

import pytest

from sensor.change_detector import SNAPSHOT_SECTIONS, ChangeDetector


@pytest.fixture()
def cd(tmp_path):
    return ChangeDetector(persistent_log_dir=str(tmp_path))


def test_capture_snapshot_with_sections_only_has_those_keys(cd):
    snap = cd._capture_snapshot(sections=["processes", "system_info"])
    assert set(snap) == {"timestamp", "processes", "system_info", "hash"}
    assert isinstance(snap["hash"], str) and len(snap["hash"]) == 16


def test_capture_snapshot_unknown_section_raises(cd):
    with pytest.raises(ValueError):
        cd._capture_snapshot(sections=["processes", "no_such_section"])


def test_collect_partial_sections_merges_and_skips_expensive(cd, monkeypatch):
    calls = {"devices": 0, "processes": 0}

    def _devices():
        calls["devices"] += 1
        return {"k": "v"}

    def _processes():
        calls["processes"] += 1
        return [{"pid": 1, "name": "a", "status": "running"}]

    monkeypatch.setattr(cd, "_list_devices", _devices)
    monkeypatch.setattr(cd, "_list_disk_partitions", lambda: {})
    monkeypatch.setattr(cd, "_list_processes", _processes)
    monkeypatch.setattr(cd, "_list_services",
                        lambda: [{"name": "S", "state": "Running", "start_mode": "Auto"}])
    monkeypatch.setattr(cd, "_get_system_info", lambda: {"platform": "x"})
    monkeypatch.setattr(cd, "_capture_registry", lambda: {})
    monkeypatch.setattr(cd, "_capture_environment", lambda: {})

    # 首轮：基准是全量的（会调设备）
    cd.set_baseline()
    assert calls == {"devices": 1, "processes": 1}

    # 只刷进程：设备方法**不得**被调用，且设备分片沿用旧值
    monkeypatch.setattr(cd, "_list_processes",
                        lambda: [{"pid": 1, "name": "b", "status": "running"}])
    changes = cd.collect(sections=["processes"])
    assert calls["devices"] == 1              # ★ 设备没被再采
    assert cd._last_check["devices"] == {"k": "v"}   # 沿用旧值
    assert any((c.metadata or {}).get("change_type") == "process_started" for c in changes)

    # 再刷一次"只有服务"（只改状态）：进程分片沿用，不会因为进程没刷新而报"进程全消失"；
    # 注：_diff_services 只报"两边都存在但状态变了"的服务（新增服务不报，历史口径）
    monkeypatch.setattr(cd, "_list_services",
                        lambda: [{"name": "S", "state": "Stopped", "start_mode": "Auto"}])
    changes2 = cd.collect(sections=["services"])
    assert [(c.metadata or {}).get("change_type") for c in changes2] == ["service_state_changed"]


def test_collect_full_snapshot_when_sections_none(cd, monkeypatch):
    """sections=None 必须与历史行为逐字一致（全量 7 个分片）。"""
    snap = cd._capture_snapshot()
    assert set(snap) == set(SNAPSHOT_SECTIONS) | {"timestamp", "hash"}
