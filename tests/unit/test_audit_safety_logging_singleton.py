"""logging_utils / safe_logger 单例迁移单元测试（方案 B：独立注册）

覆盖：
- 实例共享逻辑：logging_utils 与 safe_logger 各自单例唯一，但两模块间不共享（方案 B）
- 日志模块名称：logging_utils 输出 module_name="logging_utils"，
  safe_logger 输出 module_name="safe_logger"（格式差异保留）
- 重置：新实例、GC 回收、独立性（重置 safe_logger 不影响 logging_utils）
- 并发首次初始化、fallback 行为
"""
import gc
import json
import threading
import weakref

import pytest

import agent.logging_utils as lu
from agent.log_system import safe_logger as sl
from agent.utils.singleton_manager import is_initialized

_LOG_ACTIONS = ("log_config_access",)


@pytest.fixture(autouse=True)
def _cleanup_singletons():
    """每个用例前后重置 4 个单例，保证测试隔离"""
    lu.reset_audit_logger()
    lu.reset_safety_monitor()
    sl.reset_audit_logger()
    sl.reset_safety_monitor()
    yield
    lu.reset_audit_logger()
    lu.reset_safety_monitor()
    sl.reset_audit_logger()
    sl.reset_safety_monitor()


class _Capture:
    """临时替换 _logger.info 捕获审计日志 JSON 消息"""

    def __init__(self, audit_logger):
        self._audit_logger = audit_logger
        self.records = []
        self._orig = audit_logger._logger.info

    def __enter__(self):
        self._audit_logger._logger.info = lambda msg, *a, **kw: self.records.append(msg)
        return self

    def __exit__(self, *exc):
        self._audit_logger._logger.info = self._orig
        return False


class TestLoggingUtilsSingleton:
    """logging_utils 单例行为"""

    def test_get_audit_logger_returns_same_instance(self):
        a = lu.get_audit_logger()
        b = lu.get_audit_logger()
        assert a is b

    def test_get_safety_monitor_returns_same_instance(self):
        a = lu.get_safety_monitor()
        b = lu.get_safety_monitor()
        assert a is b

    def test_singletons_registered_in_manager(self):
        lu.get_audit_logger()
        lu.get_safety_monitor()
        assert is_initialized("audit_logger")
        assert is_initialized("safety_monitor")

    def test_reset_audit_logger_returns_new_instance(self):
        first = lu.get_audit_logger()
        lu.reset_audit_logger()
        second = lu.get_audit_logger()
        assert first is not second

    def test_reset_safety_monitor_returns_new_instance(self):
        first = lu.get_safety_monitor()
        lu.reset_safety_monitor()
        second = lu.get_safety_monitor()
        assert first is not second

    def test_reset_releases_audit_logger_for_gc(self):
        ref = weakref.ref(lu.get_audit_logger())
        lu.reset_audit_logger()
        gc.collect()
        assert ref() is None


class TestSafeLoggerIndependentSingleton:
    """方案 B：safe_logger 独立注册，与 logging_utils 不共享实例"""

    def test_safe_logger_audit_logger_returns_same_instance(self):
        a = sl.get_audit_logger()
        b = sl.get_audit_logger()
        assert a is b

    def test_safe_logger_safety_monitor_returns_same_instance(self):
        a = sl.get_safety_monitor()
        b = sl.get_safety_monitor()
        assert a is b

    def test_audit_loggers_not_shared_between_modules(self):
        """方案 B 关键：两模块 audit_logger 是不同实例"""
        assert lu.get_audit_logger() is not sl.get_audit_logger()

    def test_safety_monitors_not_shared_between_modules(self):
        assert lu.get_safety_monitor() is not sl.get_safety_monitor()

    def test_reset_safe_logger_does_not_affect_logging_utils(self):
        """重置 safe_logger 不影响 logging_utils 的已建单例（独立性）"""
        lu_audit = lu.get_audit_logger()
        sl.reset_audit_logger()
        sl.reset_safety_monitor()
        assert lu.get_audit_logger() is lu_audit

    def test_reset_safe_logger_returns_new_instance(self):
        first = sl.get_audit_logger()
        sl.reset_audit_logger()
        second = sl.get_audit_logger()
        assert first is not second


class TestModuleNameField:
    """日志模块名称验证（方案 B 保留各自语义）"""

    def test_logging_utils_audit_log_uses_logging_utils(self):
        audit = lu.get_audit_logger()
        with _Capture(audit) as cap:
            audit.log_config_access("api_key")
        payload = json.loads(cap.records[0])
        assert payload["module_name"] == "logging_utils"
        assert payload["action"].startswith("logging_utils.")

    def test_safe_logger_audit_log_uses_safe_logger(self):
        audit = sl.get_audit_logger()
        with _Capture(audit) as cap:
            audit.log_config_access("api_key")
        payload = json.loads(cap.records[0])
        assert payload["module_name"] == "safe_logger"
        assert payload["action"] == "config_access.user.user"

    def test_shared_logger_name_but_distinct_output_format(self):
        """两者共用 agent.audit logger，但日志负载格式不同（方案 B 原因）"""
        with _Capture(lu.get_audit_logger()) as cap_lu:
            lu.get_audit_logger().log_config_access("k")
        with _Capture(sl.get_audit_logger()) as cap_sl:
            sl.get_audit_logger().log_config_access("k")
        payload_lu = json.loads(cap_lu.records[0])
        payload_sl = json.loads(cap_sl.records[0])
        assert payload_lu != payload_sl
        assert "msg" in payload_sl
        assert "message" in payload_lu


class TestConcurrency:
    """并发首次初始化测试"""

    def test_concurrent_first_get_audit_logger_initializes_once(self):
        orig_cls = lu.AuditLogger
        created = []

        class CountingAuditLogger(orig_cls):
            def __init__(self, *args, **kwargs):
                created.append(1)
                super().__init__(*args, **kwargs)

        lu.AuditLogger = CountingAuditLogger
        try:
            results = []
            errors = []
            barrier = threading.Barrier(8)

            def worker():
                barrier.wait()
                try:
                    results.append(lu.get_audit_logger())
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
            lu.AuditLogger = orig_cls


class TestFallback:
    """SingletonManager 不可用时的 fallback 行为"""

    def test_logging_utils_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(lu, "_SINGLETON_AVAILABLE", False)
        a = lu.get_audit_logger()
        b = lu.get_audit_logger()
        assert a is b

    def test_safe_logger_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(sl, "_SINGLETON_AVAILABLE", False)
        a = sl.get_safety_monitor()
        b = sl.get_safety_monitor()
        assert a is b

    def test_fallback_reset_works(self, monkeypatch):
        monkeypatch.setattr(lu, "_SINGLETON_AVAILABLE", False)
        first = lu.get_safety_monitor()
        lu.reset_safety_monitor()
        second = lu.get_safety_monitor()
        assert first is not second
