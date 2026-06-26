"""
Prompt 与 Skill 版本化管理模块

功能：
- 提示词版本控制（支持版本历史、回滚）
- 回归测试机制，禁止生产环境徒手修改
- 版本对比和影响分析功能
- 结构化日志输出（包含 trace_id、module_name、action、duration_ms）
"""

from .storage import PromptStorage, PromptRecord, VersionRecord
from .version_control import VersionManager, VersionStatus
from .registry import PromptRegistry, PromptMetadata

__all__ = [
    'PromptStorage',
    'PromptRecord',
    'VersionRecord',
    'VersionManager',
    'VersionStatus',
    'PromptRegistry',
    'PromptMetadata',
]