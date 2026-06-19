"""
Core 模块全面测试 - pytest 格式
验证统一抽象层所有功能是否正常工作

当前仅保留 registry 模块测试（其他模块已清理）
"""
import pytest
import logging
from unittest.mock import MagicMock

from core.registry import (
    SimpleRegistry,
    CallbackRegistry,
    TypeRegistry,
    register,
    BaseRegistry
)

logger = logging.getLogger(__name__)


class TestRegistry:
    """测试注册表类"""

    def test_base_registry_is_abstract(self):
        """测试BaseRegistry是抽象类"""
        with pytest.raises(TypeError):
            BaseRegistry()

    def test_simple_registry_has(self):
        """测试SimpleRegistry的has方法"""
        registry = SimpleRegistry()
        registry.register("key", "value")
        assert registry.has("key")
        assert not registry.has("nonexistent")

    def test_simple_registry_list(self):
        """测试SimpleRegistry的list方法"""
        registry = SimpleRegistry()
        registry.register("a", 1)
        registry.register("b", 2)

        keys = registry.list()
        assert "a" in keys
        assert "b" in keys

    def test_simple_registry_remove(self):
        """测试SimpleRegistry的remove方法"""
        registry = SimpleRegistry()
        registry.register("key", "value")

        assert registry.remove("key")
        assert not registry.has("key")
        assert not registry.remove("nonexistent")

    def test_simple_registry_clear(self):
        """测试SimpleRegistry的clear方法"""
        registry = SimpleRegistry()
        registry.register("a", 1)
        registry.register("b", 2)

        registry.clear()
        assert registry.count() == 0

    def test_simple_registry_all(self):
        """测试SimpleRegistry的all方法"""
        registry = SimpleRegistry()
        registry.register("a", 1)

        all_items = registry.all()
        assert all_items == {"a": 1}

    def test_simple_registry_count(self):
        """测试SimpleRegistry的count方法"""
        registry = SimpleRegistry()
        assert registry.count() == 0

        registry.register("a", 1)
        assert registry.count() == 1

    def test_simple_registry_update(self):
        """测试SimpleRegistry的update方法"""
        registry = SimpleRegistry()
        registry.register("a", 1)

        registry.update({"b": 2, "c": 3})
        assert registry.count() == 3

    def test_callback_registry_trigger(self):
        """测试CallbackRegistry的trigger方法"""
        registry = CallbackRegistry()

        def test_func(x, y):
            return x + y

        registry.register("add", test_func)
        result = registry.trigger("add", 2, 3)
        assert result == 5

    def test_callback_registry_trigger_nonexistent(self):
        """测试触发不存在的回调"""
        registry = CallbackRegistry()
        result = registry.trigger("nonexistent")
        assert result is None

    def test_type_registry_create_instance(self):
        """测试TypeRegistry的create_instance方法"""
        registry = TypeRegistry()

        class TestClass:
            def __init__(self, value):
                self.value = value

        registry.register("TestClass", TestClass)
        instance = registry.create_instance("TestClass", value=42)
        assert isinstance(instance, TestClass)
        assert instance.value == 42

    def test_type_registry_create_nonexistent(self):
        """测试创建不存在的类型"""
        registry = TypeRegistry()
        instance = registry.create_instance("Nonexistent")
        assert instance is None

    def test_register_decorator(self):
        """测试register装饰器"""
        registry = SimpleRegistry()

        @register(registry, "my_func")
        def my_func():
            return "test"

        assert registry.get("my_func") == my_func


@pytest.mark.p0
@pytest.mark.unit
def test_core_module_import():
    """测试core模块导入"""
    from core import (
        BaseRegistry,
        SimpleRegistry,
        CallbackRegistry,
        TypeRegistry,
        register,
        __version__
    )

    assert __version__ == '0.1.0'
