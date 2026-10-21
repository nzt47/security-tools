# -*- coding: utf-8 -*-
"""吞噬（ingestion）P0：注册表 / 三级判定 / preview / commit / HTTP 契约。

【测试边界】
- 只用 tmp_path 造 fixture，不读真实 `~/.claude` 等目录，不跑 LLM；
- commit 用 FakeInstaller 截获 zip，不写 `data/skills_repo`；
- `service._audit` 被换成 no-op，不写审计链（属 P3/P4 的集成项）。
"""
from __future__ import annotations

import json
import zipfile

import pytest
from flask import Flask

import agent.server_auth as server_auth
import plugins.plugin_api as plugin_api
import plugins.ingestion as ingestion_plugin
from agent.ingestion import service
from agent.ingestion.adapters import Adapter, ExtractedAsset
from agent.ingestion.models import (
    LEVEL_L0,
    LEVEL_L1,
    LEVEL_L2,
    ConfirmationRequired,
    IngestSource,
    InvalidSource,
    PlanStale,
    RewriteRequired,
    SourceNotFound,
    SourceTooLarge,
    UnsupportedSource,
)
from agent.ingestion.registry import AdapterRegistry


# ════════════════════════════════════════════════════════════
#  fixtures / helpers
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    monkeypatch.setattr(service, "_audit", lambda payload: None)


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def l0_skill_json(tmp_path):
    d = tmp_path / "alpha-skill"
    d.mkdir()
    _write(d / "skill.json", json.dumps({
        "id": "alpha-skill",
        "name": "Alpha Skill",
        "description": "处理 Alpha 任务",
        "content": "# Alpha\n\nUse when alpha is needed.\n",
        "version": "2.1.0",
        "tags": ["alpha"],
    }, ensure_ascii=False))
    return d


@pytest.fixture()
def l0_skill_md(tmp_path):
    d = tmp_path / "gamma-skill"
    d.mkdir()
    _write(d / "SKILL.md", "---\nid: gamma-skill\nname: Gamma\ndescription: Gamma 技能\n---\n\n# Gamma\n\n正文。\n")
    return d


@pytest.fixture()
def l1_claude_like(tmp_path):
    d = tmp_path / "beta-skill"
    d.mkdir()
    _write(d / "SKILL.md", "---\nname: Beta Skill\ndescription: 处理 Beta 任务\n---\n\n# Beta\n\n步骤一。\n")
    _write(d / "scripts" / "main.py", "print('beta')\n")
    return d


@pytest.fixture()
def l1_title_summary(tmp_path):
    d = tmp_path / "delta-skill"
    d.mkdir()
    _write(d / "SKILL.md", "---\ntitle: Delta 标题\nsummary: Delta 摘要\n---\n\n# Delta\n")
    return d


@pytest.fixture()
def l1_scripts_only(tmp_path):
    d = tmp_path / "epsilon-skill"
    d.mkdir()
    _write(d / "scripts" / "tool.py", "print('epsilon')\n")
    return d


@pytest.fixture()
def l2_doc(tmp_path):
    d = tmp_path / "notes"
    d.mkdir()
    _write(d / "README.md", "# 一个普通说明\n\n这不是技能，只是文档。\n")
    return d / "README.md"


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
        return {
            "skill_id": self.manifest["id"],
            "name": self.manifest["name"],
            "version": self.manifest.get("version", "1.0.0"),
            "scripts_count": len([n for n in self.files if n.startswith("scripts/")]),
        }


class _FakeAdapter(Adapter):
    def __init__(self, name: str, score: int, *, priority: int = 0, fail: bool = False):
        self.name = name
        self.priority = priority
        self._score = score
        self._fail = fail

    def sniff(self, source):
        if self._fail:
            raise RuntimeError("boom")
        return self._score

    def extract(self, source):
        return ExtractedAsset(adapter=self.name, source_kind="text", body="x")


# ════════════════════════════════════════════════════════════
#  registry：按内容特征打分选择
# ════════════════════════════════════════════════════════════


