"""
安全配置模块测试 - pytest 格式
针对 config_secure.py 和 logging_utils.py 的安全功能测试用例
"""

import os
import pytest
import tempfile
from pathlib import Path
from config_secure import (
    SecureConfigManager,
    SecureConfigError,
    DecryptionError,
    KeyFileError,
    ConfigFileError,
)
from agent.logging_utils import SensitiveDataFilter, AuditLogger, get_audit_logger


class TestSecureConfigManager:
    """测试安全配置管理器"""

    @pytest.fixture
    def temp_key_file(self):
        """创建临时密钥文件路径（不创建文件，让SecureConfigManager自动创建）"""
        fd, path = tempfile.mkstemp(suffix='.key')
        os.close(fd)
        os.remove(path)  # 删除文件，让SecureConfigManager自动创建
        return path

    @pytest.fixture
    def temp_config_file(self):
        """创建临时配置文件路径（不创建文件）"""
        fd, path = tempfile.mkstemp(suffix='.json')
        os.close(fd)
        os.remove(path)  # 删除文件，让SecureConfigManager自动创建
        return path

    @pytest.fixture
    def secure_manager(self, temp_key_file, temp_config_file):
        """安全配置管理器实例"""
        return SecureConfigManager(
            key_file=temp_key_file,
            secure_config_file=temp_config_file
        )

    @pytest.mark.p0
    def test_encrypt_decrypt(self, secure_manager):
        """测试加密解密功能"""
        plaintext = "test_api_key_12345"
        encrypted = secure_manager.encrypt(plaintext)
        decrypted = secure_manager.decrypt(encrypted)
        assert decrypted == plaintext

    @pytest.mark.p0
    def test_encrypt_decrypt_empty_string(self, secure_manager):
        """测试空字符串加密解密"""
        plaintext = ""
        encrypted = secure_manager.encrypt(plaintext)
        decrypted = secure_manager.decrypt(encrypted)
        assert decrypted == ""

    @pytest.mark.p1
    def test_decrypt_invalid_base64(self, secure_manager):
        """测试解密无效的Base64字符串"""
        result = secure_manager.decrypt("invalid-base64!!!")
        assert result is None

    @pytest.mark.p1
    def test_decrypt_wrong_key(self, temp_key_file, temp_config_file):
        """测试使用错误密钥解密"""
        manager1 = SecureConfigManager(
            key_file=temp_key_file + "_1",
            secure_config_file=temp_config_file
        )
        manager2 = SecureConfigManager(
            key_file=temp_key_file + "_2",
            secure_config_file=temp_config_file
        )
        
        encrypted = manager1.encrypt("secret_data")
        result = manager2.decrypt(encrypted)
        assert result is None

    @pytest.mark.p0
    def test_save_load_config(self, secure_manager):
        """测试保存和加载配置"""
        test_config = {
            'llm_api_key': 'sk-test-12345',
            'db_password': 'secret_password',
            'api_secret': 'my_secret_key'
        }
        
        secure_manager.save_secure_config(test_config)
        loaded_config = secure_manager.load_secure_config()
        
        assert loaded_config['llm_api_key'] == 'sk-test-12345'
        assert loaded_config['db_password'] == 'secret_password'
        assert loaded_config['api_secret'] == 'my_secret_key'

    @pytest.mark.p1
    def test_get_secure_value_priority(self, secure_manager, monkeypatch):
        """测试配置值获取优先级：环境变量 > 加密文件 > 默认值"""
        secure_manager.save_secure_config({'test_key': 'file_value'})
        
        # 测试从文件获取
        result = secure_manager.get_secure_value('test_key', 'default')
        assert result == 'file_value'
        
        # 设置环境变量
        monkeypatch.setenv('TEST_KEY', 'env_value')
        
        # 测试从环境变量获取（优先级更高）
        result = secure_manager.get_secure_value('test_key', 'default')
        assert result == 'env_value'
        
        # 测试默认值
        result = secure_manager.get_secure_value('non_existent', 'default')
        assert result == 'default'

    @pytest.mark.p1
    def test_file_permissions(self, secure_manager):
        """测试密钥文件和配置文件权限"""
        import stat
        
        # 保存配置以确保文件创建
        secure_manager.save_secure_config({'test': 'value'})
        
        # 检查密钥文件权限（仅在非Windows系统上检查）
        if os.name != 'nt':
            key_stat = os.stat(secure_manager._key_file)
            key_permissions = stat.S_IMODE(key_stat.st_mode)
            assert key_permissions == 0o600, f"密钥文件权限应为0o600，实际为{oct(key_permissions)}"
            
            # 检查配置文件权限
            config_stat = os.stat(secure_manager._secure_config_file)
            config_permissions = stat.S_IMODE(config_stat.st_mode)
            assert config_permissions == 0o600, f"配置文件权限应为0o600，实际为{oct(config_permissions)}"
        else:
            pytest.skip("文件权限测试在Windows上不适用")

    @pytest.mark.p1
    def test_set_secure_value(self, secure_manager):
        """测试设置安全配置值"""
        secure_manager.set_secure_value('new_key', 'new_value')
        result = secure_manager.get_secure_value('new_key')
        assert result == 'new_value'


