"""技能管理系统业务异常

设计原则:
    - 边界显性化 — 所有可能失败的分支抛出带业务错误码的 Error，
      而非静默返回 None/null。
    - 错误码命名遵循 SKILL_<域>_<具体错误>，便于前端处理与日志检索。
"""

from __future__ import annotations
from typing import Optional, Dict, Any

import logging
import json
import uuid

logger = logging.getLogger(__name__)


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


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

    # 文件系统 (5xxx) — 三层架构
    MD_NO_FRONTMATTER = "SKILL_MD_NO_FRONTMATTER"
    MD_YAML_ERROR = "SKILL_MD_YAML_ERROR"
    MD_READ_ERROR = "SKILL_MD_READ_ERROR"
    PATH_TRAVERSAL = "SKILL_PATH_TRAVERSAL"
    INVALID_SKILL_ID = "SKILL_INVALID_SKILL_ID"
    INVALID_SCRIPT_NAME = "SKILL_INVALID_SCRIPT_NAME"
    INVALID_FILENAME = "SKILL_INVALID_FILENAME"

    # 脚本执行 (6xxx) — 第三层
    SCRIPT_EXEC_TIMEOUT = "SKILL_SCRIPT_EXEC_TIMEOUT"
    SCRIPT_EXEC_FAILED = "SKILL_SCRIPT_EXEC_FAILED"
    SCRIPT_EXEC_BLOCKED = "SKILL_SCRIPT_EXEC_BLOCKED"
    SCRIPT_NOT_FOUND = "SKILL_SCRIPT_NOT_FOUND"

    # MCP 协议 (7xxx)
    MCP_SDK_UNAVAILABLE = "SKILL_MCP_SDK_UNAVAILABLE"
    MCP_SERVER_UNREACHABLE = "SKILL_MCP_SERVER_UNREACHABLE"
    MCP_PROTOCOL_ERROR = "SKILL_MCP_PROTOCOL_ERROR"
    MCP_TOOL_NOT_FOUND = "SKILL_MCP_TOOL_NOT_FOUND"

    # 输出校验 (8xxx) — 后置验证门控
    OUTPUT_SCHEMA_INVALID = "SKILL_OUTPUT_SCHEMA_INVALID"
    OUTPUT_VALIDATION_FAILED = "SKILL_OUTPUT_VALIDATION_FAILED"


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

    def __init__(self, message: str, *,
                 fields: Optional[Dict[str, Any]] = None,
                 code: Optional[str] = None):
        use_code = code or ErrorCode.VALIDATION_ERROR
        super().__init__(message, code=use_code, details=fields or {})


class SkillReviewError(SkillMgmtError):
    code = ErrorCode.REVIEW_REJECTED


class SkillInstallError(SkillMgmtError):
    code = ErrorCode.INSTALL_FAILED


class SkillSecurityError(SkillMgmtError):
    code = ErrorCode.REVIEW_SECURITY_RISK

    def __init__(self, message: str, *, findings: Optional[list] = None):
        super().__init__(message, code=ErrorCode.REVIEW_SECURITY_RISK,
                         details={"findings": findings or []})


class SkillFileError(SkillMgmtError):
    """文件系统层异常 — skill.md 解析/读写/路径越界等"""
    pass


class SkillExecutionError(SkillMgmtError):
    """脚本执行层异常 — 超时/失败/安全阻止"""

    def __init__(self, message: str, *, code: Optional[str] = None,
                 skill_id: Optional[str] = None,
                 script_name: Optional[str] = None,
                 exit_code: Optional[int] = None,
                 stderr: Optional[str] = None,
                 duration_ms: Optional[float] = None):
        details: Dict[str, Any] = {}
        if skill_id:
            details["skill_id"] = skill_id
        if script_name:
            details["script_name"] = script_name
        if exit_code is not None:
            details["exit_code"] = exit_code
        if stderr:
            details["stderr"] = stderr[-500:]  # 截断，避免日志过大
        if duration_ms is not None:
            details["duration_ms"] = duration_ms
        use_code = code or ErrorCode.SCRIPT_EXEC_FAILED
        super().__init__(message, code=use_code, details=details)


class SkillMcpError(SkillMgmtError):
    """MCP 协议层异常 — SDK 缺失/server 不可达/协议错误"""
    code = ErrorCode.MCP_PROTOCOL_ERROR


class SkillOutputValidationError(SkillMgmtError):
    """输出 schema 校验失败异常"""

    def __init__(self, message: str, *, skill_id: str = "",
                 validation_errors: Optional[list] = None):
        super().__init__(
            message,
            code=ErrorCode.OUTPUT_VALIDATION_FAILED,
            details={"skill_id": skill_id,
                     "validation_errors": validation_errors or []},
        )


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
            "module_name": "exceptions",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
