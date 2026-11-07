# -*- coding: utf-8 -*-
"""S5 第二批：把隔离区技能草稿登记进文件轨（transform/register.py）。

锁四件事：
  1. 只登记不启用（front matter 里 enabled=false 原样保留）；
  2. 目标已存在 ⇒ 拒绝，不覆盖；
  3. intake 校验失败 ⇒ 回滚刚写的目录（不留半成品）；
  4. 台账推进到 registered，并记录 target。
"""
from __future__ import annotations

import json
from pathlib import Path

import types

import pytest

from agent.skills_mgmt.transform import jobs
from agent.skills_mgmt.transform.register import RegisterError, register_skill_job

SKILL_MD = """---
id: gamma-draft
name: 伽马草稿
description: 从外来材料抽出来的技能草稿
author: yunshu-ingestion
category: custom
source: ingestion
tags: []
content_type: markdown
enabled: false
version: 0.1.0
status: draft
---

# 伽马草稿

## 何时使用

- 需要落地测试时
"""


def _draft(tmp_path: Path) -> Path:
    tdir = tmp_path / "drafts" / "ing_x"
    tdir.mkdir(parents=True)
    (tdir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (tdir / "CARD.md").write_text("# 伽马草稿\n\n## 何时使用\n\n- 需要落地测试时\n", encoding="utf-8")
    (tdir / "manifest.json").write_text(json.dumps({
        "draft_id": "ing_x", "form": "skill", "status": "draft", "enabled": False,
        "skill_id": "gamma-draft", "skill_name": "伽马草稿",
    }, ensure_ascii=False), encoding="utf-8")
    return tdir


def _job(tmp_path: Path, tdir: Path, *, form: str = "skill") -> dict:
    return jobs.upsert_job(source="ingestion", source_ref="ing_x", form=form,
                           title="伽马草稿", artifacts=str(tdir), root=tmp_path)


def _patch_intake(monkeypatch, *, ok: bool = True):
    calls = []

    def fake_intake(skill_id, **kwargs):
        calls.append((skill_id, kwargs))
        return {"ok": ok, "skill_id": skill_id, "stage": "done" if ok else "validate",
                "error": "" if ok else "缺必填键"}

    import scripts.intake_skill as intake_mod
    monkeypatch.setattr(intake_mod, "intake_one", fake_intake, raising=False)
    monkeypatch.setattr(intake_mod, "open_pr", lambda sid: {"ok": True, "pr_url": "https://example/pr/1"},
                        raising=False)
    return calls


def test_登记写出文件轨并推进台账(tmp_path, monkeypatch):
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir)
    calls = _patch_intake(monkeypatch)
    rep = register_skill_job(job["job_id"], root=tmp_path)
    assert rep["ok"] is True and rep["skill_id"] == "gamma-draft"
    target = tmp_path / "data" / "skills_repo" / "gamma-draft"
    assert (target / "skill.md").is_file() and (target / "CARD.md").is_file()
    body = (target / "skill.md").read_text(encoding="utf-8")
    assert "enabled: false" in body, "登记不等于启用"
    assert rep["enabled"] is False
    assert calls and calls[0][0] == "gamma-draft" and calls[0][1].get("fix_meta") is True
    stored = jobs.get_job(job["job_id"], root=tmp_path)
    assert stored["status"] == "registered"
    assert stored["target"] == {"kind": "skill", "id": "gamma-draft"}


def test_目标已存在则拒绝且不覆盖(tmp_path, monkeypatch):
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir)
    _patch_intake(monkeypatch)
    target = tmp_path / "data" / "skills_repo" / "gamma-draft"
    target.mkdir(parents=True)
    (target / "skill.md").write_text("原有内容", encoding="utf-8")
    with pytest.raises(RegisterError) as exc:
        register_skill_job(job["job_id"], root=tmp_path)
    assert exc.value.code == "skill-exists"
    assert (target / "skill.md").read_text(encoding="utf-8") == "原有内容"


def test_intake_校验失败会回滚目录(tmp_path, monkeypatch):
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir)
    _patch_intake(monkeypatch, ok=False)
    with pytest.raises(RegisterError) as exc:
        register_skill_job(job["job_id"], root=tmp_path)
    assert exc.value.code == "intake-rejected"
    assert not (tmp_path / "data" / "skills_repo" / "gamma-draft").exists(), "失败必须回滚"
    assert jobs.get_job(job["job_id"], root=tmp_path)["status"] == "candidate"


