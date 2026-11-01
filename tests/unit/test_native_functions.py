# -*- coding: utf-8 -*-
"""元生功能（agent/skills_mgmt/native_functions.py）+ /api/skills-mgmt/native-functions 的测试。

守三件事：
1. 清单分两组：内置工具 / 常驻机制（常驻机制是手工清单，必须包含 H-3 两条）；
2. 重叠提示用 **containment**（技能侧被覆盖比例）+ 点名工具强信号，返回可解释的 reason；
3. 路由挂了令牌（静态标记），且返回结构与界面契约一致。
"""
from __future__ import annotations

import pytest
from flask import Flask

from agent import server_auth
from agent.skills_mgmt import native_functions as nf
from agent.skills_mgmt.transform.compare import containment

FAKE_TOOLS = [
    {"id": "read_file", "name": "read_file",
     "summary": "读取文件内容，支持按行区间读取文本文件", "status": "ok"},
    {"id": "kb_capture", "name": "kb_capture",
     "summary": "收集素材入库，文本或文件路径进 inbox", "status": "ok"},
]


def test_常驻机制含_H3_两条():
    ids = {m["id"] for m in nf.RESIDENT_MECHANISMS}
    assert {"global-core-principles", "skill"} <= ids
    assert len(ids) >= 5


def test_containment_与_jaccard_的区别():
    skill = "需要读取文件时"
    tool = "读取文件内容，支持按行区间读取文本文件，并返回行号与编码信息"
    # 长文本把 Jaccard 稀释了，但技能侧几乎被覆盖
    assert containment(skill, tool) > 0.2
    assert containment("完全无关的内容", tool) < 0.2


def test_点名工具是强信号():
    skills = [{"skill_id": "demo", "name": "演示",
               "trigger": ["需要读取文件时"], "steps": ["用 read_file 读指定文件"],
               "params": [], "guard": []}]
    hints = nf.overlap_hints(skills, FAKE_TOOLS)
    assert hints and hints[0]["skill_id"] == "demo"
    top = hints[0]["matches"][0]
    assert top["native_id"] == "read_file"
    assert top["reason"] == "explicit_mention" and top["score"] >= 0.6


def test_无关技能不进重叠清单():
    skills = [{"skill_id": "paint", "name": "配色",
               "trigger": ["需要给界面配色时"], "steps": ["先选主色再调对比度"],
               "params": [], "guard": []}]
    assert nf.overlap_hints(skills, FAKE_TOOLS) == []


def test_空技能正文被跳过():
    skills = [{"skill_id": "empty", "name": "空", "trigger": [], "steps": [],
               "params": [], "guard": []}]
    assert nf.overlap_hints(skills, FAKE_TOOLS) == []


def test_结构契约(monkeypatch):
    from agent.server_routes import routes_skills_mgmt
    monkeypatch.setattr(server_auth, "authorize_request", lambda: (True, "t", "t"))
    app = Flask(__name__)
    routes_skills_mgmt.register_routes(app, state=None)
    view = app.view_functions["api_skills_mgmt_native_functions"]
    assert getattr(view, server_auth.REQUIRES_TOKEN_ATTR, False) is True

    resp = app.test_client().get("/api/skills-mgmt/native-functions")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    kinds = {g["kind"] for g in data["groups"]}
    assert {"tool", "resident"} <= kinds
    assert isinstance(data["overlaps"], list)
    assert data["threshold"] == nf.OVERLAP_THRESHOLD
