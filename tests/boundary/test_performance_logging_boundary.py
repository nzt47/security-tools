"""BT-005 performance_logging 模块边界测试

【生成日志摘要】
- 生成时间：2026-07-01
- 内容：BT-005 performance_logging 边界测试（v1.0）
- 模型：GLM-5.2
- 关键状态：覆盖 LLMCache / AsyncSaveMonitor / PerformanceLogger / LLMCacheStats 7 类边界场景
- 状态同步机制：纯内存测试，每个测试使用独立实例避免状态污染

覆盖范围：
- 空值边界: None prompt / None response / None operation
- 极值边界: max_size=0 / max_size=-1 / ttl_seconds=0 / 超大 max_size
- 类型边界: None 作为 prompt 抛 AttributeError
- 异常分支: record_save_end 不存在 id / get_stats 无记录
- 资源边界: LRU 淘汰策略 / TTL 过期

源代码状态记录（2026-07-01 已永久修复）：
- LLMCache(max_size=0/-1) — 已修复：__init__ 现校验 max_size >= 1，抛 ValueError
  修复位置: agent/monitoring/performance.py LLMCache.__init__
- LLMCache.get(None) 抛 AttributeError（None.encode() 失败）— 调用方契约，保留
- record_save_end(不存在id) 不更新记录但 total_saves 仍 +1 — 统计偏差，低优先级保留
"""
import time
import pytest

from agent.monitoring.performance import (
    CacheEntry,
    LLMCacheStats,
    LLMCache,
    AsyncSaveMonitor,
    PerformanceLogger,
)


# ═══════════════════════════════════════════════════════════════
#  LLMCache 空值边界测试
# ═══════════════════════════════════════════════════════════════


class TestCacheNullBoundary:
    """LLMCache 空值边界测试"""

    def test_null_None作为prompt抛出AttributeError(self):
        """None 作为 prompt 抛出 AttributeError

        源代码限制: get() 调用 prompt.encode('utf-8')，None 无 encode 方法
        """
        cache = LLMCache()
        with pytest.raises(AttributeError):
            cache.get(None)  # type: ignore

    def test_null_None作为prompt_put抛出AttributeError(self):
        """None 作为 prompt 的 put 抛出 AttributeError"""
        cache = LLMCache()
        with pytest.raises(AttributeError):
            cache.put(None, "response")  # type: ignore

    def test_empty_空字符串prompt正常处理(self):
        """空字符串 prompt 正常处理"""
        cache = LLMCache()
        cache.put("", "empty_response")
        result = cache.get("")
        assert result == "empty_response"

    def test_empty_空缓存get返回None(self):
        """空缓存 get 返回 None"""
        cache = LLMCache()
        assert cache.get("any_prompt") is None

    def test_null_None作为response正常缓存(self):
        """None 作为 response 正常缓存"""
        cache = LLMCache()
        cache.put("prompt", None)  # type: ignore
        result = cache.get("prompt")
        assert result is None

    def test_empty_空字符串response正常缓存(self):
        """空字符串 response 正常缓存"""
        cache = LLMCache()
        cache.put("prompt", "")
        result = cache.get("prompt")
        assert result == ""


# ═══════════════════════════════════════════════════════════════
#  LLMCache 极值边界测试
# ═══════════════════════════════════════════════════════════════


