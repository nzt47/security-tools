"""logging_utils 单元测试（涉及文件IO）"""
import pytest
import logging
from agent.logging_utils import (
    LogRotationConfig,
    create_rotating_file_handler,
    SensitiveDataFilter,
    AuditLogger,
    get_audit_logger,
    setup_agent_logging,
    setup_error_logging,
    AgentSafetyMonitor,
    get_safety_monitor,
    safe_execute
)

# 配置测试日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_logging_utils")


def test_log_rotation_config_default():
    """默认日志轮转配置"""
    logger.info("测试: 默认日志轮转配置")
    config = LogRotationConfig()
    logger.info(f"  max_bytes: {config.max_bytes}, 预期: {50 * 1024 * 1024}")
    logger.info(f"  backup_count: {config.backup_count}, 预期: 5")
    logger.info(f"  encoding: {config.encoding}, 预期: utf-8")
    assert config.max_bytes == 50 * 1024 * 1024  # 50MB
    assert config.backup_count == 5
    assert config.encoding == "utf-8"


def test_log_rotation_config_custom():
    """自定义日志轮转配置"""
    logger.info("测试: 自定义日志轮转配置")
    config = LogRotationConfig(
        max_bytes=10 * 1024 * 1024,
        backup_count=10,
        use_timed_rotation=True
    )
    logger.info(f"  max_bytes: {config.max_bytes}, 预期: {10 * 1024 * 1024}")
    logger.info(f"  backup_count: {config.backup_count}, 预期: 10")
    logger.info(f"  use_timed_rotation: {config.use_timed_rotation}, 预期: True")
    assert config.max_bytes == 10 * 1024 * 1024
    assert config.backup_count == 10
    assert config.use_timed_rotation is True


def test_log_rotation_config_to_dict():
    """转换为字典"""
    logger.info("测试: 转换为字典")
    config = LogRotationConfig()
    config_dict = config.to_dict()
    logger.info(f"  config_dict: {config_dict}")
    assert isinstance(config_dict, dict)
    assert "max_bytes" in config_dict


def test_create_rotating_file_handler(tmp_path):
    """创建轮转文件处理器"""
    logger.info("测试: 创建轮转文件处理器")
    log_file = str(tmp_path / "test.log")
    config = LogRotationConfig()
    logger.info(f"  log_file: {log_file}")
    
    handler = create_rotating_file_handler(log_file, config)
    logger.info(f"  handler创建成功: {handler is not None}")
    assert handler is not None
    handler.close()


def test_sensitive_data_filter_api_key():
    """脱敏API密钥"""
    logger.info("测试: 脱敏API密钥")
    filter = SensitiveDataFilter()
    
    text = "API Key=sk-abcdefghijklmnopqrstuvwxyz123456"
    sanitized = filter._sanitize(text)
    logger.info(f"  原文: '{text}'")
    logger.info(f"  脱敏后: '{sanitized}'")
    assert "sk-" not in sanitized or "***" in sanitized


def test_sensitive_data_filter_password():
    """脱敏密码"""
    logger.info("测试: 脱敏密码")
    filter = SensitiveDataFilter()
    
    text = 'password="my_secret_password"'
    sanitized = filter._sanitize(text)
    logger.info(f"  原文: '{text}'")
    logger.info(f"  脱敏后: '{sanitized}'")
    assert "my_secret_password" not in sanitized
    assert "***" in sanitized or "[REDACTED]" in sanitized


def test_sensitive_data_filter_phone():
    """脱敏手机号"""
    logger.info("测试: 脱敏手机号")
    filter = SensitiveDataFilter()
    
    text = "联系电话：13812345678"
    sanitized = filter._sanitize(text)
    logger.info(f"  原文: '{text}'")
    logger.info(f"  脱敏后: '{sanitized}'")
    assert "138****5678" == sanitized or "***" in sanitized


def test_sensitive_data_filter_id_card():
    """脱敏身份证号"""
    logger.info("测试: 脱敏身份证号")
    filter = SensitiveDataFilter()
    
    text = "身份证：110101199001011234"
    sanitized = filter._sanitize(text)
    logger.info(f"  原文: '{text}'")
    logger.info(f"  脱敏后: '{sanitized}'")
    assert "110101********1234" in sanitized or "***" in sanitized


def test_sensitive_data_filter_email():
    """脱敏邮箱"""
    logger.info("测试: 脱敏邮箱")
    filter = SensitiveDataFilter()
    
    text = "邮箱：user@example.com"
    sanitized = filter._sanitize(text)
    logger.info(f"  原文: '{text}'")
    logger.info(f"  脱敏后: '{sanitized}'")
    assert "@" not in sanitized or "***" in sanitized


