"""日志处理器工厂与配置测试"""
import os
import sys
import logging
import tempfile

import pytest
from agent.log_system.handlers import (
    create_rotating_file_handler, setup_agent_logging, setup_error_logging,
)
from agent.log_system.formatter import LogRotationConfig


class TestCreateRotatingFileHandler:
    """文件处理器创建测试"""

    def test_create_default(self):
        handler = create_rotating_file_handler("test.log")
        assert handler is not None
        assert handler.__class__.__name__ == "RotatingFileHandler"

    def test_create_timed_rotation(self):
        config = LogRotationConfig(use_timed_rotation=True)
        handler = create_rotating_file_handler("test.log", config)
        assert handler.__class__.__name__ == "TimedRotatingFileHandler"

    def test_create_with_formatter(self):
        import logging
        fmt = logging.Formatter("%(message)s")
        handler = create_rotating_file_handler("test.log", formatter=fmt)
        assert handler.formatter._fmt == "%(message)s"

    def test_create_max_bytes(self):
        config = LogRotationConfig(max_bytes=1024, backup_count=3)
        handler = create_rotating_file_handler("test.log", config)
        assert handler.maxBytes == 1024
        assert handler.backupCount == 3


class TestSetupAgentLogging:
    """Agent 日志系统配置测试"""

    def test_setup_default(self):
        logger = setup_agent_logging()
        assert logger is not None
        assert logger.name == "云枢.agent"

    def test_setup_debug_mode(self):
        import logging
        logger = setup_agent_logging(debug_mode=True)
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_setup_with_file(self):
        tmpfile = os.path.join(tempfile.mkdtemp(), "agent.log")
        logger = setup_agent_logging(enable_file=True, log_file=tmpfile)
        assert logger is not None

    def test_setup_without_console(self):
        logger = setup_agent_logging(enable_console=False)
        assert logger is not None


class TestSetupErrorLogging:
    """错误日志配置测试"""

    def test_setup_error(self):
        tmpfile = os.path.join(tempfile.mkdtemp(), "errors.log")
        logger = setup_error_logging(log_file=tmpfile)
        assert logger is not None
        assert logger.level == logging.ERROR
