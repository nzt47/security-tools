
import logging
import json
import uuid

logger = logging.getLogger(__name__)


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]

"""敏感信息过滤器 — SensitiveDataFilter（向后兼容层）

核心功能已迁移至 agent.utils.sensitive_data_filter，
本模块提供向后兼容的导入接口。
"""

from agent.utils.sensitive_data_filter import (
    SensitiveDataFilter,
    SensitiveLevel,
    SensitiveMatch,
    FilterResult,
)

from agent.utils.sensitive_data_filter import mask_ip as _mask_ip

from typing import Any, Optional, Tuple


class SensitiveDataFilterCompatibility(SensitiveDataFilter):
    """内存模块专用的敏感数据过滤器（向后兼容）

    提供 memory/filter.py 中原有的 API 兼容性。
    """

    def __init__(
        self,
        custom_patterns: Optional[dict[str, Any]] = None,
        block_critical: bool = True,
        block_high: bool = True,
    ) -> None:
        super().__init__(
            custom_content_patterns=custom_patterns,
            block_critical=block_critical,
            block_high=block_high,
        )

    def check(self, content: Any, path: str = "") -> FilterResult:
        """检查内容是否包含敏感信息（向后兼容别名）"""
        return self.detect(content, path)

    def check_and_sanitize(self, content: Any) -> Tuple[bool, Any, list[SensitiveMatch]]:
        """检查并返回脱敏内容（向后兼容别名）"""
        return self.detect_and_sanitize(content)

    @property
    def BUILT_IN_PATTERNS(self) -> dict[str, Any]:
        """获取内置模式（向后兼容）"""
        return self._content_patterns


SensitiveDataFilter = SensitiveDataFilterCompatibility

__all__ = [
    'SensitiveLevel',
    'SensitiveMatch',
    'FilterResult',
    'SensitiveDataFilter',
]


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
            "module_name": "filter",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
