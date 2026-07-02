"""Agent 模块日志与安全工具

提供统一的日志配置和安全保护机制，包括：
- 日志系统配置（支持日志轮转）
- 结构化日志格式化（StructuredLogFormatter，对 JSON 日志美化显示）
- 敏感信息自动脱敏
- 权限操作审计日志
- Windows GBK 编码兼容（emoji 自动替换）

setup_agent_logging() 会在控制台 handler 上自动启用 StructuredLogFormatter：
- JSON 日志：格式化为 [trace_id] module | action | duration_ms 多行显示
- 非 JSON 日志：回退到带时间戳的标准格式
"""

import os
import sys
import re
import json
import logging
import logging.handlers
import threading
from datetime import datetime
from typing import Optional, Callable, Any, Dict, Pattern, List
import uuid

# ─────────────────────────────────────────────────
# Windows GBK 编码兼容性处理 - Emoji 替换
# ─────────────────────────────────────────────────

_EMOJI_MAP = {
    '🚀': '[ROCKET]', '📋': '[LIST]', '🎛️': '[CONTROL]', '✅': '[OK]',
    '❌': '[FAIL]', '⚠️': '[WARN]', '🔒': '[LOCK]', '🔓': '[UNLOCK]',
    '📦': '[PACKAGE]', '🔄': '[RELOAD]', '📊': '[CHART]', '🔍': '[SEARCH]',
    '💡': '[IDEA]', '🔥': '[FIRE]', '✨': '[STAR]', '🎉': '[PARTY]',
    '👏': '[CLAP]', '👍': '[THUMBS_UP]', '💬': '[CHAT]', '⏳': '[WAIT]',
    '⌛': '[TIME]', '📈': '[UP]', '📉': '[DOWN]', '🎯': '[TARGET]',
    '💻': '[PC]', '📱': '[PHONE]', '🔧': '[TOOL]', '⚙️': '[SETTINGS]',
    '🔌': '[PLUG]', '💾': '[SAVE]', '📝': '[EDIT]', '🔗': '[LINK]',
    '💎': '[DIAMOND]', '🏆': '[TROPHY]', '🎮': '[GAME]', '👤': '[USER]',
    '👥': '[USERS]', '👨‍💻': '[DEV]', '🤖': '[ROBOT]', '💀': '[SKULL]',
    '💩': '[POO]', '👻': '[GHOST]', '🤝': '[HAND_SHAKE]', '👋': '[WAVE]',
    '💪': '[MUSCLE]', '👀': '[EYES]', '💭': '[THINK]', '😀': '[SMILE]',
    '😂': '[TEARS]', '😊': '[BLUSH]', '😍': '[HEART_EYES]', '🤔': '[THINKING]',
    '🙄': '[EYE_ROLL]', '😴': '[SLEEPING]', '😎': '[COOL]', '🤓': '[NERD]',
    '😕': '[CONFUSED]', '😟': '[WORRIED]', '😭': '[LOUDLY]', '😡': '[ANGRY]',
    '🤬': '[SHOUTING]', '🎃': '[PUMPKIN]', '🎅': '[SANTA]', '🎆': '[FIREWORKS]',
    '📌': '[PIN]', '📍': '[MAP_PIN]', '📧': '[EMAIL]', '📨': '[INBOX]',
    '📤': '[OUTBOX]', '📥': '[INCOMING]', '📫': '[MAILBOX]', '✉️': '[LETTER]',
    '🔖': '[LABEL]', '🏷️': '[TAG]', '💳': '[CREDIT_CARD]', '💰': '[MONEY_BAG]',
    '🤑': '[MONEY_EYES]', '💸': '[MONEY_FLY]', '🧾': '[RECEIPT]', '🔏': '[LOCKED]',
    '🔐': '[UNLOCKED]', '🔑': '[KEY]', '🕵️': '[DETECTIVE]', '📁': '[FOLDER]',
    '📂': '[OPEN_FOLDER]', '📅': '[CALENDAR]', '📆': '[CALENDAR2]', '📚': '[BOOKS]',
    '📓': '[NOTEBOOK]', '📰': '[NEWSPAPER]', '🗂️': '[FILE_CABINET]', '🛑': '[STOP]',
    '🚫': '[NO]', '📶': '[SIGNAL]', '📳': '[VIBRATE]', '📴': '[OFF]',
    '🖥️': '[MONITOR]', '🖨️': '[PRINTER]', '🖱️': '[MOUSE]', '⌨️': '[KEYBOARD]',
    '🎫': '[TICKET]', '🎟️': '[TICKET2]', '🗳️': '[BALLOT]', '✏️': '[PENCIL]',
    '✒️': '[PEN]', '🖋️': '[FOUNTAIN]', '🖌️': '[BRUSH]', '🖍️': '[CRAYON]',
    '📇': '[CARD]', '📏': '[RULER]', '📐': '[PROTRACTOR]', '📕': '[BOOK_RED]',
    '📖': '[BOOK_OPEN]', '📗': '[BOOK_GREEN]', '📘': '[BOOK_BLUE]', '📙': '[BOOK_YELLOW]',
    '🔎': '[SEARCH2]', '🗝️': '[KEY2]', '👪': '[FAMILY]', '👨': '[MAN]',
    '👩': '[WOMAN]', '👴': '[OLD_MAN]', '👵': '[OLD_WOMAN]', '👶': '[BABY]',
    '👦': '[BOY]', '👧': '[GIRL]', '🏠': '[HOME]', '🏢': '[OFFICE]',
    '🏭': '[FACTORY]', '🌍': '[GLOBE]', '🌎': '[GLOBE]', '🌏': '[GLOBE]',
    '🗺️': '[MAP]', '🏔️': '[MOUNTAIN]', '🌊': '[WAVE]', '🌋': '[VOLCANO]',
    '🌅': '[SUNRISE]', '🌙': '[MOON]', '☀️': '[SUN]', '🌈': '[RAINBOW]',
    '❄️': '[SNOW]', '🍀': '[LUCKY]', '🌸': '[FLOWER]', '🌹': '[ROSE]',
    '🌻': '[SUNFLOWER]', '🌲': '[TREE]', '🌳': '[TREE]', '🌴': '[PALM]',
    '🍎': '[APPLE]', '🍊': '[ORANGE]', '🍋': '[LEMON]', '🍌': '[BANANA]',
    '🍉': '[WATERMELON]', '🍇': '[GRAPES]', '🍓': '[STRAWBERRY]', '🍒': '[CHERRY]',
    '🥝': '[KIWI]', '🍕': '[PIZZA]', '🍔': '[BURGER]', '🍟': '[FRIES]',
    '🌭': '[HOTDOG]', '🥪': '[SANDWICH]', '🌮': '[TACO]', '🌯': '[BURRITO]',
    '🍲': '[SOUP]', '🍜': '[NOODLES]', '🍝': '[PASTA]', '🦐': '[SHRIMP]',
    '🦞': '[LOBSTER]', '🦀': '[CRAB]', '🐟': '[FISH]', '🦈': '[SHARK]',
    '🐬': '[DOLPHIN]', '🐳': '[WHALE]', '🐢': '[TURTLE]', '🐍': '[SNAKE]',
    '🦎': '[LIZARD]', '🦖': '[DINO]', '🐉': '[DRAGON]', '🐲': '[DRAGON]',
    '🐊': '[CROCODILE]', '🐸': '[FROG]', '🐰': '[RABBIT]', '🐻': '[BEAR]',
    '🐼': '[PANDA]', '🐨': '[KOALA]', '🐯': '[TIGER]', '🦁': '[LION]',
    '🐮': '[COW]', '🐷': '[PIG]', '🐑': '[SHEEP]', '🐐': '[GOAT]',
    '🐴': '[HORSE]', '🦄': '[UNICORN]', '🐝': '[BEE]', '🐞': '[BUG]',
    '🦋': '[BUTTERFLY]', '🐌': '[SNAIL]', '🐛': '[WORM]', '🦟': '[MOSQUITO]',
    '🐦': '[BIRD]', '🐤': '[CHICK]', '🐔': '[CHICKEN]', '🦆': '[DUCK]',
    '🦅': '[EAGLE]', '🦉': '[OWL]', '🦇': '[BAT]', '🐧': '[PENGUIN]',
    '🐿️': '[SQUIRREL]', '🦔': '[HOG]', '🧑‍🎄': '[CHRISTMAS]', '🦌': '[REINDEER]',
    '🌟': '[STAR2]', '⭐': '[STAR]', '🌠': '[SHOOTING]', '💫': '[DIZZY_STARS]',
    # 以下条目来自 agent/utils/safe_logger.py（合并）
    '💽': '[FLOPPY]', '📀': '[DVD]', '🖲️': '[TRACKBALL]',
    '📃': '[PAGE]', '📄': '[DOCUMENT]', '📑': '[DIVIDER]',
}



