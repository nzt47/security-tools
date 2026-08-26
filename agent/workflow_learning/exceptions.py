"""工作流学习系统业务异常"""

from __future__ import annotations
from typing import Optional, Dict, Any


class ErrorCode:
    """业务错误码"""
    INTERNAL_ERROR = "WF_INTERNAL_ERROR"
    VALIDATION_ERROR = "WF_VALIDATION_ERROR"
    NOT_FOUND = "WF_NOT_FOUND"
    ALREADY_EXISTS = "WF_ALREADY_EXISTS"
    LEARN_FAILED = "WF_LEARN_FAILED"
    GENERATE_FAILED = "WF_GENERATE_FAILED"
    EXECUTE_FAILED = "WF_EXECUTE_FAILED"
    NO_MATCH = "WF_NO_MATCH"
    SCHEMA_ERROR = "WF_SCHEMA_ERROR"


class WorkflowLearningError(Exception):
    """工作流学习系统基础异常"""

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


class WorkflowNotFoundError(WorkflowLearningError):
    code = ErrorCode.NOT_FOUND

    def __init__(self, wf_id: str):
        super().__init__(
            f"工作流不存在: {wf_id}",
            code=ErrorCode.NOT_FOUND,
            details={"workflow_id": wf_id},
        )


class WorkflowExecutionError(WorkflowLearningError):
    code = ErrorCode.EXECUTE_FAILED


class WorkflowSchemaError(WorkflowLearningError):
    """黑板写入 schema 校验失败

    由 SharedBlackboard.write 在校验失败时抛出,
    executor 边界捕获 (转 result.success=False + 黑板 record_failure)。
    """

    def __init__(self, step_id: str, key: str, reason: str,
                 *, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"黑板写入 schema 校验失败: step={step_id} key={key} ({reason})",
            code=ErrorCode.SCHEMA_ERROR,
            details={
                "step_id": step_id,
                "key": key,
                "reason": reason,
                **(details or {}),
            },
        )
