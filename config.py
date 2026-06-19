"""云枢全局配置 — 整合所有模块的配置参数

我是来自网天的云枢，这是我的"基因编码"——决定了我的身体参数、思维模式和记忆策略。
支持从字典、环境变量和配置文件（YAML）加载。

安全特性：
- API Key 等敏感信息使用 AES-GCM 加密存储
- 优先级：环境变量 > 加密文件 > 默认值
- 配置完整性校验（使用 Pydantic）
"""

import os
import logging
import json
from copy import deepcopy
from typing import Optional, Dict, Any, List, Union

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════════
#  Pydantic 模型支持（可选）
# ════════════════════════════════════════════════════════════════════════════════

try:
    from pydantic import BaseModel, Field, ValidationError, validator
    from pydantic.error_wrappers import ErrorWrapper
    _PYDANTIC_AVAILABLE = True
    logger.info("[ok] Pydantic 已加载，启用配置校验")
except ImportError:
    _PYDANTIC_AVAILABLE = False
    logger.warning("[warn] Pydantic 未安装，配置校验功能已禁用")

# 安全配置管理器（延迟导入避免循环依赖）
_secure_manager = None

def _get_secure_manager():
    """获取安全配置管理器（单例）"""
    global _secure_manager
    if _secure_manager is None:
        try:
            from .config_secure import SecureConfigManager
        except ImportError:
            from config_secure import SecureConfigManager
        _secure_manager = SecureConfigManager()
    return _secure_manager


# ════════════════════════════════════════════════════════════════════════════════
#  配置校验模型（使用 Pydantic）
# ════════════════════════════════════════════════════════════════════════════════

class LLMConfig(BaseModel):
    """LLM 配置模型"""
    provider: str = Field(default="", description="LLM 提供商 (openai/anthropic)")
    api_key: str = Field(default="", description="API 密钥")
    model: str = Field(default="", description="模型名称")
    timeout: int = Field(default=30, ge=1, le=300, description="超时时间（秒）")

    @validator('provider')
    def provider_must_be_valid(cls, v):
        if v and v not in ['openai', 'anthropic']:
            raise ValueError(f"provider 必须是 'openai' 或 'anthropic'，当前值: {v}")
        return v


class AsyncCompressConfig(BaseModel):
    """异步压缩配置模型"""
    enabled: bool = Field(default=True, description="是否启用异步压缩")
    interval_seconds: int = Field(default=60, ge=10, le=3600, description="压缩间隔（秒）")


class BlackboxConfig(BaseModel):
    """黑盒配置模型"""
    max_size_mb: int = Field(default=10, ge=1, le=1000, description="最大文件大小（MB）")
    max_files: int = Field(default=10, ge=1, le=100, description="最大文件数量")


class MemoryConfig(BaseModel):
    """记忆配置模型"""
    data_dir: str = Field(default="./data", description="数据目录")
    token_limit: int = Field(default=4096, ge=512, le=32768, description="Token 限制")
    compress_threshold: float = Field(default=0.8, ge=0.0, le=1.0, description="压缩阈值")
    async_compress: AsyncCompressConfig = Field(default_factory=AsyncCompressConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    blackbox: BlackboxConfig = Field(default_factory=BlackboxConfig)


class SensorConfig(BaseModel):
    """传感器配置模型"""
    enable_change_detection: bool = Field(default=True, description="启用变化检测")
    enable_event_monitor: bool = Field(default=True, description="启用事件监控")
    watch_dirs: Optional[List[str]] = Field(default=None, description="监控目录列表")


class CognitiveConfig(BaseModel):
    """认知配置模型"""
    config_path: Optional[str] = Field(default=None, description="配置文件路径")


class BehaviorConfig(BaseModel):
    """行为配置模型"""
    check_interval: int = Field(default=30, ge=5, le=300, description="检查间隔（秒）")


class PermissionConfig(BaseModel):
    """权限配置模型"""
    backup_dir: str = Field(default="./.backups", description="备份目录")


class SecurityConfig(BaseModel):
    """安全配置模型"""
    enable_encryption: bool = Field(default=True, description="启用加密")
    key_file: str = Field(default=".encryption_key", description="密钥文件路径")
    secure_config_file: str = Field(default=".secure_config.json", description="安全配置文件")


class FeaturesConfig(BaseModel):
    """功能开关配置模型"""
    v2_lifetrace: bool = Field(default=False, description="启用 LifeTrace")
    v2_persona: bool = Field(default=False, description="启用 Persona")
    v2_distillation: bool = Field(default=False, description="启用人格蒸馏")
    sandbox: bool = Field(default=False, description="启用 Python 沙盒")


class VoiceConfig(BaseModel):
    """语音配置模型"""
    tts_engine: str = Field(default="pyttsx3", description="TTS 引擎")
    audio_dir: str = Field(default="./data/audio", description="音频目录")


class PlanningConfig(BaseModel):
    """规划引擎配置模型"""
    enabled: bool = Field(default=True, description="启用规划引擎")
    max_iterations: int = Field(default=10, ge=1, le=100, description="最大迭代次数")
    complexity_threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="复杂度阈值")


