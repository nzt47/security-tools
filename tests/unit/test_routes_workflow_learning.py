"""工作流学习 HTTP 路由测试（技能面板操作按钮对应的 API）。

覆盖：
    - convert-to-skill 质量门控失败应返回 400（此前 WorkflowConvertError
      继承裸 Exception，漏到末位 except → 500，前端按钮报 HTTP 500）
    - _svc 注入 tool_executor（execute 可直接执行）
"""

import pathlib

import pytest

from flask import Flask

from agent.server_routes import routes_workflow_learning as rwl
from agent.workflow_learning import WorkflowLearningService
from agent.workflow_learning.models import LearningRecord


@pytest.fixture
def wf_svc(tmp_path):
    svc = WorkflowLearningService(repo_path=str(tmp_path / "wf.json"))
    svc.set_tool_executor(lambda t, p: {"ok": True, "tool": t})
    wf = svc.learn_from_interaction(LearningRecord(
        session_id="route-test",
        user_input="统计文件行数并保存",
        tool_calls=[
            {"name": "read_file", "params": {"path": "/a.txt"},
             "success": True},
            {"name": "write_file", "params": {}, "success": True},
        ],
        success=True))
    return svc, wf


@pytest.fixture
def client(wf_svc, tmp_path, monkeypatch):
    svc, _ = wf_svc
    # 【隔离】convert 内部 _resolve_skills_service 走全局单例(生产主轨)——
    # 不注入隔离 skills 会把测试产物写进生产 data/skills_mgmt.json。
    from agent.skills_mgmt import SkillsMgmtService
    iso_skills = SkillsMgmtService(
        store_path=str(tmp_path / "skills_mgmt.json"),
        repo_path=str(tmp_path / "skills_repo"))
    monkeypatch.setattr(
        "agent.state_manager.get_skills_mgmt_service",
        lambda: iso_skills)
    app = Flask(__name__)
    app.config["TESTING"] = True
    orig_svc = rwl._svc
    rwl._svc = lambda: svc
    rwl.register_routes(app, None)
    c = app.test_client()
    yield c, svc
    rwl._svc = orig_svc


class TestConvertToSkillRoute:
    def test_quality_gate_returns_400(self, client, wf_svc):
        """未达标 workflow 转技能 → 400 QUALITY_GATE_FAILED（非 500）。"""
        c, svc = client
        wf = wf_svc[1]
        r = c.post(
            f"/api/workflow-learning/workflows/{wf.id}/convert-to-skill",
            json={"force": False})
        assert r.status_code == 400
        body = r.get_json()
        assert body.get("code") == "QUALITY_GATE_FAILED"

    def test_convert_success_returns_200(self, client, wf_svc, tmp_path):
        """达标(force=True) 转技能 → 200。"""
        c, svc = client
        wf = wf_svc[1]
        r = c.post(
            f"/api/workflow-learning/workflows/{wf.id}/convert-to-skill",
            json={"force": True, "auto_digest": False})
        assert r.status_code == 200
        body = r.get_json()
        assert body.get("ok") is True
        assert body.get("skill_id")


class TestExecuteRoute:
    def test_execute_missing_task_text_400(self, client, wf_svc):
        c, svc = client
        wf = wf_svc[1]
        r = c.post(f"/api/workflow-learning/execute/{wf.id}", json={})
        assert r.status_code == 400

    def test_execute_with_task_text_runs(self, client, wf_svc):
        """带 task_text 且已注入 tool_executor → 执行成功。"""
        c, svc = client
        wf = wf_svc[1]
        r = c.post(f"/api/workflow-learning/execute/{wf.id}",
                   json={"task_text": "统计文件行数并保存"})
        assert r.status_code == 200
        body = r.get_json()
        assert body.get("ok") is True
        result = body.get("result", {})
        assert result.get("matched") is True
        assert result.get("success") is True
