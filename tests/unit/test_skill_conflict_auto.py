# -*- coding: utf-8 -*-
"""冲突自动处置（conflict_auto）：规则判据、只停用不删除、可撤销（2026-10-30）。"""
from __future__ import annotations

import json

import pytest

from agent.skills_mgmt import conflict_auto
from agent.skills_mgmt.models import ReviewFinding, ReviewResult, Skill
from agent.skills_mgmt.service import SkillsMgmtService


def _svc(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_SKILL_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setattr(conflict_auto, "DECISIONS_PATH", tmp_path / "decisions.jsonl")
    return SkillsMgmtService(
        store_path=str(tmp_path / "skills.json"),
        repo_path=str(tmp_path / "repo"),
        class_registry_path=str(tmp_path / "classes.json"),
    )


def _seed(svc, sid, *, score=60.0, code="INT_SHARED_TRIGGER", enabled=True, tags=None):
    skill = Skill(id=sid, name=sid, description="d", content="# c", author="t", source="manual",
                  enabled=enabled, tags=list(tags or []))
    skill.review = ReviewResult(
        status="passed", score=score, security_score=90.0, quality_score=80.0,
        compatibility_score=70.0, auto_assessed=True, reviewed_by="auto",
        findings=[ReviewFinding(code=code, severity="warn", message="与 X 共享触发词：demo",
                                category="compatibility")],
    )
    svc.store.upsert(skill)
    return skill


class TestPlan:
    def test_low_score_without_usage_is_disable(self, tmp_path, monkeypatch):
        svc = _svc(tmp_path, monkeypatch)
        _seed(svc, "low", score=60.0)
        out = conflict_auto.plan(svc)
        row = out["items"][0]
        assert row["action"] == "disable" and row["kind"] == "trigger_collision"
        assert "0 命中/0 加载/0 执行" in row["reason"]
        assert out["summary"]["disable"] == 1

    def test_high_score_without_usage_escalates_to_human(self, tmp_path, monkeypatch):
        svc = _svc(tmp_path, monkeypatch)
        _seed(svc, "high", score=95.0)
        out = conflict_auto.plan(svc)
        assert out["items"][0]["action"] == "escalate"
        assert out["judge_calls"] == 0

    def test_judge_can_decide_ambiguous_case(self, tmp_path, monkeypatch):
        svc = _svc(tmp_path, monkeypatch)
        _seed(svc, "high", score=95.0)
        calls = []

        def judge(item):
            calls.append(item["skill_id"])
            return "disable"

        out = conflict_auto.plan(svc, judge=judge)
        assert out["items"][0]["action"] == "disable"
        assert out["judge_calls"] == 1 and calls == ["high"]

    def test_native_overlap_kind(self, tmp_path, monkeypatch):
        svc = _svc(tmp_path, monkeypatch)
        _seed(svc, "ovl", score=50.0, code="OVL_OPERATION_OVERLAP")
        out = conflict_auto.plan(svc)
        assert out["items"][0]["kind"] == "native_overlap"

    def test_disabled_skill_is_skipped(self, tmp_path, monkeypatch):
        svc = _svc(tmp_path, monkeypatch)
        _seed(svc, "off", score=10.0, enabled=False)
        assert conflict_auto.plan(svc)["items"] == []


class TestApplyUndo:
    def test_apply_requires_confirm(self, tmp_path, monkeypatch):
        svc = _svc(tmp_path, monkeypatch)
        _seed(svc, "low", score=60.0)
        with pytest.raises(ValueError):
            conflict_auto.apply(svc, conflict_auto.plan(svc)["items"])

    def test_apply_disables_and_records_decision(self, tmp_path, monkeypatch):
        svc = _svc(tmp_path, monkeypatch)
        _seed(svc, "low", score=60.0, tags=["imported"])
        out = conflict_auto.apply(svc, conflict_auto.plan(svc)["items"], confirm=True)
        assert out["count"] == 1
        skill = svc.get("low")
        assert skill.enabled is False, "自动处置只能停用（不删除）"
        assert "auto_dormant" in (skill.tags or []) and "imported" in (skill.tags or [])
        rows = conflict_auto.read_decisions()
        assert len(rows) == 1 and rows[0]["before"]["enabled"] is True
        assert rows[0]["action"] == "disable"

    def test_undo_restores_enabled_and_tags(self, tmp_path, monkeypatch):
        svc = _svc(tmp_path, monkeypatch)
        _seed(svc, "low", score=60.0, tags=["imported"])
        conflict_auto.apply(svc, conflict_auto.plan(svc)["items"], confirm=True)
        did = conflict_auto.read_decisions()[0]["decision_id"]
        out = conflict_auto.undo(svc, did, confirm=True)
        assert out["restored_enabled"] is True
        skill = svc.get("low")
        assert skill.enabled is True and "auto_dormant" not in (skill.tags or [])

    def test_keep_items_are_not_touched(self, tmp_path, monkeypatch):
        svc = _svc(tmp_path, monkeypatch)
        _seed(svc, "high", score=95.0)
        out = conflict_auto.apply(svc, conflict_auto.plan(svc)["items"], confirm=True)
        assert out["count"] == 0 and svc.get("high").enabled is True
