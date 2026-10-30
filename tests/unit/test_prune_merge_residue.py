# -*- coding: utf-8 -*-
"""撤销合并后的版本快照残留清理（2026-10-30）。

锁三件事：
  ① dry-run 只报数、不写文件；
  ② execute 只删「created_by=merge_skills 且 hash 命中该次合并的 src 正文」的版本，
     人工版本 / 初版一律保留；
  ③ 没有可清理项时幂等（不写盘、不产生备份）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "prune_merge_residue", str(ROOT / "scripts" / "prune_merge_residue.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fixture(tmp_path: Path):
    """造一个 dst 技能：1 个初版 + 1 个人工版 + 1 个合并残留。"""
    from agent.skills_mgmt.models import Skill, SkillVersion
    from agent.skills_mgmt.store import SkillStore

    src_content = "# 被合并方正文" + chr(10) + "alpha"
    merge_hash = hashlib.sha256(src_content.encode("utf-8")).hexdigest()[:16]

    store_path = tmp_path / "skills_mgmt.json"
    store = SkillStore(str(store_path))
    skill = Skill(id="dst-skill", name="dst", description="d", content="# 保留方正文")
    skill.versions = [
        SkillVersion(version="0.1.0", content="# 初版", created_by="system"),
        SkillVersion(version="0.2.0", content="# 人工版", created_by="human"),
        SkillVersion(version="0.3.0", content=src_content, created_by="merge_skills",
                     changelog="合并自技能 src-skill (success_rate=0.90)", hash=merge_hash),
    ]
    store.upsert(skill)

    backups = tmp_path / "skill_merge_backups.jsonl"
    backups.write_text(json.dumps({
        "merge_id": "m-1", "ts": "2026-10-30T19:40:52", "src_id": "src-skill",
        "dst_id": "dst-skill", "src_snapshot": {"content": src_content},
        "dst_before": {"name": "dst"}, "dst_snapshot": {},
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    return store_path, backups


def test_dry_run_reports_without_writing(tmp_path):
    mod = _load()
    store_path, backups = _fixture(tmp_path)
    before = store_path.read_text(encoding="utf-8")
    rep = mod.plan(store_path, backups, "2026-10-30T19:40:00")
    assert rep["removed_total"] == 1
    info = rep["dst"]["dst-skill"]
    assert info["versions_before"] == 3 and info["versions_after"] == 2
    assert store_path.read_text(encoding="utf-8") == before, "dry-run 不许写盘"


def test_execute_removes_only_merge_version(tmp_path):
    mod = _load()
    store_path, backups = _fixture(tmp_path)
    rep = mod.apply(store_path, backups, "2026-10-30T19:40:00")
    assert rep["executed"] is True and rep["removed_total"] == 1
    assert Path(rep["backup"]).exists(), "必须先备份 skills_mgmt.json"
    saved = json.loads(store_path.read_text(encoding="utf-8"))["dst-skill"]
    versions = saved["versions"]
    assert len(versions) == 2
    creators = sorted(v.get("created_by") for v in versions)
    assert creators == ["human", "system"], "只许删 merge_skills 追加的那一条"


def test_no_residue_is_idempotent(tmp_path):
    mod = _load()
    store_path, backups = _fixture(tmp_path)
    mod.apply(store_path, backups, "2026-10-30T19:40:00")
    snapshot = store_path.read_text(encoding="utf-8")
    before_list = sorted(p.name for p in tmp_path.glob("*.bak.*"))
    rep = mod.apply(store_path, backups, "2026-10-30T19:40:00")
    assert rep["executed"] is False and rep["removed_total"] == 0
    assert store_path.read_text(encoding="utf-8") == snapshot
    assert sorted(p.name for p in tmp_path.glob("*.bak.*")) == before_list, "幂等时不该再备份"


def test_since_filter_skips_older_backups(tmp_path):
    mod = _load()
    store_path, backups = _fixture(tmp_path)
    rep = mod.plan(store_path, backups, "2026-10-30T20:00:00")
    assert rep["backups"] == 0 and rep["removed_total"] == 0
