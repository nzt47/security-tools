"""core 模块边界测试 — BT-007

覆盖 core/registry.py 的边界条件：
- 空注册表行为（empty）
- 非法输入（invalid）：None/空字符串名称、非可调用对象、错误参数
- None 值处理（null）
- 超时异常传播（timeout）
- 极值（大量项目、超长名称、大对象）
- 并发安全

被测模块：core/registry.py（项目根目录的 core/ 包）
关键 API：
- BaseRegistry (ABC)
- SimpleRegistry: register/get/has/list/remove/clear/all/count/update
- CallbackRegistry: trigger
- TypeRegistry: create_instance
- register: 装饰器

【可观测性约束】
- 边界显性化：每个边界条件显式断言
"""

import logging
import threading
import time
from typing import Any

import pytest

# core 在项目根目录
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.registry import (
    BaseRegistry,
    SimpleRegistry,
    CallbackRegistry,
    TypeRegistry,
    register,
)


logger = logging.getLogger(__name__)


# ============================================================================
#  fixtures
# ============================================================================


@pytest.fixture
def empty_registry():
    """空注册表"""
    return SimpleRegistry()


@pytest.fixture
def populated_registry():
    """已填充的注册表"""
    r = SimpleRegistry()
    r.register("item1", "value1")
    r.register("item2", "value2")
    r.register("item3", "value3")
    return r


@pytest.fixture
def callback_registry():
    """回调注册表"""
    return CallbackRegistry()


@pytest.fixture
def type_registry():
    """类型注册表"""
    return TypeRegistry()


# ============================================================================
#  空注册表边界测试（empty 场景）
# ============================================================================


class TestEmptyBoundary:
    """空注册表边界行为测试"""

    def test_empty_registry_get_returns_default(self, empty_registry):
        """空注册表 get 返回默认值"""
        result = empty_registry.get("nonexistent")
        assert result is None
        result = empty_registry.get("nonexistent", default="fallback")
        assert result == "fallback"

    def test_empty_registry_list_returns_empty_list(self, empty_registry):
        """空注册表 list 返回空列表"""
        result = empty_registry.list()
        assert result == []
        assert isinstance(result, list)

    def test_empty_registry_count_zero(self, empty_registry):
        """空注册表 count 为 0"""
        assert empty_registry.count() == 0

    def test_empty_registry_has_returns_false(self, empty_registry):
        """空注册表 has 返回 False"""
        assert empty_registry.has("anything") is False

    def test_empty_registry_remove_returns_false(self, empty_registry):
        """空注册表 remove 返回 False"""
        assert empty_registry.remove("nonexistent") is False

    def test_empty_registry_all_returns_empty_dict(self, empty_registry):
        """空注册表 all 返回空字典"""
        result = empty_registry.all()
        assert result == {}
        assert isinstance(result, dict)

    def test_empty_registry_clear_no_error(self, empty_registry):
        """空注册表 clear 不报错"""
        empty_registry.clear()
        assert empty_registry.count() == 0

    def test_empty_callback_registry_trigger_returns_none(self, callback_registry):
        """空回调注册表 trigger 返回 None"""
        result = callback_registry.trigger("nonexistent")
        assert result is None

    def test_empty_type_registry_create_instance_returns_none(self, type_registry):
        """空类型注册表 create_instance 返回 None"""
        result = type_registry.create_instance("Nonexistent")
        assert result is None

    def test_empty_registry_update_empty_dict(self, empty_registry):
        """空注册表 update 空字典无副作用"""
        empty_registry.update({})
        assert empty_registry.count() == 0

    def test_empty_registry_get_with_explicit_none_default(self, empty_registry):
        """空注册表 get 显式 None 默认值"""
        result = empty_registry.get("missing", default=None)
        assert result is None


# ============================================================================
#  非法输入测试（invalid 场景）
# ============================================================================


