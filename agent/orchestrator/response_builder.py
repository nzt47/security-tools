"""ResponseBuilder — 统一响应构建

提供静态工厂方法构建统一的 Response 格式。
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional, List


@dataclass
class Response:
    """统一响应数据类"""
    success: bool = True
    data: Any = None
    error: Optional[str] = None
    msg: str = ""
    metadata: Dict = field(default_factory=dict)


class ResponseBuilder:
    """响应构建器（静态工厂）"""

    @staticmethod
    def success(data: Any = None, msg: str = "ok") -> Response:
        return Response(success=True, data=data, msg=msg)

    @staticmethod
    def error(error: str = "", msg: str = "error") -> Response:
        return Response(success=False, error=error, msg=msg)

    @staticmethod
    def rejection(reason: str = "") -> Response:
        return Response(success=False, error=reason, msg="rejected")

    @staticmethod
    def guard_blocked(reason: str = "", pattern: str = "") -> Response:
        return Response(
            success=False,
            error=f"输入被安全护栏拦截: {reason}",
            msg="blocked_by_guard",
            metadata={"matched_pattern": pattern},
        )

    @staticmethod
    def workflow_result(result: Any = None) -> Response:
        return Response(success=True, data=result, msg="handled_by_workflow")

    @staticmethod
    def llm_result(text: str = "", model: str = "") -> Response:
        return Response(
            success=True,
            data={"text": text, "model": model},
            msg="llm_response",
        )

    @staticmethod
    def offline(reason: str = "离线模式") -> Response:
        return Response(
            success=True,
            data={"text": f"当前处于离线模式: {reason}"},
            msg="offline",
        )

    @staticmethod
    def from_exception(exc: Exception, msg: str = "") -> Response:
        return Response(
            success=False,
            error=str(exc),
            msg=msg or "internal_error",
        )


# ── 快捷方法：将 Response 转换为 dict ────────────────────────────────
def _response_to_dict(r: Response) -> Dict:
    d = asdict(r)
    if r.error is None:
        d.pop("error", None)
    return d


Response.to_dict = lambda self: _response_to_dict(self)  # type: ignore