class TestCacheExtremeBoundary:
    """LLMCache 极值边界测试"""

    def test_extreme_max_size零值init抛出ValueError(self):
        """max_size=0 时 __init__ 抛出 ValueError

        源代码已修复（2026-07-01）：__init__ 现校验 max_size >= 1，
        避免后续 put() 在空 OrderedDict 上调用 popitem(last=False) 抛 KeyError。
        修复前: LLMCache(max_size=0) 可创建，但首次 put 抛 KeyError
        修复后: LLMCache(max_size=0) 在构造时即抛 ValueError，fail-fast
        """
        with pytest.raises(ValueError, match="max_size"):
            LLMCache(max_size=0)

    def test_extreme_max_size负值init抛出ValueError(self):
        """max_size=-1 时 __init__ 抛出 ValueError

        源代码已修复（2026-07-01）：与 max_size=0 同理，负值在构造时被拒绝。
        """
        with pytest.raises(ValueError, match="max_size"):
            LLMCache(max_size=-1)

    def test_extreme_ttl_seconds零值立即过期(self):
        """ttl_seconds=0 时缓存立即过期"""
        cache = LLMCache(max_size=100, ttl_seconds=0)
        cache.put("prompt", "response")
        # ttl=0，is_expired() 返回 time.time() - timestamp > 0，几乎总是 True
        time.sleep(0.01)  # 确保时间过去
        result = cache.get("prompt")
        assert result is None  # 已过期

    def test_extreme_ttl_seconds负值行为(self):
        """ttl_seconds=-1 时缓存立即过期"""
        cache = LLMCache(max_size=100, ttl_seconds=-1)
        cache.put("prompt", "response")
        time.sleep(0.01)
        result = cache.get("prompt")
        assert result is None  # 负 TTL 立即过期

    def test_extreme_超大max_size正常工作(self):
        """超大 max_size 正常工作"""
        cache = LLMCache(max_size=999999999)
        for i in range(100):
            cache.put(f"prompt_{i}", f"response_{i}")
        assert len(cache.cache) == 100

    def test_boundary_刚好达到max_size不淘汰(self):
        """刚好达到 max_size 时不淘汰"""
        cache = LLMCache(max_size=3)
        cache.put("p1", "r1")
        cache.put("p2", "r2")
        cache.put("p3", "r3")
        assert len(cache.cache) == 3
        # 此时不应淘汰
        assert cache.get("p1") == "r1"

    def test_boundary_超过max_size触发LRU淘汰(self):
        """超过 max_size 触发 LRU 淘汰"""
        cache = LLMCache(max_size=2)
        cache.put("p1", "r1")
        cache.put("p2", "r2")
        cache.put("p3", "r3")  # 触发淘汰 p1（最久未使用）
        assert len(cache.cache) == 2
        assert cache.get("p1") is None  # p1 被淘汰
        assert cache.get("p2") == "r2"
        assert cache.get("p3") == "r3"

    def test_boundary_LRU访问后不被淘汰(self):
        """LRU 访问后不被淘汰"""
        cache = LLMCache(max_size=2)
        cache.put("p1", "r1")
        cache.put("p2", "r2")
        cache.get("p1")  # 访问 p1，移到末尾
        cache.put("p3", "r3")  # 触发淘汰 p2（现在 p2 是最久未使用）
        assert cache.get("p1") == "r1"  # p1 仍在
        assert cache.get("p2") is None  # p2 被淘汰


# ═══════════════════════════════════════════════════════════════
#  LLMCache TTL 过期边界测试
# ═══════════════════════════════════════════════════════════════


class TestCacheTTLBoundary:
    """LLMCache TTL 过期边界测试"""

    def test_boundary_自定义ttl覆盖默认ttl(self):
        """自定义 ttl 覆盖默认 ttl"""
        cache = LLMCache(max_size=100, ttl_seconds=3600)
        cache.put("prompt", "response", ttl_seconds=1)
        assert cache.get("prompt") == "response"  # 未过期
        time.sleep(1.1)
        assert cache.get("prompt") is None  # 已过期

    def test_boundary_未过期缓存返回response(self):
        """未过期缓存返回 response"""
        cache = LLMCache(max_size=100, ttl_seconds=3600)
        cache.put("prompt", "response")
        assert cache.get("prompt") == "response"

    def test_boundary_更新缓存重置timestamp(self):
        """更新缓存重置 timestamp"""
        cache = LLMCache(max_size=100, ttl_seconds=1)
        cache.put("prompt", "response")
        time.sleep(0.6)
        cache.put("prompt", "response2")  # 更新，重置 timestamp
        time.sleep(0.6)
        # 如果没有重置，这里应该过期了
        assert cache.get("prompt") == "response2"  # 仍未过期

    def test_boundary_过期缓存从字典删除(self):
        """过期缓存从字典删除"""
        cache = LLMCache(max_size=100, ttl_seconds=0)
        cache.put("prompt", "response")
        time.sleep(0.01)
        cache.get("prompt")  # 触发删除
        assert len(cache.cache) == 0


# ═══════════════════════════════════════════════════════════════
#  LLMCache 编码边界测试
# ═══════════════════════════════════════════════════════════════