class TestInvalidInput:
    """非法输入测试"""

    def test_invalid_name_none_register(self, empty_registry):
        """None 作为名称注册 — 不报错但可检索"""
        empty_registry.register(None, "value")
        # None 作为键可以被注册
        assert empty_registry.has(None) is True

    def test_invalid_name_empty_string_register(self, empty_registry):
        """空字符串作为名称注册"""
        empty_registry.register("", "value")
        assert empty_registry.has("") is True
        assert empty_registry.get("") == "value"

    def test_invalid_get_with_none_name(self, populated_registry):
        """None 作为名称获取"""
        result = populated_registry.get(None)
        assert result is None

    def test_invalid_has_with_empty_name(self, populated_registry):
        """空字符串检查存在性"""
        assert populated_registry.has("") is False

    def test_invalid_remove_nonexistent(self, populated_registry):
        """移除不存在的项目返回 False"""
        assert populated_registry.remove("nonexistent") is False
        # 原有项目不受影响
        assert populated_registry.count() == 3

    def test_invalid_callback_not_callable(self, callback_registry):
        """注册非可调用对象到 CallbackRegistry — trigger 时抛 TypeError"""
        callback_registry.register("not_callable", "string_value")
        with pytest.raises(TypeError):
            callback_registry.trigger("not_callable")

    def test_invalid_type_not_type(self, type_registry):
        """注册非类型对象到 TypeRegistry — create_instance 时抛 TypeError"""
        type_registry.register("not_a_type", "string_value")
        with pytest.raises(TypeError):
            type_registry.create_instance("not_a_type")

    def test_invalid_create_instance_with_wrong_args(self, type_registry):
        """错误参数创建实例抛 TypeError"""

        class NeedArg:
            def __init__(self, required_arg):
                self.required_arg = required_arg

        type_registry.register("NeedArg", NeedArg)
        with pytest.raises(TypeError):
            type_registry.create_instance("NeedArg")  # 缺少必需参数

    def test_invalid_update_with_none(self, empty_registry):
        """None 作为更新字典抛 AttributeError"""
        with pytest.raises((AttributeError, TypeError)):
            empty_registry.update(None)

    def test_invalid_trigger_with_wrong_args(self, callback_registry):
        """错误参数触发回调抛 TypeError"""

        def func(x, y):
            return x + y

        callback_registry.register("add", func)
        with pytest.raises(TypeError):
            callback_registry.trigger("add")  # 缺少参数

    def test_invalid_register_decorator_with_invalid_registry(self):
        """装饰器使用无效注册表"""
        with pytest.raises(AttributeError):
            @register("not_a_registry", "name")
            def func():
                pass


# ============================================================================
#  None 值处理测试（null 场景）
# ============================================================================


class TestNullBoundary:
    """None 值处理测试"""

    def test_null_default_value_in_get(self, populated_registry):
        """get 的默认值为 None"""
        result = populated_registry.get("missing", default=None)
        assert result is None

    def test_null_item_register(self, empty_registry):
        """注册 None 值"""
        empty_registry.register("null_item", None)
        assert empty_registry.has("null_item") is True
        assert empty_registry.get("null_item") is None

    def test_null_name_register(self, empty_registry):
        """None 作为名称"""
        empty_registry.register(None, "value")
        assert empty_registry.has(None) is True

    def test_null_callback_trigger_returns_none(self, callback_registry):
        """trigger 不存在的回调返回 None"""
        result = callback_registry.trigger("nonexistent_callback", "arg1", "arg2")
        assert result is None

    def test_null_create_instance_returns_none(self, type_registry):
        """create_instance 不存在的类型返回 None"""
        result = type_registry.create_instance("NullType", "arg")
        assert result is None

    def test_null_get_returns_none_without_default(self, empty_registry):
        """get 不传 default 时返回 None"""
        result = empty_registry.get("missing")
        assert result is None

    def test_null_item_in_list(self, empty_registry):
        """None 值项目出现在 list 中"""
        empty_registry.register("key", None)
        assert "key" in empty_registry.list()


# ============================================================================
#  超时异常传播测试（timeout 场景）
# ============================================================================


class TestTimeoutBoundary:
    """超时/异常传播测试"""

    def test_timeout_callback_raises_timeout_error(self, callback_registry):
        """回调抛 TimeoutError 时向上传播"""

        def slow_callback():
            raise TimeoutError("Operation timed out")

        callback_registry.register("slow", slow_callback)
        with pytest.raises(TimeoutError):
            callback_registry.trigger("slow")

    def test_timeout_long_running_callback(self, callback_registry):
        """长时间运行的回调正常完成"""
        sleep_time = 0.01

        def long_callback():
            time.sleep(sleep_time)
            return "completed"

        callback_registry.register("long", long_callback)
        start = time.time()
        result = callback_registry.trigger("long")
        duration = time.time() - start
        assert result == "completed"
        assert duration >= sleep_time

    def test_timeout_create_instance_raises_timeout(self, type_registry):
        """create_instance 构造函数抛 TimeoutError 时传播"""

        class TimeoutClass:
            def __init__(self):
                raise TimeoutError("Init timed out")

        type_registry.register("TimeoutClass", TimeoutClass)
        with pytest.raises(TimeoutError):
            type_registry.create_instance("TimeoutClass")

    def test_timeout_trigger_with_timeout_exception(self, callback_registry):
        """trigger 传播自定义超时异常"""

        class CustomTimeoutError(Exception):
            pass

        def callback():
            raise CustomTimeoutError("Custom timeout")

        callback_registry.register("custom_timeout", callback)
        with pytest.raises(CustomTimeoutError):
            callback_registry.trigger("custom_timeout")


