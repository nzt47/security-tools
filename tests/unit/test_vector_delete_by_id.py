# -*- coding: utf-8 -*-
"""向量记忆「按 id 删除」（交接单 §9 后续计划第 1 步）。

背景：外部记忆导入要改走向量记忆，而向量侧原先只有 `clear()`（全清）。
没有按 id 删除，任何"导入/写入后又想撤"的链路都只能假装成功——本仓 2026-10-25
刚修过同类缺陷（`DELETE /api/memory/<index>` 恒回 ok:true 却不删数据）。

这组用例钉三件事：
1. 后端 delete 必须**同事务**删 vec0 与 meta 两张表（否则出现"向量在、正文没了"的残影）；
2. "删掉了"与"本来就没有"必须可区分（True / False），上层才能如实报 404 而不是 200；
3. HTTP 层：找不到 404、id 非法 400、向量系统未初始化 503、成功才写审计。
"""
from __future__ import annotations

import importlib
import logging
import sys
import types
from pathlib import Path

import pytest

from memory.vector_store.sqlite_vec_backend import SqliteVecBackend

ROOT = Path(__file__).resolve().parents[2]
DIM = 8


def _backend(tmp_path) -> SqliteVecBackend:
    """构造真后端（dim=8，不加载 embedding 模型，毫秒级）。

    【为什么先 pop 一次】同进程里可能有别的用例用 `sys.modules["sqlite_vec"] = None`
    模拟"可选依赖缺失"，若没还原，后续 import 会报
    `import of sqlite_vec halted; None in sys.modules`。这里先清掉污染再导入；
    真不可用（CI 没装 sqlite-vec）就显式 skip，而不是让 3 条用例看着像回归。
    """
    if sys.modules.get("sqlite_vec", "absent") is None:
        sys.modules.pop("sqlite_vec", None)
    try:
        import sqlite_vec  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"本机缺少可用的 sqlite-vec 扩展（{type(exc).__name__}），跳过后端层用例")
    return SqliteVecBackend(db_path=str(tmp_path / "vec.db"), collection_name="t", dim=DIM)


def _add(backend, item_id, text):
    vec = [0.1] * DIM
    assert backend.add(item_id=item_id, content=text, embedding=vec,
                       metadata={"k": item_id}, timestamp="2026-10-25T00:00:00+08:00")


# ════════════════════════════════════════════════════════════
#  后端层
# ════════════════════════════════════════════════════════════


def test_backend_delete_removes_both_tables(tmp_path):
    backend = _backend(tmp_path)
    _add(backend, "mem_a", "要删掉的记忆")
    _add(backend, "mem_b", "要保留的记忆")
    assert backend.count() == 2

    assert backend.delete("mem_a") is True
    assert backend.get_by_id("mem_a") is None
    assert backend.count() == 1
    # 另一条必须原样在（不能顺手清空）
    assert backend.get_by_id("mem_b")["content"] == "要保留的记忆"
    # KNN 查不到已删项（证明 vec0 里的向量也删了，而不是只删了 meta）
    hits = backend.search([0.1] * DIM, top_k=5)
    assert [h["id"] for h in hits] == ["mem_b"]


def test_backend_delete_missing_returns_false(tmp_path):
    backend = _backend(tmp_path)
    assert backend.delete("never_existed") is False
    assert backend.count() == 0


# ════════════════════════════════════════════════════════════
#  HTTP 层
# ════════════════════════════════════════════════════════════


class _VectorStub:
    """最小向量记忆替身：与真实 VectorStore 的同名方法集（trap #10）。"""

    def __init__(self, existing=("mem_ok",)):
        self.existing = set(existing)
        self.calls = []

    def delete(self, item_id):
        self.calls.append(item_id)
        if item_id not in self.existing:
            return False
        self.existing.discard(item_id)
        return True


@pytest.fixture()
def client(monkeypatch):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    stub = _VectorStub()

    class _Yunshu:
        _vector_memory = stub

    fake_app_server = types.ModuleType("app_server")
    fake_app_server._Yunshu = _Yunshu
    fake_app_server.logger = logging.getLogger("fake_app_server")
    fake_app_server.require_token = lambda f: f
    fake_app_server.log_request = lambda *a, **k: (lambda f: f)
    monkeypatch.setitem(sys.modules, "app_server", fake_app_server)

    fake_audit = types.ModuleType("agent.audit.facade")

    class _Facade:
        @staticmethod
        def record(action, **kwargs):
            return types.SimpleNamespace(seq=4242)

    fake_audit.audit = _Facade()
    monkeypatch.setitem(sys.modules, "agent.audit.facade", fake_audit)

    from flask import Flask

    from agent.api_envelope import install_error_handlers

    mem = importlib.import_module("plugins.memory")
    app = Flask(__name__)
    app.register_blueprint(mem.bp)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client(), stub


def test_route_deletes_existing_item(client):
    http, stub = client
    resp = http.delete("/api/vector/item/mem_ok")
    assert resp.status_code == 200
    assert resp.headers.get("X-Envelope") == "v2"
    data = resp.get_json()["data"]
    assert data["deleted"] is True and data["item_id"] == "mem_ok"
    assert data["audit"]["seq"] == 4242
    assert stub.calls == ["mem_ok"]


def test_route_missing_item_is_404_not_fake_ok(client):
    http, _stub = client
    resp = http.delete("/api/vector/item/mem_gone")
    assert resp.status_code == 404
    assert resp.mimetype == "application/problem+json"
    assert resp.get_json()["type"].endswith("vector-item-not-found")


def test_route_rejects_overlong_item_id(client):
    """超长 id 直接 400，且**不触碰存储**（形态闸门在前）。"""
    http, stub = client
    resp = http.delete("/api/vector/item/" + "a" * 200)
    assert resp.status_code == 400
    assert resp.get_json()["type"].endswith("invalid-item-id")
    assert stub.calls == [], "形态非法时不应触碰存储"


def test_route_unavailable_vector_store_is_503(monkeypatch):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    class _Yunshu:
        _vector_memory = None

    fake_app_server = types.ModuleType("app_server")
    fake_app_server._Yunshu = _Yunshu
    fake_app_server.logger = logging.getLogger("fake_app_server")
    fake_app_server.require_token = lambda f: f
    fake_app_server.log_request = lambda *a, **k: (lambda f: f)
    monkeypatch.setitem(sys.modules, "app_server", fake_app_server)

    from flask import Flask

    mem = importlib.import_module("plugins.memory")
    app = Flask(__name__)
    app.register_blueprint(mem.bp)
    app.config["TESTING"] = True
    resp = app.test_client().delete("/api/vector/item/mem_ok")
    assert resp.status_code == 503

