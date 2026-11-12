"""外部端口存活探针（`agent/monitoring/port_probe.py`，2026-11-12）

【判据】这不是"再上一套监控"，而是**最薄的一层 TCP 探活**：
① 没配置 = 不探任何端口（never，不进健康分分母）；
② 配置了但连不上 = warn + 逐条 target（真读数，不是猜）；
③ 只回"配置里的 target"，不落对端地址；非法配置项跳过并记日志（不让一条坏配置拖垮全部）。
"""
from __future__ import annotations

import pytest

from agent.monitoring import port_probe


def test_parse_targets_accepts_separators_and_drops_invalid():
    raw = "127.0.0.1:5678;host2:1234, bad, x:notaport, host:70000"
    assert port_probe.parse_targets(raw) == [("127.0.0.1", 5678), ("host2", 1234)]


def test_parse_targets_caps_at_max(monkeypatch):
    monkeypatch.setattr(port_probe, "MAX_TARGETS", 2)
    assert len(port_probe.parse_targets("a:1,b:2,c:3")) == 2


def test_probe_targets_marks_ok_and_failure_per_target():
    calls = []

    def fake(host, port, timeout):
        calls.append((host, port))
        return port == 5678

    rows = port_probe.probe_targets([("127.0.0.1", 5678), ("127.0.0.1", 9999)],
                                    connector=fake)
    assert [r["ok"] for r in rows] == [True, False]
    assert rows[0]["target"] == "127.0.0.1:5678"
    assert rows[0]["latency_ms"] >= 0.0
    assert calls == [("127.0.0.1", 5678), ("127.0.0.1", 9999)]


def test_probe_targets_swallows_connector_exception():
    def boom(host, port, timeout):
        raise RuntimeError("network stack exploded")

    rows = port_probe.probe_targets([("h", 1)], connector=boom)
    assert rows[0]["ok"] is False


def test_unit_probe_never_when_not_configured():
    out = port_probe.unit_probe([])
    assert out["status"] == "never"
    assert "未配置" in out["summary"]
    assert out["metrics"]["configured"] == 0


def test_unit_probe_ok_when_all_open():
    out = port_probe.unit_probe([("127.0.0.1", 5678)], connector=lambda *a: True)
    assert out["status"] == "ok"
    assert out["metrics"]["open"] == 1
    assert out["issues"] == []


def test_unit_probe_warn_lists_closed_targets():
    def fake(host, port, timeout):
        return port != 9999

    out = port_probe.unit_probe([("127.0.0.1", 5678), ("127.0.0.1", 9999)],
                                connector=fake)
    assert out["status"] == "warn"
    assert out["metrics"]["closed"] == 1
    assert any("127.0.0.1:9999" in issue for issue in out["issues"])


def test_default_connector_is_read_only_tcp(monkeypatch):
    """默认连接器必须走 socket.create_connection（不是 HTTP 请求）。"""
    seen = {}

    class _CM:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_create(address, timeout=None):
        seen["address"] = address
        seen["timeout"] = timeout
        return _CM()

    monkeypatch.setattr(port_probe.socket, "create_connection", fake_create)
    assert port_probe._default_connector("127.0.0.1", 5678, 0.2) is True
    assert seen == {"address": ("127.0.0.1", 5678), "timeout": 0.2}
