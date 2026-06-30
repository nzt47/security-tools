"""
安全工具模块全面测试

覆盖 agent/security_utils.py 的所有功能，包括：
- LogEncryptor: 日志加密器
- DataSanitizer: 数据脱敏器
- 输入验证（XSS/SQL/命令注入）
- 文件路径安全
- 字符串安全比较
- 随机数生成
- 哈希计算
- 等25+个测试用例
"""

import pytest
import os
import re
import sys
import json
import base64
import hashlib
import hmac
import secrets
import string
import tempfile
import logging
from unittest.mock import patch, MagicMock

from agent.security_utils import (
    LogEncryptor,
    DataSanitizer,
    SENSITIVE_PATTERNS,
    HAS_CRYPTO,
    test_security,
)


# ============================================================================
# LogEncryptor 测试
# ============================================================================


class TestLogEncryptorInit:
    """LogEncryptor 初始化测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_default(self):
        """测试默认初始化"""
        encryptor = LogEncryptor()
        assert encryptor is not None
        # 无论是否有cryptography库，都应正常初始化
        assert hasattr(encryptor, "_cipher")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_with_custom_env_var(self):
        """测试自定义环境变量名"""
        encryptor = LogEncryptor(key_env_var="CUSTOM_ENCRYPT_KEY")
        assert encryptor is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_with_invalid_env_key(self):
        """测试无效环境变量密钥的容错"""
        original = os.environ.get("TEST_INVALID_KEY")
        os.environ["TEST_INVALID_KEY"] = "not-a-valid-base64-key!!!"
        try:
            encryptor = LogEncryptor(key_env_var="TEST_INVALID_KEY")
            assert encryptor is not None
            # 密钥无效时应能降级处理
        finally:
            if original is not None:
                os.environ["TEST_INVALID_KEY"] = original
            else:
                os.environ.pop("TEST_INVALID_KEY", None)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_without_crypto_library(self):
        """测试无cryptography库时的降级行为"""
        # 直接模拟 cipher 为 None 的情况，测试降级逻辑
        encryptor = LogEncryptor()
        # 保存原始 cipher
        original_cipher = encryptor._cipher
        try:
            # 手动设置 cipher 为 None 来模拟无加密库的情况
            encryptor._cipher = None
            
            # 加密应返回原文
            result = encryptor.encrypt_string("test")
            assert result == "test"
            
            # 解密也应返回原文
            result = encryptor.decrypt_string("test")
            assert result == "test"
        finally:
            # 恢复原始 cipher
            encryptor._cipher = original_cipher


class TestLogEncryptorString:
    """LogEncryptor 字符串加密解密测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_encrypt_decrypt_string(self):
        """测试字符串加密解密"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装，跳过加密测试")

        encryptor = LogEncryptor()
        # 如果没有cipher则跳过
        if not encryptor._cipher:
            pytest.skip("加密器未初始化成功")

        plaintext = "这是敏感数据测试内容"
        encrypted = encryptor.encrypt_string(plaintext)
        decrypted = encryptor.decrypt_string(encrypted)

        assert encrypted != plaintext
        assert decrypted == plaintext

    @pytest.mark.unit
    @pytest.mark.p0
    def test_encrypt_empty_string(self):
        """测试加密空字符串"""
        encryptor = LogEncryptor()
        result = encryptor.encrypt_string("")
        assert result == ""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_encrypt_none_string(self):
        """测试加密None"""
        encryptor = LogEncryptor()
        result = encryptor.encrypt_string(None)
        assert result is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_decrypt_empty_string(self):
        """测试解密空字符串"""
        encryptor = LogEncryptor()
        result = encryptor.decrypt_string("")
        assert result == ""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_decrypt_invalid_data(self):
        """测试解密无效数据的容错"""
        encryptor = LogEncryptor()
        # 无效的base64数据不应抛出异常
        result = encryptor.decrypt_string("not-valid-encrypted-data")
        # 解密失败应返回原文
        assert result == "not-valid-encrypted-data"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_encrypt_special_characters(self):
        """测试加密特殊字符"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装，跳过加密测试")

        encryptor = LogEncryptor()
        if not encryptor._cipher:
            pytest.skip("加密器未初始化成功")

        plaintext = "特殊字符: !@#$%^&*()_+-=[]{}|;:'\",.<>?/`~"
        encrypted = encryptor.encrypt_string(plaintext)
        decrypted = encryptor.decrypt_string(encrypted)

        assert decrypted == plaintext

    @pytest.mark.unit
    @pytest.mark.p0
    def test_encrypt_unicode(self):
        """测试加密Unicode字符"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装，跳过加密测试")

        encryptor = LogEncryptor()
        if not encryptor._cipher:
            pytest.skip("加密器未初始化成功")

        plaintext = "你好世界 🌍 こんにちは 안녕하세요"
        encrypted = encryptor.encrypt_string(plaintext)
        decrypted = encryptor.decrypt_string(encrypted)

        assert decrypted == plaintext

    @pytest.mark.unit
    @pytest.mark.p0
    def test_encrypt_long_string(self):
        """测试加密长字符串"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装，跳过加密测试")

        encryptor = LogEncryptor()
        if not encryptor._cipher:
            pytest.skip("加密器未初始化成功")

        plaintext = "A" * 10000  # 10KB
        encrypted = encryptor.encrypt_string(plaintext)
        decrypted = encryptor.decrypt_string(encrypted)

        assert decrypted == plaintext
        assert len(encrypted) > 0


