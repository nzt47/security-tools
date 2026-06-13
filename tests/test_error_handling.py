"""错误处理模块测试用例

覆盖异常路径的单元测试，包括：
- 配置校验测试
- 模块导入错误处理测试
- 统一错误处理装饰器测试
- 日志轮转配置测试
"""

import os
import sys
import unittest
import logging
from unittest.mock import patch, MagicMock, Mock

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestConfigValidation(unittest.TestCase):
    """配置校验测试"""
    
    def setUp(self):
        """设置测试环境"""
        from config import Config, validate_config, validate_and_fix_config
        
        self.Config = Config
        self.validate_config = validate_config
        self.validate_and_fix_config = validate_and_fix_config
    
    def test_validate_empty_config(self):
        """测试空配置校验"""
        errors = self.validate_config({})
        self.assertGreater(len(errors), 0)
    
    def test_validate_valid_config(self):
        """测试有效配置校验"""
        config = {
            "sensor": {"enable_change_detection": True, "enable_event_monitor": True},
            "cognitive": {"config_path": None},
            "memory": {
                "data_dir": "./data",
                "token_limit": 4096,
                "llm": {"provider": "", "api_key": "", "model": "", "timeout": 30},
            },
            "behavior": {"check_interval": 30},
            "permission": {"backup_dir": "./.backups"},
            "security": {"enable_encryption": True},
        }
        errors = self.validate_config(config)
        self.assertEqual(len(errors), 0)
    
    def test_validate_invalid_token_limit(self):
        """测试无效的 token_limit"""
        config = {
            "sensor": {},
            "cognitive": {},
            "memory": {"token_limit": 100},
            "behavior": {},
            "permission": {},
            "security": {},
        }
        errors = self.validate_config(config)
        self.assertTrue(any("token_limit" in e['loc'] for e in errors))
    
    def test_validate_and_fix_missing_section(self):
        """测试自动修复缺失的配置节"""
        config = {"sensor": {}}
        fixed_config, errors = self.validate_and_fix_config(config)
        self.assertIn("memory", fixed_config)
        self.assertGreater(len(errors), 0)
    
    def test_config_init_with_validation(self):
        """测试配置初始化时的校验功能"""
        config = self.Config(validate=True)
        self.assertIsInstance(config.merged, dict)
    
    def test_config_with_overrides(self):
        """测试带覆盖配置的初始化"""
        overrides = {"behavior": {"check_interval": 60}}
        config = self.Config(overrides)
        self.assertEqual(config.get("behavior", "check_interval"), 60)


class TestErrorHandlingDecorators(unittest.TestCase):
    """错误处理装饰器测试"""
    
    def setUp(self):
        """设置测试环境"""
        from agent.monitoring.decorators import (
            handle_errors,
            catch_and_report,
            safe_call,
            async_handle_errors,
        )
        from agent.error_handler import ErrorCategory, ErrorSeverity
        
        self.handle_errors = handle_errors
        self.catch_and_report = catch_and_report
        self.safe_call = safe_call
        self.async_handle_errors = async_handle_errors
        self.ErrorCategory = ErrorCategory
        self.ErrorSeverity = ErrorSeverity
    
    def test_handle_errors_decorator_basic(self):
        """测试 handle_errors 装饰器基本功能"""
        @self.handle_errors(
            error_category=self.ErrorCategory.EXTERNAL_SERVICE,
            report_error=False,
            log_error=False,
        )
        def success_func():
            return "success"
        
        result = success_func()
        self.assertEqual(result, "success")
    
    def test_handle_errors_decorator_exception(self):
        """测试 handle_errors 装饰器异常处理"""
        @self.handle_errors(
            error_category=self.ErrorCategory.EXTERNAL_SERVICE,
            report_error=False,
            log_error=False,
            return_on_error="fallback",
        )
        def fail_func():
            raise ValueError("test error")
        
        result = fail_func()
        self.assertEqual(result, "fallback")
    
    def test_handle_errors_decorator_retry(self):
        """测试 handle_errors 装饰器重试功能"""
        call_count = [0]
        
        @self.handle_errors(
            error_category=self.ErrorCategory.EXTERNAL_SERVICE,
            report_error=False,
            log_error=False,
            retry_on_error=True,
            max_retries=2,
            retry_delay=0,
            return_on_error="final_fallback",
        )
        def retry_func():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise ValueError("temporary error")
            return "success"
        
        result = retry_func()
        self.assertEqual(result, "success")
        self.assertEqual(call_count[0], 3)
    
    def test_handle_errors_decorator_ignored_exception(self):
        """测试 handle_errors 装饰器忽略指定异常"""
        class IgnoredError(Exception):
            pass
        
        @self.handle_errors(
            error_category=self.ErrorCategory.EXTERNAL_SERVICE,
            report_error=False,
            log_error=False,
            ignored_exceptions=(IgnoredError,),
        )
        def ignore_func():
            raise IgnoredError("should be ignored")
        
        with self.assertRaises(IgnoredError):
            ignore_func()
    
    def test_catch_and_report_decorator(self):
        """测试 catch_and_report 装饰器"""
        @self.catch_and_report(ValueError, context={"test": "context"})
        def catch_func():
            raise ValueError("catch me")
        
        with self.assertRaises(ValueError):
            catch_func()
    
    def test_safe_call_decorator(self):
        """测试 safe_call 装饰器"""
        @self.safe_call(default_return="default", log_errors=False, report_errors=False)
        def safe_func():
            raise ValueError("safe error")
        
        result = safe_func()
        self.assertEqual(result, "default")
    
    @patch('asyncio.sleep')
    def test_async_handle_errors_decorator(self, mock_sleep):
        """测试 async_handle_errors 装饰器"""
        import asyncio
        
        @self.async_handle_errors(
            report_error=False,
            log_error=False,
            retry_on_error=True,
            max_retries=1,
            retry_delay=0.1,
            return_on_error="fallback_result",
        )
        async def async_fail_func():
            raise ValueError("async error")
        
        result = asyncio.run(async_fail_func())
        self.assertEqual(result, "fallback_result")


