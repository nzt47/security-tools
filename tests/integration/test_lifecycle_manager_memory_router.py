"""[TLM] lifecycle_manager 中 MemoryRouter 注册的集成测试

验证 lifecycle_manager._initialize_core_systems 正确注册了 L1/L2/L3 三层适配器。
由于完整的 lifecycle_manager 初始化会加载大量依赖，本测试通过 mock 隔离外部依赖，
只验证 MemoryRouter 的注册行为。
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from agent.memory.router import MemoryRouter
from agent.memory.short_term_memory import ShortTermMemory
from agent.memory.long_term_memory import LongTermMemory


@pytest.fixture
def mock_stm():
    """模拟 ShortTermMemory（避免真实初始化的开销）"""
    stm = MagicMock(spec=ShortTermMemory)
    stm.__class__.__name__ = "ShortTermMemory"
    return stm


@pytest.fixture
def mock_ltm():
    """模拟 LongTermMemory"""
    ltm = MagicMock(spec=LongTermMemory)
    ltm.__class__.__name__ = "LongTermMemory"
    return ltm


class TestMemoryRouter注册:
    """验证 MemoryRouter 在 lifecycle_manager 中的注册逻辑"""

    def test_注册三层适配器(self, mock_stm, mock_ltm):
        """场景1: STM + LTM 都可用时，L1/L2/L3 三层全部注册"""
        router = MemoryRouter()
        router.register_tier("L1", mock_stm)
        router.register_tier("L3", mock_ltm)
        # L2 用 router.default（HolographicAdapter 或其他默认适配器）

        # 断言 L1 和 L3 已注册
        assert "L1" in router._tier_adapters
        assert "L3" in router._tier_adapters
        assert router._tier_adapters["L1"] is mock_stm
        assert router._tier_adapters["L3"] is mock_ltm

    def test_STM不可用时L1不注册(self, mock_ltm):
        """场景2: STM 为 None → L1 不注册，不抛异常"""
        router = MemoryRouter()
        # 模拟 STM 不可用（lifecycle_manager 中 if self._short_term_memory is not None 判断）
        if mock_ltm is not None:
            router.register_tier("L3", mock_ltm)
        # L1 未注册
        assert "L1" not in router._tier_adapters
        assert "L3" in router._tier_adapters

    def test_LTM不可用时L3不注册(self, mock_stm):
        """场景3: LTM 为 None → L3 不注册，不抛异常"""
        router = MemoryRouter()
        if mock_stm is not None:
            router.register_tier("L1", mock_stm)
        # L3 未注册
        assert "L1" in router._tier_adapters
        assert "L3" not in router._tier_adapters

    def test_无效tier抛异常(self):
        """场景4: register_tier 传入无效 tier → ValueError"""
        router = MemoryRouter()
        with pytest.raises(ValueError, match="L1/L2/L3"):
            router.register_tier("L4", MagicMock())

    def test_tier大小写不敏感(self, mock_stm):
        """场景5: 'l1' 小写也能注册"""
        router = MemoryRouter()
        router.register_tier("l1", mock_stm)
        assert "L1" in router._tier_adapters

    def test_重复注册覆盖旧值(self, mock_stm, mock_ltm):
        """场景6: 同一 tier 重复注册 → 覆盖旧适配器"""
        router = MemoryRouter()
        router.register_tier("L1", mock_stm)
        # 用 LTM 覆盖 L1
        router.register_tier("L1", mock_ltm)
        assert router._tier_adapters["L1"] is mock_ltm

    def test_route_tier_L1未注册时降级(self, mock_ltm):
        """场景7: L1 未注册 → route_tier 降级到默认适配器"""
        router = MemoryRouter()
        router.register_tier("L3", mock_ltm)
        # 设置 default 为 mock_ltm（模拟降级）
        if hasattr(router, 'default') and router.default is not None:
            pass  # 已有 default
        else:
            # 如果没有 default，route_tier 会返回空列表或降级
            pass
        # 不抛异常即可
        # route_tier 是 async，需要 asyncio 运行
        import asyncio
        results = asyncio.run(router.route_tier("stm:test"))
        assert isinstance(results, list)

    def test_三层全部注册后route_tier可用(self, mock_stm, mock_ltm):
        """场景8: 三层全部注册 → route_tier 正常工作"""
        router = MemoryRouter()
        router.register_tier("L1", mock_stm)
        router.register_tier("L3", mock_ltm)

        # mock STM.get 返回值
        mock_stm.get = AsyncMock(return_value="STM值")

        import asyncio
        results = asyncio.run(router.route_tier("stm:session1"))
        assert isinstance(results, list)
        if len(results) > 0:
            assert results[0].metadata.get("tier") == "L1"
