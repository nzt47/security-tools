"""POST /api/memory/review 路由集成测试

验证 TLM Step 2 新增的记忆审查路由：
- GET /api/memory/review: 返回上次审查结果 + LTM 统计
- POST /api/memory/review: 触发 review_quick()
- 503: 记忆审查器未启用
- 500: 内部异常

设计原则：
- mock require_token/log_request 为 passthrough，专注业务逻辑
- mock Yunshu._memory_reviewer 和 _long_term_memory
- 使用 Flask test client，不启动真实服务器

【2026-09 迁移】`/api/memory/review` 的活体已从 `agent/server_routes/routes_memory.py`
（整模块从未被 app_server 注册 ⇒ 该路由在生产 404）迁入 `plugins/memory.py`。
故本测试改为**挂载插件 blueprint**（真实路由层不变），并把插件视图函数内部
`from app_server import ...` 的解析目标换成本测试的替身模块：真实 app_server
一旦导入会加载向量模型并起调度线程，而这里只需要 `require_token` / `log_request`
两个装饰器与 `_Yunshu`。断言与用例名与原版逐条一致。
"""
import asyncio
import logging
import sys
import types
from unittest.mock import MagicMock, patch
from flask import Flask

import pytest

pytestmark = pytest.mark.integration


def _fake_app_server():
    """构造 app_server 替身（插件视图内 `from app_server import ...` 的解析目标）"""
    fake = types.ModuleType("app_server")
    fake.require_token = lambda f: f
    fake.log_request = lambda f=None, **kw: (f if f is not None else (lambda x: x))
    fake.tracing_disabled = True
    fake.logger = logging.getLogger("test.routes_memory_review")
    fake._Yunshu = None  # 由 _make_app_with_reviewer 按用例覆盖
    return fake


@pytest.fixture(scope="module")
def plugin_harness():
    """sys.modules 里的 app_server 替身 + 全新 reload 的插件蓝图。

    Why reload：`plugins/memory.py::_view` 在**首次请求**时惰性 `from app_server import
    require_token, log_request` 并缓存包装结果。若同进程内已有用例走过真实 app_server
    （如 tests/unit/test_legacy_memory_routes.py 导入真实入口），缓存里就是真实装饰器
    （会 401）。reload 生成全新 blueprint 与全新闭包，保证本模块拿到 passthrough 版。
    （plugin_api.register_plugin 按 name 幂等，重复 reload 不会污染插件注册表。）
    """
    import importlib

    import plugins.memory as plugin_memory

    fake = _fake_app_server()
    with patch.dict(sys.modules, {"app_server": fake}):
        importlib.reload(plugin_memory)
        yield fake, plugin_memory.bp


_HARNESS = None


@pytest.fixture(autouse=True)
def _bind_harness(plugin_harness):
    """把当前模块的 harness 暴露给下方工厂函数（保持原版调用签名不变）"""
    global _HARNESS
    _HARNESS = plugin_harness
    yield


def _make_app_with_reviewer(reviewer=None, ltm_stats=None, ltm=None,
                             review_quick_result=None,
                             raise_on_review=False):
    """构造带 mock reviewer 的 Flask test app（挂载 plugins/memory.py 蓝图）

    Args:
        reviewer: mock reviewer 实例（None 表示未启用）
        ltm_stats: mock LTM get_stats 返回值
        ltm: mock LTM 实例（None 时自动构造）
        review_quick_result: review_quick() 的返回值
        raise_on_review: True 时 review_quick 抛异常
    """
    fake, bp = _HARNESS

    app = Flask(__name__)
    app.config.update(TESTING=True)

    # 构造 mock Yunshu
    yunshu = MagicMock()
    yunshu._memory_reviewer = reviewer
    if ltm is not None:
        yunshu._long_term_memory = ltm
    else:
        mock_ltm = MagicMock()
        mock_ltm.get_stats.return_value = ltm_stats or {"total_entries": 0}
        yunshu._long_term_memory = mock_ltm

    fake._Yunshu = yunshu

    app.register_blueprint(bp)
    client = app.test_client()

    return client, fake


def _cleanup_patches(fake):
    """兼容原版签名与调用点：现在不再逐个 patch 模块属性（由 harness 统一处理），
    这里只把替身里的 Yunshu 归还，避免用例间串味。"""
    if isinstance(fake, types.ModuleType):
        fake._Yunshu = None