def test_sensitive_data_filter_dict():
    """脱敏字典"""
    logger.info("测试: 脱敏字典")
    filter = SensitiveDataFilter()
    
    data = {
        "api_key": "sk-test123456",
        "password": "secret123",
        "normal_field": "value"
    }
    
    sanitized = filter._sanitize_dict(data)
    logger.info(f"  原数据: {data}")
    logger.info(f"  脱敏后: {sanitized}")
    assert sanitized["api_key"] != "sk-test123456"
    assert sanitized["password"] != "secret123"
    assert sanitized["normal_field"] == "value"


def test_sensitive_data_filter_list():
    """脱敏列表"""
    logger.info("测试: 脱敏列表")
    filter = SensitiveDataFilter()
    
    data = {
        "items": ["password=secret", "normal text"]
    }
    
    sanitized = filter._sanitize_dict(data)
    logger.info(f"  原数据: {data}")
    logger.info(f"  脱敏后: {sanitized}")
    assert "secret" not in sanitized["items"][0]


def test_audit_logger_log_config_access():
    """审计日志记录配置访问"""
    logger.info("测试: 审计日志记录配置访问")
    audit_logger = AuditLogger()
    audit_logger.log_config_access("test_key", "test_user")
    logger.info(f"  已记录配置访问: test_key, test_user")


def test_audit_logger_log_authentication():
    """审计日志记录认证"""
    logger.info("测试: 审计日志记录认证")
    audit_logger = AuditLogger()
    audit_logger.log_authentication("test_user", True, "192.168.1.1")
    logger.info(f"  已记录认证: test_user, True, 192.168.1.1")


def test_global_audit_logger():
    """全局审计日志实例"""
    logger.info("测试: 全局审计日志实例")
    logger1 = get_audit_logger()
    logger2 = get_audit_logger()
    logger.info(f"  logger1 is logger2: {logger1 is logger2}")
    assert logger1 is logger2


def test_safety_monitor_record_iteration():
    """安全监控器记录迭代"""
    logger.info("测试: 安全监控器记录迭代")
    monitor = AgentSafetyMonitor(max_iterations_per_minute=10)
    
    result = monitor.record_iteration("test_task")
    logger.info(f"  record_iteration结果: {result}")
    assert result is True


def test_safety_monitor_check_state():
    """安全监控器检查状态"""
    logger.info("测试: 安全监控器检查状态")
    monitor = AgentSafetyMonitor()
    
    result = monitor.check_state("test_task", "running")
    logger.info(f"  check_state结果: {result}")
    assert result is True


def test_global_safety_monitor():
    """全局安全监控器实例"""
    logger.info("测试: 全局安全监控器实例")
    monitor1 = get_safety_monitor()
    monitor2 = get_safety_monitor()
    logger.info(f"  monitor1 is monitor2: {monitor1 is monitor2}")
    assert monitor1 is monitor2


def test_safe_execute():
    """安全执行包装器"""
    logger.info("测试: 安全执行包装器")
    def test_func():
        return "success"
    
    result = safe_execute(test_func, timeout=5)
    logger.info(f"  执行结果: {result}")
    assert result == "success"


def test_safe_execute_timeout():
    """安全执行包装器（超时测试）"""
    logger.info("测试: 安全执行包装器（超时测试）")
    import time
    
    def slow_func():
        time.sleep(2)
        return "too slow"
    
    result = safe_execute(slow_func, timeout=0.1)
    logger.info(f"  超时执行结果: {result}")
    assert result is None


def test_safe_execute_with_exception():
    """安全执行包装器（异常处理）"""
    logger.info("测试: 安全执行包装器（异常处理）")
    def error_func():
        raise ValueError("test error")
    
    with pytest.raises(ValueError):
        safe_execute(error_func)
    logger.info(f"  异常已被正确捕获")


def test_setup_agent_logging(tmp_path):
    """设置Agent日志系统"""
    logger.info("测试: 设置Agent日志系统")
    log_file = str(tmp_path / "agent.log")
    logger.info(f"  log_file: {log_file}")
    
    agent_logger = setup_agent_logging(
        debug_mode=True,
        log_file=log_file,
        enable_console=False,
        enable_file=True
    )
    logger.info(f"  logger创建成功: {agent_logger is not None}")
    assert agent_logger is not None


def test_setup_error_logging(tmp_path):
    """设置错误日志"""
    logger.info("测试: 设置错误日志")
    log_file = str(tmp_path / "errors.log")
    logger.info(f"  log_file: {log_file}")
    
    error_logger = setup_error_logging(log_file=log_file)
    logger.info(f"  logger创建成功: {error_logger is not None}, name: {error_logger.name}")
    assert error_logger is not None
    assert error_logger.name == "agent.errors"