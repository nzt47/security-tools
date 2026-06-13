"""
LoggingUtils 测试 - pytest 格式
针对 agent/logging_utils.py 的完整测试用例
"""
import os
import sys
import re
import json
import pytest
import logging
import tempfile
from unittest.mock import patch, MagicMock

from agent.logging_utils import (
    LogRotationConfig,
    create_rotating_file_handler,
    setup_agent_logging,
    setup_error_logging,
    SensitiveDataFilter,
    AuditLogger,
    get_audit_logger,
    AgentSafetyMonitor,
    get_safety_monitor,
    safe_execute,
    AgentTimeoutException,
    AgentLoopException,
    AgentStateStuckException,
)


class TestLogRotationConfig:
    """测试日志轮转配置类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_default_init(self):
        """测试默认初始化"""
        config = LogRotationConfig()
        assert config.max_bytes == 50 * 1024 * 1024
        assert config.backup_count == 5
        assert config.encoding == "utf-8"
        assert config.when == "midnight"
        assert config.interval == 1
        assert config.utc is False
        assert config.use_timed_rotation is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_custom_init(self):
        """测试自定义初始化"""
        config = LogRotationConfig(
            max_bytes=10 * 1024 * 1024,
            backup_count=10,
            encoding="gbk",
            when="H",
            interval=6,
            utc=True,
            use_timed_rotation=True,
        )
        assert config.max_bytes == 10 * 1024 * 1024
        assert config.backup_count == 10
        assert config.encoding == "gbk"
        assert config.when == "H"
        assert config.interval == 6
        assert config.utc is True
        assert config.use_timed_rotation is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_to_dict(self):
        """测试转换为字典"""
        config = LogRotationConfig(
            max_bytes=10 * 1024 * 1024,
            backup_count=3,
            use_timed_rotation=True,
        )
        result = config.to_dict()
        assert isinstance(result, dict)
        assert result["max_bytes"] == 10 * 1024 * 1024
        assert result["backup_count"] == 3
        assert result["use_timed_rotation"] is True


class TestCreateRotatingFileHandler:
    """测试创建轮转文件处理器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_create_size_based_handler(self):
        """测试创建基于大小的轮转处理器"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            handler = create_rotating_file_handler(log_file)
            assert handler is not None
            assert isinstance(handler, logging.handlers.RotatingFileHandler)
            handler.close()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_create_time_based_handler(self):
        """测试创建基于时间的轮转处理器"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            config = LogRotationConfig(use_timed_rotation=True)
            handler = create_rotating_file_handler(log_file, config=config)
            assert handler is not None
            assert isinstance(handler, logging.handlers.TimedRotatingFileHandler)
            handler.close()

    @pytest.mark.unit
    @pytest.mark.p1
    def test_create_handler_with_formatter(self):
        """测试带格式化器的处理器"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            formatter = logging.Formatter("%(asctime)s - %(message)s")
            handler = create_rotating_file_handler(log_file, formatter=formatter)
            assert handler.formatter == formatter
            handler.close()

    @pytest.mark.unit
    @pytest.mark.p1
    def test_create_handler_creates_dir(self):
        """测试自动创建日志目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "subdir", "test.log")
            assert not os.path.exists(os.path.dirname(log_file))
            handler = create_rotating_file_handler(log_file)
            assert os.path.exists(os.path.dirname(log_file))
            handler.close()


class TestSetupAgentLogging:
    """测试 Agent 日志配置"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_setup_agent_logging_basic(self):
        """测试基本日志配置"""
        logger = setup_agent_logging(
            debug_mode=False,
            enable_console=True,
            enable_file=False,
        )
        assert logger is not None
        assert isinstance(logger, logging.Logger)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_setup_agent_logging_debug_mode(self):
        """测试调试模式"""
        logger = setup_agent_logging(
            debug_mode=True,
            enable_console=True,
            enable_file=False,
        )
        assert logger is not None


class TestSetupErrorLogging:
    """测试错误日志配置"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_setup_error_logging_basic(self):
        """测试配置错误日志（不带文件输出）"""
        with patch('agent.logging_utils.create_rotating_file_handler') as mock_handler:
            mock_handler.return_value = MagicMock()
            logger = setup_error_logging()
            assert logger is not None
            assert logger.name == "agent.errors"
            assert logger.level == logging.ERROR


