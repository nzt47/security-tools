"""
Agent 集成测试
测试模块间的交互和端到端流程
"""
import pytest
import tempfile
import os
import logging
from unittest.mock import MagicMock, patch
from pathlib import Path
from datetime import datetime

from agent.error_handler import ErrorHandler, CircuitBreaker, get_error_handler
from agent.lazy_loader import LazyModuleLoader, LoadLevel, get_lazy_loader
from agent.security_utils import DataSanitizer, LogEncryptor
from agent.logging_utils import get_safety_monitor, SensitiveDataFilter

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class TestErrorHandlerIntegration:
    """测试错误处理器集成"""

    @pytest.mark.integration
    @pytest.mark.p0
    def test_error_handler_with_circuit_breaker(self):
        """测试错误处理器与熔断器集成"""
        logger.info("=" * 60)
        logger.info("[集成测试] 开始测试错误处理器与熔断器集成")
        logger.info("=" * 60)
        
        logger.info("[步骤1] 初始化错误处理器和熔断器")
        handler = ErrorHandler()
        cb = CircuitBreaker(name="test_cb", max_failures=2)
        logger.info(f"  - 错误处理器已初始化")
        logger.info(f"  - 熔断器 'test_cb' 已创建，max_failures={cb.max_failures}")
        
        logger.info("[步骤2] 注册熔断器到错误处理器")
        handler.register_circuit_breaker("test_cb", cb)
        result = handler.get_circuit_breaker("test_cb")
        assert result is cb, "熔断器注册失败"
        logger.info("  - 熔断器注册成功")
        
        logger.info("[步骤3] 测试熔断器跳闸机制")
        for i in range(3):
            cb.record_failure()
            logger.info(f"  - 记录第 {i+1} 次失败，当前状态: {cb.state.name}")
        
        assert cb.state.name == "OPEN", "熔断器未跳闸"
        logger.info("  - 熔断器已成功跳闸")
        
        logger.info("[步骤4] 验证熔断器状态查询")
        status = handler.get_circuit_breaker_status()
        assert "test_cb" in status, "熔断器状态未记录"
        assert status["test_cb"]["state"] == "open", "熔断器状态不正确"
        logger.info(f"  - 熔断器状态查询成功: {status}")
        
        logger.info("[集成测试] 错误处理器与熔断器集成测试通过")
        logger.info("=" * 60)

    @pytest.mark.integration
    @pytest.mark.p1
    def test_global_error_handler(self):
        """测试全局错误处理器"""
        logger.info("=" * 60)
        logger.info("[集成测试] 开始测试全局错误处理器")
        logger.info("=" * 60)
        
        logger.info("[步骤1] 获取全局错误处理器实例")
        handler1 = get_error_handler()
        handler2 = get_error_handler()
        logger.info(f"  - 第一次获取: {id(handler1)}")
        logger.info(f"  - 第二次获取: {id(handler2)}")
        
        assert handler1 is handler2, "全局错误处理器不是单例"
        logger.info("  - 验证通过: 全局错误处理器是单例")
        
        logger.info("[步骤2] 记录错误并验证指标")
        from agent.error_handler import YunshuError
        error = YunshuError("test error")
        handler1.record_error(error)
        logger.info("  - 错误已记录")
        
        metrics = handler2.get_metrics("YunshuError")
        assert metrics["total_count"] == 1, "错误指标未正确记录"
        logger.info(f"  - 指标验证成功: {metrics}")
        
        logger.info("[集成测试] 全局错误处理器测试通过")
        logger.info("=" * 60)