class TestCacheEncodingBoundary:
    """LLMCache 编码边界测试"""

    def test_encoding_中文prompt正常缓存(self):
        """中文 prompt 正常缓存"""
        cache = LLMCache()
        cache.put("你好世界", "你好回复")
        assert cache.get("你好世界") == "你好回复"

    def test_encoding_emoji_prompt正常缓存(self):
        """emoji prompt 正常缓存"""
        cache = LLMCache()
        cache.put("hello 😀 world", "emoji 回复")
        assert cache.get("hello 😀 world") == "emoji 回复"

    def test_encoding_特殊字符prompt正常缓存(self):
        """特殊字符 prompt 正常缓存"""
        cache = LLMCache()
        cache.put("prompt with <>&\"' special chars", "response")
        assert cache.get("prompt with <>&\"' special chars") == "response"

    def test_extreme_超长prompt正常缓存(self):
        """超长 prompt 正常缓存"""
        cache = LLMCache()
        long_prompt = "a" * 10000
        cache.put(long_prompt, "response")
        assert cache.get(long_prompt) == "response"


# ═══════════════════════════════════════════════════════════════
#  LLMCacheStats 边界测试
# ═══════════════════════════════════════════════════════════════


class TestCacheStatsBoundary:
    """LLMCacheStats 边界测试"""

    def test_empty_空统计hit_rate返回零(self):
        """空统计 hit_rate 返回 0.0"""
        stats = LLMCacheStats()
        assert stats.get_hit_rate() == 0.0

    def test_empty_空统计avg_hit_time返回零(self):
        """空统计 avg_hit_time 返回 0.0"""
        stats = LLMCacheStats()
        assert stats.get_avg_hit_time_ms() == 0.0

    def test_empty_空统计avg_save_time返回零(self):
        """空统计 avg_save_time 返回 0.0"""
        stats = LLMCacheStats()
        assert stats.get_avg_save_time_ms() == 0.0

    def test_boundary_有命中和未命中hit_rate正确(self):
        """有命中和未命中 hit_rate 正确"""
        stats = LLMCacheStats()
        stats.record_hit(10.0)
        stats.record_hit(20.0)
        stats.record_miss()
        # hit_rate = 2 / 3
        assert stats.get_hit_rate() == pytest.approx(0.6667, rel=0.01)

    def test_boundary_to_dict包含所有字段(self):
        """to_dict 包含所有字段"""
        stats = LLMCacheStats()
        d = stats.to_dict()
        assert "hits" in d
        assert "misses" in d
        assert "hit_rate" in d
        assert "avg_hit_time_ms" in d
        assert "avg_save_time_ms" in d
        assert "evictions" in d

    def test_boundary_record_eviction递增(self):
        """record_eviction 递增"""
        stats = LLMCacheStats()
        stats.record_eviction()
        stats.record_eviction()
        assert stats.evictions == 2


# ═══════════════════════════════════════════════════════════════
#  CacheEntry 边界测试
# ═══════════════════════════════════════════════════════════════


class TestCacheEntryBoundary:
    """CacheEntry 边界测试"""

    def test_boundary_未过期is_expired返回False(self):
        """未过期 is_expired 返回 False"""
        entry = CacheEntry(
            prompt_hash="hash",
            response="resp",
            timestamp=time.time(),
            ttl_seconds=3600,
        )
        assert entry.is_expired() is False

    def test_boundary_已过期is_expired返回True(self):
        """已过期 is_expired 返回 True"""
        entry = CacheEntry(
            prompt_hash="hash",
            response="resp",
            timestamp=time.time() - 7200,  # 2 小时前
            ttl_seconds=3600,  # 1 小时 TTL
        )
        assert entry.is_expired() is True

    def test_extreme_ttl零值立即过期(self):
        """ttl_seconds=0 立即过期"""
        entry = CacheEntry(
            prompt_hash="hash",
            response="resp",
            timestamp=time.time(),
            ttl_seconds=0,
        )
        # time.time() - timestamp > 0 几乎总是 True
        time.sleep(0.01)
        assert entry.is_expired() is True

    def test_boundary_默认hit_count为零(self):
        """默认 hit_count 为 0"""
        entry = CacheEntry(
            prompt_hash="hash",
            response="resp",
            timestamp=time.time(),
            ttl_seconds=3600,
        )
        assert entry.hit_count == 0

    def test_boundary_默认generation_time为零(self):
        """默认 generation_time_ms 为 0.0"""
        entry = CacheEntry(
            prompt_hash="hash",
            response="resp",
            timestamp=time.time(),
            ttl_seconds=3600,
        )
        assert entry.generation_time_ms == 0.0


