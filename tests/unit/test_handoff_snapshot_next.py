# -*- coding: utf-8 -*-
"""交接卡：坏 handoff_next.json 必须**响亮**失败（不能显示成「下一步：（无）」）

2026-11-09 实测：一条没转义的引号让 6 条 open 从卡上消失，卡片写着「下一步：（无）」——
读起来像「活都干完了」。本文件钉住两条：①解析失败原样上屏；②正常文件仍照旧渲染。
"""
from __future__ import annotations

import json

import scripts.handoff_snapshot as snap


def test_坏_json_显式上屏而不是无():
    out = snap._render_next({"error": "ValueError: Expecting ',' delimiter"})
    assert "解析失败" in out
    assert "（无）" not in out
    assert "下一步不可知" in out


def test_读坏文件时带出错误(monkeypatch, tmp_path):
    bad = tmp_path / "handoff_next.json"
    bad.write_text('{"items": [ }', encoding="utf-8")
    monkeypatch.setattr(snap, "NEXT_PATH", bad)
    nxt = snap._next_actions()
    assert nxt.get("error")
    assert "解析失败" in snap._render_next(nxt)


def test_正常文件照旧渲染(monkeypatch, tmp_path):
    good = tmp_path / "handoff_next.json"
    good.write_text(json.dumps({"items": [
        {"id": "a", "status": "open"},
        {"id": "b", "status": "done"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(snap, "NEXT_PATH", good)
    nxt = snap._next_actions()
    assert [i["id"] for i in nxt["open"]] == ["a"]
    assert snap._render_next(nxt).startswith("未完成 1 项：a")
