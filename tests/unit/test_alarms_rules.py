# -*- coding: utf-8 -*-
"""告警规则求值（agent/alarms/rules.py）测试。

钉住四件事：
1. 规则文件 16 条全部读进来（不是"部分规则静默失效"）；
2. **能算的算**：给合成指标，内存/5xx 比例规则会 firing 并进候选；
3. **算不了的如实标**：指标不存在 ⇒ no_data（不是 ok、不是 0），且不产生候选；
4. `for` 持续时长语义：条件刚成立是 pending，持续够久才 firing（与 Prometheus 一致）。
"""
from __future__ import annotations

import pytest

from agent.alarms import rules as rule_engine
from agent.alarms.samples import SampleStore, _series_key


@pytest.fixture(autouse=True)
def _fresh_states():
    rule_engine.reset_states()
    yield
    rule_engine.reset_states()


def key(name: str, **labels) -> tuple:
    return _series_key(name, {k: str(v) for k, v in labels.items()})


def _rule(alert="TestRule", expr="x > 1", for_="0m", severity="warning"):
    return {"alert": alert, "expr": expr, "for": for_, "severity": severity,
            "labels": {}, "annotations": {"summary": alert}, "group": "t"}


class TestLoadRules:
    def test_十六条规则全读进来(self):
        rows = rule_engine.load_rules()
        assert len(rows) == 16
        names = {r["alert"] for r in rows}
        assert {"YunshuHighErrorRate", "YunshuHighMemoryUsage", "YunshuServiceDown"} <= names
        for row in rows:
            assert row["expr"], row
            assert row["severity"] in ("critical", "warning", "info"), row

    def test_文件坏掉时返回空而不是抛(self, tmp_path):
        broken = tmp_path / "alerts.yml"
        broken.write_text("groups: [", encoding="utf-8")
        assert rule_engine.load_rules(broken) == []


class TestEvaluate:
    def test_能算的规则会_firing_并进候选(self):
        store = SampleStore()
        store.record({key("yunshu_memory_usage_percent"): 92.0}, now=100.0)
        states = rule_engine.evaluate_all(
            [_rule("MemHigh", "yunshu_memory_usage_percent > 85", severity="critical")], store,
            now=100.0)
        assert states[0].state == "firing"
        assert states[0].value == pytest.approx(92.0)
        rows = rule_engine.candidates(states)
        assert rows and rows[0]["key"] == "rule.MemHigh"
        assert rows[0]["severity"] == "critical"
        assert "MemHigh" in rows[0]["title"]

    def test_指标不存在如实标_no_data_且不产生候选(self):
        store = SampleStore()
        store.record({key("other"): 1.0}, now=100.0)
        states = rule_engine.evaluate_all(
            [_rule("Ghost", "yunshu_tool_call_total > 1")], store, now=100.0)
        assert states[0].state == "no_data"
        assert "yunshu_tool_call_total" in states[0].reason
        assert rule_engine.candidates(states) == []

    def test_for_未满为_pending_满后_firing(self):
        store = SampleStore()
        store.record({key("m"): 99.0}, now=100.0)
        rows = [_rule("Slow", "m > 90", for_="5m")]
        first = rule_engine.evaluate_all(rows, store, now=100.0)
        assert first[0].state == "pending", "条件刚成立，未满 5m"
        assert rule_engine.candidates(first) == []
        # 同一个 store 再求值一次（时间前进 5 分钟）⇒ firing
        store.record({key("m"): 99.0}, now=400.0)
        second = rule_engine.evaluate_all(rows, store, now=400.0)
        assert second[0].state == "firing"
        assert rule_engine.candidates(second)

    def test_条件恢复后清零(self):
        store = SampleStore()
        store.record({key("m"): 99.0}, now=100.0)
        rows = [_rule("Slow2", "m > 90", for_="5m")]
        rule_engine.evaluate_all(rows, store, now=100.0)
        store.record({key("m"): 10.0}, now=200.0)
        recovered = rule_engine.evaluate_all(rows, store, now=200.0)
        assert recovered[0].state == "ok"
        assert recovered[0].since == 0.0

    def test_不可求值表达式标_unsupported(self):
        store = SampleStore()
        store.record({key("m"): 1.0}, now=100.0)
        states = rule_engine.evaluate_all([_rule("Bad", "predict_linear(m[1h], 60) > 1")],
                                          store, now=100.0)
        assert states[0].state == "unsupported"


class TestCoverage:
    def test_覆盖率读数区分能算与算不了(self):
        store = SampleStore()
        store.record({key("m"): 99.0}, now=100.0)
        rule_engine.evaluate_all([
            _rule("A", "m > 90"),
            _rule("B", "missing_metric > 1"),
            _rule("C", "predict_linear(m[1h], 1) > 1"),
        ], store, now=100.0)
        cov = rule_engine.coverage()
        assert cov["rules_total"] == 3
        assert cov["rules_evaluable"] == 1
        assert cov["rules_blocked"] == 2
        blocked = {row["alert"] for row in cov["blocked"]}
        assert blocked == {"B", "C"}
        assert all(row["reason"] for row in cov["blocked"])

    def test_真实规则集里_可求值条数不超过十六条(self):
        store = SampleStore()
        store.record({}, now=100.0)
        rule_engine.evaluate_all(store=store, now=100.0)
        cov = rule_engine.coverage()
        assert cov["rules_total"] == 16
        assert 0 <= cov["rules_evaluable"] <= 16
        assert cov["rules_evaluable"] + cov["rules_blocked"] == 16