class LogSystemConfig(BaseModel):
    """日志系统配置模型"""
    enabled: bool = Field(default=True, description="启用日志系统")
    db_path: str = Field(default="./data/logs/yunshu_logs.db", description="SQLite 数据库路径")
    raw_log_dir: str = Field(default="./data/logs/raw", description="JSONL 原始日志目录")
    retention_days: int = Field(default=90, ge=7, le=365, description="日志保留天数")
    auto_introspection: bool = Field(default=True, description="启用自动内省分析")
    introspection_interval: int = Field(default=1800, ge=300, le=86400, description="内省分析间隔（秒）")
    idle_timeout: int = Field(default=300, ge=60, le=3600, description="空闲检测超时（秒）")


class ConfigModel(BaseModel):
    """完整配置模型"""
    sensor: SensorConfig = Field(default_factory=SensorConfig)
    cognitive: CognitiveConfig = Field(default_factory=CognitiveConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    behavior: BehaviorConfig = Field(default_factory=BehaviorConfig)
    permission: PermissionConfig = Field(default_factory=PermissionConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    planning: PlanningConfig = Field(default_factory=PlanningConfig)
    log_system: LogSystemConfig = Field(default_factory=LogSystemConfig)


# ════════════════════════════════════════════════════════════════════════════════
#  配置校验器
# ════════════════════════════════════════════════════════════════════════════════

class ConfigValidationError(Exception):
    """配置校验异常"""
    def __init__(self, errors: List[Dict[str, str]]):
        super().__init__("配置校验失败，发现 %d 个错误" % len(errors))
        self.errors = errors

    def __str__(self):
        error_str = "\n".join("  • %s: %s" % (e['loc'], e['msg']) for e in self.errors)
        return "配置校验失败:\n%s" % error_str


# ============================================================================
# 配置校验模板
# 适用场景：应用启动、配置加载、配置变更
# 配置要点：优先使用 Pydantic 严格校验，降级使用基础校验
# ============================================================================
def validate_config(config: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    校验配置的完整性和正确性

    核心特性：
    - 支持 Pydantic 严格校验（优先）和基础校验（降级）
    - 详细的日志记录校验过程
    - 返回结构化的错误列表
    - 自动检测 Pydantic 可用性并选择合适的校验方式

    Args:
        config: 配置字典，应包含 sensor、cognitive、memory、behavior、permission、security 等配置节

    Returns:
        List[Dict[str, str]]: 错误列表，如果为空表示校验通过。
                              每个错误包含 'loc'（位置路径）和 'msg'（错误消息）

    使用示例:
        errors = validate_config(config_dict)
        if errors:
            for error in errors:
                print("%s: %s" % (error['loc'], error['msg']))
    """
    errors = []
    logger.debug("[配置校验] 📋 开始校验配置，配置包含 %d 个配置节", len(config.keys()))

    if not _PYDANTIC_AVAILABLE:
        logger.info("[配置校验] ⚠️ Pydantic 不可用，使用基础校验模式")
        errors.extend(_basic_validation(config))
        if errors:
            logger.debug("[配置校验] 📊 基础校验完成，发现 %d 个问题", len(errors))
        else:
            logger.debug("[配置校验] ✅ 基础校验完成，未发现问题")
        return errors

    logger.info("[配置校验] ✨ 使用 Pydantic 模型进行严格校验")
    try:
        ConfigModel(**config)
        logger.debug("[配置校验] ✅ Pydantic 校验通过，配置完整有效")
        return []
    except ValidationError as e:
        error_count = len(e.errors())
        logger.warning("[配置校验] ⚠️ Pydantic 校验失败，发现 %d 个错误", error_count)
        for error in e.errors():
            loc = ".".join(str(l) for l in error['loc'])
            msg = error['msg']
            errors.append({"loc": loc, "msg": msg})
            logger.debug("[配置校验] 📝 错误: %s -> %s", loc, msg)
        return errors


def _basic_validation(config: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    基础配置校验（不依赖 Pydantic）

    Args:
        config: 配置字典

    Returns:
        错误列表
    """
    errors = []
    logger.debug("[配置校验] 📋 开始基础配置校验，当前配置包含 %d 个配置节", len(config.keys()))

    required_sections = {
        'sensor': '感知系统配置（监控目录、变化检测等）',
        'cognitive': '认知系统配置（提示词配置路径等）',
        'memory': '记忆系统配置（数据目录、Token限制、LLM配置等）',
        'behavior': '行为控制系统配置（健康检查间隔等）',
        'permission': '权限系统配置（备份目录等）',
        'security': '安全配置（加密开关、密钥文件路径等）'
    }
    logger.debug("[配置校验] 📋 检查 %d 个必需配置节", len(required_sections))
    
    for section, description in required_sections.items():
        if section not in config:
            logger.warning("[配置校验] ⚠️ 缺少必需配置节 '%s'（%s）", section, description)
            errors.append({
                "loc": section, 
                "msg": "缺少必需的 '%s' 配置节，%s。建议参考默认配置补充此节。" % (section, description)
            })
        elif not isinstance(config[section], dict):
            section_type = type(config[section]).__name__
            logger.warning("[配置校验] ⚠️ 配置节 '%s' 类型错误，期望字典，实际为 %s", section, section_type)
            errors.append({
                "loc": section, 
                "msg": "'%s' 配置节必须是字典类型，当前类型不正确。" % section
            })
        else:
            logger.debug("[配置校验] ✅ 配置节 '%s' 检查通过", section)

    # 检查 memory 配置
    memory = config.get('memory', {})
    logger.debug("[配置校验] 🧠 检查 memory 配置")
    if isinstance(memory, dict):
        llm = memory.get('llm', {})
        if isinstance(llm, dict):
            timeout = llm.get('timeout', 30)
            if not isinstance(timeout, int) or timeout < 1 or timeout > 300:
                logger.warning("[配置校验] ⚠️ memory.llm.timeout 值 '%s' 无效，应在 1-300 秒之间", timeout)
                errors.append({
                    "loc": "memory.llm.timeout", 
                    "msg": "LLM 超时时间设置为 %d 秒无效，应在 1-300 秒范围内。默认值为 30 秒。" % timeout
                })
            else:
                logger.debug("[配置校验] ✅ memory.llm.timeout = %d 秒，校验通过", timeout)

        token_limit = memory.get('token_limit', 4096)
        if not isinstance(token_limit, int) or token_limit < 512 or token_limit > 32768:
            logger.warning("[配置校验] ⚠️ memory.token_limit 值 '%s' 无效，应在 512-32768 之间", token_limit)
            errors.append({
                "loc": "memory.token_limit", 
                "msg": "Token 限制设置为 %d 无效，应在 512-32768 范围内。默认值为 4096。" % token_limit
            })
        else:
            logger.debug("[配置校验] ✅ memory.token_limit = %d，校验通过", token_limit)
    else:
        logger.warning("[配置校验] ⚠️ memory 配置不是字典类型，将使用默认值")

    # 检查 behavior 配置
    behavior = config.get('behavior', {})
    logger.debug("[配置校验] 🤖 检查 behavior 配置")
    if isinstance(behavior, dict):
        check_interval = behavior.get('check_interval', 30)
        if not isinstance(check_interval, int) or check_interval < 5 or check_interval > 300:
            logger.warning("[配置校验] ⚠️ behavior.check_interval 值 '%s' 无效，应在 5-300 秒之间", check_interval)
            errors.append({
                "loc": "behavior.check_interval", 
                "msg": "健康检查间隔设置为 %d 秒无效，应在 5-300 秒范围内。默认值为 30 秒。" % check_interval
            })
        else:
            logger.debug("[配置校验] ✅ behavior.check_interval = %d 秒，校验通过", check_interval)

    # 检查 security 配置
    security = config.get('security', {})
    logger.debug("[配置校验] 🔒 检查 security 配置")
    if isinstance(security, dict):
        if 'enable_encryption' in security and not isinstance(security['enable_encryption'], bool):
            encryption_val = security['enable_encryption']
            logger.warning("[配置校验] ⚠️ security.enable_encryption 值 '%s' 无效，应为布尔值", encryption_val)
            errors.append({
                "loc": "security.enable_encryption", 
                "msg": "加密开关设置为 '%s' 无效，应为 True 或 False。默认值为 True。" % encryption_val
            })
        else:
            logger.debug("[配置校验] ✅ security.enable_encryption 校验通过")

    if errors:
        logger.warning("[配置校验] 📊 基础校验完成，共发现 %d 个配置问题", len(errors))
    else:
        logger.debug("[配置校验] ✅ 基础校验完成，未发现问题")
        
    return errors


def validate_and_fix_config(config: Dict[str, Any]) -> tuple[Dict[str, Any], List[Dict[str, str]]]:
    """
    校验并自动修复配置

    Args:
        config: 配置字典

    Returns:
        (修复后的配置, 未修复的错误列表)
    """
    errors = []
    fixed_config = deepcopy(config)

    # 检查并添加缺失的必需节
    required_sections = {
        'sensor': {},
        'cognitive': {},
        'memory': {},
        'behavior': {},
        'permission': {},
        'security': {},
    }

    for section, default in required_sections.items():
        if section not in fixed_config:
            fixed_config[section] = default
            errors.append({"loc": section, "msg": f"配置节缺失，已使用默认值: {default}"})

    # 修复 memory 配置
    memory = fixed_config.get('memory', {})
    if not isinstance(memory, dict):
        fixed_config['memory'] = {}
        memory = fixed_config['memory']

    llm = memory.get('llm', {})
    if not isinstance(llm, dict):
        memory['llm'] = {}
        llm = memory['llm']

    if 'timeout' in llm and (not isinstance(llm['timeout'], int) or llm['timeout'] < 1 or llm['timeout'] > 300):
        llm['timeout'] = 30
        errors.append({"loc": "memory.llm.timeout", "msg": "值无效，已修正为 30"})

    if 'token_limit' in memory and (not isinstance(memory['token_limit'], int) or memory['token_limit'] < 512 or memory['token_limit'] > 32768):
        memory['token_limit'] = 4096
        errors.append({"loc": "memory.token_limit", "msg": "值无效，已修正为 4096"})

    # 修复 behavior 配置
    behavior = fixed_config.get('behavior', {})
    if not isinstance(behavior, dict):
        fixed_config['behavior'] = {}
        behavior = fixed_config['behavior']

    if 'check_interval' in behavior and (not isinstance(behavior['check_interval'], int) or behavior['check_interval'] < 5 or behavior['check_interval'] > 300):
        behavior['check_interval'] = 30
        errors.append({"loc": "behavior.check_interval", "msg": "值无效，已修正为 30"})

    # 修复 security 配置
    security = fixed_config.get('security', {})
    if not isinstance(security, dict):
        fixed_config['security'] = {}
        security = fixed_config['security']

    if 'enable_encryption' in security and not isinstance(security['enable_encryption'], bool):
        security['enable_encryption'] = True
        errors.append({"loc": "security.enable_encryption", "msg": "值无效，已修正为 True"})

    return fixed_config, errors


# ════════════════════════════════════════════════════════════════════════════════
#  全局配置聚合器
# ════════════════════════════════════════════════════════════════════════════════

class Config:
    """全局配置聚合器

    将所有子模块的配置统一管理，提供默认值 + 加密文件 + 环境变量 + 传入覆盖的四层配置策略。

    安全特性：
        - API Key 通过加密文件存储，避免明文泄露
        - 环境变量优先级最高，便于容器化部署
        - 配置导出时自动脱敏敏感信息
        - 配置完整性校验

    使用示例:
        config = Config()
        digital_life = DigitalLife(config.merged)
    """

    # ── 默认配置 ──
    DEFAULT = {
        "sensor": {
            "enable_change_detection": True,
            "enable_event_monitor": True,
            "watch_dirs": None,
        },
        "cognitive": {
            "config_path": None,
        },
        "memory": {
            "data_dir": "./data",
            "token_limit": 4096,
            "compress_threshold": 0.8,
            "async_compress": {
                "enabled": True,
                "interval_seconds": 60,
            },
            "llm": {
                "provider": "",
                "api_key": "",
                "model": "",
                "timeout": 30,
            },
            "blackbox": {
                "max_size_mb": 10,
                "max_files": 10,
            },
        },
        "behavior": {
            "check_interval": 30,
        },
        "permission": {
            "backup_dir": "./.backups",
        },
        "security": {
            "enable_encryption": True,
            "key_file": ".encryption_key",
            "secure_config_file": ".secure_config.json",
        },
        "features": {
            "v2_lifetrace": False,
            "v2_persona": False,
            "v2_distillation": False,
            "sandbox": False,
        },
        "voice": {
            "tts_engine": "pyttsx3",
            "audio_dir": "./data/audio",
            "non_blocking": True,
        },
        "planning": {
            "enabled": True,
            "max_iterations": 10,
            "complexity_threshold": 0.5,
        },
    }

    def __init__(self, overrides: dict = None, validate: bool = True):
        """初始化全局配置

        加载顺序：内置默认 → 加密文件 → 环境变量 → 传入覆盖

        Args:
            overrides: 覆盖配置字典
            validate: 是否启用配置校验
        """
        self._data = deepcopy(self.DEFAULT)
        self._load_from_secure_config()
        self._load_from_env()
        if overrides:
            self._merge(overrides)
        
        # 配置校验
        if validate:
            self._validate_config()
    
    def _validate_config(self):
        """校验配置的完整性和正确性"""
        errors = validate_config(self._data)
        
        if errors:
            logger.warning("[配置校验] 发现 %d 个配置问题:", len(errors))
            for error in errors:
                logger.warning("   • %s: %s", error['loc'], error['msg'])
            
            # 尝试自动修复
            fixed_config, fix_errors = validate_and_fix_config(self._data)
            if fix_errors:
                self._data = fixed_config
                logger.info("[配置校验] 已自动修复配置问题:")
                for error in fix_errors:
                    logger.info("   • %s: %s", error['loc'], error['msg'])
        else:
            logger.info("[配置校验] 配置校验通过")
    
    def _load_from_secure_config(self):
        """从加密配置文件加载敏感信息
        
        安全配置文件使用 AES-GCM 加密，包含 API Key 等敏感信息。
        """
        if not self.get("security", "enable_encryption", True):
            return
        
        try:
            secure_manager = _get_secure_manager()
            secure_config = secure_manager.load_secure_config()
            
            if secure_config:
                # 加载 LLM API Key
                llm_api_key = secure_config.get('llm_api_key')
                if llm_api_key and not self.get('memory', 'llm', 'api_key'):
                    self.set(llm_api_key, 'memory', 'llm', 'api_key')
                    logger.info("[安全配置] 已从加密文件加载 LLM API Key")
                
                # 加载其他敏感配置
                for key, value in secure_config.items():
                    if key.endswith('_api_key') or key.endswith('_secret') or key.endswith('_password'):
                        # 处理如 external_api_key 这样的配置
                        parts = key.split('_')
                        if len(parts) >= 2:
                            # 尝试设置到对应的配置路径
                            if parts[0] in self._data:
                                self.set(value, parts[0], '_'.join(parts[1:]))
                                
        except Exception as e:
            logger.warning("加载加密配置失败: %s", e)
    
    def save_secure_config(self, **kwargs):
        """保存敏感配置到加密文件
        
        Args:
            kwargs: 键值对，如 llm_api_key="xxx"
        
        Example:
            config = Config()
            config.save_secure_config(llm_api_key="sk-xxx")
        """
        try:
            secure_manager = _get_secure_manager()
            secure_manager.save_secure_config(kwargs)
            logger.info(f"[安全配置] 已保存 {len(kwargs)} 个敏感配置项")
        except Exception as e:
            logger.error(f"保存加密配置失败: {e}")

    @property
    def merged(self) -> dict:
        """获取合并后的完整配置"""
        return deepcopy(self._data)

    def _load_from_env(self):
        """从环境变量加载配置

        支持的环境变量:
            LLM_PROVIDER: LLM 提供商 (openai/anthropic)
            LLM_API_KEY: API 密钥
            LLM_MODEL: 模型名称
        """
        llm = self._data["memory"]["llm"]
        if os.getenv("LLM_PROVIDER"):
            llm["provider"] = os.getenv("LLM_PROVIDER")
        if os.getenv("LLM_API_KEY"):
            llm["api_key"] = os.getenv("LLM_API_KEY")
        if os.getenv("LLM_MODEL"):
            llm["model"] = os.getenv("LLM_MODEL")

    def _merge(self, overrides: dict, target: dict = None):
        """递归合并配置"""
        if target is None:
            target = self._data
        for key, value in overrides.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                self._merge(value, target[key])
            else:
                target[key] = value

    def get(self, *keys, default=None):
        """按路径获取配置值

        Args:
            *keys: 配置键路径，如 get("memory", "llm", "model")
            default: 键不存在时的默认值

        Returns:
            配置值，键不存在返回 default
        """
        current = self._data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def set(self, value, *keys):
        """按路径设置配置值

        Args:
            value: 要设置的值
            *keys: 配置键路径
        """
        current = self._data
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[keys[-1]] = value

    def to_dict(self) -> dict:
        """导出为纯字典（隐藏敏感信息）"""
        result = deepcopy(self._data)
        # 隐藏 API Key
        llm = result.get("memory", {}).get("llm", {})
        if llm.get("api_key"):
            llm["api_key"] = "***"
        return result

    def __repr__(self) -> str:
        return f"Config({self.to_dict()})"