class TestSensitiveDataFilter:
    """测试敏感信息脱敏过滤器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_init(self):
        """测试过滤器初始化"""
        filter_obj = SensitiveDataFilter()
        assert filter_obj is not None
        assert hasattr(filter_obj, '_patterns')

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_api_key(self):
        """测试脱敏 API Key"""
        filter_obj = SensitiveDataFilter()
        test_msg = "API Key: sk-abcdefghijklmnopqrstuvwxyz123456"
        record = logging.LogRecord("test", logging.INFO, "", 0, test_msg, None, None)
        result = filter_obj.filter(record)
        assert result is True
        assert "sk-" not in record.msg
        assert "***" in record.msg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_password(self):
        """测试脱敏密码（字段名=值形式）"""
        filter_obj = SensitiveDataFilter()
        test_msg = 'password="my_secret_password"'
        record = logging.LogRecord("test", logging.INFO, "", 0, test_msg, None, None)
        filter_obj.filter(record)
        assert "my_secret_password" not in record.msg
        assert 'password="***"' in record.msg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_phone(self):
        """测试脱敏手机号"""
        filter_obj = SensitiveDataFilter()
        test_msg = "联系电话: 13812345678"
        record = logging.LogRecord("test", logging.INFO, "", 0, test_msg, None, None)
        filter_obj.filter(record)
        assert "13812345678" not in record.msg
        assert "138****5678" in record.msg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_id_card(self):
        """测试脱敏身份证号"""
        filter_obj = SensitiveDataFilter()
        test_msg = "身份证: 110101199003071234"
        record = logging.LogRecord("test", logging.INFO, "", 0, test_msg, None, None)
        filter_obj.filter(record)
        assert "110101199003071234" not in record.msg
        assert "110101********1234" in record.msg

    @pytest.mark.unit
    @pytest.mark.p1
    def test_filter_with_args(self):
        """测试脱敏日志参数"""
        filter_obj = SensitiveDataFilter()
        test_args = ("API Key: sk-abc123",)
        record = logging.LogRecord("test", logging.INFO, "", 0, "%s", test_args, None)
        filter_obj.filter(record)
        assert "sk-" not in record.args[0]
        assert "***" in record.args[0]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_sanitize_dict(self):
        """测试脱敏字典"""
        filter_obj = SensitiveDataFilter()
        test_dict = {
            "user": "admin",
            "api_key_str": "sk-secret123",
            "password_field": 'password="mypassword"',
            "nested": {"token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"}
        }
        result = filter_obj._sanitize_dict(test_dict)
        assert "sk-" not in result["api_key_str"]
        assert "mypassword" not in result["password_field"]
        assert "eyJhbGciOiJ" not in result["nested"]["token"]
        assert result["user"] == "admin"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_filter_hong_kong_phone(self):
        """测试脱敏香港手机号"""
        filter_obj = SensitiveDataFilter()
        test_msg = "香港电话: 85291234567"
        record = logging.LogRecord("test", logging.INFO, "", 0, test_msg, None, None)
        filter_obj.filter(record)
        assert "91234567" not in record.msg

    @pytest.mark.unit
    @pytest.mark.p1
    def test_filter_url_params(self):
        """测试脱敏URL参数"""
        filter_obj = SensitiveDataFilter()
        test_msg = "URL: https://example.com/api?api_key=secret123&token=abc"
        record = logging.LogRecord("test", logging.INFO, "", 0, test_msg, None, None)
        filter_obj.filter(record)
        assert "secret123" not in record.msg
        assert "abc" not in record.msg


class TestLoggingUtilsEdgeCases:
    """测试边界情况"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_sanitize_empty_string(self):
        """测试脱敏空字符串"""
        filter_obj = SensitiveDataFilter()
        result = filter_obj._sanitize("")
        assert result == ""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_sanitize_non_string(self):
        """测试脱敏非字符串"""
        filter_obj = SensitiveDataFilter()
        result = filter_obj._sanitize(123)
        assert result == 123

    @pytest.mark.unit
    @pytest.mark.p1
    def test_sanitize_dict_with_list(self):
        """测试脱敏包含列表的字典"""
        filter_obj = SensitiveDataFilter()
        test_dict = {
            "items": ["password=secret1", "token=abc123"],
            "user": "admin"
        }
        result = filter_obj._sanitize_dict(test_dict)
        assert "secret1" not in result["items"][0]
        assert "abc123" not in result["items"][1]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_create_handler_without_dir(self):
        """测试创建处理器时自动创建目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "deep", "nested", "dir", "test.log")
            handler = create_rotating_file_handler(log_file)
            assert os.path.exists(os.path.dirname(log_file))
            handler.close()


class TestAuditLogger:
    """测试审计日志记录器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_audit_logger_init(self):
        """测试审计日志初始化"""
        logger = AuditLogger()
        assert logger is not None
        assert logger._logger.name == "agent.audit"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_config_access(self):
        """测试记录配置访问"""
        logger = AuditLogger()
        logger.log_config_access("test_key", "test_user")
        # 验证日志被记录（通过检查 logger 是否正常工作）

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_config_modification(self):
        """测试记录配置修改"""
        logger = AuditLogger()
        logger.log_config_modification("test_key", "test_user")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_secure_config_access_success(self):
        """测试记录安全配置访问（成功）"""
        logger = AuditLogger()
        logger.log_secure_config_access("secure_key", True, "test_user")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_secure_config_access_failed(self):
        """测试记录安全配置访问（失败）"""
        logger = AuditLogger()
        logger.log_secure_config_access("secure_key", False, "test_user")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_encryption_key_access(self):
        """测试记录加密密钥访问"""
        logger = AuditLogger()
        logger.log_encryption_key_access(True, "test_user")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_permission_change(self):
        """测试记录权限变更"""
        logger = AuditLogger()
        logger.log_permission_change("grant", "resource1", "test_user")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_authentication_success(self):
        """测试记录认证成功"""
        logger = AuditLogger()
        logger.log_authentication("test_user", True, "192.168.1.1")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_authentication_failed(self):
        """测试记录认证失败"""
        logger = AuditLogger()
        logger.log_authentication("test_user", False, "192.168.1.1")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_sensitive_operation(self):
        """测试记录敏感操作"""
        logger = AuditLogger()
        logger.log_sensitive_operation("delete_data", {"target": "user1"}, "admin")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_sensitive_operation_no_details(self):
        """测试记录敏感操作（无详情）"""
        logger = AuditLogger()
        logger.log_sensitive_operation("delete_data", None, "admin")


