"""POST /api/memory/review 路由集成测试

验证 TLM Step 2 新增的记忆审查路由：
- GET /api/memory/review: 返回上次审查结果 + LTM 统计
- POST /api/memory/review: 触发 review_quick()
- 503: 记忆审查器未启用
- 500: 内部异常

设计原则：
- mock require_token/log_request/trace_route 为 passthrough，专注业务逻辑
- mock Yunshu._memory_reviewer 和 _long_term_memory
- 使用 Flask test client，不启动真实服务器
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch
from flask import Flask

pytestmark = pytest.mark.integration


def _make_app_with_reviewer(reviewer=None, ltm_stats=None, ltm=None,
                             review_quick_result=None,
                             raise_on_review=False):
    """构造带 mock reviewer 的 Flask test app

    Args:
        reviewer: mock reviewer 实例（None 表示未启用）
        ltm_stats: mock LTM get_stats 返回值
        ltm: mock LTM 实例（None 时自动构造）
        review_quick_result: review_quick() 的返回值
        raise_on_review: True 时 review_quick 抛异常
    """
    from agent.server_routes.routes_memory import register_routes

    patches = [
        patch("agent.server_routes.routes_memory.require_token", lambda f: f),
        patch("agent.server_routes.routes_memory.log_request", lambda f=None, **kw: (f if f else lambda x: x)),
        patch("agent.server_routes.routes_memory.trace_route", lambda name=None: lambda f: f),
    ]
    for p in patches:
        p.start()

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

    # 构造 mock state
    state = MagicMock()
    state.Yunshu = yunshu
    state.window_sensor = MagicMock()

    register_routes(app, state)
    client = app.test_client()

    return client, patches


def _cleanup_patches(patches):
    for p in patches:
        p.stop()


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
        import types
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
        from agent.server_routes.routes_memory import register_routes
        patches = [
            patch("agent.server_routes.routes_memory.require_token", lambda f: f),
            patch("agent.server_routes.routes_memory.log_request", lambda f=None, **kw: (f if f else lambda x: x)),
            patch("agent.server_routes.routes_memory.trace_route", lambda name=None: lambda f: f),
        ]
        for p in patches:
            p.start()
        try:
            app = Flask(__name__)
            app.config.update(TESTING=True)
            reviewer = MagicMock()
            reviewer.get_last_review.return_value = None
            yunshu = MagicMock()
            yunshu._memory_reviewer = reviewer
            yunshu._long_term_memory = None  # 显式设为 None
            state = MagicMock()
            state.Yunshu = yunshu
            state.window_sensor = MagicMock()
            register_routes(app, state)
            client = app.test_client()

            resp = client.get("/api/memory/review")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["stats"] == {}
        finally:
            _cleanup_patches(patches)
