# -*- coding: utf-8 -*-
"""吞噬 P1：生态/包格式适配器 + 本机源探测 + sources 端点。

覆盖真实实测形态（2026-10-21）：
- claude：`.claude/skills/<id>/SKILL.md`（name/description）；
- openclaw：`.openclaw/workspace/skills/<id>/` + `_meta.json` + `skill-card.md`；
- trae：`.trae-cn/skills/<id>/` 同 ClawHub 形态（无生态独有 front matter 键）；
- trae/openclaw 的坏 YAML（description 含未加引号的冒号）按行容错恢复；
- zip：manifest.json + skill.md（L0）、只有 SKILL.md（L1）、任意文档（L2）；
- JS 插件包：只抽取 README/commands/skills 清单，L2，不执行。
"""
from __future__ import annotations

import json
import os
import zipfile

import pytest
from flask import Flask

import agent.server_auth as server_auth
import plugins.plugin_api as plugin_api
import plugins.ingestion as ingestion_plugin
from agent.ingestion import service
from agent.ingestion.models import LEVEL_L0, LEVEL_L1, LEVEL_L2, RewriteRequired
from agent.ingestion.sources import RootSpec, discover, parse_scan_roots


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _skill_md(name: str, description: str, body: str = "# Skill\n\n正文。\n") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    monkeypatch.setattr(service, "_audit", lambda payload: None)


