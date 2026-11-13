# -*- coding: utf-8 -*-
"""首批内置工作流模板（agent/workflow_learning/templates.py，2026-11-13）。

【判据】
1. 模板能通过 LearnedWorkflow 校验、id 唯一；
2. **默认不启用**（enabled=False / status=draft）：落库 ≠ 上线，回放会真的执行工具；
3. 只用只读工具（list_directory / search_files / grep / run_lint / search_memory）；
4. 补种幂等、只补缺：已存在（含用户改过）的 id 一律跳过，不覆盖；
5. 空仓库首次补种后 count = 模板数。
"""
from __future__ import annotations

import json

import pytest

from agent.workflow_learning import templates as tpl
from agent.workflow_learning.models import LearnedWorkflow
from agent.workflow_learning.repository import WorkflowRepository

READONLY_TOOLS = {"list_directory", "search_files", "grep", "run_lint", "search_memory"}


def test_模板都能通过模型校验且_id唯一():
    ids = [item["id"] for item in tpl.BUILTIN_TEMPLATES]
    assert len(ids) == len(set(ids))
    for item in tpl.BUILTIN_TEMPLATES:
        wf = LearnedWorkflow(**item)
        assert wf.steps, wf.id
        assert wf.enabled is False and wf.status == "draft", "落库 ≠ 上线"
        assert all(step.tool_name in READONLY_TOOLS for step in wf.steps), wf.id


def test_空仓库首次补种(tmp_path):
    repo_path = tmp_path / "learned_workflows.json"
    out = tpl.seed_templates(str(repo_path))

    assert out["ok"] is True
    assert len(out["added"]) == len(tpl.BUILTIN_TEMPLATES)
    assert out["total"] == len(tpl.BUILTIN_TEMPLATES)
    saved = json.loads(repo_path.read_text(encoding="utf-8"))
    assert set(saved) == {item["id"] for item in tpl.BUILTIN_TEMPLATES}


def test_补种幂等_第二次全部跳过(tmp_path):
    repo_path = tmp_path / "learned_workflows.json"
    tpl.seed_templates(str(repo_path))
    again = tpl.seed_templates(str(repo_path))

    assert again["added"] == []
    assert set(again["skipped"]) == {item["id"] for item in tpl.BUILTIN_TEMPLATES}
    assert again["total"] == len(tpl.BUILTIN_TEMPLATES)


def test_已存在的同_id不被覆盖(tmp_path):
    repo_path = tmp_path / "learned_workflows.json"
    repo = WorkflowRepository(str(repo_path))
    first = LearnedWorkflow(**tpl.BUILTIN_TEMPLATES[0])
    first.name = "用户改过的名字"
    first.enabled = True
    repo.upsert(first)

    out = tpl.seed_templates(str(repo_path))

    assert out["skipped"] and tpl.BUILTIN_TEMPLATES[0]["id"] in out["skipped"]
    kept = WorkflowRepository(str(repo_path)).get(first.id)
    assert kept is not None and kept.name == "用户改过的名字" and kept.enabled is True


def test_CLI_seed输出_json(capsys, monkeypatch):
    monkeypatch.setattr(tpl, "seed_templates",
                        lambda path=None: {"ok": True, "added": ["tpl-x"],
                                           "skipped": [], "total": 1})
    code = tpl.main(["--seed", "--json"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 0 and payload["added"] == ["tpl-x"]