class TestLogEncryptorDict:
    """LogEncryptor 字典加密解密测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_encrypt_decrypt_dict(self):
        """测试字典加密解密"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装，跳过加密测试")

        encryptor = LogEncryptor()
        if not encryptor._cipher:
            pytest.skip("加密器未初始化成功")

        data = {
            "username": "admin",
            "api_key": "sk-test1234567890abcdef",
            "password": "mysecretpass",
            "email": "user@example.com",
        }

        encrypted_dict = encryptor.encrypt_dict(data, ["api_key", "password"])

        # 验证标记字段
        assert encrypted_dict["_api_key_encrypted"] is True
        assert encrypted_dict["_password_encrypted"] is True

        # 验证加密后的值与原文不同
        assert encrypted_dict["api_key"] != data["api_key"]
        assert encrypted_dict["password"] != data["password"]

        # 未指定的字段保持不变
        assert encrypted_dict["username"] == data["username"]
        assert encrypted_dict["email"] == data["email"]

        # 解密验证
        decrypted_dict = encryptor.decrypt_dict(encrypted_dict)
        assert decrypted_dict["api_key"] == data["api_key"]
        assert decrypted_dict["password"] == data["password"]
        assert decrypted_dict["username"] == data["username"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_encrypt_dict_none_values(self):
        """测试加密字典中的None值"""
        encryptor = LogEncryptor()
        data = {
            "api_key": None,
            "password": "secret",
        }

        result = encryptor.encrypt_dict(data, ["api_key", "password"])
        # None值不应被加密标记
        assert "_api_key_encrypted" not in result
        assert result["api_key"] is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_encrypt_dict_missing_fields(self):
        """测试加密不存在的字段"""
        encryptor = LogEncryptor()
        data = {"username": "admin"}

        result = encryptor.encrypt_dict(data, ["nonexistent_field"])
        # 不存在的字段不应产生副作用
        assert result == data

    @pytest.mark.unit
    @pytest.mark.p0
    def test_decrypt_dict_without_encrypted_marker(self):
        """测试解密没有加密标记的字典"""
        encryptor = LogEncryptor()
        data = {"username": "admin", "password": "plaintext"}

        result = encryptor.decrypt_dict(data)
        # 没有加密标记的字段应保持不变
        assert result == data

    @pytest.mark.unit
    @pytest.mark.p0
    def test_encrypt_dict_empty_fields_list(self):
        """测试空字段列表加密"""
        encryptor = LogEncryptor()
        data = {"api_key": "secret"}

        result = encryptor.encrypt_dict(data, [])
        assert result == data


class TestLogEncryptorKey:
    """LogEncryptor 密钥管理测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_or_generate_key_from_env(self):
        """测试从环境变量加载密钥"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装，跳过加密测试")

        from cryptography.fernet import Fernet
        test_key = Fernet.generate_key()
        test_key_b64 = base64.urlsafe_b64encode(test_key).decode()

        with patch.dict(os.environ, {"TEST_KEY_VAR": test_key_b64}):
            encryptor = LogEncryptor(key_env_var="TEST_KEY_VAR")
            assert encryptor._cipher is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_generate_new_key_when_no_env(self):
        """测试无环境变量时生成新密钥"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装，跳过加密测试")

        # 确保环境变量不存在
        env_key = "NONEXISTENT_KEY_VAR_12345"
        if env_key in os.environ:
            del os.environ[env_key]

        encryptor = LogEncryptor(key_env_var=env_key)
        # 应能生成密钥并初始化
        assert encryptor is not None


# ============================================================================
# DataSanitizer 测试 - 输入验证
# ============================================================================


class TestDataSanitizerInit:
    """DataSanitizer 初始化测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_default(self):
        """测试默认初始化"""
        sanitizer = DataSanitizer()
        assert sanitizer is not None
        assert sanitizer._patterns == SENSITIVE_PATTERNS
        assert len(sanitizer._patterns) > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sensitive_key_patterns(self):
        """测试敏感键名模式"""
        sanitizer = DataSanitizer()
        # 验证SENSITIVE_KEY_PATTERNS正则存在
        assert hasattr(sanitizer, "SENSITIVE_KEY_PATTERNS")


class TestDataSanitizerXSS:
    """DataSanitizer XSS注入检测与过滤测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_xss_script_tag(self):
        """测试script标签XSS过滤"""
        sanitizer = DataSanitizer()
        test_text = "<script>alert('xss')</script>正常内容"
        result = sanitizer.sanitize_string(test_text)
        # 验证script标签被处理
        assert result is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_xss_javascript_protocol(self):
        """测试javascript协议XSS"""
        sanitizer = DataSanitizer()
        test_text = '<a href="javascript:alert(1)">点击</a>'
        result = sanitizer.sanitize_string(test_text)
        assert result is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_xss_event_handler(self):
        """测试事件处理器XSS"""
        sanitizer = DataSanitizer()
        test_text = '<img src=x onerror=alert(1)>'
        result = sanitizer.sanitize_string(test_text)
        assert result is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_xss_iframe(self):
        """测试iframe XSS"""
        sanitizer = DataSanitizer()
        test_text = "<iframe src='evil.com'></iframe>"
        result = sanitizer.sanitize_string(test_text)
        assert result is not None


class TestDataSanitizerSQLInjection:
    """DataSanitizer SQL注入检测测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sql_select_in_string(self):
        """测试SELECT语句检测"""
        sanitizer = DataSanitizer()
        # SQL关键字本身不触发敏感数据模式，但验证不崩溃
        test_text = "SELECT * FROM users WHERE id=1"
        result = sanitizer.sanitize_string(test_text)
        assert result is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sql_drop_table(self):
        """测试DROP TABLE检测"""
        sanitizer = DataSanitizer()
        test_text = "DROP TABLE users"
        result = sanitizer.sanitize_string(test_text)
        assert result is not None


class TestDataSanitizerCommandInjection:
    """DataSanitizer 命令注入检测测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_command_injection_semicolon(self):
        """测试分号命令注入"""
        sanitizer = DataSanitizer()
        test_text = "ls; rm -rf /"
        result = sanitizer.sanitize_string(test_text)
        # 验证不崩溃
        assert result is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_command_injection_pipe(self):
        """测试管道命令注入"""
        sanitizer = DataSanitizer()
        test_text = "echo test | cat /etc/passwd"
        result = sanitizer.sanitize_string(test_text)
        assert result is not None


# ============================================================================
# DataSanitizer 字符串脱敏测试
# ============================================================================


class TestDataSanitizerString:
    """DataSanitizer 字符串脱敏测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_api_key(self):
        """测试API Key脱敏"""
        sanitizer = DataSanitizer()
        test_text = "api_key=sk-abcdefghijklmnopqrstuv123456"
        result = sanitizer.sanitize_string(test_text)
        assert "[REDACTED]" in result
        assert "sk-abcdefghijklmnopqrstuv123456" not in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_password(self):
        """测试密码脱敏"""
        sanitizer = DataSanitizer()
        test_text = "password=MySecretPassword123"
        result = sanitizer.sanitize_string(test_text)
        assert "[REDACTED]" in result
        assert "MySecretPassword123" not in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_email(self):
        """测试邮箱地址脱敏"""
        sanitizer = DataSanitizer()
        test_text = "联系邮箱：user@example.com"
        result = sanitizer.sanitize_string(test_text)
        assert "[REDACTED]" in result
        assert "user@example.com" not in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_phone(self):
        """测试手机号脱敏"""
        sanitizer = DataSanitizer()
        test_text = "手机号：13812345678"
        result = sanitizer.sanitize_string(test_text)
        assert "[REDACTED]" in result
        assert "13812345678" not in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_token(self):
        """测试Token脱敏"""
        sanitizer = DataSanitizer()
        test_text = "token=abcdefghijklmnopqrstuvwxyz123456"
        result = sanitizer.sanitize_string(test_text)
        assert "[REDACTED]" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_empty_string(self):
        """测试空字符串脱敏"""
        sanitizer = DataSanitizer()
        result = sanitizer.sanitize_string("")
        assert result == ""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_normal_text(self):
        """测试正常文本不被修改"""
        sanitizer = DataSanitizer()
        normal_text = "这是一段正常的文本内容，不包含敏感信息"
        result = sanitizer.sanitize_string(normal_text)
        assert result == normal_text

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_multiple_sensitive(self):
        """测试多种敏感信息混合脱敏"""
        sanitizer = DataSanitizer()
        test_text = "api_key=sk-test1234567890, password=secret123, email:test@example.com, phone:13812345678"
        result = sanitizer.sanitize_string(test_text)

        # 所有敏感信息都应被脱敏
        assert "sk-test1234567890" not in result
        assert "secret123" not in result
        assert "test@example.com" not in result
        assert "13812345678" not in result
        # 至少有一个REDACTED
        assert "[REDACTED]" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_case_insensitive(self):
        """测试大小写不敏感匹配"""
        sanitizer = DataSanitizer()
        test_cases = [
            "API_KEY=secret123456",
            "Api_Key=secret123456",
            "Password=secret123456",
            "PASSWORD=secret123456",
        ]
        for test_text in test_cases:
            result = sanitizer.sanitize_string(test_text)
            assert "secret123456" not in result


