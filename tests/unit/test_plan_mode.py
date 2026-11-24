"""E1-1 计划模式：状态机 / 持久化 / 版本 / API（2026-11-25）

钉死的行为（对应设计页 `docs/plan/2026-11-25-e1-1-plan-mode-design.md`）：
  · 未进入计划模式时，任何"记录计划"都不得改状态；
  · 计划全文落 Markdown、状态与版本落 state.json；重启（重新读盘）后状态仍在；
  · 批准 → 退出计划模式；拒绝/反馈 → 仍留在计划模式继续规划；
  · 回滚把旧版本提升为**新版本**（版本号只增），不删除历史；
  · 会话键非法时哈希，绝不让两个会话映射到同一目录（路径穿越 + 串台）。
"""
from __future__ import annotations

import json
import os

import pytest

from agent.context import plan_mode


@pytest.fixture(autouse=True)
def _isolated_root(tmp_path):
    plan_mode.set_root(str(tmp_path / "plan_mode"))
    yield
    plan_mode.set_root(None)


@pytest.fixture()
def session_id():
    return "sess-plan-1"


class TestStateMachine:
    def test_默认态是_idle_且不落文件(self, session_id):
        st = plan_mode.get_state(session_id)
        assert st["plan_mode"] is False
        assert st["status"] == "idle"
        assert st["version"] == 0
        assert st["plan"] == ""
        assert not os.path.exists(os.path.join(plan_mode.get_root(), session_id))

    def test_进入计划模式_落_markdown_占位(self, session_id):
        st = plan_mode.enter(session_id)
        assert st["plan_mode"] is True
        assert st["status"] == "planning"
        plan_file = os.path.join(plan_mode.get_root(), session_id, "plan.md")
        assert os.path.isfile(plan_file)
        assert "计划模式" in open(plan_file, encoding="utf-8").read()

    def test_非计划模式下记录回复不改变任何状态(self, session_id):
        assert plan_mode.record_turn_plan(session_id, "一段回答") is None
        st = plan_mode.get_state(session_id)
        assert st["version"] == 0 and st["status"] == "idle"

    def test_记录回复生成版本并待批准(self, session_id):
        plan_mode.enter(session_id)
        st = plan_mode.record_turn_plan(session_id, "# 计划\n1. 读代码\n2. 改配置")
        assert st["version"] == 1
        assert st["status"] == "pending_approval"
        assert "读代码" in st["plan"]
        # plan.md 与 v1.md 同内容
        root = os.path.join(plan_mode.get_root(), session_id)
        assert open(os.path.join(root, "v1.md"), encoding="utf-8").read() == st["plan"]
        assert open(os.path.join(root, "plan.md"), encoding="utf-8").read() == st["plan"]
        # state.json 是合法 JSON（原子写没有留下半截文件）
        doc = json.load(open(os.path.join(root, "state.json"), encoding="utf-8"))
        assert doc["version"] == 1

    def test_空回复不产生版本(self, session_id):
        plan_mode.enter(session_id)
        assert plan_mode.record_turn_plan(session_id, "   \n ") is None
        assert plan_mode.get_state(session_id)["version"] == 0

    def test_批准后退出计划模式并保留计划(self, session_id):
        plan_mode.enter(session_id)
        plan_mode.record_turn_plan(session_id, "v1 计划")
        st = plan_mode.decide(session_id, "approve")
        assert st["plan_mode"] is False
        assert st["status"] == "approved"
        assert st["plan"] == "v1 计划\n"
        assert st["revisions"][-1]["status"] == "approved"

    def test_没有待批计划时批准被拒(self, session_id):
        plan_mode.enter(session_id)
        with pytest.raises(plan_mode.PlanModeError) as ei:
            plan_mode.decide(session_id, "approve")
        assert ei.value.code == "no_pending_plan"

    def test_拒绝后仍在计划模式并可继续规划(self, session_id):
        plan_mode.enter(session_id)
        plan_mode.record_turn_plan(session_id, "v1")
        st = plan_mode.decide(session_id, "reject", feedback="范围太大")
        assert st["plan_mode"] is True
        assert st["status"] == "planning"
        assert st["feedback"][-1]["text"] == "范围太大"
        assert st["revisions"][-1]["status"] == "rejected"
        st2 = plan_mode.record_turn_plan(session_id, "v2 缩小范围")
        assert st2["version"] == 2 and st2["status"] == "pending_approval"

    def test_反馈空文本被拒(self, session_id):
        plan_mode.enter(session_id)
        with pytest.raises(plan_mode.PlanModeError) as ei:
            plan_mode.decide(session_id, "feedback", feedback="   ")
        assert ei.value.code == "empty_feedback"

    def test_反馈记录并回到规划态(self, session_id):
        plan_mode.enter(session_id)
        plan_mode.record_turn_plan(session_id, "v1")
        st = plan_mode.decide(session_id, "feedback", feedback="补上回滚步骤")
        assert st["status"] == "planning"
        assert st["plan_mode"] is True
        assert st["feedback"][-1]["kind"] == "feedback"

    def test_回滚生成新版本且内容来自旧版本(self, session_id):
        plan_mode.enter(session_id)
        plan_mode.record_turn_plan(session_id, "第一版内容")
        plan_mode.record_turn_plan(session_id, "第二版内容")
        st = plan_mode.decide(session_id, "rollback", version=1)
        assert st["version"] == 3
        assert "第一版内容" in st["plan"]
        assert st["status"] == "pending_approval"
        assert st["plan_mode"] is True
        assert st["revisions"][-1]["rollback_of"] == 1
        # 旧版本文件仍在（回滚不删历史）
        root = os.path.join(plan_mode.get_root(), session_id)
        assert os.path.isfile(os.path.join(root, "v1.md"))
        assert os.path.isfile(os.path.join(root, "v2.md"))

    def test_回滚到不存在的版本被拒(self, session_id):
        plan_mode.enter(session_id)
        with pytest.raises(plan_mode.PlanModeError) as ei:
            plan_mode.decide(session_id, "rollback", version=99)
        assert ei.value.code == "unknown_version"

    def test_未知动作被拒(self, session_id):
        plan_mode.enter(session_id)
        with pytest.raises(plan_mode.PlanModeError) as ei:
            plan_mode.decide(session_id, "destroy")
        assert ei.value.code == "unknown_action"

    def test_退出保留文件(self, session_id):
        plan_mode.enter(session_id)
        plan_mode.record_turn_plan(session_id, "保留内容")
        st = plan_mode.exit(session_id)
        assert st["plan_mode"] is False
        assert "保留内容" in st["plan"]

    def test_状态跨调用从磁盘读回_没有内存缓存(self, session_id):
        plan_mode.enter(session_id)
        plan_mode.record_turn_plan(session_id, "持久化内容")
        # 模拟"重启"：直接读公开 API（本模块没有内存缓存，每次读 state.json）
        st = plan_mode.get_state(session_id)
        assert st["version"] == 1 and "持久化内容" in st["plan"]


