"""ShortTermMemory 集成测试

验证 TLM L1 层短期记忆的核心功能：
- save/get 基本读写
- TTL 自动过期
- LRU 淘汰策略
- cleanup_expired 手动清理
- get_stats 统计信息
- clear_task_memory 按 task_id 清理
- clear_all 全量清空
- list_entries 条目列举

设计原则：
- 每个测试独立实例化 STM，无状态污染
- async 方法通过 asyncio.run() 调用，不依赖 pytest-asyncio
- TTL 测试通过直接修改 entry.expires_at 避免时间精度问题
"""
import asyncio
import time
import pytest

pytestmark = pytest.mark.integration


def _run(coro):
    """同步运行 async 协程"""
    return asyncio.run(coro)


class TestShortTermMemoryBasic:
    """基本读写测试"""

    def test_save_and_get(self):
        from agent.memory.short_term_memory import ShortTermMemory
        stm = ShortTermMemory(max_size=10, default_ttl=60)
        ok = _run(stm.save("k1", {"step": 1}))
        assert ok is True
        value = _run(stm.get("k1"))
        assert value == {"step": 1}

    def test_get_nonexistent(self):
        from agent.memory.short_term_memory import ShortTermMemory
        stm = ShortTermMemory()
        assert _run(stm.get("not_exists")) is None

    def test_save_empty_key_returns_false(self):
        from agent.memory.short_term_memory import ShortTermMemory
        stm = ShortTermMemory()
        assert _run(stm.save("", "content")) is False
        assert _run(stm.get("")) is None

    def test_save_overwrite(self):
        from agent.memory.short_term_memory import ShortTermMemory
        stm = ShortTermMemory()
        _run(stm.save("k1", "v1"))
        _run(stm.save("k1", "v2"))
        assert _run(stm.get("k1")) == "v2"


class TestShortTermMemoryTTL:
    """TTL 过期测试"""

    def test_ttl_expiry(self):
        from agent.memory.short_term_memory import ShortTermMemory
        stm = ShortTermMemory(default_ttl=60)
        _run(stm.save("k1", "data", ttl=60))
        # 直接修改 expires_at 模拟过期
        with stm._lock:
            stm._store["k1"].expires_at = time.time() - 1
        assert _run(stm.get("k1")) is None

    def test_ttl_zero_never_expires(self):
        from agent.memory.short_term_memory import ShortTermMemory
        stm = ShortTermMemory()
        _run(stm.save("persistent", "data", ttl=0))
        with stm._lock:
            stm._store["persistent"].expires_at = 0
        assert _run(stm.get("persistent")) == "data"


class TestShortTermMemoryLRU:
    """LRU 淘汰测试"""

    def test_lru_eviction_when_full(self):
        from agent.memory.short_term_memory import ShortTermMemory
        stm = ShortTermMemory(max_size=3)
        _run(stm.save("k1", "v1"))
        _run(stm.save("k2", "v2"))
        _run(stm.save("k3", "v3"))
        # 写入第 4 个，应淘汰 k1（最老且未访问）
        _run(stm.save("k4", "v4"))
        assert _run(stm.get("k1")) is None
        assert _run(stm.get("k4")) == "v4"

    def test_lru_accessed_not_evicted(self):
        from agent.memory.short_term_memory import ShortTermMemory
        stm = ShortTermMemory(max_size=3)
        _run(stm.save("k1", "v1"))
        _run(stm.save("k2", "v2"))
        _run(stm.save("k3", "v3"))
        # 访问 k1，标记为已访问
        _run(stm.get("k1"))
        # 写入第 4 个，k1 被访问过，应淘汰 k2（未访问中最老）
        _run(stm.save("k4", "v4"))
        assert _run(stm.get("k1")) == "v1"
        assert _run(stm.get("k2")) is None


class TestShortTermMemoryCleanup:
    """清理功能测试"""

    def test_cleanup_expired(self):
        from agent.memory.short_term_memory import ShortTermMemory
        stm = ShortTermMemory()
        _run(stm.save("fresh", "data", ttl=60))
        _run(stm.save("stale", "data", ttl=60))
        with stm._lock:
            stm._store["stale"].expires_at = time.time() - 1
        count = stm.cleanup_expired()
        assert count == 1
        assert _run(stm.get("fresh")) == "data"
        assert _run(stm.get("stale")) is None

    def test_clear_task_memory(self):
        from agent.memory.short_term_memory import ShortTermMemory
        stm = ShortTermMemory()
        _run(stm.save("k1", "v1", task_id="task_A"))
        _run(stm.save("k2", "v2", task_id="task_A"))
        _run(stm.save("k3", "v3", task_id="task_B"))
        count = _run(stm.clear_task_memory("task_A"))
        assert count == 2
        assert _run(stm.get("k1")) is None
        assert _run(stm.get("k2")) is None
        assert _run(stm.get("k3")) == "v3"

    def test_clear_all(self):
        from agent.memory.short_term_memory import ShortTermMemory
        stm = ShortTermMemory()
        _run(stm.save("k1", "v1"))
        _run(stm.save("k2", "v2"))
        count = _run(stm.clear_all())
        assert count == 2
        assert _run(stm.get("k1")) is None
        assert stm.get_stats()["total_entries"] == 0


class TestShortTermMemoryStats:
    """统计与列举测试"""

    def test_get_stats(self):
        from agent.memory.short_term_memory import ShortTermMemory
        stm = ShortTermMemory(max_size=10)
        _run(stm.save("k1", "v1"))
        _run(stm.save("k2", "v2"))
        stats = stm.get_stats()
        assert stats["total_entries"] == 2
        assert stats["max_size"] == 10
        assert stats["active_entries"] == 2
        assert stats["expired_entries"] == 0
        assert "usage_pct" in stats

    def test_list_entries_excludes_expired(self):
        from agent.memory.short_term_memory import ShortTermMemory
        stm = ShortTermMemory()
        _run(stm.save("fresh", "data", ttl=60))
        _run(stm.save("stale", "data", ttl=60))
        with stm._lock:
            stm._store["stale"].expires_at = time.time() - 1
        entries = stm.list_entries(include_expired=False)
        keys = [e["key"] for e in entries]
        assert "fresh" in keys
        assert "stale" not in keys

    def test_list_entries_includes_expired(self):
        from agent.memory.short_term_memory import ShortTermMemory
        stm = ShortTermMemory()
        _run(stm.save("fresh", "data", ttl=60))
        _run(stm.save("stale", "data", ttl=60))
        with stm._lock:
            stm._store["stale"].expires_at = time.time() - 1
        entries = stm.list_entries(include_expired=True)
        keys = [e["key"] for e in entries]
        assert "fresh" in keys
        assert "stale" in keys
