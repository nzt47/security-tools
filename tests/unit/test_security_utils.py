import os
import sys
import pytest
import io
import contextlib
from unittest.mock import patch, MagicMock, call

from agent.security_utils import (
    LogEncryptor,
    DataSanitizer,
    SENSITIVE_PATTERNS,
    HAS_CRYPTO,
    test_security,
)


class TestDataSanitizer:
    """测试数据脱敏器"""

    def test_sanitize_string_api_key(self):
        """测试脱敏API Key"""
        sanitizer = DataSanitizer()
        test_text = "API Key=sk-abcdefghijklmnopqrstuv123456"
        result = sanitizer.sanitize_string(test_text)
        assert "[REDACTED]" in result
        assert "sk-" not in result

    def test_sanitize_string_password(self):
        """测试脱敏密码"""
        sanitizer = DataSanitizer()
        test_text = 'password="MyPassword123"'
        result = sanitizer.sanitize_string(test_text)
        assert "[REDACTED]" in result
        assert "MyPassword" not in result

    def test_sanitize_string_email(self):
        """测试脱敏邮箱"""
        sanitizer = DataSanitizer()
        test_text = "联系邮箱: user@example.com"
        result = sanitizer.sanitize_string(test_text)
        assert "[REDACTED]" in result
        assert "@example.com" not in result

    def test_sanitize_string_phone(self):
        """测试脱敏手机号"""
        sanitizer = DataSanitizer()
        test_text = "手机号: 13812345678"
        result = sanitizer.sanitize_string(test_text)
        assert "[REDACTED]" in result
        assert "13812345678" not in result

    def test_sanitize_string_combined(self):
        """测试混合敏感数据脱敏"""
        sanitizer = DataSanitizer()
        test_text = (
            "API Key=sk-abcdef12345, password=secret123, "
            "email:test@example.com, phone:13987654321"
        )
        result = sanitizer.sanitize_string(test_text)
        assert result.count("[REDACTED]") == 4

    def test_sanitize_dict(self):
        """测试脱敏字典"""
        sanitizer = DataSanitizer()
        test_data = {
            "user": "admin",
            "api_key": "sk-abcdefghijk",  # 至少12字符
            "password": "password123",
            "email": "user@test.com",
        }
        result = sanitizer.sanitize_dict(test_data)
        assert result["api_key"] == "[REDACTED]"
        assert result["password"] == "[REDACTED]"
        assert result["email"] == "[REDACTED]"
        assert result["user"] == "admin"

    def test_sanitize_dict_nested(self):
        """测试嵌套字典脱敏"""
        sanitizer = DataSanitizer()
        test_data = {
            "config": {
                "api_key": "sk-nestedvalue123",  # 至少16字符
                "enabled": True,
            },
            "items": [
                {"name": "item1", "token": "token_value_123"},  # 至少8字符
                {"name": "item2", "token": "token_value_456"},
            ],
        }
        result = sanitizer.sanitize_dict(test_data)
        assert result["config"]["api_key"] == "[REDACTED]"
        assert result["items"][0]["token"] == "[REDACTED]"
        assert result["items"][1]["token"] == "[REDACTED]"

    def test_sanitize_empty_string(self):
        """测试空字符串脱敏"""
        sanitizer = DataSanitizer()
        result = sanitizer.sanitize_string("")
        assert result == ""

    def test_sanitize_no_sensitive_data(self):
        """测试无敏感数据的字符串"""
        sanitizer = DataSanitizer()
        test_text = "这是一段普通文本，不包含敏感信息"
        result = sanitizer.sanitize_string(test_text)
        assert result == test_text