class TestGetAuditLogger:
    """测试获取全局审计日志记录器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_audit_logger_returns_instance(self):
        """测试获取审计日志实例"""
        logger = get_audit_logger()
        assert logger is not None
        assert isinstance(logger, AuditLogger)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_audit_logger_singleton(self):
        """测试审计日志单例"""
        logger1 = get_audit_logger()
        logger2 = get_audit_logger()
        assert logger1 is logger2


class TestAgentSafetyMonitor:
    """测试安全监控器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safety_monitor_init(self):
        """测试安全监控器初始化"""
        monitor = AgentSafetyMonitor()
        assert monitor is not None
        assert monitor.max_iterations_per_minute == 100
        assert monitor.state_stuck_threshold == 10

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safety_monitor_custom_init(self):
        """测试自定义参数初始化"""
        monitor = AgentSafetyMonitor(
            max_iterations_per_minute=50,
            state_stuck_threshold_seconds=5,
        )
        assert monitor.max_iterations_per_minute == 50
        assert monitor.state_stuck_threshold == 5

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_iteration_first_time(self):
        """测试首次记录迭代"""
        monitor = AgentSafetyMonitor()
        result = monitor.record_iteration("test_task")
        assert result is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_iteration_normal(self):
        """测试正常迭代记录"""
        monitor = AgentSafetyMonitor(max_iterations_per_minute=100)
        for _ in range(10):
            result = monitor.record_iteration("test_task")
        assert result is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_check_state_first_time(self):
        """测试首次状态检查"""
        monitor = AgentSafetyMonitor()
        result = monitor.check_state("test_task", "running")
        assert result is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_check_state_change(self):
        """测试状态变化"""
        monitor = AgentSafetyMonitor()
        monitor.check_state("test_task", "running")
        result = monitor.check_state("test_task", "completed")
        assert result is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_reset_specific_identifier(self):
        """测试重置特定标识符"""
        monitor = AgentSafetyMonitor()
        monitor.record_iteration("test_task")
        monitor.reset("test_task")
        # 重置后应该可以重新记录
        result = monitor.record_iteration("test_task")
        assert result is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_reset_all(self):
        """测试重置所有"""
        monitor = AgentSafetyMonitor()
        monitor.record_iteration("task1")
        monitor.record_iteration("task2")
        monitor.reset()
        stats = monitor.get_stats()
        assert stats["tracked_identifiers"] == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_stats(self):
        """测试获取统计"""
        monitor = AgentSafetyMonitor()
        monitor.record_iteration("test_task")
        stats = monitor.get_stats()
        assert "tracked_identifiers" in stats
        assert "max_iterations_per_minute" in stats
        assert "state_stuck_threshold" in stats