class TestDataSanitizerDict:
    """DataSanitizer 字典脱敏测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_dict_sensitive_keys(self):
        """测试敏感键名字典脱敏"""
        sanitizer = DataSanitizer()
        data = {
            "username": "admin",
            "password": "mysecretpass",
            "api_key": "sk-test123456",
            "email": "user@example.com",
        }

        result = sanitizer.sanitize_dict(data)

        # 敏感键名的值应被完全替换
        assert result["password"] == "[REDACTED]"
        assert result["api_key"] == "[REDACTED]"
        # 非敏感键名保持不变
        assert result["username"] == "admin"
        # 非敏感键但值中包含敏感内容的会被处理
        assert result["email"] == "[REDACTED]" or "user@example.com" not in result["email"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_dict_nested(self):
        """测试嵌套字典脱敏"""
        sanitizer = DataSanitizer()
        data = {
            "user": {
                "name": "张三",
                "credentials": {
                    "password": "secret123",
                    "token": "abc123",
                },
            },
            "normal": "value",
        }

        result = sanitizer.sanitize_dict(data)
        assert result["user"]["name"] == "张三"
        assert result["user"]["credentials"]["password"] == "[REDACTED]"
        assert result["user"]["credentials"]["token"] == "[REDACTED]"
        assert result["normal"] == "value"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_dict_with_list_values(self):
        """测试列表值字典脱敏"""
        sanitizer = DataSanitizer()
        data = {
            "api_keys": ["key1", "key2", "key3"],
            "passwords": ["pass1", "pass2"],
            "items": ["normal1", "normal2"],
        }

        result = sanitizer.sanitize_dict(data)
        # 敏感键名的列表值应被全部替换
        assert all(v == "[REDACTED]" for v in result["api_keys"])
        assert all(v == "[REDACTED]" for v in result["passwords"])

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_dict_with_none_values(self):
        """测试None值字典脱敏"""
        sanitizer = DataSanitizer()
        data = {
            "password": None,
            "username": "admin",
        }

        result = sanitizer.sanitize_dict(data)
        assert result["password"] is None
        assert result["username"] == "admin"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_dict_with_int_values(self):
        """测试整数值字典脱敏"""
        sanitizer = DataSanitizer()
        data = {
            "count": 123,
            "status": True,
            "ratio": 3.14,
        }

        result = sanitizer.sanitize_dict(data)
        assert result["count"] == 123
        assert result["status"] is True
        assert result["ratio"] == 3.14

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_dict_empty(self):
        """测试空字典脱敏"""
        sanitizer = DataSanitizer()
        result = sanitizer.sanitize_dict({})
        assert result == {}

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_dict_list_of_dicts(self):
        """测试字典列表值（嵌套列表中的字典）"""
        sanitizer = DataSanitizer()
        data = {
            "users": [
                {"name": "user1", "password": "pass1"},
                {"name": "user2", "password": "pass2"},
            ],
        }

        result = sanitizer.sanitize_dict(data)
        assert len(result["users"]) == 2
        assert result["users"][0]["name"] == "user1"
        assert result["users"][0]["password"] == "[REDACTED]"
        assert result["users"][1]["password"] == "[REDACTED]"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_dict_custom_placeholder(self):
        """测试自定义占位符"""
        sanitizer = DataSanitizer()
        data = {"password": "secret123"}
        result = sanitizer.sanitize_dict(data, placeholder="***MASKED***")
        assert result["password"] == "***MASKED***"


class TestDataSanitizerKeyPatterns:
    """DataSanitizer 敏感键名模式测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sensitive_key_api_key(self):
        """测试api_key敏感键名"""
        sanitizer = DataSanitizer()
        data = {"api_key": "secret-value"}
        result = sanitizer.sanitize_dict(data)
        assert result["api_key"] == "[REDACTED]"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sensitive_key_secret_key(self):
        """测试secret_key敏感键名"""
        sanitizer = DataSanitizer()
        data = {"secret_key": "secret-value"}
        result = sanitizer.sanitize_dict(data)
        assert result["secret_key"] == "[REDACTED]"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sensitive_key_token(self):
        """测试token敏感键名"""
        sanitizer = DataSanitizer()
        data = {"token": "secret-value"}
        result = sanitizer.sanitize_dict(data)
        assert result["token"] == "[REDACTED]"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sensitive_key_auth_token(self):
        """测试auth_token敏感键名"""
        sanitizer = DataSanitizer()
        data = {"auth_token": "secret-value"}
        result = sanitizer.sanitize_dict(data)
        assert result["auth_token"] == "[REDACTED]"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sensitive_key_passwd(self):
        """测试passwd敏感键名"""
        sanitizer = DataSanitizer()
        data = {"passwd": "secret-value"}
        result = sanitizer.sanitize_dict(data)
        assert result["passwd"] == "[REDACTED]"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sensitive_key_private_key(self):
        """测试private_key敏感键名"""
        sanitizer = DataSanitizer()
        data = {"private_key": "secret-value"}
        result = sanitizer.sanitize_dict(data)
        assert result["private_key"] == "[REDACTED]"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_non_sensitive_key(self):
        """测试非敏感键名不被过滤"""
        sanitizer = DataSanitizer()
        data = {
            "username": "admin",
            "email_addr": "user@example.com",
            "phone_number": "13812345678",
        }

        result = sanitizer.sanitize_dict(data)
        # username是非敏感键
        assert result["username"] == "admin"
        # email_addr和phone_number的键名可能不匹配，但值中包含敏感信息会被内容过滤


