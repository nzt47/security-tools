"""健康看板必须读五层探针（2026-11-10 修「3 项假绿」）。

旧实现：`/api/health/dashboard` 调 `assess()`（无参）⇒ 直接返回 `HealthScore` 的**硬编码默认维度**
`{response_time, error_rate, tool_success}`（全 1.0）⇒ 系统监控页「健康检查项 3 / 异常项 0」
是常量而不是测量结果，还与「健康仪表盘」的五层探针口径对不上。

新实现：`assess_with_probes(run_all_probes())`；不可用的层 `score=None`（页面显示「无数据」，
不当 0 也不当满分），并新增 `layers`（每层 available/detail/score）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask

from agent.health import dashboard as dash


def _probe(score, available=True, detail="ok", metrics=None):
    return SimpleNamespace(score=score, available=available, detail=detail,
                          metrics=metrics or {})


@pytest.fixture
def client(monkeypatch):
    probes = {
        "l1_process": _probe(1.0, detail="mem=58.0%"),
        "l2_dependency": _probe(1.0, detail="configs 2/2"),
        "l3_llm_tool": _probe(None, available=False, detail="无 LLM 调用记录"),
        "l4_business": _probe(1.0, detail="http_requests=106"),
        "l5_semantic": _probe(None, available=False, detail="近 7 天无 like/dislike"),
    }
    monkeypatch.setattr(dash, "run_all_probes", lambda: probes)
    app = Flask(__name__)
    app.register_blueprint(dash.health_bp)
    return app.test_client(), probes


def test_dimensions_are_the_five_probe_layers(client):
    http, probes = client
    body = http.get("/api/health/dashboard").get_json()["data"]
    assert set(body["layers"]) == set(probes), "layers 必须是五层探针明细，而不是旧的三个默认键"
    assert set(body["dimensions"]) == set(probes), "dimensions 与探针同源"


def test_unavailable_layer_is_none_not_zero(client):
    http, _ = client
    body = http.get("/api/health/dashboard").get_json()["data"]
    assert body["dimensions"]["l3_llm_tool"] is None, "无数据的层不能折算成 0（假告警）也不能是满分"
    assert body["dimensions"]["l5_semantic"] is None
    assert any("l3_llm_tool" in str(s) for s in body["issues"]), "无数据的层要留 issue 说明"


def test_overall_is_weighted_over_available_layers(client):
    http, _ = client
    body = http.get("/api/health/dashboard").get_json()["data"]
    # 可用层 l1/l2/l4 都是 1.0 ⇒ 加权平均 1.0（不可用层不参与）
    assert body["overall_health"] == pytest.approx(1.0, abs=1e-6)


def test_history_still_present(client):
    http, _ = client
    body = http.get("/api/health/dashboard").get_json()["data"]
    assert isinstance(body["history"], list), "历史字段保持（前端趋势图在用）"
