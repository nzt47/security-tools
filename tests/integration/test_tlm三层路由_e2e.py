"""[TLM] 三层路由端到端集成测试

验证 query → _classify_tier → route_tier → 对应适配器 → MemoryResult 完整链路。
- L1 用真实 ShortTermMemory（内存 LRU，无外部依赖）
- L3 用真实 LongTermMemory（临时 SQLite，tmp_path）
- L2 用 AsyncMock（HolographicAdapter 初始化涉及 FTS5 建表，用 mock 验证路由分发逻辑）
"""

import pytest
from unittest.mock import AsyncMock

from agent.memory.router import MemoryRouter
from agent.memory.short_term_memory import ShortTermMemory
from agent.memory.long_term_memory import LongTermMemory
from agent.memory.base import MemoryResult


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def stm():
    """真实 ShortTermMemory（default_ttl=0 永不过期）"""
    return ShortTermMemory(max_size=100, default_ttl=0)


@pytest.fixture
def ltm(tmp_path):
    """真实 LongTermMemory（临时 SQLite）"""
    return LongTermMemory(db_path=str(tmp_path / "e2e_ltm.db"))


@pytest.fixture
def l2_adapter():
    """L2 适配器 mock（HolographicAdapter 初始化涉及 FTS5 建表，用 mock 验证路由分发）

    注：如需替换为真实 HolographicAdapter，构造 HolographicAdapter(db_path=tmp_path/"holo.db")
    并预置数据即可，route_tier 调用 adapter.search(key, top_k) 的接口契约不变。
    """
    adapter = AsyncMock()

    async def _search(key, top_k=5):
        return [MemoryResult(
            content=f"L2匹配: {key}",
            confidence=0.8,
            source="holographic",
            metadata={"key": "h2_result", "importance": 3},
        )]

    adapter.search = _search
    return adapter


@pytest.fixture
def router(stm, ltm, l2_adapter):
    """注册三层适配器的 MemoryRouter"""
    r = MemoryRouter()
    r.register_tier("L1", stm)
    r.register_tier("L2", l2_adapter)
    r.register_tier("L3", ltm)
    return r


# ═══════════════════════════════════════════════════════════════
# 端到端测试
# ═══════════════════════════════════════════════════════════════

