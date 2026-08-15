"""dialog_state 模块并发安全测试

验证锁化修复后的高并发场景：
1. 并发 get_dialog_state 同一 session 仅创建一个实例（TOCTOU 防重复）
2. 并发 update 同实例 turn_count 精确（读-改-写原子）
3. 并发 resolve + update 混合不抛异常
4. 并发 to_dict + reset 混合不抛异常
"""

import threading

from agent.orchestrator.dialog_state import (
    DialogState,
    get_dialog_state,
    reset_session_state,
    _SESSION_STATES,
)


class TestDialogStateConcurrency:
    """对话状态模块并发安全测试"""

    N_THREADS = 16

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

    def test_concurrent_get_state_single_instance(self):
        """并发 get_dialog_state 同一 session 仅创建一个实例"""
        session_id = "conc-single"
        states, errors = self._run_threads(
            lambda i: get_dialog_state(session_id),
            list(range(self.N_THREADS)),
        )
        try:
            assert not errors, f"并发 get_dialog_state 抛异常: {errors}"
            ids = {id(s) for s in states}
            assert len(ids) == 1, f"同一 session 应只创建 1 个实例，实际 {len(ids)}"
            assert _SESSION_STATES[session_id] is states[0]
        finally:
            _SESSION_STATES.pop(session_id, None)

    def test_concurrent_update_turn_count_exact(self):
        """并发 update 同实例：turn_count 精确（读-改-写原子）"""
        dst = DialogState()
        results, errors = self._run_threads(
            lambda i: dst.update(keywords=["kw", f"kw-{i}"], intent=f"intent-{i}"),
            list(range(self.N_THREADS)),
        )
        assert not errors, f"并发 update 抛异常: {errors}"
        assert dst.turn_count == self.N_THREADS, (
            f"turn_count 应为 {self.N_THREADS}，实际 {dst.turn_count}"
        )

    def test_concurrent_resolve_update_mix_no_error(self):
        """并发 resolve + update 混合不抛异常（快照读取与状态更新互斥）"""
        dst = DialogState()
        dst.update(keywords=["PDF", "转换"], intent="pdf_convert",
                   user_input="帮我转换PDF")

        def worker_fn(i):
            if i % 2 == 0:
                dst.resolve("那个呢")
            else:
                dst.update(keywords=["PDF", "转换"], intent=f"intent-{i}",
                           user_input=f"输入-{i}")

        results, errors = self._run_threads(worker_fn, list(range(self.N_THREADS)))
        assert not errors, f"并发 resolve/update 抛异常: {errors}"
        assert dst.turn_count == 1 + self.N_THREADS // 2  # 初始 1 轮 + 写线程

    def test_concurrent_to_dict_reset_mix_no_error(self):
        """并发 to_dict + reset 混合不抛异常（快照与重置互斥）"""
        dst = DialogState()
        dst.update(keywords=["PDF"], intent="pdf_convert")

        def worker_fn(i):
            if i % 3 == 0:
                dst.reset()
            elif i % 3 == 1:
                dst.to_dict()
            else:
                dst.update(keywords=["PDF"], intent=f"intent-{i}")

        results, errors = self._run_threads(worker_fn, list(range(self.N_THREADS)))
        assert not errors, f"并发 to_dict/reset 抛异常: {errors}"

        # 最终状态必须可读且结构完整
        snapshot = dst.to_dict()
        assert "turn_count" in snapshot and "last_keywords" in snapshot

    def test_concurrent_update_same_keywords_no_error(self):
        """并发 update 不同会话互不干扰（会话隔离）"""
        def worker_fn(i):
            get_dialog_state(f"conc-sess-{i}").update(keywords=[f"kw-{i}"])

        results, errors = self._run_threads(worker_fn, list(range(self.N_THREADS)))
        try:
            assert not errors, f"并发跨会话 update 抛异常: {errors}"
            for i in range(self.N_THREADS):
                state = _SESSION_STATES.pop(f"conc-sess-{i}", None)
                assert state is not None
                assert state.turn_count == 1
        finally:
            for i in range(self.N_THREADS):
                _SESSION_STATES.pop(f"conc-sess-{i}", None)


class TestDialogStateCapacityGuard:
    """_SESSION_STATES 容量守卫（LRU 淘汰，防无界增长泄漏）"""

    # 测试期间清空模块级全局，避免污染其他用例
    @classmethod
    def _cleanup(cls):
        from agent.orchestrator.dialog_state import _SESSION_LAST_ACCESS
        _SESSION_STATES.clear()
        _SESSION_LAST_ACCESS.clear()

    def test_lru_eviction_after_overflow(self, monkeypatch):
        """超过容量上限后淘汰最久未访问的会话"""
        import agent.orchestrator.dialog_state as dst_mod
        self._cleanup()
        try:
            monkeypatch.setattr(dst_mod, "_MAX_SESSION_STATES", 3)
            # 创建 5 个会话（0..4），每次访问刷新 last_access
            for i in range(5):
                get_dialog_state(f"cap-{i}")
            assert len(_SESSION_STATES) == 3, (
                f"超限后应收敛到上限 3，实际 {len(_SESSION_STATES)}"
            )
            # 最旧的 cap-0/cap-1 被淘汰；最新 cap-3/cap-4 保留
            assert "cap-0" not in _SESSION_STATES
            assert "cap-1" not in _SESSION_STATES
            assert "cap-2" in _SESSION_STATES
            assert "cap-4" in _SESSION_STATES
        finally:
            self._cleanup()

    def test_accessed_session_survives_eviction(self, monkeypatch):
        """最近访问的会话（LRU 命中）不被淘汰"""
        import agent.orchestrator.dialog_state as dst_mod
        self._cleanup()
        try:
            monkeypatch.setattr(dst_mod, "_MAX_SESSION_STATES", 3)
            for i in range(4):
                get_dialog_state(f"hot-{i}")
            # 重新访问 hot-0 使其成为最近使用
            get_dialog_state("hot-0")
            get_dialog_state("hot-5")  # 新会话触发淘汰
            assert len(_SESSION_STATES) == 3
            # hot-0 刚被访问，应保留；最旧的 hot-1 被淘汰
            assert "hot-0" in _SESSION_STATES
            assert "hot-1" not in _SESSION_STATES
        finally:
            self._cleanup()

    def test_below_limit_no_eviction(self, monkeypatch):
        """容量未超限时不淘汰任何会话"""
        import agent.orchestrator.dialog_state as dst_mod
        self._cleanup()
        try:
            monkeypatch.setattr(dst_mod, "_MAX_SESSION_STATES", 5)
            for i in range(3):
                get_dialog_state(f"low-{i}")
            assert len(_SESSION_STATES) == 3
            for i in range(3):
                assert f"low-{i}" in _SESSION_STATES
        finally:
            self._cleanup()