class TestAdapterRegistry:
    def test_最高分胜出并保留完整打分表(self):
        reg = AdapterRegistry([
            _FakeAdapter("low", 10),
            _FakeAdapter("high", 80),
            _FakeAdapter("zero", 0),
        ])
        sel = reg.select(IngestSource.from_mapping("x"))
        assert sel.name == "high"
        assert sel.score == 80
        assert sel.scores == {"low": 10, "high": 80, "zero": 0}

    def test_同分先比_priority_再按注册顺序(self):
        reg = AdapterRegistry([
            _FakeAdapter("a", 50, priority=0),
            _FakeAdapter("b", 50, priority=2),
            _FakeAdapter("c", 50, priority=2),
        ])
        assert reg.select(IngestSource.from_mapping("x")).name == "b"
        reg2 = AdapterRegistry([
            _FakeAdapter("first", 50),
            _FakeAdapter("second", 50),
        ])
        assert reg2.select(IngestSource.from_mapping("x")).name == "first"

    def test_分数被夹到_0_100(self):
        reg = AdapterRegistry([_FakeAdapter("over", 999), _FakeAdapter("under", -3)])
        assert reg.score(IngestSource.from_mapping("x")) == {"over": 100, "under": 0}

    def test_单个适配器_sniff_抛错被隔离(self):
        reg = AdapterRegistry([_FakeAdapter("bad", 0, fail=True), _FakeAdapter("good", 30)])
        sel = reg.select(IngestSource.from_mapping("x"))
        assert sel.name == "good"
        assert "bad" in sel.errors and "boom" in sel.errors["bad"]

    def test_全部不识别时抛_unsupported_并带打分(self):
        reg = AdapterRegistry([_FakeAdapter("a", 0), _FakeAdapter("b", 0)])
        with pytest.raises(UnsupportedSource) as exc:
            reg.select(IngestSource.from_mapping("x"))
        assert exc.value.status == 422
        assert exc.value.scores == {"a": 0, "b": 0}

    def test_重复注册同名适配器立即失败(self):
        reg = AdapterRegistry([_FakeAdapter("same", 10)])
        with pytest.raises(ValueError):
            reg.register(_FakeAdapter("same", 20))

    def test_默认注册表含三个内置适配器(self):
        names = set(service.registry_mod.get_registry().names())
        assert {"skill_json", "skill_md", "docs"} <= names


# ════════════════════════════════════════════════════════════
#  三级判定 / 归一化
# ════════════════════════════════════════════════════════════


class TestPreviewConformance:
    def test_L0_skill_json_直入(self, l0_skill_json):
        plan = service.preview(l0_skill_json)
        assert plan.adapter == "skill_json"
        assert plan.conformance.level == LEVEL_L0
        assert plan.requires_rewrite is False
        assert plan.skill is not None
        assert plan.skill.id == "alpha-skill"
        assert "直接安装" in " ".join(plan.actions)
        assert plan.skill.scripts == {}

    def test_L0_SKILL_md_含_id_name_description(self, l0_skill_md):
        plan = service.preview(l0_skill_md)
        assert plan.adapter == "skill_md"
        assert plan.conformance.level == LEVEL_L0
        assert plan.skill.id == "gamma-skill"
        assert "正文" in plan.skill.body

    def test_L1_claude_形态补_manifest_并搬脚本(self, l1_claude_like):
        plan = service.preview(l1_claude_like)
        assert plan.adapter == "skill_md"
        assert plan.conformance.level == LEVEL_L1
        assert plan.requires_rewrite is False
        assert plan.skill.id == "beta-skill"
        assert plan.skill.name == "Beta Skill"
        assert "main.py" in plan.skill.scripts
        joined = " ".join(plan.actions)
        assert "补写 manifest.json" in joined
        assert "main.py" in joined

    def test_L1_front_matter_键名映射(self, l1_title_summary):
        plan = service.preview(l1_title_summary)
        assert plan.conformance.level == LEVEL_L1
        assert plan.skill.name == "Delta 标题"
        assert plan.skill.description == "Delta 摘要"
        assert plan.skill.extra["mapped_keys"]["title"] == "name"
        assert plan.skill.extra["mapped_keys"]["summary"] == "description"
        assert "title→name" in " ".join(plan.actions)

    def test_L1_只有脚本生成骨架不编造能力(self, l1_scripts_only):
        plan = service.preview(l1_scripts_only)
        assert plan.conformance.level == LEVEL_L1
        assert plan.skill is not None
        assert "## 脚本" in plan.skill.body
        assert "tool.py" in plan.skill.body
        assert "没有说明" in " ".join(plan.warnings)

    def test_L2_普通文档只预览不可提交(self, l2_doc):
        plan = service.preview(l2_doc)
        assert plan.conformance.level == LEVEL_L2
        assert plan.requires_rewrite is True
        assert plan.skill is None
        assert "尚未触发 LLM 重写" in " ".join(plan.warnings)
        with pytest.raises(RewriteRequired):
            service.commit(l2_doc, plan_id=plan.plan_id, confirm=True,
                           installer=FakeInstaller())

    def test_L2_粘贴原文(self):
        plan = service.preview({"text": "# 一段提示词\n\n照做即可。"})
        assert plan.conformance.level == LEVEL_L2
        assert plan.source["kind"] == "text"
        assert plan.source["chars"] > 0
        assert "text" not in plan.source  # 不回显原文

    def test_计划_JSON_可序列化且正文有上限(self, l1_claude_like):
        plan = service.preview(l1_claude_like)
        payload = plan.to_dict(body_limit=5)
        json.dumps(payload, ensure_ascii=False)
        assert payload["skill"]["body_preview"]["truncated"] is True
        assert len(payload["skill"]["body_preview"]["text"]) == 5


