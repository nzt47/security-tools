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
