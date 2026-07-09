"""集成测试收集配置

check_*.py 是运维检查脚本，在模块级直接发起 HTTP 请求连接 Prometheus。
当 Prometheus 未运行时会导致收集错误（ConnectionRefusedError）。

策略：默认跳过这些脚本的收集，通过环境变量 RUN_PROM_CHECKS=1 启用。
"""
import os

import pytest

if os.environ.get("RUN_PROM_CHECKS", "0") != "1":
    collect_ignore = [
        "check_5xx_source.py",
        "check_baseline.py",
        "check_targets.py",
    ]


@pytest.fixture
def ab_test_manager(tmp_path):
    """每个测试独立的 ABTestManager，使用临时 SQLite 隔离。"""
    from agent.ab_testing import ABTestManager
    mgr = ABTestManager(storage_path=str(tmp_path / "ab_testing"))
    mgr.initialize()
    yield mgr


@pytest.fixture
def feedback_manager(tmp_path):
    """每个测试独立的 FeedbackManager，使用临时 SQLite 隔离。"""
    from agent.feedback import FeedbackManager
    mgr = FeedbackManager(storage_path=str(tmp_path / "feedback"))
    mgr.initialize()
    yield mgr


@pytest.fixture
def skills_mgmt_client():
    """构造最小 Flask app + TestClient，mock 服务层。

    返回 (client, mock_svc) 元组，测试可配置 mock_svc 的返回值。
    """
    from flask import Flask
    from unittest.mock import MagicMock, patch
    from agent.server_routes.routes_skills_mgmt import register_routes

    mock_svc = MagicMock()
    patches = [
        patch(
            "agent.server_routes.routes_skills_mgmt.get_skills_mgmt_service",
            return_value=mock_svc,
        ),
        patch(
            "agent.server_routes.routes_skills_mgmt.require_token",
            lambda f: f,
        ),
    ]
    for p in patches:
        p.start()

    app = Flask(__name__)
    app.config.update(TESTING=True)
    state = type("_S", (), {})()
    register_routes(app, state)
    client = app.test_client()

    yield client, mock_svc

    for p in patches:
        p.stop()
