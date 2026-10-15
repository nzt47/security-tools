# -*- coding: utf-8 -*-
"""路由↔UI 覆盖盘点：归一化 / 分类 / 产物一致性 / 端点（2026-10-07）。

【锁死三件事】
  1. 归一化：Flask 的 <int:x>/<x> 与 JS 的 ${x} 折成同一个 *；不同路径不能被混同；
  2. 分类：classify() 的四类计数自洽，且 --check 的 content_hash 与提交产物一致；
  3. 端点：/api/audit/route-ui-coverage 走统一信封；产物缺失时报 404 且**响亮**。
"""
from __future__ import annotations

import importlib
import json
import logging
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(300)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _mod():
    return importlib.import_module("scripts.audit.route_ui_coverage")


class Test归一化:
    def test_flask_变量段与_js_模板都折成星号(self):
        m = _mod()
        assert m.norm("/api/sessions/<string:sid>/messages") == m.norm("/api/sessions/${sid}/messages")
        assert m.norm("/api/sessions/<int:sid>") == "/api/sessions/*"

    def test_不同路径不得混同(self):
        m = _mod()
        assert m.norm("/api/skills") != m.norm("/api/skills-mgmt")
        assert m.norm("/api/skills/") == "/api/skills"

    def test_查询串被剥离(self):
        m = _mod()
        assert m.norm("/api/x?a=1") == "/api/x"


class Test分类:
    def test_四类计数自洽且非空(self):
        m = _mod()
        payload = m.classify()
        t = payload["totals"]
        assert t["routes"] > 100, "路由数太少，不像全仓"
        assert t["routes"] == t["ui"] + t["cli_script"] + t["runtime_only"] + t["unreferenced"]
        # 活体（线上会被服务）与死副本（该路径还有未接线定义）是**两个正交事实**：
        # 同一路径可以既 live、又 dead_copy（活体在 plugins/x.py、死副本在未接线的
        # agent/server_routes/y.py）。故不再要求 live + dead_copy == routes。
        assert t["live"] <= t["routes"], "活体数不得超过路由总数"
        assert t["dead_copy"] <= t["routes"], "死副本数不得超过路由总数"
        entries = payload["entries"]
        assert t["live"] == sum(1 for e in entries if e["live"])
        assert t["dead_copy"] == sum(1 for e in entries if e["dead_copy"])
        assert t["dead_definitions"] == sum(len(e["dead_defs"]) for e in entries) or \
            t["dead_definitions"] >= t["dead_copy"], "死副本定义位点应不少于死副本路径数"
        # 活体分类之和 == 活体数；且 ui 里活体只是一部分（死副本与活体同路径时也会被前端引用）
        assert sum(t["live_by_category"].values()) == t["live"]
        assert 0 <= t["live_by_category"]["ui"] <= t["ui"]

    def test_未分类活体路由不得超过上限_只允许收缩(self):
        """「新路由必须被分类」的收口：活体 unreferenced 数只能降不能升。

        与 failures_baseline.txt 同族：上限写在一个**独立提交文件**里，
        重派生产物**不会**顺手抬高它 —— 想涨必须显式改这个文件并写明理由。
        """
        m = _mod()
        ceiling_path = ROOT / "reports" / "route_ui_unreferenced_ceiling.json"
        assert ceiling_path.exists(), "缺少 reports/route_ui_unreferenced_ceiling.json"
        ceiling = json.loads(ceiling_path.read_text(encoding="utf-8"))["live_unreferenced"]
        current = m.classify()["totals"]["live_by_category"]["unreferenced"]
        assert current <= ceiling, (
            "新增了未分类的活体路由：当前 " + str(current) + " > 上限 " + str(ceiling)
            + "。把它接进前端 UI 或 CLI/脚本，或显式抬高该上限并写明理由（只允许收缩）。"
        )

    def test_产物与工作树一致(self):
        """--check 的实质：重算 content_hash 必须等于提交产物里的那个。"""
        m = _mod()
        art = ROOT / "reports" / "route_ui_coverage.json"
        assert art.exists(), "缺少提交产物 reports/route_ui_coverage.json（运行脚本生成）"
        old = json.loads(art.read_text(encoding="utf-8"))
        assert old.get("content_hash") == m.classify()["content_hash"], (
            "路由↔UI 分类已漂移：运行 python scripts/audit/route_ui_coverage.py 重新派生并提交"
        )


