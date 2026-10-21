# -*- coding: utf-8 -*-
"""吞噬 P3：去重/合并/吸收建议 + commit merge_into / 草稿队列。"""
from __future__ import annotations

import json
import zipfile
from types import SimpleNamespace

import pytest
from flask import Flask

import agent.server_auth as server_auth
import plugins.plugin_api as plugin_api
import plugins.ingestion as ingestion_plugin
from agent.ingestion import service
from agent.ingestion.models import MergeFailed
from agent.skills_mgmt.models import Skill, SkillStatus
from agent.skills_mgmt.reviewer import SkillReviewer
from agent.skills_mgmt.service import SkillsMgmtService

BODY = (
    "# Alpha\n\n"
    "记忆摘要：把对话历史压缩成摘要，保留关键决策与待办。\n"
    "步骤一：读取对话；步骤二：提炼要点；步骤三：输出摘要。\n"
)


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    monkeypatch.setattr(service, "_audit", lambda payload: None)


class FakeCatalog:
    def __init__(self, skills=None):
        self.store = SimpleNamespace(list_all=lambda: list(skills or []))
        self.reviewer = SkillReviewer()


class FakeMergeService:
    def __init__(self, *, target_exists=True, merge_fails=False):
        self.installed = []
        self.merged = []
        self.deleted = []
        self.absorbed = []
        self.assessed = []
        self.target_exists = target_exists
        self.merge_fails = merge_fails

    def install_from_zip(self, zip_path: str):
        self.installed.append(zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            count = len([n for n in zf.namelist() if n.startswith("scripts/")])
        return {"skill_id": manifest["id"], "name": manifest["name"],
                "version": manifest.get("version", "1.0.0"), "scripts_count": count}

    def get(self, skill_id: str):
        if not self.target_exists or skill_id != "target":
            raise KeyError(skill_id)
        return object()

    def merge_duplicate_skills(self, src_id: str, dst_id: str, strategy: str = "auto"):
        if self.merge_fails:
            raise RuntimeError("merge boom")
        self.merged.append((src_id, dst_id))
        return {"merged_id": dst_id, "removed_id": src_id}

    def delete(self, skill_id: str):
        self.deleted.append(skill_id)

    def _advisory_assess(self, skill_id: str):
        self.assessed.append(skill_id)

    def _auto_classify(self, skill_id: str):
        pass

    def absorb_overlap(self, skill_id: str, overlap: str = "", source: str = ""):
        self.absorbed.append((skill_id, overlap))


@pytest.fixture()
def incoming(tmp_path):
    d = tmp_path / "incoming"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: Alpha\ndescription: 处理 Alpha 任务\n---\n\n" + BODY,
        encoding="utf-8")
    return d


class TestDedupePlan:
    def test_既有重复给出合并建议(self, incoming):
        catalog = FakeCatalog([
            Skill(id="alpha-existing", name="Alpha", description="处理 Alpha 任务",
                  content=BODY.strip(), status=SkillStatus.DRAFT),
        ])
        plan = service.preview(incoming, dedupe=True, skills_service=catalog)
        item = next(x for x in plan.dedupe if x["id"] == "alpha-existing")
        assert item["kind"] == "existing"
        assert item["action"] == "merge"
        assert item["strong"] is True
        assert plan.merge_suggestion is not None
        assert plan.merge_suggestion["target_id"] == "alpha-existing"
        assert plan.merge_suggestion["action"] == "merge"

    def test_原生重叠只吸收不合并(self, incoming):
        plan = service.preview(incoming, dedupe=True, skills_service=FakeCatalog())
        native = [x for x in plan.dedupe if x["kind"] == "native"]
        assert native
        assert native[0]["id"] == "memory_summary"
        assert native[0]["action"] == "absorb"
        assert plan.merge_suggestion is None

    def test_同名_id提示覆盖且不建议_merge(self, incoming):
        catalog = FakeCatalog([
            Skill(id="incoming", name="Old", description="旧", content="旧内容"),
        ])
        plan = service.preview(incoming, dedupe=True, skills_service=catalog)
        item = next(x for x in plan.dedupe if x["id"] == "incoming")
        assert item["action"] == "overwrite"
        assert plan.merge_suggestion is None
        assert any("覆盖" in w for w in plan.warnings)

    def test_L2_未重写也做原生去重(self, tmp_path):
        d = tmp_path / "docs"
        d.mkdir()
        (d / "README.md").write_text("# 说明\n\n" + BODY, encoding="utf-8")
        plan = service.preview(d, dedupe=True, skills_service=FakeCatalog())
        assert plan.conformance.level == "L2"
        assert any(x["kind"] == "native" and x["id"] == "memory_summary"
                   for x in plan.dedupe)