def test_启用双写文件轨与主轨(tmp_path, monkeypatch):
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir)
    _patch_intake(monkeypatch)
    register_skill_job(job["job_id"], root=tmp_path)

    enabled_calls = []

    class FakeSvc:
        def set_enabled(self, skill_id, enabled):
            enabled_calls.append((skill_id, enabled))
            return None

    import agent.state_manager as state_mod
    monkeypatch.setattr(state_mod, "get_skills_mgmt_service", lambda: FakeSvc(), raising=False)

    from agent.skills_mgmt.transform.register import enable_skill_job
    rep = enable_skill_job(job["job_id"], root=tmp_path)
    assert rep["ok"] is True and rep["enabled"] is True
    assert enabled_calls == [("gamma-draft", True)]
    md = (tmp_path / "data" / "skills_repo" / "gamma-draft" / "skill.md").read_text(encoding="utf-8")
    assert "enabled: true" in md, "文件轨必须同步改（§8 陷阱 7 双写）"
    assert jobs.get_job(job["job_id"], root=tmp_path)["status"] == "enabled"


def test_未登记不能启用(tmp_path):
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir)
    from agent.skills_mgmt.transform.register import enable_skill_job
    with pytest.raises(RegisterError) as exc:
        enable_skill_job(job["job_id"], root=tmp_path)
    assert exc.value.code == "job-status"


def test_主轨失败回滚文件轨(tmp_path, monkeypatch):
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir)
    _patch_intake(monkeypatch)
    register_skill_job(job["job_id"], root=tmp_path)

    class BoomSvc:
        def set_enabled(self, skill_id, enabled):
            raise RuntimeError("db locked")

    import agent.state_manager as state_mod
    monkeypatch.setattr(state_mod, "get_skills_mgmt_service", lambda: BoomSvc(), raising=False)

    from agent.skills_mgmt.transform.register import enable_skill_job
    with pytest.raises(RegisterError) as exc:
        enable_skill_job(job["job_id"], root=tmp_path)
    assert exc.value.code == "enable-failed"
    md = (tmp_path / "data" / "skills_repo" / "gamma-draft" / "skill.md").read_text(encoding="utf-8")
    assert "enabled: false" in md, "主轨失败必须把文件轨回滚（两轨一致）"
    assert jobs.get_job(job["job_id"], root=tmp_path)["status"] == "registered"


def test_非技能形态拒绝(tmp_path, monkeypatch):
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir, form="tool")
    _patch_intake(monkeypatch)
    with pytest.raises(RegisterError) as exc:
        register_skill_job(job["job_id"], root=tmp_path)
    assert exc.value.code == "job-form"


def test_已登记作业不能再登记(tmp_path, monkeypatch):
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir)
    _patch_intake(monkeypatch)
    register_skill_job(job["job_id"], root=tmp_path)
    with pytest.raises(RegisterError) as exc:
        register_skill_job(job["job_id"], root=tmp_path)
    assert exc.value.code == "job-status"


def test_缺_SKILL_md_拒绝(tmp_path, monkeypatch):
    tdir = tmp_path / "drafts" / "ing_empty"
    tdir.mkdir(parents=True)
    job = _job(tmp_path, tdir)
    _patch_intake(monkeypatch)
    with pytest.raises(RegisterError) as exc:
        register_skill_job(job["job_id"], root=tmp_path)
    assert exc.value.code == "draft-missing"

def _tool_draft(tmp_path: Path) -> Path:
    tdir = tmp_path / "drafts" / "ing_tool"
    (tdir / "agent" / "tools").mkdir(parents=True)
    (tdir / "data" / "tool_definitions").mkdir(parents=True)
    (tdir / "tests" / "unit").mkdir(parents=True)
    (tdir / "agent" / "tools" / "demo_tool.py").write_text("TOOL_NAME = 'demo_tool'\n", encoding="utf-8")
    (tdir / "data" / "tool_definitions" / "demo_tool.yaml").write_text(
        "name: demo_tool\nstatus: draft\nenabled: false\n", encoding="utf-8")
    (tdir / "tests" / "unit" / "test_demo_tool.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (tdir / "manifest.json").write_text(json.dumps({
        "draft_id": "ing_tool", "form": "tool", "skill_id": "demo-tool",
        "files": ["manifest.json", "agent/tools/demo_tool.py",
                  "data/tool_definitions/demo_tool.yaml", "tests/unit/test_demo_tool.py"],
    }, ensure_ascii=False), encoding="utf-8")
    return tdir


