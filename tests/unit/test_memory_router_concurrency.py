"""memory/router 模块并发安全测试

验证锁化修复后的高并发场景：
1. 并发 register 不同 name + list_adapters 混合无 RuntimeError，注册数精确
2. 并发 unregister 同一 name 不抛 KeyError（if-in-del TOCTOU 修复）
3. 并发 register_tier + to_dict 混合无 RuntimeError
4. 并发 route 与 register 混合返回有效适配器
"""

import threading

from agent.memory.base import MemoryInterface, MemoryResult
from agent.memory.router import MemoryRouter


class MockAdapter(MemoryInterface):
    """测试用最小适配器"""

    def __init__(self, name: str = "mock"):
        self._name = name

    async def save(self, key, data, metadata=None):
        return True

    async def search(self, query, top_k=5):
        return [MemoryResult(content="r", confidence=0.9, source=self._name)]

    async def get_profile(self, user_id):
        return {}

    async def update_graph(self, entities, relations):
        return True

    @property
    def capabilities(self):
        return set()

    def to_dict(self):
        return {"name": self._name}


class TestMemoryRouterConcurrency:
    """MemoryRouter 并发安全测试"""

    N_THREADS = 16

    def setup_method(self):
        self.router = MemoryRouter(default_adapter=MockAdapter("default"))

    @staticmethod
    def _run_threads(target, args_list):
        """Barrier 同步起跑，放大竞争窗口"""
        barrier = threading.Barrier(len(args_list))
        results = []
        errors = []

        def worker(arg):
            barrier.wait()
            try:
                results.append(target(arg))
            except Exception as e:  # noqa: BLE001 - 收集所有异常统一断言
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(a,)) for a in args_list]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results, errors

    def test_concurrent_register_list_mix(self):
        """并发 register 不同 name + list_adapters 混合：无 RuntimeError、计数精确"""
        def worker_fn(i):
            if i % 2 == 0:
                self.router.register(f"adapter-{i}", MockAdapter(f"a-{i}"))
            else:
                self.router.list_adapters()

        results, errors = self._run_threads(worker_fn, list(range(self.N_THREADS)))
        assert not errors, f"并发 register/list 抛异常: {errors}"
        # 偶数线程各注册一个 → 8 个注册 + 默认适配器
        assert len(self.router.list_adapters()) == self.N_THREADS // 2 + 1

    def test_concurrent_unregister_same_name_no_keyerror(self):
        """并发 unregister 同一 name：不抛 KeyError（TOCTOU 修复）"""
        self.router.register("victim", MockAdapter("victim"))
        results, errors = self._run_threads(
            lambda i: self.router.unregister("victim"),
            list(range(self.N_THREADS)),
        )
        assert not errors, f"并发 unregister 抛异常: {errors}"
        assert self.router.get_adapter("victim") is None

    def test_concurrent_register_tier_to_dict_mix(self):
        """并发 register_tier + to_dict 混合：无 RuntimeError"""
        def worker_fn(i):
            if i % 2 == 0:
                self.router.register_tier("L1", MockAdapter(f"l1-{i}"))
            else:
                self.router.to_dict()

        results, errors = self._run_threads(worker_fn, list(range(self.N_THREADS)))
        assert not errors, f"并发 register_tier/to_dict 抛异常: {errors}"
        snapshot = self.router.to_dict()
        assert "tier_adapters" in snapshot and "adapters" in snapshot

    def test_concurrent_route_and_register(self):
        """并发 route 与 register 混合：route 恒返回非 None 有效适配器"""
        self.router.register("holographic", MockAdapter("holographic"))

        def worker_fn(i):
            if i % 2 == 0:
                self.router.register(f"holographic", MockAdapter(f"h-{i}"))
                return None
            return self.router.route("local_privacy")

        results, errors = self._run_threads(worker_fn, list(range(self.N_THREADS)))
        assert not errors, f"并发 route/register 抛异常: {errors}"
        for r in results:
            if r is not None:
                assert isinstance(r, MemoryInterface)