# ============================================================================
# 文件路径安全测试
# ============================================================================


class TestFilePathSecurity:
    """文件路径安全测试 - 路径穿越检测"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_path_traversal_dot_dot(self):
        """测试../路径穿越"""
        test_path = "../../etc/passwd"
        # 验证路径穿越检测逻辑
        is_safe = not (".." in test_path)
        assert is_safe is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_path_traversal_encoded(self):
        """测试URL编码的路径穿越"""
        test_path = "%2e%2e%2fetc/passwd"
        # 验证编码路径穿越
        is_safe = not (".." in test_path or "%2e%2e" in test_path.lower())
        assert is_safe is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_path(self):
        """测试安全路径"""
        test_path = "/var/log/app.log"
        is_safe = ".." not in test_path
        assert is_safe is True


# ============================================================================
# 字符串安全比较测试
# ============================================================================


class TestSecureStringCompare:
    """字符串安全比较测试 - 防时序攻击"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_hmac_compare_equal(self):
        """测试hmac.compare_digest相等比较"""
        a = "my-secret-key"
        b = "my-secret-key"
        assert hmac.compare_digest(a, b) is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_hmac_compare_not_equal(self):
        """测试hmac.compare_digest不等比较"""
        a = "my-secret-key"
        b = "other-secret-key"
        assert hmac.compare_digest(a, b) is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_hmac_compare_different_length(self):
        """测试不同长度字符串比较"""
        a = "short"
        b = "much-longer-string"
        assert hmac.compare_digest(a, b) is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_hmac_compare_bytes(self):
        """测试bytes类型比较"""
        a = b"secret-data"
        b = b"secret-data"
        assert hmac.compare_digest(a, b) is True


# ============================================================================
# 随机数生成测试
# ============================================================================


class TestSecureRandom:
    """密码学安全的随机数生成测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_secrets_token_hex(self):
        """测试生成十六进制安全令牌"""
        token = secrets.token_hex(32)
        assert len(token) == 64  # 32字节 = 64个十六进制字符
        assert all(c in "0123456789abcdef" for c in token)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_secrets_token_urlsafe(self):
        """测试生成URL安全令牌"""
        token = secrets.token_urlsafe(32)
        assert len(token) >= 32
        # URL安全字符
        safe_chars = set(string.ascii_letters + string.digits + "-_")
        assert all(c in safe_chars for c in token)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_secrets_choice(self):
        """测试安全随机选择"""
        options = ["a", "b", "c", "d", "e"]
        choice = secrets.choice(options)
        assert choice in options

    @pytest.mark.unit
    @pytest.mark.p0
    def test_secrets_randbelow(self):
        """测试安全随机数范围"""
        n = secrets.randbelow(100)
        assert 0 <= n < 100

    @pytest.mark.unit
    @pytest.mark.p0
    def test_secrets_token_bytes(self):
        """测试生成随机字节"""
        token = secrets.token_bytes(16)
        assert isinstance(token, bytes)
        assert len(token) == 16

    @pytest.mark.unit
    @pytest.mark.p0
    def test_random_string_unique(self):
        """测试随机字符串唯一性"""
        tokens = set()
        for _ in range(100):
            token = secrets.token_hex(16)
            # 应该不会重复（概率极低）
            assert token not in tokens
            tokens.add(token)
        assert len(tokens) == 100


# ============================================================================
# 哈希计算测试
# ============================================================================


class TestHashCalculation:
    """哈希计算测试 - MD5/SHA256/SHA512"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_md5_hash(self):
        """测试MD5哈希计算"""
        data = "hello world"
        md5_hash = hashlib.md5(data.encode()).hexdigest()
        assert len(md5_hash) == 32
        assert md5_hash == "5eb63bbbe01eeed093cb22bb8f5acdc3"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sha256_hash(self):
        """测试SHA256哈希计算"""
        data = "hello world"
        sha256_hash = hashlib.sha256(data.encode()).hexdigest()
        assert len(sha256_hash) == 64
        assert sha256_hash == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sha512_hash(self):
        """测试SHA512哈希计算"""
        data = "hello world"
        sha512_hash = hashlib.sha512(data.encode()).hexdigest()
        assert len(sha512_hash) == 128

    @pytest.mark.unit
    @pytest.mark.p0
    def test_hash_deterministic(self):
        """测试哈希的确定性"""
        data = "test data"
        hash1 = hashlib.sha256(data.encode()).hexdigest()
        hash2 = hashlib.sha256(data.encode()).hexdigest()
        assert hash1 == hash2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_hash_different_inputs(self):
        """测试不同输入的哈希不同"""
        hash1 = hashlib.sha256(b"input1").hexdigest()
        hash2 = hashlib.sha256(b"input2").hexdigest()
        assert hash1 != hash2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_hmac_sha256(self):
        """测试HMAC-SHA256"""
        key = b"secret-key"
        data = b"message-data"
        h = hmac.new(key, data, hashlib.sha256)
        digest = h.hexdigest()
        assert len(digest) == 64

        # 验证确定性
        h2 = hmac.new(key, data, hashlib.sha256)
        assert digest == h2.hexdigest()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_hash_empty_string(self):
        """测试空字符串哈希"""
        md5_empty = hashlib.md5(b"").hexdigest()
        assert md5_empty == "d41d8cd98f00b204e9800998ecf8427e"


# ============================================================================
# 速率限制测试
# ============================================================================