class Test活体判定:
    """回归：where 带 ":行号"，判活必须先剥行号。

    历史缺陷：旧实现拿 "agent/server_routes/routes_x.py:123" 直接取模块名，得到
    "routes_x.py:123"，永远命不中 registered_mods ⇒ 所有 server_routes 定义被判死，
    实测 457 条路由里 223 条假死（dead_copy 虚高）。
    """

    def test_定义文件判活必须先剥行号(self):
        m = _mod()
        registered = {"routes_skills_mgmt"}
        assert m._is_live_where(
            "agent/server_routes/routes_skills_mgmt.py:166", registered) is True
        assert m._is_live_where(
            "agent/server_routes/routes_personality.py:23", registered) is False
        assert m._is_live_where("plugins/status.py:665", set()) is True
        assert m._is_live_where("app_server.py:2098", set()) is True

    def test_已接线模块的路由不再被判死(self):
        m = _mod()
        entries = {e["norm"]: e for e in m.classify()["entries"]}
        # 只定义在已接线的 routes_skills_mgmt.py 里 ⇒ 活体、且无死副本
        e = entries["/api/skills-mgmt"]
        assert e["live"] is True, "已接线模块的路由被误判为死副本"
        assert e["dead_copy"] is False
        assert e["dead_defs"] == []
        # 活体在 plugins/status.py、死副本在未接线的 routes_personality.py ⇒ 两个事实都记下
        p = entries["/api/personality"]
        assert p["live"] is True, "plugins/status.py 提供了它，应判活"
        assert p["dead_copy"] is True, "该路径还有未接线定义，应记死副本"
        assert any("routes_personality.py" in x for x in p["dead_defs"])


class Test说明:
    """每条路由都要有一句「它做什么」；来源可追溯，且不参与门禁哈希。"""

    def test_first_doc_line_压平空白并截断(self):
        m = _mod()
        assert m._first_doc_line("\n\n  第一行\n第二行") == "第一行"
        assert m._first_doc_line(None) == ""
        assert len(m._first_doc_line("x" * 300)) == 120

    def test_每条路由都有非空说明且来源可追溯(self):
        m = _mod()
        entries = m.classify()["entries"]
        missing = [e["path"] for e in entries if not e.get("description")]
        assert missing == [], "这些路由没有任何说明：" + repr(missing[:10])
        assert all(e["description_source"] in ("handler", "module") for e in entries)

    def test_说明优先取处理函数_docstring(self):
        m = _mod()
        entries = {e["norm"]: e for e in m.classify()["entries"]}
        e = entries["/api/agent-lines"]
        assert e["description_source"] == "handler"
        assert "主线档案" in e["description"]

    def test_说明不参与门禁哈希(self):
        """说明随注释漂移，改它不得触发 --check 漂移（与 refs 同理）。"""
        m = _mod()
        payload = m.classify()
        h1 = payload["content_hash"]
        for e in payload["entries"]:
            e["description"] = "被改掉的说明"
            e["description_source"] = "module"
        assert m._content_hash(payload) == h1


