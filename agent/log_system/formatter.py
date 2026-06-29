
import logging
import json
import uuid

logger = logging.getLogger(__name__)


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]

"""日志格式化配置"""
from typing import Dict, Any

class LogRotationConfig:
    """日志轮转配置类"""
    def __init__(
        self,
        max_bytes: int = 50 * 1024 * 1024,
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
        return {k: v for k, v in self.__dict__.items()}


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
            "module_name": "formatter",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