class TestMergeCommit:
    def test_merge_into_成功(self, incoming):
        fake = FakeMergeService()
        plan = service.preview(incoming, dedupe=True, skills_service=FakeCatalog())
        result = service.commit(incoming, plan_id=plan.plan_id, confirm=True,
                                installer=fake, merge_into="target")
        assert result.merged is True
        assert result.merged_into == "target"
        assert result.removed_id == "incoming"
        assert result.skill_id == "target"
        assert result.queued is False
        assert fake.merged == [("incoming", "target")]
        assert fake.deleted == []

    def test_merge_目标不存在时不安装(self, incoming):
        fake = FakeMergeService(target_exists=False)
        plan = service.preview(incoming, dedupe=True, skills_service=FakeCatalog())
        with pytest.raises(MergeFailed):
            service.commit(incoming, plan_id=plan.plan_id, confirm=True,
                           installer=fake, merge_into="target")
        assert fake.installed == []

    def test_merge_到自身被拒(self, incoming):
        fake = FakeMergeService()
        plan = service.preview(incoming, dedupe=True, skills_service=FakeCatalog())
        with pytest.raises(MergeFailed):
            service.commit(incoming, plan_id=plan.plan_id, confirm=True,
                           installer=fake, merge_into="incoming")
        assert fake.installed == []

    def test_merge_失败回滚新技能(self, incoming):
        fake = FakeMergeService(merge_fails=True)
        plan = service.preview(incoming, dedupe=True, skills_service=FakeCatalog())
        with pytest.raises(MergeFailed):
            service.commit(incoming, plan_id=plan.plan_id, confirm=True,
                           installer=fake, merge_into="target")
        assert fake.installed
        assert fake.deleted == ["incoming"]

    def test_无_merge_落草稿并吸收原生重叠(self, incoming):
        fake = FakeMergeService()
        plan = service.preview(incoming, dedupe=True, skills_service=FakeCatalog())
        result = service.commit(incoming, plan_id=plan.plan_id, confirm=True,
                                installer=fake)
        assert result.queued is True
        assert result.merged is False
        assert fake.assessed == ["incoming"]
        assert fake.absorbed and "记忆摘要" in fake.absorbed[0][1]


class TestImportQueue:
    def test_ingestion_草稿进入待放行队列(self, tmp_path):
        svc = SkillsMgmtService(store_path=str(tmp_path / "skills.json"),
                                repo_path=str(tmp_path / "repo"))
        svc.store.upsert(Skill(id="q-ing", name="Q", source="ingestion:skill_md",
                               status=SkillStatus.DRAFT, content="x"))
        rows = svc.import_queue()
        ids = {r["id"] for r in rows}
        assert "q-ing" in ids


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


class TestMergeHttp:
    def test_commit_透传_merge_into(self, client, incoming, monkeypatch):
        fake = FakeMergeService()
        monkeypatch.setattr(service, "_get_skills_service", lambda: FakeCatalog())
        monkeypatch.setattr(service, "_default_installer", lambda: fake)
        plan = client.post("/api/ingestion/preview",
                           json={"source": str(incoming)},
                           headers=AUTH).get_json()["data"]["plan"]
        r = client.post("/api/ingestion/commit",
                        json={"source": str(incoming), "plan_id": plan["plan_id"],
                              "confirm": True, "merge_into": "target"},
                        headers=AUTH)
        assert r.status_code == 200
        result = r.get_json()["data"]["result"]
        assert result["merged"] is True
        assert result["merged_into"] == "target"