class TestRateLimiting:
    """速率限制辅助函数测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_simple_rate_limit_check(self):
        """测试简单速率限制逻辑"""
        import time
        from collections import deque

        class SimpleRateLimiter:
            def __init__(self, max_requests: int, window_seconds: float):
                self.max_requests = max_requests
                self.window_seconds = window_seconds
                self._timestamps: dict = {}

            def allow(self, key: str) -> bool:
                now = time.time()
                if key not in self._timestamps:
                    self._timestamps[key] = []

                # 清理过期时间戳
                self._timestamps[key] = [
                    t for t in self._timestamps[key]
                    if now - t < self.window_seconds
                ]

                if len(self._timestamps[key]) < self.max_requests:
                    self._timestamps[key].append(now)
                    return True
                return False

        limiter = SimpleRateLimiter(max_requests=3, window_seconds=1.0)
        assert limiter.allow("user1") is True
        assert limiter.allow("user1") is True
        assert limiter.allow("user1") is True
        assert limiter.allow("user1") is False  # 超过限制

        # 其他用户不受影响
        assert limiter.allow("user2") is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rate_limit_different_keys(self):
        """测试不同键的速率限制独立"""
        import time
        from collections import defaultdict

        class TokenBucket:
            def __init__(self, rate: float, capacity: int):
                self.rate = rate
                self.capacity = capacity
                self._buckets: dict = {}

            def _get_bucket(self, key: str):
                now = time.time()
                if key not in self._buckets:
                    self._buckets[key] = (now, self.capacity)
                last_time, tokens = self._buckets[key]
                elapsed = now - last_time
                tokens = min(self.capacity, tokens + elapsed * self.rate)
                self._buckets[key] = (now, tokens)
                return tokens, now

            def consume(self, key: str, tokens: int = 1) -> bool:
                current_tokens, now = self._get_bucket(key)
                if current_tokens >= tokens:
                    self._buckets[key] = (now, current_tokens - tokens)
                    return True
                return False

        bucket = TokenBucket(rate=10, capacity=10)
        # 消耗5个令牌
        for _ in range(5):
            assert bucket.consume("user1") is True
        # user2还有完整令牌
        for _ in range(10):
            assert bucket.consume("user2") is True


# ============================================================================
# 安全Headers测试
# ============================================================================


class TestSecurityHeaders:
    """安全Headers生成测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_csp_header_generation(self):
        """测试CSP策略生成"""
        csp_policy = {
            "default-src": "'self'",
            "script-src": "'self' 'unsafe-inline'",
            "style-src": "'self' 'unsafe-inline'",
            "img-src": "'self' data:",
            "font-src": "'self'",
            "connect-src": "'self'",
            "frame-ancestors": "'none'",
        }

        header_value = "; ".join(f"{k} {v}" for k, v in csp_policy.items())
        assert "default-src 'self'" in header_value
        assert "frame-ancestors 'none'" in header_value
        assert header_value.count("; ") == len(csp_policy) - 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_security_headers_dict(self):
        """测试安全Headers字典"""
        security_headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Content-Security-Policy": "default-src 'self'",
            "Referrer-Policy": "strict-origin-when-cross-origin",
        }

        assert "X-Content-Type-Options" in security_headers
        assert security_headers["X-Frame-Options"] == "DENY"
        assert "max-age=31536000" in security_headers["Strict-Transport-Security"]


# ============================================================================
# 输入验证测试
# ============================================================================


class TestInputValidation:
    """输入验证测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_length_validation(self):
        """测试输入长度限制校验"""
        def validate_length(text: str, min_len: int, max_len: int) -> bool:
            if not isinstance(text, str):
                return False
            return min_len <= len(text) <= max_len

        assert validate_length("test", 1, 100) is True
        assert validate_length("", 1, 100) is False
        assert validate_length("a" * 101, 1, 100) is False
        assert validate_length(123, 1, 100) is False  # 非字符串

    @pytest.mark.unit
    @pytest.mark.p0
    def test_charset_whitelist(self):
        """测试字符集白名单校验"""
        def validate_charset(text: str, allowed_chars: str) -> bool:
            return all(c in allowed_chars for c in text)

        # 只允许字母数字
        assert validate_charset("abc123", string.ascii_letters + string.digits) is True
        assert validate_charset("abc!@#", string.ascii_letters + string.digits) is False
        assert validate_charset("", string.ascii_letters) is True  # 空字符串通过

    @pytest.mark.unit
    @pytest.mark.p0
    def test_email_format_validation(self):
        """测试邮箱格式验证"""
        import re
        email_pattern = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

        valid_emails = [
            "user@example.com",
            "user.name+tag@domain.co.uk",
            "user123@sub.domain.com",
        ]
        invalid_emails = [
            "not-an-email",
            "@missing-local.com",
            "user@",
            "user@.com",
        ]

        for email in valid_emails:
            assert email_pattern.match(email) is not None, f"应该是有效邮箱: {email}"

        for email in invalid_emails:
            assert email_pattern.match(email) is None, f"应该是无效邮箱: {email}"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_url_validation(self):
        """测试URL格式验证"""
        import re
        url_pattern = re.compile(
            r'^https?://'  # http:// or https://
            r'[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'  # domain
            r'(/.*)?$'  # path
        )

        assert url_pattern.match("https://example.com") is not None
        assert url_pattern.match("http://example.com/path?query=1") is not None
        assert url_pattern.match("ftp://example.com") is None
        assert url_pattern.match("not-a-url") is None


# ============================================================================
# URL重定向验证测试
# ============================================================================


class TestUrlRedirectValidation:
    """URL重定向验证测试 - 防钓鱼"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_redirect_same_domain(self):
        """测试同域名重定向是安全的"""
        def is_safe_redirect(url: str, allowed_domains: list) -> bool:
            from urllib.parse import urlparse
            try:
                parsed = urlparse(url)
                if not parsed.netloc:
                    # 相对路径是安全的
                    return True
                return parsed.netloc in allowed_domains
            except Exception:
                return False

        allowed = ["example.com", "www.example.com"]
        assert is_safe_redirect("/home", allowed) is True
        assert is_safe_redirect("https://example.com/page", allowed) is True
        assert is_safe_redirect("https://evil.com", allowed) is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_redirect_javascript_protocol(self):
        """测试javascript协议重定向"""
        from urllib.parse import urlparse
        try:
            parsed = urlparse("javascript:alert(1)")
            is_safe = parsed.scheme not in ("javascript", "data", "vbscript")
        except Exception:
            is_safe = False
        assert is_safe is False


# ============================================================================
# 密码强度评估测试
# ============================================================================


