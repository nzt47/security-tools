"""技能管理系统业务异常

设计原则:
    - 边界显性化 — 所有可能失败的分支抛出带业务错误码的 Error，
      而非静默返回 None/null。
    - 错误码命名遵循 SKILL_<域>_<具体错误>，便于前端处理与日志检索。
"""

from __future__ import annotations
from typing import Optional, Dict, Any


class ErrorCode:
    """业务错误码常量"""

    # 通用 (1xxx)
    INTERNAL_ERROR = "SKILL_INTERNAL_ERROR"
    VALIDATION_ERROR = "SKILL_VALIDATION_ERROR"
    NOT_FOUND = "SKILL_NOT_FOUND"
    ALREADY_EXISTS = "SKILL_ALREADY_EXISTS"
    PERMISSION_DENIED = "SKILL_PERMISSION_DENIED"

    # 创建/安装 (2xxx)
    CREATE_FAILED = "SKILL_CREATE_FAILED"
    INSTALL_FAILED = "SKILL_INSTALL_FAILED"
    INSTALL_FORMAT_UNSUPPORTED = "SKILL_INSTALL_FORMAT_UNSUPPORTED"
    INSTALL_SOURCE_UNREACHABLE = "SKILL_INSTALL_SOURCE_UNREACHABLE"

    # 审核 (3xxx)
    REVIEW_DUPLICATE = "SKILL_REVIEW_DUPLICATE"
    REVIEW_SECURITY_RISK = "SKILL_REVIEW_SECURITY_RISK"
    REVIEW_QUALITY_LOW = "SKILL_REVIEW_QUALITY_LOW"
    REVIEW_REJECTED = "SKILL_REVIEW_REJECTED"

    # 增强 (4xxx)
    VERSION_CONFLICT = "SKILL_VERSION_CONFLICT"
    PARAM_OPTIMIZE_FAILED = "SKILL_PARAM_OPTIMIZE_FAILED"


class SkillMgmtError(Exception):
    """技能管理系统基础异常

    Attributes:
        code: 业务错误码 (见 ErrorCode)
        message: 人类可读的错误描述
        details: 可选的结构化补充信息
    """

    code: str = ErrorCode.INTERNAL_ERROR

    def __init__(self, message: str, *, code: Optional[str] = None,
                 details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "error": self.message,
            "code": self.code,
            "details": self.details,
        }


class SkillNotFoundError(SkillMgmtError):
    code = ErrorCode.NOT_FOUND

    def __init__(self, skill_id: str):
        super().__init__(
            f"技能不存在: {skill_id}",
            code=ErrorCode.NOT_FOUND,
            details={"skill_id": skill_id},
        )


class SkillAlreadyExistsError(SkillMgmtError):
    code = ErrorCode.ALREADY_EXISTS

    def __init__(self, skill_id: str):
        super().__init__(
            f"技能已存在: {skill_id}",
            code=ErrorCode.ALREADY_EXISTS,
            details={"skill_id": skill_id},
        )


class SkillValidationError(SkillMgmtError):
    code = ErrorCode.VALIDATION_ERROR

    def __init__(self, message: str, *, fields: Optional[Dict[str, Any]] = None):
        super().__init__(message, code=ErrorCode.VALIDATION_ERROR, details=fields or {})


class SkillReviewError(SkillMgmtError):
    code = ErrorCode.REVIEW_REJECTED


class SkillInstallError(SkillMgmtError):
    code = ErrorCode.INSTALL_FAILED


class SkillSecurityError(SkillMgmtError):
    code = ErrorCode.REVIEW_SECURITY_RISK

    def __init__(self, message: str, *, findings: Optional[list] = None):
        super().__init__(message, code=ErrorCode.REVIEW_SECURITY_RISK,
                         details={"findings": findings or []})
