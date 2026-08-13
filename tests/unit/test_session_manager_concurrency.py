"""SessionManager 并发稳定性测试

验证并发 add_message 下消息无丢失、message_count 精确（2026-08-13 并发审计
B 背景：锁内 index 读-改-写串行化保证计数一致性）：

1. 并发 add_message 不同会话：各会话消息数精确、无丢失
2. 并发 add_message 同会话：message_count 精确、消息全部可读
"""

import threading

from agent.session_manager import SessionManager


class TestSessionManagerConcurrency:
    """SessionManager 并发稳定性测试"""

    @staticmethod
    def _run_threads(target, args_list):
        """Barrier 同步起跑，放大竞争窗口"""
        barrier = threading.Barrier(len(args_list))
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

    def test_concurrent_add_different_sessions(self, tmp_path):
        """并发 add_message 不同会话：各会话消息数精确、无丢失"""
        mgr = SessionManager(sessions_dir=str(tmp_path / "sessions"))
        n_threads, per_thread = 8, 30
        session_ids = [mgr.create_session(f"s-{i}")["id"] for i in range(n_threads)]

        def worker(tid):
            for i in range(per_thread):
                mgr.add_message(session_ids[tid], "user", f"msg-{tid}-{i}")

        errors = self._run_threads(worker, list(range(n_threads)))
        assert not errors, f"并发 add_message 抛异常: {errors}"

        for tid in range(n_threads):
            msgs = mgr.get_messages(session_ids[tid], limit=per_thread)
            assert len(msgs) == per_thread, (
                f"会话 {session_ids[tid]} 应有 {per_thread} 条消息，实际 {len(msgs)}"
            )

    def test_concurrent_add_same_session_count_exact(self, tmp_path):
        """并发 add_message 同会话：message_count 精确、消息全部可读"""
        mgr = SessionManager(sessions_dir=str(tmp_path / "sessions"))
        sid = mgr.create_session("shared")["id"]
        n_threads, per_thread = 8, 25

        def worker(tid):
            for i in range(per_thread):
                mgr.add_message(sid, "user", f"msg-{tid}-{i}")

        errors = self._run_threads(worker, list(range(n_threads)))
        assert not errors, f"并发 add_message 抛异常: {errors}"

        total = n_threads * per_thread
        msgs = mgr.get_messages(sid, limit=total)
        assert len(msgs) == total, f"共享会话应有 {total} 条消息，实际 {len(msgs)}"
        # 消息内容无重复（并发交错不产生重复消息）
        contents = [m["content"] for m in msgs]
        assert len(set(contents)) == total, f"消息存在重复：共 {total} 条，去重后 {len(set(contents))} 条"
        # message_count 索引精确
        index = mgr._read_index()
        entry = next(s for s in index if s["id"] == sid)
        assert entry["message_count"] == total, (
            f"message_count 应为 {total}，实际 {entry['message_count']}"
        )

    def test_slow_append_does_not_block_reads(self, tmp_path, monkeypatch):
        """核心验证 B：消息 append 移出锁外后，慢磁盘不阻塞读操作

        模拟慢磁盘（open("a") 延迟 0.2s）：若 append 持锁（旧实现），并发
        get_messages 需等待 ≈0.2s；锁外则读立即完成（<0.15s）。
        """
        import builtins
        import time

        mgr = SessionManager(sessions_dir=str(tmp_path / "sessions"))
        sid = mgr.create_session("s1")["id"]
        mgr.add_message(sid, "user", "init")

        real_open = builtins.open

        def slow_open(*args, **kwargs):
            # 仅模拟"a"（消息追加）模式的慢磁盘，其他模式走原实现
            if len(args) > 1 and args[1] == "a":
                time.sleep(0.2)
            return real_open(*args, **kwargs)

        monkeypatch.setattr(builtins, "open", slow_open)

        start = time.time()
        t = threading.Thread(target=lambda: mgr.add_message(sid, "user", "slow-append"))
        t.start()
        time.sleep(0.05)  # 确保 append 线程已进入慢磁盘段
        mgr.get_messages(sid, limit=50)  # 不应被 append 的 0.2s 磁盘延迟阻塞
        elapsed = time.time() - start
        t.join()

        assert elapsed < 0.15, (
            f"读操作不应被慢 append 阻塞（append 应持锁外）：旧实现约 0.2s，实际 {elapsed:.3f}s"
        )
        # 慢 append 最终仍成功写入（消息计数一致）
        assert mgr.get_message_count(sid) == 2
