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

#: `install_hooks()` 会劫持的三个方法 —— 模块导入时（尚无任何补丁）抓一次快照。
#: 用途见 `_cleanup_singleton` 的 ② 步：它是"原始态"的**可信来源**，
#: 而 `agent/llm_monitor.py` 的模块级 `_orig_*` 备份不可信（可能存的是补丁本身）。
_LLM_SERVICE_METHOD_SNAPSHOT = {
    _n: LLMService.__dict__.get(_n) for _n in ("_do_chat", "_do_summarize", "_get_client")
}


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后重置单例，保证测试隔离（reset 触发 cleanup 卸载 hooks）

    【不易·2026-09-20 追加：强制复原 ``_do_chat``，修掉一处真实的跨文件污染】
    仅靠 ``reset_llm_monitor()`` **不能**把类复原到"上游真实方法"，原因是本仓
    ``agent/llm_monitor.py:579`` 的 ``install_hooks()`` 会把**安装当时**的
    ``LLMService._do_chat`` 备份进模块级 ``_orig_do_chat``：

        · 用例先把类换成 lambda/failing（无论用手工赋值还是 monkeypatch）
        · ``install_hooks()`` 随即把这个 lambda 备份成 ``_orig_do_chat``
        · ``uninstall_hooks()`` 于是把它**还原回来**
        · pytest 的 ``monkeypatch`` 复原又可能被上面的 ``uninstall`` 覆盖
        ⇒ 最终类上留下**补丁残留**，污染后续所有依赖 ``_do_chat`` 的文件。

    实测（确定性，非 flaky）：
        python -m pytest tests/unit/test_llm_monitor_singleton.py \
                         tests/unit/test_tools_prompt_alignment.py -q -p no:randomly
        ⇒ 修复前 ``test_llm_service_do_chat_永远无工具故必须中和`` 失败
          （``ValueError: boom``；该用例以属主方式 ``svc._do_chat([...])`` 只传一个参数，
            而泄漏进来的补丁签名要求 ``(self, messages, ...)``）

    故本夹具在用例前后**双保险**：
      1. 先按原样 ``reset``（清单例 + 触发模块自己的 cleanup）；
      2. **再把三个被 install_hooks 劫持的方法/备份强行复原**为人话口径的"原始态"。
         ``_orig_*`` 是 ``install_hooks`` 的"我改了什么"记录，它不是可信的原始值来源
         （上一条已说明它可能存的是补丁本身）⇒ 一并置 ``None``，让下一次
         ``install_hooks`` 重新从类上取备份。

    【变易】不要在用例内依赖"记得写复原"：那正是原先出问题的形态。
    """
    module.reset_llm_monitor()
    yield
    module.reset_llm_monitor()
    # ② **强制复原被劫持的方法** —— 唯一不依赖"用例写得对"的收尾方式。
    #
    #    为什么要绕开 `uninstall_hooks()`：它从模块级 `_orig_*` 取"原始值"，
    #    而 `install_hooks()` 备份的是**安装当时**类上的方法（可能已是补丁）
    #    且 `_orig_*` 为 None 时它干脆不复原 ⇒ 两种情况下类上都可能残留补丁。
    #
    #    这里的正确性依据：`LLMService.__dict__` 里的函数对象是**类属性**，
    #    只在被显式 `setattr`（安装补丁）时才变；而 `monkeypatch` 复原也走
    #    `setattr`。故在**用例开始前**抓一次快照，是最可信的"原始态"来源 ——
    #    前提是每一次收尾都真正复原，从而保证"下一个用例开始时类上仍是原样"。
    for _name, _fn in _LLM_SERVICE_METHOD_SNAPSHOT.items():
        if _fn is not None:
            setattr(LLMService, _name, _fn)
    # ③ 清空备份记录（它是"我改了什么"的记录，不是可信原始值）⇒ 逼下次 install 重新取
    module._orig_do_chat = None
    module._orig_do_summarize = None
    module._orig_get_client = None
    # ④ 单例状态归零（reset 已做，这里防御性再置一次）
    try:
        module.get_monitor()._hooks_installed = False
    except Exception:  # noqa: BLE001 监控器不可用不该影响测试收尾
        pass


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

    def test_patched_do_chat_records_interaction(self, monkeypatch):
        """补丁 _do_chat 调用后记录一次 LLM 交互

        【不易·2026-09-20 改为 monkeypatch，修掉一处真实的跨文件污染】
        原写法是「手工赋值 + try/finally 里先复原再 uninstall」。问题在于：
        ``install_hooks()`` 会把**当时**的 ``_do_chat``（即手工塞进去的 lambda）
        备份进模块级 ``_orig_do_chat``，于是 ``uninstall_hooks()`` 会把它**还原回来**
        —— 真正把类复原到干净的只有 try 体末尾那句 ``LLMService._do_chat = orig``。
        ⇒ **一旦用例中途失败（断言不过），那条复原语句就不执行，lambda 永久留在
        ``LLMService`` 上**，后续所有依赖 ``_do_chat`` 的用例被污染。

        实测（2026-09-20，确定性）：
            python -m pytest tests/unit/test_llm_monitor_singleton.py \
                             tests/unit/test_tools_prompt_alignment.py -q -p no:randomly
            ⇒ ``test_tools_prompt_alignment.py::TestCallSitesSendConsistentRequest::
               test_llm_service_do_chat_永远无工具故必须中和`` 失败（``ValueError: boom``）
        该用例调用 ``svc._do_chat([...])``（属主方法、**只传一个参数**），
        而泄漏进来的补丁签名是 ``_patched_do_chat(self, messages, ...)``
        ⇒ 那个 dict 被当成 ``self``，第二参数成了 ``system_prompt`` ⇒ 断言全错。

        ``monkeypatch.setattr`` 由 pytest 保证在 **teardown 无条件复原**（含失败路径），
        故这是本仓推荐的写法；顺带把它移到 ``install_hooks()`` **之前**，
        语义更直白（先换掉被测方法，再让 install 基于它装补丁）。
        """
        monkeypatch.setattr(
            LLMService, "_do_chat",
            lambda self, messages, system_prompt="", max_tokens=1024, temperature=0.7: None,
        )
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
        module.uninstall_hooks()

    def test_patched_do_chat_records_error_on_exception(self, monkeypatch):
        """补丁 _do_chat 抛异常时仍记录 error 且异常向上传播

        【不易·2026-09-20】同上一用例，改为 ``monkeypatch.setattr`` —— 理由见其 docstring。
        本用例的泄漏后果尤其严重：它塞进去的 ``failing`` 每次都 ``raise ValueError("boom")``，
        一旦泄漏，后续任何调用 ``_do_chat`` 的用例都会莫名抛错。
        """

        def _failing(self, messages, system_prompt="", max_tokens=1024, temperature=0.7):
            raise ValueError("boom")

        monkeypatch.setattr(LLMService, "_do_chat", _failing)
        module.install_hooks()
        self_obj = types.SimpleNamespace(model="m", provider="p")
        with pytest.raises(ValueError, match="boom"):
            LLMService._do_chat(self_obj, [{"role": "user", "content": "x"}])
        records, total = get_monitor().get_records(source="chat")
        assert total == 1
        assert "boom" in records[0]["error"]
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