_logger = logging.getLogger(__name__)


def _trace_id():
    """生成 trace_id（结构化日志用）"""
    return uuid.uuid4().hex[:16]


def log_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    """规范化日志字典并返回，供 logger.X(log_dict({...})) 直接传递 dict

    消除调用方 json.dumps + formatter json.loads 的双重序列化开销。
    """
    data = dict(payload)
    if "msg" in data:
        if "message" not in data:
            data["message"] = data.pop("msg")
        else:
            data.pop("msg")
    data.setdefault("trace_id", _trace_id())
    data.setdefault("module_name", "unknown")
    data.setdefault("action", "unknown")
    data.setdefault("duration_ms", 0)
    return data


def _safe_log_message(message):
    """安全处理日志消息，替换 emoji 避免 GBK 编码问题"""
    if not isinstance(message, str):
        return message

    for emoji, replacement in _EMOJI_MAP.items():
        message = message.replace(emoji, replacement)

    return message


def _safe_log_dict(data: Dict) -> Dict:
    """递归处理 dict 中的所有 str 值，替换 emoji 避免 GBK 编码问题"""
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = _safe_log_message(value)
        elif isinstance(value, dict):
            result[key] = _safe_log_dict(value)
        elif isinstance(value, list):
            result[key] = [
                _safe_log_message(v) if isinstance(v, str)
                else _safe_log_dict(v) if isinstance(v, dict)
                else v
                for v in value
            ]
        else:
            result[key] = value
    return result