class TestGetMemoryReview:
    """GET /api/memory/review 测试"""

    def test_get_review_no_history(self):
        """无历史审查时返回 last_review=null"""
        reviewer = MagicMock()
        reviewer.get_last_review.return_value = None
        client, patches = _make_app_with_reviewer(
            reviewer=reviewer,
            ltm_stats={"total_entries": 5},
        )
        try:
            resp = client.get("/api/memory/review")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["last_review"] is None
            assert data["stats"]["total_entries"] == 5
        finally:
            _cleanup_patches(patches)

    def test_get_review_with_history(self):
        """有历史审查时返回 last_review"""
        reviewer = MagicMock()
        # 用 SimpleNamespace 模拟 ReviewResult，vars() 可正常返回 __dict__
        fake_result = types.SimpleNamespace(
            reviewed_at=1700000000.0,
            total_entries=10,
            healthy_entries=8,
            stale_entries=1,
            duplicate_entries=1,
            sensitive_unverified=0,
            suggestions=["建议1"],
            report={},
        )
        reviewer.get_last_review.return_value = fake_result
        client, patches = _make_app_with_reviewer(reviewer=reviewer)
        try:
            resp = client.get("/api/memory/review")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["last_review"] is not None
            assert data["last_review"]["total_entries"] == 10
        finally:
            _cleanup_patches(patches)


class TestPostMemoryReview:
    """POST /api/memory/review 测试"""

    def test_post_review_trigger(self):
        """POST 触发 review_quick，返回 ok=True"""
        reviewer = MagicMock()
        quick_result = {
            "reviewed_at": 1700000000.0,
            "quick": True,
            "total_entries": 3,
            "sensitive_entries": 0,
            "high_importance_entries": 1,
            "verified_entries": 0,
            "unverified_entries": 1,
            "suggestions": ["存在 1 条未审查的重要记忆"],
        }

        async def fake_review_quick():
            return quick_result
        reviewer.review_quick = fake_review_quick

        client, patches = _make_app_with_reviewer(reviewer=reviewer)
        try:
            resp = client.post("/api/memory/review")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["result"]["quick"] is True
            assert data["result"]["total_entries"] == 3
        finally:
            _cleanup_patches(patches)


class TestMemoryReviewEdgeCases:
    """边界与异常测试"""

    def test_reviewer_not_initialized(self):
        """reviewer=None 时返回 503"""
        client, patches = _make_app_with_reviewer(reviewer=None)
        try:
            resp = client.get("/api/memory/review")
            assert resp.status_code == 503
            data = resp.get_json()
            assert "未启用" in data["error"]
        finally:
            _cleanup_patches(patches)

    def test_reviewer_none_on_post(self):
        """POST 时 reviewer=None 也返回 503"""
        client, patches = _make_app_with_reviewer(reviewer=None)
        try:
            resp = client.post("/api/memory/review")
            assert resp.status_code == 503
        finally:
            _cleanup_patches(patches)

    def test_internal_error_on_get(self):
        """GET 时 reviewer.get_last_review 抛异常，返回 500"""
        reviewer = MagicMock()
        reviewer.get_last_review.side_effect = RuntimeError("db locked")
        client, patches = _make_app_with_reviewer(reviewer=reviewer)
        try:
            resp = client.get("/api/memory/review")
            assert resp.status_code == 500
            data = resp.get_json()
            assert "db locked" in data["error"]
        finally:
            _cleanup_patches(patches)

    def test_internal_error_on_post(self):
        """POST 时 review_quick 抛异常，返回 500"""
        reviewer = MagicMock()

        async def fake_review_quick():
            raise RuntimeError("async error")
        reviewer.review_quick = fake_review_quick

        client, patches = _make_app_with_reviewer(reviewer=reviewer)
        try:
            resp = client.post("/api/memory/review")
            assert resp.status_code == 500
            data = resp.get_json()
            assert "async error" in data["error"]
        finally:
            _cleanup_patches(patches)

    def test_get_with_no_ltm(self):
        """LTM 为 None 时 GET 返回空 stats（不影响 reviewer 已初始化的场景）"""
        fake, bp = _HARNESS

        app = Flask(__name__)
        app.config.update(TESTING=True)
        reviewer = MagicMock()
        reviewer.get_last_review.return_value = None
        yunshu = MagicMock()
        yunshu._memory_reviewer = reviewer
        yunshu._long_term_memory = None  # 显式设为 None
        fake._Yunshu = yunshu
        app.register_blueprint(bp)
        client = app.test_client()

        resp = client.get("/api/memory/review")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["stats"] == {}
