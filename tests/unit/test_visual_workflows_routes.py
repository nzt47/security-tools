"""可视化编辑器工作流草稿 API 单元测试

覆盖 /api/visual-workflows/* 端点：
- 保存（新建/更新/自动生成 id/中文名 slug）
- 列表与详情（字段规范化：丢弃 xyflow 运行时附加字段、NaN 位置过滤）
- 删除与 404
- 参数校验（nodes/edges 缺失 → 400）
"""

from __future__ import annotations

import pytest
from flask import Flask

from agent.server_routes.routes_visual_workflows import register_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    """独立临时存储 + Flask 测试客户端（写操作不需要 token，默认关闭）"""
    monkeypatch.delenv("FLASK_API_TOKEN", raising=False)
    store = tmp_path / "visual_workflows.json"
    monkeypatch.setenv("VISUAL_WORKFLOWS_STORE", str(store))
    app = Flask(__name__)
    app.config["TESTING"] = True
    register_routes(app, None)
    return app.test_client()


def _payload(**overrides):
    body = {
        "id": "",
        "name": "日报生成流程",
        "description": "可视化编排测试",
        "nodes": [
            {
                "id": "skill-1",
                "type": "skill",
                "position": {"x": 10, "y": 20},
                # 模拟 xyflow 运行时附加字段（应被规范化丢弃）
                "measured": {"width": 160, "height": 60},
                "selected": True,
                "data": {
                    "label": "收集数据",
                    "nodeType": "skill",
                    "skillId": "collector",
                    "skillName": "数据收集",
                    "timeout": 30,
                    "retryCount": 0,
                    "params": {"source": "api"},
                },
            },
            {
                "id": "conditional-2",
                "type": "conditional",
                "position": {"x": 300, "y": 20},
                "data": {
                    "label": "是否继续",
                    "nodeType": "conditional",
                    "condition": "count > 0",
                    "trueBranch": "",
                    "falseBranch": "",
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "skill-1", "target": "conditional-2",
             "sourceHandle": None, "animated": True},
            {"id": "e2", "source": "conditional-2", "target": "skill-1",
             "sourceHandle": "true", "animated": True},
        ],
        "yaml": "name: 日报生成流程\nversion: '1.0'\nsteps: []",
    }
    body.update(overrides)
    return body


class TestVisualWorkflowsApi:
    """可视化工作流草稿 CRUD 测试"""

    def test_save_creates_and_normalizes(self, client):
        resp = client.post("/api/visual-workflows", json=_payload(id="skill-1"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["action"] == "created"
        wf = body["workflow"]
        assert wf["id"] == "skill-1" and wf["name"] == "日报生成流程"

        # 详情验证规范化
        detail = client.get("/api/visual-workflows/skill-1").get_json()
        assert detail["ok"] is True
        rec = detail["workflow"]
        assert rec["node_count"] == 2 and rec["edge_count"] == 2
        node0 = rec["nodes"][0]
        assert node0["id"] == "skill-1"
        assert "measured" not in node0 and "selected" not in node0
        assert node0["data"]["skillId"] == "collector"
        # 无条件 sourceHandle=None 的连线不应保留该键
        assert "sourceHandle" not in rec["edges"][0]
        assert rec["edges"][1]["sourceHandle"] == "true"
        assert rec["yaml"].startswith("name: 日报生成流程")
        assert rec["created_at"] and rec["updated_at"]

    def test_save_with_explicit_id_updates(self, client):
        client.post("/api/visual-workflows", json=_payload(id="my-wf"))
        resp = client.post("/api/visual-workflows",
                           json=_payload(id="my-wf", name="改名流程"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["action"] == "updated"
        assert body["workflow"]["id"] == "my-wf"
        detail = client.get("/api/visual-workflows/my-wf").get_json()
        assert detail["workflow"]["name"] == "改名流程"
        # 更新保留 created_at
        created = client.get("/api/visual-workflows/my-wf").get_json()
        assert created["workflow"]["created_at"] == body["workflow"]["created_at"]

    def test_save_auto_generates_id_from_chinese_name(self, client):
        resp = client.post("/api/visual-workflows", json=_payload(id=""))
        wf = resp.get_json()["workflow"]
        assert wf["id"]  # 中文名 slug 为空 → 时间戳兜底，非空即可
        # 英文名 → slug 化 id
        resp2 = client.post(
            "/api/visual-workflows",
            json=_payload(id="", name="My Daily Report! 生成"),
        )
        wf2 = resp2.get_json()["workflow"]
        # 中文与符号在 slug 化时被剔除 → 回退为纯 ASCII 段
        assert wf2["id"] == "my-daily-report"

    def test_invalid_payload_rejected(self, client):
        # nodes 缺失
        resp = client.post("/api/visual-workflows", json={"name": "x"})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False
        # nodes 非数组
        resp = client.post("/api/visual-workflows",
                           json={"name": "x", "nodes": {}, "edges": []})
        assert resp.status_code == 400

    def test_save_filters_bad_nodes_and_positions(self, client):
        body = _payload()
        body["nodes"].append({"id": "", "type": "skill"})          # 无 id → 丢弃
        body["nodes"].append("not-a-dict")                          # 非对象 → 丢弃
        body["nodes"][0]["position"] = {"x": float("nan"), "y": 9}  # NaN → 0
        resp = client.post("/api/visual-workflows", json=body)
        assert resp.get_json()["ok"] is True
        saved_id = resp.get_json()["workflow"]["id"]
        detail = client.get(f"/api/visual-workflows/{saved_id}").get_json()
        wf = detail["workflow"]
        assert wf["node_count"] == 2
        assert wf["nodes"][0]["position"] == {"x": 0.0, "y": 9.0}

    def test_list_and_delete(self, client):
        assert client.get("/api/visual-workflows").get_json()["total"] == 0
        client.post("/api/visual-workflows", json=_payload(id="a-wf"))
        client.post("/api/visual-workflows", json=_payload(id="b-wf"))
        lst = client.get("/api/visual-workflows").get_json()
        assert lst["total"] == 2
        assert {i["id"] for i in lst["items"]} == {"a-wf", "b-wf"}
        assert all("nodes" not in i for i in lst["items"])  # 列表只含摘要

        # 删除
        resp = client.delete("/api/visual-workflows/a-wf")
        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        assert client.get("/api/visual-workflows").get_json()["total"] == 1
        # 重复删除 → 404
        resp = client.delete("/api/visual-workflows/a-wf")
        assert resp.status_code == 404
        # 不存在详情 → 404
        assert client.get("/api/visual-workflows/nope").status_code == 404
