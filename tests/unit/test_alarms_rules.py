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
        # 2026-11-13：up{} 类规则单列 external（方案 C 永不评估），不再算进 blocked
        assert (cov["rules_evaluable"] + cov["rules_blocked"]
                + cov["rules_external"]) == 16

class Test延迟规则排除非交互端点:
    """2026-11-11：P99/P95 必须排除"耗时 ≠ 交互延迟"的端点（Owner 授权「分析后决定」）。

    【现场依据】实测本部署：modules.topology 一次 3.2s（健康仪表盘 60s 自动刷新一直在踩）、
    health.probe_trend 1.4s、SSE 评估流一次 10s+（histogram 记的是连接存活时长）。不排除的话，
    "打开监控页 / 开着评估流"就必然 P99>3s —— 那不是用户请求慢，是扫描量与长连接被算进了延迟。

    判据（三条互为反证）：
      ① 慢的只有被排除的端点 ⇒ **ok**（规则不该响）；
      ② 无排除时同样的数据 ⇒ **firing**（证明是"排除"起的作用，不是数据本身不触发）；
      ③ 普通交互端点真的慢（约 5s）⇒ 仍然 **firing**（排除集不许把该盯的也吞掉）。
    """

    _BUCKET = "yunshu_http_request_duration_seconds_bucket"
    _CHAT = "plugins.chat.api_chat"
    _STREAM = "api_skills_mgmt_assess_stream"
    _TOPO = "modules.topology"

    def _snap(self, endpoint: str, counts: dict) -> dict:
        return {key(self._BUCKET, le=le, endpoint=endpoint, method="GET", status="200"): value
                for le, value in counts.items()}

    def _store(self, *, stream_slow: bool = False, topo_slow: bool = False,
               chat_slow: bool = False):
        """两个采样点（100s / 400s）：交互端点 +20 次；被排除端点各来几发慢请求。

        【为什么两端都要写**全量桶**】直方图的 bucket 是**累计计数器**：某个 le 的序列在窗口
        起点缺席时，求值器会按 0 起算（把历史累计当成本轮增量），数字会虚高。现实里
        prometheus_client 从第一次观测起就会导出所有 le，所以夹具也必须两端同形 —— 2026-11-11
        第一版夹具漏了 le="5.0" 的起点，导致"慢请求 20 次"被算成 120 次。
        """
        buckets = ("0.05", "0.1", "5.0", "10.0", "+Inf")
        chat_before = {le: 100 for le in buckets}
        if chat_slow:
            chat_after = {"0.05": 100, "0.1": 100, "5.0": 120, "10.0": 120, "+Inf": 120}
        else:
            chat_after = {le: 120 for le in buckets}
        stream_before = {le: 0 for le in ("10.0", "+Inf")}
        stream_after = {"10.0": 0, "+Inf": 5 if stream_slow else 0}
        topo_before = {le: 0 for le in ("5.0", "+Inf")}
        topo_after = {"5.0": 0, "+Inf": 4 if topo_slow else 0}
        store = SampleStore()
        store.record({**self._snap(self._CHAT, chat_before),
                      **self._snap(self._STREAM, stream_before),
                      **self._snap(self._TOPO, topo_before)}, now=100.0)
        store.record({**self._snap(self._CHAT, chat_after),
                      **self._snap(self._STREAM, stream_after),
                      **self._snap(self._TOPO, topo_after)}, now=400.0)
        return store

    @staticmethod
    def _real_rule(alert: str) -> dict:
        return next(r for r in rule_engine.load_rules() if r["alert"] == alert)

    def _eval_twice(self, rule: dict, store) -> str:
        """跑两轮（第二轮跨过 for 时长）取最终状态 —— for 是"连续成立够久才 firing"。"""
        rule_engine.reset_states()
        rule_engine.evaluate_all([rule], store, now=400.0)
        states = rule_engine.evaluate_all([rule], store, now=1001.0)
        return states[0].state

    def test_流式端点慢时_P99_不告警(self):
        state = self._eval_twice(self._real_rule("YunshuHighLatencyP99"),
                                 self._store(stream_slow=True))
        assert state == "ok", "SSE 的连接时长不是响应延迟，不能把 P99 抬到告警"

    def test_拓扑重建慢时_P95_不告警(self):
        state = self._eval_twice(self._real_rule("YunshuHighLatencyP95"),
                                 self._store(topo_slow=True))
        assert state == "ok", "按需重扫描端点的耗时是扫描量，不能算进交互延迟"

    def test_不做排除时同样的数据会_firing(self):
        """反证：同一份数据，换成不带排除的表达式就必须 firing（否则前两条测的是"数据不触发"）。"""
        raw = ("histogram_quantile(0.99, "
               "sum(rate(yunshu_http_request_duration_seconds_bucket[5m])) by (le)) > 3")
        state = self._eval_twice(_rule("NoFilter", raw), self._store(stream_slow=True))
        assert state == "firing", "反证失败：不排除时流式端点应把 P99 抬高到阈值以上"

    def test_普通交互端点慢仍然告警(self):
        state = self._eval_twice(self._real_rule("YunshuHighLatencyP99"),
                                 self._store(chat_slow=True))
        assert state == "firing", "排除集不许把该盯的交互端点也吞掉"