# -*- coding: utf-8 -*-
"""工具草稿 AI 实现：校验、写回、登记（不执行外部动作）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent.ingestion import toolbuild

GOOD = '''"""demo"""
from typing import Any, Dict

TOOL_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string", "description": "目录"}},
    "required": ["path"],
}


def demo_tool(**kwargs: Any) -> Dict[str, Any]:
    path = str(kwargs.get("path") or "")
    if not path:
        return {"ok": False, "data": None, "error": "missing path"}
    return {"ok": True, "data": {"path": path}, "error": ""}
'''


class FakeLLM:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.text


@pytest.fixture()
def draft(tmp_path):
    root = tmp_path / "drafts" / "ing_tool"
    (root / "agent" / "tools").mkdir(parents=True)
    (root / "data" / "tool_definitions").mkdir(parents=True)
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "source" / "scripts").mkdir(parents=True)
    (root / "source" / "scripts" / "deploy.sh").write_text("echo hi\n", encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "draft_id": "ing_tool", "form": "tool", "status": "draft",
        "enabled": False, "skill_id": "demo_tool", "skill_name": "Demo Tool",
        "source_ref": "", "warnings": ["骨架"],
    }), encoding="utf-8")
    (root / "agent" / "tools" / "demo_tool.py").write_text(
        "TOOL_SCHEMA = {\"type\": \"object\", \"properties\": {\"x\": {\"type\": \"string\"}}}\n\n"
        "def demo_tool(**kwargs):\n    raise NotImplementedError()\n", encoding="utf-8")
    (root / "data" / "tool_definitions" / "demo_tool.yaml").write_text(
        yaml.safe_dump({"name": "demo_tool", "tags": ["draft"],
                        "schema": {"type": "object", "properties": {"x": {"type": "string"}}}}),
        encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _isolate_custom_tools_dir(monkeypatch, tmp_path):
    """register 会把来源脚本复制进 custom/；测试必须落在 tmp_path，禁止写进工作树。

    2026-10-27 实测：两条既有 register 用例没隔离 CUSTOM_TOOLS_DIR，
    直接在 agent/tools/custom/ingested/ 下留下 demo_tool / demo-tool 两个目录。
    """
    monkeypatch.setattr("agent.tools.persistence.CUSTOM_TOOLS_DIR",
                        str(tmp_path / "custom"))


class TestValidate:
    def test_good_code_passes(self):
        v = toolbuild.validate_code(GOOD, "demo_tool")
        assert v["ok"] is True, v["errors"]
        assert v["schema"]["properties"]["path"]

    def test_missing_function_and_bad_schema(self):
        code = "TOOL_SCHEMA = []\n"
        v = toolbuild.validate_code(code, "demo_tool")
        assert v["ok"] is False
        assert any("缺少函数" in e for e in v["errors"])

    def test_dangerous_and_secret_rejected(self):
        code = GOOD.replace("return {\"ok\": True", "import os\n    os.system('x')\n    key = 'sk-ABCDEFGH12345678'\n    return {\"ok\": True")
        v = toolbuild.validate_code(code, "demo_tool")
        assert v["ok"] is False
        assert any("os.system" in e for e in v["errors"])
        assert any("密钥" in e for e in v["errors"])

    def test_schema_with_module_constants_and_annassign(self):
        """LLM 常写 TOOL_SCHEMA: Dict[str, Any] = {...} 且 enum 引用模块常量。

        2026-10-27 实测：deepseek-flash 生成的 schema 用 AnnAssign + _ACTIONS 常量，
        旧校验只认 Assign + ast.literal_eval ⇒ 误报"必须是字面量 dict"。
        """
        code = """
from typing import Any, Dict
_ACTIONS = ["deploy", "list"]
TOOL_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"action": {"type": "string", "enum": _ACTIONS}},
    "required": ["action"],
}
def demo_tool(**kwargs: Any) -> Dict[str, Any]:
    return {"ok": True, "data": None, "error": ""}
