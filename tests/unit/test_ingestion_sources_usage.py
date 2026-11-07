# -*- coding: utf-8 -*-
"""「本机可吞噬源」的**用过标记**（2026-11-07）。

【为什么要有】候选列表 100 条、名字都差不多，没有标记只能靠记性判断
"这条是不是已经生成过草稿 / 已经入轨"。实测同一批里 brainstorming、algorithmic-art、
byted-* 都早已用过，列表里完全看不出 —— 于是会重复生成一堆同名草稿。

判定口径（都按等值比较，不把名字拼进路径）：
  · registered —— `data/skills_repo/<候选名>` 存在；
  · draft      —— 某个草稿 manifest 的 `source_ref == path:<候选路径>`；
  · 两者都有 ⇒ registered 优先（更靠后），草稿时间仍带出来做提示。
"""
from __future__ import annotations

import json
from pathlib import Path

from agent.ingestion import sources


def _cand(name: str, path: str) -> dict:
    return {"ecosystem": "trae", "name": name, "path": path, "kind": "skill-md",
            "previewable": True, "warnings": []}


def _draft(base: Path, draft_id: str, source_ref: str, created_at: str) -> None:
    root = base / draft_id
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "draft_id": draft_id, "form": "skill", "source_ref": source_ref,
        "created_at": created_at}), encoding="utf-8")


class TestMarkUsage:
    def test_unused_candidate_has_empty_usage(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sources, "_DRAFTS_DIR", tmp_path / "drafts")
        monkeypatch.setattr(sources, "_SKILLS_DIR", tmp_path / "skills")
        (tmp_path / "skills").mkdir()
        rows = [_cand("never-used", str(tmp_path / "src" / "never-used"))]
        sources._mark_usage(rows)
        assert rows[0]["usage"] == ""
        assert rows[0]["usage_draft_id"] == "" and rows[0]["usage_at"] == ""

    def test_draft_marks_by_exact_path(self, tmp_path, monkeypatch):
        drafts, skills = tmp_path / "drafts", tmp_path / "skills"
        skills.mkdir()
        monkeypatch.setattr(sources, "_DRAFTS_DIR", drafts)
        monkeypatch.setattr(sources, "_SKILLS_DIR", skills)
        used_path = str(tmp_path / "src" / "used")
        _draft(drafts, "ing_abc", "path:" + used_path, "2026-11-07T08:26:08+08:00")
        rows = [_cand("used", used_path), _cand("other", str(tmp_path / "src" / "other"))]
        sources._mark_usage(rows)
        assert rows[0]["usage"] == "draft"
        assert rows[0]["usage_draft_id"] == "ing_abc"
        assert rows[0]["usage_at"].startswith("2026-11-07")
        assert rows[1]["usage"] == ""      # 只是**前缀相同**的邻居，不能被误标

    def test_registered_wins_over_draft(self, tmp_path, monkeypatch):
        drafts, skills = tmp_path / "drafts", tmp_path / "skills"
        (skills / "both").mkdir(parents=True)
        monkeypatch.setattr(sources, "_DRAFTS_DIR", drafts)
        monkeypatch.setattr(sources, "_SKILLS_DIR", skills)
        path = str(tmp_path / "src" / "both")
        _draft(drafts, "ing_both", "path:" + path, "2026-11-07T08:00:00+08:00")
        rows = [_cand("both", path)]
        sources._mark_usage(rows)
        assert rows[0]["usage"] == "registered"
        assert rows[0]["usage_draft_id"] == "ing_both", "草稿信息仍要带出来（提示用）"

    def test_broken_manifest_is_skipped_not_fatal(self, tmp_path, monkeypatch):
        drafts, skills = tmp_path / "drafts", tmp_path / "skills"
        skills.mkdir()
        bad = drafts / "ing_bad"
        bad.mkdir(parents=True)
        (bad / "manifest.json").write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(sources, "_DRAFTS_DIR", drafts)
        monkeypatch.setattr(sources, "_SKILLS_DIR", skills)
        rows = [_cand("x", str(tmp_path / "x"))]
        sources._mark_usage(rows)   # 不抛
        assert rows[0]["usage"] == ""
