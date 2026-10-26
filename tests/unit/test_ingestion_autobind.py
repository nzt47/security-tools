# -*- coding: utf-8 -*-
"""吞噬草稿：AI 自动绑定 + 登记为工作流（不注册/不启用）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent.ingestion import autobind, drafts, tool_catalog


def _tool_yaml(root: Path, name: str, params: dict, required: list) -> None:
    (root / (name + ".yaml")).write_text(
        yaml.safe_dump({
            "name": name,
            "category": "test",
            "description": name + " 的说明",
            "schema": {"type": "object", "properties": params, "required": required},
        }, allow_unicode=True),
        encoding="utf-8",
    )


@pytest.fixture()
def tool_dir(tmp_path):
    d = tmp_path / "tools"
    d.mkdir()
    _tool_yaml(d, "search_files", {"pattern": {"type": "string"}}, ["pattern"])
    _tool_yaml(d, "read_file", {"path": {"type": "string"}}, ["path"])
    _tool_yaml(d, "write_file", {"path": {"type": "string"}, "content": {"type": "string"}}, ["path"])
    return d


def _workflow(steps=3):
    return {
        "id": "demo-flow",
        "name": "Demo Flow",
        "description": "演示",
        "trigger_patterns": ["demo"],
        "requires_binding": True,
        "steps": [
            {"step_id": f"step_{i}", "description": f"步骤 {i}", "tool_name": "",
             "params_template": {}, "output_key": f"step_{i}_output"}
            for i in range(1, steps + 1)
        ],
    }


class FakeLLM:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.text


class TestCatalog:
    def test_reads_yaml_names_and_params(self, tool_dir):
        cat = tool_catalog.load_catalog(str(tool_dir))
        names = {t.name for t in cat}
        assert names == {"search_files", "read_file", "write_file"}
        sf = [t for t in cat if t.name == "search_files"][0]
        assert sf.params == ("pattern",) and sf.required == ("pattern",)
        assert "params=pattern" in sf.compact()

    def test_steps_reference_only_real_tool_names(self, tool_dir):
        names = tool_catalog.tool_names(str(tool_dir))
        steps = ["用 search_files 找到日志", "读文件", "调用 write_file 落盘"]
        assert tool_catalog.steps_referencing_tools(steps, names) == 2
        assert tool_catalog.steps_referencing_tools(["搜索文件", "汇总"], names) == 0


class TestBindings:
    def test_all_bound_clears_requires_binding(self, tool_dir):
        names = tool_catalog.tool_names(str(tool_dir))
        wf, report = autobind.apply_bindings(_workflow(2), [
            {"step_id": "step_1", "tool_name": "search_files", "params_template": {"pattern": "$input"}},
            {"step_id": "step_2", "tool_name": "write_file", "params_template": {"path": "out.md"}},
        ], names=names, model="fake")
        assert report["bound"] == 2 and report["total"] == 2 and not report["unbound"]
        assert wf["requires_binding"] is False
        assert wf["steps"][0]["tool_name"] == "search_files"
        assert wf["binding_meta"]["bound"] == 2

    def test_unknown_tool_is_dropped_and_reported(self, tool_dir):
        names = tool_catalog.tool_names(str(tool_dir))
        wf, report = autobind.apply_bindings(_workflow(2), [
            {"step_id": "step_1", "tool_name": "made_up_tool", "params_template": {}},
        ], names=names)
        assert wf["steps"][0]["tool_name"] == ""
        assert wf["requires_binding"] is True
        assert [u["step_id"] for u in report["unbound"]] == ["step_1", "step_2"]

    def test_missing_binding_is_unbound_with_reason(self, tool_dir):
        names = tool_catalog.tool_names(str(tool_dir))
        wf, report = autobind.apply_bindings(_workflow(2), [
            {"step_id": "step_1", "tool_name": "read_file", "params_template": {"path": "a"}},
            {"step_id": "step_2", "tool_name": "", "reason": "没有合适的工具"},
        ], names=names)
        assert report["bound"] == 1
        reasons = {u["step_id"]: u["reason"] for u in report["unbound"]}
        assert "没有合适的工具" in reasons["step_2"]

    def test_parse_fenced_json(self):
        assert autobind.parse_response("```json\n{\"bindings\": []}\n```") == []
        with pytest.raises(autobind.BindError):
            autobind.parse_response("不是 JSON")


class TestPropose:
    def test_propose_uses_llm_and_validates(self, tool_dir):
        payload = json.dumps({"bindings": [
            {"step_id": "step_1", "tool_name": "search_files", "params_template": {"pattern": "$input"}},
            {"step_id": "step_2", "tool_name": "write_file", "params_template": {"path": "r.md"}},
        ]})
        llm = FakeLLM(payload)
        wf, report = autobind.propose(_workflow(2), llm, tool_dir=str(tool_dir))
        assert report["bound"] == report["total"] == 2
        assert llm.calls, "必须真的调用 LLM"
        prompt = llm.calls[0][0][0]["content"]
        assert "search_files" in prompt and "step_1" in prompt

    def test_propose_without_llm_is_503(self, tool_dir):
        with pytest.raises(autobind.BindError) as ei:
            autobind.propose(_workflow(2), None, tool_dir=str(tool_dir))
        assert int(ei.value.status) == 503

    def test_prompt_contains_budget_guard(self, tool_dir):
        with pytest.raises(autobind.BindError):
            autobind.build_prompt(_workflow(autobind.MAX_STEPS + 1), tool_dir=str(tool_dir))


class TestRegister:
    def test_unbound_is_rejected(self, tool_dir):
        names = tool_catalog.tool_names(str(tool_dir))
        with pytest.raises(autobind.BindError) as ei:
            autobind.build_learned_workflow(_workflow(2), names=names)
        assert "未绑定步骤" in ei.value.detail

    def test_all_bound_builds_disabled_workflow(self, tool_dir):
        names = tool_catalog.tool_names(str(tool_dir))
        wf, _ = autobind.apply_bindings(_workflow(2), [
            {"step_id": "step_1", "tool_name": "search_files", "params_template": {"pattern": "x"}},
            {"step_id": "step_2", "tool_name": "write_file", "params_template": {"path": "y"}},
        ], names=names, model="fake")
        learned = autobind.build_learned_workflow(wf, draft_id="ing_test", names=names)
        assert learned.id == "demo-flow"
        assert learned.enabled is False
        assert learned.status == "active"
        assert learned.workflow_type == "toolchain"
        assert [s.tool_name for s in learned.steps] == ["search_files", "write_file"]

    def test_register_rejects_unknown_tool(self, tool_dir):
        names = tool_catalog.tool_names(str(tool_dir))
        wf = _workflow(1)
        wf["steps"][0]["tool_name"] = "ghost_tool"
        with pytest.raises(autobind.BindError) as ei:
            autobind.build_learned_workflow(wf, names=names)
        assert "未知工具" in ei.value.detail


class TestDraftIO:
    def test_save_workflow_updates_manifest(self, tmp_path):
        root = tmp_path / "drafts" / "ing_demo"
        (root / "workflows").mkdir(parents=True)
        wf = _workflow(1)
        (root / "workflows" / "demo.json").write_text(json.dumps(wf), encoding="utf-8")
        (root / "manifest.json").write_text(json.dumps({"draft_id": "ing_demo", "form": "workflow",
                                                       "warnings": ["旧"]}), encoding="utf-8")
        wf["steps"][0]["tool_name"] = "search_files"
        saved = drafts.save_workflow("ing_demo", wf, warnings=["新"], draft_dir=tmp_path / "drafts")
        assert saved["bound"] == 1 and saved["total"] == 1
        got = drafts.load_workflow("ing_demo", draft_dir=tmp_path / "drafts")
        assert got["manifest"]["warnings"] == ["新"]
        assert got["manifest"]["requires_binding"] is False
        assert got["workflow"]["steps"][0]["tool_name"] == "search_files"
        rows = drafts.list_drafts(tmp_path / "drafts")
        assert rows[0]["bound_steps"] == 1 and rows[0]["total_steps"] == 1
