"""[TLM-L5] MemoryRouter 三层路由单元测试

覆盖：
- _classify_tier 自动判定规则（L1/L2/L3）
- register_tier 注册
- route_tier 显式指定 + 自动判定
- L1 key 查找（STM.get 包装为 MemoryResult）
- L2/L3 search 调用
- 适配器未注册降级
- to_dict 包含 tier 信息
- 中文短句 vs 英文 Key 的 Mock 数据准确性
"""

import pytest
import logging
from unittest.mock import AsyncMock, MagicMock

from agent.memory.router import MemoryRouter, _contains_cjk
from agent.memory.base import MemoryResult, MemoryInterface


# ── Mock 适配器 ──

class MockShortTermMemory:
    """模拟 ShortTermMemory（L1：只有 get，无 search）"""
    def __init__(self):
        self._store = {"stm:ctx1": "会话中间结果", "abc": "短key值"}
        self.get = AsyncMock(side_effect=lambda k: self._store.get(k))

    async def get(self, key):  # type: ignore[no-redef]
        return self._store.get(key)


class MockSearchAdapter:
    """模拟有 search 方法的适配器（L2/L3 通用）"""
    def __init__(self, name: str = "mock"):
        self._name = name
        self.search = AsyncMock(return_value=[
            MemoryResult(content=f"{name} 结果1", confidence=0.9, source=name),
            MemoryResult(content=f"{name} 结果2", confidence=0.7, source=name),
        ])

    async def search(self, query, top_k=5):  # type: ignore[no-redef]
        return [
            MemoryResult(content=f"{self._name} 结果", confidence=0.9, source=self._name),
        ]


class MockMemoryInterface(MemoryInterface):
    """实现 MemoryInterface 的 mock（用作默认适配器）"""
    async def save(self, key, data, metadata=None):
        return True

    async def search(self, query, top_k=5):
        return [MemoryResult(content="default 结果", confidence=0.5, source="default")]

    async def get_profile(self, user_id):
        return {}

    async def update_graph(self, entities, relations):
        return True


# ── 辅助：构造带 mock 适配器的 router ──

def _make_router_with_tiers():
    """构造注册了 L1/L2/L3 mock 适配器的 router"""
    router = MemoryRouter(default_adapter=MockMemoryInterface())
    stm = MockShortTermMemory()
    holo = MockSearchAdapter("holographic")
    ltm = MockSearchAdapter("long_term")
    router.register_tier("L1", stm)
    router.register_tier("L2", holo)
    router.register_tier("L3", ltm)
    return router, stm, holo, ltm


# ═══════════════════════════════════════════════════════════════
# _contains_cjk 辅助函数
# ═══════════════════════════════════════════════════════════════

class TestContainsCjk:
    def test_pure_ascii_returns_false(self):
        assert _contains_cjk("abc123") is False

    def test_chinese_returns_true(self):
        assert _contains_cjk("用户偏好") is True

    def test_mixed_returns_true(self):
        assert _contains_cjk("abc用户") is True

    def test_empty_returns_false(self):
        assert _contains_cjk("") is False

    def test_japanese_returns_true(self):
        assert _contains_cjk("テスト") is True


# ═══════════════════════════════════════════════════════════════
# _classify_tier 自动判定规则
# ═══════════════════════════════════════════════════════════════