class Test意图标注:
    """方案 §7：人工标注「该进 UI / 不该进」，只把「该进却没进」生成待办。"""

    def test_标注文件被消费且取值合法(self):
        m = _mod()
        intents = m._load_intents()
        assert intents, "config/route_ui_intent.json 应被解析出标注"
        assert intents["/api/browser/screenshot"]["intent"] == "should_be_in_ui"
        assert all(v["intent"] in m.INTENT_VALUES for v in intents.values())

    def test_每条路由都带意图字段且ui_pending自洽(self):
        m = _mod()
        payload = m.classify()
        valid = {"should_be_in_ui", "should_not_be_in_ui", "unclassified"}
        for e in payload["entries"]:
            assert e["intent"] in valid
            assert e["ui_pending"] is (
                e["intent"] == "should_be_in_ui" and e["live"] and e["category"] != "ui"
            )
        pending_norms = {p["norm"] for p in payload["pending_ui"]}
        assert pending_norms == {e["norm"] for e in payload["entries"] if e["ui_pending"]}
        assert not any(
            e["ui_pending"] for e in payload["entries"] if e["intent"] == "should_not_be_in_ui"
        )

    def test_该进未进待办已清空(self):
        """分批接入完成：所有标注 should_be_in_ui 的活体路由都已进 UI。"""
        m = _mod()
        payload = m.classify()
        pending = sorted(e["norm"] for e in payload["entries"] if e["ui_pending"])
        assert pending == [], "仍有该进却未进的路由：" + repr(pending)
        assert payload["pending_ui"] == []

    def test_已接入的路由不再是待办(self):
        """分批接入的回环：接入后 category=ui 且 ui_pending=False。"""
        m = _mod()
        entries = {e["norm"]: e for e in m.classify()["entries"]}
        for route in ("/api/config/logs", "/api/browser/screenshot",
                      "/api/browser/close", "/api/clipboard",
                      "/api/cognitive/status", "/api/cognitive/reject"):
            assert entries[route]["category"] == "ui", route
            assert entries[route]["ui_pending"] is False, route

    def test_认知调试面被标注为不该进(self):
        """cognitive/prompt 与 cognitive/translate 属调试面，判为不该进。"""
        m = _mod()
        entries = {e["norm"]: e for e in m.classify()["entries"]}
        for route in ("/api/cognitive/prompt", "/api/cognitive/translate/*"):
            assert entries[route]["intent"] == "should_not_be_in_ui", route
            assert entries[route]["ui_pending"] is False, route

    def test_baseURL_相对字面量被计入_ui(self):
        """src/api/ 下 request({url: '/dashboard/summary'}) 经 baseURL=/api ⇒ 应算 ui。"""
        m = _mod()
        entries = {e["norm"]: e for e in m.classify()["entries"]}
        entry = entries["/api/dashboard/summary"]
        assert entry["category"] == "ui"
        assert entry["ui_refs"], "应记录 src/api 下的引用"
        assert entry["ui_pending"] is False

    def test_标注文件缺失时退化为空而不是抛错(self, tmp_path, monkeypatch):
        m = _mod()
        monkeypatch.setattr(m, "INTENT_FILE", tmp_path / "missing.json")
        assert m._load_intents() == {}

    def test_标注文件损坏时退化为空而不是抛错(self, tmp_path, monkeypatch):
        m = _mod()
        bad = tmp_path / "bad.json"
        bad.write_text("{ not json", encoding="utf-8")
        monkeypatch.setattr(m, "INTENT_FILE", bad)
        assert m._load_intents() == {}


@pytest.fixture
def cov_client(monkeypatch, tmp_path):
    fake = types.ModuleType("app_server")
    fake.require_token = lambda f: f
    fake.log_request = lambda *a, **k: (lambda f: f)
    fake.logger = logging.getLogger("fake_app_server")
    monkeypatch.setitem(sys.modules, "app_server", fake)

    from flask import Flask
    from agent.api_envelope import install_error_handlers

    mod = importlib.import_module("plugins.audit_coverage")
    artifact = tmp_path / "route_ui_coverage.json"
    monkeypatch.setattr(mod, "_ARTIFACT", str(artifact))

    app = Flask(__name__)
    app.register_blueprint(mod.bp)
    install_error_handlers(app, enabled=True)
    app.config["TESTING"] = True
    return app.test_client(), artifact


class Test端点:
    def test_信封与载荷(self, cov_client):
        client, artifact = cov_client
        artifact.write_text(json.dumps({"totals": {"routes": 1}, "content_hash": "x"}), encoding="utf-8")
        resp = client.get("/api/audit/route-ui-coverage")
        assert resp.headers.get("X-Envelope") == "v2", "成功响应必须带统一信封头"
        body = resp.get_json()
        assert body["code"] == 200
        assert body["data"]["totals"]["routes"] == 1

    def test_产物缺失时报404且响亮(self, cov_client):
        client, _artifact = cov_client  # 不写文件
        resp = client.get("/api/audit/route-ui-coverage")
        assert resp.status_code == 404, "产物缺失应 404，不得静默返回空对象"
        assert resp.headers.get("X-Envelope") == "v2", "错误路径同样带信封（本仓统一模型）"
