# -*- coding: utf-8 -*-
"""报警器采集（agent/alarms/collector.py）行为测试。

【钉住的口径】
1. **无数据不报警**：探针 score=None（没测到）不产生告警 —— 与 L5 一致；
2. **只有确实降级才报警**：分数 < 0.8 才报，< 0.5 升级为 critical；
3. **单位看板只报 warn/error**：never（无数据）/ off（已关闭）不报，避免把"设计如此"变噪音；
4. **数据源不可用 ≠ 已恢复**：该源读不到时，旧告警保持 firing（keep），不被误判恢复；
5. **恢复自动收口**：条件消失 → resolved。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.alarms import collector
from agent.alarms.collector import (
    AUDIT_TAIL_WINDOW,
    HEALTH_CRITICAL_SCORE,
    HEALTH_DEGRADE_SCORE,
    collect_once,
    health_candidates,
    unit_candidates,
    audit_candidate,
)
from agent.alarms.store import STATE_FIRING, STATE_RESOLVED, AlarmStore


@pytest.fixture
def store(tmp_path: Path) -> AlarmStore:
    return AlarmStore(tmp_path / "state.json", tmp_path / "events.jsonl")


def _point(**layers):
    return {"probe_details": {k: {"score": v[0], "detail": v[1]} for k, v in layers.items()}}


class TestHealthCandidates:
    def test_无数据不报警(self):
        assert health_candidates(_point(l3_llm_tool=(None, "无调用"), l4_business=(None, "无请求"))) == []
        assert health_candidates(_point(l5_semantic=(None, "近 7 天没有 like/dislike"))) == []

    def test_阈值以上不报警(self):
        assert health_candidates(_point(l1=(HEALTH_DEGRADE_SCORE, "ok"))) == []
        assert health_candidates(_point(l1=(1.0, "ok"))) == []

    def test_低于阈值报警且分级(self):
        rows = health_candidates(_point(l1_process=(0.7, "mem 90%"), l4_business=(0.2, "5xx 12%")))
        by = {r["key"]: r for r in rows}
        assert by["health.l1_process"]["severity"] == "warning"
        assert by["health.l4_business"]["severity"] == "critical"
        assert by["health.l4_business"]["key"] == "health.l4_business"
        assert "L4 业务" in by["health.l4_business"]["title"]
        assert by["health.l1_process"]["detail"] == "mem 90%"

    def test_临界值按严格小于判定(self):
        # 0.8 本身不算降级；0.799 算（不做四舍五入）
        assert health_candidates(_point(l2_dependency=(0.8, ""))) == []
        assert health_candidates(_point(l2_dependency=(0.799, "")))
        # critical 也按严格小于：0.5 还是 warning
        assert (health_candidates(_point(l2_dependency=(HEALTH_CRITICAL_SCORE, "")))[0]["severity"]
                == "warning")

    def test_空载荷与坏形状不抛(self):
        assert health_candidates(None) == []
        assert health_candidates({}) == []
        assert health_candidates({"probe_details": {"l1": "not-a-dict"}}) == []


class TestUnitCandidates:
    def test_只报_warn_与_error(self):
        payload = {"units": [
            {"id": "skills", "name": "技能", "status": "warn", "summary": "0 次执行"},
            {"id": "tools", "name": "工具", "status": "error", "issues": ["探针超时", "x"]},
            {"id": "wf", "name": "工作流", "status": "never"},
            {"id": "sched", "name": "调度", "status": "off"},
            {"id": "mem", "name": "记忆", "status": "ok"},
        ]}
        rows = unit_candidates(payload)
        assert [r["key"] for r in rows] == ["unit.skills", "unit.tools"]
        assert rows[0]["severity"] == "warning"
        assert rows[1]["severity"] == "critical"
        assert rows[1]["detail"].startswith("探针超时")

    def test_空与缺字段安全(self):
        assert unit_candidates(None) == []
        assert unit_candidates({"units": [{"id": "x"}]}) == []


class TestAuditCandidate:
    def test_通过时不报(self):
        assert audit_candidate({"ok": True}) == []
        assert audit_candidate(None) == []

    def test_不一致时报_critical(self):
        rows = audit_candidate({"ok": False, "detail": "seq 12 处哈希不符"})
        assert rows and rows[0]["key"] == "audit.chain"
        assert rows[0]["severity"] == "critical"
        assert "哈希不符" in rows[0]["detail"]


class TestCollectOnce:
    def test_一轮上报与下一轮恢复(self, store):
        first = collect_once(store, sources={
            "health": lambda: _point(l3_llm_tool=(0.3, "err 40%")),
            "units": lambda: {"units": [{"id": "skills", "name": "技能", "status": "warn"}]},
            "audit": lambda: {"ok": True},
        })
        assert first["raised"] == 2 and first["resolved"] == 0
        keys = {a["key"]: a["state"] for a in store.list()}
        assert keys == {"health.l3_llm_tool": STATE_FIRING, "unit.skills": STATE_FIRING}

        second = collect_once(store, sources={
            "health": lambda: _point(l3_llm_tool=(0.99, "ok")),
            "units": lambda: {"units": []},
            "audit": lambda: {"ok": True},
        })
        assert second["resolved"] == 2
        assert all(a["state"] == STATE_RESOLVED for a in store.list())

    def test_数据源不可用时不误判恢复(self, store):
        collect_once(store, sources={
            "health": lambda: _point(l3_llm_tool=(0.3, "err")),
            "units": lambda: {"units": []},
            "audit": lambda: {"ok": True},
        })
        # 健康历史读不到（None）⇒ 该源本轮跳过：旧告警保持 firing，不被标恢复
        stats = collect_once(store, sources={
            "health": lambda: None,
            "units": lambda: {"units": []},
            "audit": lambda: {"ok": True},
        })
        assert stats["checked"]["health"] is False
        assert stats["resolved"] == 0
        assert stats["kept"] == ["health.l3_llm_tool"]
        assert store.list()[0]["state"] == STATE_FIRING

    def test_单源抛异常不影响其它源(self, store):
        def boom():
            raise RuntimeError("probe down")

        stats = collect_once(store, sources={
            "health": boom,
            "units": lambda: {"units": [{"id": "skills", "name": "技能", "status": "warn"}]},
            "audit": lambda: {"ok": True},
        })
        assert stats["checked"]["health"] is False
        assert stats["raised"] == 1
        assert store.list()[0]["key"] == "unit.skills"

    def test_默认台账可注入(self, store, monkeypatch):
        monkeypatch.setattr(collector, "get_store", lambda: store)
        collect_once(sources={"health": lambda: _point(l4_business=(0.1, "5xx")),
                              "units": lambda: {"units": []},
                              "audit": lambda: {"ok": True}})
        assert store.count() == 1


class TestWarmup:
    """冷启动窗口：重启后首轮不采单位看板（避免"首次探测超时"变成 3 条 critical）。

    【现场依据】2026-11-11 04:51 部署重启后 2 秒内出现 3 条 critical「探测超时」
    （unit.skills.usage / unit.tools.registry / unit.ingestion.drafts），21 秒后自愈。
    """

    def test_窗口内跳过单位看板(self, store, monkeypatch):
        import time as _time
        monkeypatch.setattr(collector, "_started_at", _time.monotonic())
        seen = {"units": 0}
        monkeypatch.setattr(collector, "read_health_point", lambda: {"probe_details": {}})
        monkeypatch.setattr(collector, "read_audit_verification", lambda: {"ok": True})
        monkeypatch.setattr(collector, "read_units",
                            lambda: seen.__setitem__("units", seen["units"] + 1) or {"units": []})
        # 2026-11-11：新增的调度/CI 事件源读**本机运行时文件**，判据必须与机器状态无关
        monkeypatch.setattr(collector, "read_scheduler_failures", lambda: [])
        monkeypatch.setattr(collector, "read_ci_status", lambda: None)

        stats = collect_once(store)          # sources=None ⇒ 真实采集路径

        assert stats["warmup"] is True
        assert seen["units"] == 0, "冷启动窗口内不该读单位看板"
        assert stats["checked"].get("units") in (None, False)

    def test_窗口结束后恢复采集(self, store, monkeypatch):
        import time as _time
        monkeypatch.setattr(collector, "_started_at", _time.monotonic() - collector.WARMUP_SEC - 1)
        monkeypatch.setattr(collector, "read_health_point", lambda: {"probe_details": {}})
        monkeypatch.setattr(collector, "read_audit_verification", lambda: {"ok": True})
        monkeypatch.setattr(collector, "read_units",
                            lambda: {"units": [{"id": "skills", "name": "技能", "status": "warn"}]})
        # 同上：调度/CI 两个新源与"这一台机器"无关，钉成空以保持断言确定
        monkeypatch.setattr(collector, "read_scheduler_failures", lambda: [])
        monkeypatch.setattr(collector, "read_ci_status", lambda: None)

        stats = collect_once(store)

        assert stats["warmup"] is False
        assert stats["raised"] == 1
        assert store.list()[0]["key"] == "unit.skills"

    def test_注入_sources_时不受窗口影响(self, store, monkeypatch):
        """单测注入的 sources 代表"稳态"，不该被启动时刻影响。"""
        import time as _time
        monkeypatch.setattr(collector, "_started_at", _time.monotonic())
        stats = collect_once(store, sources={
            "health": lambda: {"probe_details": {}},
            "units": lambda: {"units": [{"id": "tools", "name": "工具", "status": "warn"}]},
            "audit": lambda: {"ok": True},
        })
        assert stats["warmup"] is False
        assert stats["raised"] == 1


class TestConfig:
    def test_阈值与窗口是模块常量(self):
        assert HEALTH_DEGRADE_SCORE == 0.8
        assert HEALTH_CRITICAL_SCORE == 0.5
        assert AUDIT_TAIL_WINDOW > 0

    def test_开关默认开_可关(self, monkeypatch):
        monkeypatch.delenv(collector.ENV_FLAG, raising=False)
        assert collector.enabled() is True
        monkeypatch.setenv(collector.ENV_FLAG, "0")
        assert collector.enabled() is False
        assert collector.start_collector() is None, "关掉时不该起线程"
