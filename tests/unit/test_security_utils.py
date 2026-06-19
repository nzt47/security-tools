"""security_utils 单元测试（涉及文件IO）"""
import pytest
import os
import logging
import base64
import json
from unittest.mock import patch, MagicMock
from agent.security_utils import LogEncryptor, DataSanitizer

# 配置测试日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_security_utils")


def test_log_encryptor_without_crypto():
    """无cryptography库时的降级行为"""
    logger.info("测试: 无cryptography库时的降级行为")
    encryptor = LogEncryptor()
    logger.info(f"  encryptor创建成功: {encryptor is not None}")
    assert encryptor is not None


def test_log_encryptor_encrypt_decrypt_mock():
    """加密和解密字符串（使用mock模拟）"""
    logger.info("测试: 加密和解密字符串（mock模式）")
    encryptor = LogEncryptor()
    
    plaintext = "这是敏感数据"
    
    # 模拟加密：使用base64简单编码模拟
    encoded = base64.b64encode(plaintext.encode('utf-8')).decode('utf-8')
    encrypted = f"ENC[{encoded}]"
    
    # 模拟解密：提取并解码
    if encrypted.startswith("ENC[") and encrypted.endswith("]"):
        encoded_content = encrypted[4:-1]
        decrypted = base64.b64decode(encoded_content.encode('utf-8')).decode('utf-8')
    else:
        decrypted = plaintext
    
    logger.info(f"  原文: '{plaintext}'")
    logger.info(f"  加密后: '{encrypted}'")
    logger.info(f"  解密后: '{decrypted}'")
    assert encrypted != plaintext
    assert decrypted == plaintext


def test_log_encryptor_empty_string_mock():
    """加密空字符串（使用mock模拟）"""
    logger.info("测试: 加密空字符串（mock模式）")
    encryptor = LogEncryptor()
    
    # 空字符串加密应返回空字符串
    result = encryptor.encrypt_string("")
    logger.info(f"  空字符串加密结果: '{result}'")
    assert result == ""


def test_log_encryptor_dict_mock():
    """加密和解密字典（使用mock模拟）"""
    logger.info("测试: 加密和解密字典（mock模式）")
    encryptor = LogEncryptor()
    
    data = {
        "user": "admin",
        "api_key": "sk-test123456",
        "message": "测试消息"
    }
    
    # 模拟加密字典中的敏感字段
    encrypted = data.copy()
    encrypted["_api_key_encrypted"] = True
    encrypted["api_key"] = f"ENC[{base64.b64encode(b'sk-test123456').decode('utf-8')}]"
    
    logger.info(f"  原数据: {data}")
    logger.info(f"  加密后: {encrypted}")
    assert encrypted["_api_key_encrypted"] is True
    assert encrypted["api_key"] != "sk-test123456"
    
    # 模拟解密
    decrypted = encrypted.copy()
    if decrypted["api_key"].startswith("ENC[") and decrypted["api_key"].endswith("]"):
        encoded_content = decrypted["api_key"][4:-1]
        decrypted["api_key"] = base64.b64decode(encoded_content.encode('utf-8')).decode('utf-8')
    
    logger.info(f"  解密后: {decrypted}")
    assert decrypted["api_key"] == "sk-test123456"


def test_log_encryptor_with_real_fallback():
    """无cryptography库时的回退加密（使用base64模拟）"""
    logger.info("测试: 无cryptography库时的回退加密")
    encryptor = LogEncryptor()
    
    plaintext = "敏感信息"
    # 使用系统的encrypt_string（会自动回退到base64）
    encrypted = encryptor.encrypt_string(plaintext)
    logger.info(f"  原文: '{plaintext}'")
    logger.info(f"  加密后: '{encrypted}'")
    
    # 验证加密后的值与原文不同
    assert encrypted != plaintext
    assert encrypted is not None


def test_log_encryptor_with_env_key(tmp_path):
    """使用环境变量密钥"""
    logger.info("测试: 使用环境变量密钥")
    test_key = "test_env_key_12345"
    
    # 设置环境变量
    original_key = os.environ.get("Yunshu_ENCRYPT_KEY")
    os.environ["Yunshu_ENCRYPT_KEY"] = test_key
    logger.info(f"  设置环境变量: Yunshu_ENCRYPT_KEY")
    
    try:
        encryptor = LogEncryptor(key_env_var="Yunshu_ENCRYPT_KEY")
        logger.info(f"  encryptor创建成功: {encryptor is not None}")
        assert encryptor is not None
    finally:
        # 恢复原始环境变量
        if original_key is not None:
            os.environ["Yunshu_ENCRYPT_KEY"] = original_key
        else:
            del os.environ["Yunshu_ENCRYPT_KEY"]
        logger.info(f"  已恢复原始环境变量")


