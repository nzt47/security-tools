# -*- coding: utf-8 -*-
"""吞噬 P4：tool/workflow 草稿生成 + digestion borrowed 入轨（不自动启用）。"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent.ingestion import drafts as drafts_mod
from agent.ingestion import service
from agent.ingestion.models import InvalidSource, RewriteRequired

PLAN = SimpleNamespace(source_ref="path:C:/tmp/source", plan_id="ig_test")


class FakeInstaller:
    def __init__(self):
        self.calls = []
        self.assessed = []
        self.absorbed = []

    def install_from_zip(self, zip_path: str):
        self.calls.append(zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            count = len([n for n in zf.namelist() if n.startswith("scripts/")])
        return {"skill_id": manifest["id"], "name": manifest["name"],
                "version": manifest.get("version", "1.0.0"), "scripts_count": count}

    def _advisory_assess(self, skill_id: str):
        self.assessed.append(skill_id)

    def _auto_classify(self, skill_id: str):
        pass

    def absorb_overlap(self, skill_id: str, overlap: str = "", source: str = ""):
        self.absorbed.append(skill_id)


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    monkeypatch.setattr(service, "_audit", lambda payload: None)


@pytest.fixture()
def tool_skill(tmp_path):
    d = tmp_path / "p4-tool-smoke"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: P4 Tool Smoke\ndescription: 把输入整理成报告\n---\n\n"
        "# P4 Tool\n\n读取输入并输出报告。\n",
        encoding="utf-8")
    (d / "scripts").mkdir()
    (d / "scripts" / "run.py").write_text("print('x')\n", encoding="utf-8")
    return d


@pytest.fixture()
def workflow_skill(tmp_path):
    d = tmp_path / "p4-flow-smoke"
    d.mkdir()
    (d / "SKILL.md").write_text(
        # 步骤里必须点名真实工具（data/tool_definitions），否则 2026-10-26 起推荐为 skill
        "---\nname: P4 Flow Smoke\ndescription: 固定步骤流程\n---\n\n"
        "# P4 Flow\n\n## 步骤\n1. 用 search_files 读取输入\n2. 用 read_file 整理字段\n"
        "3. 用 write_file 输出报告\n4. 用 run_tests 归档\n",
        encoding="utf-8")
    return d


@pytest.fixture()
def plain_skill(tmp_path):
    d = tmp_path / "p4-plain-smoke"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: P4 Plain Smoke\ndescription: 需要判断的说明型技能\n---\n\n"
        "# P4 Plain\n\n按情况给出建议。\n",
        encoding="utf-8")
    return d


class TestRecommendation:
    def test_有脚本推荐_tool(self, tool_skill):
        plan = service.preview(tool_skill)
        assert plan.recommendation.form == "tool"
        assert any("form=tool" in a for a in plan.actions)

    def test_稳定步骤推荐_workflow(self, workflow_skill):
        plan = service.preview(workflow_skill)
        assert plan.recommendation.form == "workflow"
        assert any("form=workflow" in a for a in plan.actions)

    def test_步骤没点名工具时降级为技能(self, tmp_path):
        """只有编号步骤、没有真实工具名 ⇒ 技能（避免生成无法执行的工作流骨架）。

        2026-10-26 实测：code-refactor 的 18 步是 Claude 的 Grep/Count/Replace，
        照旧规则会被推荐成 workflow，用户拿到的是 18 步全空的骨架。
        """
        d = tmp_path / "p4-prose"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: P4 Prose\ndescription: 操作说明\n---\n\n"
            "## 步骤\n1. 读取输入\n2. 整理字段\n3. 输出报告\n4. 归档\n",
            encoding="utf-8")
        plan = service.preview(d)
        assert plan.recommendation.form == "skill"
        assert "对应到云枢工具" in plan.recommendation.reason

    def test_无证据仍推荐_skill(self, plain_skill):
        plan = service.preview(plain_skill)
        assert plan.recommendation.form == "skill"


class TestDraftCommit:
    def test_tool_草稿只写草稿目录且不安装(self, tool_skill, tmp_path):
        plan = service.preview(tool_skill)
        fake = FakeInstaller()
        result = service.commit(tool_skill, plan_id=plan.plan_id, confirm=True,
                                form="tool", draft_dir=tmp_path / "drafts",
                                installer=fake)
        assert result.draft is True
        assert result.installed is False
        assert result.form == "tool"
        assert result.queued is False
        assert fake.calls == []
        root = Path(result.draft_dir)
        assert (root / "manifest.json").exists()
        tool_py = root / "agent" / "tools" / "p4_tool_smoke.py"
        tool_yaml = root / "data" / "tool_definitions" / "p4_tool_smoke.yaml"
        test_py = root / "tests" / "unit" / "test_p4_tool_smoke.py"
        assert tool_py.exists() and tool_yaml.exists() and test_py.exists()
        tool_text = tool_py.read_text(encoding="utf-8")
        assert "NotImplementedError" in tool_text
        compile(tool_text, str(tool_py), "exec")  # 草稿自身必须是合法 Python
        # 2026-10-26 修复的回归：测试骨架此前把换行写成字面量 "\n"，py_compile 直接 SyntaxError
        test_src_path = root / "tests" / "unit" / "test_p4_tool_smoke.py"
        test_src = test_src_path.read_text(encoding="utf-8")
        compile(test_src, str(test_src_path), "exec")
        assert "\nimport pytest" not in test_src, "换行必须是真换行，不能是字面量 \\n"
        # 来源 scripts 复制进草稿（实现材料），不会执行
        assert (root / "source" / "scripts" / "run.py").is_file()
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        assert "source/scripts/run.py" in manifest["files"]
        meta = yaml.safe_load(tool_yaml.read_text(encoding="utf-8"))
        assert meta["status"] == "draft"
        assert meta["enabled"] is False
        assert not (Path(__file__).resolve().parents[2] / "agent" / "tools" / "p4_tool_smoke.py").exists()

    def test_workflow_草稿带步骤且未启用(self, workflow_skill, tmp_path):
        plan = service.preview(workflow_skill)
        result = service.commit(workflow_skill, plan_id=plan.plan_id, confirm=True,
                                form="workflow", draft_dir=tmp_path / "drafts",
                                installer=FakeInstaller())
        assert result.draft is True
        root = Path(result.draft_dir)
        wf_files = list((root / "workflows").glob("*.json"))
        assert wf_files
        workflow = json.loads(wf_files[0].read_text(encoding="utf-8"))
        assert workflow["status"] == "draft"
        assert workflow["enabled"] is False
        assert workflow["requires_binding"] is True
        assert len(workflow["steps"]) >= 3
        # 2026-10-26：警告从一句"未绑定/不自动加载"拆成三条可操作提示
        # （是什么问题 → 下一步改哪个文件 → 不登记的后果），避免用户照推荐选了工作流却卡住
        joined = "\n".join(result.warnings)
        assert "草稿只是骨架" in joined and "不能直接执行" in joined
        assert "下一步：编辑 workflows/" in joined and "tool_name" in joined
        assert "不会被任何加载器扫描" in joined and "不登记" in joined
        assert all(len(w) > 0 for w in result.warnings)

    def test_草稿可被评审页列出并可安全删除(self, workflow_skill, tmp_path):
        """草稿不在技能队列里，必须能被「草稿评审」列出（含警告与目录），并可清理。

        2026-10-26 实测：生成后去评审页一片空白（那里只读 /api/skills-mgmt/queue），
        用户因此以为草稿没生成。list_drafts 把隔离区的真实产物摊开。
        """
        plan = service.preview(workflow_skill)
        result = service.commit(workflow_skill, plan_id=plan.plan_id, confirm=True,
                                form="workflow", draft_dir=tmp_path / "drafts",
                                installer=FakeInstaller())
        base = tmp_path / "drafts"
        rogue = base / "not_a_draft"
        rogue.mkdir()
        (rogue / "x.txt").write_text("x", encoding="utf-8")

        rows = drafts_mod.list_drafts(base)
        assert len(rows) == 1 and rows[0]["draft_id"] == Path(result.draft_dir).name
        row = rows[0]
        assert row["form"] == "workflow"
        assert row["status"] == "draft" and row["enabled"] is False
        assert any("草稿只是骨架" in w for w in row["warnings"])
        assert row["draft_dir"].endswith(row["draft_id"])

        # 名字必须与 listing 等值（防路径注入），且必须有 manifest.json
        assert drafts_mod.delete_draft("../drafts", draft_dir=base) is False
        assert drafts_mod.delete_draft("not_a_draft", draft_dir=base) is False
        assert drafts_mod.delete_draft("ing_not_exist", draft_dir=base) is False
        assert drafts_mod.delete_draft(row["draft_id"], draft_dir=base) is True
        assert drafts_mod.list_drafts(base) == []
        assert rogue.exists(), "非草稿目录不得被删除"

    def test_默认_form_skill_仍安装(self, tool_skill, tmp_path):
        plan = service.preview(tool_skill)
        fake = FakeInstaller()
        result = service.commit(tool_skill, plan_id=plan.plan_id, confirm=True,
                                draft_dir=tmp_path / "drafts", installer=fake)
        assert result.draft is False
        assert result.installed is True
        assert result.form == "skill"
        assert len(fake.calls) == 1

    def test_未知_form_被拒(self, plain_skill):
        plan = service.preview(plain_skill)
        with pytest.raises(InvalidSource):
            service.commit(plain_skill, plan_id=plan.plan_id, confirm=True,
                           form="video", installer=FakeInstaller())

    def test_草稿形态拒绝_merge_into(self, tool_skill):
        plan = service.preview(tool_skill)
        with pytest.raises(InvalidSource):
            service.commit(tool_skill, plan_id=plan.plan_id, confirm=True,
                           form="tool", merge_into="target",
                           installer=FakeInstaller())

    def test_L2_未重写不能出草稿(self, tmp_path):
        d = tmp_path / "doc"
        d.mkdir()
        (d / "README.md").write_text("# 文档\n\n普通文档。\n", encoding="utf-8")
        plan = service.preview(d)
        with pytest.raises(RewriteRequired):
            service.commit(d, plan_id=plan.plan_id, confirm=True,
                           form="workflow", installer=FakeInstaller())


class TestBorrowedStage:
    def test_安装后调用_borrowed_入轨(self, plain_skill, monkeypatch):
        calls = {}

        def fake_stage(skill_id, plan):
            calls["skill_id"] = skill_id
            calls["source_ref"] = plan.source_ref
            return SimpleNamespace(applied=True, verdict="applied", reasons=[])

        monkeypatch.setattr(service, "_stage_migrate_borrowed", fake_stage)
        plan = service.preview(plain_skill)
        result = service.commit(plain_skill, plan_id=plan.plan_id, confirm=True,
                                installer=FakeInstaller())
        assert calls["skill_id"] == "p4-plain-smoke"
        assert result.stage == "borrowed"
        assert result.stage_verdict == "applied"

    def test_入轨未应用如实告警(self, plain_skill, monkeypatch):
        monkeypatch.setattr(service, "_stage_migrate_borrowed",
                            lambda skill_id, plan: SimpleNamespace(
                                applied=False, verdict="not_found",
                                reasons=["descriptor 不存在"]))
        plan = service.preview(plain_skill)
        result = service.commit(plain_skill, plan_id=plan.plan_id, confirm=True,
                                installer=FakeInstaller())
        assert result.stage == ""
        assert result.stage_verdict == "not_found"
        assert any("not_found" in w for w in result.warnings)

    def test_草稿不进入_borrowed(self, tool_skill, tmp_path, monkeypatch):
        called = []
        monkeypatch.setattr(service, "_stage_migrate_borrowed",
                            lambda skill_id, plan: called.append(skill_id))
        plan = service.preview(tool_skill)
        service.commit(tool_skill, plan_id=plan.plan_id, confirm=True,
                       form="tool", draft_dir=tmp_path / "drafts",
                       installer=FakeInstaller())
        assert called == []

    def test_borrowed_助手证据含真实台账引用(self, monkeypatch):
        captured = {}

        def fake_stage(cid, to_stage, evidence, **kwargs):
            captured.update({"cid": cid, "to": to_stage, "evidence": evidence, **kwargs})
            return SimpleNamespace(applied=True, verdict="applied", reasons=[])

        monkeypatch.setattr("agent.digestion.stage.stage_migrate", fake_stage)
        migration = service._stage_migrate_borrowed("p4-tool-smoke", PLAN)
        assert migration.applied is True
        assert captured["cid"] == "cp.skill.p4-tool-smoke"
        assert captured["to"] == "borrowed"
        assert captured["evidence"]["first_entry"] is True
        assert "capability_id=cp.skill.p4-tool-smoke" in captured["evidence"]["trace_policy"]
        assert captured["actor"] == "agent.ingestion"

    def test_borrowed_门控证据可直接评估(self):
        from agent.digestion.stage import evaluate_migration
        verdict, reasons = evaluate_migration(
            capability_id="cp.skill.p4-tool-smoke", from_stage=None,
            to_stage="borrowed",
            evidence={"first_entry": True,
                      "trace_policy": "trace:skill-import:ledger=x#capability_id=cp.skill.p4-tool-smoke"})
        assert verdict == "applied", reasons

    def test_ledger_trace_policy_含能力键(self):
        from agent.descriptors.bridge import ledger_trace_policy
        policy = ledger_trace_policy(source="skill-import",
                                     capability_id="cp.skill.p4-tool-smoke",
                                     route="import-ledger")
        assert "capability_id=cp.skill.p4-tool-smoke" in policy
        assert "ledger=" in policy and "read=" in policy
