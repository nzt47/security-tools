# -*- coding: utf-8 -*-
"""报警器「全域覆盖」三件事的行为测试（2026-11-11）。

钉住的口径：
1. **补埋点**：只对**已注册**的指标族自增（不新建 ⇒ 不会让 app_server 的 Counter 撞名起不来）；
   业务指标层（tool_call / hallucination / eval_score）通过采样环的投影被看到；
2. **上 Prometheus**：未配置 = 完全不参与；配置了但拉不到 ⇒ 规则源本轮"不可用"
   （保留旧告警，绝不判"已恢复"）；
3. **事件源扩容**：计划任务失败读执行历史（同任务多次失败合并）、CI 只读同步文件
   （缺失/过期 = 不可用）、每个工作流只报最新一次、cancelled 不算失败。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agent.alarms import collector, prometheus_source, samples
from agent.alarms.samples import (
    BUSINESS_ALERT_FAMILIES,
    _parse_exposition,
    _series_key,
    snapshot_all,
    snapshot_business_metrics,
)
from agent.monitoring import alarm_metrics


# ════════════════════════════════════════════════════════════
#  ① 埋点
# ════════════════════════════════════════════════════════════

class _FakeChild:
    def __init__(self, log):
        self._log = log

    def inc(self):
        self._log.append("inc")


class _FakeFamily:
    def __init__(self):
        self.calls = []

    def labels(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeChild([kwargs])


class TestAlarmMetrics:
    def test_族不存在时静默跳过_不新建(self, monkeypatch):
        monkeypatch.setattr(alarm_metrics, "_collector", lambda name: None)
        # 三种埋点都不得抛异常（族缺失 = 本部署没装 prometheus，不该影响主链路）
        alarm_metrics.record_llm_call("deepseek", "deepseek-flash", True)
        alarm_metrics.record_tool_call("read_file", True)
        alarm_metrics.record_security_block("tool_gate.deny")

    def test_llm_调用按成功失败分标签(self, monkeypatch):
        family = _FakeFamily()
        monkeypatch.setattr(alarm_metrics, "_collector", lambda name: family)
        alarm_metrics.record_llm_call("deepseek", "flash", True)
        alarm_metrics.record_llm_call("deepseek", "flash", False)
        assert family.calls[0] == {"provider": "deepseek", "model": "flash", "status": "success"}
        assert family.calls[1]["status"] == "error"

    def test_安全拦截按等级与分类打标签(self, monkeypatch):
        family = _FakeFamily()
        monkeypatch.setattr(alarm_metrics, "_collector", lambda name: family)
        alarm_metrics.record_security_block("guardrails.ssrf", "critical", "network")
        assert family.calls[0] == {"rule": "guardrails.ssrf", "level": "critical",
                                   "category": "network"}

    def test_工具埋点走业务指标层(self, monkeypatch):
        seen = {}

        def fake_record(tool_name, category, success, duration=None):
            seen.update(tool_name=tool_name, category=category, success=success, duration=duration)

        import agent.monitoring.business_metrics as bm
        monkeypatch.setattr(bm, "record_tool_call", fake_record)
        alarm_metrics.record_tool_call("search_memory", True, duration=0.5)
        assert seen == {"tool_name": "search_memory", "category": "", "success": True,
                        "duration": 0.5}

    def test_collector_取不到族时返回_None(self, monkeypatch):
        # 真实 REGISTRY 里没有这个自定义名字 ⇒ None（绝不新建）
        assert alarm_metrics._collector("yunshu_no_such_family_total") is None


# ════════════════════════════════════════════════════════════
#  业务指标层 → 采样环的投影
# ════════════════════════════════════════════════════════════

_TEXT = "\n".join([
    "# HELP yunshu_tool_call_total 工具调用总次数",
    "# TYPE yunshu_tool_call_total counter",
    'yunshu_tool_call_total{tool_category="file",success="True",tool_name="read_file"} 7',
    'yunshu_tool_call_total{tool_category="file",success="False",tool_name="read_file"} 1',
    'yunshu_skill_eval_score{skill_id="s1",task_success="True",quantile="0.5"} 0.82',
    'yunshu_skill_eval_score_sum{skill_id="s1",task_success="True"} 0.82',
    'yunshu_interaction_total{a="b"} 3',  # 不在白名单的族：不并
])


class TestBusinessBridge:
    def test_解析导出文本(self):
        values = _parse_exposition(_TEXT)
        key = _series_key("yunshu_tool_call_total",
                          {"tool_category": "file", "success": "True", "tool_name": "read_file"})
        assert values[key] == 7
        assert _series_key("yunshu_skill_eval_score",
                           {"skill_id": "s1", "task_success": "True", "quantile": "0.5"}) in values

    def test_只并规则引用的族(self):
        assert "yunshu_tool_call_total" in BUSINESS_ALERT_FAMILIES
        assert "yunshu_skill_hallucination_total" in BUSINESS_ALERT_FAMILIES
        assert "yunshu_skill_eval_score" in BUSINESS_ALERT_FAMILIES
        assert "yunshu_interaction_total" not in BUSINESS_ALERT_FAMILIES

    def test_业务层不可用返回空而不是抛(self, monkeypatch):
        import agent.monitoring.business_metrics as bm

        def boom():
            raise RuntimeError("collector down")

        monkeypatch.setattr(bm, "get_business_metrics_collector", boom)
        assert snapshot_business_metrics() == {}

    def test_技能评估与幻觉发射点能被采样环看到(self, monkeypatch):
        """端到端（进程内）：`emit_eval_score_metric` → 业务指标层 → 采样投影。

        这两族（`yunshu_skill_hallucination_total` / `yunshu_skill_eval_score`）本来就有
        真实写入点（技能执行后的评估回执），缺的只是"报警器能不能看见"。这里不 mock 任何
        一层：真调发射函数，再真读采样投影 —— 看见即说明桥接生效、规则不再永远 no_data。
        """
        from agent.skills_mgmt import observability as obs

        # 【为什么要把写出侧钉到同一个收集器实例】obs 在 **import 期**缓存了
        #   `_metrics = get_business_metrics_collector()`；同分片里若有别的用例换了/
        #   换了可用性标记（或收集器被重置），写出侧与 `snapshot_business_metrics()`
        #   读到的就不是一个对象 —— 2026-11-11 CI shard3 实测：集合里有几十个
        #   yunshu_skill_* 族，唯独我们这两族没有（写进了旧实例）。
        #   生产里本来只有一个全局单例，这里显式钉住，既复现真实关系又不依赖测试顺序。
        import agent.monitoring.business_metrics as bm
        collector = bm.get_business_metrics_collector()
        monkeypatch.setattr(obs, "_metrics", collector, raising=False)
        monkeypatch.setattr(obs, "_METRICS_AVAILABLE", True, raising=False)

        from agent.skills_mgmt.observability import emit_eval_score_metric

        emit_eval_score_metric("test-alarm-skill", {
            "task_success": True,
            "instruction_followed": True,
            "hallucination_detected": True,
            "score": 0.42,
        })
        values = snapshot_business_metrics()
        names = {key[0] for key in values}
        assert "yunshu_skill_hallucination_total" in names
        assert "yunshu_skill_eval_score" in names
        # 规则要的是 {quantile="0.5"} 这一条（业务指标层按分位数投影导出）
        quantile_keys = [k for k in values if k[0] == "yunshu_skill_eval_score"
                         and dict(k[1]).get("quantile") == "0.5"]
        assert quantile_keys, "缺少 quantile=0.5 投影 ⇒ SkillEvalScoreLow 规则取不到值"

    def test_采样合并注册表优先(self, monkeypatch):
        monkeypatch.setattr(samples, "snapshot_registry",
                            lambda: {_series_key("a_total", {}): 1.0})
        monkeypatch.setattr(samples, "snapshot_business_metrics",
                            lambda: {_series_key("a_total", {}): 99.0,
                                     _series_key("b_total", {}): 2.0})
        monkeypatch.setattr(prometheus_source, "configured", lambda: False)
        merged = snapshot_all()
        assert merged[_series_key("a_total", {})] == 1.0, "注册表（真实值）优先"
        assert merged[_series_key("b_total", {})] == 2.0


# ════════════════════════════════════════════════════════════
#  ② 外部 Prometheus（可选）
# ════════════════════════════════════════════════════════════

class _FakeResponse:
    def __init__(self, body: str):
        self._body = body

    def read(self):
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class TestPrometheusSource:
    def setup_method(self):
        prometheus_source.reset_state()

    def teardown_method(self):
        prometheus_source.reset_state()

    def test_未配置时完全不参与(self, monkeypatch):
        monkeypatch.delenv(prometheus_source.ENV_URL, raising=False)
        assert prometheus_source.configured() is False
        assert prometheus_source.fetch_up() is None
        assert prometheus_source.last_ok() is None

    def test_拉取_up_保留标签(self, monkeypatch):
        monkeypatch.setenv(prometheus_source.ENV_URL, "http://127.0.0.1:9090")
        body = json.dumps({"status": "success", "data": {"result": [
            {"metric": {"__name__": "up", "job": "yunshu", "instance": "127.0.0.1:5678"},
             "value": [1700000000, "1"]},
            {"metric": {"__name__": "up", "job": "prometheus", "instance": "127.0.0.1:9090"},
             "value": [1700000000, "0"]},
        ]}})
        monkeypatch.setattr(prometheus_source.urllib.request, "urlopen",
                            lambda url, timeout=None: _FakeResponse(body))
        got = prometheus_source.fetch_up()
        assert got[_series_key("up", {"job": "yunshu", "instance": "127.0.0.1:5678"})] == 1.0
        assert got[_series_key("up", {"job": "prometheus", "instance": "127.0.0.1:9090"})] == 0.0
        assert prometheus_source.last_ok() is True

    def test_拉不到记不可用(self, monkeypatch):
        monkeypatch.setenv(prometheus_source.ENV_URL, "http://127.0.0.1:9090")

        def boom(url, timeout=None):
            raise OSError("connection refused")

        monkeypatch.setattr(prometheus_source.urllib.request, "urlopen", boom)
        assert prometheus_source.fetch_up() is None
        assert prometheus_source.last_ok() is False

    def test_配置了但不可用时规则源整体保留(self, monkeypatch):
        """keep 语义：规则源不可用 ⇒ read_rule_states 返回 None（采集层保留 rule.* 旧告警）。"""
        monkeypatch.setenv(prometheus_source.ENV_URL, "http://127.0.0.1:9090")
        monkeypatch.setattr(prometheus_source, "last_ok", lambda: False)
        monkeypatch.setattr(samples, "get_sample_store",
                            lambda: type("S", (), {"record": lambda self: 0})())
        assert collector.read_rule_states() is None

    def test_未配置时规则源照常求值(self, monkeypatch):
        monkeypatch.delenv(prometheus_source.ENV_URL, raising=False)
        monkeypatch.setattr(samples, "get_sample_store",
                            lambda: type("S", (), {"record": lambda self: 0})())

        class _State:
            def to_dict(self):
                return {"alert": "X", "state": "ok"}

        import agent.alarms.rules as rules_mod
        monkeypatch.setattr(rules_mod, "evaluate_all", lambda: [_State()])
        assert collector.read_rule_states() == [{"alert": "X", "state": "ok"}]


# ════════════════════════════════════════════════════════════
#  ③ 事件源扩容：计划任务 / CI
# ════════════════════════════════════════════════════════════

class TestSchedulerSource:
    def test_同任务多次失败合并成一条(self):
        rows = collector.scheduler_candidates([
            {"name": "周报", "task_id": "t1", "status": "failed", "type": "python_func",
             "error": "boom"},
            {"name": "周报", "task_id": "t2", "status": "failed", "type": "python_func",
             "error": "boom again"},
        ])
        assert len(rows) == 1
        row = rows[0]
        assert row["key"] == "scheduler.周报"
        assert row["severity"] == "warning"
        assert "失败 2 次" in row["detail"]
        assert "boom again" in row["detail"]

    def test_没有失败就不报(self):
        assert collector.scheduler_candidates([]) == []
        assert collector.scheduler_candidates(None) == []

    def test_只取窗口内失败(self, monkeypatch):
        recent = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
        old = (datetime.now() - timedelta(hours=10)).isoformat(timespec="seconds")
        monkeypatch.setattr(collector, "SCHEDULER_FAILURE_WINDOW_SEC", 3600)

        class _Sched:
            def get_history(self, limit=100):
                return [
                    {"name": "新失败", "status": "failed", "start_time": recent},
                    {"name": "旧失败", "status": "failed", "start_time": old},
                    {"name": "成功", "status": "success", "start_time": recent},
                ]

        import agent.task_scheduler as ts
        monkeypatch.setattr(ts, "get_scheduler", lambda: _Sched())
        got = collector.read_scheduler_failures()
        assert [r["name"] for r in got] == ["新失败"]

    def test_调度器不可用返回_None(self, monkeypatch):
        import agent.task_scheduler as ts

        def boom():
            raise RuntimeError("scheduler down")

        monkeypatch.setattr(ts, "get_scheduler", boom)
        assert collector.read_scheduler_failures() is None


class TestCiSource:
    def test_每个工作流只报最新一次(self):
        rows = collector.ci_candidates({"runs": [
            {"workflow": "测试", "conclusion": "failure", "created_at": "2026-11-11T10:00:00Z"},
            {"workflow": "测试", "conclusion": "success", "created_at": "2026-11-11T12:00:00Z"},
            {"workflow": "部署", "conclusion": "failure", "created_at": "2026-11-11T09:00:00Z",
             "branch": "master", "url": "https://x/1"},
        ]})
        assert [r["key"] for r in rows] == ["ci.部署"]
        assert "master" in rows[0]["detail"]

    def test_取消不算失败(self):
        rows = collector.ci_candidates({"runs": [
            {"workflow": "测试", "conclusion": "cancelled", "created_at": "2026-11-11T10:00:00Z"},
            {"workflow": "测试", "conclusion": "timed_out", "created_at": "2026-11-11T11:00:00Z"},
        ]})
        assert [r["key"] for r in rows] == ["ci.测试"]

    def test_文件缺失按不可用(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(collector, "CI_STATUS_FILE", tmp_path / "nope.json")
        assert collector.read_ci_status() is None

    def test_文件过期按不可用(self, monkeypatch, tmp_path: Path):
        path = tmp_path / "status.json"
        stale = (datetime.now() - timedelta(hours=5)).astimezone().isoformat(timespec="seconds")
        path.write_text(json.dumps({"synced_at": stale, "runs": []}), encoding="utf-8")
        monkeypatch.setattr(collector, "CI_STATUS_FILE", path)
        monkeypatch.setattr(collector, "CI_MAX_AGE_SEC", 3600)
        assert collector.read_ci_status() is None

    def test_新鲜文件可读(self, monkeypatch, tmp_path: Path):
        path = tmp_path / "status.json"
        fresh = datetime.now().astimezone().isoformat(timespec="seconds")
        path.write_text(json.dumps({"synced_at": fresh, "runs": []}), encoding="utf-8")
        monkeypatch.setattr(collector, "CI_STATUS_FILE", path)
        got = collector.read_ci_status()
        assert got is not None and got["synced_at"] == fresh
