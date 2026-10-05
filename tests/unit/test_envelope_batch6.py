"""P1-front **第六批**：记忆/检索四个端点的显式信封契约（2026-10-05）。

【本批端点】记忆与检索面，前三个在 plugins/memory.py，第四个在 agent/server_routes/routes_knowledge.py：
  · GET  /api/memory/overview   → pages/hub/memory/index.tsx
  · POST /api/memory/manual     → pages/hub/memory/index.tsx
  · POST /api/vector/search     → pages/hub/memory/index.tsx 与 memory/search.tsx（两个消费方）
  · POST /api/knowledge/query   → pages/hub/memory/search.tsx

【本批第一次迁 **POST** 端点】前端为此新增 postEnvelope()（src/api/envelope.ts），
它的**请求**语义与 hubPost 逐条对齐（未显式传令牌时自动附带本地令牌；
**只在确有 body 时才声明 Content-Type** —— 空 body 配 JSON 头会让后端 get_json() 抛 415/400，
本仓实测过这一条）。前端侧的守卫在 src/api/envelope.test.ts（新增 5 条，含那条内容类型断言）。

【一条**迁移顺手修掉的真实缺陷**】/api/knowledge/query 返回的键是 hits，
而迁移前前端写的是 pickList(r, 'results') —— pickList 传了 prefer 时**只找那一个键**，
于是「知识库」页签**一直返回空列表**（不报错，静默空）。本批改成显式读 hits。
本文件覆盖后端侧（信封 + 载荷）；前端侧的键名由本批提交信息与交接文档钉住。

【已知不一致，**本次不动**】/api/vector/search 的后端读 top_k（默认 5），
而前端一直传 limit: 10 ⇒ 那个 10 **从未生效**。改它属于「改请求契约」，
与本批「只改响应解析」不是一件事，故留原样并在调用点标注。

【为什么不导入 app_server】见第二批守卫的头注（本机 80–100s，CI 覆盖率分片下超 300s 预算）。
这里用最小 Flask app + 替身：plugins/memory.py 的 _view 装饰器是**惰性**从 app_server
取 require_token/log_request 的，故替身里放两个恒等实现即可，不必碰 _AUTH_DISABLED_FOR_TEST。
"""
from __future__ import annotations

import importlib
import logging
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(300)

ROOT = Path(__file__).resolve().parents[2]


class _MemoryStub:
    """_Yunshu._memory 的最小替身（记下 add_memory 的入参，供断言用）。"""

    def __init__(self):
        self.added: list = []

        class _Storage:
            @staticmethod
            def load_recent_messages(limit=20):
                return [{"role": "user", "content": "你好"}]

        class _BlackBox:
            @staticmethod
            def analyze():
                return {"black_box_events": 3}

        self._storage = _Storage()
        self._black_box = _BlackBox()

    @staticmethod
    def load_summary():
        return ("摘要正文", 7)

    def add_memory(self, item):
        self.added.append(item)


