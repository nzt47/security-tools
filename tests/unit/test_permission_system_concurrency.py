"""permission_system 并发安全测试。

修复前：模块级单例 PermissionSystem 被多路请求并发调用——_blocked_count/
_warned_count 的 += 为读-改-写序列（并发丢计数）；_log_permission 的
len()+1 生成 id 为 TOCTOU（并发 id 重复）；confirm_action 遍历 _permission_log
与并发 append 抛 RuntimeError（list changed size during iteration）；
_record_alert 的 append+截断重建读-改-写丢告警。修复后：RLock 保护权限日志/
告警历史/计数器，锁内仅内存变更，logger 与文件 I/O 在锁外（持锁纪律）。
"""

import shutil
import tempfile
import threading

import pytest

from agent.permission_system import PermissionSystem

# 【P1 A3】D 类环境性慢测试分流：test_concurrent_confirm_no_crash 的 30 线程
# teardown t.join() 在 Windows 阻塞 _wait_for_tstate_lock，thread 超时无法中断
# → 整块被强杀（2026-08-14 实测）。fast 模式默认排除、slow 模式单独跑；
# 根因修复（t.join(timeout)+daemon 线程）列 P2 项。
pytestmark = pytest.mark.slow

# 危险文本（匹配 critical 关键词库：rm -rf /）
CRITICAL_TEXT = "rm -rf /"
# 警告文本（仅匹配 warning：rm -r，不匹配 critical）
WARNING_TEXT = "rm -r folder"


class TestPermissionSystemConcurrency:
    """PermissionSystem 并发读写（RLock 原子化）。"""

    def setup_method(self):
        self._backup_tmp = tempfile.mkdtemp(prefix="perm_test_")
        self.ps = PermissionSystem(backup_dir=self._backup_tmp)

    def teardown_method(self):
        shutil.rmtree(self._backup_tmp, ignore_errors=True)

    def test_concurrent_blocked_count_precise(self):
        """50 线程 × 20 次 critical 文本：blocked_count 计数无丢失"""
        n_threads, per = 50, 20
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    result = self.ps.check_text(CRITICAL_TEXT)
                    assert result["level"] == "critical" and not result["safe"]
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        stats = self.ps.get_security_stats()
        assert stats["blocked_count"] == total              # 读-改-写计数无丢失
        assert stats["warned_count"] == 0

    def test_concurrent_warned_count_precise(self):
        """50 线程 × 20 次 warning 文本：warned_count 计数无丢失"""
        n_threads, per = 50, 20
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(per):
                    result = self.ps.check_text(WARNING_TEXT)
                    assert result["level"] == "warning"
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        stats = self.ps.get_security_stats()
        assert stats["warned_count"] == total
        assert stats["blocked_count"] == 0

    def test_concurrent_log_ids_unique(self):
        """50 线程 × 20 次 check_action：日志条数精确、id 全局唯一（无 TOCTOU 重复）"""
        n_threads, per = 50, 20
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    # 单 -r 仅匹配危险模式（需二次确认），不匹配黑名单 rm -rf /
                    result = self.ps.check_action(f"rm -r /tmp/dir-{tid}-{i}")
                    assert result.requires_confirmation
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        log = self.ps.get_permission_log(limit=total)
        assert len(log) == total                             # 日志无丢失
        ids = [entry["id"] for entry in log]
        assert len(set(ids)) == total                        # id 无重复
        # 全部待确认操作可顺序确认成功
        assert all(self.ps.confirm_action(eid) for eid in ids)

    def test_concurrent_confirm_no_crash(self):
        """并发 check_action（生成待确认日志）+ confirm_action（遍历确认）：不崩溃"""
        n_threads, per = 30, 40
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    if tid % 2 == 0:
                        self.ps.check_action(f"rm -r /data/{tid}-{i}")
                    else:
                        # 遍历日志确认（可能尚未生成对应 id → 返回 False，不崩溃）
                        for entry in self.ps.get_permission_log(limit=200):
                            self.ps.confirm_action(entry["id"])
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发确认不应抛 RuntimeError: {errors}"
        stats = self.ps.get_security_stats()
        assert stats["permission_checks"] > 0

    def test_concurrent_alerts_truncation(self):
        """250 次 critical 告警：截断至 200 条、blocked_count 精确"""
        n_threads, per = 25, 10
        total = n_threads * per  # 250 > 200 上限
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                for _ in range(per):
                    self.ps.check_text(CRITICAL_TEXT)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        stats = self.ps.get_security_stats()
        assert stats["blocked_count"] == total               # 计数不受截断影响
        assert stats["total_alerts"] == 200                  # 截断上限恒定
        assert len(self.ps.get_alerts(limit=1000)) == 200

    def test_concurrent_read_mix_consistent(self):
        """并发 check_text + check_action + 读统计/日志/告警混合：不崩溃、状态一致"""
        n_threads, per = 30, 30
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    if tid % 3 == 0:
                        self.ps.check_text(CRITICAL_TEXT if i % 2 else WARNING_TEXT)
                    elif tid % 3 == 1:
                        self.ps.check_action(f"rm -rf /tmp/mix-{tid}-{i}")
                    else:
                        stats = self.ps.get_security_stats()
                        assert stats["blocked_count"] >= 0
                        self.ps.get_permission_log()
                        self.ps.get_alerts()
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"读写混合不应抛异常: {errors}"
        stats = self.ps.get_security_stats()
        assert stats["blocked_count"] + stats["warned_count"] > 0
        assert stats["permission_checks"] > 0