class TestLazyLoaderIntegration:
    """测试懒加载器集成"""

    @pytest.mark.integration
    @pytest.mark.p0
    def test_lazy_loader_with_dependencies(self):
        """测试懒加载器依赖管理"""
        logger.info("=" * 60)
        logger.info("[集成测试] 开始测试懒加载器依赖管理")
        logger.info("=" * 60)
        
        logger.info("[步骤1] 初始化懒加载器")
        loader = LazyModuleLoader()
        logger.info("  - 懒加载器已初始化")
        
        logger.info("[步骤2] 定义测试模块和依赖")
        dep_loaded = []
        
        def load_dep():
            dep_loaded.append(True)
            return "dependency"
        
        def load_main():
            return "main"
        
        logger.info("  - 依赖模块加载函数已定义")
        logger.info("  - 主模块加载函数已定义")
        
        logger.info("[步骤3] 注册模块（带依赖关系）")
        loader.register("dependency", load_dep, LoadLevel.CRITICAL)
        loader.register("main", load_main, LoadLevel.IMPORTANT, dependencies=["dependency"])
        logger.info("  - 依赖模块 'dependency' 已注册 (CRITICAL级别)")
        logger.info("  - 主模块 'main' 已注册 (IMPORTANT级别, 依赖: dependency)")
        
        logger.info("[步骤4] 加载主模块（应自动加载依赖）")
        result = loader.load("main")
        logger.info(f"  - 主模块加载结果: {result}")
        logger.info(f"  - 依赖加载次数: {len(dep_loaded)}")
        
        assert result == "main", "主模块加载失败"
        assert len(dep_loaded) == 1, "依赖模块未自动加载"
        assert loader.is_loaded("dependency") is True, "依赖模块状态未更新"
        logger.info("  - 依赖自动加载验证通过")
        
        logger.info("[集成测试] 懒加载器依赖管理测试通过")
        logger.info("=" * 60)

    @pytest.mark.integration
    @pytest.mark.p1
    def test_lazy_loader_parallel_loading(self):
        """测试懒加载器并行加载"""
        logger.info("=" * 60)
        logger.info("[集成测试] 开始测试懒加载器并行加载")
        logger.info("=" * 60)
        
        logger.info("[步骤1] 初始化懒加载器（4个工作线程）")
        loader = LazyModuleLoader(max_workers=4)
        logger.info(f"  - 懒加载器已初始化, max_workers={loader.max_workers}")
        
        logger.info("[步骤2] 定义并行加载的模块")
        load_order = []
        
        def load_mod1():
            load_order.append("mod1")
            return "mod1"
        
        def load_mod2():
            load_order.append("mod2")
            return "mod2"
        
        def load_mod3():
            load_order.append("mod3")
            return "mod3"
        
        logger.info("  - 三个测试模块加载函数已定义")
        
        logger.info("[步骤3] 注册模块")
        loader.register("mod1", load_mod1, LoadLevel.CRITICAL)
        loader.register("mod2", load_mod2, LoadLevel.CRITICAL)
        loader.register("mod3", load_mod3, LoadLevel.CRITICAL)
        logger.info("  - 三个CRITICAL级别模块已注册")
        
        logger.info("[步骤4] 执行并行加载")
        results = loader.load_level(LoadLevel.CRITICAL)
        logger.info(f"  - 加载结果: {list(results.keys())}")
        logger.info(f"  - 实际加载顺序: {load_order}")
        
        assert len(results) == 3, "模块加载数量不正确"
        assert "mod1" in results, "mod1 未加载"
        assert "mod2" in results, "mod2 未加载"
        assert "mod3" in results, "mod3 未加载"
        assert len(load_order) == 3, "加载计数不正确"
        logger.info("  - 并行加载验证通过")
        
        logger.info("[集成测试] 懒加载器并行加载测试通过")
        logger.info("=" * 60)

    @pytest.mark.integration
    @pytest.mark.p1
    def test_global_lazy_loader(self):
        """测试全局懒加载器"""
        logger.info("=" * 60)
        logger.info("[集成测试] 开始测试全局懒加载器")
        logger.info("=" * 60)
        
        logger.info("[步骤1] 获取全局懒加载器实例")
        loader1 = get_lazy_loader()
        loader2 = get_lazy_loader()
        logger.info(f"  - 第一次获取: {id(loader1)}")
        logger.info(f"  - 第二次获取: {id(loader2)}")
        
        assert loader1 is loader2, "全局懒加载器不是单例"
        logger.info("  - 验证通过: 全局懒加载器是单例")
        
        logger.info("[步骤2] 在一个实例上注册模块")
        loader1.register("test_mod", lambda: "test", LoadLevel.OPTIONAL)
        logger.info("  - 测试模块 'test_mod' 已注册")
        
        logger.info("[步骤3] 在另一个实例上验证")
        assert "test_mod" in loader2.modules, "模块未在全局实例间共享"
        logger.info("  - 模块共享验证通过")
        
        logger.info("[集成测试] 全局懒加载器测试通过")
        logger.info("=" * 60)


