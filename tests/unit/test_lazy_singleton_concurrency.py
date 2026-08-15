"""低危并发风险批量修复测试（2026-08-13 并发审计 #3/#4/#5/E）

覆盖：
1. #5 模块级懒加载单例双检锁：并发首次调用只创建一个实例
2. #3 model_router `_get_client` 双检锁：client 只创建一次
3. #4 LazyCollectionProxy `_ensure_collection` 双检锁：集合只创建一次
4. E sensitive_data_filter 并发 filter + add_content_pattern：无 RuntimeError
"""

import sys
import threading
import types

import agent.log_system.collectors as collectors
from agent.memory_optimized import LazyCollectionProxy
from agent.model_router.adapters import OpenAIAdapter
from agent.utils.sensitive_data_filter import SensitiveDataFilter, SensitiveLevel


def run_threads(target, args_list, barrier_count=None):
    """Barrier 同步起跑，放大竞争窗口；返回异常列表"""
    count = barrier_count if barrier_count is not None else len(args_list)
    barrier = threading.Barrier(count)
    errors = []

    def worker(arg):
        barrier.wait()
        try:
            target(arg)
        except Exception as e:  # noqa: BLE001 - 收集所有异常统一断言
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(a,)) for a in args_list]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


class TestLazySingletonConcurrency:
    """#5 模块级懒加载单例双检"""

    def test_operation_collector_created_once(self, monkeypatch):
        """并发首次 get_operation_collector：只创建一个实例"""
        monkeypatch.setattr(collectors, "_operation_collector", None)
        created = []
        orig_new = collectors.OperationCollector.__new__

        def counting_new(cls, *args, **kwargs):
            created.append(cls)
            return orig_new(cls)

        monkeypatch.setattr(collectors.OperationCollector, "__new__", counting_new)

        results = []
        lock = threading.Lock()

        def worker(_):
            c = collectors.get_operation_collector()
            with lock:
                results.append(c)

        errors = run_threads(worker, range(16))
        assert not errors, f"并发 get_operation_collector 抛异常: {errors}"
        assert len(created) == 1, f"应只创建 1 个实例，实际 {len(created)}"
        assert all(r is results[0] for r in results), "所有调用应返回同一实例"

    def test_system_event_collector_created_once(self, monkeypatch):
        """并发首次 get_system_event_collector：只创建一个实例"""
        monkeypatch.setattr(collectors, "_system_event_collector", None)
        created = []
        orig_new = collectors.SystemEventCollector.__new__

        def counting_new(cls, *args, **kwargs):
            created.append(cls)
            return orig_new(cls)

        monkeypatch.setattr(collectors.SystemEventCollector, "__new__", counting_new)

        results = []
        lock = threading.Lock()

        def worker(_):
            c = collectors.get_system_event_collector()
            with lock:
                results.append(c)

        errors = run_threads(worker, range(16))
        assert not errors, f"并发 get_system_event_collector 抛异常: {errors}"
        assert len(created) == 1, f"应只创建 1 个实例，实际 {len(created)}"
        assert all(r is results[0] for r in results), "所有调用应返回同一实例"


class TestAdapterClientConcurrency:
    """#3 model_router `_get_client` 双检"""

    def test_openai_client_created_once(self, monkeypatch):
        """并发首次 _get_client：client 只创建一次"""
        created = []

        class MockOpenAI:
            def __init__(self, **kwargs):
                created.append(kwargs)

        fake_mod = types.ModuleType("openai")
        fake_mod.OpenAI = MockOpenAI
        monkeypatch.setitem(sys.modules, "openai", fake_mod)

        adapter = OpenAIAdapter("gpt-4o", api_key="test-key")
        results = []
        lock = threading.Lock()

        def worker(_):
            c = adapter._get_client()
            with lock:
                results.append(c)

        errors = run_threads(worker, range(16))
        assert not errors, f"并发 _get_client 抛异常: {errors}"
        assert len(created) == 1, f"应只创建 1 个 client，实际 {len(created)}"
        assert all(r is adapter._client for r in results), "所有调用应返回同一 client"


class TestLazyCollectionProxyConcurrency:
    """#4 LazyCollectionProxy `_ensure_collection` 双检"""

    def test_collection_created_once(self):
        """并发首次 _ensure_collection：集合只创建一次"""
        calls = []

        class MockClient:
            def get_or_create_collection(self, name):
                calls.append(name)
                return object()

        proxy = LazyCollectionProxy(MockClient(), "coll")
        results = []
        lock = threading.Lock()

        def worker(_):
            c = proxy._ensure_collection()
            with lock:
                results.append(c)

        errors = run_threads(worker, range(16))
        assert not errors, f"并发 _ensure_collection 抛异常: {errors}"
        assert len(calls) == 1, f"应只调用 1 次 get_or_create_collection，实际 {len(calls)}"
        assert all(r is results[0] for r in results), "所有调用应返回同一集合"


class TestSensitiveFilterConcurrency:
    """E sensitive_data_filter 并发 filter + add_content_pattern"""

    def test_concurrent_filter_and_add_pattern(self):
        """并发 filter 与 add_content_pattern：无 RuntimeError（整体替换）"""
        f = SensitiveDataFilter()
        errors = []
        barrier = threading.Barrier(4)

        def do_filter(_):
            barrier.wait()
            for _ in range(200):
                try:
                    f.filter({"api_key": "sk-12345", "note": "hello"})
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

        def do_add(_):
            barrier.wait()
            for i in range(50):
                try:
                    f.add_pattern(
                        f"pat-{i}", rf"pat{i}\d+",
                        level=SensitiveLevel.MEDIUM, description="t",
                    )
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

        threads = [
            threading.Thread(target=do_filter, args=(0,)),
            threading.Thread(target=do_filter, args=(1,)),
            threading.Thread(target=do_add, args=(0,)),
            threading.Thread(target=do_add, args=(1,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发 filter/add_pattern 抛异常: {errors[:5]}"
        # 新增的模式已生效（detect 可见）：pat-3 正则 pat3\d+ 匹配 "pat3123"
        result = f.detect("pat3123")
        assert result.violations, "新增模式应被匹配到"