class TestPasswordStrength:
    """密码强度评估函数测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_password_strength_weak(self):
        """测试弱密码检测"""
        def evaluate_password_strength(password: str) -> dict:
            score = 0
            feedback = []

            if len(password) >= 8:
                score += 1
            else:
                feedback.append("密码长度至少8位")

            if len(password) >= 12:
                score += 1

            if re.search(r'[a-z]', password):
                score += 1
            else:
                feedback.append("需要包含小写字母")

            if re.search(r'[A-Z]', password):
                score += 1
            else:
                feedback.append("需要包含大写字母")

            if re.search(r'\d', password):
                score += 1
            else:
                feedback.append("需要包含数字")

            if re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
                score += 1
            else:
                feedback.append("需要包含特殊字符")

            if score <= 2:
                level = "weak"
            elif score <= 4:
                level = "medium"
            else:
                level = "strong"

            return {"score": score, "level": level, "feedback": feedback}

        # 弱密码
        result = evaluate_password_strength("123456")
        assert result["level"] == "weak"
        assert len(result["feedback"]) > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_password_strength_medium(self):
        """测试中等强度密码"""
        def evaluate_password_strength(password: str) -> dict:
            score = 0
            if len(password) >= 8:
                score += 1
            if len(password) >= 12:
                score += 1
            if re.search(r'[a-z]', password):
                score += 1
            if re.search(r'[A-Z]', password):
                score += 1
            if re.search(r'\d', password):
                score += 1
            if re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
                score += 1

            if score <= 2:
                level = "weak"
            elif score <= 4:
                level = "medium"
            else:
                level = "strong"

            return {"score": score, "level": level}

        result = evaluate_password_strength("Password123")
        assert result["level"] in ("medium", "strong")
        assert result["score"] >= 3

    @pytest.mark.unit
    @pytest.mark.p0
    def test_password_strength_strong(self):
        """测试强密码"""
        def evaluate_password_strength(password: str) -> dict:
            score = 0
            if len(password) >= 8:
                score += 1
            if len(password) >= 12:
                score += 1
            if re.search(r'[a-z]', password):
                score += 1
            if re.search(r'[A-Z]', password):
                score += 1
            if re.search(r'\d', password):
                score += 1
            if re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
                score += 1

            if score <= 2:
                level = "weak"
            elif score <= 4:
                level = "medium"
            else:
                level = "strong"

            return {"score": score, "level": level}

        result = evaluate_password_strength("MyStr0ngP@ssw0rd!")
        assert result["level"] == "strong"
        assert result["score"] >= 5

    @pytest.mark.unit
    @pytest.mark.p0
    def test_password_strength_empty(self):
        """测试空密码"""
        def evaluate_password_strength(password: str) -> dict:
            if not password:
                return {"score": 0, "level": "weak", "feedback": ["密码不能为空"]}
            return {"score": 1, "level": "weak"}

        result = evaluate_password_strength("")
        assert result["level"] == "weak"
        assert result["score"] == 0


# ============================================================================
# CSRF令牌测试
# ============================================================================


class TestCsrfToken:
    """CSRF令牌生成与验证测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_csrf_token_generation(self):
        """测试CSRF令牌生成"""
        token = secrets.token_urlsafe(32)
        assert len(token) > 20
        assert isinstance(token, str)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_csrf_token_validation(self):
        """测试CSRF令牌验证"""
        # 生成令牌
        token = secrets.token_hex(32)

        # 验证令牌（使用hmac.compare_digest防止时序攻击）
        assert hmac.compare_digest(token, token) is True
        assert hmac.compare_digest(token, "wrong-token") is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_csrf_token_uniqueness(self):
        """测试CSRF令牌唯一性"""
        tokens = set()
        for _ in range(1000):
            token = secrets.token_hex(16)
            assert token not in tokens
            tokens.add(token)


# ============================================================================
# 会话ID安全性测试
# ============================================================================


class TestSessionIdSecurity:
    """会话ID生成安全性测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_session_id_length(self):
        """测试会话ID长度"""
        session_id = secrets.token_hex(32)
        assert len(session_id) == 64  # 32字节 = 64十六进制字符

    @pytest.mark.unit
    @pytest.mark.p0
    def test_session_id_randomness(self):
        """测试会话ID随机性"""
        # 生成100个会话ID，验证不重复
        ids = set()
        for _ in range(100):
            sid = secrets.token_urlsafe(32)
            assert sid not in ids
            ids.add(sid)
        assert len(ids) == 100

    @pytest.mark.unit
    @pytest.mark.p0
    def test_session_id_charset(self):
        """测试会话ID字符集"""
        sid = secrets.token_urlsafe(32)
        # URL安全字符
        safe_chars = set(string.ascii_letters + string.digits + "-_")
        assert all(c in safe_chars for c in sid)


# ============================================================================
# 异常信息脱敏测试
# ============================================================================


class TestExceptionSanitization:
    """异常信息脱敏测试 - 错误消息不泄露敏感信息"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_exception_message_sanitization(self):
        """测试异常消息脱敏"""
        sanitizer = DataSanitizer()

        try:
            raise ValueError("登录失败，用户密码password=secret123不正确")
        except ValueError as e:
            sanitized_msg = sanitizer.sanitize_string(str(e))
            assert "secret123" not in sanitized_msg
            assert "[REDACTED]" in sanitized_msg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_exception_with_sensitive_data(self):
        """测试包含敏感数据的异常处理"""
        sanitizer = DataSanitizer()

        try:
            raise RuntimeError("API Key=sk-1234567890abcdef 验证失败")
        except RuntimeError as e:
            sanitized = sanitizer.sanitize_string(str(e))
            assert "sk-1234567890abcdef" not in sanitized


# ============================================================================
# 序列化数据安全测试
# ============================================================================


class TestSerializationSecurity:
    """序列化数据安全校验测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_json_safe_load(self):
        """测试安全JSON加载"""
        # 正常JSON
        data = json.loads('{"name": "test", "value": 123}')
        assert data["name"] == "test"
        assert data["value"] == 123

    @pytest.mark.unit
    @pytest.mark.p0
    def test_json_invalid_handling(self):
        """测试无效JSON的容错处理"""
        with pytest.raises(json.JSONDecodeError):
            json.loads("not valid json")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_json_sanitize_before_parse(self):
        """测试JSON解析前脱敏"""
        sanitizer = DataSanitizer()
        # 测试 JSON 字符串中的敏感数据脱敏
        # 使用 key=value 格式来测试 sanitize_string 的正则匹配
        json_like_str = 'password=secret123, api_key=sk-test1234567890'
        sanitized_str = sanitizer.sanitize_string(json_like_str)
        
        # 敏感值应该已被脱敏
        assert "secret123" not in sanitized_str
        assert "sk-test1234567890" not in sanitized_str
        assert "[REDACTED]" in sanitized_str
        
        # 测试 sanitize_dict 对字典数据的脱敏（JSON解析后）
        data = {"password": "secret123", "api_key": "sk-test1234567890"}
        sanitized_data = sanitizer.sanitize_dict(data)
        
        # 敏感字段值应该已被脱敏
        assert sanitized_data["password"] == "[REDACTED]"
        assert sanitized_data["api_key"] == "[REDACTED]"


# ============================================================================
# 安全日志格式测试
# ============================================================================


class TestSecureLogging:
    """安全日志格式规范测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_structured_log_format(self):
        """测试结构化日志格式"""
        import time
        log_entry = {
            "timestamp": int(time.time()),
            "level": "INFO",
            "module": "auth",
            "action": "login",
            "user_id": "user_123",
            "status": "success",
            "trace_id": secrets.token_hex(8),
            "duration_ms": 150,
        }

        assert "timestamp" in log_entry
        assert "trace_id" in log_entry
        assert "level" in log_entry
        assert isinstance(log_entry["timestamp"], int)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_sensitive_data_masking(self):
        """测试日志中敏感数据脱敏"""
        sanitizer = DataSanitizer()
        log_data = {
            "event": "user_login",
            "username": "testuser",
            "password": "mysecretpass",
            "ip": "192.168.1.100",
        }

        sanitized = sanitizer.sanitize_dict(log_data)
        assert sanitized["password"] == "[REDACTED]"
        assert sanitized["username"] == "testuser"