# ════════════════════════════════════════════════════════════
#  plan_id / 输入校验
# ════════════════════════════════════════════════════════════


class TestPlanIdentity:
    def test_同一来源两次预览_plan_id_相同(self, l1_claude_like):
        assert service.preview(l1_claude_like).plan_id == service.preview(l1_claude_like).plan_id

    def test_内容变化后_plan_id_变化且旧计划失效(self, l1_claude_like):
        first = service.preview(l1_claude_like)
        (l1_claude_like / "SKILL.md").write_text(
            "---\nname: Beta Skill\ndescription: 已改动\n---\n\n# Beta\n\n新正文。\n",
            encoding="utf-8")
        second = service.preview(l1_claude_like)
        assert second.plan_id != first.plan_id
        fake = FakeInstaller()
        with pytest.raises(PlanStale):
            service.commit(l1_claude_like, plan_id=first.plan_id, confirm=True, installer=fake)
        assert fake.calls == []

    def test_来源不存在(self, tmp_path):
        with pytest.raises(SourceNotFound):
            service.preview(tmp_path / "nope")

    def test_空目录无法识别(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        with pytest.raises(UnsupportedSource):
            service.preview(d)

    def test_纯文本超过上限(self):
        with pytest.raises(SourceTooLarge):
            service.preview({"text": "x" * (service.MAX_SOURCE_TEXT_CHARS + 1)})

    def test_source_同时给_path_和_text_被拒(self):
        with pytest.raises(InvalidSource):
            service.preview({"path": "a", "text": "b"})


# ════════════════════════════════════════════════════════════
#  commit：复用 install_from_zip
# ════════════════════════════════════════════════════════════


class TestCommit:
    def test_L1_commit_拼出_manifest_skill_md_与脚本(self, l1_claude_like):
        plan = service.preview(l1_claude_like)
        fake = FakeInstaller()
        result = service.commit(l1_claude_like, plan_id=plan.plan_id, confirm=True,
                                installer=fake)
        assert result.skill_id == "beta-skill"
        assert result.scripts_count == 1
        assert result.installed is True
        assert fake.manifest["id"] == "beta-skill"
        assert fake.manifest["source"].startswith("ingestion:")
        assert "skill.md" in fake.files
        assert "scripts/main.py" in fake.files
        # 脚本必须逐字节搬运（Windows 上 write_text 会做 \n→\r\n 翻译，不能用字面量比）
        assert fake.files["scripts/main.py"] == (
            (l1_claude_like / "scripts" / "main.py").read_bytes().decode("utf-8"))
        # skill.md 必须是云枢可解析的 front matter，而不是手拼字符串
        from agent.skills_mgmt.file_store import SkillMDParser
        meta, body = SkillMDParser.parse(fake.files["skill.md"])
        assert meta["id"] == "beta-skill"
        assert "Beta" in body

    def test_未确认不写文件(self, l1_claude_like):
        plan = service.preview(l1_claude_like)
        fake = FakeInstaller()
        with pytest.raises(ConfirmationRequired):
            service.commit(l1_claude_like, plan_id=plan.plan_id, confirm=False, installer=fake)
        assert fake.calls == []

    def test_脚本不被执行(self, l1_claude_like):
        # 脚本内容是一个会写文件的危险动作；commit 只搬运，不 import/exec
        (l1_claude_like / "scripts" / "evil.py").write_text(
            "open('PWNED', 'w').write('x')\n", encoding="utf-8")
        plan = service.preview(l1_claude_like)
        fake = FakeInstaller()
        service.commit(l1_claude_like, plan_id=plan.plan_id, confirm=True, installer=fake)
        assert "scripts/evil.py" in fake.files
        assert not (l1_claude_like / "PWNED").exists()
        assert not (l1_claude_like / "scripts" / "PWNED").exists()

    def test_安装器异常转成_install_failed(self, l1_claude_like):
        plan = service.preview(l1_claude_like)

        class Boom:
            def install_from_zip(self, path):
                raise RuntimeError("disk full")

        from agent.ingestion.models import InstallFailed
        with pytest.raises(InstallFailed):
            service.commit(l1_claude_like, plan_id=plan.plan_id, confirm=True, installer=Boom())


# ════════════════════════════════════════════════════════════
#  HTTP 薄层：鉴权 / 信封 / 参数校验
# ════════════════════════════════════════════════════════════


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FLASK_API_TOKEN", "test-token")
    monkeypatch.setattr(server_auth, "_AUTH_DISABLED_FOR_TEST", False)
    # 绕开 plugin_api 对 app_server 的延迟解析，直接用轻量 server_auth（同语义）
    monkeypatch.setattr(plugin_api, "_resolve_host_decorator",
                        lambda name: getattr(server_auth, name))
    app = Flask(__name__)
    app.register_blueprint(ingestion_plugin.bp)
    return app.test_client()


AUTH = {"Authorization": "Bearer test-token"}


class TestHttpContract:
    def test_预览需要令牌(self, client, l0_skill_json):
        assert client.post("/api/ingestion/preview",
                           json={"source": str(l0_skill_json)}).status_code == 401

    def test_预览返回统一信封与计划(self, client, l1_claude_like):
        r = client.post("/api/ingestion/preview",
                        json={"source": str(l1_claude_like)}, headers=AUTH)
        assert r.status_code == 200
        body = r.get_json()
        assert body["code"] == 200
        assert body["data"]["plan"]["conformance"]["level"] == LEVEL_L1
        assert body["data"]["plan"]["plan_id"].startswith("ig_")
        assert r.headers.get("X-Envelope") == "v2"

    def test_参数错误是_problem_json(self, client):
        r = client.post("/api/ingestion/preview", json={}, headers=AUTH)
        assert r.status_code == 400
        assert r.mimetype == "application/problem+json"
        assert r.get_json()["type"].endswith("/invalid-source")

    def test_commit_未确认返回_400(self, client, l1_claude_like):
        plan = service.preview(l1_claude_like)
        r = client.post("/api/ingestion/commit",
                        json={"source": str(l1_claude_like), "plan_id": plan.plan_id},
                        headers=AUTH)
        assert r.status_code == 400
        assert r.get_json()["type"].endswith("/confirmation-required")

    def test_commit_L2_返回_409_rewrite_required(self, client, l2_doc):
        plan = service.preview(l2_doc)
        r = client.post("/api/ingestion/commit",
                        json={"source": str(l2_doc), "plan_id": plan.plan_id,
                              "confirm": True},
                        headers=AUTH)
        assert r.status_code == 409
        assert r.get_json()["type"].endswith("/rewrite-required")

    def test_commit_成功走假安装器(self, client, l1_claude_like, monkeypatch):
        fake = FakeInstaller()
        monkeypatch.setattr(service, "_default_installer", lambda: fake)
        plan = service.preview(l1_claude_like)
        r = client.post("/api/ingestion/commit",
                        json={"source": str(l1_claude_like), "plan_id": plan.plan_id,
                              "confirm": True},
                        headers=AUTH)
        assert r.status_code == 200
        body = r.get_json()
        assert body["data"]["result"]["skill_id"] == "beta-skill"
        assert len(fake.calls) == 1

    def test_plan_id_缺失被拒(self, client, l0_skill_json):
        r = client.post("/api/ingestion/commit",
                        json={"source": str(l0_skill_json), "confirm": True},
                        headers=AUTH)
        assert r.status_code == 400
        assert r.get_json()["type"].endswith("/invalid-source")
