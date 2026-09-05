"""过程蒸馏 HTTP 路由单元测试。

用独立 Flask app 注册路由（不启动 app_server），验证：
    - 路由注册（/api/process-distill/health、/distill）
    - health 返回服务能力
    - distill 无素材 400；有素材走规则降级返回 200（注入隔离服务）
"""

import json
import pathlib

import pytest

# 待 distill 服务单例注入：monkeypatch routes 模块的 _get_service


def _make_app():
    from flask import Flask
    from agent.server_routes.routes_process_distill import register_routes
    app = Flask(__name__)
    app.config["TESTING"] = True
    register_routes(app, None)
    return app


@pytest.fixture
def app():
    return _make_app()


@pytest.fixture
def iso_distill_service(tmp_path):
    """构造隔离门面（临时仓库 + 规则降级，不触网/不写生产）。"""
    from agent.process_distill.service import ProcessDistillService
    from agent.skills_mgmt import SkillsMgmtService
    from agent.workflow_learning.generator import WorkflowGenerator
    from agent.workflow_learning.matcher import WorkflowMatcher
    from agent.workflow_learning.repository import WorkflowRepository

    repo = WorkflowRepository(path=str(tmp_path / "wf.json"))
    wf_svc = type("_WF", (), {
        "get": lambda self, wid: repo.get(wid),
        "generator": WorkflowGenerator(repo, WorkflowMatcher()),
    })()
    skills_svc = SkillsMgmtService(
        store_path=str(tmp_path / "skills.json"),
        repo_path=str(tmp_path / "skills_repo"),
    )
    return ProcessDistillService(use_default_llm=False,
                                 wf_svc=wf_svc, skills_svc=skills_svc)


class TestProcessDistillRoutes:
    def test_routes_registered(self, app):
        rules = sorted(str(r) for r in app.url_map.iter_rules())
        assert "/api/process-distill/health" in rules
        assert "/api/process-distill/distill" in rules

    def test_health(self, app):
        c = app.test_client()
        r = c.get("/api/process-distill/health")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert set(body["artifacts"]) == {"workflow", "skill"}

    def test_distill_requires_input(self, app, monkeypatch):
        from agent.server_routes import routes_process_distill as mod
        monkeypatch.setattr(mod, "_get_service", lambda: None)
        c = app.test_client()
        r = c.post("/api/process-distill/distill",
                   json={"query": "", "paths": []})
        assert r.status_code == 400
        assert r.get_json()["code"] == "VALIDATION_ERROR"

    def test_distill_paths_rule_ok(self, app, monkeypatch, tmp_path,
                                   iso_distill_service):
        from agent.server_routes import routes_process_distill as mod
        monkeypatch.setattr(mod, "_get_service",
                            lambda: iso_distill_service)
        src = tmp_path / "sop.md"
        src.write_text("# SOP\n\n1. 拉代码\n2. 跑测试\n3. 重启\n",
                       encoding="utf-8")
        c = app.test_client()
        r = c.post("/api/process-distill/distill",
                   json={"paths": [str(src)], "artifacts": ["skill"]})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["process"]["method"] == "rule"
        assert body["artifacts"]["skill"]["action"] == "created"
        # 素材已瘦身（无全文）
        for m in body["materials"]:
            assert "content" not in m

    def test_distill_malformed_json(self, app, monkeypatch,
                                    iso_distill_service):
        from agent.server_routes import routes_process_distill as mod
        monkeypatch.setattr(mod, "_get_service",
                            lambda: iso_distill_service)
        c = app.test_client()
        # 非法 JSON body
        r = c.post("/api/process-distill/distill",
                   data="{not json", content_type="application/json")
        # silent=True → data 为空 → 无 query/paths → 400
        assert r.status_code == 400
