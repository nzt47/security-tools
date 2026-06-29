"""日志采集器测试 — 装饰器与采集器功能"""
import os
import tempfile

import pytest

from agent.log_system.collectors import log_operation
from agent.log_system.storage import LogStorage, _set_storage
from agent.log_system.models import LogEntry, LogCategory, LogLevel


class TestLogOperationDecorator:
    """日志操作装饰器测试"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.store = LogStorage(db_path=self.db_path, raw_log_dir=self.tmpdir)
        self.store.initialize()
        _set_storage(self.store)

    def teardown_method(self):
        self.store.close()
        _set_storage(None)

    def test_decorator_logs_operation(self):
        @log_operation(category="test", source="test_module")
        def my_func():
            return "result"

        my_func()

    def test_decorator_with_error(self):
        @log_operation(category="test")
        def failing_func():
            raise ValueError("测试错误")

        with pytest.raises(ValueError):
            failing_func()

    def test_decorator_no_storage(self):
        _set_storage(None)  # 无存储时不应报错

        @log_operation(category="test")
        def simple():
            return "ok"

        assert simple() == "ok"
