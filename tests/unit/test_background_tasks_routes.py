"""后台任务 HTTP 面（/api/background/tasks*）单元测试

对应需求：「添加显示后台任务的下拉菜单按钮，用于查看和管理系统后台运行的任务」。

为什么还要一条「真实路由表」用例：
本仓有过教训 —— 路由模块只写进死代码 `register_all_routes`、测试用手搓
`Flask(__name__)` 注册 ⇒ 测试全绿而线上 404。故这里显式用**真实入口**
`import app_server` 校验端点确实挂在 url_map 上（与生产同一份注册代码）。
"""
from __future__ import annotations

import time

import pytest
from flask import Flask

from agent.async_executor import AsyncExecutor
from agent.server_routes.routes_background import register_routes


@pytest.fixture
def executor(tmp_path, monkeypatch):
    """隔离的执行器：任务文件写进 tmp（不碰仓库 data/async_tasks.jsonl）"""
    ex = AsyncExecutor(max_workers=1, result_ttl=3600)
    ex._tasks_file = str(tmp_path / "async_tasks.jsonl")
    monkeypatch.setattr("agent.async_executor.get_async_executor", lambda *a, **k: ex)
    yield ex
    ex.shutdown(wait=False)


@pytest.fixture
def client(executor):
    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_routes(app, lambda: None)
    return app.test_client()


def _seed(executor, **kw):
    task = {
        "id": kw.get("id", "task_abc"),
        "name": kw.get("name", "蒸馏任务"),
        "tool_name": kw.get("tool_name", "process_distill_run"),
        "params": {},
        "status": kw.get("status", "running"),
        "progress": kw.get("progress", "40%"),
        "result": kw.get("result"),
        "error": kw.get("error"),
        "created_at": kw.get("created_at", time.strftime("%Y-%m-%dT%H:%M:%S")),
        "started_at": kw.get("started_at", time.strftime("%Y-%m-%dT%H:%M:%S")),
        "completed_at": kw.get("completed_at"),
        "timeout": kw.get("timeout"),
    }
    executor._tasks[task["id"]] = task
    return task


class TestList:
    def test_空任务列表(self, client):
        r = client.get("/api/background/tasks")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["tasks"] == []
        assert body["running"] == 0

    def test_列表返回摘要字段(self, client, executor):
        _seed(executor, id="task_1", status="running", result={"big": "payload"})
        body = client.get("/api/background/tasks").get_json()
        assert body["total"] == 1
        assert body["running"] == 1
        task = body["tasks"][0]
        assert task["id"] == "task_1"
        assert task["tool_name"] == "process_distill_run"
        assert task["status"] == "running"
        assert task["has_result"] is True
        # 摘要不搬运结果本体（下拉菜单不需要整包结果）
        assert "result" not in task
        assert "params" not in task

    def test_按状态过滤(self, client, executor):
        _seed(executor, id="task_run", status="running")
        _seed(executor, id="task_done", status="completed")
        body = client.get("/api/background/tasks?status=completed").get_json()
        assert [t["id"] for t in body["tasks"]] == ["task_done"]
        assert body["running"] == 0
        assert body["status_filter"] == "completed"

    def test_limit_越界收敛(self, client, executor):
        for i in range(3):
            _seed(executor, id=f"task_{i}")
        assert len(client.get("/api/background/tasks?limit=0").get_json()["tasks"]) >= 1
        assert len(client.get("/api/background/tasks?limit=abc").get_json()["tasks"]) == 3


class TestStatusAndResult:
    def test_单任务状态(self, client, executor):
        _seed(executor, id="task_s1", status="running")
        body = client.get("/api/background/tasks/task_s1").get_json()
        assert body["ok"] is True and body["status"] == "running"

    def test_任务不存在返回404(self, client):
        r = client.get("/api/background/tasks/task_missing")
        assert r.status_code == 404
        assert r.get_json()["ok"] is False

    def test_结果_未完成给出提示(self, client, executor):
        _seed(executor, id="task_r1", status="running")
        body = client.get("/api/background/tasks/task_r1/result").get_json()
        assert body["ok"] is True
        assert body["status"] == "running"
        assert "尚未完成" in body["message"]

    def test_结果_已完成返回结果体(self, client, executor):
        _seed(executor, id="task_r2", status="completed", result={"output": "ok"},
              completed_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        body = client.get("/api/background/tasks/task_r2/result").get_json()
        assert body["result"] == {"output": "ok"}


class TestCancel:
    def test_取消运行中任务(self, client, executor):
        _seed(executor, id="task_c1", status="running")
        body = client.post("/api/background/tasks/task_c1/cancel").get_json()
        assert body["ok"] is True and body["status"] == "cancelled"
        assert executor._tasks["task_c1"]["status"] == "cancelled"

    def test_已完成任务不可取消(self, client, executor):
        _seed(executor, id="task_c2", status="completed")
        r = client.post("/api/background/tasks/task_c2/cancel")
        assert r.status_code == 400
        assert r.get_json()["ok"] is False

    def test_取消不存在任务(self, client):
        assert client.post("/api/background/tasks/nope/cancel").get_json()["ok"] is False


def test_端点已在真实入口注册():
    """真实 url_map 守门：手搓 Flask 全绿而线上 404 的坑（见文件头说明）"""
    import app_server  # noqa: PLC0415 真实入口

    paths = {str(r.rule) for r in app_server.app.url_map.iter_rules()}
    assert "/api/background/tasks" in paths
    assert "/api/background/tasks/<task_id>" in paths
    assert "/api/background/tasks/<task_id>/result" in paths
    assert "/api/background/tasks/<task_id>/cancel" in paths