class TestModuleImportErrorHandling(unittest.TestCase):
    """模块导入错误处理测试"""
    
    def test_safe_import_success(self):
        """测试安全导入成功"""
        from agent.digital_life import _safe_import
        
        def import_os():
            import os
            return os
        
        result, success = _safe_import("os", import_os)
        self.assertTrue(success)
        self.assertIsNotNone(result)
    
    def test_safe_import_failure(self):
        """测试安全导入失败"""
        from agent.digital_life import _safe_import
        
        def import_nonexistent():
            import nonexistent_module_xyz123  # type: ignore
        
        result, success = _safe_import("nonexistent", import_nonexistent)
        self.assertFalse(success)
        self.assertIsNone(result)
    
    def test_safe_import_from_success(self):
        """测试从包导入成功"""
        from agent.digital_life import _safe_import_from
        
        modules, success = _safe_import_from('os', 'path')
        self.assertTrue(success)
        self.assertIn('path', modules)
        self.assertIsNotNone(modules['path'])
    
    def test_safe_import_from_failure(self):
        """测试从包导入失败"""
        from agent.digital_life import _safe_import_from
        
        modules, success = _safe_import_from('nonexistent_package_xyz', 'something')
        self.assertFalse(success)
        self.assertIsNone(modules.get('something'))


class TestLogRotation(unittest.TestCase):
    """日志轮转配置测试"""
    
    def setUp(self):
        """设置测试环境"""
        from agent.logging_utils import (
            LogRotationConfig,
            create_rotating_file_handler,
            setup_agent_logging,
            setup_error_logging,
        )
        
        self.LogRotationConfig = LogRotationConfig
        self.create_rotating_file_handler = create_rotating_file_handler
        self.setup_agent_logging = setup_agent_logging
        self.setup_error_logging = setup_error_logging
    
    def test_log_rotation_config_default(self):
        """测试日志轮转配置默认值"""
        config = self.LogRotationConfig()
        self.assertEqual(config.max_bytes, 50 * 1024 * 1024)
        self.assertEqual(config.backup_count, 5)
        self.assertEqual(config.encoding, "utf-8")
    
    def test_log_rotation_config_custom(self):
        """测试日志轮转配置自定义值"""
        config = self.LogRotationConfig(
            max_bytes=10 * 1024 * 1024,
            backup_count=10,
            encoding="utf-16",
        )
        self.assertEqual(config.max_bytes, 10 * 1024 * 1024)
        self.assertEqual(config.backup_count, 10)
        self.assertEqual(config.encoding, "utf-16")
    
    def test_log_rotation_config_to_dict(self):
        """测试日志轮转配置转换为字典"""
        config = self.LogRotationConfig()
        config_dict = config.to_dict()
        self.assertIsInstance(config_dict, dict)
        self.assertIn('max_bytes', config_dict)
    
    def test_create_rotating_file_handler_size_based(self):
        """测试创建基于大小的轮转处理器"""
        handler = self.create_rotating_file_handler(
            "./logs/test_rotation.log",
            self.LogRotationConfig(use_timed_rotation=False),
        )
        self.assertIsInstance(handler, logging.handlers.RotatingFileHandler)
        handler.close()
    
    def test_create_rotating_file_handler_time_based(self):
        """测试创建基于时间的轮转处理器"""
        handler = self.create_rotating_file_handler(
            "./logs/test_timed.log",
            self.LogRotationConfig(use_timed_rotation=True),
        )
        self.assertIsInstance(handler, logging.handlers.TimedRotatingFileHandler)
        handler.close()
    
    def test_setup_agent_logging_basic(self):
        """测试设置 Agent 日志系统"""
        logger = self.setup_agent_logging(
            debug_mode=False,
            enable_console=False,
            enable_file=False,
        )
        self.assertIsInstance(logger, logging.Logger)
    
    def test_setup_error_logging(self):
        """测试设置错误日志系统"""
        logger = self.setup_error_logging(
            log_file="./logs/test_errors.log",
        )
        self.assertIsInstance(logger, logging.Logger)
        self.assertEqual(logger.level, logging.ERROR)