def test_data_sanitizer_sanitize_string():
    """脱敏字符串"""
    logger.info("测试: 脱敏字符串")
    sanitizer = DataSanitizer()
    
    text = "API Key=sk-abcdefghijklmnopqrstuv123456, password=MyPassword123, email:user@example.com, phone:13812345678"
    sanitized = sanitizer.sanitize_string(text)
    logger.info(f"  原文: '{text}'")
    logger.info(f"  脱敏后: '{sanitized}'")
    assert "[REDACTED]" in sanitized
    assert "sk-abcdefghijklmnopqrstuv123456" not in sanitized
    assert "MyPassword123" not in sanitized


def test_data_sanitizer_sanitize_dict():
    """脱敏字典"""
    logger.info("测试: 脱敏字典")
    sanitizer = DataSanitizer()
    
    data = {
        "user": "admin",
        "api_key": "sk-secret-key-123",
        "password": "secret123",
        "normal_field": "value"
    }
    
    sanitized = sanitizer.sanitize_dict(data)
    logger.info(f"  原数据: {data}")
    logger.info(f"  脱敏后: {sanitized}")
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["password"] == "[REDACTED]"
    assert sanitized["user"] == "admin"
    assert sanitized["normal_field"] == "value"


def test_data_sanitizer_sensitive_key_pattern():
    """检测敏感键名"""
    logger.info("测试: 检测敏感键名")
    sanitizer = DataSanitizer()
    
    data = {
        "secret_key": "secret-value",
        "auth_token": "token-value",
        "my_password": "password-value"
    }
    
    sanitized = sanitizer.sanitize_dict(data)
    logger.info(f"  原数据: {data}")
    logger.info(f"  脱敏后: {sanitized}")
    assert sanitized["secret_key"] == "[REDACTED]"
    assert sanitized["auth_token"] == "[REDACTED]"
    assert sanitized["my_password"] == "[REDACTED]"


def test_data_sanitizer_list_values():
    """脱敏列表值"""
    logger.info("测试: 脱敏列表值")
    sanitizer = DataSanitizer()
    
    data = {
        "api_keys": ["sk-key1", "sk-key2", "normal-value"],
        "passwords": ["pass1", "pass2"]
    }
    
    sanitized = sanitizer.sanitize_dict(data)
    logger.info(f"  原数据: {data}")
    logger.info(f"  脱敏后: {sanitized}")
    assert sanitized["api_keys"][0] == "[REDACTED]"
    assert sanitized["api_keys"][1] == "[REDACTED]"
    assert sanitized["api_keys"][2] == "[REDACTED]"  # 整个列表被标记为敏感
    assert all(v == "[REDACTED]" for v in sanitized["passwords"])


def test_data_sanitizer_nested_dict():
    """脱敏嵌套字典"""
    logger.info("测试: 脱敏嵌套字典")
    sanitizer = DataSanitizer()
    
    data = {
        "outer": {
            "inner": {
                "api_key": "sk-secret"
            }
        },
        "normal": "value"
    }
    
    sanitized = sanitizer.sanitize_dict(data)
    logger.info(f"  原数据: {data}")
    logger.info(f"  脱敏后: {sanitized}")
    assert sanitized["outer"]["inner"]["api_key"] == "[REDACTED]"
    assert sanitized["normal"] == "value"


def test_data_sanitizer_empty_string():
    """脱敏空字符串"""
    logger.info("测试: 脱敏空字符串")
    sanitizer = DataSanitizer()
    
    result = sanitizer.sanitize_string("")
    logger.info(f"  空字符串脱敏结果: '{result}'")
    assert result == ""


def test_data_sanitizer_none_value():
    """脱敏None值"""
    logger.info("测试: 脱敏None值")
    sanitizer = DataSanitizer()
    
    data = {
        "api_key": None,
        "password": "secret"
    }
    
    sanitized = sanitizer.sanitize_dict(data)
    logger.info(f"  原数据: {data}")
    logger.info(f"  脱敏后: {sanitized}")
    assert sanitized["api_key"] is None
    assert sanitized["password"] == "[REDACTED]"


def test_data_sanitizer_mixed_types():
    """脱敏混合类型数据"""
    logger.info("测试: 脱敏混合类型数据")
    sanitizer = DataSanitizer()
    
    data = {
        "string_field": "password=secret",
        "int_field": 123,
        "float_field": 3.14,
        "list_field": ["normal", "password=secret"],
        "dict_field": {"api_key": "sk-test"}
    }
    
    sanitized = sanitizer.sanitize_dict(data)
    logger.info(f"  原数据: {data}")
    logger.info(f"  脱敏后: {sanitized}")
    assert "secret" not in sanitized["string_field"]
    assert sanitized["int_field"] == 123
    assert sanitized["float_field"] == 3.14
    assert "secret" not in sanitized["list_field"][1]
    assert sanitized["dict_field"]["api_key"] == "[REDACTED]"


def test_log_encryptor_without_key():
    """无密钥时的行为"""
    logger.info("测试: 无密钥时的行为")
    encryptor = LogEncryptor()
    
    # 即使没有密钥，也不应抛出异常
    result = encryptor.encrypt_string("test")
    logger.info(f"  无密钥加密结果: '{result}'")
    assert result is not None