# -*- coding: utf-8 -*-
"""ui_guard（UI 规范守卫）单测：引擎 / 修复 / HTTP / 自动发现声明。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from agent.ui_guard import check, fix, health
from agent.ui_guard import engine, fixer


pytestmark = [pytest.mark.unit]


def _tmp_ui(tmp_path: Path, body: str) -> Path:
    src = tmp_path / "yunshu-ui" / "src" / "components"
    src.mkdir(parents=True)
    p = src / "Foo.tsx"
    p.write_text(body, encoding="utf-8")
    return p


def test_模块声明了状态源与域():
    import agent.ui_guard as pkg
    assert pkg.MODULE_STATUS_SOURCE == "api:/api/ui-guard/health"
    assert pkg.MODULE_DOMAIN == "ops"
    assert pkg.MODULE_NAME


def test_check_报告形状与基线():
    r = check(limit=5)
    d = r.to_dict(limit=5)
    assert d["schema_version"] == 1
    assert d["mode"] == "fast"
    assert set(d["counts"]) >= {"total", "error", "warning"}
    assert d["baseline"]["passed"] is True
    assert d["baseline"]["regressions"] == []
    assert len(d["findings"]) <= 5
    assert d["scanned_files"] > 0
    assert d["duration_ms"] >= 0


def test_check_规则过滤():
    r = check(rules=[engine.RULE_TABS], limit=10)
    assert all(f.rule == engine.RULE_TABS for f in r.findings)


def test_check_rules_过滤_空结果不报错():
    r = check(rules=["no.such.rule"], limit=10)
    assert r.findings == []
    assert r.baseline["passed"] is True


def test_按钮对齐与标题字号能定位(tmp_path):
    _tmp_ui(tmp_path, 'export function X(){ return <div><Button className="w-full text-left">x</Button><h2>t</h2></div> }\n')
    r = check(root=tmp_path, limit=50)
    rules = {f.rule for f in r.findings}
    assert engine.RULE_BUTTON in rules
    assert engine.RULE_HEADING in rules
    btn = [f for f in r.findings if f.rule == engine.RULE_BUTTON][0]
    assert btn.line == 1 and btn.fixable is True


def test_表达式_className_能报但不可自动修(tmp_path):
    _tmp_ui(tmp_path, 'export function X(){ return <div><h2 className={cn("x")}>t</h2></div> }\n')
    r = check(root=tmp_path, limit=20)
    hits = [f for f in r.findings if f.rule == engine.RULE_HEADING]
    assert len(hits) == 1 and hits[0].fixable is False
    assert fix(dry_run=True, root=tmp_path)["change_count"] == 0


def test_fix_dry_run_不写盘(tmp_path):
    p = _tmp_ui(tmp_path, 'export function X(){ return <div><Button className="w-full">x</Button><h2>t</h2></div> }\n')
    before = p.read_text(encoding="utf-8")
    res = fix(dry_run=True, root=tmp_path)
    assert res["applied"] is False
    assert res["change_count"] == 2
    assert p.read_text(encoding="utf-8") == before


def test_fix_落盘并复检(tmp_path):
    p = _tmp_ui(tmp_path, 'export function X(){ return <div><Button className="w-full">x</Button><h2>t</h2></div> }\n')
    res = fix(dry_run=False, root=tmp_path, files=["yunshu-ui/src/components/Foo.tsx"])
    assert res["applied"] is True and res["errors"] == []
    text = p.read_text(encoding="utf-8")
    assert "justify-start" in text and 'className="text-ui"' in text
    after = check(root=tmp_path, limit=50)
    assert engine.RULE_BUTTON not in {f.rule for f in after.findings}
    assert engine.RULE_HEADING not in {f.rule for f in after.findings}


def test_health_形状():
    h = health()
    assert h["module"] == "ui_guard"
    assert h["status"] in ("healthy", "warning")
    assert "baseline_regressions" in h
    assert isinstance(h["overall"], float)


def _client():
    from agent.ui_guard import routes
    app = Flask(__name__)
    routes.register_routes(app, None)
    app.config["TESTING"] = True
    return app.test_client()


def test_http_check_与_rules():
    c = _client()
    r = c.get("/api/ui-guard/check?limit=3&mode=fast")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["report"]["findings_total"] >= 0
    rules = c.get("/api/ui-guard/rules").get_json()
    assert rules["ok"] is True and any(x["id"] == engine.RULE_BUTTON for x in rules["rules"])
    h = c.get("/api/ui-guard/health").get_json()
    assert h["module"] == "ui_guard"


def test_http_fix_默认干跑_落盘要_confirm():
    c = _client()
    r = c.post("/api/ui-guard/fix", json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["result"]["dry_run"] is True
    bad = c.post("/api/ui-guard/fix", json={"dry_run": False})
    assert bad.status_code == 400
    assert "confirm" in bad.get_json()["error"]
