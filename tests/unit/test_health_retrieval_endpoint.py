"""检索降级可见化：`/api/health/retrieval`（W5/TASK-08 ⑤）

要求（06-基线台账 §3.5 D1「ChromaDB 静默降级可见化」同族）：**降级必须可见，不得静默**。

契约（已冻结，勿改签名）：`get_hybrid_retriever().embedding_health()`
  —— agent/tool_router_hybrid.py:1387 / 1407，字段
  mode / init_failed / worker_alive / available / failure_total / restart_attempts /
  max_restart_attempts / restarting / retry_exhausted / next_restart_in_sec / last_failure
  （+ retriever_degraded）。

【为什么单开端点而不塞进 `/api/health` 的响应体】实测 `GET /api/health` 的响应体是
**数组**（plugins/status.py:298-303 的传感器读数列表），前端
`static/js/sidebar/status-panel.js:30` 直接 `data.forEach(...)`，且
tests/unit/test_auth_migration_step1.py:114-123 与
tests/contract/contract_definitions.py:200-221 把它钉死为数组契约
⇒ 按"新增只读端点"落地（同 /api/health/auth 的先例），**降级可见且零破坏**。

⚠ 本模块 `import app_server`（80–100s），故给足超时预算（同 test_graceful_shutdown_persist.py）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.timeout(900)

PATH = "/api/health/retrieval"


@pytest.fixture(scope="module")
def flask_app():
    import app_server  # noqa: PLC0415 真实入口

    app_server.app.config.update(TESTING=True)
    return app_server.app


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()


class _FakeRetriever:
    def __init__(self, health):
        self._health = health

    def embedding_health(self):
        return dict(self._health)


def _stub(monkeypatch, retriever):
    import agent.tool_router_hybrid as trh
    monkeypatch.setattr(trh, "get_hybrid_retriever", lambda: retriever)


def test_端点已在真实入口注册(flask_app):
    """手搓 Flask 全绿而线上 404 是本仓踩过的坑（见 test_background_tasks_routes.py:5-8）"""
    paths = {str(r.rule) for r in flask_app.url_map.iter_rules()}
    assert PATH in paths


class TestDegradedVisible:
    def test_worker起不来时明确报告已降级为仅BM25(self, client, monkeypatch):
        _stub(monkeypatch, _FakeRetriever({
            "mode": "bm25_only", "init_failed": True, "worker_alive": False,
            "available": False, "failure_total": 5, "restart_attempts": 3,
            "max_restart_attempts": 3, "restarting": False, "retry_exhausted": True,
            "next_restart_in_sec": None,
            "last_failure": {"code": "worker_exit", "detail": "exit code 3221225477"},
            "retriever_degraded": True,
        }))
        r = client.get(PATH)
        assert r.status_code == 200
        j = r.get_json()
        assert j["mode"] == "bm25_only"
        assert j["degraded"] is True
        assert j["status"] == "degraded"
        assert j["degrade_to"] == "bm25_only"
        # 冻结契约字段逐字透传（一个都不能丢）
        for k in ("mode", "failure_total", "retry_exhausted", "worker_alive", "last_failure",
                  "init_failed", "available", "restart_attempts", "max_restart_attempts",
                  "restarting", "next_restart_in_sec", "retriever_degraded"):
            assert k in j, f"冻结契约字段 {k} 丢了"
        assert j["failure_total"] == 5
        assert j["retry_exhausted"] is True
        assert j["last_failure"]["code"] == "worker_exit"

    def test_健康时不得误报降级(self, client, monkeypatch):
        _stub(monkeypatch, _FakeRetriever({
            "mode": "hybrid", "init_failed": False, "worker_alive": True,
            "available": True, "failure_total": 0, "retry_exhausted": False,
            "retriever_degraded": False, "last_failure": {},
        }))
        j = client.get(PATH).get_json()
        assert j["mode"] == "hybrid"
        assert j["degraded"] is False
        assert j["status"] == "ok"
        assert j["degrade_to"] is None

    def test_融合层单独判降级也计入(self, client, monkeypatch):
        """mode 仍是 hybrid，但融合层已判降级 ⇒ 同样必须可见（取口径并集）"""
        _stub(monkeypatch, _FakeRetriever({
            "mode": "hybrid", "retriever_degraded": True, "retry_exhausted": False,
        }))
        j = client.get(PATH).get_json()
        assert j["degraded"] is True
        assert j["status"] == "degraded"

    def test_重试已耗尽也计入(self, client, monkeypatch):
        _stub(monkeypatch, _FakeRetriever({"mode": "hybrid", "retry_exhausted": True}))
        assert client.get(PATH).get_json()["degraded"] is True


class TestFailSafe:
    def test_检索器不可得时明确unknown_而不是静默当成健康(self, client, monkeypatch):
        _stub(monkeypatch, None)
        r = client.get(PATH)
        assert r.status_code == 200
        j = r.get_json()
        assert j["status"] == "unknown"
        assert j["degraded"] is None
        assert j["mode"] == "unknown"

    def test_探针自身异常不返回500(self, client, monkeypatch):
        class _Boom:
            def embedding_health(self):
                raise RuntimeError("worker 句柄已失效")

        _stub(monkeypatch, _Boom())
        r = client.get(PATH)
        assert r.status_code == 200
        assert r.get_json()["status"] == "error"

    def test_导入失败不返回500(self, client, monkeypatch):
        import agent.tool_router_hybrid as trh

        def _raise():
            raise ImportError("模拟依赖缺失")

        monkeypatch.setattr(trh, "get_hybrid_retriever", _raise)
        r = client.get(PATH)
        assert r.status_code == 200
        assert r.get_json()["status"] == "error"
