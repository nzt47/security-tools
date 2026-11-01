# -*- coding: utf-8 -*-
"""技能分层加载（agent/skills_mgmt/layered.py）+ /<id>/card 接口的测试。

守四件事：
1. 四层都能取，且**越往下越大**（L0 < L1 ≤ L2）；
2. 有 CARD.md 走 card，没有就降级 derived（不要求老技能补文件）；
3. 超预算截断并置 truncated=true（不许藏内容）；
4. 未知技能/未知层给出明确错误，不是空字符串。
"""
from __future__ import annotations

from pathlib import Path

from agent.skills_mgmt import layered
from agent.skills_mgmt.layered import load_layer

BODY = """---
id: demo-skill
name: 演示技能
description: 用来测试分层加载。
---

# 演示技能

## 何时使用

- 测试时

## 步骤

1. 第一步
2. 第二步
"""


def _mk(root: Path, sid: str = "demo-skill", card: str = "") -> Path:
    d = root / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "skill.md").write_text(BODY, encoding="utf-8")
    if card:
        (d / "CARD.md").write_text(card, encoding="utf-8")
    return d


def test_四层递进且_L1_比_L2_小(tmp_path):
    d = _mk(tmp_path)
    # 正文要够长，否则「前 40 行」就等于全文，分层看不出差别
    (d / "skill.md").write_text(
        BODY + "\n" + "\n".join("## 小节 %d\n\n内容 %d" % (i, i) for i in range(30)),
        encoding="utf-8")
    l0 = load_layer("demo-skill", "L0", repo=tmp_path)
    l1 = load_layer("demo-skill", "L1", repo=tmp_path)
    l2 = load_layer("demo-skill", "L2", repo=tmp_path)
    assert l0["ok"] and l1["ok"] and l2["ok"]
    assert l0["chars"] < l1["chars"] <= l2["chars"]
    assert l1["source"] == "derived"  # 没有 CARD.md


def test_有_CARD_md_走卡片层(tmp_path):
    _mk(tmp_path, card="# 卡\n\n- 触发：测试\n")
    r = load_layer("demo-skill", "L1", repo=tmp_path)
    assert r["source"] == "card" and "触发：测试" in r["text"]


def test_降级层含目录与正文片段(tmp_path):
    _mk(tmp_path)
    text = load_layer("demo-skill", "L1", repo=tmp_path)["text"]
    assert "[目录]" in text and "何时使用" in text and "[正文前" in text


def test_超预算截断并标记(tmp_path):
    # 用**卡片**超预算来测截断（降级层只取前 40 行，天然到不了 6000 字）
    big_card = "# 大卡\n\n" + "\n".join("- 第 %d 条" % i for i in range(2000))
    _mk(tmp_path, card=big_card)
    r = load_layer("demo-skill", "L1", repo=tmp_path)
    assert r["source"] == "card"
    assert r["truncated"] is True
    assert r["chars"] > layered.BUDGET_CHARS["L1"]
    assert "已截断" in r["text"]


def test_L3_列脚本(tmp_path):
    d = _mk(tmp_path)
    (d / "scripts").mkdir()
    (d / "scripts" / "main.py").write_text("print(1)\n", encoding="utf-8")
    r = load_layer("demo-skill", "L3", repo=tmp_path)
    assert r["scripts"] == ["main.py"]


def test_未知技能与未知层(tmp_path):
    _mk(tmp_path)
    assert load_layer("nope", "L1", repo=tmp_path)["ok"] is False
    assert load_layer("demo-skill", "L9", repo=tmp_path)["ok"] is False


def test_接口与管理器接线(monkeypatch):
    from flask import Flask
    from agent import server_auth
    from agent.server_routes import routes_skills_mgmt
    monkeypatch.setattr(server_auth, "authorize_request", lambda: (True, "t", "t"))
    app = Flask(__name__)
    routes_skills_mgmt.register_routes(app, state=None)
    view = app.view_functions["api_skills_mgmt_card"]
    assert getattr(view, server_auth.REQUIRES_TOKEN_ATTR, False) is True

    r = app.test_client().get("/api/skills-mgmt/pdf/card?layer=L1")
    assert r.status_code == 200
    data = r.get_json()
    assert data["layer"] == "L1" and data["text"]

    bad = app.test_client().get("/api/skills-mgmt/pdf/card?layer=L9")
    assert bad.status_code == 400
