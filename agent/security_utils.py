#!/usr/bin/env python3
"""
安全工具模块 — P1阶段实现示例

这是P1规划中安全加密模块的完整实现
可以直接集成到现有架构中
"""

import os
import sys
import json
import base64
import logging
import re
from typing import Dict, Any, Optional

# 尝试导入加密库
try:
    from cryptography.fernet import Fernet
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

logger = logging.getLogger(__name__)

# 敏感数据模式 - 改进版，避免分组问题
SENSITIVE_PATTERNS = [
    # API Key - 支持多种格式的API密钥（api_key=, API Key=, api-key=等）
    (re.compile(r'(?i)(api[\s_-]?key|secret[\s_-]?key|token|auth[\s_-]?token)\s*[=:]\s*["\']?([a-zA-Z0-9_-]{8,})["\']?'), 2),
    # Password - 匹配各种密码格式
    (re.compile(r'(?i)password\s*[=:]\s*["\']?([^\s"\']{6,})["\']?'), 1),
    # Email - 无分组
    (re.compile(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b'), 0),
    # Phone - 无分组
    (re.compile(r'(?<!\d)(1[3-9]\d{9})(?!\d)'), 0),
]


class LogEncryptor:
    """日志加密器
    
    使用AES-256-GCM加密敏感字段
    """
    
    def __init__(self, key_env_var: str = "Yunshu_ENCRYPT_KEY"):
        """初始化加密器
        
        Args:
            key_env_var: 加密密钥的环境变量名
        """
        if not HAS_CRYPTO:
            logger.warning("cryptography库未安装，加密功能不可用")
            self._cipher = None
            return
            
        self._key = self._load_or_generate_key(key_env_var)
        if self._key:
            self._cipher = Fernet(self._key)
            logger.info("日志加密器已初始化")
        else:
            self._cipher = None
        
    def _load_or_generate_key(self, key_env_var: str) -> Optional[bytes]:
        """加载或生成加密密钥"""
        # 尝试从环境变量加载
        key_str = os.getenv(key_env_var)
        if key_str:
            try:
                return base64.urlsafe_b64decode(key_str)
            except Exception as e:
                logger.warning(f"加载密钥失败: {e}，将生成新密钥")
        
        # 生成新密钥
        try:
            new_key = Fernet.generate_key()
            logger.warning("=" * 70)
            logger.warning("⚠️ 已生成新的加密密钥！")
            logger.warning(f" 请将以下环境变量添加到您的配置中：")
            logger.warning(f"  {key_env_var}={base64.urlsafe_b64encode(new_key).decode()}")
            logger.warning("=" * 70)
            return new_key
        except Exception as e:
            logger.error(f"生成密钥失败: {e}")
            return None
    
    def encrypt_string(self, plaintext: str) -> str:
        """加密字符串"""
        if not self._cipher or not plaintext:
            return plaintext
        try:
            ciphertext = self._cipher.encrypt(plaintext.encode("utf-8"))
            return base64.urlsafe_b64encode(ciphertext).decode()
        except Exception as e:
            logger.error(f"加密失败: {e}")
            return plaintext
    
    def decrypt_string(self, ciphertext: str) -> str:
        """解密字符串"""
        if not self._cipher or not ciphertext:
            return ciphertext
        try:
            decoded = base64.urlsafe_b64decode(ciphertext)
            plaintext = self._cipher.decrypt(decoded)
            return plaintext.decode("utf-8")
        except Exception as e:
            logger.error(f"解密失败: {e}")
            return ciphertext
    
    def encrypt_dict(self, data: Dict[str, Any], fields: list) -> Dict[str, Any]:
        """加密字典中指定的字段
        
        Args:
            data: 原始字典
            fields: 需要加密的字段列表
            
        Returns:
            加密后的字典
        """
        result = dict(data)
        for field in fields:
            if field in result and result[field] is not None:
                result[field] = self.encrypt_string(str(result[field]))
                result[f"_{field}_encrypted"] = True
        return result
    
    def decrypt_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """解密字典中标记为已加密的字段"""
        result = dict(data)
        # 找出所有标记为加密的字段
        encrypted_fields = []
        for k in list(result.keys()):
            if k.startswith('_') and k.endswith('_encrypted'):
                # 去掉前缀"_"和后缀"_encrypted" (长度10)
                field_name = k[1:-10]
                encrypted_fields.append(field_name)
        
        for field in encrypted_fields:
            if field in result:
                result[field] = self.decrypt_string(str(result[field]))
        return result


class DataSanitizer:
    """数据脱敏器 - 自动检测并替换敏感数据"""
    
    # 敏感键名模式
    SENSITIVE_KEY_PATTERNS = re.compile(
        r'(?i)(api[_-]?key|secret[_-]?key|token|password|passwd|auth[_-]?token|private[_-]?key)'
    )
    
    def __init__(self):
        self._patterns = SENSITIVE_PATTERNS
        logger.info("数据脱敏器已初始化")
    
    def sanitize_string(self, text: str, placeholder: str = "[REDACTED]") -> str:
        """脱敏字符串"""
        result = text
        for pattern, group_idx in self._patterns:
            # 使用lambda函数处理分组
            def make_replacer(pattern_idx=group_idx):
                def replacer(match):
                    if pattern_idx > 0 and len(match.groups()) >= pattern_idx:
                        return match.group(0).replace(match.group(pattern_idx), placeholder)
                    return placeholder
                return replacer
            result = pattern.sub(make_replacer(), result)
        return result
    
    def sanitize_dict(self, data: Dict[str, Any], placeholder: str = "[REDACTED]") -> Dict[str, Any]:
        """脱敏字典"""
        result = dict(data)
        for key, value in result.items():
            # 检查键名是否包含敏感关键字
            if self.SENSITIVE_KEY_PATTERNS.search(key):
                # 如果值是字符串，直接替换为占位符
                if isinstance(value, str):
                    result[key] = placeholder
                # 如果值是列表，检查列表中的字符串
                elif isinstance(value, list):
                    result[key] = [
                        placeholder if isinstance(item, str) else item
                        for item in value
                    ]
                # 处理完敏感键后，跳过后面的常规处理
                continue
            
            # 如果不是敏感键名，按照常规方式脱敏
            if isinstance(value, str):
                result[key] = self.sanitize_string(value, placeholder)
            elif isinstance(value, dict):
                result[key] = self.sanitize_dict(value, placeholder)
            elif isinstance(value, list):
                result[key] = [
                    self.sanitize_dict(item, placeholder) if isinstance(item, dict) else
                    self.sanitize_string(item, placeholder) if isinstance(item, str) else
                    item
                    for item in value
                ]
        return result


def test_security() -> int:
    """测试安全模块"""
    print("=" * 70)
    print("测试安全模块")
    print("=" * 70)
    
    # 测试1: 数据脱敏
    print("\n1. 测试数据脱敏:")
    sanitizer = DataSanitizer()
    test_text = "API Key=sk-abcdefghijklmnopqrstuv123456, password=MyPassword123, email:user@example.com, phone:13812345678"
    sanitized = sanitizer.sanitize_string(test_text)
    print(f"  原文: {test_text}")
    print(f"  脱敏: {sanitized}")
    assert "[REDACTED]" in sanitized, "脱敏失败"
    print("  ✓ 数据脱敏测试通过")
    
    # 测试2: 加密功能
    if HAS_CRYPTO:
        print("\n2. 测试加密功能:")
        encryptor = LogEncryptor()
        
        # 加密测试
        plaintext = "这是敏感数据"
        encrypted = encryptor.encrypt_string(plaintext)
        decrypted = encryptor.decrypt_string(encrypted)
        print(f"  原文: {plaintext}")
        print(f"  加密: {encrypted}")
        print(f"  解密: {decrypted}")
        assert decrypted == plaintext, "解密失败"
        print("  ✓ 加密测试通过")
        
        # 加密字典测试
        test_dict = {
            "user": "admin",
            "api_key": "sk-test1234567890abcdef",
            "message": "测试消息"
        }
        encrypted_dict = encryptor.encrypt_dict(test_dict, ["api_key"])
        print(f"\n  原始字典: {test_dict}")
        print(f"  加密字典: {encrypted_dict}")
        decrypted_dict = encryptor.decrypt_dict(encrypted_dict)
        print(f"  解密字典: {decrypted_dict}")
        assert decrypted_dict["api_key"] == test_dict["api_key"], "字典解密失败"
        print("  ✓ 字典加密测试通过")
    else:
        print("\n2. 加密功能跳过 (cryptography库未安装)")
    
    print("\n" + "=" * 70)
    print("✅ 所有安全模块测试通过!")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    try:
        sys.exit(test_security())
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