# ============================================================================
#  极值测试（extreme 场景）
# ============================================================================


class TestExtremeValues:
    """极值与极端场景测试"""

    def test_extreme_many_items_register(self, empty_registry):
        """大量项目注册"""
        for i in range(1000):
            empty_registry.register(f"item_{i}", i)
        assert empty_registry.count() == 1000
        assert empty_registry.get("item_999") == 999

    def test_extreme_long_name(self, empty_registry):
        """超长名称"""
        long_name = "x" * 10000
        empty_registry.register(long_name, "value")
        assert empty_registry.has(long_name) is True
        assert empty_registry.get(long_name) == "value"

    def test_extreme_large_item(self, empty_registry):
        """大对象注册"""
        large_item = {"data": list(range(10000))}
        empty_registry.register("large", large_item)
        result = empty_registry.get("large")
        assert len(result["data"]) == 10000

    def test_extreme_update_large_dict(self, empty_registry):
        """大批量 update"""
        large_update = {f"key_{i}": i for i in range(500)}
        empty_registry.update(large_update)
        assert empty_registry.count() == 500

    def test_extreme_repeated_register_same_key(self, empty_registry):
        """重复注册同一键覆盖"""
        for i in range(100):
            empty_registry.register("same_key", i)
        assert empty_registry.count() == 1
        assert empty_registry.get("same_key") == 99

    def test_extreme_repeated_remove_same_key(self, populated_registry):
        """重复移除同一键"""
        assert populated_registry.remove("item1") is True
        assert populated_registry.remove("item1") is False  # 第二次移除失败
        assert populated_registry.remove("item1") is False  # 第三次也失败


# ============================================================================
#  并发安全测试
# ============================================================================