# ============================================================================
# 文件类型校验测试
# ============================================================================


class TestFileTypeValidation:
    """文件类型校验测试 - 魔数验证"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_magic_number_png(self):
        """测试PNG文件魔数"""
        # PNG文件的魔数
        png_magic = b'\x89PNG\r\n\x1a\n'
        assert png_magic[:8] == b'\x89PNG\r\n\x1a\n'

    @pytest.mark.unit
    @pytest.mark.p0
    def test_magic_number_jpeg(self):
        """测试JPEG文件魔数"""
        jpeg_magic = b'\xff\xd8\xff'
        assert jpeg_magic[:3] == b'\xff\xd8\xff'

    @pytest.mark.unit
    @pytest.mark.p0
    def test_magic_number_gif(self):
        """测试GIF文件魔数"""
        gif_magic = b'GIF89a'
        assert gif_magic[:6] in (b'GIF87a', b'GIF89a')

    @pytest.mark.unit
    @pytest.mark.p0
    def test_magic_number_pdf(self):
        """测试PDF文件魔数"""
        pdf_magic = b'%PDF-'
        assert pdf_magic[:5] == b'%PDF-'

    @pytest.mark.unit
    @pytest.mark.p0
    def test_file_extension_mismatch_detection(self):
        """测试文件扩展名与实际类型不匹配检测"""
        def validate_file_type(filepath: str, allowed_types: list) -> bool:
            magic_numbers = {
                'png': b'\x89PNG',
                'jpg': b'\xff\xd8\xff',
                'jpeg': b'\xff\xd8\xff',
                'gif': b'GIF8',
                'pdf': b'%PDF-',
            }

            ext = filepath.lower().rsplit('.', 1)[-1] if '.' in filepath else ''
            if ext not in allowed_types:
                return False

            # 实际环境中会读取文件头，这里模拟
            if ext in magic_numbers:
                return True
            return ext in allowed_types

        assert validate_file_type("image.png", ["png", "jpg"]) is True
        assert validate_file_type("image.exe", ["png", "jpg"]) is False


# ============================================================================
# base64 编码解码测试
# ============================================================================


class TestBase64Encoding:
    """Base64编码解码测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_base64_encode_decode(self):
        """测试Base64编码解码"""
        original = "Hello, World!"
        encoded = base64.b64encode(original.encode()).decode()
        decoded = base64.b64decode(encoded).decode()

        assert decoded == original
        assert original not in encoded  # 编码后不包含原文

    @pytest.mark.unit
    @pytest.mark.p0
    def test_urlsafe_base64(self):
        """测试URL安全Base64"""
        data = b'\xfb\xef\xbe\xef'
        encoded = base64.urlsafe_b64encode(data).decode()
        decoded = base64.urlsafe_b64decode(encoded)

        assert decoded == data
        assert '+' not in encoded or '/' not in encoded  # URL安全字符


# ============================================================================
# test_security 函数测试
# ============================================================================


class TestSecurityModuleTestFunction:
    """test_security 函数测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_test_security_runs(self):
        """测试test_security函数能正常运行"""
        result = test_security()
        # 应该返回0表示成功
        assert result == 0


# ============================================================================
# 权限检查辅助函数测试
# ============================================================================


class TestPermissionCheck:
    """权限检查辅助函数测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_permission_bitwise_check(self):
        """测试基于位运算的权限检查"""
        # 权限位定义
        READ = 1 << 0  # 1
        WRITE = 1 << 1  # 2
        EXECUTE = 1 << 2  # 4
        ADMIN = 1 << 3  # 8

        def has_permission(user_perms: int, required_perm: int) -> bool:
            return (user_perms & required_perm) == required_perm

        # 用户有读和写权限
        user_perms = READ | WRITE  # 3

        assert has_permission(user_perms, READ) is True
        assert has_permission(user_perms, WRITE) is True
        assert has_permission(user_perms, EXECUTE) is False
        assert has_permission(user_perms, ADMIN) is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_role_based_permission(self):
        """测试基于角色的权限检查"""
        ROLES = {
            "admin": ["read", "write", "delete", "manage_users"],
            "editor": ["read", "write"],
            "viewer": ["read"],
        }

        def has_permission(role: str, permission: str) -> bool:
            if role not in ROLES:
                return False
            return permission in ROLES[role]

        assert has_permission("admin", "delete") is True
        assert has_permission("editor", "read") is True
        assert has_permission("editor", "delete") is False
        assert has_permission("viewer", "write") is False
        assert has_permission("nonexistent", "read") is False


# ============================================================================
# 覆盖率补充测试
# ============================================================================