class TestGetSafetyMonitor:
    """测试获取全局安全监控器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_safety_monitor_returns_instance(self):
        """测试获取安全监控器实例"""
        monitor = get_safety_monitor()
        assert monitor is not None
        assert isinstance(monitor, AgentSafetyMonitor)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_safety_monitor_singleton(self):
        """测试安全监控器单例"""
        monitor1 = get_safety_monitor()
        monitor2 = get_safety_monitor()
        assert monitor1 is monitor2


class TestSafeExecute:
    """测试安全执行包装器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_execute_success(self):
        """测试成功执行"""
        def success_func():
            return "success"
        
        result = safe_execute(success_func, timeout=5)
        assert result == "success"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_execute_with_exception(self):
        """测试执行异常"""
        def fail_func():
            raise ValueError("test error")
        
        with pytest.raises(ValueError):
            safe_execute(fail_func, timeout=5)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_execute_with_default_return(self):
        """测试默认返回值"""
        def slow_func():
            import time
            time.sleep(10)
            return "slow"
        
        result = safe_execute(slow_func, timeout=0.1, default_return="default")
        assert result == "default"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_execute_with_identifier(self):
        """测试带标识符执行"""
        def success_func():
            return "success"
        
        result = safe_execute(success_func, timeout=5, identifier="test_task")
        assert result == "success"


class TestExceptions:
    """测试自定义异常类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_agent_timeout_exception(self):
        """测试超时异常"""
        exc = AgentTimeoutException("timeout")
        assert str(exc) == "timeout"
        assert isinstance(exc, Exception)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_agent_loop_exception(self):
        """测试循环异常"""
        exc = AgentLoopException("loop detected")
        assert str(exc) == "loop detected"
        assert isinstance(exc, Exception)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_agent_state_stuck_exception(self):
        """测试状态卡死异常"""
        exc = AgentStateStuckException("state stuck")
        assert str(exc) == "state stuck"
        assert isinstance(exc, Exception)


class TestSetupAgentLoggingComplete:
    """测试 Agent 日志配置的完整覆盖"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_setup_agent_logging_with_file(self):
        """测试带文件输出的日志配置"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "agent.log")
            logger = setup_agent_logging(
                debug_mode=False,
                log_file=log_file,
                enable_console=True,
                enable_file=True,
            )
            assert logger is not None
            # 关闭所有文件处理器以释放文件锁
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                if hasattr(handler, 'close'):
                    handler.close()
                    root_logger.removeHandler(handler)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_setup_agent_logging_debug_with_file(self):
        """测试调试模式带文件输出"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "debug.log")
            logger = setup_agent_logging(
                debug_mode=True,
                log_file=log_file,
                enable_console=True,
                enable_file=True,
            )
            assert logger is not None
            # 关闭所有文件处理器以释放文件锁
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                if hasattr(handler, 'close'):
                    handler.close()
                    root_logger.removeHandler(handler)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_setup_agent_logging_no_console(self):
        """测试无控制台输出"""
        logger = setup_agent_logging(
            debug_mode=False,
            enable_console=False,
            enable_file=False,
        )
        assert logger is not None


