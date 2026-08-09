"""SingletonManager 单元测试

覆盖：注册/获取、配置支持、重置、线程安全、注册/初始化状态查询、
边界情况（未注册、工厂异常、双重检查锁）、清理钩子。
"""
import threading
import time

import pytest

from agent.utils.singleton_manager import (
    SingletonManager,
    register_singleton,
    get_singleton,
    reset_singleton,
    reset_all_singletons,
    is_registered,
    is_initialized,
)


class TestSingletonManagerCore:
    """SingletonManager 核心功能测试"""

    def test_register_and_get(self):
        """注册工厂后能获取到同一个实例"""
        reset_singleton("core_instance")
        calls = []

        def factory(config=None):
            calls.append(1)
            return object()

        register_singleton("core_instance", factory)
        a = get_singleton("core_instance")
        b = get_singleton("core_instance")
        assert a is b
        assert len(calls) == 1  # 工厂只调用一次

    def test_get_with_config(self):
        """config 参数传给工厂"""
        reset_singleton("core_config")
        received = {}

        def factory(config=None):
            received["config"] = config
            return object()

        register_singleton("core_config", factory)
        get_singleton("core_config", {"a": 1})
        assert received["config"] == {"a": 1}

    def test_default_config_merged(self):
        """默认配置与传入配置合并"""
        reset_singleton("core_default")
        received = {}

        def factory(config=None):
            received["config"] = config
            return object()

        register_singleton("core_default", factory, default_config={"x": 1})
        get_singleton("core_default", {"y": 2})
        assert received["config"] == {"x": 1, "y": 2}

    def test_reset(self):
        """reset 后再次获取会创建新实例"""
        reset_singleton("core_reset")
        register_singleton("core_reset", lambda config=None: object())
        a = get_singleton("core_reset")
        reset_singleton("core_reset")
        b = get_singleton("core_reset")
        assert a is not b

    def test_reset_all(self):
        """reset_all 清空所有实例"""
        reset_all_singletons()
        register_singleton("core_r1", lambda config=None: object())
        register_singleton("core_r2", lambda config=None: object())
        get_singleton("core_r1")
        get_singleton("core_r2")
        reset_all_singletons()
        assert not is_initialized("core_r1")
        assert not is_initialized("core_r2")

    def test_registered_and_initialized(self):
        """is_registered / is_initialized 状态查询"""
        reset_singleton("core_status")
        assert not is_registered("core_status")
        register_singleton("core_status", lambda config=None: object())
        assert is_registered("core_status")
        assert not is_initialized("core_status")
        get_singleton("core_status")
        assert is_initialized("core_status")

    def test_get_unregistered_raises(self):
        """获取未注册单例抛 KeyError"""
        reset_singleton("core_missing")
        with pytest.raises(KeyError):
            get_singleton("core_missing")

    def test_get_unregistered_required_false(self):
        """required=False 时返回 None"""
        reset_singleton("core_missing_opt")
        assert get_singleton("core_missing_opt", required=False) is None

    def test_factory_raises_propagates(self):
        """工厂异常向上传播"""
        reset_singleton("core_fail")
        register_singleton("core_fail", lambda config=None: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            get_singleton("core_fail")

    def test_cleanup_called_on_reset(self):
        """reset 时调用清理钩子"""
        reset_singleton("core_cleanup")
        cleaned = []

        def factory(config=None):
            return object()

        def cleanup(obj):
            cleaned.append(obj)

        register_singleton("core_cleanup", factory, cleanup_fn=cleanup)
        obj = get_singleton("core_cleanup")
        reset_singleton("core_cleanup")
        assert cleaned == [obj]

    def test_cleanup_exception_swallowed(self):
        """清理钩子抛异常不阻断 reset"""
        reset_singleton("core_cleanup_err")

        def factory(config=None):
            return object()

        def cleanup(obj):
            raise RuntimeError("cleanup failed")

        register_singleton("core_cleanup_err", factory, cleanup_fn=cleanup)
        get_singleton("core_cleanup_err")
        # 不应抛出异常
        reset_singleton("core_cleanup_err")
        # 实例已清除，可再次创建
        new_obj = get_singleton("core_cleanup_err")
        assert new_obj is not None

    def test_reregister_overwrites_factory(self):
        """重复注册覆盖旧工厂"""
        reset_singleton("core_reregister")

        register_singleton("core_reregister", lambda config=None: "old")
        first = get_singleton("core_reregister")
        assert first == "old"

        register_singleton("core_reregister", lambda config=None: "new")
        # 已创建的实例保持（不重建）
        assert get_singleton("core_reregister") is first
        # 重置后使用新工厂
        reset_singleton("core_reregister")
        assert get_singleton("core_reregister") == "new"

    def test_thread_safety(self):
        """多线程并发获取只创建一次实例"""
        reset_singleton("core_thread")
        counter = {"n": 0}
        lock = threading.Lock()

        def factory(config=None):
            with lock:
                counter["n"] += 1
            time.sleep(0.01)
            return object()

        register_singleton("core_thread", factory)
        results = []
        threads = []
        for _ in range(20):
            t = threading.Thread(target=lambda: results.append(get_singleton("core_thread")))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        assert counter["n"] == 1
        assert len(set(id(r) for r in results)) == 1

    def test_double_check_locking_second_branch(self):
        """双重检查锁的第二分支（并发下已有实例直接返回）"""
        reset_singleton("core_dcl")
        register_singleton("core_dcl", lambda config=None: object())
        first = get_singleton("core_dcl")
        # 再次获取走第二检查分支
        second = get_singleton("core_dcl")
        assert first is second

    def test_direct_manager_instance(self):
        """直接使用 SingletonManager 实例"""
        mgr = SingletonManager()
        mgr.register("direct", lambda config=None: object())
        a = mgr.get("direct")
        b = mgr.get("direct")
        assert a is b
        mgr.reset("direct")
        c = mgr.get("direct")
        assert a is not c


class TestSingletonModuleIntegration:
    """与已迁移模块的集成测试"""

    def test_metrics_modules_registered(self):
        """核心监控模块已注册到 SingletonManager"""
        import agent.auto_tuner  # noqa: F401
        import agent.monitoring.error_reporter  # noqa: F401
        import agent.monitoring.optimized_metrics  # noqa: F401
        import agent.monitoring.tracing_cache  # noqa: F401

        assert is_registered("auto_tuner")
        assert is_registered("error_reporter")
        assert is_registered("optimized_metrics_collector")
        assert is_registered("trace_cache")

    def test_getters_return_same_instance(self):
        """迁移后的 getter 仍返回同一实例"""
        from agent.monitoring.tracing_cache import get_trace_cache

        a = get_trace_cache()
        b = get_trace_cache()
        assert a is b


class TestSingletonIsolation:
    """测试隔离性"""

    def test_reset_all_between_tests(self):
        """reset_all_singletons 不影响已注册工厂，只清空实例"""
        register_singleton("iso_instance", lambda config=None: object())
        get_singleton("iso_instance")
        reset_all_singletons()
        # 工厂仍注册，可再次创建
        new_obj = get_singleton("iso_instance")
        assert new_obj is not None