def test_工具草稿登记搬三件产物且保持未启用(tmp_path):
    tdir = _tool_draft(tmp_path)
    job = jobs.upsert_job(source="ingestion", source_ref="ing_tool", form="tool",
                          title="demo", artifacts=str(tdir), root=tmp_path)
    from agent.skills_mgmt.transform.register import register_draft_job
    rep = register_draft_job(job["job_id"], root=tmp_path)
    assert rep["ok"] is True and rep["enabled"] is False
    assert (tmp_path / "agent" / "tools" / "demo_tool.py").is_file()
    yaml_text = (tmp_path / "data" / "tool_definitions" / "demo_tool.yaml").read_text(encoding="utf-8")
    assert "enabled: false" in yaml_text, "登记不等于启用"
    assert (tmp_path / "tests" / "unit" / "test_demo_tool.py").is_file()
    assert jobs.get_job(job["job_id"], root=tmp_path)["status"] == "registered"


def test_工具目标已存在整体拒绝(tmp_path):
    tdir = _tool_draft(tmp_path)
    job = jobs.upsert_job(source="ingestion", source_ref="ing_tool", form="tool",
                          title="demo", artifacts=str(tdir), root=tmp_path)
    existing = tmp_path / "data" / "tool_definitions" / "demo_tool.yaml"
    existing.parent.mkdir(parents=True)
    existing.write_text("name: demo_tool\nenabled: true\n", encoding="utf-8")
    from agent.skills_mgmt.transform.register import register_draft_job
    with pytest.raises(RegisterError) as exc:
        register_draft_job(job["job_id"], root=tmp_path)
    assert exc.value.code == "target-exists"
    # 不能出现半套：agent/tools 那份也不该被写进去
    assert not (tmp_path / "agent" / "tools" / "demo_tool.py").exists()


def test_工作流草稿未绑定工具拒绝登记(tmp_path):
    tdir = tmp_path / "drafts" / "ing_wf"
    (tdir / "workflows").mkdir(parents=True)
    (tdir / "workflows" / "wf-demo.json").write_text(json.dumps({
        "id": "wf-demo", "name": "演示工作流", "status": "draft", "enabled": False,
        "steps": [{"step_id": "step_1", "tool_name": "", "params_template": {}}],
    }, ensure_ascii=False), encoding="utf-8")
    job = jobs.upsert_job(source="ingestion", source_ref="ing_wf", form="workflow",
                          title="演示", artifacts=str(tdir), root=tmp_path)
    from agent.skills_mgmt.transform.register import register_draft_job
    with pytest.raises(RegisterError) as exc:
        register_draft_job(job["job_id"], root=tmp_path)
    assert exc.value.code == "workflow-unbound"
    assert jobs.get_job(job["job_id"], root=tmp_path)["status"] == "candidate"


def test_主轨独有技能只改主轨不报错(tmp_path, monkeypatch):
    """没有文件轨的技能（global-core-principles / skill / brainstorming 这一类）也能启用。

    【为什么单列】2026-11-07 实测：转化队列对 id 为 brainstorming 的作业点「启用」，
    弹的是「文件轨里没有 brainstorming/skill.md」。双写被当成了"两轨必须都存在"，
    而"只有主轨"是口径 §5 明确允许的合法状态。
    """
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir)
    _patch_intake(monkeypatch)
    register_skill_job(job["job_id"], root=tmp_path)
    # 模拟"运行时把文件轨合并掉了"：只留主轨
    import shutil as _shutil
    _shutil.rmtree(tmp_path / "data" / "skills_repo" / "gamma-draft")

    enabled_calls = []

    class FakeSvc:
        def get(self, skill_id):
            return {"id": skill_id}

        def set_enabled(self, skill_id, enabled):
            enabled_calls.append((skill_id, enabled))

    import agent.state_manager as state_mod
    monkeypatch.setattr(state_mod, "get_skills_mgmt_service", lambda: FakeSvc(), raising=False)

    from agent.skills_mgmt.transform.register import enable_skill_job
    rep = enable_skill_job(job["job_id"], root=tmp_path)
    assert rep["ok"] is True and rep["enabled"] is True
    assert rep["file_track"] == "" and rep["file_track_patched"] is False
    assert "只有主轨" in rep["note"]
    assert enabled_calls == [("gamma-draft", True)], "没有文件轨也必须把主轨真的启用"
    assert jobs.get_job(job["job_id"], root=tmp_path)["status"] == "enabled"