class TestSessionKey:
    def test_非法会话_id_哈希且不穿透目录(self):
        key = plan_mode.session_key("../../etc/passwd")
        assert "/" not in key and "\\" not in key and ".." not in key
        assert key.startswith("h-")

    def test_不同非法_id_不会撞同一目录(self):
        assert plan_mode.session_key("a/b") != plan_mode.session_key("a_b")

    def test_合法_id_原样使用(self):
        assert plan_mode.session_key("sess-abc_123") == "sess-abc_123"

    def test_空_id_报错(self):
        with pytest.raises(plan_mode.PlanModeError):
            plan_mode.session_key("  ")


class TestDirectiveAndDelete:
    def test_系统提示只在计划模式开启时出现(self, session_id):
        assert plan_mode.system_directive(session_id) == ""
        plan_mode.enter(session_id)
        assert plan_mode.system_directive(session_id) == plan_mode.PLAN_DIRECTIVE
        plan_mode.exit(session_id)
        assert plan_mode.system_directive(session_id) == ""

    def test_删除计划目录(self, session_id):
        plan_mode.enter(session_id)
        plan_mode.record_turn_plan(session_id, "x")
        assert plan_mode.delete_plan(session_id) is True
        assert plan_mode.get_state(session_id)["status"] == "idle"
        assert plan_mode.delete_plan(session_id) is False  # 幂等


class TestRoutes:
    @pytest.fixture()
    def client(self, monkeypatch):
        from flask import Flask
        import agent.server_auth as server_auth
        from agent.server_routes.routes_plan_mode import register_routes

        # 测试不依赖部署环境的令牌配置：鉴权本身不在本 Epic 的验收范围
        monkeypatch.setattr(server_auth, "authorize_request",
                            lambda: (True, "tester", "test"))
        app = Flask("plan_mode_routes")
        app.config.update(TESTING=True)
        register_routes(app)
        return app.test_client()

    def test_get_默认态(self, client, session_id):
        resp = client.get("/api/plan-mode/state?session_id=%s" % session_id)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True and data["plan_mode"] is False
        assert data["status"] == "idle"

    def test_get_缺_session_400(self, client):
        assert client.get("/api/plan-mode/state").status_code == 400

    def test_post_mode_进入与退出(self, client, session_id):
        r1 = client.post("/api/plan-mode/mode", json={"session_id": session_id, "enabled": True})
        assert r1.status_code == 200 and r1.get_json()["plan_mode"] is True
        r2 = client.post("/api/plan-mode/mode", json={"session_id": session_id, "enabled": False})
        assert r2.status_code == 200 and r2.get_json()["plan_mode"] is False

    def test_post_mode_非布尔_400(self, client, session_id):
        r = client.post("/api/plan-mode/mode", json={"session_id": session_id, "enabled": "yes"})
        assert r.status_code == 400

    def test_decision_无待批计划_409_带_code(self, client, session_id):
        plan_mode.enter(session_id)
        r = client.post("/api/plan-mode/decision",
                        json={"session_id": session_id, "action": "approve"})
        assert r.status_code == 409
        assert r.get_json()["code"] == "no_pending_plan"

    def test_decision_批准返回_next_message(self, client, session_id):
        plan_mode.enter(session_id)
        plan_mode.record_turn_plan(session_id, "计划正文")
        r = client.post("/api/plan-mode/decision",
                        json={"session_id": session_id, "action": "approve"})
        assert r.status_code == 200
        data = r.get_json()
        assert data["status"] == "approved" and data["plan_mode"] is False
        assert "开始按计划执行" in data["next_message"]

    def test_decision_拒绝把意见带进_next_message(self, client, session_id):
        plan_mode.enter(session_id)
        plan_mode.record_turn_plan(session_id, "计划正文")
        r = client.post("/api/plan-mode/decision",
                        json={"session_id": session_id, "action": "reject",
                              "feedback": "先补测试"})
        data = r.get_json()
        assert data["status"] == "planning" and data["plan_mode"] is True
        assert "先补测试" in data["next_message"]

    def test_decision_回滚(self, client, session_id):
        plan_mode.enter(session_id)
        plan_mode.record_turn_plan(session_id, "旧版本")
        plan_mode.record_turn_plan(session_id, "新版本")
        r = client.post("/api/plan-mode/decision",
                        json={"session_id": session_id, "action": "rollback", "version": 1})
        data = r.get_json()
        assert r.status_code == 200
        assert data["version"] == 3 and "旧版本" in data["plan"]
        assert data["next_message"] is None