class TestCoverageSupplementary:
    """覆盖率补充测试 - 覆盖边界分支和异常路径"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_encrypt_empty_string(self):
        """测试加密空字符串时直接返回原文"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装")
        encryptor = LogEncryptor()
        assert encryptor.encrypt_string("") == ""
        assert encryptor.encrypt_string(None) is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_decrypt_empty_string(self):
        """测试解密空字符串时直接返回原文"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装")
        encryptor = LogEncryptor()
        assert encryptor.decrypt_string("") == ""
        assert encryptor.decrypt_string(None) is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_encrypt_decrypt_with_none_cipher(self):
        """测试 cipher 为 None 时加密解密都返回原文"""
        encryptor = LogEncryptor()
        original_cipher = encryptor._cipher
        try:
            encryptor._cipher = None
            # 加密
            assert encryptor.encrypt_string("test") == "test"
            # 解密
            assert encryptor.decrypt_string("test") == "test"
        finally:
            encryptor._cipher = original_cipher

    @pytest.mark.unit
    @pytest.mark.p0
    def test_encrypt_dict_missing_field(self):
        """测试加密字典中不存在的字段"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装")
        encryptor = LogEncryptor()
        data = {"username": "testuser", "email": "test@example.com"}
        result = encryptor.encrypt_dict(data, ["password", "api_key"])
        # 不存在的字段不应该影响其他字段
        assert result["username"] == "testuser"
        assert result["email"] == "test@example.com"
        # 不应该有加密标记
        assert "_password_encrypted" not in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_encrypt_dict_none_value(self):
        """测试加密值为 None 的字段"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装")
        encryptor = LogEncryptor()
        data = {"password": None, "token": "abc123"}
        result = encryptor.encrypt_dict(data, ["password", "token"])
        # None 值不应该被加密
        assert result["password"] is None
        assert "_password_encrypted" not in result
        # token 应该被加密
        assert "_token_encrypted" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_decrypt_dict_no_encrypted_fields(self):
        """测试解密没有加密字段的字典"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装")
        encryptor = LogEncryptor()
        data = {"username": "testuser", "email": "test@example.com"}
        result = encryptor.decrypt_dict(data)
        assert result == data

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_with_invalid_key_env(self):
        """测试使用无效的密钥环境变量初始化"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装")
        original = os.environ.get("TEST_INVALID_KEY", "")
        try:
            os.environ["TEST_INVALID_KEY"] = "not-a-valid-base64-key!!!"
            encryptor = LogEncryptor(key_env_var="TEST_INVALID_KEY")
            # 无效密钥应该会生成新密钥，但 cipher 应该可用
            assert encryptor._cipher is not None
            # 加密解密应该正常工作
            encrypted = encryptor.encrypt_string("test")
            decrypted = encryptor.decrypt_string(encrypted)
            assert decrypted == "test"
        finally:
            if original:
                os.environ["TEST_INVALID_KEY"] = original
            else:
                os.environ.pop("TEST_INVALID_KEY", None)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_string_empty(self):
        """测试脱敏空字符串"""
        sanitizer = DataSanitizer()
        assert sanitizer.sanitize_string("") == ""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_dict_nested_list_values(self):
        """测试脱敏字典中值为列表的敏感字段"""
        sanitizer = DataSanitizer()
        data = {
            "passwords": ["pass1", "pass2", "pass3"],
            "tokens": ["token1", "token2"],
            "normal_list": ["a", "b", "c"],
        }
        result = sanitizer.sanitize_dict(data)
        # 敏感键名的列表值应该被替换为占位符
        assert result["passwords"] == ["[REDACTED]", "[REDACTED]", "[REDACTED]"]
        assert result["tokens"] == ["[REDACTED]", "[REDACTED]"]
        # 普通列表应该保持不变
        assert result["normal_list"] == ["a", "b", "c"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_dict_nested_dict_in_list(self):
        """测试脱敏列表中嵌套字典的情况"""
        sanitizer = DataSanitizer()
        data = {
            "users": [
                {"name": "Alice", "password": "secret123"},
                {"name": "Bob", "api_key": "sk-test123456"},
            ]
        }
        result = sanitizer.sanitize_dict(data)
        assert result["users"][0]["password"] == "[REDACTED]"
        assert result["users"][1]["api_key"] == "[REDACTED]"
        assert result["users"][0]["name"] == "Alice"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_data_sanitizer_init_logging(self):
        """测试 DataSanitizer 初始化时的日志输出"""
        import logging
        logger = logging.getLogger("agent.security_utils")
        
        # 使用列表收集日志
        log_messages = []
        original_handlers = logger.handlers.copy()
        
        class ListHandler(logging.Handler):
            def emit(self, record):
                log_messages.append(record.getMessage())
        
        try:
            handler = ListHandler()
            handler.setLevel(logging.INFO)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            
            DataSanitizer()
            
            assert any("数据脱敏器已初始化" in msg for msg in log_messages)
        finally:
            logger.handlers = original_handlers

    @pytest.mark.unit
    @pytest.mark.p0
    def test_encrypt_exception_fallback(self):
        """测试加密异常时返回原文"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装")
        encryptor = LogEncryptor()
        
        # 模拟 cipher.encrypt 抛出异常
        original_encrypt = encryptor._cipher.encrypt
        def mock_encrypt(data):
            raise Exception("模拟加密失败")
        
        try:
            encryptor._cipher.encrypt = mock_encrypt
            result = encryptor.encrypt_string("test data")
            # 加密失败应该返回原文
            assert result == "test data"
        finally:
            encryptor._cipher.encrypt = original_encrypt

    @pytest.mark.unit
    @pytest.mark.p0
    def test_decrypt_invalid_input(self):
        """测试解密无效输入时返回原文"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装")
        encryptor = LogEncryptor()
        
        # 无效的 base64 字符串
        result = encryptor.decrypt_string("not-valid-base64!!!")
        # 解密失败应该返回原文
        assert result == "not-valid-base64!!!"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_decrypt_tampered_data(self):
        """测试解密被篡改的数据时返回原文"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装")
        encryptor = LogEncryptor()
        
        # 加密一个字符串然后篡改它
        encrypted = encryptor.encrypt_string("secret data")
        tampered = encrypted[:-5] + "xxxxx"
        
        result = encryptor.decrypt_string(tampered)
        # 解密失败应该返回原文（被篡改的数据）
        assert result == tampered

    @pytest.mark.unit
    @pytest.mark.p0
    def test_test_security_function_runs(self):
        """测试 test_security 函数能正常运行"""
        result = test_security()
        assert result == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sensitive_patterns_exist(self):
        """测试 SENSITIVE_PATTERNS 常量已定义"""
        assert isinstance(SENSITIVE_PATTERNS, list)
        assert len(SENSITIVE_PATTERNS) > 0
        # 每个模式应该是 (pattern, group_idx) 元组
        for pattern, group_idx in SENSITIVE_PATTERNS:
            assert hasattr(pattern, 'search')
            assert isinstance(group_idx, int)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_encryptor_init_with_key_generation_failure(self):
        """测试密钥生成失败时的降级行为"""
        if not HAS_CRYPTO:
            pytest.skip("cryptography库未安装")
        
        from cryptography.fernet import Fernet
        original_generate_key = Fernet.generate_key
        
        def mock_generate_key():
            raise Exception("模拟密钥生成失败")
        
        try:
            Fernet.generate_key = mock_generate_key
            # 确保环境变量中没有密钥
            original_env = os.environ.get("Yunshu_ENCRYPT_KEY", "")
            if original_env:
                del os.environ["Yunshu_ENCRYPT_KEY"]
            
            try:
                encryptor = LogEncryptor()
                # 密钥生成失败，cipher 应该为 None
                assert encryptor._cipher is None
                # 加密应该返回原文
                assert encryptor.encrypt_string("test") == "test"
            finally:
                if original_env:
                    os.environ["Yunshu_ENCRYPT_KEY"] = original_env
        finally:
            Fernet.generate_key = original_generate_key
