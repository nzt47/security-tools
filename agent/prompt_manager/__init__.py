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
from .roles import PROMPT_ROLES, PromptFragment, ComposedPrompt, compose_fragments

__all__ = [
    'PromptStorage',
    'PromptRecord',
    'VersionRecord',
    'VersionManager',
    'VersionStatus',
    'PromptRegistry',
    'PromptMetadata',
    # 角色（片段"拥有者"）维度 —— 与 prompt_type 正交，见 roles.py 模块 docstring
    'PROMPT_ROLES',
    'PromptFragment',
    'ComposedPrompt',
    'compose_fragments',
]