"""
        v = toolbuild.validate_code(code, "demo_tool")
        assert v["ok"] is True, v["errors"]
        assert v["schema"]["properties"]["action"]["enum"] == ["deploy", "list"]

    def test_skeleton_still_not_implemented(self):
        v = toolbuild.validate_code("def demo_tool(**kwargs):\n    raise NotImplementedError()\n", "demo_tool")
        assert v["ok"] is False


class TestLLMCall:
    def test_uses_raw_client_with_long_timeout(self):
        from types import SimpleNamespace
        calls = {}

        def create(**kwargs):
            calls.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="code"))])

        class Client:
            model = "deepseek-flash"

            def _is_openai_compat(self):
                return True

            def _get_client(self):
                return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        assert toolbuild._call_llm(Client(), "p") == "code"
        assert calls["max_tokens"] == toolbuild.MAX_TOKENS
        assert calls["timeout"] == toolbuild.LLM_TIMEOUT_SEC

    def test_none_content_returns_empty(self):
        from types import SimpleNamespace

        class Client:
            model = "m"

            def _is_openai_compat(self):
                return True

            def _get_client(self):
                return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
                    create=lambda **kw: SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=None))]))))

        assert toolbuild._call_llm(Client(), "p") == ""

    def test_chat_fallback_when_raw_client_unavailable(self):
        class Client:
            def chat(self, messages, **kwargs):
                return "fallback-code"

        assert toolbuild._call_llm(Client(), "p") == "fallback-code"


class TestBuildApply:
    def test_build_and_apply(self, draft):
        llm = FakeLLM("~~~python\n" + GOOD + "\n~~~")
        out = toolbuild.build("ing_tool", llm, draft_dir=draft.parent)
        assert out["validation"]["ok"] is True
        assert llm.calls, "必须调用 LLM"
        saved = toolbuild.apply("ing_tool", out["code"], out["validation"], draft_dir=draft.parent)
        assert saved["ok"] is True
        tool_file = draft / "agent" / "tools" / "demo_tool.py"
        assert "return {\"ok\": True" in tool_file.read_text(encoding="utf-8")
        meta = yaml.safe_load((draft / "data" / "tool_definitions" / "demo_tool.yaml").read_text(encoding="utf-8"))
        assert meta["schema"]["properties"]["path"]
        assert "ai_implemented" in meta["tags"]
        test_src = (draft / "tests" / "unit" / "test_demo_tool.py").read_text(encoding="utf-8")
        compile(test_src, "test_demo_tool.py", "exec")
        manifest = json.loads((draft / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["implemented"] is True

    def test_materials_include_source_scripts(self, draft):
        m = toolbuild.load_materials("ing_tool", draft_dir=draft.parent)
        assert m["name"] == "demo_tool"
        assert [n for n, _ in m["scripts"]] == ["deploy.sh"]

    def test_build_rejects_invalid_output(self, draft):
        llm = FakeLLM("~~~python\n" + "def demo_tool(**kwargs):\n    raise NotImplementedError()\n" + "\n~~~")
        out = toolbuild.build("ing_tool", llm, draft_dir=draft.parent)
        assert out["validation"]["ok"] is False


class TestRegister:
    def test_register_requires_implementation(self, draft):
        with pytest.raises(toolbuild.BuildError) as ei:
            toolbuild.register("ing_tool", draft_dir=draft.parent)
        assert "NotImplementedError" in ei.value.detail

    def test_register_handles_hyphenated_skill_id(self, draft, monkeypatch):
        """manifest.skill_id 带连字符（byted-bp-...），实际文件是下划线命名。

        2026-10-27 活体实测：apply 成功但 register 报"还没有实现文件"——
        register 仍用原始 skill_id 拼路径。锁住归一化后的查找。
        """
        saved = toolbuild.apply("ing_tool", GOOD,
                                toolbuild.validate_code(GOOD, "demo_tool"),
                                draft_dir=draft.parent)
        assert saved["ok"] is True
        manifest_path = draft / "manifest.json"
        meta = json.loads(manifest_path.read_text(encoding="utf-8"))
        meta["skill_id"] = "demo-tool"
        manifest_path.write_text(json.dumps(meta), encoding="utf-8")

        class FakeGen:
            def generate_persistent(self, *args, **kwargs):
                return True

        monkeypatch.setattr("agent.tools.persistence.persist_dynamic_tool",
                            lambda *a, **k: True)
        out = toolbuild.register("ing_tool", draft_dir=draft.parent, generator=FakeGen())
        assert out["tool_name"] == "demo_tool"

    def test_register_uses_generator_and_persist(self, draft, monkeypatch):
        saved = toolbuild.apply("ing_tool", GOOD,
                                toolbuild.validate_code(GOOD, "demo_tool"),
                                draft_dir=draft.parent)
        assert saved["ok"] is True

        class FakeGen:
            def __init__(self):
                self.calls = []

            def generate_persistent(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return True

        gen = FakeGen()
        persisted = []
        monkeypatch.setattr("agent.tools.persistence.persist_dynamic_tool",
                            lambda *a, **k: persisted.append((a, k)) or True)
        out = toolbuild.register("ing_tool", draft_dir=draft.parent, generator=gen)
        assert out["tool_name"] == "demo_tool"
        assert gen.calls and gen.calls[0][0][0] == "demo_tool"
        assert persisted, "必须写台账/治理 YAML"

    def test_register_copies_source_scripts_into_custom_dir(self, draft, monkeypatch, tmp_path):
        """登记要把来源脚本一起落盘（§11.1）：换机/清空 custom 后仍能找到 deploy.sh。

        生成代码按 custom/<category>/<source_id>/scripts 探测脚本；此前 register
        只写 <name>.py，实测靠人工复制才没炸。台账同时记 assets，便于自省。
        """
        toolbuild.apply("ing_tool", GOOD, toolbuild.validate_code(GOOD, "demo_tool"),
                        draft_dir=draft.parent)
        custom = tmp_path / "custom"
        monkeypatch.setattr("agent.tools.persistence.CUSTOM_TOOLS_DIR", str(custom))

        class FakeGen:
            def generate_persistent(self, *args, **kwargs):
                return True

        captured = {}
        monkeypatch.setattr("agent.tools.persistence.persist_dynamic_tool",
                            lambda *a, **k: captured.update(k) or True)
        out = toolbuild.register("ing_tool", draft_dir=draft.parent, generator=FakeGen())
        copied = custom / "ingested" / "demo_tool" / "scripts" / "deploy.sh"
        assert copied.is_file()
        assert copied.read_text(encoding="utf-8") == "echo hi\n"
        assert captured["assets"] == out["scripts"]
        assert out["scripts"][0].replace("\\", "/").endswith("demo_tool/scripts/deploy.sh")

    def test_register_without_source_scripts_leaves_no_dir(self, draft, monkeypatch, tmp_path):
        import shutil

        shutil.rmtree(draft / "source")
        toolbuild.apply("ing_tool", GOOD, toolbuild.validate_code(GOOD, "demo_tool"),
                        draft_dir=draft.parent)
        custom = tmp_path / "custom"
        monkeypatch.setattr("agent.tools.persistence.CUSTOM_TOOLS_DIR", str(custom))
        captured = {}
        monkeypatch.setattr("agent.tools.persistence.persist_dynamic_tool",
                            lambda *a, **k: captured.update(k) or True)

        class FakeGen:
            def generate_persistent(self, *args, **kwargs):
                return True

        out = toolbuild.register("ing_tool", draft_dir=draft.parent, generator=FakeGen())
        assert out["scripts"] == []
        assert captured["assets"] == []
        assert not (custom / "ingested").exists()


def test_persist_dynamic_tool_records_assets(monkeypatch, tmp_path):
    """台账要能记随工具落盘的来源脚本（assets 只在有值时出现）。"""
    from agent.tools import persistence

    monkeypatch.setattr(persistence, "_read_ledger", lambda: {"version": 1, "tools": {}})
    written = {}
    monkeypatch.setattr(persistence, "_write_ledger", lambda data: written.update(data))
    monkeypatch.setattr(persistence, "TOOL_DEFS_DIR", str(tmp_path))
    assets = ["agent/tools/custom/ingested/demo/scripts/deploy.sh"]
    assert persistence.persist_dynamic_tool(
        "demo_tool", "demo", {"type": "object"}, assets=assets) is True
    assert written["tools"]["demo_tool"]["assets"] == assets