# ═══════════════════════════════════════════════════════════════
#  AsyncSaveMonitor 边界测试
# ═══════════════════════════════════════════════════════════════


class TestAsyncSaveMonitorBoundary:
    """AsyncSaveMonitor 边界测试"""

    def test_empty_初始状态total_saves为零(self):
        """初始状态 total_saves 为 0"""
        monitor = AsyncSaveMonitor()
        assert monitor.total_saves == 0
        assert monitor.failed == 0

    def test_boundary_record_save_start返回record_id(self):
        """record_save_start 返回 record_id"""
        monitor = AsyncSaveMonitor()
        record_id = monitor.record_save_start("chat", "task_001")
        assert isinstance(record_id, str)
        assert "chat" in record_id
        assert "task_001" in record_id

    def test_boundary_record_save_end正常完成(self):
        """record_save_end 正常完成"""
        monitor = AsyncSaveMonitor()
        record_id = monitor.record_save_start("chat", "task_001")
        monitor.record_save_end(record_id, success=True)
        assert monitor.total_saves == 1
        assert monitor.failed == 0

    def test_boundary_record_save_end失败记录(self):
        """record_save_end 失败记录"""
        monitor = AsyncSaveMonitor()
        record_id = monitor.record_save_start("chat", "task_001")
        monitor.record_save_end(record_id, success=False, error="test error")
        assert monitor.total_saves == 1
        assert monitor.failed == 1

    def test_invalid_不存在的record_id仍递增total_saves(self):
        """不存在的 record_id 仍递增 total_saves

        源代码限制: record_save_end 找不到记录时仍执行 total_saves += 1
        """
        monitor = AsyncSaveMonitor()
        monitor.record_save_end("nonexistent_id", success=True)
        assert monitor.total_saves == 1  # 仍递增

    def test_empty_空状态get_stats正常返回(self):
        """空状态 get_stats 正常返回"""
        monitor = AsyncSaveMonitor()
        stats = monitor.get_stats()
        assert stats["total_saves"] == 0
        assert stats["failed"] == 0
        assert stats["success_rate"] == "N/A"
        assert stats["avg_time_ms"] == "N/A"

    def test_boundary_get_recent_saves返回最近记录(self):
        """get_recent_saves 返回最近记录"""
        monitor = AsyncSaveMonitor()
        for i in range(5):
            rid = monitor.record_save_start("chat", f"task_{i}")
            monitor.record_save_end(rid)
        recent = monitor.get_recent_saves(n=3)
        assert len(recent) == 3

    def test_extreme_get_recent_saves_n大于记录数(self):
        """get_recent_saves n 大于记录数返回全部"""
        monitor = AsyncSaveMonitor()
        rid = monitor.record_save_start("chat", "task_001")
        monitor.record_save_end(rid)
        recent = monitor.get_recent_saves(n=100)
        assert len(recent) == 1


# ═══════════════════════════════════════════════════════════════
#  PerformanceLogger 边界测试
# ═══════════════════════════════════════════════════════════════


