# -*- coding: utf-8 -*-
"""草稿登记（agent/ingestion/register_draft.py）+ 0 步工作流的拒绝口径。

【这份用例守三个东西】
1. 三种形态各自落到**正确的真实位置**（skills_repo / agent/tools/custom + tool_definitions / learned_workflows），
   而不是"看起来登记成功"；
2. **登记 ≠ 启用**：默认写 enabled=false / status=draft；只有 enable=True 才改状态；
3. **0 步来源不许产出工作流草稿**（2026-11-07：trae/document-skill 正文没有步骤，
   以前会落一个 0 步死骨架，现在当场 400 并指向技能形态）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.ingestion import drafts as drafts_mod
from agent.ingestion import register_draft as register_mod


def _skill_draft(base: Path, sid: str = "demo-skill") -> Path:
    root = base / "ing_demo_skill"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nid: %s\nname: demo\ntags: [demo]\nstatus: draft\nenabled: false\n---\n\n# demo\n\n正文\n" % sid,
        encoding="utf-8")
    (root / "CARD.md").write_text("# demo\n", encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({
        "draft_id": "ing_demo_skill", "form": "skill", "status": "draft",
        "skill_id": sid, "skill_name": "demo", "created_at": "2026-11-07T08:00:00+08:00",
        "files": ["manifest.json", "SKILL.md", "CARD.md"], "warnings": []}), encoding="utf-8")
    return root


class TestSkillRegister:
    def test_writes_file_track_and_keeps_draft_status(self, tmp_path, monkeypatch):
        repo = tmp_path / "skills_repo"
        monkeypatch.setattr(register_mod, "SKILLS_REPO", repo)
        called = {}

        class _Svc:
            def backfill_file_track(self, *, dry_run=True):
                called["dry_run"] = dry_run
                return {"created": 1}

        import agent.skills_mgmt as sm
        monkeypatch.setattr(sm, "SkillsMgmtService", lambda: _Svc())
        root = _skill_draft(tmp_path / "drafts")
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

        out = register_mod.register_skill(root, manifest, enable=False)
        assert out["skill_id"] == "demo-skill"
        assert (repo / "demo-skill" / "skill.md").is_file()
        assert (repo / "demo-skill" / "CARD.md").is_file()
        assert called == {"dry_run": False}, "主轨补登记必须真的落库（dry_run=False）"
        text = (repo / "demo-skill" / "skill.md").read_text(encoding="utf-8")
        assert "status: draft" in text and "enabled: false" in text, "登记默认不启用"

    def test_written_file_name_is_lowercase_skill_md(self, tmp_path, monkeypatch):
        """文件轨的入口名**必须**是 `skill.md`（大小写敏感的文件系统上也一样）。

        【为什么单列一条】此前写的是 `SKILL.md`：Windows 大小写不敏感 ⇒ 本机看不出问题，
        Linux（CI / 容器）上 file_store / capability_cards / layered / backfill_file_track
        里的 `dir / "skill.md"` 全部找不到文件 ⇒ 该技能"登记成功但对文件轨不可见"。
        这里比对**目录里的真实文件名**（不靠 is_file()），才能在 Windows 上也抓到回归。
        """
        repo = tmp_path / "skills_repo"
        monkeypatch.setattr(register_mod, "SKILLS_REPO", repo)

        class _Svc:
            def backfill_file_track(self, *, dry_run=True):
                return {}

        import agent.skills_mgmt as sm
        monkeypatch.setattr(sm, "SkillsMgmtService", lambda: _Svc())
        root = _skill_draft(tmp_path / "drafts")
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        register_mod.register_skill(root, manifest, enable=True)

        names = sorted(p.name for p in (repo / "demo-skill").iterdir())
        assert "skill.md" in names, f"文件轨入口名不是小写 skill.md：{names}"
        assert "SKILL.md" not in names, (
            "又写成大写 SKILL.md 了：Windows 能跑，Linux 上该技能对文件轨完全不可见"
        )

    def test_enable_patches_front_matter(self, tmp_path, monkeypatch):
        repo = tmp_path / "skills_repo"
        monkeypatch.setattr(register_mod, "SKILLS_REPO", repo)

        class _Svc:
            def backfill_file_track(self, *, dry_run=True):
                return {}

        import agent.skills_mgmt as sm
        monkeypatch.setattr(sm, "SkillsMgmtService", lambda: _Svc())
        root = _skill_draft(tmp_path / "drafts")
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        register_mod.register_skill(root, manifest, enable=True)
        text = (repo / "demo-skill" / "skill.md").read_text(encoding="utf-8")
        assert "status: approved" in text and "enabled: true" in text


class TestWorkflowRegister:
    def _draft(self, base: Path, *, steps, bound=True, description="d1") -> Path:
        root = base / "ing_wf"
        (root / "workflows").mkdir(parents=True)
        (root / "workflows" / "demo-workflow.json").write_text(json.dumps({
            "id": "demo-workflow", "name": "demo", "description": "", "status": "draft",
            "enabled": False, "trigger_patterns": ["demo"],
            "steps": [{"step_id": "step_1", "description": description,
                       "tool_name": ("git" if bound else ""), "params_template": {},
                       "output_key": "o1", "need_llm": False, "timeout_ms": 30000}] * steps,
        }), encoding="utf-8")
        (root / "manifest.json").write_text(json.dumps({
            "draft_id": "ing_wf", "form": "workflow", "status": "draft",
            "skill_id": "demo", "skill_name": "demo",
            "created_at": "2026-11-07T08:00:00+08:00", "files": [], "warnings": []}),
            encoding="utf-8")
        return root

    def test_unbound_steps_become_llm_steps(self, tmp_path, monkeypatch):
        """没绑工具的步骤 = 需要判断的步骤 ⇒ 就地补成 LLM 步，并声明 hybrid。

        【为什么不是拒绝】绑不上工具的步骤往往本来就该由模型判断（读 diff、评估方案）；
        模型层 `toolchain` 禁含 need_llm，故这里必须如实声明 `workflow_type='hybrid'`。
        """
        captured = {}

        class _Repo:
            def upsert(self, wf):
                captured["wf"] = wf

        import agent.workflow_learning.repository as repo_mod
        monkeypatch.setattr(repo_mod, "WorkflowRepository", lambda *a, **k: _Repo())
        root = self._draft(tmp_path, steps=2, bound=False)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        out = register_mod.register_workflow(root, manifest)
        assert out["tool_steps"] == 0 and out["llm_steps"] == 2
        wf = captured["wf"]
        assert wf.workflow_type == "hybrid"
        assert all(s.need_llm and s.prompt_template for s in wf.steps)

    def test_step_without_tool_and_description_is_rejected(self, tmp_path, monkeypatch):
        class _Repo:
            def upsert(self, wf):  # pragma: no cover - 不该被调用
                raise AssertionError("不应写库")

        import agent.workflow_learning.repository as repo_mod
        monkeypatch.setattr(repo_mod, "WorkflowRepository", lambda *a, **k: _Repo())
        root = self._draft(tmp_path, steps=1, bound=False, description="")
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        with pytest.raises(register_mod.RegisterError) as ei:
            register_mod.register_workflow(root, manifest)
        assert "无法登记" in ei.value.message

    def test_zero_steps_are_rejected(self, tmp_path):
        root = self._draft(tmp_path, steps=0, bound=True)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        with pytest.raises(register_mod.RegisterError):
            register_mod.register_workflow(root, manifest)


class TestWorkflowDraftGuard:
    """0 步来源在**生成草稿**这一步就该被拒（不是等到登记）。"""

    def test_zero_step_source_raises_invalid_source(self, tmp_path):
        from agent.ingestion.models import IngestPlan, IngestSource, InvalidSource, NormalizedSkill

        skill = NormalizedSkill(id="doc", name="文档", description="",
                                body="只有一句说明，没有任何编号步骤。\n")
        plan = IngestPlan(plan_id="ig_x", source=IngestSource(kind="path", path=str(tmp_path)), source_ref="path:%s" % tmp_path, adapter="docs")
        with pytest.raises(InvalidSource) as ei:
            drafts_mod.write_workflow_draft(skill, plan, draft_dir=tmp_path)
        assert "0 步" in ei.value.message
        assert not any(tmp_path.iterdir()), "被拒时不得留下半截草稿目录"

    def test_source_with_steps_still_writes_draft(self, tmp_path):
        from agent.ingestion.models import IngestPlan, IngestSource, NormalizedSkill

        body = "## Workflow\n\n1. 第一步\n2. 第二步\n3. 第三步\n"
        skill = NormalizedSkill(id="wf", name="wf", description="", body=body)
        out = drafts_mod.write_workflow_draft(skill, IngestPlan(plan_id="ig_y", source=IngestSource(kind="path", path=str(tmp_path)), source_ref="path:%s" % tmp_path, adapter="docs"), draft_dir=tmp_path)
        assert out["form"] == "workflow"
        assert len(out["workflow"]["steps"]) == 3