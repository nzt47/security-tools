# -*- coding: utf-8 -*-
"""泛化后的「未来时间戳」清理脚本（§11.26 U2，2026-10-30）。

锁四件事：
  ① dry-run 只报数、不动文件（默认语义，误跑不伤人）；
  ② --execute 先备份、再删未来行、回读为 0，且写审计 `purge_future_rows`；
  ③ 没有未来行时幂等：文件字节不变、不产生备份；
  ④ 坏 JSON 行与无时间戳字段的行原样保留（不是清理目标）。
另外覆盖「时间戳字段名不统一」（ts / created_at）与旧入口兼容。
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "scripts" / (name + ".py")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _now_future():
    now = datetime.now().replace(microsecond=0).isoformat()
    future = (datetime.now() + timedelta(days=400)).replace(microsecond=0).isoformat()
    return now, future


class _FakeAudit:
    def __init__(self):
        self.calls = []

    def record(self, *args, **kwargs):
        self.calls.append((args, kwargs))


@pytest.fixture()
def fake_audit(monkeypatch):
    import agent.audit as audit_pkg
    stub = _FakeAudit()
    monkeypatch.setattr(audit_pkg, "audit", stub)
    return stub


class TestGenericPurge:
    def test_dry_run_does_not_touch_file(self, tmp_path):
        mod = _load("purge_future_rows")
        now, future = _now_future()
        p = tmp_path / "events.jsonl"
        _write(p, [{"ts": now, "id": "good"}, {"ts": future, "id": "bad"}])
        before = p.read_text(encoding="utf-8")
        res = mod.purge(p, execute=False)
        assert res["future_rows"] == 1 and res["total_rows"] == 2 and res["dry_run"] is True
        assert res["future_dates"] == {future[:10]: 1}
        assert p.read_text(encoding="utf-8") == before, "dry-run 不许动文件"
        assert not list(tmp_path.glob("*.bak.*")), "dry-run 不许产生备份"

    def test_execute_backs_up_and_cleans(self, tmp_path, fake_audit):
        mod = _load("purge_future_rows")
        now, future = _now_future()
        p = tmp_path / "events.jsonl"
        _write(p, [{"ts": now, "id": "good"}, {"ts": future, "id": "bad"}])
        res = mod.purge(p, execute=True)
        assert res["future_rows"] == 1 and res["kept_rows"] == 1 and res["future_after"] == 0
        assert Path(res["backup"]).exists(), "必须先写备份"
        kept = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert [r["id"] for r in kept] == ["good"]
        assert fake_audit.calls, "清理必须写审计"
        args, kwargs = fake_audit.calls[0]
        assert args[0] == "purge_future_rows"
        assert kwargs["payload"]["removed"] == 1

    def test_no_future_rows_is_idempotent(self, tmp_path):
        mod = _load("purge_future_rows")
        now, _future = _now_future()
        p = tmp_path / "events.jsonl"
        _write(p, [{"ts": now, "id": "good"}])
        before = p.read_text(encoding="utf-8")
        res = mod.purge(p, execute=True)
        assert res["future_rows"] == 0
        assert p.read_text(encoding="utf-8") == before
        assert not list(tmp_path.glob("*.bak.*")), "无未来行不该产生备份"

    def test_created_at_field_and_bad_lines_kept(self, tmp_path):
        mod = _load("purge_future_rows")
        _now, future = _now_future()
        p = tmp_path / "archive.jsonl"
        p.write_text(
            json.dumps({"created_at": future, "id": "bad"}, ensure_ascii=False) + "\n"
            + "{not-json}\n"
            + json.dumps({"id": "no-ts-kept"}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        res = mod.purge(p, execute=True)
        assert res["future_rows"] == 1 and res["future_after"] == 0
        text = p.read_text(encoding="utf-8")
        assert "{not-json}" in text, "坏行必须原样保留"
        assert "no-ts-kept" in text, "无时间戳字段的行不参与清理"
        assert "bad" not in text


class TestLegacyEntry:
    def test_legacy_script_delegates_with_old_audit_action(self, tmp_path, fake_audit):
        mod = _load("purge_future_assessment_events")
        now, future = _now_future()
        p = tmp_path / "skills_assessment_events.jsonl"
        _write(p, [{"ts": now, "skill_id": "good"}, {"ts": future, "skill_id": "bad"}])
        res = mod.purge(p, execute=True)
        assert res["future_rows"] == 1 and res["future_after"] == 0
        args, kwargs = fake_audit.calls[0]
        assert args[0] == "skills.assess.events.purge_future", "旧入口必须保持历史审计动作名"
        assert kwargs["subject"] == "data/skills_assessment_events.jsonl"