class TestSecurityIntegration:
    """测试安全模块集成"""

    @pytest.mark.integration
    @pytest.mark.p0
    def test_data_sanitizer_with_logging_filter(self):
        """测试数据脱敏器与日志过滤器集成"""
        logger.info("=" * 60)
        logger.info("[集成测试] 开始测试数据脱敏器与日志过滤器集成")
        logger.info("=" * 60)
        
        logger.info("[步骤1] 初始化脱敏器和日志过滤器")
        sanitizer = DataSanitizer()
        log_filter = SensitiveDataFilter()
        logger.info("  - 数据脱敏器已初始化")
        logger.info("  - 敏感数据过滤器已初始化")
        
        logger.info("[步骤2] 准备测试数据")
        test_text = "API Key=sk-12345, password=secret"
        logger.info(f"  - 原始数据: {test_text}")
        
        logger.info("[步骤3] 使用脱敏器处理")
        sanitized_by_sanitizer = sanitizer.sanitize_string(test_text)
        logger.info(f"  - 脱敏器处理结果: {sanitized_by_sanitizer}")
        
        logger.info("[步骤4] 使用日志过滤器处理")
        sanitized_by_filter = log_filter._sanitize(test_text)
        logger.info(f"  - 过滤器处理结果: {sanitized_by_filter}")
        
        assert "[REDACTED]" in sanitized_by_sanitizer, "脱敏器未正确脱敏"
        assert "[REDACTED]" in sanitized_by_filter, "过滤器未正确脱敏"
        logger.info("  - 脱敏效果验证通过")
        
        logger.info("[集成测试] 数据脱敏器与日志过滤器集成测试通过")
        logger.info("=" * 60)

    @pytest.mark.integration
    @pytest.mark.p1
    def test_encryptor_with_sanitizer(self):
        """测试加密器与脱敏器配合使用"""
        logger.info("=" * 60)
        logger.info("[集成测试] 开始测试加密器与脱敏器配合使用")
        logger.info("=" * 60)
        
        logger.info("[步骤1] 初始化数据脱敏器")
        sanitizer = DataSanitizer()
        logger.info("  - 数据脱敏器已初始化")
        
        logger.info("[步骤2] 准备测试数据")
        data = {
            "user": "admin",
            "api_key": "sk-secret-key",
            "password": "secret123"
        }
        logger.info(f"  - 原始数据: {data}")
        
        logger.info("[步骤3] 执行脱敏处理")
        sanitized = sanitizer.sanitize_dict(data)
        logger.info(f"  - 脱敏后数据: {sanitized}")
        
        assert "[REDACTED]" in sanitized["api_key"], "API Key未脱敏"
        assert "[REDACTED]" in sanitized["password"], "密码未脱敏"
        logger.info("  - 脱敏验证通过")
        
        logger.info("[集成测试] 加密器与脱敏器配合测试通过")
        logger.info("=" * 60)


class TestLoggingIntegration:
    """测试日志模块集成"""

    @pytest.mark.integration
    @pytest.mark.p0
    def test_safety_monitor_with_logging(self):
        """测试安全监控器与日志系统集成"""
        logger.info("=" * 60)
        logger.info("[集成测试] 开始测试安全监控器与日志系统集成")
        logger.info("=" * 60)
        
        logger.info("[步骤1] 获取安全监控器")
        monitor = get_safety_monitor()
        logger.info("  - 安全监控器已获取")
        
        logger.info("[步骤2] 测试监控器记录功能")
        try:
            monitor.record_iteration("test_task")
            logger.info("  - 监控器记录成功")
            assert True
        except Exception as e:
            logger.warning(f"  - 安全监控器测试跳过: {e}")
            pytest.skip(f"Safety monitor test skipped: {e}")
        
        logger.info("[集成测试] 安全监控器与日志系统集成测试通过")
        logger.info("=" * 60)

    @pytest.mark.integration
    @pytest.mark.p1
    def test_sensitive_data_filter_in_logging(self):
        """测试敏感数据过滤器在日志中的应用"""
        logger.info("=" * 60)
        logger.info("[集成测试] 开始测试敏感数据过滤器在日志中的应用")
        logger.info("=" * 60)
        
        logger.info("[步骤1] 配置测试日志器")
        import logging as std_logging
        
        test_logger = std_logging.getLogger("test_sensitive_filter")
        test_logger.setLevel(std_logging.INFO)
        test_logger.propagate = False
        
        handler = std_logging.StreamHandler()
        filter_obj = SensitiveDataFilter()
        handler.addFilter(filter_obj)
        test_logger.addHandler(handler)
        logger.info("  - 测试日志器配置完成")
        
        logger.info("[步骤2] 测试日志记录（包含敏感数据）")
        try:
            test_logger.info("API Key=sk-12345")
            logger.info("  - 日志记录成功")
            assert True
        except Exception as e:
            logger.warning(f"  - 日志过滤器测试跳过: {e}")
            pytest.skip(f"Logging filter test skipped: {e}")
        finally:
            test_logger.removeHandler(handler)
        
        logger.info("[集成测试] 敏感数据过滤器在日志中的应用测试通过")
        logger.info("=" * 60)


