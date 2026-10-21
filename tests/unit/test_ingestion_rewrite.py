# -*- coding: utf-8 -*-
"""吞噬 P2：LLM 重写层 / 原文存档 / schema 校验 / 失败回退 / archive 提交。

模型/温度/缓存按 2026-10-21 用户拍板：
- 复用运行时 LLMService（测试用 FakeLLM 注入）；
- temperature=0.2、max_tokens=2000；
- 缓存默认关；CHAT_RESPONSE_CACHE=1 时按 prompt 哈希缓存。
"""
from __future__ import annotations

import json
import sys
import zipfile

import pytest
from flask import Flask

import agent.server_auth as server_auth
import plugins.plugin_api as plugin_api
import plugins.ingestion as ingestion_plugin
from agent.ingestion import rewrite, service
from agent.ingestion.models import (
    LEVEL_L0,
    LEVEL_L2,
    PlanStale,
    RewriteRequired,
)

VALID_OUTPUT = """---
id: beta-rewritten
name: Beta Rewritten
description: 把 Beta 文档整理成可执行的云枢步骤
version: 1.0.0
tags:
  - beta
---
# Beta Rewritten

## 适用场景
当用户需要按 Beta 文档完成一次可重复的整理任务时使用。只使用原文提供的材料，
不引入原文没有的 API 或依赖。

## 触发条件
用户提到 Beta、整理 Beta 文档、按 Beta 流程执行时触发。

## 步骤
1. 读取原文材料。
2. 按原文步骤逐条执行。
3. 输出结构化结果。

## 参数
材料未提供额外参数；如原文有输入，以原文为准。

## 示例
材料未提供示例；按原文步骤执行即可。
"""


class FakeLLM:
    model = "fake-model"

    def __init__(self, output: str = "", error: Exception | None = None):
        self.output = output
        self.error = error
        self.calls = []

    def chat(self, messages, system_prompt="", max_tokens=0, temperature=0):
        self.calls.append({"messages": messages, "system_prompt": system_prompt,
                           "max_tokens": max_tokens, "temperature": temperature})
        if self.error:
            raise self.error
        return self.output


class OneArgFakeLLM:
    model = "one-arg"

    def __init__(self, output: str):
        self.output = output
        self.calls = []

    def chat(self, prompt):
        self.calls.append(prompt)
        return self.output


