"""安全配置管理器 - 加密存储敏感信息

使用AES-GCM加密算法安全存储API Key等敏感配置，支持：
- 加密存储到文件
- 从环境变量读取（优先级最高）
- 加密密钥管理
- 友好的错误提示和异常处理
"""

import os
import json
import base64
import hashlib
import logging
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.exceptions import InvalidTag
from pathlib import Path
from typing import Optional, Dict, Any

# 加密配置常量
DEFAULT_KEY_FILE = ".encryption_key"
DEFAULT_SECURE_CONFIG_FILE = ".secure_config.json"
SALT_SIZE = 16
NONCE_SIZE = 12

# 日志记录器
logger = logging.getLogger(__name__)


class SecureConfigError(Exception):
    """安全配置异常基类"""
    pass


class DecryptionError(SecureConfigError):
    """解密失败异常"""
    pass


class KeyFileError(SecureConfigError):
    """密钥文件异常"""
    pass


class ConfigFileError(SecureConfigError):
    """配置文件异常"""
    pass

class SecureConfigManager:
    """安全配置管理器 - 使用AES-GCM加密存储敏感配置"""
    
    def __init__(self, key_file: str = None, secure_config_file: str = None):
        """
        初始化安全配置管理器
        
        Args:
            key_file: 加密密钥文件路径，默认为 .encryption_key
            secure_config_file: 加密配置文件路径，默认为 .secure_config.json
        """
        self._key_file = Path(key_file or DEFAULT_KEY_FILE)
        self._secure_config_file = Path(secure_config_file or DEFAULT_SECURE_CONFIG_FILE)
        self._encryption_key = None
        self._backend = default_backend()
        
        # 确保密钥存在
        self._ensure_key_exists()
    
    def _generate_key(self) -> bytes:
        """生成256位加密密钥"""
        return os.urandom(32)
    
    def _derive_key(self, password: bytes, salt: bytes) -> bytes:
        """使用PBKDF2从密码派生密钥"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=self._backend
        )
        return kdf.derive(password)
    
    def _ensure_key_exists(self):
        """确保加密密钥文件存在"""
        if not self._key_file.exists():
            try:
                # 创建密钥文件所在目录
                self._key_file.parent.mkdir(parents=True, exist_ok=True)
                
                # 生成并保存密钥
                key = self._generate_key()
                with open(self._key_file, 'wb') as f:
                    f.write(key)
                
                # 设置文件权限（仅所有者可读）
                os.chmod(self._key_file, 0o600)
                
                logger.info(f"[安全配置] 已创建加密密钥文件: {self._key_file}")
            except Exception as e:
                raise KeyFileError(f"创建密钥文件失败: {e}") from e
    
    def _load_encryption_key(self) -> bytes:
        """加载加密密钥"""
        if self._encryption_key is None:
            try:
                with open(self._key_file, 'rb') as f:
                    self._encryption_key = f.read()
                
                if len(self._encryption_key) != 32:
                    raise KeyFileError(f"密钥文件长度不正确，期望32字节，实际{len(self._encryption_key)}字节")
                
                logger.debug(f"[安全配置] 已加载加密密钥: {self._key_file}")
            except FileNotFoundError:
                raise KeyFileError(f"密钥文件不存在: {self._key_file}")
            except PermissionError:
                raise KeyFileError(f"无法读取密钥文件（权限不足）: {self._key_file}")
            except Exception as e:
                raise KeyFileError(f"加载密钥文件失败: {e}") from e
        
        return self._encryption_key
    
    def encrypt(self, plaintext: str) -> str:
        """
        加密字符串
        
        Args:
            plaintext: 要加密的明文
        
        Returns:
            加密后的Base64字符串
        """
        key = self._load_encryption_key()
        nonce = os.urandom(NONCE_SIZE)
        
        cipher = Cipher(algorithms.AES(key), modes.GCM(nonce), backend=self._backend)
        encryptor = cipher.encryptor()
        
        ciphertext = encryptor.update(plaintext.encode('utf-8')) + encryptor.finalize()
        
        # 返回 nonce + tag + ciphertext 的Base64编码
        return base64.b64encode(nonce + encryptor.tag + ciphertext).decode('utf-8')
    
    def decrypt(self, encrypted_text: str) -> Optional[str]:
        """
        解密字符串
        
        Args:
            encrypted_text: 加密的Base64字符串
        
        Returns:
            解密后的明文，解密失败返回None
        """
        try:
            if not encrypted_text or not isinstance(encrypted_text, str):
                logger.warning("[安全配置] 解密失败：输入为空或不是字符串")
                return None
            
            key = self._load_encryption_key()
            data = base64.b64decode(encrypted_text)
            
            if len(data) < NONCE_SIZE + 16:
                logger.warning("[安全配置] 解密失败：数据长度不足")
                return None
            
            nonce = data[:NONCE_SIZE]
            tag = data[NONCE_SIZE:NONCE_SIZE + 16]
            ciphertext = data[NONCE_SIZE + 16:]
            
            cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag), backend=self._backend)
            decryptor = cipher.decryptor()
            
            plaintext = decryptor.update(ciphertext) + decryptor.finalize()
            return plaintext.decode('utf-8')
        
        except InvalidTag:
            logger.warning("[安全配置] 解密失败：无效的认证标签（密钥不匹配或数据被篡改）")
            return None
        except ValueError as e:
            logger.warning(f"[安全配置] 解密失败：Base64解码错误 - {e}")
            return None
        except TypeError as e:
            logger.warning(f"[安全配置] 解密失败：类型错误 - {e}")
            return None
        except KeyFileError as e:
            logger.error(f"[安全配置] 解密失败：密钥加载错误 - {e}")
            return None
        except Exception as e:
            logger.error(f"[安全配置] 解密失败：未知错误 - {e}")
            return None
    
    def save_secure_config(self, config: Dict[str, Any]):
        """
        保存加密配置到文件
        
        Args:
            config: 包含敏感信息的配置字典
        """
        encrypted_config = {}
        for key, value in config.items():
            if isinstance(value, str) and value:
                encrypted_config[key] = self.encrypt(value)
            else:
                encrypted_config[key] = value
        
        self._secure_config_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(self._secure_config_file, 'w', encoding='utf-8') as f:
            json.dump(encrypted_config, f, indent=2)
        
        os.chmod(self._secure_config_file, 0o600)
    
    def load_secure_config(self) -> Dict[str, Any]:
        """
        加载并解密配置
        
        Returns:
            解密后的配置字典
        """
        if not self._secure_config_file.exists():
            logger.debug(f"[安全配置] 加密配置文件不存在: {self._secure_config_file}")
            return {}
        
        try:
            with open(self._secure_config_file, 'r', encoding='utf-8') as f:
                encrypted_config = json.load(f)
            
            decrypted_config = {}
            failed_keys = []
            
            for key, value in encrypted_config.items():
                if isinstance(value, str) and value:
                    decrypted = self.decrypt(value)
                    if decrypted is not None:
                        decrypted_config[key] = decrypted
                    else:
                        failed_keys.append(key)
                else:
                    decrypted_config[key] = value
            
            if failed_keys:
                logger.warning(f"[安全配置] 以下配置项解密失败: {', '.join(failed_keys)}")
            
            logger.debug(f"[安全配置] 已加载 {len(decrypted_config)} 个安全配置项")
            return decrypted_config
        
        except json.JSONDecodeError as e:
            logger.error(f"[安全配置] 配置文件格式错误: {e}")
            raise ConfigFileError(f"配置文件格式错误: {e}") from e
        except PermissionError:
            logger.error(f"[安全配置] 无法读取配置文件（权限不足）: {self._secure_config_file}")
            raise ConfigFileError(f"无法读取配置文件（权限不足）: {self._secure_config_file}")
        except Exception as e:
            logger.error(f"[安全配置] 加载配置文件失败: {e}")
            raise ConfigFileError(f"加载配置文件失败: {e}") from e
    
    def get_secure_value(self, key: str, default: Any = None) -> Any:
        """
        获取安全配置值
        
        优先级：环境变量 > 加密文件 > 默认值
        
        Args:
            key: 配置键
            default: 默认值
        
        Returns:
            配置值
        """
        # 优先从环境变量获取
        env_value = os.getenv(key.upper().replace('.', '_'))
        if env_value is not None:
            return env_value
        
        # 从加密文件获取
        config = self.load_secure_config()
        return config.get(key, default)
    
    def set_secure_value(self, key: str, value: str):
        """
        设置安全配置值（加密存储）
        
        Args:
            key: 配置键
            value: 配置值（将被加密存储）
        """
        config = self.load_secure_config()
        config[key] = value
        self.save_secure_config(config)


class SecureConfigMixin:
    """安全配置混合类 - 为Config类添加加密存储能力"""
    
    def __init__(self, *args, **kwargs):
        self._secure_manager = SecureConfigManager()
        super().__init__(*args, **kwargs)
    
    def _load_secure_config(self):
        """加载安全配置并合并到配置中"""
        secure_config = self._secure_manager.load_secure_config()
        
        if secure_config:
            # 处理LLM配置
            llm_api_key = secure_config.get('llm_api_key')
            if llm_api_key and not self.get('memory', 'llm', 'api_key'):
                self.set(llm_api_key, 'memory', 'llm', 'api_key')
            
            # 处理其他安全配置
            for key in ['api_key', 'secret', 'token', 'password']:
                value = secure_config.get(key)
                if value:
                    parts = key.split('_')
                    if len(parts) > 1:
                        self.set(value, *parts)
    
    def save_secure_config(self, **kwargs):
        """
        保存敏感配置到加密文件
        
        Args:
            kwargs: 键值对，如 llm_api_key="xxx"
        """
        self._secure_manager.save_secure_config(kwargs)
    
    def get_secure(self, key: str, default: Any = None) -> Any:
        """
        获取安全配置值
        
        Args:
            key: 配置键
            default: 默认值
        
        Returns:
            配置值（优先从环境变量获取）
        """
        return self._secure_manager.get_secure_value(key, default)


def encrypt_file(input_path: str, output_path: str, password: str = None):
    """
    加密文件内容
    
    Args:
        input_path: 输入文件路径
        output_path: 输出加密文件路径
        password: 密码（可选，不提供则使用系统密钥）
    """
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if password:
        # 使用密码派生密钥
        salt = os.urandom(SALT_SIZE)
        key = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        ).derive(password.encode('utf-8'))
    else:
        # 使用系统密钥
        manager = SecureConfigManager()
        key = manager._load_encryption_key()
    
    nonce = os.urandom(NONCE_SIZE)
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce), backend=default_backend())
    encryptor = cipher.encryptor()
    
    ciphertext = encryptor.update(content.encode('utf-8')) + encryptor.finalize()
    
    with open(output_path, 'wb') as f:
        if password:
            f.write(salt)
        f.write(nonce)
        f.write(encryptor.tag)
        f.write(ciphertext)
    
    os.chmod(output_path, 0o600)


def decrypt_file(input_path: str, output_path: str, password: str = None):
    """
    解密文件内容
    
    Args:
        input_path: 加密文件路径
        output_path: 输出解密文件路径
        password: 密码（可选，不提供则使用系统密钥）
    """
    with open(input_path, 'rb') as f:
        data = f.read()
    
    if password:
        # 从文件读取salt并派生密钥
        salt = data[:SALT_SIZE]
        remaining = data[SALT_SIZE:]
        key = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        ).derive(password.encode('utf-8'))
    else:
        # 使用系统密钥
        manager = SecureConfigManager()
        key = manager._load_encryption_key()
        remaining = data
    
    nonce = remaining[:NONCE_SIZE]
    tag = remaining[NONCE_SIZE:NONCE_SIZE + 16]
    ciphertext = remaining[NONCE_SIZE + 16:]
    
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag), backend=default_backend())
    decryptor = cipher.decryptor()
    
    plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(plaintext.decode('utf-8'))