class EmojiFilter(logging.Filter):
    """日志过滤器 - 自动替换 emoji 字符

    支持 dict 和 str 两种 record.msg 类型。
    """

    def filter(self, record):
        if record.msg is not None:
            if isinstance(record.msg, dict):
                record.msg = _safe_log_dict(record.msg)
            elif isinstance(record.msg, str):
                record.msg = _safe_log_message(record.msg)
        if record.args:
            record.args = tuple(
                _safe_log_message(arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


class DictToJsonFilter(logging.Filter):
    """将 dict 类型的 record.msg 序列化为 JSON 字符串

    仅应挂载于文件 handler，控制台 handler 不应挂载。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, dict):
            record.msg = json.dumps(record.msg, ensure_ascii=False)
        return True

# ─────────────────────────────────────────────────
# 日志轮转配置
# ─────────────────────────────────────────────────

class LogRotationConfig:
    """
    日志轮转配置类
    
    配置参数：
        max_bytes: 单个日志文件最大大小（字节），默认为 50MB
        backup_count: 保留的备份文件数量，默认为 5
        encoding: 日志文件编码，默认为 utf-8
        when: 轮转时间单位（用于 TimedRotatingFileHandler）
        interval: 轮转间隔（与 when 配合使用）
        utc: 是否使用 UTC 时间
    """
    
    def __init__(
        self,
        max_bytes: int = 50 * 1024 * 1024,  # 50MB
        backup_count: int = 5,
        encoding: str = "utf-8",
        when: str = "midnight",
        interval: int = 1,
        utc: bool = False,
        use_timed_rotation: bool = False,
    ):
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.encoding = encoding
        self.when = when
        self.interval = interval
        self.utc = utc
        self.use_timed_rotation = use_timed_rotation

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'max_bytes': self.max_bytes,
            'backup_count': self.backup_count,
            'encoding': self.encoding,
            'when': self.when,
            'interval': self.interval,
            'utc': self.utc,
            'use_timed_rotation': self.use_timed_rotation,
        }

# ─────────────────────────────────────────────────
# 日志处理器工厂
# ─────────────────────────────────────────────────

def create_rotating_file_handler(
    log_file: str,
    config: LogRotationConfig = None,
    formatter: logging.Formatter = None,
) -> logging.Handler:
    """
    创建带轮转功能的文件处理器
    
    Args:
        log_file: 日志文件路径
        config: 轮转配置
        formatter: 日志格式化器
    
    Returns:
        日志处理器
    """
    if config is None:
        config = LogRotationConfig()
    
    # 确保日志目录存在
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    
    if config.use_timed_rotation:
        # 基于时间的轮转（如每天、每周）
        handler = logging.handlers.TimedRotatingFileHandler(
            filename=log_file,
            when=config.when,
            interval=config.interval,
            backupCount=config.backup_count,
            encoding=config.encoding,
            utc=config.utc,
        )
    else:
        # 基于文件大小的轮转
        handler = logging.handlers.RotatingFileHandler(
            filename=log_file,
            maxBytes=config.max_bytes,
            backupCount=config.backup_count,
            encoding=config.encoding,
        )
    
    if formatter:
        handler.setFormatter(formatter)
    
    return handler

# ─────────────────────────────────────────────────
# 日志配置
# ─────────────────────────────────────────────────

def setup_agent_logging(
    debug_mode: bool = False,
    log_file: Optional[str] = None,
    rotation_config: LogRotationConfig = None,
    enable_console: bool = True,
    enable_file: bool = False,
) -> logging.Logger:
    """
    配置 Agent 模块的日志系统

    当 enable_console=True 时，控制台 handler 自动启用 StructuredLogFormatter：
    - JSON 日志（含 trace_id/module_name/action/duration_ms）会被美化显示
    - 非 JSON 日志回退到带时间戳的标准格式
    - 文件 handler 始终使用标准 Formatter（保证日志文件可解析）

    Args:
        debug_mode: 是否启用调试模式
        log_file: 日志文件路径（如果启用文件日志）
        rotation_config: 日志轮转配置
        enable_console: 是否启用控制台输出（默认启用 StructuredLogFormatter）
        enable_file: 是否启用文件输出

    Returns:
        主日志记录器
    """
    # 基础日志格式
    log_format = "%(asctime)s [%(levelname)8s] %(name)-25s: %(message)s"
    date_format = "%H:%M:%S"
    
    formatter = logging.Formatter(log_format, date_format)
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)8s] %(name)-25s %(process)d:%(thread)d: %(message)s",
        "%Y-%m-%d %H:%M:%S"
    )

    # 获取根日志记录器
    root_logger = logging.getLogger()
    
    # 清除默认处理器
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    root_logger.setLevel(logging.DEBUG if debug_mode else logging.INFO)

    # 控制台处理器
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        # 尝试使用结构化日志格式化器（对 JSON 日志美化，非 JSON 回退到标准格式）
        try:
            from scripts.struct_log_formatter import StructuredLogFormatter
            console_handler.setFormatter(StructuredLogFormatter())
        except ImportError:
            console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.DEBUG if debug_mode else logging.INFO)
        console_handler.addFilter(SensitiveDataFilter())
        console_handler.addFilter(EmojiFilter())
        root_logger.addHandler(console_handler)

    # 文件处理器（带轮转）
    if enable_file and log_file:
        if rotation_config is None:
            rotation_config = LogRotationConfig()
        
        file_handler = create_rotating_file_handler(
            log_file,
            rotation_config,
            file_formatter,
        )
        file_handler.setLevel(logging.DEBUG if debug_mode else logging.INFO)
        file_handler.addFilter(SensitiveDataFilter())
        file_handler.addFilter(EmojiFilter())
        file_handler.addFilter(DictToJsonFilter())
        root_logger.addHandler(file_handler)
        logger = logging.getLogger("云枢.agent")
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.setup_agent_logging.log_file", "duration_ms": 0, "message": f"日志文件: {log_file}"}, ensure_ascii=False))
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.setup_agent_logging.rotation_config", "duration_ms": 0, "message": f"日志轮转: 最大 {rotation_config.max_bytes // (1024 * 1024)}MB/文件, 保留 {rotation_config.backup_count} 个备份"}, ensure_ascii=False))

    # 降低第三方库日志噪音
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    # Agent 核心模块日志级别
    agent_modules = [
        "agent.digital_life",
        "agent.behavior_controller",
        "agent.permission_system",
        "agent.safety_guard",
        "agent.system_tools",
        "agent.tools",
        "agent.planning",  # 如果存在
    ]

    for module in agent_modules:
        if debug_mode:
            logging.getLogger(module).setLevel(logging.DEBUG)
        else:
            logging.getLogger(module).setLevel(logging.INFO)

    # 规划引擎模块
    planning_modules = [
        "planning.core",
        "planning.decomposer",
        "planning.executor",
        "planning.reflector",
        "planning.state_machine",
        "planning.react",
        "planning.models",
    ]

    for module in planning_modules:
        if debug_mode:
            logging.getLogger(module).setLevel(logging.DEBUG)
        else:
            logging.getLogger(module).setLevel(logging.INFO)

    logger = logging.getLogger("云枢.agent")

    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.setup_agent_logging.log", "duration_ms": 0, "message": "=" * 70}, ensure_ascii=False))
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.setup_agent_logging.agent", "duration_ms": 0, "message": "Agent 模块日志系统已初始化"}, ensure_ascii=False))
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.setup_agent_logging.log", "duration_ms": 0, "message": f"调试模式: {'启用' if debug_mode else '关闭'}"}, ensure_ascii=False))
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.setup_agent_logging.log", "duration_ms": 0, "message": f"控制台输出: {'启用' if enable_console else '关闭'}"}, ensure_ascii=False))
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.setup_agent_logging.log", "duration_ms": 0, "message": f"文件输出: {'启用' if enable_file else '关闭'}"}, ensure_ascii=False))
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.setup_agent_logging.agent", "duration_ms": 0, "message": f"Agent 模块: {'DEBUG' if debug_mode else 'INFO'} 级别"}, ensure_ascii=False))
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.setup_agent_logging.log", "duration_ms": 0, "message": f"规划引擎: {'DEBUG' if debug_mode else 'INFO'} 级别"}, ensure_ascii=False))
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.setup_agent_logging.log", "duration_ms": 0, "message": "=" * 70}, ensure_ascii=False))

    return logger


def setup_error_logging(
    log_file: str = "./logs/errors.log",
    rotation_config: LogRotationConfig = None,
) -> logging.Logger:
    """
    配置错误日志专用记录器（独立轮转）

    Args:
        log_file: 错误日志文件路径
        rotation_config: 日志轮转配置

    Returns:
        错误日志记录器
    """
    if rotation_config is None:
        rotation_config = LogRotationConfig(
            max_bytes=20 * 1024 * 1024,  # 20MB
            backup_count=10,
        )
    
    logger = logging.getLogger("agent.errors")
    logger.setLevel(logging.ERROR)
    logger.propagate = False  # 不传播到根日志
    
    # 清除现有处理器
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # 创建轮转文件处理器
    file_handler = create_rotating_file_handler(
        log_file,
        rotation_config,
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s %(process)d:%(thread)d\n"
            "  File \"%(pathname)s\", line %(lineno)d, in %(funcName)s\n"
            "  %(message)s\n"
            "%(exc_info)s\n",
            "%Y-%m-%d %H:%M:%S"
        ),
    )
    file_handler.addFilter(SensitiveDataFilter())
    logger.addHandler(file_handler)
    
    return logger


# ─────────────────────────────────────────────────
# 敏感信息脱敏
# ─────────────────────────────────────────────────

class SensitiveDataFilter(logging.Filter):
    """
    日志敏感信息自动脱敏过滤器
    
    使用正则表达式匹配并替换日志中的敏感信息，包括：
    - API Key（如 sk-xxx, pk-xxx）
    - 密码（password, secret）
    - Token（token, jwt）
    - 密钥（key, secret_key）
    - 手机号（中国大陆、香港）
    - 身份证号（中国大陆18位）
    """
    
    def __init__(self):
        super().__init__()
        self._patterns = self._build_patterns()
    
    def _build_patterns(self) -> List[Pattern]:
        """
        构建敏感信息匹配正则表达式列表
        
        Returns:
            正则表达式列表
        """
        patterns = []
        
        # API Key 模式（独立的密钥字符串）
        patterns.append(re.compile(r'\bsk-[a-zA-Z0-9_-]{10,}\b', re.IGNORECASE))
        patterns.append(re.compile(r'\bpk-[a-zA-Z0-9_-]{10,}\b', re.IGNORECASE))
        
        # 通用密钥格式（较长的base64字符串）
        patterns.append(re.compile(r'\b[a-zA-Z0-9+/]{30,}\b', re.IGNORECASE))
        
        # JWT Token（以ey开头，较长的base64字符串）
        patterns.append(re.compile(r'\bey[A-Za-z0-9+/=]{40,}\b', re.IGNORECASE))
        
        # 密码字段值（完整匹配并替换）
        # P0-SEC-002 修复：[^"'\&\s]* 排除 & 和空白，避免吞噬相邻 URL 参数
        patterns.append(re.compile(r'(password|secret|token)\s*=\s*["\']?[^"\'&\s]*["\']?', re.IGNORECASE))
        patterns.append(re.compile(r'(password|secret|token)\s*:\s*["\']?[^"\'&\s]*["\']?', re.IGNORECASE))

        # 密钥字段值（完整匹配并替换）
        patterns.append(re.compile(r'(api_key|api\.key|secret_key|access_token)\s*=\s*["\']?[^"\'&\s]*["\']?', re.IGNORECASE))
        patterns.append(re.compile(r'(api_key|api\.key|secret_key|access_token)\s*:\s*["\']?[^"\'&\s]*["\']?', re.IGNORECASE))

        # URL 中的敏感参数（完整匹配并替换）
        patterns.append(re.compile(r'([?&])(api_key|key|secret|token)\s*=\s*[^&\s]*', re.IGNORECASE))

        # Bearer Token（P0-SEC-001 修复：独立匹配，整段替换为 Bearer [REDACTED]）
        patterns.append(re.compile(r'(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*'))
        
        # 手机号（中国大陆：11位数字，以1开头）
        patterns.append(re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'))
        
        # 手机号（带区号格式）
        patterns.append(re.compile(r'(?<!\d)(\+?86)?1[3-9]\d{9}(?!\d)'))
        
        # 香港手机号（8位数字，或带+852前缀）
        patterns.append(re.compile(r'(?<!\d)(\+?852)?[569]\d{7}(?!\d)'))
        
        # 身份证号（18位，支持最后一位为X）
        patterns.append(re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)'))
        
        # 身份证号（15位旧版）
        patterns.append(re.compile(r'(?<!\d)\d{15}(?!\d)'))
        
        return patterns
    
    def filter(self, record: logging.LogRecord) -> bool:
        """
        过滤日志记录，脱敏敏感信息
        
        Args:
            record: 日志记录对象
        
        Returns:
            True（始终允许记录，只是脱敏内容）
        """
        if hasattr(record, 'msg'):
            if isinstance(record.msg, str):
                record.msg = self._sanitize(record.msg)
            elif isinstance(record.msg, dict):
                record.msg = self._sanitize_dict(record.msg)

        if hasattr(record, 'args') and isinstance(record.args, tuple):
            sanitized_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    sanitized_args.append(self._sanitize(arg))
                elif isinstance(arg, dict):
                    sanitized_args.append(self._sanitize_dict(arg))
                else:
                    sanitized_args.append(arg)
            record.args = tuple(sanitized_args)
        
        return True
    
    def _sanitize(self, text: str) -> str:
        """
        脱敏文本中的敏感信息
        
        Args:
            text: 原始文本
        
        Returns:
            脱敏后的文本
        """
        if not isinstance(text, str):
            return text
        
        try:
            result = text
            
            # 处理独立的敏感字符串（API Key、密钥等）
            standalone_patterns = [
                re.compile(r'\bsk-[a-zA-Z0-9_-]{5,}\b', re.IGNORECASE),
                re.compile(r'\bpk-[a-zA-Z0-9_-]{5,}\b', re.IGNORECASE),
                re.compile(r'\b[a-zA-Z0-9+/]{20,}\b', re.IGNORECASE),
                re.compile(r'\bey[A-Za-z0-9+/=]{30,}\b', re.IGNORECASE),
            ]
            
            for pattern in standalone_patterns:
                result = pattern.sub('[REDACTED]', result)
            
            # 处理邮箱地址（完全脱敏，去除 @ 符号避免泄露邮箱特征）
            email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', re.IGNORECASE)
            result = email_pattern.sub('[REDACTED]', result)
            
            # 处理字段名=值形式的敏感信息
            # P0-SEC-002 修复：[^"'\&\s]* 排除 & 和空白，避免贪婪吞噬相邻 URL 参数
            field_patterns = [
                (re.compile(r'(password|secret|token)\s*=\s*["\']?([^"\'&\s]*)["\']?', re.IGNORECASE), r'\1="[REDACTED]"'),
                (re.compile(r'(password|secret|token)\s*:\s*["\']?([^"\'&\s]*)["\']?', re.IGNORECASE), r'\1: "[REDACTED]"'),
                (re.compile(r'(api_key|api\.key|secret_key|access_token)\s*=\s*["\']?([^"\'&\s]*)["\']?', re.IGNORECASE), r'\1="[REDACTED]"'),
                (re.compile(r'(api_key|api\.key|secret_key|access_token)\s*:\s*["\']?([^"\'&\s]*)["\']?', re.IGNORECASE), r'\1: "[REDACTED]"'),
                (re.compile(r'([?&])(api_key|key|secret|token)\s*=\s*([^&\s]*)', re.IGNORECASE), r'\1\2=[REDACTED]'),
            ]

            for pattern, replacement in field_patterns:
                result = pattern.sub(replacement, result)

            # 处理 Bearer Token（P0-SEC-001 修复：整段替换为 Bearer [REDACTED]）
            result = re.sub(r'(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*', 'Bearer [REDACTED]', result)
            
            # 先处理身份证号（18位）- 保留前6位和后4位
            # 18位身份证: 前6位地区码 + 8位生日 + 3位顺序码 + 1位校验码
            result = re.sub(r'(\d{6})\d{8}(\d{3}[Xx])', r'\1********\2', result)
            result = re.sub(r'(\d{6})\d{8}(\d{4})', r'\1********\2', result)
            
            # 处理身份证号（15位旧版）- 保留前6位和后3位
            result = re.sub(r'(\d{6})\d{6}(\d{3})', r'\1******\2', result)
            
            # 处理手机号（中国大陆）- 保留前3位和后4位 (11位: 1+1+1 + 4 + 4)
            result = re.sub(r'(1[3-9]\d)\d{4}(\d{4})', r'\1****\2', result)
            
            # 处理带区号的手机号
            result = re.sub(r'(\+?86)(1[3-9]\d)\d{4}(\d{4})', r'\1\2****\3', result)
            
            # 处理香港手机号 - 保留前缀和后4位
            result = re.sub(r'(\+?852)?([569]\d{3})\d{4}', lambda m: f"{m.group(1) or ''}{m.group(2)}****", result)
            
            return result
            
        except re.error as e:
            _logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils._sanitize.log", "duration_ms": 0, "message": f"脱敏正则表达式错误: {e}"}, ensure_ascii=False))
            return text
        except TypeError as e:
            _logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils._sanitize.log", "duration_ms": 0, "message": f"脱敏类型错误: {e}"}, ensure_ascii=False))
            return text
        except Exception as e:
            _logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils._sanitize.type", "duration_ms": 0, "message": f"脱敏处理异常: {type(e).__name__}: {e}"}, ensure_ascii=False))
            return text
    
    def _sanitize_dict(self, data: Dict) -> Dict:
        """
        递归脱敏字典中的敏感信息
        
        Args:
            data: 字典数据
        
        Returns:
            脱敏后的字典
        """
        result = {}
        sensitive_keys = {'password', 'secret', 'token', 'api_key', 'api.key', 'secret_key', 'access_token'}
        
        for key, value in data.items():
            # 如果键名是敏感的，直接替换值
            if key.lower() in sensitive_keys or any(sensitive in key.lower() for sensitive in sensitive_keys):
                result[key] = '[REDACTED]'
            elif isinstance(value, str):
                result[key] = self._sanitize(value)
            elif isinstance(value, dict):
                result[key] = self._sanitize_dict(value)
            elif isinstance(value, list):
                result[key] = [self._sanitize(v) if isinstance(v, str) else v for v in value]
            else:
                result[key] = value
        return result


# ─────────────────────────────────────────────────
# 审计日志
# ─────────────────────────────────────────────────

class AuditLogger:
    """
    权限操作审计日志记录器
    
    记录所有安全相关操作，包括：
    - 配置访问/修改
    - 权限变更
    - 敏感信息访问
    """
    
    def __init__(self):
        self._logger = logging.getLogger("agent.audit")
        self._logger.setLevel(logging.INFO)
        
        # 确保审计日志有独立处理器（输出到单独文件）
        if not self._logger.handlers:
            handler = logging.FileHandler(
                os.path.join(os.path.dirname(__file__), '..', 'logs', 'audit.log'),
                encoding='utf-8'
            )
            handler.setFormatter(logging.Formatter(
                '%(asctime)s [%(levelname)s] %(message)s',
                '%Y-%m-%d %H:%M:%S'
            ))
            self._logger.addHandler(handler)
            self._logger.propagate = False
    
    def log_config_access(self, config_key: str, user: str = "system"):
        """
        记录配置访问
        
        Args:
            config_key: 访问的配置键
            user: 访问用户（默认为系统）
        """
        self._logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.log_config_access.config_access", "duration_ms": 0, "message": f"CONFIG_ACCESS | user={user} | key={config_key}"}, ensure_ascii=False))
    
    def log_config_modification(self, config_key: str, user: str = "system"):
        """
        记录配置修改
        
        Args:
            config_key: 修改的配置键
            user: 修改用户（默认为系统）
        """
        self._logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.log_config_modification.config_modify", "duration_ms": 0, "message": f"CONFIG_MODIFY | user={user} | key={config_key}"}, ensure_ascii=False))
    
    def log_secure_config_access(self, config_key: str, success: bool, user: str = "system"):
        """
        记录安全配置访问
        
        Args:
            config_key: 访问的安全配置键
            success: 是否成功
            user: 访问用户（默认为系统）
        """
        status = "SUCCESS" if success else "FAILED"
        self._logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.log_secure_config_access.secure_config_access", "duration_ms": 0, "message": f"SECURE_CONFIG_ACCESS | user={user} | key={config_key} | status={status}"}, ensure_ascii=False))
    
    def log_encryption_key_access(self, success: bool, user: str = "system"):
        """
        记录加密密钥访问
        
        Args:
            success: 是否成功
            user: 访问用户（默认为系统）
        """
        status = "SUCCESS" if success else "FAILED"
        self._logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.log_encryption_key_access.encryption_key_access", "duration_ms": 0, "message": f"ENCRYPTION_KEY_ACCESS | user={user} | status={status}"}, ensure_ascii=False))
    
    def log_permission_change(self, action: str, resource: str, user: str = "system"):
        """
        记录权限变更
        
        Args:
            action: 操作类型（grant/revoke/modify）
            resource: 资源名称
            user: 操作用户（默认为系统）
        """
        self._logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.log_permission_change.permission_change", "duration_ms": 0, "message": f"PERMISSION_CHANGE | user={user} | action={action} | resource={resource}"}, ensure_ascii=False))
    
    def log_authentication(self, username: str, success: bool, ip_address: str = None):
        """
        记录认证尝试
        
        Args:
            username: 用户名
            success: 是否成功
            ip_address: 客户端IP地址（可选）
        """
        status = "SUCCESS" if success else "FAILED"
        ip_info = f" | ip={ip_address}" if ip_address else ""
        self._logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.log_authentication.authentication", "duration_ms": 0, "message": f"AUTHENTICATION | username={username} | status={status}{ip_info}"}, ensure_ascii=False))
    
    def log_sensitive_operation(self, operation: str, details: dict = None, user: str = "system"):
        """
        记录敏感操作
        
        Args:
            operation: 操作类型
            details: 操作详情（将被脱敏）
            user: 操作用户（默认为系统）
        """
        details_str = ""
        if details:
            sanitizer = SensitiveDataFilter()
            sanitized_details = sanitizer._sanitize_dict(details)
            details_str = f" | details={json.dumps(sanitized_details, ensure_ascii=False)}"
        
        self._logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.log_sensitive_operation.sensitive_operation", "duration_ms": 0, "message": f"SENSITIVE_OPERATION | user={user} | operation={operation}{details_str}"}, ensure_ascii=False))


# 全局审计日志实例
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """获取全局审计日志记录器（单例）"""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


# ─────────────────────────────────────────────────
# 安全保护机制
# ─────────────────────────────────────────────────

class AgentTimeoutException(Exception):
    """Agent 操作超时异常"""
    pass


class AgentLoopException(Exception):
    """Agent 循环检测异常"""
    pass


class AgentStateStuckException(Exception):
    """Agent 状态卡死异常"""
    pass


class AgentSafetyMonitor:
    """
    Agent 安全监控器

    防止死循环、状态卡死等异常情况
    """

    def __init__(
        self,
        max_iterations_per_minute: int = 100,
        state_stuck_threshold_seconds: int = 10,
    ):
        """
        初始化安全监控器

        Args:
            max_iterations_per_minute: 每分钟最大迭代次数
            state_stuck_threshold_seconds: 状态卡死阈值（秒）
        """
        self._lock = threading.Lock()
        self._iteration_count = {}
        self._last_state = {}
        self._state_change_time = {}

        self.max_iterations_per_minute = max_iterations_per_minute
        self.state_stuck_threshold = state_stuck_threshold_seconds

        self.logger = logging.getLogger("agent.safety")
        self.logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.__init__.log", "duration_ms": 0, "message": "安全监控器已初始化"}, ensure_ascii=False))

    def record_iteration(self, identifier: str) -> bool:
        """
        记录一次迭代，检查是否异常

        Args:
            identifier: 任务标识符

        Returns:
            是否正常（未检测到异常）
        """
        with self._lock:
            current_time = datetime.now()

            if identifier not in self._iteration_count:
                self._iteration_count[identifier] = {
                    'total': 0,
                    'window_start': current_time,
                    'window_count': 0,
                }

            record = self._iteration_count[identifier]
            time_diff = (current_time - record['window_start']).total_seconds()

            # 每分钟重置窗口计数器
            if time_diff >= 60:
                record['window_start'] = current_time
                record['window_count'] = 0
            else:
                record['window_count'] += 1

                # 检测快速循环
                if record['window_count'] > self.max_iterations_per_minute:
                    self.logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.record_iteration.identifier", "duration_ms": 0, "message": f"⚠️ 检测到快速循环: {identifier}, "
                        f"1分钟内迭代 {record['window_count']} 次"}, ensure_ascii=False))
                    return False

            record['total'] += 1
            return True

    def check_state(self, identifier: str, state: str) -> bool:
        """
        检查状态变化，检测是否卡死

        Args:
            identifier: 任务标识符
            state: 当前状态

        Returns:
            是否正常（未检测到卡死）
        """
        with self._lock:
            current_time = datetime.now()

            if identifier not in self._last_state:
                self._last_state[identifier] = state
                self._state_change_time[identifier] = current_time
                return True

            old_state = self._last_state[identifier]

            if old_state == state:
                # 状态未变化，检查卡死时间
                stuck_time = (
                    current_time - self._state_change_time[identifier]
                ).total_seconds()

                if stuck_time > self.state_stuck_threshold:
                    self.logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.check_state.identifier", "duration_ms": 0, "message": f"⚠️ 检测到状态卡死: {identifier}, "
                        f"状态 '{state}' 保持 {stuck_time:.1f} 秒"}, ensure_ascii=False))
                    return False
            else:
                # 状态变化了，更新记录
                self._last_state[identifier] = state
                self._state_change_time[identifier] = current_time

            return True

    def reset(self, identifier: str = None):
        """重置监控数据"""
        with self._lock:
            if identifier:
                self._iteration_count.pop(identifier, None)
                self._last_state.pop(identifier, None)
                self._state_change_time.pop(identifier, None)
            else:
                self._iteration_count.clear()
                self._last_state.clear()
                self._state_change_time.clear()

    def get_stats(self) -> dict:
        """获取监控统计"""
        with self._lock:
            return {
                'tracked_identifiers': len(self._iteration_count),
                'max_iterations_per_minute': self.max_iterations_per_minute,
                'state_stuck_threshold': self.state_stuck_threshold,
            }


# 全局安全监控器实例
_safety_monitor: Optional[AgentSafetyMonitor] = None


def get_safety_monitor() -> AgentSafetyMonitor:
    """获取全局安全监控器（单例）"""
    global _safety_monitor
    if _safety_monitor is None:
        _safety_monitor = AgentSafetyMonitor()
    return _safety_monitor


# ─────────────────────────────────────────────────
# 安全执行包装器
# ─────────────────────────────────────────────────

def safe_execute(
    func: Callable,
    timeout: float = 30.0,
    default_return: Any = None,
    identifier: str = None,
) -> Any:
    """
    带超时保护的函数执行包装器

    Args:
        func: 要执行的函数
        timeout: 超时时间（秒）
        default_return: 超时时的默认返回值
        identifier: 任务标识符（用于监控）

    Returns:
        函数返回值或默认值

    Example:
        >>> def my_task():
        ...     return "完成"
        >>> result = safe_execute(my_task, timeout=10)
        >>> print(result)
        完成
    """
    logger = logging.getLogger("agent.safety.safe_execute")

    # 检查安全监控
    monitor = get_safety_monitor()
    task_id = identifier or f"task_{datetime.now().timestamp()}"

    if not monitor.record_iteration(task_id):
        logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.safe_execute.task_id", "duration_ms": 0, "message": f"⚠️ 安全监控拒绝执行: {task_id}"}, ensure_ascii=False))
        return default_return

    # 使用线程执行，实现超时保护
    result_container = {'value': None, 'exception': None}

    def target():
        try:
            result_container['value'] = func()
        except Exception as e:
            result_container['exception'] = e
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.target.log", "duration_ms": 0, "message": f"执行异常: {e}"}, ensure_ascii=False))

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.safe_execute.timeout", "duration_ms": 0, "message": f"⏱️ 执行超时（{timeout}秒）: {task_id}"}, ensure_ascii=False))
        return default_return

    if result_container['exception']:
        raise result_container['exception']

    return result_container['value']


def safe_execute_async(
    func: Callable,
    timeout: float = 30.0,
    identifier: str = None,
) -> tuple[Any, Optional[Exception]]:
    """
    带超时保护的异步函数执行（返回异常）

    Args:
        func: 要执行的异步函数
        timeout: 超时时间（秒）
        identifier: 任务标识符

    Returns:
        (结果, 异常对象如果有)

    Example:
        >>> async def my_task():
        ...     return "完成"
        >>> result, error = safe_execute_async(my_task, timeout=10)
        >>> if error:
        ...     print(f"错误: {error}")
        >>> else:
        ...     print(f"结果: {result}")
    """
    import asyncio
    logger = logging.getLogger("agent.safety.safe_execute_async")

    task_id = identifier or f"async_{datetime.now().timestamp()}"

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    result_container = {'value': None, 'exception': None}

    async def async_target():
        try:
            result_container['value'] = await func()
        except Exception as e:
            result_container['exception'] = e

    future = asyncio.ensure_future(async_target())

    try:
        loop.run_until_complete(asyncio.wait_for(future, timeout=timeout))
    except asyncio.TimeoutError:
        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.safe_execute_async.timeout", "duration_ms": 0, "message": f"⏱️ 异步执行超时（{timeout}秒）: {task_id}"}, ensure_ascii=False))
        future.cancel()
        return None, AgentTimeoutException(f"执行超时（{timeout}秒）")
    except Exception as e:
        logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "logging_utils", "action": "logging_utils.safe_execute_async.log", "duration_ms": 0, "message": f"异步执行异常: {e}"}, ensure_ascii=False))
        return None, e

    if result_container['exception']:
        return None, result_container['exception']

    return result_container['value'], None


# ─────────────────────────────────────────────────
# 导出
# ─────────────────────────────────────────────────

__all__ = [
    'setup_agent_logging',
    'setup_error_logging',
    'LogRotationConfig',
    'create_rotating_file_handler',
    'AgentSafetyMonitor',
    'get_safety_monitor',
    'safe_execute',
    'safe_execute_async',
    'AgentTimeoutException',
    'AgentLoopException',
    'AgentStateStuckException',
    'SensitiveDataFilter',
    'AuditLogger',
    'get_audit_logger',
    'log_dict',
    'DictToJsonFilter',
]