class FakeInstaller:
    def __init__(self) -> None:
        self.calls = []
        self.files = {}
        self.manifest = {}

    def install_from_zip(self, zip_path: str):
        self.calls.append(zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            self.files = {name: zf.read(name).decode("utf-8") for name in zf.namelist()}
        self.manifest = json.loads(self.files["manifest.json"])
        return {"skill_id": self.manifest["id"], "name": self.manifest["name"],
                "version": self.manifest.get("version", "1.0.0"),
                "scripts_count": len([n for n in self.files if n.startswith("scripts/")])}


def _make_zip(path, files: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)


# ════════════════════════════════════════════════════════════
#  生态目录适配器（实测形态）
# ════════════════════════════════════════════════════════════


class TestEcosystemAdapters:
    def test_claude_目录按路径提示选中(self, tmp_path):
        d = tmp_path / ".claude" / "skills" / "alpha"
        _write(d / "SKILL.md", _skill_md("alpha", "Alpha 技能"))
        plan = service.preview(d)
        assert plan.adapter == "claude"
        assert plan.conformance.level == LEVEL_L1
        assert plan.skill.id == "alpha"
        assert plan.skill.extra["ecosystem"] == "claude"

    def test_openclaw_读_meta_json_的_slug_与_version(self, tmp_path):
        d = tmp_path / ".openclaw" / "workspace" / "skills" / "pdf"
        _write(d / "SKILL.md", "---\nname: pdf\ndescription: PDF 工具\nlicense: MIT\n---\n\n# PDF\n")
        _write(d / "_meta.json", json.dumps({"slug": "pdf", "version": "0.1.4", "ownerId": "o1"}))
        _write(d / "skill-card.md", "## Description\nPDF")
        plan = service.preview(d)
        assert plan.adapter == "openclaw"
        assert plan.conformance.level == LEVEL_L0
        assert plan.skill.id == "pdf"
        assert plan.skill.version == "0.1.4"
        assert plan.skill.extra["clawhub_meta"]["slug"] == "pdf"

    def test_openclaw_front_matter_metadata_作为内容证据(self, tmp_path):
        d = tmp_path / "generic" / "beta"
        _write(d / "SKILL.md",
               "---\nname: beta\ndescription: Beta\nmetadata:\n  openclaw:\n    emoji: 1\n---\n\n# Beta\n")
        plan = service.preview(d)
        assert plan.adapter == "openclaw"

    def test_trae_目录按路径提示选中(self, tmp_path):
        d = tmp_path / ".trae-cn" / "skills" / "gamma"
        _write(d / "SKILL.md", _skill_md("gamma", "Gamma 技能"))
        _write(d / "_meta.json", json.dumps({"slug": "gamma", "version": "2.0.0", "ownerId": "o2"}))
        plan = service.preview(d)
        assert plan.adapter == "trae"
        assert plan.conformance.level == LEVEL_L0
        assert plan.skill.version == "2.0.0"
        assert plan.skill.extra["ecosystem"] == "trae"

    def test_trae_坏_yaml_按行容错恢复(self, tmp_path):
        d = tmp_path / ".trae-cn" / "skills" / "delta"
        # 实测形态：description 里含未加引号的 ": " ⇒ PyYAML ScannerError
        _write(d / "SKILL.md",
               "---\nname: delta\ndescription: Improve code. Chinese triggers: 重构、清理代码\n---\n\n# Delta\n")
        _write(d / "_meta.json", json.dumps({"slug": "delta", "version": "0.1.0"}))
        plan = service.preview(d)
        assert plan.adapter == "trae"
        assert plan.skill.name == "delta"
        assert "Chinese triggers" in plan.skill.description
        assert any("容错" in w for w in plan.warnings)

    def test_clawhub_通用目录不冒充_trae_openclaw(self, tmp_path):
        d = tmp_path / "generic" / "epsilon"
        _write(d / "SKILL.md", _skill_md("epsilon", "Epsilon"))
        _write(d / "_meta.json", json.dumps({"slug": "epsilon", "version": "1.0.0"}))
        plan = service.preview(d)
        assert plan.adapter == "skill_md"
        assert plan.skill.extra["ecosystem"] == ""

    def test_第二段_front_matter_合并并从正文剥离(self, tmp_path):
        d = tmp_path / ".openclaw" / "workspace" / "skills" / "flow"
        _write(d / "SKILL.md",
               "---\nname: flow\ndescription: Flow\n---\n"
               "---\nsummary: Flow summary\ntags:\n  - automation\n---\n\n# Flow\n")
        plan = service.preview(d)
        assert plan.adapter == "openclaw"
        assert "automation" in plan.skill.tags
        assert not plan.skill.body.lstrip().startswith("---")
        assert any("第二段" in w for w in plan.warnings)


# ════════════════════════════════════════════════════════════
#  zip
# ════════════════════════════════════════════════════════════


class TestZipAdapter:
    def test_zip_manifest_加_skill_md_为_L0(self, tmp_path):
        z = tmp_path / "alpha.zip"
        _make_zip(z, {
            "manifest.json": json.dumps({"id": "alpha-zip", "name": "Alpha", "description": "Zip 技能",
                                         "version": "1.2.3", "category": "custom"}),
            "skill.md": _skill_md("Alpha", "Zip 技能"),
            "scripts/main.py": "print('zip')\n",
        })
        plan = service.preview(z)
        assert plan.adapter == "zip"
        assert plan.conformance.level == LEVEL_L0
        assert plan.skill.id == "alpha-zip"
        assert plan.skill.version == "1.2.3"
        assert "main.py" in plan.skill.scripts
        fake = FakeInstaller()
        result = service.commit(z, plan_id=plan.plan_id, confirm=True, installer=fake)
        assert result.skill_id == "alpha-zip"
        assert "scripts/main.py" in fake.files

    def test_zip_只有_SKILL_md_为_L1(self, tmp_path):
        z = tmp_path / "beta.zip"
        _make_zip(z, {"SKILL.md": _skill_md("beta", "Beta")})
        plan = service.preview(z)
        assert plan.adapter == "zip"
        assert plan.conformance.level == LEVEL_L1
        assert "补写 manifest.json" in " ".join(plan.actions)

    def test_zip_任意文档为_L2_且不可提交(self, tmp_path):
        z = tmp_path / "docs.zip"
        _make_zip(z, {"README.md": "# 普通文档\n\n不是技能。"})
        plan = service.preview(z)
        assert plan.conformance.level == LEVEL_L2
        with pytest.raises(RewriteRequired):
            service.commit(z, plan_id=plan.plan_id, confirm=True, installer=FakeInstaller())

    def test_zip_路径穿越条目被跳过(self, tmp_path):
        z = tmp_path / "evil.zip"
        _make_zip(z, {
            "manifest.json": json.dumps({"id": "evil-zip", "name": "Evil"}),
            "skill.md": _skill_md("Evil", "Evil"),
            "../evil.py": "open('PWNED','w')\n",
        })
        plan = service.preview(z)
        assert plan.conformance.level == LEVEL_L0
        assert any("不安全" in w for w in plan.warnings)
        assert "evil.py" not in plan.raw.get("files", [])
        assert not (tmp_path / "PWNED").exists()


# ════════════════════════════════════════════════════════════
#  JS 插件包（只抽取不执行）
# ════════════════════════════════════════════════════════════


class TestJsPluginAdapter:
    def _plugin(self, tmp_path):
        d = tmp_path / "js-plugin"
        _write(d / "package.json", json.dumps({
            "name": "demo-plugin", "version": "0.3.0", "description": "演示 JS 插件",
            "bin": {"demo": "./cli.js"},
        }))
        _write(d / "README.md", "# Demo\n\n这是插件说明。\n")
        _write(d / "commands" / "hello.md", "# hello\n")
        _write(d / "skills" / "foo" / "SKILL.md", _skill_md("foo", "Foo"))
        _write(d / "index.js", "throw new Error('must not execute')\n")
        return d

    def test_JS_插件包为_L2_且只抽取清单(self, tmp_path):
        d = self._plugin(tmp_path)
        plan = service.preview(d)
        assert plan.adapter == "js_plugin"
        assert plan.conformance.level == LEVEL_L2
        assert plan.skill is None
        assert plan.raw["js_plugin"] is True
        assert "hello.md" in plan.raw["commands"]
        assert "foo/" in plan.raw["skills"]
        assert any("不能执行" in w for w in plan.warnings)
        with pytest.raises(RewriteRequired):
            service.commit(d, plan_id=plan.plan_id, confirm=True, installer=FakeInstaller())

    def test_普通_node_项目不被误认(self, tmp_path):
        d = tmp_path / "plain-node"
        _write(d / "package.json", json.dumps({"name": "plain", "version": "1.0.0"}))
        _write(d / "index.js", "console.log(1)\n")
        from agent.ingestion.models import UnsupportedSource
        with pytest.raises(UnsupportedSource):
            service.preview(d)


# ════════════════════════════════════════════════════════════
#  本机源探测
# ════════════════════════════════════════════════════════════


class TestSources:
    def _roots(self, tmp_path):
        claude = tmp_path / "roots" / "claude"
        openclaw = tmp_path / "roots" / "openclaw"
        _write(claude / "alpha" / "SKILL.md", _skill_md("alpha", "Alpha"))
        (claude / "empty").mkdir(parents=True)
        _write(openclaw / "pdf" / "SKILL.md", _skill_md("pdf", "PDF"))
        _write(openclaw / "pdf" / "_meta.json", json.dumps({"slug": "pdf", "version": "1.0.0"}))
        missing = tmp_path / "roots" / "missing"
        return claude, openclaw, missing

    def test_环境变量根解析与缺失根如实报(self, tmp_path, monkeypatch):
        claude, openclaw, missing = self._roots(tmp_path)
        raw = os.pathsep.join([f"claude={claude}", f"openclaw={openclaw}", str(missing)])
        monkeypatch.setenv("INGEST_SCAN_ROOTS", raw)
        specs = parse_scan_roots()
        assert [(s.ecosystem) for s in specs] == ["claude", "openclaw", "generic"]
        data = discover()
        assert data["stats"]["roots"] == 3
        assert data["stats"]["missing_roots"] == 1
        assert data["stats"]["candidates"] == 2
        by = {(c["ecosystem"], c["name"]): c for c in data["candidates"]}
        assert by[("claude", "alpha")]["kind"] == "skill-md"
        assert by[("openclaw", "pdf")]["kind"] == "skill-md"
        assert data["roots"][2]["error"] == "not-found"

    def test_直接传_RootSpec(self, tmp_path):
        claude, _openclaw, _missing = self._roots(tmp_path)
        data = discover(roots=[RootSpec("claude", str(claude), source="test")])
        assert data["stats"]["candidates"] == 1
        assert data["candidates"][0]["ecosystem"] == "claude"

    def test_发现的候选可直接预览(self, tmp_path):
        claude, _openclaw, _missing = self._roots(tmp_path)
        data = discover(roots=[RootSpec("claude", str(claude), source="test")])
        candidate = data["candidates"][0]
        # 自定义根不在 .claude 路径下 ⇒ 前端把候选的 ecosystem 作为提示带回
        plan = service.preview({"path": candidate["path"],
                                "ecosystem": candidate["ecosystem"]})
        assert plan.adapter == "claude"
        assert plan.skill.id == "alpha"

    def test_默认根不带未配置的路径(self, monkeypatch):
        monkeypatch.delenv("INGEST_SCAN_ROOTS", raising=False)
        data = discover()
        assert data["stats"]["roots"] >= 3
        assert all(row["ecosystem"] in ("claude", "openclaw", "trae") for row in data["roots"])


# ════════════════════════════════════════════════════════════
#  GET /api/ingestion/sources
# ════════════════════════════════════════════════════════════


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


class TestSourcesHttp:
    def test_需要令牌(self, client):
        assert client.get("/api/ingestion/sources").status_code == 401

    def test_返回根与候选(self, client, tmp_path, monkeypatch):
        claude = tmp_path / "claude"
        _write(claude / "alpha" / "SKILL.md", _skill_md("alpha", "Alpha"))
        monkeypatch.setenv("INGEST_SCAN_ROOTS", f"claude={claude}")
        r = client.get("/api/ingestion/sources", headers=AUTH)
        assert r.status_code == 200
        body = r.get_json()
        data = body["data"]["sources"]
        assert data["stats"]["candidates"] == 1
        assert data["candidates"][0]["name"] == "alpha"
        assert data["roots"][0]["ecosystem"] == "claude"