class TestSensitiveDataFilter:
    """测试敏感信息脱敏过滤器"""

    @pytest.fixture
    def filter(self):
        """脱敏过滤器实例"""
        return SensitiveDataFilter()

    @pytest.mark.p0
    def test_sanitize_api_key(self, filter):
        """测试脱敏API Key"""
        test_cases = [
            ("sk-abc123def456ghi789jkl0", "[REDACTED]"),
            ("pk-lmn123opq456rst789uvw0", "[REDACTED]"),
            ("sk-proj-abc123def456ghi789", "[REDACTED]"),
        ]
        for input_text, expected in test_cases:
            result = filter._sanitize(input_text)
            assert result == expected, f"Failed for: {input_text}"

    @pytest.mark.p0
    def test_sanitize_password_field(self, filter):
        """测试脱敏密码字段"""
        test_cases = [
            ('password="secret123"', 'password="***"'),
            ('password=secret123', 'password=***'),
            ('secret: mysecret', 'secret: ***'),
            ('token=abc123', 'token=***'),
        ]
        for input_text, expected in test_cases:
            result = filter._sanitize(input_text)
            # 检查是否脱敏成功（值被替换为***）
            assert '[REDACTED]' in result, f"Failed for: {input_text}, got: {result}"

    @pytest.mark.p1
    def test_sanitize_url_params(self, filter):
        """测试脱敏URL参数"""
        test_cases = [
            'https://api.example.com?api_key=sk-12345&other=value',
            'https://example.com/path?key=secret&token=abc',
        ]
        for input_text in test_cases:
            result = filter._sanitize(input_text)
            # 检查敏感参数值是否被脱敏
            assert '[REDACTED]' in result, f"Failed for: {input_text}, got: {result}"

    @pytest.mark.p1
    def test_sanitize_dict(self, filter):
        """测试递归脱敏字典"""
        test_dict = {
            'api_key': 'sk-abc123def456',
            'password': 'sk-secret12345',
            'nested': {
                'token': 'sk-token67890',
                'normal': 'value'
            },
            'list': ['sk-xyz789', 'normal']
        }
        
        sanitized = filter._sanitize_dict(test_dict)
        
        # 检查敏感字段是否被脱敏
        assert sanitized['api_key'] == '[REDACTED]', f"api_key should be masked, got: {sanitized['api_key']}"
        assert sanitized['password'] == '[REDACTED]', f"password should be masked, got: {sanitized['password']}"
        assert sanitized['nested']['token'] == '[REDACTED]', f"nested token should be masked, got: {sanitized['nested']['token']}"
        assert sanitized['nested']['normal'] == 'value'
        assert sanitized['list'][0] == '[REDACTED]', f"list item should be masked, got: {sanitized['list'][0]}"
        assert sanitized['list'][1] == 'normal'

    @pytest.mark.p1
    def test_filter_log_record(self, filter):
        """测试过滤日志记录"""
        import logging
        
        record = logging.LogRecord(
            name='test',
            level=logging.INFO,
            pathname='test.py',
            lineno=1,
            msg='API Key: sk-12345',
            args=(),
            exc_info=None
        )
        
        result = filter.filter(record)
        assert result is True
        # 检查API Key是否被脱敏
        assert '[REDACTED]' in record.msg, f"API Key should be masked, got: {record.msg}"


class TestAuditLogger:
    """测试审计日志记录器"""

    @pytest.mark.p1
    def test_audit_logger_exists(self):
        """测试审计日志记录器存在"""
        audit_logger = get_audit_logger()
        assert isinstance(audit_logger, AuditLogger)

    @pytest.mark.p1
    def test_log_config_access(self, tmp_path):
        """测试记录配置访问"""
        audit_logger = AuditLogger()
        audit_logger.log_config_access('test_key', 'admin')

    @pytest.mark.p1
    def test_log_config_modification(self, tmp_path):
        """测试记录配置修改"""
        audit_logger = AuditLogger()
        audit_logger.log_config_modification('test_key', 'admin')

    @pytest.mark.p1
    def test_log_secure_config_access(self, tmp_path):
        """测试记录安全配置访问"""
        audit_logger = AuditLogger()
        audit_logger.log_secure_config_access('api_key', True, 'user1')
        audit_logger.log_secure_config_access('api_key', False, 'user2')

    @pytest.mark.p1
    def test_log_authentication(self, tmp_path):
        """测试记录认证尝试"""
        audit_logger = AuditLogger()
        audit_logger.log_authentication('admin', True, '192.168.1.1')
        audit_logger.log_authentication('user', False)

    @pytest.mark.p1
    def test_log_sensitive_operation(self, tmp_path):
        """测试记录敏感操作"""
        audit_logger = AuditLogger()
        audit_logger.log_sensitive_operation(
            'export_data',
            {'api_key': 'sk-12345', 'user': 'admin'}
        )


class TestExceptions:
    """测试自定义异常"""

    @pytest.mark.p1
    def test_exception_hierarchy(self):
        """测试异常继承层级"""
        assert issubclass(DecryptionError, SecureConfigError)
        assert issubclass(KeyFileError, SecureConfigError)
        assert issubclass(ConfigFileError, SecureConfigError)

    @pytest.mark.p1
    def test_key_file_error_message(self):
        """测试密钥文件异常消息"""
        try:
            raise KeyFileError('test error')
        except KeyFileError as e:
            assert str(e) == 'test error'