def test_两轨都没有时如实报找不到(tmp_path, monkeypatch):
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir)
    _patch_intake(monkeypatch)
    register_skill_job(job["job_id"], root=tmp_path)
    import shutil as _shutil
    _shutil.rmtree(tmp_path / "data" / "skills_repo" / "gamma-draft")

    class FakeSvc:
        def get(self, skill_id):
            raise KeyError(skill_id)

        def set_enabled(self, skill_id, enabled):  # pragma: no cover - 不该被调用
            raise AssertionError("主轨没有该技能时不该调 set_enabled")

    import agent.state_manager as state_mod
    monkeypatch.setattr(state_mod, "get_skills_mgmt_service", lambda: FakeSvc(), raising=False)

    from agent.skills_mgmt.transform.register import enable_skill_job
    with pytest.raises(RegisterError) as exc:
        enable_skill_job(job["job_id"], root=tmp_path)
    assert exc.value.code == "skill-not-found"
    assert jobs.get_job(job["job_id"], root=tmp_path)["status"] == "registered"

def test_作业里是旧名字时按_name_解析到主轨_id(tmp_path, monkeypatch):
    """作业 target.id 可能记的是**名字**（或合并前的旧 id）：按 name 唯一命中就该能用。

    【实测来源】2026-11-07 转化队列里 id 为 `brainstorming` 的作业：
    主轨真实 id 是 `pd-brainstorming-697b717a-skill`（name=brainstorming），
    文件轨也在那个 id 下。旧实现只按 id 找 ⇒ 用户只看到「文件轨里没有
    brainstorming/skill.md」，按钮是死胡同。
    """
    tdir = _draft(tmp_path)
    job = _job(tmp_path, tdir)
    _patch_intake(monkeypatch)
    register_skill_job(job["job_id"], root=tmp_path)
    import shutil as _shutil
    _shutil.rmtree(tmp_path / "data" / "skills_repo" / "gamma-draft")
    # 主轨里同一个技能换了 id，name 仍是作业里那个
    (tmp_path / "data" / "skills_repo" / "pd-gamma").mkdir(parents=True)
    (tmp_path / "data" / "skills_repo" / "pd-gamma" / "skill.md").write_text(
        "---\nid: pd-gamma\nname: gamma-draft\nenabled: false\n---\n\n# x\n", encoding="utf-8")

    enabled_calls = []

    class FakeSvc:
        def get(self, skill_id):
            if skill_id != "pd-gamma":
                raise KeyError(skill_id)
            return {"id": skill_id}

        def list_all(self):
            return [types.SimpleNamespace(id="pd-gamma", name="gamma-draft"),
                    types.SimpleNamespace(id="other", name="别的技能")]

        def set_enabled(self, skill_id, enabled):
            enabled_calls.append((skill_id, enabled))

    import agent.state_manager as state_mod
    monkeypatch.setattr(state_mod, "get_skills_mgmt_service", lambda: FakeSvc(), raising=False)

    from agent.skills_mgmt.transform.register import enable_skill_job
    rep = enable_skill_job(job["job_id"], root=tmp_path)
    assert rep["ok"] is True
    assert rep["skill_id"] == "pd-gamma" and rep["job_target_id"] == "gamma-draft"
    assert rep["resolved_by"] == "name"
    assert "已按主轨 id pd-gamma 处理" in rep["note"]
    assert enabled_calls == [("pd-gamma", True)]
    md = (tmp_path / "data" / "skills_repo" / "pd-gamma" / "skill.md").read_text(encoding="utf-8")
    assert "enabled: true" in md, "解析到真实 id 后，那一份文件轨也要同步改"