class TestClassifyTierL1:
    """L1: 纯 ASCII key 或 stm:/session: 前缀"""

    def test_stm_prefix(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, reason = router._classify_tier("stm:ctx1")
        assert tier == "L1"
        assert "stm:" in reason

    def test_session_prefix(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, _ = router._classify_tier("session:abc")
        assert tier == "L1"

    def test_short_ascii_key(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, reason = router._classify_tier("abc123")
        assert tier == "L1"
        assert "ASCII key" in reason

    def test_rejects_chinese_short(self):
        """中文短句（如'用户偏好'4字）不应走 L1"""
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, _ = router._classify_tier("用户偏好")
        assert tier != "L1"

    def test_rejects_space(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, _ = router._classify_tier("ab cd")
        assert tier != "L1"

    def test_rejects_long_ascii(self):
        """长度>=8 的 ASCII 不走 L1"""
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, _ = router._classify_tier("abcdefgh")
        assert tier != "L1"


class TestClassifyTierL2:
    """L2: 时间词或操作词"""

    def test_time_word_cn_recent(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, reason = router._classify_tier("最近的操作记录")
        assert tier == "L2"
        assert "最近" in reason

    def test_time_word_cn_last(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, _ = router._classify_tier("上次的对话")
        assert tier == "L2"

    def test_time_word_en_recent(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, reason = router._classify_tier("recent activity log")
        assert tier == "L2"
        assert "recent" in reason

    def test_op_word_cn(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, reason = router._classify_tier("做了什么操作")
        assert tier == "L2"
        assert "做了" in reason

    def test_op_word_en(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, _ = router._classify_tier("what did you do")
        assert tier == "L2"


class TestClassifyTierL3:
    """L3: 语义词、长查询、兜底"""

    def test_semantic_word_cn(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, reason = router._classify_tier("用户偏好设置")
        assert tier == "L3"
        assert "偏好" in reason

    def test_semantic_word_en(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, reason = router._classify_tier("user prefer theme")
        assert tier == "L3"
        assert "prefer" in reason

    def test_long_query(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, reason = router._classify_tier("这是一个比较长的查询句子")
        assert tier == "L3"
        assert "12" in reason

    def test_fallback(self):
        """不命中 L1/L2 的短句走 L3 兜底"""
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, reason = router._classify_tier("短句")
        assert tier == "L3"
        assert "兜底" in reason


# ═══════════════════════════════════════════════════════════════
# register_tier
# ═══════════════════════════════════════════════════════════════

class TestRegisterTier:
    def test_register_l1_l2_l3(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        router.register_tier("L1", MockShortTermMemory())
        router.register_tier("L2", MockSearchAdapter())
        router.register_tier("L3", MockSearchAdapter())
        assert set(router._tier_adapters.keys()) == {"L1", "L2", "L3"}

    def test_invalid_tier_raises(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        with pytest.raises(ValueError, match="L1/L2/L3"):
            router.register_tier("L4", MockSearchAdapter())

    def test_case_insensitive(self):
        """小写 'l1' 应转为 'L1'"""
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        router.register_tier("l1", MockShortTermMemory())
        assert "L1" in router._tier_adapters


# ═══════════════════════════════════════════════════════════════
# route_tier 显式指定
# ═══════════════════════════════════════════════════════════════

class TestRouteTierExplicit:
    @pytest.mark.asyncio
    async def test_explicit_l1_calls_get(self):
        router, stm, holo, ltm = _make_router_with_tiers()
        results = await router.route_tier("stm:ctx1", tier="L1")
        assert len(results) == 1
        assert results[0].source == "short_term"
        assert results[0].metadata["tier"] == "L1"
        assert results[0].confidence == 1.0
        stm.get.assert_awaited_once_with("stm:ctx1")

    @pytest.mark.asyncio
    async def test_explicit_l2_calls_search(self):
        router, stm, holo, ltm = _make_router_with_tiers()
        results = await router.route_tier("查询", tier="L2", top_k=3)
        assert len(results) >= 1
        assert all(r.metadata["tier"] == "L2" for r in results)
        holo.search.assert_awaited_once_with("查询", 3)

    @pytest.mark.asyncio
    async def test_explicit_l3_calls_search(self):
        router, stm, holo, ltm = _make_router_with_tiers()
        results = await router.route_tier("偏好", tier="L3", top_k=2)
        assert len(results) >= 1
        assert all(r.metadata["tier"] == "L3" for r in results)
        ltm.search.assert_awaited_once_with("偏好", 2)

    @pytest.mark.asyncio
    async def test_invalid_tier_falls_back_to_auto(self):
        router, stm, holo, ltm = _make_router_with_tiers()
        # tier="L4" 无效 → 自动判定（"最近" → L2）
        results = await router.route_tier("最近的记录", tier="L4")
        assert len(results) >= 1
        assert all(r.metadata["tier"] == "L2" for r in results)

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        router, _, _, _ = _make_router_with_tiers()
        assert await router.route_tier("", tier="L1") == []
        assert await router.route_tier("", tier=None) == []


# ═══════════════════════════════════════════════════════════════
# route_tier 自动判定
# ═══════════════════════════════════════════════════════════════

class TestRouteTierAuto:
    @pytest.mark.asyncio
    async def test_auto_l1_stm_prefix(self):
        router, stm, _, _ = _make_router_with_tiers()
        results = await router.route_tier("stm:ctx1")
        assert len(results) == 1
        assert results[0].source == "short_term"
        stm.get.assert_awaited()

    @pytest.mark.asyncio
    async def test_auto_l2_time_word(self):
        router, _, holo, _ = _make_router_with_tiers()
        results = await router.route_tier("最近的操作")
        assert all(r.metadata["tier"] == "L2" for r in results)
        holo.search.assert_awaited()

    @pytest.mark.asyncio
    async def test_auto_l3_semantic_word(self):
        router, _, _, ltm = _make_router_with_tiers()
        results = await router.route_tier("用户偏好设置")
        assert all(r.metadata["tier"] == "L3" for r in results)
        ltm.search.assert_awaited()

    @pytest.mark.asyncio
    async def test_auto_l1_miss_returns_empty(self):
        """L1 自动判定命中但 STM.get 返回 None"""
        router, stm, _, _ = _make_router_with_tiers()
        results = await router.route_tier("xyz")  # 纯 ASCII key，走 L1，但 STM 无此 key
        assert results == []


# ═══════════════════════════════════════════════════════════════
# 降级逻辑
# ═══════════════════════════════════════════════════════════════

class TestRouteTierFallback:
    @pytest.mark.asyncio
    async def test_l1_not_registered_falls_to_default(self):
        """L1 适配器未注册 → 降级到默认适配器"""
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        router.register_tier("L2", MockSearchAdapter("holographic"))
        router.register_tier("L3", MockSearchAdapter("long_term"))
        # 不注册 L1，query 走 L1 自动判定
        results = await router.route_tier("stm:ctx1")
        assert len(results) >= 1
        assert all(r.metadata["tier"] == "L1" for r in results)
        assert results[0].source == "default"

    @pytest.mark.asyncio
    async def test_l2_search_exception_returns_empty(self):
        """L2 search 抛异常 → 返回空列表"""
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        bad_adapter = MockSearchAdapter("holographic")
        bad_adapter.search = AsyncMock(side_effect=RuntimeError("db error"))
        router.register_tier("L2", bad_adapter)
        results = await router.route_tier("最近的记录", tier="L2")
        assert results == []


# ═══════════════════════════════════════════════════════════════
# 日志验证
# ═══════════════════════════════════════════════════════════════

class TestRouteTierLogging:
    @pytest.mark.asyncio
    async def test_auto_classify_logs_tier_and_reason(self, caplog):
        router, _, _, _ = _make_router_with_tiers()
        with caplog.at_level(logging.INFO, logger="agent.memory.router"):
            await router.route_tier("用户偏好")
        # 验证日志包含层级和原因
        log_text = "\n".join(r.message for r in caplog.records)
        assert "route_tier 自动判定" in log_text
        assert "L3" in log_text
        assert "偏好" in log_text

    @pytest.mark.asyncio
    async def test_explicit_tier_logs(self, caplog):
        router, _, _, _ = _make_router_with_tiers()
        with caplog.at_level(logging.INFO, logger="agent.memory.router"):
            await router.route_tier("test", tier="L2")
        log_text = "\n".join(r.message for r in caplog.records)
        assert "显式指定" in log_text
        assert "L2" in log_text


# ═══════════════════════════════════════════════════════════════
# to_dict
# ═══════════════════════════════════════════════════════════════

class TestToDict:
    def test_includes_tier_map(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        d = router.to_dict()
        assert "tier_map" in d
        assert d["tier_map"]["L1"] == "short_term"
        assert d["tier_map"]["L2"] == "holographic"
        assert d["tier_map"]["L3"] == "long_term"

    def test_includes_tier_adapters(self):
        router, _, _, _ = _make_router_with_tiers()
        d = router.to_dict()
        assert "tier_adapters" in d
        assert set(d["tier_adapters"].keys()) == {"L1", "L2", "L3"}

    def test_empty_tier_adapters(self):
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        d = router.to_dict()
        assert d["tier_adapters"] == {}


# ═══════════════════════════════════════════════════════════════
# Mock 数据准确性验证（中文短句 vs 英文 Key）
# ═══════════════════════════════════════════════════════════════

class TestMockDataAccuracy:
    """构造中文短句和英文 Key 的 Mock 数据，验证路由器判定准确性"""

    @pytest.mark.parametrize("query,expected_tier,desc", [
        # ── 英文 Key → L1 ──
        ("stm:ctx1", "L1", "stm 前缀"),
        ("session:abc", "L1", "session 前缀"),
        ("abc123", "L1", "短 ASCII key（6字符）"),
        ("usr_id", "L1", "短 ASCII key（6字符）"),
        # ── 中文短句 → L3（不走 L1，因为含 CJK）──
        ("用户偏好", "L3", "中文短句含语义词'偏好'"),
        ("知识库", "L3", "中文短句含语义词'知识'"),
        ("短句", "L3", "中文短句无关键词，L3 兜底"),
        # ── 中文含时间词 → L2 ──
        ("最近的记录", "L2", "含时间词'最近'"),
        ("上次的对话", "L2", "含时间词'上次'"),
        ("今天做了什么", "L2", "含时间词'今天'+操作词'做了'"),
        # ── 英文含时间词 → L2 ──
        ("recent activity", "L2", "含时间词'recent'"),
        ("last operation", "L2", "含时间词'last'+操作词'operation'"),
        # ── 英文含语义词 → L3 ──
        ("user prefer theme", "L3", "含语义词'prefer'"),
        ("knowledge about ai", "L3", "含语义词'knowledge'+'about'"),
        # ── 长查询 → L3 ──
        ("这是一个比较长的查询句子", "L3", "长度>=12 字符"),
    ])
    def test_classify_accuracy(self, query, expected_tier, desc):
        """验证各种 Mock 数据的路由判定准确性"""
        router = MemoryRouter(default_adapter=MockMemoryInterface())
        tier, reason = router._classify_tier(query)
        assert tier == expected_tier, (
            f"[{desc}] query='{query}' 期望 {expected_tier} 但得到 {tier}（原因: {reason}）"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("query,expected_source,desc", [
        ("stm:ctx1", "short_term", "英文 stm 前缀 → L1 STM 查找"),
        ("abc", "short_term", "英文短 key → L1 STM 查找（命中）"),
        ("最近的记录", "holographic", "中文时间词 → L2 Holographic 搜索"),
        ("用户偏好", "long_term", "中文语义词 → L3 LongTerm 搜索"),
    ])
    async def test_route_accuracy(self, query, expected_source, desc):
        """验证端到端路由结果来源准确性"""
        router, stm, holo, ltm = _make_router_with_tiers()
        # 确保 STM 有 "abc" 这个 key
        stm._store["abc"] = "短key值"
        results = await router.route_tier(query)
        if results:
            assert results[0].source == expected_source, (
                f"[{desc}] query='{query}' 期望 source={expected_source} "
                f"但得到 source={results[0].source}"
            )
