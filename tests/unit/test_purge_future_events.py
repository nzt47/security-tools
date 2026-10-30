# -*- coding: utf-8 -*-
"""评估事件台账里的「未来时间戳」：读侧防御 + 存量清理（2026-10-30）。

现象：动态中心最新动态停在 2027-10-19/20 —— `data/skills_assessment_events.jsonl` 里
有 190 条 2027 年的历史污染（早期未隔离测试直接写生产台账）。
本文件锁三件事：
  ① 服务读侧（events / feed / long-poll）丢掉未来行；
  ② purge 脚本默认 dry-run 不动文件；
  ③ --execute 先备份、再删未来行、回读为 0。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _rows():
    now = datetime.now().isoformat(timespec="seconds")
    future = (datetime.now() + timedelta(days=400)).replace(microsecond=0).isoformat()
    return now, future, [
        {"ts": now, "kind": "auto", "skill_id": "good", "verdict": "ok", "summary": "正常"},
        {"ts": future, "kind": "auto", "skill_id": "bad", "verdict": "ok", "summary": "未来脏数据"},
    ]


class TestReadSideDefence:
    def test_assessment_events_skips_future_rows(self, tmp_path, monkeypatch):
        from agent.skills_mgmt import log_archiver, service as svc_mod

        now, _future, rows = _rows()
        p = tmp_path / "skills_assessment_events.jsonl"
        _write(p, rows)
        monkeypatch.setattr(log_archiver, "active_events_file", lambda: p)
        monkeypatch.setattr(log_archiver, "archive_daily_file", lambda *a, **k: None)

        svc = svc_mod.SkillsMgmtService(
            store_path=str(tmp_path / "skills.json"),
            repo_path=str(tmp_path / "repo"),
            class_registry_path=str(tmp_path / "classes.json"),
        )
        got = svc.assessment_events(limit=10)
        assert [r["skill_id"] for r in got] == ["good"], got
        assert got[0]["ts"] == now

    def test_feed_skips_future_rows(self, tmp_path, monkeypatch):
        from agent.skills_mgmt import log_archiver, service as svc_mod

        _now, _future, rows = _rows()
        p = tmp_path / "skills_assessment_events.jsonl"
        _write(p, rows)
        monkeypatch.setattr(log_archiver, "active_events_file", lambda: p)
        monkeypatch.setattr(log_archiver, "archive_daily_file", lambda *a, **k: None)

        svc = svc_mod.SkillsMgmtService(
            store_path=str(tmp_path / "skills.json"),
            repo_path=str(tmp_path / "repo"),
            class_registry_path=str(tmp_path / "classes.json"),
        )
        feed = svc.assessment_feed(limit=10)
        assert [r["skill_id"] for r in feed] == ["good"], feed

    def test_helper_threshold(self):
        from agent.skills_mgmt.service import is_future_timestamp

        assert is_future_timestamp((datetime.now() + timedelta(days=1)).isoformat()) is True
        assert is_future_timestamp(datetime.now().isoformat()) is False
        assert is_future_timestamp("") is False
        assert is_future_timestamp("not-a-date") is False


class TestPurgeScript:
    def _mod(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "purge_future_events", str(ROOT / "scripts" / "purge_future_assessment_events.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_dry_run_does_not_touch_file(self, tmp_path, monkeypatch):
        mod = self._mod()
        _now, _future, rows = _rows()
        p = tmp_path / "events.jsonl"
        _write(p, rows)
        before = p.read_text(encoding="utf-8")
        res = mod.purge(p, execute=False)
        assert res["future_rows"] == 1 and res["total_rows"] == 2 and res["dry_run"] is True
        assert p.read_text(encoding="utf-8") == before, "dry-run 不许动文件"

    def test_execute_removes_future_and_keeps_backup(self, tmp_path, monkeypatch):
        mod = self._mod()
        calls = []
        import agent.audit as audit_pkg
        monkeypatch.setattr(audit_pkg, "audit", type("A", (), {"record": staticmethod(lambda *a, **k: calls.append((a, k)))})())

        _now, _future, rows = _rows()
        p = tmp_path / "events.jsonl"
        _write(p, rows)
        res = mod.purge(p, execute=True)
        assert res["future_rows"] == 1 and res["kept_rows"] == 1 and res["future_after"] == 0
        assert Path(res["backup"]).exists(), "必须先写备份"
        kept = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert [r["skill_id"] for r in kept] == ["good"]
        assert calls, "清理必须写审计"

    def test_no_future_rows_is_noop(self, tmp_path):
        mod = self._mod()
        now = datetime.now().isoformat(timespec="seconds")
        p = tmp_path / "events.jsonl"
        _write(p, [{"ts": now, "kind": "auto", "skill_id": "good"}])
        before = p.read_text(encoding="utf-8")
        res = mod.purge(p, execute=True)
        assert res["future_rows"] == 0
        assert p.read_text(encoding="utf-8") == before
