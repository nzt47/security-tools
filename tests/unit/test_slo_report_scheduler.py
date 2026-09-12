"""TASK-S8-06（周报挂调度）单测：SLO 周报定时生成与存档。

覆盖：
- 默认关闭不注册（安全底线）
- 显式开启后注册为 cron 任务，且 cron 参数正确（周一/时/分）
- 未传 scheduler 时返回明确原因（不自行创建调度器）
- run_once 真实落盘（md/json）+ 审计追加
- 脚本非零退出 → ok=False，但审计仍写入（不静默）
- 非法配置回退默认 / 越界夹紧
- 默认调度时间语义：day_of_week=0 即周一（Python weekday() 语义）
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from agent.monitoring import slo_report_scheduler as mod


# ── 工具 ──────────────────────────────────────────────────
class FakeScheduler:
    """最小 TaskScheduler 替身（只记录 add_cron_task 调用）。"""

    def __init__(self) -> None:
        self.tasks: list = []

    def add_cron_task(self, name, func, day_of_week=None, hour=0, minute=0):
        self.tasks.append({
            "name": name, "func": func, "task_id": f"py-{len(self.tasks)+1}",
            "cron": {"day_of_week": day_of_week, "hour": hour, "minute": minute},
        })


@pytest.fixture()
def isolate(tmp_path, monkeypatch):
    """隔离落盘：manifest 审计与存档都指向 tmp，并清空相关 env。"""
    for key in list(os.environ):
        if key.startswith(mod._ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(f"{mod._ENV_PREFIX}_AUDIT_FILE",
                       str(tmp_path / "audit.jsonl"))
    return tmp_path


def _read_audit(tmp_path: Path):
    p = tmp_path / "audit.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ── 注册行为 ──────────────────────────────────────────────
def test_disabled_by_default_does_not_register(isolate):
    r = mod.register_slo_report_scheduler(FakeScheduler())
    assert r["ok"] is True
    assert r["registered"] is False
    assert r["reason"] == "disabled"


def test_enabled_registers_cron_with_expected_schedule(isolate, monkeypatch):
    monkeypatch.setenv(f"{mod._ENV_PREFIX}_ENABLED", "true")
    sched = FakeScheduler()
    r = mod.register_slo_report_scheduler(sched)
    assert r["registered"] is True
    assert r["task_id"] == "py-1"
    assert r["schedule"] == {"day_of_week": 0, "hour": 9, "minute": 0}
    task = sched.tasks[0]
    assert task["name"] == mod.TASK_NAME
    assert task["cron"]["day_of_week"] == 0      # 周一
    assert callable(task["func"])


def test_custom_schedule_honored(isolate, monkeypatch):
    monkeypatch.setenv(f"{mod._ENV_PREFIX}_ENABLED", "1")
    monkeypatch.setenv(f"{mod._ENV_PREFIX}_DAY_OF_WEEK", "4")
    monkeypatch.setenv(f"{mod._ENV_PREFIX}_HOUR", "21")
    monkeypatch.setenv(f"{mod._ENV_PREFIX}_MINUTE", "30")
    sched = FakeScheduler()
    r = mod.register_slo_report_scheduler(sched)
    assert r["schedule"] == {"day_of_week": 4, "hour": 21, "minute": 30}


def test_no_scheduler_returns_explicit_reason(isolate, monkeypatch):
    monkeypatch.setenv(f"{mod._ENV_PREFIX}_ENABLED", "true")
    r = mod.register_slo_report_scheduler(None)
    assert r["registered"] is False
    assert "no_scheduler" in r["reason"]


# ── 生成与存档 ────────────────────────────────────────────
def _stub_runner_ok(cmd, cwd, timeout):
    """桩执行器：按 cmd 里的 --md/--out 真实落盘（模拟脚本成功）。"""
    md = Path(cmd[cmd.index("--md") + 1])
    js = Path(cmd[cmd.index("--out") + 1])
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("# 周报（桩）\n", encoding="utf-8")
    js.write_text('{"stub": true}', encoding="utf-8")
    return {"returncode": 0, "stdout_tail": "", "stderr_tail": ""}


def test_run_once_archives_and_audits(isolate, monkeypatch):
    out_dir = isolate / "archive"
    r = mod.run_once(days=7, out_dir=out_dir, runner=_stub_runner_ok,
                     now=datetime(2026, 9, 14, 9, 0))
    assert r["ok"] is True
    assert r["archived"]["md"].endswith("slo_weekly_20260914.md")
    assert (out_dir / "slo_weekly_20260914.md").exists()
    assert (out_dir / "slo_weekly_20260914.json").exists()
    audit = _read_audit(isolate)
    assert len(audit) == 1
    assert audit[0]["ok"] is True and audit[0]["days"] == 7


def test_run_once_failure_is_recorded_not_swallowed(isolate):
    def _fail(cmd, cwd, timeout):
        return {"returncode": 3, "stdout_tail": "", "stderr_tail": "boom"}

    r = mod.run_once(out_dir=isolate / "a", runner=_fail)
    assert r["ok"] is False
    audit = _read_audit(isolate)
    assert len(audit) == 1 and audit[0]["ok"] is False
    assert audit[0]["returncode"] == 3


def test_run_once_missing_script_reports_note(isolate, monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_REPORT_SCRIPT", tmp_path / "nope.py")
    r = mod.run_once(out_dir=isolate / "a", runner=_stub_runner_ok)
    assert r["ok"] is False
    assert "周报脚本不存在" in r["note"]


# ── 配置健壮性 ────────────────────────────────────────────
def test_invalid_config_falls_back_and_clamps(isolate, monkeypatch):
    monkeypatch.setenv(f"{mod._ENV_PREFIX}_DAY_OF_WEEK", "99")
    monkeypatch.setenv(f"{mod._ENV_PREFIX}_HOUR", "not-a-number")
    monkeypatch.setenv(f"{mod._ENV_PREFIX}_DAYS", "0")
    assert mod._schedule()["day_of_week"] == 6      # 夹紧到上界
    assert mod._schedule()["hour"] == 9             # 回退默认
    assert mod._days() == 1                         # 夹紧到下界