class TestConcurrencySafety:
    """并发安全测试"""

    def test_concurrent_register(self, empty_registry):
        """并发注册不同键"""
        errors = []

        def worker(worker_id):
            try:
                for i in range(100):
                    empty_registry.register(f"worker_{worker_id}_item_{i}", i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(w,)) for w in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert empty_registry.count() == 1000

    def test_concurrent_get(self, populated_registry):
        """并发获取"""
        results = []
        errors = []

        def worker():
            try:
                for _ in range(100):
                    val = populated_registry.get("item1")
                    results.append(val)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 1000
        assert all(r == "value1" for r in results)

    def test_concurrent_remove_different_keys(self):
        """并发移除不同键"""
        registry = SimpleRegistry()
        for i in range(100):
            registry.register(f"item_{i}", i)

        errors = []

        def worker(start):
            try:
                for i in range(start, start + 10):
                    registry.remove(f"item_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(w * 10,)) for w in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert registry.count() == 0


# ============================================================================
#  装饰器边界测试
# ============================================================================


class TestRegisterDecorator:
    """register 装饰器边界测试"""

    def test_register_decorator_with_none_name(self):
        """None 名称时使用函数名"""
        registry = SimpleRegistry()

        @register(registry, None)
        def my_func():
            return "test"

        assert registry.get("my_func") == my_func

    def test_register_decorator_default_name(self):
        """不传 name 时使用函数名"""
        registry = SimpleRegistry()

        @register(registry)
        def my_func():
            return "test"

        assert registry.get("my_func") == my_func

    def test_register_decorator_overwrite(self):
        """装饰器覆盖同名注册"""
        registry = SimpleRegistry()

        @register(registry, "name")
        def func1():
            return "first"

        @register(registry, "name")
        def func2():
            return "second"

        assert registry.get("name") == func2
        assert registry.get("name")() == "second"

    def test_register_decorator_multiple_registries(self):
        """同一函数注册到多个注册表"""
        r1 = SimpleRegistry()
        r2 = SimpleRegistry()

        @register(r1, "func")
        @register(r2, "func")
        def my_func():
            return "test"

        assert r1.get("func") == my_func
        assert r2.get("func") == my_func

    def test_register_decorator_with_class(self):
        """装饰器注册类"""
        registry = TypeRegistry()

        @register(registry, "MyClass")
        class MyClass:
            def __init__(self, val):
                self.val = val

        instance = registry.create_instance("MyClass", val=42)
        assert isinstance(instance, MyClass)
        assert instance.val == 42


# ============================================================================
#  状态管理与副本测试
# ============================================================================


class TestRegistryState:
    """注册表状态管理测试"""

    def test_registry_clear_empties_all(self, populated_registry):
        """clear 清空所有项目"""
        assert populated_registry.count() == 3
        populated_registry.clear()
        assert populated_registry.count() == 0
        assert populated_registry.list() == []

    def test_registry_all_returns_copy(self, populated_registry):
        """all 返回副本，修改不影响内部状态"""
        all_items = populated_registry.all()
        all_items["new_key"] = "new_value"
        assert populated_registry.has("new_key") is False

    def test_registry_list_returns_copy(self, populated_registry):
        """list 返回副本，修改不影响内部状态"""
        keys = populated_registry.list()
        keys.append("new_key")
        assert populated_registry.has("new_key") is False

    def test_registry_update_merges(self, populated_registry):
        """update 合并而非替换"""
        populated_registry.update({"item4": "value4", "item5": "value5"})
        assert populated_registry.count() == 5
        assert populated_registry.get("item4") == "value4"
        # 原有项保留
        assert populated_registry.get("item1") == "value1"

    def test_registry_register_overwrites(self, populated_registry):
        """register 覆盖同名键"""
        populated_registry.register("item1", "new_value")
        assert populated_registry.get("item1") == "new_value"
        assert populated_registry.count() == 3  # 数量不变

    def test_registry_update_overwrites_existing(self, populated_registry):
        """update 覆盖已存在的键"""
        populated_registry.update({"item1": "overwritten"})
        assert populated_registry.get("item1") == "overwritten"
        assert populated_registry.count() == 3


# ============================================================================
#  BaseRegistry 抽象类测试
# ============================================================================


class TestBaseRegistryAbstract:
    """BaseRegistry 抽象类测试"""

    def test_base_registry_is_abstract(self):
        """BaseRegistry 不能实例化"""
        with pytest.raises(TypeError):
            BaseRegistry()

    def test_base_registry_subclass_must_implement_all(self):
        """子类必须实现所有抽象方法"""

        class IncompleteRegistry(BaseRegistry):
            def register(self, name, item):
                pass

        with pytest.raises(TypeError):
            IncompleteRegistry()

    def test_base_registry_complete_subclass(self):
        """完整子类可实例化"""

        class CompleteRegistry(BaseRegistry):
            def __init__(self):
                self._items = {}

            def register(self, name, item):
                self._items[name] = item

            def get(self, name, default=None):
                return self._items.get(name, default)

            def has(self, name):
                return name in self._items

            def list(self):
                return list(self._items.keys())

            def remove(self, name):
                if name in self._items:
                    del self._items[name]
                    return True
                return False

        registry = CompleteRegistry()
        registry.register("key", "value")
        assert registry.get("key") == "value"
        assert registry.has("key") is True
        assert "key" in registry.list()
        assert registry.remove("key") is True


# ============================================================================
#  日志与可观测性测试
# ============================================================================


class TestRegistryLogging:
    """注册表日志测试"""

    def test_registry_init_logs_info(self, caplog):
        """初始化时输出 info 日志"""
        with caplog.at_level(logging.INFO, logger="core.registry"):
            SimpleRegistry("TestRegistry")
        assert len(caplog.records) > 0
        assert any("initialized" in r.message for r in caplog.records)

    def test_registry_clear_logs_warning(self, caplog):
        """clear 时输出 warning 日志"""
        registry = SimpleRegistry()
        registry.register("item", "value")
        with caplog.at_level(logging.WARNING, logger="core.registry"):
            registry.clear()
        assert any("cleared" in r.message for r in caplog.records)

    def test_registry_register_logs_debug(self, caplog):
        """register 时输出 debug 日志"""
        registry = SimpleRegistry()
        with caplog.at_level(logging.DEBUG, logger="core.registry"):
            registry.register("item", "value")
        assert any("Registered" in r.message for r in caplog.records)

    def test_registry_remove_logs_debug(self, caplog):
        """remove 成功时输出 debug 日志"""
        registry = SimpleRegistry()
        registry.register("item", "value")
        with caplog.at_level(logging.DEBUG, logger="core.registry"):
            registry.remove("item")
        assert any("Removed" in r.message for r in caplog.records)