@pytest.fixture
def batch6(monkeypatch):
    """返回 (test_client, memory_stub)。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    memory_stub = _MemoryStub()

    class _Yunshu:
        _memory = memory_stub

    fake_app_server = types.ModuleType("app_server")
    fake_app_server._Yunshu = _Yunshu
    fake_app_server.logger = logging.getLogger("fake_app_server")
    # plugins/memory.py 的 _view 惰性取这两个装饰器；测试只关心响应契约，故都是恒等实现
    fake_app_server.require_token = lambda f: f
    fake_app_server.log_request = lambda *a, **k: (lambda f: f)
    monkeypatch.setitem(sys.modules, "app_server", fake_app_server)

    from flask import Flask
    from agent.api_envelope import install_error_handlers

    mem = importlib.import_module("plugins.memory")
    app = Flask(__name__)
    app.register_blueprint(mem.bp)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client(), memory_stub


def _envelope(resp, path):
    got = resp.headers.get("X-Envelope")
    assert got == "v2", (
        path + " 没有声明信封（实测头=" + repr(got) + "，HTTP " + str(resp.status_code) + "）\n"
        "两种可能：① 迁移被回退（视图改回了 jsonify）；② 信封版本变了。\n"
        "另注意：/api/memory/* 在 agent/server_routes/routes_memory.py 还有一份**未接线**的旧实现，"
        "活体在 plugins/memory.py。"
    )
    body = resp.get_json()
    assert body.get("code") == 200, repr(body)[:200]
    return body.get("data")


class Test记忆概览:
    def test_信封与载荷形状(self, batch6):
        client, _ = batch6
        data = _envelope(client.get("/api/memory/overview"), "/api/memory/overview")
        assert isinstance(data, dict), "data 应为对象，实测 " + repr(type(data).__name__)
        for key in ("summary_version", "summary_text", "recent_messages", "message_count", "log_stats"):
            assert key in data, (
                "记忆概览缺少既有键「" + key + "」⇒ 迁移信封时改变了契约。实测键："
                + repr(sorted(data.keys()))
            )
        assert data["summary_version"] == 7
        assert data["message_count"] == 1


class Test手动记忆:
    def test_信封与载荷形状(self, batch6):
        client, stub = batch6
        resp = client.post("/api/memory/manual", json={"content": "记一笔"})
        data = _envelope(resp, "/api/memory/manual")
        assert data.get("ok") is True, "data 应仍是既有的 {ok: true}，实测 " + repr(data)[:200]
        assert stub.added and "记一笔" in stub.added[0]["content"], (
            "业务动作没发生（add_memory 未被调用）—— 本用例要证明迁移只动了响应包装。"
        )

    def test_空内容仍是_400_且不带信封(self, batch6):
        """**错误路径本次不动**：仍是既有的 {ok:false,error} + 400。"""
        client, _ = batch6
        resp = client.post("/api/memory/manual", json={"content": "   "})
        assert resp.status_code == 400, "校验行为被改动了：实测 " + str(resp.status_code)
        assert resp.headers.get("X-Envelope") is None, (
            "错误侧的统一（RFC 9457）是另一条线；本批只迁成功侧，这里出现信封说明改动越界了。"
        )


class Test向量搜索:
    def test_信封与载荷形状(self, batch6):
        client, _ = batch6
        # 替身没有 _vector_memory ⇒ 走「不可用」分支（它同样是**成功**响应）
        data = _envelope(client.post("/api/vector/search", json={"query": "x"}), "/api/vector/search")
        for key in ("ok", "results", "count", "available"):
            assert key in data, (
                "向量搜索缺少既有键「" + key + "」⇒ 前端将读不到结果。实测键："
                + repr(sorted(data.keys()))
            )
        assert data["available"] is False

    def test_空查询仍是_400(self, batch6):
        client, _ = batch6
        resp = client.post("/api/vector/search", json={"query": ""})
        assert resp.status_code == 400, "校验行为被改动了：实测 " + str(resp.status_code)


class Test生产接线:
    def test_记忆端点走_plugins_memory_蓝图(self):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        mem = importlib.import_module("plugins.memory")
        assert getattr(mem, "PLUGIN", None) is not None, "plugins.memory 没有 PLUGIN"
        assert mem.PLUGIN.blueprint is mem.bp, "PLUGIN.blueprint 与模块级 bp 不是同一个对象"
        src = (ROOT / "plugins" / "memory.py").read_text(encoding="utf-8")
        for route in (
            '@bp.route("/api/memory/overview")',
            '@bp.route("/api/memory/manual", methods=["POST"])',
            '@bp.route("/api/vector/search", methods=["POST"])',
        ):
            assert route in src, (
                "plugins/memory.py 里找不到 " + route + " —— 若活体换了文件，请同步更新本断言与前端解析。"
            )

    def test_知识检索走_routes_knowledge(self):
        src = (ROOT / "agent" / "server_routes" / "routes_knowledge.py").read_text(encoding="utf-8")
        assert '@app.route("/api/knowledge/query", methods=["POST"])' in src, (
            "routes_knowledge.py 里的 /api/knowledge/query 不见了 —— 同上。"
        )
        assert "_ok({" in src, (
            "routes_knowledge.py 里的成功返回没有走统一信封出口 —— 前端 postEnvelope 会直接抛错。"
        )