class TestTLM三层路由E2E:
    """端到端路由测试：query → 判定 → 路由 → 适配器 → MemoryResult"""

    # ── L1 路径 ──

    @pytest.mark.asyncio
    async def test_L1_显式前缀key查找(self, router, stm):
        """场景1: stm: 前缀 → L1 → STM.get → MemoryResult"""
        await stm.save("stm:session1", "会话1的内容")
        results = await router.route_tier("stm:session1")
        assert len(results) == 1
        assert results[0].source == "short_term"
        assert results[0].content == "会话1的内容"
        assert results[0].metadata["tier"] == "L1"

    @pytest.mark.asyncio
    async def test_L1_纯ASCII_key(self, router, stm):
        """场景2: 纯 ASCII 短 key → L1"""
        await stm.save("abc123", "ASCII值")
        results = await router.route_tier("abc123")
        assert len(results) == 1
        assert results[0].source == "short_term"
        assert results[0].content == "ASCII值"

    # ── L2 路径 ──

    @pytest.mark.asyncio
    async def test_L2_时间词触发(self, router, l2_adapter):
        """场景3: 含时间词'最近' → L2 → HolographicAdapter.search"""
        results = await router.route_tier("最近的操作记录")
        assert len(results) >= 1
        assert results[0].source == "holographic"
        assert results[0].metadata["tier"] == "L2"

    @pytest.mark.asyncio
    async def test_L2_操作词触发(self, router):
        """场景4: 含操作词'做了' → L2（用不含时间词的 query 避免先命中时间词）"""
        results = await router.route_tier("做了什么操作")
        assert len(results) >= 1
        assert results[0].metadata["tier"] == "L2"

    # ── L3 路径 ──

    @pytest.mark.asyncio
    async def test_L3_语义词触发(self, router, ltm):
        """场景5: 含语义词'偏好' → L3 → LTM.search"""
        await ltm.save("pref1", "用户偏好：深色主题", tags=["preference"])
        results = await router.route_tier("用户偏好")
        assert len(results) >= 1
        assert results[0].source == "long_term_memory"
        assert results[0].metadata["tier"] == "L3"
        assert "偏好" in str(results[0].content)

    @pytest.mark.asyncio
    async def test_L3_兜底(self, router, ltm):
        """场景6: 短句不命中 L1/L2 → L3 兜底"""
        await ltm.save("k1", "短句内容")
        results = await router.route_tier("短句")
        assert len(results) >= 1
        assert results[0].metadata["tier"] == "L3"

    @pytest.mark.asyncio
    async def test_L3_keyword搜索(self, router, ltm):
        """场景7: L3 keyword 搜索（route_tier 不支持 query_embedding，semantic 见专用测试）"""
        await ltm.save("doc1", "机器学习基础", importance=4)
        await ltm.save("doc2", "深度学习进阶", importance=4)
        results = await router.route_tier("机器学习", top_k=5)
        assert len(results) >= 1
        assert results[0].metadata["tier"] == "L3"
        # 按 importance DESC 排序
        assert results[0].metadata.get("importance", 0) >= 3

    # ── 显式 tier 覆盖 ──

    @pytest.mark.asyncio
    async def test_显式tier覆盖自动判定(self, router, ltm, stm):
        """场景8: query='abc123' 自动判定 L1，但显式 tier='L3' 强制走 L3"""
        await stm.save("abc123", "STM中的值")
        await ltm.save("abc123", "LTM中包含abc123的内容")  # content 含 query 关键词
        results = await router.route_tier("abc123", tier="L3")
        # 显式 L3 → 走 LTM.search，不走 STM.get
        assert all(r.metadata["tier"] == "L3" for r in results)
        assert len(results) >= 1

    # ── 边界情况 ──

    @pytest.mark.asyncio
    async def test_空query返回空列表(self, router):
        """场景9: 空字符串 → 返回 []"""
        results = await router.route_tier("")
        assert results == []

    @pytest.mark.asyncio
    async def test_未注册tier降级(self, router, ltm):
        """场景10: tier='L4' 无效 → 自动判定降级"""
        await ltm.save("k1", "内容")
        # tier='L4' 不在 L1/L2/L3 中，自动判定
        results = await router.route_tier("内容", tier="L4")
        # 不抛异常，返回结果（自动判定为 L3）
        assert isinstance(results, list)

    # ── 边界情况补充（显式 tier=L3 覆盖验证）──

    @pytest.mark.asyncio
    async def test_L3显式_无匹配数据返回空列表(self, router, ltm):
        """边界1: tier='L3' + LTM 无匹配 → 返回空列表（不抛异常）"""
        await ltm.save("doc1", "完全不相关的内容")
        results = await router.route_tier("不存在的关键词", tier="L3")
        assert results == []

    @pytest.mark.asyncio
    async def test_L3显式_tier小写也能工作(self, router, ltm):
        """边界2: tier='l3' 小写 → 正常路由到 L3"""
        await ltm.save("doc1", "测试内容", importance=4)
        results = await router.route_tier("测试", tier="l3")
        assert len(results) >= 1
        assert results[0].metadata["tier"] == "L3"

    @pytest.mark.asyncio
    async def test_L3显式_adapter抛异常返回空列表(self, router, ltm):
        """边界3: tier='L3' + adapter.search 抛异常 → 返回空列表（不向上抛）"""
        # 用 mock 替换 L3 适配器，让 search 抛异常
        from unittest.mock import AsyncMock
        bad_adapter = AsyncMock()
        bad_adapter.search = AsyncMock(side_effect=RuntimeError("模拟数据库损坏"))
        router._tier_adapters["L3"] = bad_adapter
        results = await router.route_tier("任意", tier="L3")
        assert results == []  # 异常被捕获，返回空列表

    @pytest.mark.asyncio
    async def test_L3未注册降级到default(self, tmp_path):
        """边界4: tier='L3' + L3 适配器未注册 → 降级到 default 适配器（不抛异常）

        构造函数保证 default 永不为 None（HolographicAdapter()），
        所以 L3 未注册时降级到 default.search，不会 AttributeError。
        """
        from agent.memory.router import MemoryRouter
        router = MemoryRouter()  # 不注册任何 tier 适配器
        # default 是 HolographicAdapter()，search 应正常工作
        results = await router.route_tier("任意查询", tier="L3")
        assert isinstance(results, list)  # 不抛异常即可


# ═══════════════════════════════════════════════════════════════
# _classify_tier 单元断言（配合 e2e 验证判定准确性）
# ═══════════════════════════════════════════════════════════════

class TestClassifyTier准确性:
    """验证 _classify_tier 的判定逻辑（e2e 的前置条件）"""

    def test_L1_stm前缀(self, router):
        tier, reason = router._classify_tier("stm:abc")
        assert tier == "L1"
        assert "stm" in reason or "前缀" in reason

    def test_L1_session前缀(self, router):
        tier, reason = router._classify_tier("session:xyz")
        assert tier == "L1"

    def test_L1_纯ASCII短key(self, router):
        tier, reason = router._classify_tier("abc123")
        assert tier == "L1"
        assert "ASCII" in reason

    def test_L1_中文短key不命中(self, router):
        """'用户偏好' 含中文 → 不应判定为 L1"""
        tier, _ = router._classify_tier("用户偏好")
        assert tier != "L1"

    def test_L2_时间词最近(self, router):
        tier, reason = router._classify_tier("最近的记录")
        assert tier == "L2"
        assert "时间词" in reason

    def test_L2_操作词做了(self, router):
        tier, reason = router._classify_tier("做了什么操作")
        assert tier == "L2"
        assert "操作词" in reason

    def test_L3_语义词偏好(self, router):
        tier, reason = router._classify_tier("用户偏好")
        assert tier == "L3"
        assert "语义词" in reason

    def test_L3_兜底(self, router):
        tier, reason = router._classify_tier("短句")
        assert tier == "L3"
        assert "兜底" in reason

    def test_L3_长查询(self, router):
        """长度 >= 12 → L3"""
        tier, reason = router._classify_tier("这是一个很长的查询字符串")
        assert tier == "L3"
        assert "长查询" in reason or "语义词" in reason or "兜底" in reason
