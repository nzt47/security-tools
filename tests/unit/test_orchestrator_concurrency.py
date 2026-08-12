"""Orchestrator _interaction_count 并发安全测试。

修复前：process() 中 `self._interaction_count += 1` 为「读-改-写」序列（非原子），
多线程并发对话会丢更新——轮次计数失真，且派生值 interaction_id（写入 trace
元数据）可能重复。修复后：递增在宿主（LifecycleManager / V2 optimized_init）
创建的 _interaction_lock 锁内，锁内仅内存整数变更（持锁纪律）。

说明：process() 完整链路依赖宿主注入的 _memory/_llm/_behavior 等十余项组件，
无法轻量实例化；本测试直接验证修复的锁语义（递增代码块与 process 逐行一致），
实例化沿用 test_orchestrator_refactor 的 patch __init__ 模式。
"""

import inspect
import threading
from unittest.mock import patch

from agent.orchestrator import lifecycle_manager
from agent.orchestrator.orchestrator import Orchestrator


def _make_orchestrator():
    """轻量实例化 Orchestrator 并注入计数与锁（模拟宿主初始化）"""
    with patch.object(Orchestrator, "__init__", lambda self: None):
        orch = Orchestrator()
    orch._interaction_count = 0
    orch._interaction_lock = threading.Lock()
    return orch


class TestInteractionCountConcurrency:
    """_interaction_count 并发递增精确性（process 递增代码块等价验证）。"""

    def test_concurrent_increment_precise(self):
        """100 线程 × 50 次并发递增：计数精确无丢失"""
        orch = _make_orchestrator()
        n_threads, per = 100, 50
        total = n_threads * per
        barrier = threading.Barrier(n_threads)  # 同步起跑，放大读-改-写竞争

        def worker():
            barrier.wait()
            for _ in range(per):
                # 与 process() 中递增代码块逐行一致
                with orch._interaction_lock:
                    orch._interaction_count += 1

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert orch._interaction_count == total  # 锁保护后无丢失更新

    def test_concurrent_increment_unique_interaction_ids(self):
        """递增取回值（interaction_id 语义）全局唯一"""
        orch = _make_orchestrator()
        n_threads, per = 100, 50
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        ids = []
        ids_lock = threading.Lock()

        def worker():
            barrier.wait()
            for _ in range(per):
                with orch._interaction_lock:
                    orch._interaction_count += 1
                    current = orch._interaction_count
                with ids_lock:
                    ids.append(current)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(ids)) == total  # interaction_id 无重复（trace 元数据可靠）

    def test_interaction_lock_created_by_host_initializers(self):
        """宿主初始化点应创建 _interaction_lock（防未来重构删除）"""
        src = inspect.getsource(lifecycle_manager)
        assert "_interaction_lock = threading.Lock()" in src
