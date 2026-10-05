"""P1-front **第二批**：模块拓扑与工作流列表的显式信封契约（2026-10-05）。

【这两条为什么一起做】
- `GET /api/modules/topology` —— 消费方只有 `pages/hub/module-list.tsx`（用 `pickObj` 猜）；
- `GET /api/workflow-learning/workflows` —— 消费方只有 `pages/hub/memory/workflow.tsx`（用 `pickList` 猜）。
两条都是**单消费方、单实现、无 405 混入**，正是 §2.4 选择标准里的"消费面已明确"。

【为什么沿用第一批的"不导入 app_server"写法】
第一批的守卫初版用 `import app_server` 取真实 app，实测该导入本机 80–100s，
在 CI 覆盖率分片下**超过 300s 预算**，把 Shard 4/6 拖红。
故这里同样**只导入被测模块**（实测毫秒级），把"有没有接上"用静态断言补回来：

| 要证明的 | 手段 |
|---|---|
| 视图真的声明了信封、载荷形状没变 | 把被测蓝图/路由挂到**最小 Flask app** 上打真实请求 |
| 生产确实挂上了这些路由 | 静态：蓝图带 url_prefix 含该路径 / app_server 里有 register 接线 |
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(300)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def modules_app():
    """modules 蓝图：`url_prefix=/api/modules` + `@modules_bp.route("/topology")`。"""
    from flask import Flask
    from agent.api_envelope import install_error_handlers
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    m = importlib.import_module("agent.modules_api")
    app = Flask(__name__)
    app.register_blueprint(m.modules_bp)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(scope="module")
def workflow_app():
    """workflow-learning 模块：`register_routes(app, state)`（state 未被这些路由使用）。"""
    from flask import Flask
    from agent.api_envelope import install_error_handlers
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    m = importlib.import_module("agent.server_routes.routes_workflow_learning")
    app = Flask(__name__)
    m.register_routes(app, None)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client()


class Test模块拓扑:
    def test_成功响应带信封(self, modules_app):
        resp = modules_app.get("/api/modules/topology")
        assert resp.headers.get("X-Envelope") == "v2", (
            "该端点没有声明信封（实测头=" + repr(resp.headers.get("X-Envelope"))
            + "，HTTP " + str(resp.status_code) + "）⇒ 前端 getEnvelope 会抛错。\n"
            "两种可能：① 迁移被回退（视图改回了 jsonify）；② 信封版本变了。"
        )
        body = resp.get_json()
        assert body.get("code") == 200, repr(body)[:200]

    def test_载荷形状未被信封改坏(self, modules_app):
        """业务键必须仍在 data 下，且 domains 仍是数组（页面靠它展平模块树）。"""
        data = modules_app.get("/api/modules/topology").get_json().get("data")
        assert isinstance(data, dict), "data 应为对象，实测 " + repr(type(data).__name__)
        for key in ("generated_at", "overall_health", "domains"):
            assert key in data, (
                "拓扑载荷缺少既有业务键「" + key + "」⇒ 迁移信封时改变了契约。"
                "实测键：" + repr(sorted(data.keys()))
            )
        assert isinstance(data["domains"], list), "domains 应为数组"
        if data["domains"]:
            d0 = data["domains"][0]
            for key in ("domain_id", "domain_name", "nodes"):
                assert key in d0, "域对象缺少「" + key + "」，实测键：" + repr(sorted(d0.keys()))
            assert isinstance(d0["nodes"], list)


class Test工作流列表:
    def test_成功响应带信封(self, workflow_app):
        resp = workflow_app.get("/api/workflow-learning/workflows")
        assert resp.headers.get("X-Envelope") == "v2", (
            "该端点没有声明信封（实测头=" + repr(resp.headers.get("X-Envelope"))
            + "，HTTP " + str(resp.status_code) + "）⇒ 前端 getEnvelope 会抛错。"
        )
        assert resp.get_json().get("code") == 200

    def test_载荷形状与分页字段(self, workflow_app):
        """`items` 必须仍是数组、`total` 必须仍可取到 —— 页面靠这两个键渲染与计数。"""
        body = workflow_app.get("/api/workflow-learning/workflows").get_json()
        data = body.get("data")
        assert isinstance(data, dict), "data 应为对象，实测 " + repr(type(data).__name__)
        for key in ("ok", "items", "total"):
            assert key in data, (
                "工作流载荷缺少既有业务键「" + key + "」⇒ 迁移信封时改变了契约。"
                "实测键：" + repr(sorted(data.keys()))
            )
        assert isinstance(data["items"], list), "items 应为数组"
        assert isinstance(data["total"], int), "total 应为整数"
        assert data["total"] == len(data["items"]), (
            "total(" + str(data["total"]) + ") 与 items 长度(" + str(len(data["items"]))
            + ") 不一致 —— 迁移时把某个键放错了层。"
        )
        # meta 是"信封层的通用信息"，与 data 里的业务 total 并存（不删业务键）
        assert body.get("meta", {}).get("total") == data["total"], (
            "信封 meta.total 与业务载荷 total 不一致："
            + repr(body.get("meta")) + " vs " + repr(data["total"])
        )


class Test生产确实挂上了这些路由:
    """上面打的是最小 app —— 这里静态证明生产也挂了它们（防"写了没接线"）。

    本仓有先例：`routes_monitoring` 等模块写了 register_routes 但从未接线，
    端点由别处提供（见 test_server_routes_registration_inventory.py::KNOWN_UNREGISTERED）。
    """

    def test_modules_蓝图带正确前缀(self):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        m = importlib.import_module("agent.modules_api")
        assert m.modules_bp.url_prefix == "/api/modules", (
            "modules 蓝图的 url_prefix 变了（实测 " + repr(m.modules_bp.url_prefix)
            + "）—— 本文件断言的路径随之失效，需同步更新。"
        )
        app_src = (ROOT / "app_server.py").read_text(encoding="utf-8")
        assert "modules_api" in app_src, "app_server.py 里看不到 modules_api 接线"

    def test_workflow_learning_被_app_server_接线(self):
        app_src = (ROOT / "app_server.py").read_text(encoding="utf-8")
        assert "routes_workflow_learning" in app_src, (
            "app_server.py 里看不到 routes_workflow_learning —— 该模块可能已被改成未接线，"
            "那它写的路由就不会生效（本仓这类\"写了没接线\"有先例）。"
        )
        assert "register_routes" in app_src, "app_server.py 里看不到 register_routes 调用"
