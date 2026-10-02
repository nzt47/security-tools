"""委派记录 HTTP 面（GET /api/subagent/history）测试

对应线上反馈：「子代理」下拉里**永远什么都没有**。根因不是没接线，而是它读的是
"当前存活的分身容器"，而委派跑完即回收 ⇒ 那个集合在正常业务里恒为空。本端点改为
读**委派记录**（每次委派落一条，见 agent/subagent/delegation_history.py）。

本文件锁死：
  1. 记录**最新在前**、字段形状（UI 直接渲染它）；
  2. limit 收敛（非法/越界不放大请求）；
  3. 记录读取失败 ⇒ 500 + ok=False（不静默返回空列表冒充"没有记录"）；
  4. **路由优先级**：``/api/subagent/history`` 是静态路径，必须命中本处理器，
     不能被 ``/api/subagent/<name>`` 抢走（否则表现是"分身不存在: history"的 404）；
  5. **真实入口** ``import app_server`` 后该路径确实在 url_map 里 ——
     本仓有过"手搓 Flask 全绿、线上 404"的教训（见 test_background_tasks_routes.py 文件头）。
"""
from __future__ import annotations

import pytest
from flask import Flask

from agent.server_routes import routes_subagent as routes_module
from agent.server_routes.routes_subagent import register_routes
from agent.subagent.delegation_history import DelegationHistory

pytestmark = pytest.mark.timeout(900)


class _FakeYunshu:
    """本文件只测历史端点；其余端点用不到，给最小实现即可"""

    _subagent_mgr = None
    _llm = None

    def list_subagents(self):
        return []


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    """构造 client；``delegation_history`` 换到 tmp（不碰仓库 data/）"""
    def _make(records: list[dict] | None = None):
        hist = DelegationHistory(path=str(tmp_path / "delegations.jsonl"))
        for rec in (records or []):
            hist.append(rec)
        monkeypatch.setattr(routes_module, "delegation_history", hist)
        state = type("S", (), {"Yunshu": _FakeYunshu()})()
        app = Flask(__name__)
        app.config.update(TESTING=True)
        register_routes(app, state)
        return app.test_client(), hist
    return _make


class TestHistoryEndpoint:
    def test_记录最新在前_且形状可被_UI_直接渲染(self, make_client):
        client, _ = make_client([
            {"delegation_id": "dlg-old", "subagent": "sa-1", "source": "tool",
             "ok": True, "goal": "旧任务"},
            {"delegation_id": "dlg-new", "subagent": "delegate-x", "source": "ui",
             "ok": False, "goal": "新任务", "error_code": "E_DELEGATION_NO_CHANNEL",
             "error": "未配置执行通道"},
        ])
        body = client.get("/api/subagent/history").get_json()
        assert body["ok"] is True
        assert body["count"] == 2
        assert body["total"] == 2
        assert [r["delegation_id"] for r in body["records"]] == ["dlg-new", "dlg-old"]
        assert body["records"][0]["source"] == "ui"
        assert body["records"][0]["error_code"] == "E_DELEGATION_NO_CHANNEL"

    def test_没有记录时返回空列表(self, make_client):
        client, _ = make_client([])
        body = client.get("/api/subagent/history").get_json()
        assert body["ok"] is True and body["records"] == [] and body["total"] == 0

    def test_limit_收敛(self, make_client, monkeypatch):
        client, hist = make_client([])
        seen: list[int] = []

        def _query(limit: int = 20):
            seen.append(limit)
            return []

        monkeypatch.setattr(hist, "query", _query)
        for q, expect in (("?limit=5", 5), ("?limit=abc", 20), ("?limit=0", 1),
                          ("?limit=999", 100), ("", 20)):
            client.get(f"/api/subagent/history{q}")
        assert seen == [5, 20, 1, 100, 20], f"limit 未被收敛: {seen}"

    def test_记录读取异常返回_500_而不是空列表(self, make_client, monkeypatch):
        """静默返回空列表会把"读失败"伪装成"没有记录"——正是本次要修的那类误导"""
        client, hist = make_client([])

        def _boom(limit: int = 20):
            raise RuntimeError("磁盘读取失败")

        monkeypatch.setattr(hist, "query", _boom)
        r = client.get("/api/subagent/history")
        assert r.status_code == 500
        assert r.get_json()["ok"] is False

    def test_静态路径不被_子代理名_动态路由抢走(self, make_client):
        """否则线上表现是 404「分身不存在: history」"""
        client, _ = make_client([])
        app = client.application
        rules = {str(r.rule) for r in app.url_map.iter_rules()}
        assert "/api/subagent/history" in rules
        assert "/api/subagent/<name>" in rules
        # 静态优先：请求必须落在历史端点上（返回 ok/records 而不是"分身不存在"）
        body = client.get("/api/subagent/history").get_json()
        assert body["ok"] is True and "records" in body
        assert "分身不存在" not in str(body)


@pytest.fixture(scope="module")
def real_url_paths() -> set:
    """真实路由表（``import app_server`` 后枚举；同进程内只付一次导入代价）

    【不易·为什么用完要整表还原】该导入会把整套内建工具登记进**进程级**
    ``agent/tools/_registry``（导入副作用，非本测试断言对象），留着会污染同进程
    后续测试文件（同 test_background_tasks_routes.py / test_server_routes_registration_inventory.py）。
    """
    from agent import tools as _tools

    saved = dict(_tools._registry)
    try:
        import app_server  # noqa: PLC0415 真实入口，与生产同一份注册代码
        yield {str(r.rule) for r in app_server.app.url_map.iter_rules()}
    finally:
        _tools._registry.clear()
        _tools._registry.update(saved)
        _tools._registry_version += 1


def test_委派端点已在真实入口注册(real_url_paths):
    """手搓 Flask 全绿而线上 404 的坑：两个委派入口都必须出现在**真实** url_map 里

    ``/api/subagent/delegate``（临时分身，界面在"一个分身都没有"时走的正是它）
    与 ``/api/subagent/<name>/delegate``（具名分身）是两条**不同**的规则，
    静态那条若被动态那条吞掉，线上表现就是"临时委派莫名 404 / 打成具名委派"。
    """
    assert "/api/subagent/history" in real_url_paths
    assert "/api/subagent/delegate" in real_url_paths
    assert "/api/subagent/<name>/delegate" in real_url_paths
