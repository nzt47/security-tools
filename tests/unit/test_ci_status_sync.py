"""`agent/monitoring/ci_status.py` —— CI 结果同步 + 周期任务（2026-11-12）

【判据来源】报警器的 CI 事件源读 `data/ci/status.json`，>2h 未同步即按"不可用"。
现场实证：2026-11-11 18:14 之后再没人同步 ⇒ CI 覆盖离线 7 小时，而页面上只有
`checked.ci=false`（"本轮没读到"），看不出"上次是几点"。本文件钉三件事：
① 同步失败**不抛异常**（否则调度器会每 30 分钟记一条"计划任务失败"）；
② 写出的字段名与 `collector.ci_candidates` 对齐；
③ gap_summary 的 2h 阈值与 collector 同口径（过期就是过期）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent.monitoring import ci_status as cs


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


GH_ROWS = [
    {"databaseId": 1, "name": "ci", "displayTitle": "fix", "workflowName": "云枢系统测试流程",
     "status": "completed", "conclusion": "success", "headBranch": "master",
     "createdAt": "2026-11-12T00:00:00Z", "url": "https://example.invalid/1"},
]


def _runner(rows, code: int = 0, stderr: str = ""):
    def run(argv):
        assert argv[0] == "gh"
        if argv[1:3] == ["run", "list"]:
            return code, json.dumps(rows, ensure_ascii=False), stderr
        if argv[1:3] == ["repo", "view"]:
            return 0, json.dumps({"nameWithOwner": "owner/name"}), ""
        return 1, "", "unexpected argv"
    return run


def _iso(delta_hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=delta_hours)).isoformat()


def test_build_payload_maps_collector_fields():
    payload = cs.build_payload(GH_ROWS, repo="owner/name")
    assert payload["repo"] == "owner/name"
    assert payload["source"] == "gh run list"
    assert payload["synced_at"]
    row = payload["runs"][0]
    assert row["workflow"] == "云枢系统测试流程"
    assert row["conclusion"] == "success"
    assert row["created_at"] == "2026-11-12T00:00:00Z"
    assert row["database_id"] == 1


def test_sync_once_writes_status_file(tmp_path):
    out = tmp_path / "ci" / "status.json"
    result = cs.sync_once(out_path=out, runner=_runner(GH_ROWS))
    assert result["status"] == "written"
    assert result["runs"] == 1
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["runs"][0]["workflow"] == "云枢系统测试流程"


def test_sync_once_missing_gh_returns_unavailable(tmp_path):
    def boom(argv):
        raise FileNotFoundError("gh")

    out = tmp_path / "status.json"
    result = cs.sync_once(out_path=out, runner=boom)
    assert result["status"] == "unavailable"
    assert "找不到 gh" in result["reason"]
    assert not out.exists()                       # 失败不写假文件


def test_sync_once_nonzero_exit_returns_unavailable(tmp_path):
    out = tmp_path / "status.json"
    result = cs.sync_once(out_path=out, runner=_runner([], code=4, stderr="not logged in"))
    assert result["status"] == "unavailable"
    assert "exit=4" in result["reason"]


def test_sync_once_timeout_is_swallowed(tmp_path):
    import subprocess

    def slow(argv):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=1)

    result = cs.sync_once(out_path=tmp_path / "s.json", runner=slow)
    assert result["status"] == "unavailable"


def test_gap_summary_missing_file_is_not_ok(tmp_path):
    gap = cs.gap_summary(out_path=tmp_path / "missing.json")
    assert gap["available"] is False
    assert gap["stale"] is True
    assert "从未同步" in gap["detail"]


def test_gap_summary_fresh_vs_stale(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"synced_at": _iso(0.5), "runs": [{}]}), encoding="utf-8")
    fresh = cs.gap_summary(out_path=path)
    assert fresh["available"] is True and fresh["stale"] is False
    assert fresh["runs"] == 1

    path.write_text(json.dumps({"synced_at": _iso(3.0), "runs": []}), encoding="utf-8")
    stale = cs.gap_summary(out_path=path)
    assert stale["stale"] is True
    assert "阈值" in stale["detail"]


def test_register_disabled_by_env(monkeypatch):
    monkeypatch.setenv(cs.ENV_ENABLED, "0")
    assert cs.register_ci_sync_scheduler(_Scheduler())["status"] == "disabled"


def test_register_adds_interval_task_idempotently(monkeypatch):
    monkeypatch.delenv(cs.ENV_ENABLED, raising=False)
    sched = _Scheduler()
    first = cs.register_ci_sync_scheduler(sched)
    assert first["status"] == "scheduled"
    assert sched.tasks[0]["name"] == cs.TASK_NAME
    assert sched.tasks[0]["zero_token"] is True
    cs.register_ci_sync_scheduler(sched)
    assert len([t for t in sched.tasks if t["name"] == cs.TASK_NAME]) == 1
