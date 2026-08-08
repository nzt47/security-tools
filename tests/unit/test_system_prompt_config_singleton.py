"""system_prompt_config 单例迁移单元测试

覆盖：单例唯一性、重置（新实例 + GC 回收 + 幂等）、测试隔离场景
（重置清除陈旧配置缓存）、并发首次初始化、SingletonManager 不可用 fallback。
"""
import gc
import json
import threading
import weakref

import pytest

import agent.system_prompt_config as module
from agent.utils.singleton_manager import is_initialized


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后重置单例，保证测试隔离"""
    module.reset_system_prompt_manager()
    yield
    module.reset_system_prompt_manager()


class TestSystemPromptConfigSingleton:
    """单例行为测试"""

    def test_get_manager_returns_same_instance(self):
        a = module.get_manager()
        b = module.get_manager()
        assert a is b

    def test_get_manager_registers_in_singleton_manager(self):
        module.get_manager()
        assert is_initialized("system_prompt_manager")

    def test_reset_returns_new_instance(self):
        first = module.get_manager()
        module.reset_system_prompt_manager()
        second = module.get_manager()
        assert first is not second

    def test_reset_releases_instance_for_gc(self):
        """重置后实例可被 GC 回收"""
        ref = weakref.ref(module.get_manager())
        module.reset_system_prompt_manager()
        gc.collect()
        assert ref() is None

    def test_reset_idempotent_when_not_initialized(self):
        module.reset_system_prompt_manager()
        module.reset_system_prompt_manager()


class TestTestIsolation:
    """测试隔离场景：重置单例清除陈旧配置缓存"""

    @staticmethod
    def _write_config(tmp_path, enabled):
        """写入带指定 lifetrace 开关的临时配置文件"""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({
            "version": 2,
            "sections": {"lifetrace": {"enabled": enabled}},
        }, ensure_ascii=False), encoding="utf-8")
        return str(cfg_file)

    def test_reset_clears_stale_cache(self, monkeypatch, tmp_path):
        """重置后重新加载配置，清除旧实例的缓存值"""
        monkeypatch.setattr(module, "CONFIG_FILE", self._write_config(tmp_path, True))
        assert module.is_section_enabled("lifetrace", default=False) is True

        # 修改配置文件：enabled 变 False
        monkeypatch.setattr(module, "CONFIG_FILE", self._write_config(tmp_path, False))
        # 未重置：命中旧实例缓存，仍返回旧值
        assert module.is_section_enabled("lifetrace", default=False) is True

        # 重置单例：新实例重新加载，返回新值
        module.reset_system_prompt_manager()
        assert module.is_section_enabled("lifetrace", default=False) is False

    def test_reset_between_queries_provides_clean_state(self, monkeypatch, tmp_path):
        """两次查询之间重置，均从干净状态加载"""
        monkeypatch.setattr(module, "CONFIG_FILE", self._write_config(tmp_path, True))
        module.reset_system_prompt_manager()
        assert module.is_section_enabled("lifetrace", default=False) is True
        module.reset_system_prompt_manager()
        assert module.is_section_enabled("lifetrace", default=False) is True

    def test_is_section_enabled_returns_default_when_missing(self, monkeypatch, tmp_path):
        """配置缺失节时返回 default"""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"version": 2, "sections": {}}), encoding="utf-8")
        monkeypatch.setattr(module, "CONFIG_FILE", str(cfg_file))
        assert module.is_section_enabled("persona", default=False) is False


class TestSystemPromptConfigConcurrency:
    """并发场景测试"""

    def test_concurrent_first_get_initializes_once(self):
        """多线程并发首次 get_manager 只构造一个实例（双重检查锁）"""
        orig_cls = module.SystemPromptConfigManager
        created = []

        class CountingManager(orig_cls):
            def __init__(self, *args, **kwargs):
                created.append(1)
                super().__init__(*args, **kwargs)

        module.SystemPromptConfigManager = CountingManager
        try:
            results = []
            errors = []
            barrier = threading.Barrier(8)

            def worker():
                barrier.wait()
                try:
                    results.append(module.get_manager())
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
            module.SystemPromptConfigManager = orig_cls

    def test_concurrent_get_after_init_returns_same_instance(self):
        module.get_manager()
        instances = []

        def worker():
            instances.append(module.get_manager())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(i is instances[0] for i in instances)


class TestSystemPromptConfigFallback:
    """SingletonManager 不可用时的 fallback 行为"""

    def test_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        a = module.get_manager()
        b = module.get_manager()
        assert a is b

    def test_fallback_reset_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = module.get_manager()
        module.reset_system_prompt_manager()
        second = module.get_manager()
        assert first is not second