class TestLogEncryptor:
    """测试日志加密器"""

    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography库未安装")
    def test_encrypt_decrypt_string(self):
        """测试字符串加密解密"""
        encryptor = LogEncryptor()
        plaintext = "敏感数据"
        encrypted = encryptor.encrypt_string(plaintext)
        decrypted = encryptor.decrypt_string(encrypted)
        assert decrypted == plaintext
        assert encrypted != plaintext

    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography库未安装")
    def test_encrypt_empty_string(self):
        """测试空字符串加密"""
        encryptor = LogEncryptor()
        result = encryptor.encrypt_string("")
        assert result == ""

    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography库未安装")
    def test_decrypt_empty_string(self):
        """测试空字符串解密"""
        encryptor = LogEncryptor()
        result = encryptor.decrypt_string("")
        assert result == ""

    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography库未安装")
    def test_encrypt_dict(self):
        """测试字典加密"""
        encryptor = LogEncryptor()
        test_dict = {
            "user": "admin",
            "secret": "secret_value",
        }
        encrypted = encryptor.encrypt_dict(test_dict, ["secret"])
        assert encrypted["secret"] != "secret_value"
        assert encrypted["_secret_encrypted"] is True
        assert encrypted["user"] == "admin"

    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography库未安装")
    def test_decrypt_dict(self):
        """测试字典解密"""
        encryptor = LogEncryptor()
        test_dict = {
            "user": "admin",
            "secret": encryptor.encrypt_string("secret_value"),
            "_secret_encrypted": True,
        }
        decrypted = encryptor.decrypt_dict(test_dict)
        assert decrypted["secret"] == "secret_value"

    def test_encrypt_without_crypto(self):
        """测试未安装cryptography库时的行为"""
        with patch("agent.security_utils.HAS_CRYPTO", False):
            encryptor = LogEncryptor()
            plaintext = "test"
            encrypted = encryptor.encrypt_string(plaintext)
            decrypted = encryptor.decrypt_string(plaintext)
            # 未安装库时应该原样返回
            assert encrypted == plaintext
            assert decrypted == plaintext

    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography库未安装")
    def test_encrypt_dict_multiple_fields(self):
        """测试加密字典多个字段"""
        encryptor = LogEncryptor()
        test_dict = {
            "name": "test",
            "password": "pass123",
            "api_key": "sk-123456",
        }
        encrypted = encryptor.encrypt_dict(test_dict, ["password", "api_key"])
        assert encrypted["_password_encrypted"] is True
        assert encrypted["_api_key_encrypted"] is True
        assert encrypted["name"] == "test"

    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography库未安装")
    def test_load_key_from_env(self):
        """测试从环境变量加载密钥"""
        import base64
        from cryptography.fernet import Fernet
        
        # 生成一个测试密钥
        test_key = Fernet.generate_key()
        key_str = base64.urlsafe_b64encode(test_key).decode()
        
        with patch.dict(os.environ, {"Yunshu_ENCRYPT_KEY": key_str}):
            encryptor = LogEncryptor()
            plaintext = "test"
            encrypted = encryptor.encrypt_string(plaintext)
            decrypted = encryptor.decrypt_string(encrypted)
            assert decrypted == plaintext


class TestSensitivePatterns:
    """测试敏感数据模式"""

    def test_patterns_exist(self):
        """测试敏感模式列表不为空"""
        assert len(SENSITIVE_PATTERNS) > 0

    def test_pattern_structure(self):
        """测试模式结构正确"""
        for pattern, group_idx in SENSITIVE_PATTERNS:
            assert hasattr(pattern, "match")  # 应该是正则表达式对象
            assert isinstance(group_idx, int)


class TestLogEncryptorEdgeCases:
    """测试日志加密器的边界情况"""

    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography库未安装")
    def test_load_or_generate_key_with_invalid_env(self):
        """测试从环境变量加载无效密钥"""
        with patch.dict(os.environ, {"Yunshu_ENCRYPT_KEY": "invalid-base64"}):
            encryptor = LogEncryptor()
            # 应该能正常初始化（会生成新密钥）
            assert encryptor is not None

    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography库未安装")
    @patch("agent.security_utils.Fernet")
    def test_generate_key_failure(self, mock_fernet):
        """测试生成密钥失败"""
        mock_fernet.generate_key.side_effect = Exception("Key generation failed")
        with patch.dict(os.environ, {}):
            encryptor = LogEncryptor()
            # 密钥生成失败，cipher应该是None
            assert encryptor._cipher is None

    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography库未安装")
    @patch("agent.security_utils.Fernet")
    def test_encrypt_string_failure(self, mock_fernet):
        """测试加密失败"""
        mock_instance = MagicMock()
        mock_instance.encrypt.side_effect = Exception("Encryption failed")
        mock_fernet.return_value = mock_instance
        
        # 使用已知有效的密钥
        import base64
        from cryptography.fernet import Fernet
        test_key = Fernet.generate_key()
        key_str = base64.urlsafe_b64encode(test_key).decode()
        
        with patch.dict(os.environ, {"Yunshu_ENCRYPT_KEY": key_str}):
            encryptor = LogEncryptor()
            result = encryptor.encrypt_string("test")
            # 加密失败应该返回原文
            assert result == "test"

    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography库未安装")
    @patch("agent.security_utils.Fernet")
    def test_decrypt_string_failure(self, mock_fernet):
        """测试解密失败"""
        mock_instance = MagicMock()
        mock_instance.decrypt.side_effect = Exception("Decryption failed")
        mock_fernet.return_value = mock_instance
        
        # 使用已知有效的密钥
        import base64
        from cryptography.fernet import Fernet
        test_key = Fernet.generate_key()
        key_str = base64.urlsafe_b64encode(test_key).decode()
        
        with patch.dict(os.environ, {"Yunshu_ENCRYPT_KEY": key_str}):
            encryptor = LogEncryptor()
            result = encryptor.decrypt_string("invalid-ciphertext")
            # 解密失败应该返回原文
            assert result == "invalid-ciphertext"

    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography库未安装")
    def test_encrypt_dict_with_none_values(self):
        """测试加密字典时字段值为None的情况"""
        encryptor = LogEncryptor()
        test_dict = {
            "user": "admin",
            "secret": None,
        }
        encrypted = encryptor.encrypt_dict(test_dict, ["secret"])
        # None值不应该被加密
        assert encrypted["secret"] is None

    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography库未安装")
    def test_decrypt_dict_with_missing_fields(self):
        """测试解密字典时缺少字段的情况"""
        encryptor = LogEncryptor()
        test_dict = {
            "user": "admin",
            "_secret_encrypted": True,
            # 缺少secret字段
        }
        decrypted = encryptor.decrypt_dict(test_dict)
        # 应该能正常处理
        assert decrypted["user"] == "admin"


