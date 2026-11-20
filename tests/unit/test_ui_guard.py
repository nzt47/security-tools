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
    from agent.server_routes import routes_ui_guard as routes
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


def test_history_追加_读取_截断(tmp_path):
    from agent.ui_guard import history

    p = tmp_path / "history.jsonl"
    r = check(limit=1)
    for _ in range(3):
        assert history.record(r, source="model", path=p) is True
    items = history.recent(limit=2, path=p)
    assert len(items) == 2
    assert items[0]["source"] == "model"
    assert {"ts", "error", "warning", "total", "regressions", "scanned_files"} <= set(items[0])

    for _ in range(history.MAX_RECORDS + 3):
        history.record(r, path=p)
    lines = [x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == history.MAX_RECORDS, "历史只保留最近 N 条"


def test_history_坏行被跳过(tmp_path):
    from agent.ui_guard import history

    p = tmp_path / "history.jsonl"
    p.write_text("{not-json\n" + json.dumps({"ts": "t", "error": 0}) + "\n", encoding="utf-8")
    items = history.recent(limit=10, path=p)
    assert len(items) == 1 and items[0]["ts"] == "t"


def test_ui_guard_fix_登记为写工具且默认停用():
    import yaml
    from agent.lines.models import load_tool_meta

    y = yaml.safe_load((Path(__file__).resolve().parents[2]
                        / "data" / "tool_definitions" / "ui_guard_fix.yaml").read_text(encoding="utf-8"))
    assert y["enabled"] is False, "写工具必须默认停用"
    meta = load_tool_meta(force=True)["ui_guard_fix"]
    assert meta.effect == "write" and meta.risk == "high"
    assert meta.needs_approval is True, "写工具必须落在审批边界内"


def test_ui_guard_fix_dry_run_不写盘(tmp_path, monkeypatch):
    import agent.ui_guard.fixer as fx
    from agent import tools
    import agent.tools.ui_guard_tools as ug

    src = tmp_path / "yunshu-ui" / "src"
    src.mkdir(parents=True)
    p = src / "Foo.tsx"
    p.write_text('<div><h2>t</h2></div>\n', encoding="utf-8")
    monkeypatch.setattr(fx, "REPO_ROOT", tmp_path)
    ug.register_all()

    out = tools.call("ui_guard_fix", dry_run=True)
    assert out["ok"] is True and out["dry_run"] is True
    assert out["change_count"] == 1 and out["changes_total"] == 1
    assert "text-ui" not in p.read_text(encoding="utf-8"), "dry_run 不应写盘"


def test_ui_guard_fix_落盘(tmp_path, monkeypatch):
    import agent.ui_guard.fixer as fx
    from agent import tools
    import agent.tools.ui_guard_tools as ug

    src = tmp_path / "yunshu-ui" / "src"
    src.mkdir(parents=True)
    p = src / "Foo.tsx"
    p.write_text('<div><h2>t</h2></div>\n', encoding="utf-8")
    monkeypatch.setattr(fx, "REPO_ROOT", tmp_path)
    ug.register_all()

    out = tools.call("ui_guard_fix", dry_run=False)
    assert out["ok"] is True and out["applied"] is True
    assert 'className="text-ui"' in p.read_text(encoding="utf-8")


def test_http_history_只记显式检查(tmp_path, monkeypatch):
    hist = tmp_path / "history.jsonl"
    monkeypatch.setenv("UI_GUARD_HISTORY_PATH", str(hist))
    c = _client()
    c.get("/api/ui-guard/check?limit=1")
    assert c.get("/api/ui-guard/history?limit=10").get_json()["items"] == [], (
        "health/普通检查不应写历史")
    c.get("/api/ui-guard/check?limit=1&record=1")
    items = c.get("/api/ui-guard/history?limit=10").get_json()["items"]
    assert len(items) == 1
    assert items[0]["source"] == "api" and items[0]["error"] == 0
    assert items[0]["warning"] >= 0
