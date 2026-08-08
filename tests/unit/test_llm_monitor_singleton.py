"""llm_monitor 单例迁移单元测试

覆盖：
- 单例行为：唯一性、注册、reset/GC/幂等
- install_hooks 安装：替换 LLMService 方法、幂等、补丁函数记录逻辑
- uninstall_hooks 卸载（重点）：恢复原始方法、reset 触发 cleanup 卸载、闭包悬空引用防护
- 并发首次初始化、fallback 行为
"""
import gc
import threading
import types
import weakref

import pytest

import agent.llm_monitor as module
from agent.llm_monitor import LLMMonitor, get_monitor
from agent.utils.singleton_manager import is_initialized
from memory.llm_service import LLMService


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后重置单例，保证测试隔离（reset 触发 cleanup 卸载 hooks）"""
    module.reset_llm_monitor()
    yield
    module.reset_llm_monitor()


class TestLLMMonitorSingleton:
    """单例行为测试"""

    def test_get_monitor_returns_same_instance(self):
        a = get_monitor()
        b = get_monitor()
        assert a is b

    def test_registers_in_singleton_manager(self):
        get_monitor()
        assert is_initialized("llm_monitor")

    def test_reset_returns_new_instance(self):
        first = get_monitor()
        module.reset_llm_monitor()
        second = get_monitor()
        assert first is not second

    def test_reset_releases_instance_for_gc(self):
        ref = weakref.ref(get_monitor())
        module.reset_llm_monitor()
        gc.collect()
        assert ref() is None

    def test_reset_idempotent_when_not_initialized(self):
        module.reset_llm_monitor()
        module.reset_llm_monitor()


class TestInstallHooks:
    """install_hooks 安装逻辑测试（重点）"""

    def test_install_marks_hooks_installed(self):
        """install_hooks 后 _hooks_installed 置 True"""
        get_monitor()
        module.install_hooks()
        assert get_monitor()._hooks_installed is True

    def test_install_replaces_llm_service_methods(self):
        """install_hooks 替换 LLMService 的 _do_chat / _do_summarize / _get_client"""
        orig_chat = LLMService._do_chat
        orig_summarize = LLMService._do_summarize
        orig_get_client = LLMService._get_client
        try:
            module.install_hooks()
            assert LLMService._do_chat is not orig_chat
            assert LLMService._do_summarize is not orig_summarize
            assert LLMService._get_client is not orig_get_client
        finally:
            module.uninstall_hooks()

    def test_install_is_idempotent(self):
        """重复 install_hooks 不重复替换（_hooks_installed 短路）"""
        module.install_hooks()
        first_patch = LLMService._do_chat
        module.install_hooks()
        assert LLMService._do_chat is first_patch
        module.uninstall_hooks()

    def test_patched_do_chat_records_interaction(self):
        """补丁 _do_chat 调用后记录一次 LLM 交互"""
        orig = LLMService._do_chat
        LLMService._do_chat = (
            lambda self, messages, system_prompt="", max_tokens=1024, temperature=0.7: None
        )
        try:
            module.install_hooks()
            self_obj = types.SimpleNamespace(model="gpt-4", provider="test")
            LLMService._do_chat(
                self_obj,
                [{"role": "user", "content": "hello"}],
                system_prompt="sys",
            )
            monitor = get_monitor()
            records, total = monitor.get_records(source="chat")
            assert total == 1
            assert records[0]["model"] == "gpt-4"
            assert records[0]["provider"] == "test"
        finally:
            LLMService._do_chat = orig
            module.uninstall_hooks()

    def test_patched_do_chat_records_error_on_exception(self):
        """补丁 _do_chat 抛异常时仍记录 error 且异常向上传播"""
        orig = LLMService._do_chat

        def failing(self, messages, system_prompt="", max_tokens=1024, temperature=0.7):
            raise ValueError("boom")

        LLMService._do_chat = failing
        try:
            module.install_hooks()
            self_obj = types.SimpleNamespace(model="m", provider="p")
            with pytest.raises(ValueError, match="boom"):
                LLMService._do_chat(self_obj, [{"role": "user", "content": "x"}])
            records, total = get_monitor().get_records(source="chat")
            assert total == 1
            assert "boom" in records[0]["error"]
        finally:
            LLMService._do_chat = orig
            module.uninstall_hooks()


class TestUninstallHooks:
    """uninstall_hooks 卸载逻辑测试（重点）"""

    def test_uninstall_restores_original_methods(self):
        """uninstall_hooks 恢复 LLMService 原始方法"""
        orig_chat = LLMService._do_chat
        orig_summarize = LLMService._do_summarize
        orig_get_client = LLMService._get_client
        module.install_hooks()
        module.uninstall_hooks()
        assert LLMService._do_chat is orig_chat
        assert LLMService._do_summarize is orig_summarize
        assert LLMService._get_client is orig_get_client

    def test_uninstall_clears_original_backups(self):
        """卸载后模块级原始方法备份置 None（无残留引用）"""
        module.install_hooks()
        module.uninstall_hooks()
        assert module._orig_do_chat is None
        assert module._orig_do_summarize is None
        assert module._orig_get_client is None

    def test_uninstall_when_not_installed_is_safe(self):
        """未安装钩子时 uninstall 安全幂等"""
        module.uninstall_hooks()
        module.uninstall_hooks()

    def test_reset_triggers_hook_uninstall(self):
        """reset 触发 cleanup：钩子被卸载，方法恢复原始（闭包悬空防护）"""
        orig_chat = LLMService._do_chat
        get_monitor()
        module.install_hooks()
        assert LLMService._do_chat is not orig_chat
        module.reset_llm_monitor()
        assert LLMService._do_chat is orig_chat  # 不再悬空引用旧实例闭包

    def test_reset_after_install_new_instance_has_clean_state(self):
        """重置后新实例 _hooks_installed 为 False（可再次安装）"""
        get_monitor()
        module.install_hooks()
        module.reset_llm_monitor()
        fresh = get_monitor()
        assert fresh._hooks_installed is False
        fresh.start_hook_free = None  # 无残留标记
        module.install_hooks()
        assert get_monitor()._hooks_installed is True
        module.uninstall_hooks()

    def test_reset_idempotent_after_install(self):
        """安装后连续 reset 安全（cleanup 幂等）"""
        module.install_hooks()
        module.reset_llm_monitor()
        module.reset_llm_monitor()


class TestLLMMonitorConcurrency:
    """并发场景测试"""

    def test_concurrent_first_get_initializes_once(self):
        """多线程并发首次 get 只构造一个实例"""
        orig_cls = module.LLMMonitor
        created = []

        class CountingMonitor(orig_cls):
            def __init__(self, max_records=None):
                created.append(1)
                super().__init__(max_records)

        module.LLMMonitor = CountingMonitor
        try:
            results = []
            errors = []
            barrier = threading.Barrier(8)

            def worker():
                barrier.wait()
                try:
                    results.append(get_monitor())
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
            module.LLMMonitor = orig_cls

    def test_concurrent_get_after_init_returns_same_instance(self):
        get_monitor()
        instances = []

        def worker():
            instances.append(get_monitor())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(i is instances[0] for i in instances)


class TestLLMMonitorFallback:
    """SingletonManager 不可用时的 fallback 行为"""

    def test_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        a = get_monitor()
        b = get_monitor()
        assert a is b

    def test_fallback_reset_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_monitor()
        module.reset_llm_monitor()
        second = get_monitor()
        assert first is not second

    def test_fallback_install_uninstall_works(self, monkeypatch):
        """fallback 模式下 install/uninstall 行为一致"""
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        orig_chat = LLMService._do_chat
        module.install_hooks()
        assert LLMService._do_chat is not orig_chat
        module.uninstall_hooks()
        assert LLMService._do_chat is orig_chat
