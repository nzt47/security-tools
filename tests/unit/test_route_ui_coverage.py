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
        assert t["live"] + t["dead_copy"] == t["routes"]
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
