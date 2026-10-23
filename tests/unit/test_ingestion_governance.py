# -*- coding: utf-8 -*-
"""吞噬 P6：存量资产治理 scan/plan/apply（归档优先，真删二次确认）。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

import agent.server_auth as server_auth
import plugins.plugin_api as plugin_api
import plugins.ingestion as ingestion_plugin
from agent.ingestion import governance
from agent.ingestion.models import ConfirmationRequired
from agent.skills_mgmt.models import SkillStatus


class FakeSkill:
    def __init__(self, sid, name, description="", content="", *, enabled=True,
                 usage=0, version="1.0.0", status=SkillStatus.PUBLISHED):
        self.id = sid
        self.name = name
        self.description = description
        self.content = content
        self.enabled = enabled
        self.version = version
        self.status = status
        self.source = "manual"
        self.category = "custom"
        self.metrics = SimpleNamespace(usage_count=usage)
        self.touched = False

    def touch(self):
        self.touched = True


class FakeReviewer:
    def __init__(self, pairs=None):
        self.pairs = pairs or []

    def find_duplicates(self, skills, min_jaccard=0.75):
        return list(self.pairs)


class FakeStore:
    def __init__(self, skills):
        self.skills = {s.id: s for s in skills}
        self.upserted = []

    def list_all(self):
        return list(self.skills.values())

    def upsert(self, skill):
        self.skills[skill.id] = skill
        self.upserted.append(skill.id)


class FakeFileStore:
    def __init__(self):
        self.meta = []

    def update_meta(self, sid, patch):
        self.meta.append((sid, patch))


class FakeService:
    def __init__(self, skills, pairs=None, orphans=None):
        self.store = FakeStore(skills)
        self.reviewer = FakeReviewer(pairs)
        self.file_store = FakeFileStore()
        self.merged = []
        self.updated = []
        self.reviewed = []
        self.orphan_calls = []
        self._orphans = orphans or []

    def cleanup_report(self):
        return {"orphans": self._orphans, "unused": []}

    def get(self, sid):
        return self.store.skills[sid]

    def update(self, sid, patch):
        skill = self.store.skills[sid]
        for k, v in patch.items():
            setattr(skill, k, v)
        self.updated.append((sid, patch))
        return skill

    def review_skill(self, sid):
        self.reviewed.append(sid)
        return {"ok": True}

    def merge_duplicate_skills(self, src, dst, strategy="auto"):
        self.merged.append((src, dst, strategy))
        return {"merged_id": dst, "removed_id": src}

    def cleanup_orphans(self, dry_run=True):
        self.orphan_calls.append(dry_run)
        return {"removed": [o.get("id") for o in self._orphans]}


def build_service():
    long_body = "# A\n\n" + "\n\n".join(
        f"## 主题{i}\n\n" + ("内容" * 2500) for i in range(1, 5))
    a = FakeSkill("alpha", "Alpha", "A 技能", long_body, usage=5, version="2.0.0")
    b = FakeSkill("beta", "Beta", "B 技能", long_body, usage=1, version="1.0.0")
    c = FakeSkill("gamma", "Gamma", "", "短内容", enabled=False, usage=0)
    orphan = FakeSkill("orphan", "Orphan", "孤儿", "x")
    pairs = [{"skill_a": "alpha", "skill_b": "beta", "name_a": "Alpha",
              "name_b": "Beta", "jaccard": 1.0, "recommend_action": "merge"}]
    return FakeService([a, b, c, orphan], pairs,
                       orphans=[{"id": "orphan", "reason": "索引有、文件无"}])


def setup_plan(svc):
    data = governance.plan(skills_service=svc)
    return data


class TestScanPlan:
    def test_scan_识别重复_拆分_归档_孤儿(self):
        svc = build_service()
        data = governance.scan(skills_service=svc)
        assert data["total"] == 4
        items = {i["id"]: i for i in data["items"]}
        assert "duplicate" in items["alpha"]["issues"]
        assert "split" in items["alpha"]["issues"]
        assert "refactor" in items["gamma"]["issues"]
        assert "archive" in items["gamma"]["issues"]
        assert "orphan" in items["orphan"]["issues"]
        assert data["summary"]["duplicate"] == 2

    def test_plan_产出_merge_archive_split_refactor_清理孤儿(self):
        svc = build_service()
        data = setup_plan(svc)
        actions = {(a["action"], a["target"]) for a in data["actions"]}
        assert ("merge", "beta") in actions       # beta 使用/版本较低，作为被合并方
        assert ("archive", "gamma") in actions
        assert ("split", "alpha") in actions
        assert ("refactor", "gamma") in actions
        assert ("delete_orphans", "orphans") in actions
        assert data["plan_id"].startswith("gp_")
        assert governance.plan(skills_service=svc)["plan_id"] == data["plan_id"]

    def test_dry_run_不改任何数据(self):
        svc = build_service()
        data = setup_plan(svc)
        out = governance.apply(plan_id=data["plan_id"], action="merge",
                               target="beta", dry_run=True,
                               skills_service=svc, plan_result=data)
        assert out["applied"] is False
        assert out["dry_run"] is True
        assert svc.merged == []

    def test_真执行必须_confirm(self):
        svc = build_service()
        data = setup_plan(svc)
        with pytest.raises(ConfirmationRequired):
            governance.apply(plan_id=data["plan_id"], action="merge",
                             target="beta", dry_run=False,
                             skills_service=svc, plan_result=data)
        assert svc.merged == []


class TestApply:
    def test_merge_复用_merge_duplicate_skills(self):
        svc = build_service()
        data = setup_plan(svc)
        out = governance.apply(plan_id=data["plan_id"], action="merge",
                               target="beta", confirm=True, dry_run=False,
                               skills_service=svc, plan_result=data)
        assert out["applied"] is True
        assert svc.merged == [("beta", "alpha", "auto")]

    def test_archive_只归档不删除(self):
        svc = build_service()
        data = setup_plan(svc)
        governance.apply(plan_id=data["plan_id"], action="archive",
                         target="gamma", confirm=True, dry_run=False,
                         skills_service=svc, plan_result=data)
        skill = svc.get("gamma")
        assert skill.status == SkillStatus.ARCHIVED
        assert skill.enabled is False
        assert "gamma" in svc.store.upserted

    def test_split_只写子草稿且父项不动(self, tmp_path):
        svc = build_service()
        data = setup_plan(svc)
        out = governance.apply(plan_id=data["plan_id"], action="split",
                               target="alpha", confirm=True, dry_run=False,
                               skills_service=svc, plan_result=data,
                               split_dir=tmp_path)
        result = out["result"]
        assert result["count"] >= 3
        root = Path(result["draft_dir"])
        assert (root / "manifest.json").exists()
        assert len(list(root.glob("alpha-part-*.md"))) == result["count"]
        assert svc.get("alpha").touched is False
        import shutil
        shutil.rmtree(root.parent, ignore_errors=True)

    def test_围栏内的二级标题不当章节(self):
        content = "\n".join([
            "## 甲", "正文甲",
            "```markdown", "## 示例标题（围栏内）", "内容", "```",
            "## 乙", "正文乙",
        ])
        secs = governance._content_sections(content)
        assert [s["title"] for s in secs] == ["甲", "乙"]
        assert "## 示例标题（围栏内）" in secs[0]["body"]
        assert secs[0]["body"].count("```") == 2
        assert governance._headings(content) == ["甲", "乙"]

    def test_波浪线围栏与未闭合围栏(self):
        content = "## 甲\n~~~\n## 假\n~~~\n## 乙\n乙正文\n"
        assert [s["title"] for s in governance._content_sections(content)] == ["甲", "乙"]
        # 未闭合围栏：其后的 ## 属于代码块，不得外溢成新章节
        content2 = "## 甲\n正文\n```\n## 假\n仍在代码块\n"
        secs2 = governance._content_sections(content2)
        assert [s["title"] for s in secs2] == ["甲"]
        assert "## 假" in secs2[0]["body"]

    def test_切分产物不产生断裂围栏(self, tmp_path):
        skill = FakeSkill("delta", "Delta", content=
                          "## 一\n正文一\n```bash\n## 假\n```\n## 二\n正文二\n")
        out = governance._write_split(skill, plan_id="gp_test_fence", draft_dir=tmp_path)
        assert out["count"] == 2
        root = Path(out["draft_dir"])
        for name in out["parts"]:
            body = (root / name).read_text(encoding="utf-8")
            assert body.count("```") % 2 == 0
        import shutil
        shutil.rmtree(root.parent, ignore_errors=True)

    def test_refactor_写说明并重跑评审(self):
        svc = build_service()
        data = setup_plan(svc)
        governance.apply(plan_id=data["plan_id"], action="refactor",
                         target="gamma", confirm=True, dry_run=False,
                         skills_service=svc, plan_result=data)
        assert svc.file_store.meta
        assert svc.file_store.meta[0][0] == "gamma"
        assert svc.reviewed == ["gamma"]

    def test_清理孤儿走既有_cleanup(self):
        svc = build_service()
        data = setup_plan(svc)
        governance.apply(plan_id=data["plan_id"], action="delete_orphans",
                         target="orphans", confirm=True, dry_run=False,
                         skills_service=svc, plan_result=data)
        assert svc.orphan_calls == [False]

    def test_plan_id_or_action_不匹配被拒(self):
        svc = build_service()
        data = setup_plan(svc)
        with pytest.raises(ValueError):
            governance.apply(plan_id="gp_bad", action="merge", target="beta",
                             confirm=True, dry_run=False, skills_service=svc,
                             plan_result=data)
        with pytest.raises(ValueError):
            governance.apply(plan_id=data["plan_id"], action="merge", target="nope",
                             confirm=True, dry_run=False, skills_service=svc,
                             plan_result=data)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FLASK_API_TOKEN", "test-token")
    monkeypatch.setattr(server_auth, "_AUTH_DISABLED_FOR_TEST", False)
    monkeypatch.setattr(plugin_api, "_resolve_host_decorator",
                        lambda name: getattr(server_auth, name))
    app = Flask(__name__)
    app.register_blueprint(ingestion_plugin.bp)
    return app.test_client()


AUTH = {"Authorization": "Bearer test-token"}


class TestGovernanceHttp:
    def test_scan_返回只读盘点(self, client, monkeypatch):
        monkeypatch.setattr("agent.ingestion.governance.scan",
                            lambda **kw: {"ok": True, "total": 1, "items": []})
        r = client.post("/api/ingestion/governance/scan", json={}, headers=AUTH)
        assert r.status_code == 200
        assert r.get_json()["data"]["scan"]["total"] == 1

    def test_apply_默认_dry_run(self, client, monkeypatch):
        captured = {}

        def fake_apply(**kw):
            captured.update(kw)
            return {"ok": True, "dry_run": kw.get("dry_run"), "applied": False}
        monkeypatch.setattr("agent.ingestion.governance.apply", fake_apply)
        r = client.post("/api/ingestion/governance/apply",
                        json={"plan_id": "gp_1", "action": "merge", "target": "b"},
                        headers=AUTH)
        assert r.status_code == 200
        assert captured["dry_run"] is True
        assert captured["confirm"] is False