class TestErrorHandler(unittest.TestCase):
    """错误处理器测试"""
    
    def setUp(self):
        """设置测试环境"""
        from agent.error_handler import (
            get_error_handler,
            ErrorHandler,
            YunshuError,
            RecoverableError,
            CriticalError,
            ErrorSeverity,
            ErrorCategory,
        )
        
        self.get_error_handler = get_error_handler
        self.YunshuError = YunshuError
        self.RecoverableError = RecoverableError
        self.CriticalError = CriticalError
        self.ErrorSeverity = ErrorSeverity
        self.ErrorCategory = ErrorCategory
    
    def test_error_handler_singleton(self):
        """测试错误处理器单例"""
        handler1 = self.get_error_handler()
        handler2 = self.get_error_handler()
        self.assertIs(handler1, handler2)
    
    def test_Yunshu_error_basic(self):
        """测试 YunshuError 基本功能"""
        error = self.YunshuError(
            "test error",
            severity=self.ErrorSeverity.WARNING,
            category=self.ErrorCategory.DATA_INVALID,
            context={"key": "value"},
        )
        self.assertEqual(error.message, "test error")
        self.assertEqual(error.severity, self.ErrorSeverity.WARNING)
        self.assertEqual(error.category, self.ErrorCategory.DATA_INVALID)
    
    def test_Yunshu_error_to_dict(self):
        """测试 YunshuError 转换为字典"""
        error = self.YunshuError("test")
        error_dict = error.to_dict()
        self.assertIsInstance(error_dict, dict)
        self.assertIn('message', error_dict)
        self.assertIn('severity', error_dict)
    
    def test_recoverable_error(self):
        """测试可恢复错误"""
        error = self.RecoverableError("recoverable")
        self.assertTrue(error.recoverable)
        self.assertTrue(error.retryable)
    
    def test_critical_error(self):
        """测试严重错误"""
        error = self.CriticalError("critical")
        self.assertTrue(error.requires_restart)
        self.assertEqual(error.severity, self.ErrorSeverity.CRITICAL)
    
    def test_error_handler_record_error(self):
        """测试错误处理器记录错误"""
        handler = self.get_error_handler()
        error = self.YunshuError("test record")
        result = handler.record_error(error)
        
        # 验证返回的是标准化的 YunshuError
        self.assertIsInstance(result, self.YunshuError)
        
        # 验证指标已更新
        metrics = handler.get_metrics("YunshuError")
        self.assertGreater(metrics.get("total_count", 0), 0)


class TestCircuitBreaker(unittest.TestCase):
    """熔断器测试"""
    
    def setUp(self):
        """设置测试环境"""
        from agent.error_handler import CircuitBreaker, CircuitState
        
        self.CircuitBreaker = CircuitBreaker
        self.CircuitState = CircuitState
    
    def test_circuit_breaker_initial_state(self):
        """测试熔断器初始状态"""
        cb = self.CircuitBreaker(max_failures=3)
        self.assertEqual(cb.state, self.CircuitState.CLOSED)
    
    def test_circuit_breaker_open_after_failures(self):
        """测试熔断器在失败后打开"""
        cb = self.CircuitBreaker(max_failures=2)
        
        def failing_func():
            raise ValueError("fail")
        
        # 第一次失败
        with self.assertRaises(ValueError):
            cb.execute(failing_func)
        
        # 第二次失败，熔断器应该打开
        with self.assertRaises(Exception):
            cb.execute(failing_func)
        
        self.assertEqual(cb.state, self.CircuitState.OPEN)
    
    def test_circuit_breaker_record_success(self):
        """测试熔断器记录成功"""
        cb = self.CircuitBreaker(max_failures=2)
        
        def success_func():
            return "success"
        
        result = cb.execute(success_func)
        self.assertEqual(result, "success")
        self.assertEqual(cb.success_count, 1)


if __name__ == "__main__":
    # 设置日志级别避免测试输出过多
    logging.basicConfig(level=logging.WARNING)
    
    # 创建测试套件
    suite = unittest.TestSuite()
    
    # 添加所有测试类
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestConfigValidation))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestErrorHandlingDecorators))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestModuleImportErrorHandling))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestLogRotation))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestErrorHandler))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestCircuitBreaker))
    
    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)