class FakeInstaller:
    def __init__(self):
        self.calls = []
        self.files = {}

    def install_from_zip(self, zip_path: str):
        self.calls.append(zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            self.files = {name: zf.read(name).decode("utf-8") for name in zf.namelist()}
        manifest = json.loads(self.files["manifest.json"])
        return {"skill_id": manifest["id"], "name": manifest["name"],
                "version": manifest.get("version", "1.0.0"),
                "scripts_count": len([n for n in self.files if n.startswith("scripts/")])}


@pytest.fixture(autouse=True)
def _isolated_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(rewrite, "DEFAULT_ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.delenv("CHAT_RESPONSE_CACHE", raising=False)
    monkeypatch.setattr(service, "_audit", lambda payload: None)
    # P3：插件 preview(dedupe=True) 默认会读真实技能库；P2 测试只关心重写，
    # 这里显式关闭目录读取，保留原生重叠检测（无需真实库）。
    monkeypatch.setattr(service, "_get_skills_service", lambda: None)
    return tmp_path / "archive"


@pytest.fixture()
def doc(tmp_path):
    d = tmp_path / "notes"
    d.mkdir()
    (d / "README.md").write_text(
        "# 普通文档\n\n这是一个普通说明文档，不是技能。\n"
        "它描述了如何整理资料、核对输入、输出结果。\n" * 5,
        encoding="utf-8")
    return d


class TestRewritePreview:
    def test_合法重写生成草稿与存档(self, doc, _isolated_archive):
        fake = FakeLLM(VALID_OUTPUT)
        plan = service.preview(doc, rewrite=True, llm_client=fake)
        assert plan.conformance.level == LEVEL_L2
        assert plan.rewritten is True
        assert plan.requires_rewrite is False
        assert plan.archive_id.startswith("ir_")
        assert plan.skill is not None
        assert plan.skill.id == "beta-rewritten"
        assert "## 适用场景" in plan.skill.body
        assert len(fake.calls) == 1
        assert fake.calls[0]["temperature"] == 0.2
        assert fake.calls[0]["max_tokens"] == 2000
        archive_path = _isolated_archive / (plan.archive_id + ".json")
        assert archive_path.exists()
        archive = rewrite.load_archive(plan.archive_id)
        assert archive["validation"]["ok"] is True
        assert archive["model"]["name"] == "fake-model"
        assert archive["prompt_version"] == rewrite.PROMPT_VERSION
        assert "普通说明文档" in archive["original"]["text"]
        assert archive["skill"]["id"] == "beta-rewritten"

    def test_单参数_chat_客户端兼容(self, doc):
        fake = OneArgFakeLLM(VALID_OUTPUT)
        plan = service.preview(doc, rewrite=True, llm_client=fake)
        assert plan.rewritten is True
        assert fake.calls

    def test_短路输出失败保持_L2_但存档原文(self, doc, _isolated_archive):
        fake = FakeLLM("# 太短\n\n没有章节。")
        plan = service.preview(doc, rewrite=True, llm_client=fake)
        assert plan.rewritten is False
        assert plan.requires_rewrite is True
        assert plan.skill is None
        assert plan.rewrite_errors
        assert any("章节" in e or "过短" in e for e in plan.rewrite_errors)
        archive = rewrite.load_archive(plan.archive_id)
        assert archive["validation"]["ok"] is False
        assert archive["skill"] is None
        assert "普通说明文档" in archive["original"]["text"]

    def test_LLM_异常失败并留证(self, doc):
        fake = FakeLLM(error=RuntimeError("boom"))
        plan = service.preview(doc, rewrite=True, llm_client=fake)
        assert plan.requires_rewrite is True
        assert any("LLM 调用失败" in e for e in plan.rewrite_errors)
        archive = rewrite.load_archive(plan.archive_id)
        assert archive["validation"]["ok"] is False
        assert any("boom" in e for e in archive["validation"]["errors"])

    def test_LLM_不可用也存档(self, doc, monkeypatch):
        monkeypatch.setattr(service, "_default_llm_client", lambda: None)
        plan = service.preview(doc, rewrite=True)
        assert plan.requires_rewrite is True
        assert any("不可用" in e for e in plan.rewrite_errors)
        assert rewrite.load_archive(plan.archive_id) is not None

    def test_L0_忽略_rewrite_不调_LLM(self, tmp_path):
        d = tmp_path / "skill"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nid: alpha\nname: Alpha\ndescription: Alpha 技能\n---\n\n# Alpha\n",
            encoding="utf-8")
        fake = FakeLLM(VALID_OUTPUT)
        plan = service.preview(d, rewrite=True, llm_client=fake)
        assert plan.conformance.level == LEVEL_L0
        assert plan.rewritten is False
        assert plan.archive_id == ""
        assert fake.calls == []


class TestRewriteCommit:
    def test_按存档提交安装且不二次调_LLM(self, doc):
        fake = FakeLLM(VALID_OUTPUT)
        plan = service.preview(doc, rewrite=True, llm_client=fake)
        installer = FakeInstaller()
        result = service.commit(doc, plan_id=plan.plan_id, confirm=True,
                                archive_id=plan.archive_id, installer=installer)
        assert result.skill_id == "beta-rewritten"
        assert result.rewritten is True
        assert result.archive_id == plan.archive_id
        assert len(fake.calls) == 1
        assert len(installer.calls) == 1

    def test_来源变化后旧存档失效(self, doc):
        fake = FakeLLM(VALID_OUTPUT)
        plan = service.preview(doc, rewrite=True, llm_client=fake)
        (doc / "README.md").write_text("# 已改\n\n完全不同的内容。\n", encoding="utf-8")
        with pytest.raises(PlanStale):
            service.commit(doc, plan_id=plan.plan_id, confirm=True,
                           archive_id=plan.archive_id, installer=FakeInstaller())

    def test_未通过校验的存档不能安装(self, doc):
        fake = FakeLLM("# 太短")
        plan = service.preview(doc, rewrite=True, llm_client=fake)
        with pytest.raises(RewriteRequired):
            service.commit(doc, plan_id=plan.plan_id, confirm=True,
                           archive_id=plan.archive_id, installer=FakeInstaller())

    def test_存档缺失或篡改被拒(self, doc):
        fake = FakeLLM(VALID_OUTPUT)
        plan = service.preview(doc, rewrite=True, llm_client=fake)
        missing = "ir_" + "0" * 24
        with pytest.raises(PlanStale):
            service.commit(doc, plan_id=plan.plan_id, confirm=True,
                           archive_id=missing, installer=FakeInstaller())
        archive_path = rewrite.DEFAULT_ARCHIVE_DIR / (plan.archive_id + ".json")
        data = json.loads(archive_path.read_text(encoding="utf-8"))
        data["skill"]["name"] = "被篡改"
        archive_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        assert rewrite.load_archive(plan.archive_id) is None
        with pytest.raises(PlanStale):
            service.commit(doc, plan_id=plan.plan_id, confirm=True,
                           archive_id=plan.archive_id, installer=FakeInstaller())

    def test_L2_不带_archive_被拒(self, doc):
        plan = service.preview(doc)
        with pytest.raises(RewriteRequired):
            service.commit(doc, plan_id=plan.plan_id, confirm=True,
                           installer=FakeInstaller())

    def test_commit_rewrite_必须带_archive(self, doc):
        from agent.ingestion.models import InvalidSource
        with pytest.raises(InvalidSource):
            service.commit(doc, plan_id="ig_x", confirm=True, rewrite=True,
                           installer=FakeInstaller())


class TestSchemaAndCache:
    def test_schema_校验缺章节失败(self):
        from types import SimpleNamespace
        source = SimpleNamespace(path="x", display_name="x")
        asset = SimpleNamespace(files={}, adapter="docs")
        skill, errors, warnings = rewrite.validate_rewrite(
            "# X\n\n太短", source, asset, archive_id="ir_" + "1" * 24, model="m")
        assert skill is None
        assert errors

    def test_代码围栏被剥离(self, doc):
        fence = chr(96) * 3
        fake = FakeLLM(fence + "markdown\n" + VALID_OUTPUT + "\n" + fence)
        plan = service.preview(doc, rewrite=True, llm_client=fake)
        assert plan.rewritten is True
        assert plan.skill.body.startswith("# Beta")

    def test_CHAT_RESPONSE_CACHE_开启时命中(self, doc, monkeypatch, _isolated_archive):
        monkeypatch.setenv("CHAT_RESPONSE_CACHE", "1")
        fake = FakeLLM(VALID_OUTPUT)
        first = service.preview(doc, rewrite=True, llm_client=fake)
        second = service.preview(doc, rewrite=True, llm_client=fake)
        assert len(fake.calls) == 1
        assert first.archive_id == second.archive_id
        assert any("缓存" in w for w in second.warnings)
        assert list((_isolated_archive / "cache").glob("*.json"))


class TestPluginLlmResolver:
    def test_优先使用_app_server_的_llm(self, monkeypatch):
        sentinel = object()
        fake_yunshu = type("Y", (), {"_llm": sentinel})()
        fake_app = type("A", (), {"_Yunshu": fake_yunshu})()
        monkeypatch.setitem(sys.modules, "app_server", fake_app)
        assert ingestion_plugin._resolve_llm_client() is sentinel

    def test_app_server_不可用时回退域模块(self, monkeypatch):
        sentinel = object()
        monkeypatch.delitem(sys.modules, "app_server", raising=False)
        monkeypatch.setattr(service, "_default_llm_client", lambda: sentinel)
        assert ingestion_plugin._resolve_llm_client() is sentinel


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


class TestRewriteHttp:
    def test_preview_rewrite_返回草稿与_archive(self, client, doc, monkeypatch):
        monkeypatch.setattr(ingestion_plugin, "_resolve_llm_client",
                            lambda: FakeLLM(VALID_OUTPUT))
        r = client.post("/api/ingestion/preview",
                        json={"source": str(doc), "rewrite": True}, headers=AUTH)
        assert r.status_code == 200
        plan = r.get_json()["data"]["plan"]
        assert plan["rewritten"] is True
        assert plan["requires_rewrite"] is False
        assert plan["archive_id"].startswith("ir_")
        assert plan["skill"]["id"] == "beta-rewritten"

    def test_commit_archive_走假安装器(self, client, doc, monkeypatch):
        fake_llm = FakeLLM(VALID_OUTPUT)
        fake_installer = FakeInstaller()
        monkeypatch.setattr(ingestion_plugin, "_resolve_llm_client", lambda: fake_llm)
        monkeypatch.setattr(service, "_default_installer", lambda: fake_installer)
        plan = client.post("/api/ingestion/preview",
                           json={"source": str(doc), "rewrite": True},
                           headers=AUTH).get_json()["data"]["plan"]
        r = client.post("/api/ingestion/commit",
                        json={"source": str(doc), "plan_id": plan["plan_id"],
                              "archive_id": plan["archive_id"], "confirm": True},
                        headers=AUTH)
        assert r.status_code == 200
        result = r.get_json()["data"]["result"]
        assert result["rewritten"] is True
        assert result["archive_id"] == plan["archive_id"]
        assert len(fake_installer.calls) == 1

    def test_preview_rewrite_失败仍是_200_但_requires_rewrite(self, client, doc, monkeypatch):
        monkeypatch.setattr(ingestion_plugin, "_resolve_llm_client",
                            lambda: FakeLLM("# 太短"))
        plan = client.post("/api/ingestion/preview",
                           json={"source": str(doc), "rewrite": True},
                           headers=AUTH).get_json()["data"]["plan"]
        assert plan["rewritten"] is False
        assert plan["requires_rewrite"] is True
        assert plan["rewrite_errors"]

    def test_commit_未通过校验返回_409(self, client, doc, monkeypatch):
        monkeypatch.setattr(ingestion_plugin, "_resolve_llm_client",
                            lambda: FakeLLM("# 太短"))
        plan = client.post("/api/ingestion/preview",
                           json={"source": str(doc), "rewrite": True},
                           headers=AUTH).get_json()["data"]["plan"]
        r = client.post("/api/ingestion/commit",
                        json={"source": str(doc), "plan_id": plan["plan_id"],
                              "archive_id": plan["archive_id"], "confirm": True},
                        headers=AUTH)
        assert r.status_code == 409
        assert r.get_json()["type"].endswith("/rewrite-required")
