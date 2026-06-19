"""
Core 模块测试 - pytest 格式
验证统一抽象层是否正常工作

当前仅保留 registry 模块测试（其他模块已清理）
"""
import pytest
import logging

from core.registry import SimpleRegistry

logger = logging.getLogger(__name__)


class TestSimpleRegistry:
    """测试简单注册器"""

    @pytest.fixture
    def registry(self):
        """创建空的注册器"""
        return SimpleRegistry()

    def test_register_and_get(self, registry):
        """测试注册和获取"""
        def test_func():
            return "test"

        registry.register("test", test_func)
        assert registry.get("test") is test_func


@pytest.mark.p0
@pytest.mark.unit
def test_all_core_functionality():
    """综合测试 - P0 优先级 - 简化版本"""
    # 注册器测试
    registry = SimpleRegistry()
    registry.register("key", lambda: 42)
    assert registry.get("key")() == 42

    logger.info("✅ 所有 Core 模块测试通过!")
