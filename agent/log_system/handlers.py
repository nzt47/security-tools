
import logging
import json
import uuid

logger = logging.getLogger(__name__)


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]

"""日志处理器工厂与配置"""
import os
import sys
import logging
import json
import uuid
import logging.handlers
from typing import Optional

from agent.log_system.formatter import LogRotationConfig
from agent.log_system.emoji_map import EmojiFilter
from agent.log_system.safe_logger import SensitiveDataFilter


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

    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    if config.use_timed_rotation:
        handler = logging.handlers.TimedRotatingFileHandler(
            filename=log_file,
            when=config.when,
            interval=config.interval,
            backupCount=config.backup_count,
            encoding=config.encoding,
            utc=config.utc,
        )
    else:
        handler = logging.handlers.RotatingFileHandler(
            filename=log_file,
            maxBytes=config.max_bytes,
            backupCount=config.backup_count,
            encoding=config.encoding,
        )

    if formatter:
        handler.setFormatter(formatter)

    return handler


def setup_agent_logging(
    debug_mode: bool = False,
    log_file: Optional[str] = None,
    rotation_config: LogRotationConfig = None,
    enable_console: bool = True,
    enable_file: bool = False,
) -> logging.Logger:
    """
    配置 Agent 模块的日志系统

    Args:
        debug_mode: 是否启用调试模式
        log_file: 日志文件路径
        rotation_config: 日志轮转配置
        enable_console: 是否启用控制台输出
        enable_file: 是否启用文件输出

    Returns:
        主日志记录器
    """
    log_format = "%(asctime)s [%(levelname)8s] %(name)-25s: %(message)s"
    date_format = "%H:%M:%S"
    formatter = logging.Formatter(log_format, date_format)
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)8s] %(name)-25s %(process)d:%(thread)d: %(message)s",
        "%Y-%m-%d %H:%M:%S"
    )
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    root_logger.setLevel(logging.DEBUG if debug_mode else logging.INFO)

    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.DEBUG if debug_mode else logging.INFO)
        console_handler.addFilter(SensitiveDataFilter())
        console_handler.addFilter(EmojiFilter())
        root_logger.addHandler(console_handler)

    if enable_file and log_file:
        if rotation_config is None:
            rotation_config = LogRotationConfig()
        file_handler = create_rotating_file_handler(log_file, rotation_config, file_formatter)
        file_handler.setLevel(logging.DEBUG if debug_mode else logging.INFO)
        file_handler.addFilter(SensitiveDataFilter())
        file_handler.addFilter(EmojiFilter())
        root_logger.addHandler(file_handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    for module in [
        "agent.digital_life", "agent.behavior_controller", "agent.permission_system",
        "agent.safety_guard", "agent.system_tools", "agent.tools", "agent.planning",
    ]:
        logging.getLogger(module).setLevel(logging.DEBUG if debug_mode else logging.INFO)

    for module in [
        "planning.core", "planning.decomposer", "planning.executor",
        "planning.reflector", "planning.state_machine", "planning.react", "planning.models",
    ]:
        logging.getLogger(module).setLevel(logging.DEBUG if debug_mode else logging.INFO)

    logger = logging.getLogger("云枢.agent")
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "handlers", "action": "log", "msg": "=" * 70}, ensure_ascii=False))
    logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "handlers", "action": "agent", "msg": "Agent 模块日志系统已初始化"}, ensure_ascii=False))
    return logger


def setup_error_logging(
    log_file: str = "./logs/errors.log",
    rotation_config: LogRotationConfig = None,
) -> logging.Logger:
    """配置错误日志专用记录器"""
    if rotation_config is None:
        rotation_config = LogRotationConfig(max_bytes=20 * 1024 * 1024, backup_count=10)
    logger = logging.getLogger("agent.errors")
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    file_handler = create_rotating_file_handler(
        log_file, rotation_config,
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


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "handlers",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
