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

# 预编译 emoji 匹配正则：用一次 re.sub 代替 N 次循环 str.replace（性能优化 5-10 倍）
_EMOJI_PATTERN = re.compile('|'.join(re.escape(e) for e in _EMOJI_MAP.keys()))



_logger = logging.getLogger(__name__)


def _trace_id():
    """生成 trace_id（结构化日志用）"""
    return uuid.uuid4().hex[:16]


# ─────────────────────────────────────────────────
# 性能埋点缓存（模块级）——避免每次 log_dict 调用的 from import 开销
# ─────────────────────────────────────────────────
# 首次调用 log_dict 时初始化，之后仅一次属性查找（约 0.02us）
_PERF_MODULE_LOADED: Optional[bool] = None
_PERF_IS_ENABLED_FN: Optional[Callable[[], bool]] = None
_PERF_RECORD_CALL_FN: Optional[Callable[..., None]] = None


def _ensure_perf_monitor_loaded() -> None:
    """延迟加载 perf_monitor 模块函数引用到模块级缓存

    机制：幂等性——多次调用安全，仅首次真正执行 import
    边界显性化：导入失败时不抛错，标记为不可用
    """
    global _PERF_MODULE_LOADED, _PERF_IS_ENABLED_FN, _PERF_RECORD_CALL_FN
    if _PERF_MODULE_LOADED is not None:
        return
    try:
        from agent.utils import perf_monitor as _pm
        _PERF_IS_ENABLED_FN = _pm.is_enabled
        _PERF_RECORD_CALL_FN = _pm.record_call
        _PERF_MODULE_LOADED = True
    except Exception:
        _PERF_MODULE_LOADED = False


def log_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    """规范化日志字典并返回，供 logger.X(log_dict({...})) 直接传递 dict

    消除调用方 json.dumps + formatter json.loads 的双重序列化开销。

    性能埋点：启用 AGENT_PERF_LOGGING=1 时，测量新模式（dict 规范化）耗时，
    并实时对比旧模式（json.dumps）耗时，输出详细对比日志。
    机制：Request ID 采样控制（perf_monitor 内部）+ 边界显性化（异常不吞掉）。

    内存优化：模块级缓存 perf check 函数引用，避免每次 from import 开销；
              dict 规范化采用就地板判断模式，减少 setdefault 函数调用开销。
    """
    # 性能埋点快速路径判断（首次加载后仅一次属性查找 + 一次函数调用，约 0.05us）
    if _PERF_MODULE_LOADED is None:
        _ensure_perf_monitor_loaded()
    _perf_on = _PERF_IS_ENABLED_FN() if _PERF_MODULE_LOADED else False

    if not _perf_on:
        # 快速路径：无埋点开销
        # 直接构造 dict 并就地规范化，避免重复 dict 复制
        data = dict(payload)
        if "msg" in data:
            if "message" not in data:
                data["message"] = data.pop("msg")
            else:
                data.pop("msg")
        if "trace_id" not in data:
            data["trace_id"] = _trace_id()
        if "module_name" not in data:
            data["module_name"] = "unknown"
        if "action" not in data:
            data["action"] = "unknown"
        if "duration_ms" not in data:
            data["duration_ms"] = 0
        return data

    # 性能埋点路径：测量新模式耗时，并实时对比旧模式 json.dumps
    import time as _perf_time
    _start = _perf_time.perf_counter()

    data = dict(payload)
    if "msg" in data:
        if "message" not in data:
            data["message"] = data.pop("msg")
        else:
            data.pop("msg")
    if "trace_id" not in data:
        data["trace_id"] = _trace_id()
    if "module_name" not in data:
        data["module_name"] = "unknown"
    if "action" not in data:
        data["action"] = "unknown"
    if "duration_ms" not in data:
        data["duration_ms"] = 0

    _new_us = (_perf_time.perf_counter() - _start) * 1_000_000

    # 对比旧模式：json.dumps（调用方序列化开销）
    _start = _perf_time.perf_counter()
    json.dumps(payload, ensure_ascii=False)
    _old_us = (_perf_time.perf_counter() - _start) * 1_000_000

    if _PERF_RECORD_CALL_FN is not None:
        try:
            _PERF_RECORD_CALL_FN("log_dict", "normalize", _new_us, _old_us)
        except Exception:
            pass

    return data


def _safe_log_message(message):
    """安全处理日志消息，替换 emoji 避免 GBK 编码问题

    性能优化：用预编译正则一次替换所有 emoji，比循环 str.replace 快 5-10 倍。
    快速路径：文本中不含 emoji 字符时直接返回原字符串（避免不必要的字符串操作）。
    """
    if not isinstance(message, str):
        return message

    # 快速路径：若无任何 emoji 命中，直接返回（避免不必要的字符串操作）
    if not _EMOJI_PATTERN.search(message):
        return message

    return _EMOJI_PATTERN.sub(lambda m: _EMOJI_MAP[m.group(0)], message)


