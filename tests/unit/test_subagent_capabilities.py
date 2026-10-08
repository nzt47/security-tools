"""主权分身能力投影守卫（agent/subagent/capabilities.py + GET /api/subagent/capabilities）

组装台的「主权清单」必须由**后端**给事实，否则前端会把"字段在、没人读"化妆成"已接线"。
本文件钉死四件事：

  1. **三态词表封闭**：owned / partial / missing，UI 的标签由后端派生；
  2. **反幻觉**：说某面"拥有"就必须指得到实现文件，且这些路径**真实存在**；
  3. **partial / missing 不许含糊**：必须写明 gap（缺什么）与 next_stage（何时补）；
  4. **只读投影**：路由返回 20 面，且报告里不含任何密钥 / 配置值。

【不易】最小 Flask app + 替身（**不 import app_server**），与既有 subagent 路由测试同款。
"""

from __future__ import annotations

import json

import pytest
from flask import Flask

from agent.server_routes.routes_subagent import register_routes
from agent.subagent.capabilities import (
    LAYERS,
    MATURITY_LEVELS,
    MISSING,
    OWNED,
    PARTIAL,
    SOVEREIGNTY_FACES,
    STATES,
    STATE_LABELS,
    SovereigntyFace,
    sovereignty_report,
    verify_faces,
)


class TestTable:
    def test_自检零问题(self):
        assert verify_faces() == [], "唯一权威表自身必须自洽（含证据文件存在性）"

    def test_六层二十面(self):
        assert len(LAYERS) == 6
        assert len(SOVEREIGNTY_FACES) == 20
        assert len({f.key for f in SOVEREIGNTY_FACES}) == 20, "key 必须唯一"

    def test_每层至少一面且都在词表内(self):
        layer_keys = [layer["key"] for layer in LAYERS]
        for key in layer_keys:
            assert [f for f in SOVEREIGNTY_FACES if f.layer == key], "空层: " + key
        for face in SOVEREIGNTY_FACES:
            assert face.layer in layer_keys

    def test_状态词表封闭(self):
        assert STATES == (OWNED, PARTIAL, MISSING)
        assert set(STATE_LABELS) == set(STATES)
        for face in SOVEREIGNTY_FACES:
            assert face.state in STATES

    def test_owned面必须有证据文件(self):
        for face in SOVEREIGNTY_FACES:
            if face.state == OWNED:
                assert face.evidence_files, face.key + " 声称拥有却没有证据文件"

    def test_partial与missing必须给缺口与阶段(self):
        for face in SOVEREIGNTY_FACES:
            if face.state in (PARTIAL, MISSING):
                assert face.gap.strip(), face.key + " 缺 gap"
                assert face.next_stage.strip(), face.key + " 缺 next_stage"

    def test_阶段词表封闭(self):
        # owned 面允许带"残余边界"（如模型同 provider 已拥有、跨 provider 属 S5），
        # 故 next_stage 对 owned 是可选的；但只能是空串或已登记阶段，不许自造。
        allowed = {"", "S3", "S4", "S5"}
        for face in SOVEREIGNTY_FACES:
            assert face.next_stage in allowed, face.key + " 的 next_stage 不在词表内"

    def test_反幻觉_假证据文件被自检抓住(self):
        fake = SovereigntyFace(
            key="fake", layer="brain", label="假的", question="q",
            state=OWNED, evidence="编造的", evidence_files=("no/such/file.py",))
        problems = verify_faces((fake,), require_files=True)
        assert any("不存在" in p for p in problems), (
            "证据文件不存在必须被判为问题 —— 否则能力清单会退化成一份编造的 PPT")

    def test_反幻觉_真表逐个文件存在(self):
        assert verify_faces(require_files=True) == []


class TestReport:
    def test_报告结构(self):
        report = sovereignty_report()
        assert len(report["layers"]) == 6
        assert len(report["faces"]) == len(SOVEREIGNTY_FACES)
        assert report["summary"]["total"] == len(SOVEREIGNTY_FACES)
        assert report["maturity"]["current"] in {"L0", "L1", "L2", "L3"}
        assert len(report["maturity"]["levels"]) == len(MATURITY_LEVELS)

    def test_summary与面一致(self):
        report = sovereignty_report()
        summary = report["summary"]
        for state in STATES:
            assert summary[state] == sum(1 for f in SOVEREIGNTY_FACES if f.state == state)
        assert summary["owned"] + summary["partial"] + summary["missing"] == 20

    def test_层内面键与面表一致(self):
        report = sovereignty_report()
        by_layer = {}
        for face in report["faces"]:
            by_layer.setdefault(face["layer"], []).append(face["key"])
        for layer in report["layers"]:
            assert layer["faces"] == by_layer.get(layer["key"], [])

    def test_报告不含密钥形态(self):
        blob = json.dumps(sovereignty_report(), ensure_ascii=False)
        for forbidden in ("sk-", "api_key", "FLASK_API_TOKEN", "Bearer"):
            assert forbidden not in blob


class TestRoute:
    @pytest.fixture
    def client(self):
        app = Flask(__name__)
        app.config.update(TESTING=True)
        register_routes(app, type("S", (), {"Yunshu": object()})())
        return app.test_client()

    def test_路由返回二十面且带三态(self, client):
        resp = client.get("/api/subagent/capabilities")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["ok"] is True
        caps = body["capabilities"]
        assert len(caps["faces"]) == 20
        assert caps["summary"]["total"] == 20
        states = {f["state"] for f in caps["faces"]}
        assert states <= set(STATES)
        for face in caps["faces"]:
            assert face["state_label"] == STATE_LABELS[face["state"]]
            assert face["evidence"].strip()

    def test_路由只读_不改任何东西(self, client):
        first = client.get("/api/subagent/capabilities").get_json()
        second = client.get("/api/subagent/capabilities").get_json()
        assert first == second, "能力投影必须幂等（只读、无副作用）"