"""告警运行态合并（2026-11-11，条目 alerts-runtime-state）。

背景：/api/observability/alerts 此前只回**规则定义**（alert/expr/for/labels），没有运行态，
健康页「状态 / 触发时间」两列只能写「—（端点未返回）」。

口径（写进 _merge_alert_runtime_state 的注释）：
  ① 只**增字段**（state / activeAt / value / fire_count），不删既有键；
  ② 评估器没跑过、或该规则没被评估 ⇒ **不加字段**（前端继续显示占位，绝不编「未触发」）；
  ③ activeAt 取 started_at（firing 起点）或 pending_since（进入 pending 的时刻）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.server_routes import routes_logging as rl


@pytest.fixture(autouse=True)
def _no_rule_engine_states():
    """隔离报警器的**规则引擎**运行态（2026-11-11 CI 实测）。

    `_merge_alert_runtime_state` 有**两个**运行态来源：alert_evaluator（live）与
    报警器本进程规则引擎（`agent/alarms/rules._states`，local）。本文件测的是前者
    「拿不到时不要编状态」，但 `_states` 是**模块级全局**：同分片里只要先跑过
    `test_alarms_rules` 之类的用例，local 就非空 ⇒ `runtime_state` 必然是 True，
    用例变红（2026-11-11 两轮 CI 都是这条：`assert True is False`，且它在上一轮
    §11.89 的 master 上就已经红）。这里把规则引擎状态清空并还原，判据不变：
    「两个来源都没有运行态时，规则表原样返回」。
    """
    from agent.alarms import rules as rule_engine
    rule_engine.reset_states()
    yield
    rule_engine.reset_states()


def _rules():
    return [
        {"name": "g1", "rules": [
            {"alert": "cpu_high"},
            {"alert": "never_evaluated"},
        ]},
    ]


def _fake_evaluator(rows):
    return SimpleNamespace(get_alerts=lambda: rows)


def test_merge_adds_state_and_active_at(monkeypatch):
    import agent.monitoring.alert_evaluator as ae
    monkeypatch.setattr(ae, "get_alert_evaluator", lambda: _fake_evaluator([
        {"name": "cpu_high", "state": "firing", "value": 93.5, "fire_count": 2,
         "started_at": 1700000000.0},
    ]))
    groups = _rules()
    assert rl._merge_alert_runtime_state(groups) is True
    rule = groups[0]["rules"][0]
    assert rule["state"] == "firing" and rule["fire_count"] == 2 and rule["value"] == 93.5
    assert rule["activeAt"].startswith("2023-11-14T22:13:20"), "started_at 要转成 ISO（UTC）"
    # 未被评估的规则**不加字段**（前端显示占位，不编「未触发」）
    assert "state" not in groups[0]["rules"][1]


def test_pending_since_used_when_not_started(monkeypatch):
    import agent.monitoring.alert_evaluator as ae
    monkeypatch.setattr(ae, "get_alert_evaluator", lambda: _fake_evaluator([
        {"name": "cpu_high", "state": "pending", "value": 81.0, "fire_count": 0,
         "started_at": None, "pending_since": 1700000100.0},
    ]))
    groups = _rules()
    rl._merge_alert_runtime_state(groups)
    assert groups[0]["rules"][0]["state"] == "pending"
    assert groups[0]["rules"][0]["activeAt"].startswith("2023-11-14T22:15:00")


def test_evaluator_unavailable_keeps_rules_untouched(monkeypatch):
    import agent.monitoring.alert_evaluator as ae
    def boom():
        raise RuntimeError("evaluator not started")
    monkeypatch.setattr(ae, "get_alert_evaluator", boom)
    groups = _rules()
    assert rl._merge_alert_runtime_state(groups) is False
    assert groups == _rules(), "运行态不可用时规则表必须原样返回（不塞假状态）"


def test_empty_evaluator_reports_false(monkeypatch):
    import agent.monitoring.alert_evaluator as ae
    monkeypatch.setattr(ae, "get_alert_evaluator", lambda: _fake_evaluator([]))
    groups = _rules()
    assert rl._merge_alert_runtime_state(groups) is False
