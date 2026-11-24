"""E0-4 工具治理小件的契约测试：重复调用提醒（`_repeat`）。

覆盖：开关/阈值/窗口解析、重复计数与提醒点（默认 3/5/8）、窗口过期重置、不同参数/工具分开计、
排除名单、结果注记、非 dict 不处理、关闭即恢复旧形状、状态形状。

【时间上下文（每步时钟）本次未做】把时钟塞进每个工具结果会被「结果整字典相等」的既有契约
断言拦住（实测 7 处 unit 失败）⇒ 已登记为余项，等选好 chat/orchestrator 的消息注入点再做。
"""
from __future__ import annotations

import pytest

from agent.tools import governance

_ENVS = (governance.REPEAT_ENABLED_ENV, governance.REPEAT_THRESHOLDS_ENV,
         governance.REPEAT_WINDOW_ENV)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for key in _ENVS:
        monkeypatch.delenv(key, raising=False)
    governance.reset_state()
    yield
    governance.reset_state()


def test_默认开启():
    assert governance.repeat_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "off", "no", "disable"])
def test_关闭写法(monkeypatch, raw):
    monkeypatch.setenv(governance.REPEAT_ENABLED_ENV, raw)
    assert governance.repeat_enabled() is False


def test_阈值解析(monkeypatch):
    assert governance.thresholds() == (3, 5, 8)
    monkeypatch.setenv(governance.REPEAT_THRESHOLDS_ENV, "2, 4,4,9")
    assert governance.thresholds() == (2, 4, 9)
    monkeypatch.setenv(governance.REPEAT_THRESHOLDS_ENV, "abc,1")
    assert governance.thresholds() == (3, 5, 8)


def test_窗口解析与钳制(monkeypatch):
    assert governance.window_seconds() == 300.0
    monkeypatch.setenv(governance.REPEAT_WINDOW_ENV, "1")
    assert governance.window_seconds() == 10.0
    monkeypatch.setenv(governance.REPEAT_WINDOW_ENV, "99999")
    assert governance.window_seconds() == 3600.0
    monkeypatch.setenv(governance.REPEAT_WINDOW_ENV, "oops")
    assert governance.window_seconds() == 300.0


def test_第三次才提醒_第五次再提醒():
    params = {"path": "a.txt"}
    assert governance.note_call("read_file", params) is None
    assert governance.note_call("read_file", params) is None
    third = governance.note_call("read_file", params)
    assert third is not None and third["count"] == 3
    assert "先读上一次结果" in third["hint"]
    assert governance.note_call("read_file", params) is None
    fifth = governance.note_call("read_file", params)
    assert fifth is not None and fifth["count"] == 5


def test_不同参数分开计数():
    for _ in range(2):
        governance.note_call("read_file", {"path": "a"})
    assert governance.note_call("read_file", {"path": "b"}) is None


def test_不同工具分开计数():
    for _ in range(2):
        governance.note_call("read_file", {"path": "a"})
    assert governance.note_call("list_directory", {"path": "a"}) is None


def test_窗口过期后重新计数():
    params = {"path": "a"}
    base = 1_000_000.0
    for index in range(2):
        governance.note_call("read_file", params, now=base + index)
    # 窗口外的一次调用 ⇒ 计数从 1 重新开始（不会在第 3 次提醒）
    assert governance.note_call("read_file", params, now=base + 10_000) is None


def test_排除名单不计数():
    params = {"content": "x"}
    for _ in range(3):
        assert governance.note_call("todo_write", params) is None


def test_关闭后不提醒(monkeypatch):
    monkeypatch.setenv(governance.REPEAT_ENABLED_ENV, "0")
    params = {"path": "a"}
    for _ in range(3):
        assert governance.note_call("read_file", params) is None


def test_结果注记_命中提醒点才加字段():
    params = {"path": "a.txt"}
    result = {"ok": True}
    governance.annotate_result("read_file", params, result)
    governance.annotate_result("read_file", params, result)
    assert "_repeat" not in result
    governance.annotate_result("read_file", params, result)
    assert result["_repeat"]["count"] == 3
    assert set(result) == {"ok", "_repeat"}  # 不额外加字段（时间上下文未做）


def test_结果注记_只对dict生效():
    value = ["not", "a", "dict"]
    governance.annotate_result("read_file", {}, value)
    assert value == ["not", "a", "dict"]


def test_status_形状():
    payload = governance.status()
    assert payload["repeat_reminder"] is True
    assert payload["thresholds"] == [3, 5, 8]
    assert "timeout_budget" in payload["tool_timeout"]
    assert "todo_write" in payload["excluded"]
