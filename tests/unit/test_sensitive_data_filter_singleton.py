"""sensitive_data_filter 单例迁移单元测试

覆盖：
- 单例行为：唯一性、注册、reset/GC/幂等、__all__ 导出
- 过滤功能冒烟：password 脱敏、递归过滤、便捷函数走单例
- 并发首次初始化、fallback 行为
"""
import gc
import threading
import weakref

import pytest

import agent.utils.sensitive_data_filter as module
from agent.utils.sensitive_data_filter import (
    SensitiveDataFilter,
    filter_sensitive_data,
    get_default_filter,
    mask_ip,
)
from agent.utils.singleton_manager import get_singleton, is_initialized


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后重置单例，保证测试隔离"""
    module.reset_default_filter()
    yield
    module.reset_default_filter()


class TestDefaultFilterSingleton:
    """单例行为测试"""

    def test_get_default_filter_returns_same_instance(self):
        a = get_default_filter()
        b = get_default_filter()
        assert a is b

    def test_registers_in_singleton_manager(self):
        get_default_filter()
        assert is_initialized("sensitive_data_filter")

    def test_singleton_manager_channel_returns_same_instance(self):
        f = get_default_filter()
        assert get_singleton("sensitive_data_filter") is f

    def test_factory_returns_default_filter(self):
        assert isinstance(module._create_default_filter(), SensitiveDataFilter)

    def test_reset_returns_new_instance(self):
        first = get_default_filter()
        module.reset_default_filter()
        second = get_default_filter()
        assert first is not second

    def test_reset_releases_instance_for_gc(self):
        ref = weakref.ref(get_default_filter())
        module.reset_default_filter()
        gc.collect()
        assert ref() is None

    def test_reset_idempotent_when_not_initialized(self):
        module.reset_default_filter()
        module.reset_default_filter()

    def test_reset_exported_in_all(self):
        assert "reset_default_filter" in module.__all__


class TestDefaultFilterFunctionality:
    """过滤功能冒烟测试"""

    def test_filter_sensitive_data_masks_password(self):
        """filter_sensitive_data 脱敏敏感字段"""
        result = filter_sensitive_data({"username": "alice", "password": "secret123"})
        assert result["username"] == "alice"
        assert result["password"] == module.REDACTED_VALUE

    def test_recursive_dict_filter(self):
        """嵌套 dict 递归过滤"""
        data = {"user": {"token": "abc123", "age": 30}}
        result = get_default_filter().filter(data)
        assert result["user"]["token"] == module.REDACTED_VALUE
        assert result["user"]["age"] == 30

    def test_mask_ip(self):
        """mask_ip 掩码 IP"""
        assert mask_ip("192.168.1.10") == "192.168.xxx.xxx"

    def test_plain_text_through_singleton(self):
        """普通文本无敏感信息时不改动"""
        text = "hello world"
        assert get_default_filter().mask(text) == text

    def test_filter_returns_dict_type(self):
        """filter 返回 dict 类型"""
        result = filter_sensitive_data({"a": 1})
        assert isinstance(result, dict)


class TestDefaultFilterConcurrency:
    """并发场景测试"""

    def test_concurrent_first_get_initializes_once(self):
        """多线程并发首次 get 只构造一个实例（双检锁）"""
        orig_cls = module.SensitiveDataFilter
        created = []

        class CountingFilter(orig_cls):
            def __init__(self, *args, **kwargs):
                created.append(1)
                super().__init__(*args, **kwargs)

        module.SensitiveDataFilter = CountingFilter
        try:
            results = []
            errors = []
            barrier = threading.Barrier(8)

            def worker():
                barrier.wait()
                try:
                    results.append(get_default_filter())
                except Exception as e:  # pragma: no cover
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors
            assert len(created) == 1, f"应只构造一次，实际 {len(created)} 次"
            assert all(r is results[0] for r in results)
        finally:
            module.SensitiveDataFilter = orig_cls

    def test_concurrent_get_after_init_returns_same_instance(self):
        get_default_filter()
        instances = []

        def worker():
            instances.append(get_default_filter())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(i is instances[0] for i in instances)


class TestDefaultFilterFallback:
    """SingletonManager 不可用时的 fallback 行为"""

    def test_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        a = get_default_filter()
        b = get_default_filter()
        assert a is b

    def test_fallback_reset_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_default_filter()
        module.reset_default_filter()
        second = get_default_filter()
        assert first is not second

    def test_fallback_filter_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        result = filter_sensitive_data({"password": "x"})
        assert result["password"] == module.REDACTED_VALUE
