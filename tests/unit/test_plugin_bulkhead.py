"""每插件并发闸门（P1 bulkhead）单元测试。"""
from __future__ import annotations

from agent.plugin_bulkhead import PluginBulkhead


def test_limit_blocks_third_acquire_and_release_recovers():
    b = PluginBulkhead(2)
    assert b.acquire("p") is True
    assert b.acquire("p") is True
    assert b.acquire("p") is False
    b.release("p")
    assert b.acquire("p") is True


def test_independent_plugins_do_not_share_budget():
    b = PluginBulkhead(1)
    assert b.acquire("a") is True
    assert b.acquire("b") is True
    assert b.acquire("a") is False


def test_zero_disables_gate():
    b = PluginBulkhead(0)
    for _ in range(100):
        assert b.acquire("a") is True
    b.release("a")
    assert b.limit == 0


def test_release_without_acquire_is_safe():
    b = PluginBulkhead(1)
    b.release("never-seen")  # 不应抛异常
    assert b.acquire("never-seen") is True