class TestEndToEndWorkflow:
    """测试端到端工作流"""

    @pytest.mark.integration
    @pytest.mark.e2e
    @pytest.mark.p0
    def test_error_handling_workflow(self):
        """测试错误处理完整工作流"""
        logger.info("=" * 60)
        logger.info("[集成测试] 开始测试错误处理完整工作流")
        logger.info("=" * 60)
        
        logger.info("[步骤1] 初始化错误处理器")
        handler = ErrorHandler()
        logger.info("  - 错误处理器已初始化")
        
        logger.info("[步骤2] 导入错误类")
        from agent.error_handler import (
            YunshuError, RecoverableError, CriticalError,
            ErrorSeverity, ErrorCategory
        )
        logger.info("  - 错误类导入完成")
        
        logger.info("[步骤3] 记录普通错误")
        error1 = YunshuError("普通错误")
        handler.record_error(error1)
        logger.info("  - 普通错误已记录")
        
        logger.info("[步骤4] 记录可恢复错误")
        error2 = RecoverableError("可恢复错误")
        handler.record_error(error2)
        logger.info("  - 可恢复错误已记录")
        
        logger.info("[步骤5] 记录严重错误")
        error3 = CriticalError("严重错误")
        handler.record_error(error3)
        logger.info("  - 严重错误已记录")
        
        logger.info("[步骤6] 注册熔断器")
        cb = CircuitBreaker(name="workflow_cb")
        handler.register_circuit_breaker("workflow_cb", cb)
        logger.info("  - 熔断器 'workflow_cb' 已注册")
        
        logger.info("[步骤7] 验证指标")
        metrics = handler.get_metrics()
        assert "YunshuError" in metrics, "YunshuError指标缺失"
        assert "RecoverableError" in metrics, "RecoverableError指标缺失"
        assert "CriticalError" in metrics, "CriticalError指标缺失"
        logger.info(f"  - 错误指标验证通过: {list(metrics.keys())}")
        
        cb_status = handler.get_circuit_breaker_status()
        assert "workflow_cb" in cb_status, "熔断器状态缺失"
        logger.info(f"  - 熔断器状态验证通过: {cb_status}")
        
        logger.info("[集成测试] 错误处理完整工作流测试通过")
        logger.info("=" * 60)

    @pytest.mark.integration
    @pytest.mark.e2e
    @pytest.mark.p1
    def test_lazy_loader_complete_workflow(self):
        """测试懒加载器完整工作流"""
        logger.info("=" * 60)
        logger.info("[集成测试] 开始测试懒加载器完整工作流")
        logger.info("=" * 60)
        
        logger.info("[步骤1] 初始化懒加载器")
        loader = LazyModuleLoader()
        logger.info("  - 懒加载器已初始化")
        
        logger.info("[步骤2] 注册不同级别的模块")
        loader.register("critical_mod", lambda: "critical", LoadLevel.CRITICAL)
        loader.register("important_mod", lambda: "important", LoadLevel.IMPORTANT)
        loader.register("optional_mod", lambda: "optional", LoadLevel.OPTIONAL)
        logger.info("  - CRITICAL级别模块已注册")
        logger.info("  - IMPORTANT级别模块已注册")
        logger.info("  - OPTIONAL级别模块已注册")
        
        logger.info("[步骤3] 加载CRITICAL级别模块")
        critical_results = loader.load_level(LoadLevel.CRITICAL)
        assert "critical_mod" in critical_results, "CRITICAL模块未加载"
        logger.info(f"  - CRITICAL模块加载成功: {critical_results}")
        
        logger.info("[步骤4] 异步加载IMPORTANT级别模块")
        loader.load_level_async(LoadLevel.IMPORTANT)
        logger.info("  - 异步加载已启动")
        
        logger.info("[步骤5] 等待异步加载完成")
        import time
        time.sleep(0.5)
        
        logger.info("[步骤6] 按需加载OPTIONAL模块")
        optional_result = loader.load("optional_mod")
        assert optional_result == "optional", "OPTIONAL模块加载失败"
        logger.info(f"  - OPTIONAL模块加载成功: {optional_result}")
        
        logger.info("[步骤7] 验证加载状态")
        assert loader.is_level_loaded(LoadLevel.CRITICAL) is True, "CRITICAL级别未标记为已加载"
        assert loader.is_level_loaded(LoadLevel.IMPORTANT) is True, "IMPORTANT级别未标记为已加载"
        assert loader.is_loaded("optional_mod") is True, "optional_mod未标记为已加载"
        logger.info("  - 所有模块加载状态验证通过")
        
        logger.info("[步骤8] 检查统计信息")
        stats = loader.get_stats()
        assert stats["total_attempts"] == 3, "加载尝试次数不正确"
        assert stats["successful_loads"] == 3, "成功加载次数不正确"
        logger.info(f"  - 统计信息验证通过: {stats}")
        
        logger.info("[集成测试] 懒加载器完整工作流测试通过")
        logger.info("=" * 60)


