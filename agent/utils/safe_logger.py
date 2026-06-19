import logging
import sys

# ── 从 logging_utils 导入 emoji 映射，消除重复 ──
from agent.logging_utils import _EMOJI_MAP

def safe_log_message(message):
    if not isinstance(message, str):
        return message

    for emoji, replacement in _EMOJI_MAP.items():
        message = message.replace(emoji, replacement)

    return message

class SafeLogger:
    def __init__(self, name):
        self._logger = logging.getLogger(name)
    
    def debug(self, msg, *args, **kwargs):
        msg = safe_log_message(msg)
        self._logger.debug(msg, *args, **kwargs)
    
    def info(self, msg, *args, **kwargs):
        msg = safe_log_message(msg)
        self._logger.info(msg, *args, **kwargs)
    
    def warning(self, msg, *args, **kwargs):
        msg = safe_log_message(msg)
        self._logger.warning(msg, *args, **kwargs)
    
    def error(self, msg, *args, **kwargs):
        msg = safe_log_message(msg)
        self._logger.error(msg, *args, **kwargs)
    
    def critical(self, msg, *args, **kwargs):
        msg = safe_log_message(msg)
        self._logger.critical(msg, *args, **kwargs)
    
    def exception(self, msg, *args, **kwargs):
        msg = safe_log_message(msg)
        self._logger.exception(msg, *args, **kwargs)

def get_safe_logger(name):
    return SafeLogger(name)

def fix_logging_encoding():
    for handler in logging.root.handlers:
        if hasattr(handler, 'stream'):
            stream = handler.stream
            if hasattr(stream, 'encoding') and stream.encoding in ('gbk', 'gb2312', 'cp936'):
                original_write = stream.write
                def safe_write(msg):
                    msg = safe_log_message(msg)
                    return original_write(msg)
                stream.write = safe_write