class TestSensitiveDataFilterComplete:
    """测试敏感信息脱敏过滤器的完整覆盖"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_pk_key(self):
        """测试脱敏 pk- 开头的密钥"""
        filter_obj = SensitiveDataFilter()
        test_msg = "Key: pk-abcdefghijklmnopqrstuvwxyz123456"
        record = logging.LogRecord("test", logging.INFO, "", 0, test_msg, None, None)
        filter_obj.filter(record)
        assert "pk-" not in record.msg
        assert "***" in record.msg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_jwt_token(self):
        """测试脱敏 JWT Token"""
        filter_obj = SensitiveDataFilter()
        test_msg = "Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        record = logging.LogRecord("test", logging.INFO, "", 0, test_msg, None, None)
        filter_obj.filter(record)
        assert "eyJhbGciOiJ" not in record.msg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_token_field(self):
        """测试脱敏 token 字段"""
        filter_obj = SensitiveDataFilter()
        test_msg = 'token="my_secret_token"'
        record = logging.LogRecord("test", logging.INFO, "", 0, test_msg, None, None)
        filter_obj.filter(record)
        assert "my_secret_token" not in record.msg
        assert 'token="***"' in record.msg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_secret_field(self):
        """测试脱敏 secret 字段"""
        filter_obj = SensitiveDataFilter()
        test_msg = 'secret="my_secret_value"'
        record = logging.LogRecord("test", logging.INFO, "", 0, test_msg, None, None)
        filter_obj.filter(record)
        assert "my_secret_value" not in record.msg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_api_key_field(self):
        """测试脱敏 api_key 字段"""
        filter_obj = SensitiveDataFilter()
        test_msg = 'api_key="sk-abc123"'
        record = logging.LogRecord("test", logging.INFO, "", 0, test_msg, None, None)
        filter_obj.filter(record)
        assert "sk-abc123" not in record.msg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_access_token_field(self):
        """测试脱敏 access_token 字段"""
        filter_obj = SensitiveDataFilter()
        test_msg = 'access_token="token123"'
        record = logging.LogRecord("test", logging.INFO, "", 0, test_msg, None, None)
        filter_obj.filter(record)
        assert "token123" not in record.msg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_15_digit_id_card(self):
        """测试脱敏15位身份证号"""
        filter_obj = SensitiveDataFilter()
        test_msg = "身份证: 110101900101123"
        record = logging.LogRecord("test", logging.INFO, "", 0, test_msg, None, None)
        filter_obj.filter(record)
        assert "110101900101123" not in record.msg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_phone_with_country_code(self):
        """测试脱敏带区号的手机号"""
        filter_obj = SensitiveDataFilter()
        test_msg = "电话: +8613812345678"
        record = logging.LogRecord("test", logging.INFO, "", 0, test_msg, None, None)
        filter_obj.filter(record)
        assert "13812345678" not in record.msg

    @pytest.mark.unit
    @pytest.mark.p1
    def test_filter_dict_nested(self):
        """测试脱敏嵌套字典"""
        filter_obj = SensitiveDataFilter()
        test_dict = {
            "level1": {
                "level2": {
                    "password": "password=secret123"  # 使用字段名=值形式
                }
            }
        }
        result = filter_obj._sanitize_dict(test_dict)
        assert "secret123" not in str(result)