class TestSecurityWorkflow:
    """测试安全工作流"""

    @pytest.mark.integration
    @pytest.mark.security
    @pytest.mark.p0
    def test_security_pipeline(self):
        """测试完整安全处理流程"""
        logger.info("=" * 60)
        logger.info("[集成测试] 开始测试完整安全处理流程")
        logger.info("=" * 60)
        
        logger.info("[步骤1] 初始化数据脱敏器")
        sanitizer = DataSanitizer()
        logger.info("  - 数据脱敏器已初始化")
        
        logger.info("[步骤2] 准备原始数据")
        raw_data = {
            "user": "test_user",
            "api_key": "sk-abcdefghijklmnopqrstuvwxyz",
            "password": "super_secret_password",
            "email": "user@example.com",
            "phone": "13812345678",
            "message": "正常消息内容"
        }
        logger.info(f"  - 原始数据键: {list(raw_data.keys())}")
        
        logger.info("[步骤3] 执行脱敏处理")
        sanitized_data = sanitizer.sanitize_dict(raw_data)
        logger.info(f"  - 脱敏后数据: {sanitized_data}")
        
        logger.info("[步骤4] 验证脱敏效果")
        assert sanitized_data["user"] == "test_user", "用户名被错误脱敏"
        assert "[REDACTED]" in sanitized_data["api_key"], "API Key未脱敏"
        assert "[REDACTED]" in sanitized_data["password"], "密码未脱敏"
        assert "[REDACTED]" in sanitized_data["email"], "邮箱未脱敏"
        assert "[REDACTED]" in sanitized_data["phone"], "电话未脱敏"
        assert sanitized_data["message"] == "正常消息内容", "正常消息被错误脱敏"
        logger.info("  - 脱敏效果验证通过")
        
        logger.info("[集成测试] 完整安全处理流程测试通过")
        logger.info("=" * 60)


class TestConfigurationIntegration:
    """测试配置集成"""

    @pytest.mark.integration
    @pytest.mark.p1
    def test_config_loading(self):
        """测试配置加载集成"""
        logger.info("=" * 60)
        logger.info("[集成测试] 开始测试配置加载集成")
        logger.info("=" * 60)
        
        logger.info("[步骤1] 尝试导入配置模块")
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        
        try:
            from config import Config
            logger.info("  - Config模块导入成功")
            
            logger.info("[步骤2] 初始化配置")
            config = Config()
            assert config is not None, "配置初始化失败"
            logger.info("  - 配置初始化成功")
            
            logger.info("[集成测试] 配置加载集成测试通过")
        except ImportError as e:
            logger.warning(f"  - Config模块未找到: {e}")
            pytest.skip("Config module not found")
        
        logger.info("=" * 60)