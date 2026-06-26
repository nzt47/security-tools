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
