"""`agent/learning/perception_cycle.py` —— 感知周期采集（2026-11-12）

【为什么要有这批用例】
本模块补的是"感知面没有周期调用方"这个真空：FileWatcher 的事件没人 drain、
ChangeDetector.collect() 没人按周期跑。判据必须钉住三件事：
① 文件事件被聚合成**一条** `file_change`（不是逐条灌记忆，否则一次批量导入刷几百行）；
② 快照 diff **有节流**（WMI 重活，每 5 分钟跑一次会变成噪音源）；
③ 没有 BodySensor / 读取失败时**不抛异常**（调度器 python_func 只看异常判失败，
   抛一次就会让报警器每轮记一条"计划任务失败"）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.learning import perception_cycle as pc


#: 测试用基准时刻（2027-01-15 前后）：**不要**用 1000 这种小 epoch ——
#: Windows 上 datetime.fromtimestamp(1000 - 8h) 是负数，会直接抛 OSError。
NOW = 1_800_000_000.0


def _utc(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


class _Reading:
    """够用的 SensorReading 替身（只用到 value / metadata）。"""

    def __init__(self, value: str, event_type: str, is_dir: bool = False):
        self.value = value
        self.metadata = {"event_type": event_type, "is_directory": is_dir,
                         "src_path": value}


class _Watcher:
    def __init__(self, readings=None, running: bool = True):
        self.readings = list(readings or [])
        self.is_running = running
        self.watched_dirs = ["/tmp/knowledge"]

    def collect(self):
        out, self.readings = self.readings, []
        return out


class _Detector:
    def __init__(self, changes=None):
        self.changes = list(changes or [])
        self.calls = 0
        self.sections = []          # 每次 collect 传进来的分片（None = 全量）

    def collect(self, sections=None):
        self.calls += 1
        self.sections.append(None if sections is None else list(sections))
        out, self.changes = self.changes, []
        return out


class _Body:
    def __init__(self, watcher=None, detector=None):
        self.file_watcher = watcher
        self.change_detector = detector
        self.ensured = 0

    def ensure_passive_sensors(self):
        self.ensured += 1
        return {"change_detector": self.change_detector is not None,
                "file_watcher": self.file_watcher is not None}


class _Scheduler:
    def __init__(self):
        self.tasks = []

    def add_interval_task(self, name, func, interval_seconds, **kw):
        self.tasks.append({"name": name, "func": func, "interval": interval_seconds,
                           "task_id": "py_%d" % len(self.tasks), **kw})

    def remove_task(self, task_id):
        before = len(self.tasks)
        self.tasks = [t for t in self.tasks if t.get("task_id") != task_id]
        return len(self.tasks) < before


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """每个用例从"刚启动"状态开始（模块级 _state 会跨用例残留）。"""
    monkeypatch.delenv(pc.ENV_ENABLED, raising=False)
    monkeypatch.delenv(pc.ENV_TICK_SECONDS, raising=False)
    monkeypatch.delenv(pc.ENV_CHANGE_SECONDS, raising=False)
    pc._state.update({
        "ticks": 0, "last_tick_at": None, "last_file_event_at": None,
        "last_change_at": None, "last_change_detect_at": None,
        "last_slow_detect_at": None,
        "file_events_total": 0, "change_events_total": 0,
    })
    pc._body = None
    yield
    pc._body = None


def _hook(captured):
    def hook(changes):
        captured.extend(changes)
    return hook


def test_tick_aggregates_file_events_into_one_file_change(tmp_path):
    captured = []
    watcher = _Watcher([_Reading("/k/a.md", "created"), _Reading("/k/b.md", "modified")])
    body = _Body(watcher=watcher, detector=_Detector())
    status = tmp_path / "perception_status.json"

    out = pc.run_perception_tick(body=body, now=NOW, hook=_hook(captured),
                                 status_path=status)

    assert out["status"] == "ok"
    assert out["file_events"] == 2
    assert out["changed"] == 1                       # ★ 两条事件 → 一条变更
    assert len(captured) == 1
    change = captured[0]
    assert change["type"] == "file_changed"          # 混合类型 → 批量类型
    assert change["detail"]["by_type"] == {"file_added": 1, "file_modified": 1}
    assert "文件变更 2 项" in change["description"]
    assert body.ensured == 1                         # 被动传感器被要求就绪

    saved = json.loads(status.read_text(encoding="utf-8"))
    assert saved["watcher_running"] is True
    assert saved["file_events"] == 2
    assert saved["watch_dirs"] == ["/tmp/knowledge"]
    assert saved["last_file_event_at"] is not None


def test_single_event_keeps_its_own_type_and_delete_is_warning(tmp_path):
    captured = []
    body = _Body(watcher=_Watcher([_Reading("/k/x.md", "deleted")]),
                 detector=_Detector())
    pc.run_perception_tick(body=body, now=NOW, hook=_hook(captured),
                           status_path=tmp_path / "s.json")
    assert captured[0]["type"] == "file_removed"
    assert captured[0]["severity"] == "warning"


def test_change_detect_is_throttled(tmp_path):
    """★ 快照 diff 默认 30 分钟一次：WMI 设备/进程枚举不该跟着 5 分钟 tick 跑"""
    detector = _Detector([{"name": "change_process_started", "type": "process_started",
                           "description": "新进程启动: x"}])
    body = _Body(watcher=_Watcher([]), detector=detector)
    status = tmp_path / "s.json"

    first = pc.run_perception_tick(body=body, now=NOW, hook=lambda c: None,
                                   status_path=status)
    assert first["diff_ran"] is True and detector.calls == 1

    second = pc.run_perception_tick(body=body, now=NOW + 60.0, hook=lambda c: None,
                                    status_path=status)
    assert second["diff_ran"] is False and detector.calls == 1   # 未到节流窗口

    third = pc.run_perception_tick(
        body=body, now=NOW + pc.change_detect_seconds() + 1.0,
        hook=lambda c: None, status_path=status)
    assert third["diff_ran"] is True and detector.calls == 2


def test_slow_sections_only_on_slow_cadence(tmp_path):
    """★ 贵分片（设备/磁盘）按 1h 刷新，便宜分片按 30min —— 别让 WMI 每轮都跑"""
    detector = _Detector([])
    body = _Body(watcher=_Watcher([]), detector=detector)
    status = tmp_path / "s.json"

    pc.run_perception_tick(body=body, now=NOW, hook=lambda c: None, status_path=status)
    assert detector.sections[0] == list(pc.FAST_SECTIONS) + list(pc.SLOW_SECTIONS)

    # 30 分钟后：便宜分片刷新，贵分片还没到期
    pc.run_perception_tick(body=body, now=NOW + pc.change_detect_seconds() + 1,
                           hook=lambda c: None, status_path=status)
    assert detector.sections[1] == list(pc.FAST_SECTIONS)

    # 1 小时后：贵分片到期，重新一起采
    pc.run_perception_tick(body=body, now=NOW + pc.change_detect_slow_seconds() + 2,
                           hook=lambda c: None, status_path=status)
    assert detector.sections[2] == list(pc.FAST_SECTIONS) + list(pc.SLOW_SECTIONS)
    saved = json.loads(status.read_text(encoding="utf-8"))
    assert "devices" in saved["change_detect_sections"]


def test_no_body_is_reported_not_raised(tmp_path):
    status = tmp_path / "s.json"
    out = pc.run_perception_tick(now=NOW, hook=lambda c: None, status_path=status)
    assert out["status"] == "no_body"
    saved = json.loads(status.read_text(encoding="utf-8"))
    assert saved["body_attached"] is False


def test_watcher_not_running_is_visible(tmp_path):
    body = _Body(watcher=_Watcher([_Reading("/k/a.md", "created")], running=False),
                 detector=_Detector())
    captured = []
    pc.run_perception_tick(body=body, now=NOW, hook=_hook(captured),
                           status_path=tmp_path / "s.json")
    assert captured == []                                     # 未启动不 drain
    saved = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert saved["watcher_running"] is False


def test_gap_summary_without_status_says_no_reading(tmp_path):
    gap = pc.gap_summary(status_path=tmp_path / "missing.json",
                         memory_dir=str(tmp_path), now=NOW)
    assert gap["available"] is False
    assert "还没有感知周期读数" in gap["detail"]


def _write_status(path: Path, *, ts: str, file_event: str | None,
                  running: bool = True) -> None:
    path.write_text(json.dumps({
        "ts": ts, "watch_dirs": ["/k"], "watcher_running": running,
        "last_file_event_at": file_event, "last_change_at": None,
    }), encoding="utf-8")


def test_gap_summary_reports_ages(tmp_path):
    # 时间戳**必须带时区**：naive ISO 会被按本地时区解释，断言随机器时区漂移
    mem = tmp_path / "novelty_memory"
    mem.mkdir()
    (mem / pc.MEMORY_FILE_NAME).write_text(
        json.dumps({"ts": "1970-01-01T00:15:00+00:00", "event_type": "file_change"}) + "\n",
        encoding="utf-8")
    status = tmp_path / "s.json"
    _write_status(status, ts="1970-01-01T00:16:00+00:00",
                  file_event="1970-01-01T00:00:00+00:00")

    gap = pc.gap_summary(status_path=status, memory_dir=str(mem), now=1000.0)
    assert gap["available"] is True
    assert gap["watcher_running"] is True
    assert gap["last_file_event_age_seconds"] == 1000.0
    assert gap["long_silent"] is False                  # 16 分钟 → 不算"长期静默"
    assert gap["last_novelty_age_seconds"] == 100.0


def test_gap_summary_marks_long_silence_only_when_known(tmp_path):
    status = tmp_path / "s.json"
    _write_status(status, ts="1970-01-01T00:00:00+00:00",
                  file_event="1970-01-01T00:00:00+00:00")
    gap = pc.gap_summary(status_path=status, memory_dir=str(tmp_path), now=200000.0)
    assert gap["long_silent"] is True                   # 200000s > 24h
    assert "超过 24h 无事件" in gap["detail"]

    # 刚启动还没事件：不许说"长期静默"（测不到 ≠ 长期没变化）
    _write_status(status, ts="1970-01-01T00:00:00+00:00", file_event=None)
    fresh = pc.gap_summary(status_path=status, memory_dir=str(tmp_path), now=100.0)
    assert fresh["long_silent"] is False
    assert "还没有文件事件" in fresh["detail"]


def test_register_disabled_by_env(monkeypatch, tmp_path):
    monkeypatch.setenv(pc.ENV_ENABLED, "false")
    out = pc.register_perception_scheduler(_Scheduler())
    assert out["status"] == "disabled"


def test_register_adds_interval_task_and_is_idempotent(tmp_path):
    sched = _Scheduler()
    first = pc.register_perception_scheduler(sched)
    assert first["status"] == "scheduled"
    assert sched.tasks[0]["name"] == pc.TASK_NAME
    assert sched.tasks[0]["zero_token"] is True
    # 同名只留一份（重复注册不会把调度表越堆越长）
    pc.register_perception_scheduler(sched)
    assert len([t for t in sched.tasks if t["name"] == pc.TASK_NAME]) == 1