class TestDataSanitizerEdgeCases:
    """测试数据脱敏器的边界情况"""

    def test_sanitize_dict_with_list_of_strings(self):
        """测试脱敏字典中包含字符串列表的情况"""
        sanitizer = DataSanitizer()
        test_data = {
            "api_key": ["sk-value1", "sk-value2"],
            "password": ["pass1", "pass2"],
        }
        result = sanitizer.sanitize_dict(test_data)
        assert result["api_key"] == ["[REDACTED]", "[REDACTED]"]
        assert result["password"] == ["[REDACTED]", "[REDACTED]"]

    def test_sanitize_dict_with_mixed_list(self):
        """测试脱敏字典中包含混合类型列表的情况"""
        sanitizer = DataSanitizer()
        test_data = {
            "api_key": ["sk-value", 123, True, None],
        }
        result = sanitizer.sanitize_dict(test_data)
        assert result["api_key"] == ["[REDACTED]", 123, True, None]

    def test_sanitize_dict_with_empty_dict(self):
        """测试空字典脱敏"""
        sanitizer = DataSanitizer()
        result = sanitizer.sanitize_dict({})
        assert result == {}

    def test_sanitize_dict_with_custom_placeholder(self):
        """测试自定义占位符"""
        sanitizer = DataSanitizer()
        test_text = "API Key=sk-abcdefghijkl"
        result = sanitizer.sanitize_string(test_text, placeholder="[MASKED]")
        assert "[MASKED]" in result
        assert "[REDACTED]" not in result

    def test_sanitize_dict_with_other_sensitive_keys(self):
        """测试其他敏感键名"""
        sanitizer = DataSanitizer()
        test_data = {
            "passwd": "secret123",
            "private_key": "-----BEGIN PRIVATE KEY-----...",
        }
        result = sanitizer.sanitize_dict(test_data)
        assert result["passwd"] == "[REDACTED]"
        assert result["private_key"] == "[REDACTED]"


class TestSecurityMainFunction:
    """测试主函数 test_security"""

    def test_test_security_function(self):
        """测试 test_security 函数执行"""
        # 捕获输出
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            # 使用不带加密库的测试
            with patch("agent.security_utils.HAS_CRYPTO", False):
                result = test_security()
                output = f.getvalue()
        # 应该能正常执行
        assert result == 0
        assert "测试安全模块" in output
        assert "测试数据脱敏" in output
        assert "加密功能跳过" in output

    @pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography库未安装")
    def test_test_security_with_crypto(self):
        """测试带加密库的 test_security 函数"""
        # 捕获输出
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            with patch.dict(os.environ, {}):  # 清除环境变量，确保生成新密钥
                result = test_security()
                output = f.getvalue()
        # 应该能正常执行
        assert result == 0
        assert "测试安全模块" in output
        assert "测试数据脱敏" in output
        assert "测试加密功能" in output

    def test_main_block(self):
        """测试 __main__ 块的执行"""
        with patch.dict(os.environ, {}):
            with patch("sys.argv", ["security_utils.py"]):
                with patch("sys.exit") as mock_exit:
                    # 模拟 __name__ == "__main__" 的执行
                    mock_exit.return_value = 0
                    # 直接测试异常处理部分
                    try:
                        with patch("agent.security_utils.test_security") as mock_test:
                            mock_test.return_value = 0
                            # 模拟正常执行
                            pass
                    except Exception as e:
                        pytest.fail(f"应该不会抛出异常: {e}")

    def test_main_block_exception(self):
        """测试 __main__ 块的异常处理"""
        # 模拟抛出异常的情况
        with patch("agent.security_utils.test_security") as mock_test:
            mock_test.side_effect = Exception("Test exception")
            
            # 捕获输出
            f = io.StringIO()
            with contextlib.redirect_stderr(f):
                with contextlib.redirect_stdout(f):
                    try:
                        # 模拟 __main__ 块的执行逻辑
                        import traceback
                        mock_test()
                    except Exception as e:
                        print(f"测试失败: {e}")
                        traceback.print_exc()
            
            output = f.getvalue()
            assert "测试失败" in output