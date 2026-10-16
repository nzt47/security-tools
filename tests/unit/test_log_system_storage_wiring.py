# -*- coding: utf-8 -*-
"""日志系统存储接线（2026-10-17）：注册路径必须把全局 LogStorage 初始化。

【背景】实测 GET /logs/api/stats 返回 503「日志系统未初始化」，拓扑图 ops.logs
节点因此长期离线。根因：生产入口 dashboard.register_log_system() 只注册蓝图 + 起
内省循环，**从不** _set_storage ⇒ get_storage() 恒为 None。本文件钉住两件事：
  1. ensure_global_storage() 会初始化且幂等；
  2. 初始化后 /logs/api/stats 返回 200，未初始化时仍如实 503（不假装有数据）。
"""
from __future__ import annotations

import pytest
from flask import Flask

from agent.log_system import storage as st


@pytest.fixture(autouse=True)
def _reset_storage():
    st._set_storage(None)
    yield
    st._set_storage(None)


@pytest.fixture
def temp_factory(monkeypatch, tmp_path):
    """把 LogStorage 换成落 tmp_path 的实例工厂，避免碰真实 data/logs。"""
    original = st.LogStorage

    def _make(*args, **kwargs):
        return original(db_path=str(tmp_path / "logs.db"), raw_log_dir=str(tmp_path / "raw"))

    monkeypatch.setattr(st, "LogStorage", _make)
    return _make


def _stats_client():
    from agent.log_system.dashboard import log_system_bp
    app = Flask(__name__)
    app.register_blueprint(log_system_bp)
    return app.test_client()


class TestEnsureGlobalStorage:
    def test_初始化一次且幂等(self, temp_factory):
        assert st.get_storage() is None
        assert st.ensure_global_storage() is True
        first = st.get_storage()
        assert first is not None
        assert st.ensure_global_storage() is False
        assert st.get_storage() is first

    def test_初始化后端点返回200(self, temp_factory):
        st.ensure_global_storage()
        resp = _stats_client().get("/logs/api/stats?hours=1")
        assert resp.status_code == 200, resp.get_data(as_text=True)[:200]
        body = resp.get_json()
        assert body["total_count"] == 0

    def test_未初始化时如实503(self):
        assert st.get_storage() is None
        resp = _stats_client().get("/logs/api/stats?hours=1")
        assert resp.status_code == 503


class TestRegisterWiring:
    def test_register_log_system_会初始化存储(self, temp_factory, monkeypatch):
        """生产入口接线：register_log_system() 之后存储必须就绪。"""
        import agent.log_system.dashboard as dash
        # 不启动内省后台循环（本用例只验证存储接线）
        monkeypatch.setattr(dash, "get_introspection",
                            lambda: type("E", (), {"start_background_loop": lambda self, **k: None})())
        app = Flask(__name__)
        dash.register_log_system(app)
        assert st.get_storage() is not None