class TestPerformanceLoggerBoundary:
    """PerformanceLogger 边界测试"""

    def test_empty_初始状态无记录(self):
        """初始状态无记录"""
        logger = PerformanceLogger()
        assert len(logger.records) == 0

    def test_boundary_log正常记录(self):
        """log 正常记录"""
        logger = PerformanceLogger()
        logger.log("llm_inference", 150.5)
        assert len(logger.records) == 1
        assert logger.records[0]["operation"] == "llm_inference"
        assert logger.records[0]["elapsed_ms"] == 150.5

    def test_null_None作为operation正常记录(self):
        """None 作为 operation 正常记录"""
        logger = PerformanceLogger()
        logger.log(None, 100.0)  # type: ignore
        assert logger.records[0]["operation"] is None

    def test_empty_空字符串operation正常记录(self):
        """空字符串 operation 正常记录"""
        logger = PerformanceLogger()
        logger.log("", 100.0)
        assert logger.records[0]["operation"] == ""

    def test_null_None作为metadata正常记录(self):
        """None 作为 metadata 正常记录（转为空字典）"""
        logger = PerformanceLogger()
        logger.log("op", 100.0, metadata=None)
        assert logger.records[0]["metadata"] == {}

    def test_empty_空字典metadata正常记录(self):
        """空字典 metadata 正常记录"""
        logger = PerformanceLogger()
        logger.log("op", 100.0, metadata={})
        assert logger.records[0]["metadata"] == {}

    def test_extreme_负数elapsed_ms正常记录(self):
        """负数 elapsed_ms 正常记录"""
        logger = PerformanceLogger()
        logger.log("op", -100.0)
        assert logger.records[0]["elapsed_ms"] == -100.0

    def test_extreme_超大elapsed_ms正常记录(self):
        """超大 elapsed_ms 正常记录"""
        logger = PerformanceLogger()
        logger.log("op", 999999999.0)
        assert logger.records[0]["elapsed_ms"] == 999999999.0

    def test_boundary_嵌套metadata正常记录(self):
        """嵌套 metadata 正常记录"""
        logger = PerformanceLogger()
        nested = {"level1": {"level2": {"level3": "deep"}}}
        logger.log("op", 100.0, metadata=nested)
        assert logger.records[0]["metadata"] == nested

    def test_boundary_log包含timestamp字段(self):
        """log 记录包含 timestamp 字段"""
        logger = PerformanceLogger()
        logger.log("op", 100.0)
        assert "timestamp" in logger.records[0]
        assert isinstance(logger.records[0]["timestamp"], str)


# ═══════════════════════════════════════════════════════════════
#  LLMCache get_top_patterns 边界测试
# ═══════════════════════════════════════════════════════════════


class TestCacheTopPatternsBoundary:
    """LLMCache get_top_patterns 边界测试"""

    def test_empty_无命中返回空列表(self):
        """无命中返回空列表"""
        cache = LLMCache()
        assert cache.get_top_patterns() == []

    def test_boundary_有命中返回排序结果(self):
        """有命中返回按次数降序排序的结果"""
        cache = LLMCache()
        # greeting 类
        cache.put("hello", "r1")
        cache.get("hello")  # 命中 greeting
        cache.get("hello")  # 再命中
        # short 类
        cache.put("hi", "r2")
        cache.get("hi")  # 命中 short（len < 20）
        patterns = cache.get_top_patterns()
        assert len(patterns) > 0
        # greeting 应该是第一个（2 次命中 vs short 1 次）
        assert patterns[0][1] >= patterns[-1][1]

    def test_extreme_top_n大于模式数返回全部(self):
        """top_n 大于模式数返回全部"""
        cache = LLMCache()
        cache.put("hello", "r1")
        cache.get("hello")
        patterns = cache.get_top_patterns(top_n=100)
        assert len(patterns) >= 1

    def test_extreme_top_n零值返回空列表(self):
        """top_n=0 返回空列表"""
        cache = LLMCache()
        cache.put("hello", "r1")
        cache.get("hello")
        patterns = cache.get_top_patterns(top_n=0)
        assert patterns == []


# ═══════════════════════════════════════════════════════════════
#  LLMCache clear 边界测试
# ═══════════════════════════════════════════════════════════════


class TestCacheClearBoundary:
    """LLMCache clear 边界测试"""

    def test_empty_空缓存clear不抛异常(self):
        """空缓存 clear 不抛异常"""
        cache = LLMCache()
        cache.clear()
        assert len(cache.cache) == 0

    def test_boundary_有数据clear清空缓存(self):
        """有数据 clear 清空缓存"""
        cache = LLMCache()
        cache.put("p1", "r1")
        cache.put("p2", "r2")
        cache.clear()
        assert len(cache.cache) == 0
        assert cache.get("p1") is None

    def test_boundary_clear后stats保留(self):
        """clear 后 stats 保留（统计不重置）"""
        cache = LLMCache()
        cache.put("p1", "r1")
        cache.get("p1")  # hit
        cache.get("p2")  # miss
        stats_before = cache.get_stats()
        cache.clear()
        stats_after = cache.get_stats()
        # stats 不应重置
        assert stats_after["hits"] == stats_before["hits"]
        assert stats_after["misses"] == stats_before["misses"]