def _safe_log_dict(data: Dict) -> Dict:
    """递归处理 dict 中的所有 str 值，替换 emoji 避免 GBK 编码问题

    性能优化：快速路径——若 dict 中无 str 值（常见于纯数值日志），直接返回原 dict 副本。
    """
    # 快速路径：扫描一遍，若无 str/dict/list 嵌套则直接浅拷贝返回
    has_str = False
    for value in data.values():
        if isinstance(value, str):
            has_str = True
            break
        if isinstance(value, (dict, list)):
            has_str = True  # 需要递归，走完整路径
            break

    if not has_str:
        return dict(data)

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

    性能埋点：启用 AGENT_PERF_LOGGING=1 时，对比 dict emoji 处理耗时。
    机制：Request ID 采样控制（perf_monitor 内部）。
    """

    def filter(self, record):
        if record.msg is not None:
            if isinstance(record.msg, dict):
                # 性能埋点（关闭时仅一次布尔判断）
                _perf_on = (
                    _PERF_IS_ENABLED_FN() if _PERF_MODULE_LOADED else False
                )

                if _perf_on:
                    import time as _perf_time
                    _start = _perf_time.perf_counter()
                    record.msg = _safe_log_dict(record.msg)
                    _new_us = (_perf_time.perf_counter() - _start) * 1_000_000
                    # 旧模式等价：对整个 JSON 字符串做一次 emoji 替换
                    _start = _perf_time.perf_counter()
                    _tmp = _safe_log_message(json.dumps(record.msg, ensure_ascii=False))
                    _old_us = (_perf_time.perf_counter() - _start) * 1_000_000
                    if _PERF_RECORD_CALL_FN is not None:
                        try:
                            _PERF_RECORD_CALL_FN("EmojiFilter", "dict_safe", _new_us, _old_us)
                        except Exception:
                            pass
                else:
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

    性能埋点：启用 AGENT_PERF_LOGGING=1 时，记录 dict→JSON 单次序列化耗时，
    对比旧模式（formatter 已 json.loads 后再 json.dumps 的二次序列化）。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, dict):
            # 性能埋点（关闭时仅一次布尔判断）
            _perf_on = (
                _PERF_IS_ENABLED_FN() if _PERF_MODULE_LOADED else False
            )

            if _perf_on:
                import time as _perf_time
                _start = _perf_time.perf_counter()
                record.msg = json.dumps(record.msg, ensure_ascii=False)
                _new_us = (_perf_time.perf_counter() - _start) * 1_000_000
                # 旧模式无等价（旧模式在 formatter 中已 loads+dumps，此处无对比）
                # 仅记录单次序列化耗时，验证"消除二次序列化"
                if _PERF_RECORD_CALL_FN is not None:
                    try:
                        _PERF_RECORD_CALL_FN("DictToJsonFilter", "serialize", _new_us, 0.0)
                    except Exception:
                        pass
            else:
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

    # 生产环境强制输出 DEBUG 日志的关键调试模块
    # 这些模块的 DEBUG 日志在非 debug_mode 下也会输出到控制台/文件，
    # 便于线上排查配置错误。专用 handler 仅放行 DEBUG 级别记录，
    # INFO+ 记录仍由根 logger 的 handler 处理，避免重复输出。
    always_debug_modules = [
        "agent.config_validation",  # 配置校验调试信息（18 条 DEBUG 日志）
    ]
    for module in always_debug_modules:
        module_logger = logging.getLogger(module)
        module_logger.setLevel(logging.DEBUG)

        # 专用 DEBUG handler：仅放行 DEBUG 级别记录
        debug_console = logging.StreamHandler(sys.stdout)
        debug_console.setLevel(logging.DEBUG)
        debug_console.setFormatter(formatter)
        debug_console.addFilter(SensitiveDataFilter())
        debug_console.addFilter(EmojiFilter())
        debug_console.addFilter(
            lambda record: record.levelno == logging.DEBUG
        )
        module_logger.addHandler(debug_console)

        if enable_file and log_file:
            debug_file = create_rotating_file_handler(
                log_file,
                rotation_config or LogRotationConfig(),
                file_formatter,
            )
            debug_file.setLevel(logging.DEBUG)
            debug_file.addFilter(SensitiveDataFilter())
            debug_file.addFilter(EmojiFilter())
            debug_file.addFilter(DictToJsonFilter())
            debug_file.addFilter(
                lambda record: record.levelno == logging.DEBUG
            )
            module_logger.addHandler(debug_file)

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

    性能优化：
    1. _MASK_RULES: 所有正则预编译为类属性，避免每次调用重新编译/查找缓存
    2. _SENSITIVE_FAST_CHECK: 一次 search 判断是否包含敏感特征，跳过 19 次 sub 调用
    """

    # mask() 预编译正则（性能优化：避免每次调用重新编译/查找缓存）
    # 与 agent/utils/sensitive_data_filter.py 的 _MASK_RULES 对齐
    _MASK_RULES = [
        # 1. password/secret/token 字段值（=）
        (re.compile(r'(password|passwd|pwd|secret|token)\s*=\s*["\']?([^"\'&\s]*)["\']?',
                    re.IGNORECASE), r'\1="[REDACTED]"'),
        # 2. password/secret/token 字段值（:）
        (re.compile(r'(password|passwd|pwd|secret|token)\s*:\s*["\']?([^"\'&\s]*)["\']?',
                    re.IGNORECASE), r'\1: "[REDACTED]"'),
        # 3. api_key/secret_key/access_token 字段值（=）
        (re.compile(r'(api_key|api\.key|secret_key|access_token)\s*=\s*["\']?([^"\'&\s]*)["\']?',
                    re.IGNORECASE), r'\1="[REDACTED]"'),
        # 4. api_key/secret_key/access_token 字段值（:）
        (re.compile(r'(api_key|api\.key|secret_key|access_token)\s*:\s*["\']?([^"\'&\s]*)["\']?',
                    re.IGNORECASE), r'\1: "[REDACTED]"'),
        # 5. URL 查询参数中的凭证
        (re.compile(r'([?&])(api_key|key|secret|token)\s*=\s*[^&\s]*', re.IGNORECASE),
         r'\1\2=[REDACTED]'),
        # 6. 独立的 sk- API Key（较长）
        (re.compile(r'\bsk-[a-zA-Z0-9_-]{10,}\b', re.IGNORECASE), '[REDACTED]'),
        # 7. 独立的 pk- API Key
        (re.compile(r'\bpk-[a-zA-Z0-9_-]{10,}\b', re.IGNORECASE), '[REDACTED]'),
        # 8. 通用密钥格式（较长的 base64 字符串）
        (re.compile(r'\b[a-zA-Z0-9+/]{20,}\b', re.IGNORECASE), '[REDACTED]'),
        # 9. JWT Token（以 ey 开头，较长）
        (re.compile(r'\bey[A-Za-z0-9+/=]{30,}\b', re.IGNORECASE), '[REDACTED]'),
        # 10. Bearer Token（P0-SEC-001：整段替换为 Bearer [REDACTED]）
        (re.compile(r'(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*'), 'Bearer [REDACTED]'),
        # 11. 身份证号（18位带 X 结尾）- 保留前 6 位和后 4 位
        (re.compile(r'(\d{6})\d{8}(\d{3}[Xx])'), r'\1********\2'),
        # 12. 身份证号（18位纯数字）
        (re.compile(r'(\d{6})\d{8}(\d{4})'), r'\1********\2'),
        # 13. 身份证号（15位旧版）- 保留前 6 位和后 3 位
        (re.compile(r'(\d{6})\d{6}(\d{3})'), r'\1******\2'),
        # 14. 中国大陆手机号（11位）- 保留前 3 位和后 4 位
        (re.compile(r'(1[3-9]\d)\d{4}(\d{4})'), r'\1****\2'),
        # 15. 带区号的中国大陆手机号
        (re.compile(r'(\+?86)(1[3-9]\d)\d{4}(\d{4})'), r'\1\2****\3'),
        # 16. 香港手机号 - 保留前缀和后 4 位
        (re.compile(r'(\+?852)?([569]\d{3})\d{4}'),
         lambda m: f"{m.group(1) or ''}{m.group(2)}****"),
        # 17. 邮箱地址（完全脱敏，去除 @ 符号避免泄露邮箱特征）
        (re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', re.IGNORECASE),
         '[REDACTED]'),
        # 18. 独立的敏感字符串（API Key、密钥等，短模式）
        (re.compile(r'\bsk-[a-zA-Z0-9_-]{5,}\b', re.IGNORECASE), '[REDACTED]'),
        # 19. 独立的 pk- 短模式
        (re.compile(r'\bpk-[a-zA-Z0-9_-]{5,}\b', re.IGNORECASE), '[REDACTED]'),
    ]

    # 快速检测正则：一次 search 判断是否包含任何敏感特征
    # 含数字、@、:、=、或敏感关键词则需进一步脱敏
    _SENSITIVE_FAST_CHECK = re.compile(
        r'\d|@|:|=|password|token|key|secret|BEGIN|Bearer|sk-|eyJ|AKIA|gh[pousr]_',
        re.IGNORECASE,
    )

    def __init__(self):
        super().__init__()
        # 保留 _patterns 用于向后兼容（其他模块可能引用）
        self._patterns = [p for p, _ in self._MASK_RULES]
    
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

        性能优化：
        1. 所有正则预编译为类属性 _MASK_RULES，避免每次调用重新编译/查找缓存
        2. 快速路径：若文本不含任何敏感特征（数字、@、:、=、关键词），
           一次 search 即返回原文本，跳过 19 次 sub 调用

        Args:
            text: 原始文本

        Returns:
            脱敏后的文本
        """
        if not isinstance(text, str):
            return text

        try:
            # 快速路径：若无任何敏感特征，直接返回（跳过 19 次 sub 调用）
            if not self._SENSITIVE_FAST_CHECK.search(text):
                return text

            # 使用预编译正则，避免每次调用重新编译
            result = text
            for pattern, replacement in self._MASK_RULES:
                result = pattern.sub(replacement, result)

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
            log_path = os.path.join(os.path.dirname(__file__), '..', 'logs', 'audit.log')
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            handler = logging.FileHandler(
                log_path,
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
