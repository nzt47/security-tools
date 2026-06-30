"""云枢综合技能管理系统 (Skills Management)

灵感来源: Claude Skills、Code Skills、Trae Skills、OpenClaw、Hermes 等。

四大核心能力:
    1. 技能创建 (creator): AI 辅助生成 / 手动开发 / 多格式安装
    2. 技能发现与管理 (searcher + reviewer): 高级搜索 + 三重审核
       (重复检测 / 安全扫描 / 质量评估)
    3. 技能集成与增强 (enhancer): 版本管理 / 参数优化 / 性能追踪
    4. (与 agent/workflow_learning 协同) 智能工作流学习

公开入口:
    from agent.skills_mgmt import SkillsMgmtService
    svc = SkillsMgmtService()
"""

from .service import SkillsMgmtService
from .models import (
    Skill,
    SkillVersion,
    SkillCategory,
    SkillStatus,
    ReviewResult,
    ReviewStatus,
    SkillMetrics,
    SkillSearchParams,
    SkillSearchResult,
)
from .exceptions import (
    SkillMgmtError,
    SkillNotFoundError,
    SkillAlreadyExistsError,
    SkillValidationError,
    SkillReviewError,
    SkillInstallError,
    SkillSecurityError,
    ErrorCode,
)

__all__ = [
    "SkillsMgmtService",
    "Skill",
    "SkillVersion",
    "SkillCategory",
    "SkillStatus",
    "ReviewResult",
    "ReviewStatus",
    "SkillMetrics",
    "SkillSearchParams",
    "SkillSearchResult",
    "SkillMgmtError",
    "SkillNotFoundError",
    "SkillAlreadyExistsError",
    "SkillValidationError",
    "SkillReviewError",
    "SkillInstallError",
    "SkillSecurityError",
    "ErrorCode",
]

__version__ = "1.0.0"
