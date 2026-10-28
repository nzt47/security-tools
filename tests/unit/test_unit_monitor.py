# -*- coding: utf-8 -*-
"""功能单位监控（unit_monitor / unit_probes）—— 登记、超时、错误、缓存、探测口径。

背景（2026-10-28）：「功能在跑但没产出」此前没有任何统一视图（压缩 73% 空摘要、
反思 0 注入、三个学习调度器 last_run 恒 null 都看不出来）。这些用例守三件事：
① 单个 probe 抛错/卡死不能拖垮整页；② 缓存与 refresh 语义；③ 关键口径
（空摘要=warn、长期记忆 0 行=warn、无工作流=never）不被「看起来绿」掩盖。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from agent.monitoring import unit_monitor as um
from agent.monitoring import unit_probes as up


@pytest.fixture(autouse=True)
def _clean_registry():
    um.reset_registry()
    yield
    um.reset_registry()


def test_register_and_collect():
    um.register(um.Unit("x.one", "单位一", "demo", lambda: {"status": "ok", "summary": "fine"}))
    out = um.collect(refresh=True)
    assert out["summary"]["total"] == 1
    assert out["units"][0]["id"] == "x.one"
    assert out["units"][0]["status"] == "ok"


def test_probe_exception_becomes_error_not_crash():
    def boom():
        raise RuntimeError("读库炸了")

    um.register(um.Unit("x.boom", "坏单位", "demo", boom))
    um.register(um.Unit("x.ok", "好单位", "demo", lambda: {"status": "ok"}))
    out = um.collect(refresh=True)
    by_id = {u["id"]: u for u in out["units"]}
    assert by_id["x.boom"]["status"] == "error"
    assert "读库炸了" in by_id["x.boom"]["summary"]
    assert by_id["x.ok"]["status"] == "ok"


def test_probe_timeout_is_reported():
    def slow():
        time.sleep(0.5)
        return {"status": "ok"}

    um.register(um.Unit("x.slow", "慢单位", "demo", slow))
    out = um.collect(refresh=True, timeout=0.05)
    u = out["units"][0]
    assert u["status"] == "error"
    assert any("超时" in i for i in u["issues"])


def test_cache_then_refresh():
    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        return {"status": "ok", "summary": "第 %d 次" % calls["n"]}

    um.register(um.Unit("x.cache", "缓存", "demo", probe))
    first = um.collect(refresh=True)
    second = um.collect()
    assert first["cached"] is False and second["cached"] is True
    assert calls["n"] == 1
    third = um.collect(refresh=True)
    assert third["cached"] is False and calls["n"] == 2


def test_family_filter():
    um.register(um.Unit("a.skills", "S", "skills", lambda: {"status": "ok"}))
    um.register(um.Unit("a.memory", "M", "memory", lambda: {"status": "ok"}))
    out = um.collect(refresh=True, families=["memory"])
    assert [u["id"] for u in out["units"]] == ["a.memory"]
    assert out["summary"]["total"] == 1


def test_health_score_weights():
    units = [{"status": "ok"}, {"status": "warn"}, {"status": "error"}]
    assert um.health_score(units) == pytest.approx((1.0 + 0.6 + 0.0) / 3 * 100, abs=0.05)


def test_register_all_exposes_the_expected_units():
    n = up.register_all()
    assert n == 12
    ids = {u.id for u in um.registered()}
    assert {"runtime.service", "skills.usage", "memory.compress", "scheduler.tasks"} <= ids


def test_probe_memory_compress_flags_empty_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "_ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "summary.txt").write_text("", encoding="utf-8")
    (tmp_path / "data" / "summary_version.txt").write_text("88", encoding="utf-8")
    r = up.probe_memory_compress()
    assert r["status"] == "warn"
    assert "0 字节" in r["summary"]
    assert r["issues"], "空摘要必须给出可操作 issue"


def test_probe_memory_compress_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "_ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "summary.txt").write_text("x" * 120, encoding="utf-8")
    (tmp_path / "data" / "summary_version.txt").write_text("9", encoding="utf-8")
    r = up.probe_memory_compress()
    assert r["status"] == "ok" and r["metrics"]["bytes"] == 120


def test_probe_skills_usage_never_when_no_events(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "_ROOT", tmp_path)
    monkeypatch.setenv("CP_SKILL_USAGE_LOG", str(tmp_path / "empty.jsonl"))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "skills_mgmt.json").write_text(json.dumps({"skills": {}}), encoding="utf-8")
    r = up.probe_skills_usage()
    assert r["status"] == "never"
    assert r["issues"]


def test_probe_workflows_never_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "_ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "learned_workflows.json").write_text(json.dumps({"workflows": []}), encoding="utf-8")
    r = up.probe_workflows()
    assert r["status"] == "never"


def _usage_fixture(tmp_path, monkeypatch, *, enabled, hits):
    """写一份技能主轨 + 使用台账：enabled=启用技能 id，hits=每条 match 事件命中的技能 id。"""
    monkeypatch.setattr(up, "_ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "skills_mgmt.json").write_text(
        json.dumps({"skills": {sid: {"enabled": True} for sid in enabled}}), encoding="utf-8")
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("CP_SKILL_USAGE_LOG", str(log))
    now = datetime.now(timezone.utc).isoformat()
    log.write_text("".join(json.dumps({"ts": now, "stage": "match", "skill_id": sid}) + "\n"
                           for sid in hits), encoding="utf-8")


def test_probe_skills_usage_small_sample_is_not_warned(tmp_path, monkeypatch):
    # 1 次 miss 属样本不足：不能在「43 个启用技能」上判 0 命中（2026-10-29 线上误报）。
    _usage_fixture(tmp_path, monkeypatch, enabled=["a", "b", "c"], hits=[""])
    r = up.probe_skills_usage()
    assert r["status"] == "ok"
    assert r["issues"] == []
    assert r["metrics"]["sample_sufficient"] is False
    assert "样本不足" in r["summary"]


def test_probe_skills_usage_warns_when_zero_hit_share_high(tmp_path, monkeypatch):
    enabled = ["s%d" % i for i in range(10)]
    # 20 次 match 只命中 1 个技能 ⇒ 0 命中占比 90% > 80% 阈值。
    _usage_fixture(tmp_path, monkeypatch, enabled=enabled, hits=["s0"] * 20)
    r = up.probe_skills_usage()
    assert r["status"] == "warn"
    assert r["metrics"]["zero_hit_ratio"] > up.USAGE_WARN_ZERO_HIT_RATIO
    assert r["issues"] and "90%" in r["issues"][0]


def test_probe_skills_usage_ok_when_most_skills_get_hit(tmp_path, monkeypatch):
    enabled = ["s%d" % i for i in range(10)]
    hits = ["s0"] * 5 + ["s1"] * 5 + ["s2"] * 5 + ["s3"] * 5
    _usage_fixture(tmp_path, monkeypatch, enabled=enabled, hits=hits)
    r = up.probe_skills_usage()
    assert r["status"] == "ok"
    assert r["issues"] == []
    assert r["metrics"]["zero_hit_ratio"] < up.USAGE_WARN_ZERO_HIT_RATIO
