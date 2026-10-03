"""统一响应信封 + RFC 9457 错误模型（重构计划 阶段 2 / R3 · 审计 H-3）。

【现状（2026-10-03 审计实测）】
  · 后端**成功**结构 ≥ 5 种：裸对象、裸数组、{ok,data}、{code,data,message}、{data:...}；
  · 后端**错误**结构 ≥ 6 种：{"error": str}、{"ok": false, "error": ...}、
    {"code": 非200, "message": ...}、纯文本、HTML 回落、以及 401 的 {"error": ...}；
  · **0 个 @app.errorhandler** ⇒ 404/405/500 一律回落 Flask 的 HTML 页面。
    前端 `utils/request.ts` 要求 `code === 200`；`pages/hub/components/ui.tsx` 的
    hubGet/hubPost 则用 `pickObj()` **启发式猜信封** —— 即"客户端不知道后端长什么样"。

【本模块做什么】
  ① 给出**唯一**的 `ok()` / `problem()` 实现（错误用 RFC 9457 子集）；
  ② `install_error_handlers(app)` 注册全局 errorhandler，消灭 HTML 回落（A6 / K3）。

【为什么错误模型选 RFC 9457 而不是自创】RFC 9457（2023 取代 RFC 7807）定义了
`type/title/status/detail/instance` 五字段 + 可扩展成员，是 IETF 标准，
前端与第三方无需读本仓文档就能解析。方案第 7.3 节的建议与审计结论一致。

【为什么成功信封沿用 `{code, data, message}` 而不是另起一套】
  前端 `utils/request.ts`（React 侧）**已经在**按 `code === 200` 解包，
  且 `ApiResponse` 接口是公开的。换成 RFC 风格的成功体是一次**无收益的破坏性变更**；
  真正的问题是"五种结构并存"，不是"code 这个字段不好"。
  故：成功侧**收敛到既有那一种**（不改前端契约），错误侧**收敛到 RFC 9457**（新标准）。

【/api 之外为什么不改】本模块的 errorhandler 只接管 `/api/` 前缀：
  · 浏览器取一个不存在的页面/静态资源，收到 HTML 404 才是正确语义；
  · 把页面 404 也改成 problem+json 会让静态资源缺失更难排查，且没有任何消费方需要它。
  K3（"错误结构种数 → 1"）针对的是**API 面**，前端客户端也只解析 API 面。

【双写与回退】每个响应带 `X-Envelope: v2` 头：
  · 让前端可以**显式**判断"这个响应我认得"，从而逐步删掉 pickObj 启发式；
  · 出问题时可用 `YUNSHU_RFC9457_ERRORS=0` 整段关闭 errorhandler，回到 HTML 回落。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional

from flask import jsonify, request
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)

#: 信封版本头：客户端据此**显式**判断响应形态，而不必猜。
ENVELOPE_HEADER = "X-Envelope"
ENVELOPE_VERSION = "v2"

#: RFC 9457 要求问题详情用 application/problem+json。
PROBLEM_CONTENT_TYPE = "application/problem+json"

#: type 的 URI 前缀。RFC 9457 只要求是个 URI，不要求可解析；
#: 用本服务的保留域名而非 example.com，便于将来真要托管说明页时不用改客户端。
PROBLEM_TYPE_BASE = "https://yunshu.local/problems/"

#: 状态码 → (type slug, 人读 title)。title 面向**人**，detail 面向**本次请求**。
_PROBLEM_TABLE: Dict[int, tuple] = {
    400: ("bad-request", "请求无效"),
    401: ("unauthorized", "未授权"),
    403: ("forbidden", "没有权限"),
    404: ("not-found", "资源不存在"),
    405: ("method-not-allowed", "方法不被允许"),
    409: ("conflict", "状态冲突"),
    413: ("payload-too-large", "请求体过大"),
    415: ("unsupported-media-type", "不支持的媒体类型"),
    422: ("unprocessable-entity", "请求无法处理"),
    429: ("too-many-requests", "请求过于频繁"),
    500: ("internal-error", "服务器内部错误"),
    502: ("bad-gateway", "上游响应无效"),
    503: ("service-unavailable", "服务暂不可用"),
    504: ("gateway-timeout", "上游超时"),
}


#: 状态码 → **面向调用方**的默认 detail。
#: 【为什么需要它】Werkzeug 的 `exc.description` 是英文（如
#: "The requested URL was not found on the server..."）。实测下来 404/405 的 detail
#: 会变成一句英文，而 title 是中文 —— 同一个响应体里两种语言，
#: 前端若要展示 detail 就得自己再翻译一遍。故给出中文默认值；
#: **只有当路由自定义了 description（与 Werkzeug 默认不同）时才用它** ——
#: 那说明作者有意提供了更具体的信息，不该被默认文案盖掉。
_DEFAULT_DETAIL: Dict[int, str] = {
    400: "请求参数无效，请检查后重试。",
    401: "缺少或无效的 API 令牌。",
    403: "当前身份没有访问该资源的权限。",
    404: "请求的资源不存在。",
    405: "该端点不支持此 HTTP 方法。",
    409: "当前状态与该操作冲突。",
    413: "请求体过大。",
    415: "不支持的请求内容类型。",
    422: "请求无法处理（语义校验未通过）。",
    429: "请求过于频繁，请稍后重试。",
    500: "服务器内部错误，请稍后重试或查看服务端日志。",
    502: "上游响应无效。",
    503: "服务暂不可用，请稍后重试。",
    504: "上游服务响应超时。",
}


def _fallback_slug(status: int) -> tuple:
    return ("http-" + str(status), "HTTP " + str(status))


def ok(data: Any = None, *, message: str = "", status: int = 200,
       meta: Optional[Dict[str, Any]] = None):
    """成功响应（**唯一**入口）。

    Args:
        data: 业务数据（原样放在 `data` 键下）
        message: 人读提示（前端仅在 `code != 200` 时展示，保留以兼容既有调用点）
        status: HTTP 状态码，默认 200
        meta: 可选元信息（分页/计数等），放在 `meta` 键下；
              **不放进 data**，避免与业务字段撞名（本仓已有过 DTO 撞名的先例）。
    """
    payload: Dict[str, Any] = {"code": status, "data": data, "message": message}
    if meta:
        payload["meta"] = meta
    resp = jsonify(payload)
    resp.status_code = status
    resp.headers[ENVELOPE_HEADER] = ENVELOPE_VERSION
    return resp


def problem(status: int, *, title: Optional[str] = None, detail: str = "",
            type_slug: Optional[str] = None, instance: Optional[str] = None,
            errors: Optional[Iterable[Any]] = None,
            headers: Optional[Dict[str, str]] = None):
    """错误响应（**唯一**入口，RFC 9457 子集）。

    字段：
        type     URI，指向问题类型的说明（本实现为 `.../problems/<slug>`）
        title    问题类型的**人读**简述（同类问题恒定，不含本次请求细节）
        status   HTTP 状态码（**与响应状态码一致**，RFC 9457 的要求）
        detail   本次请求的**具体**说明
        instance 本次问题的实例标识（默认取请求路径）
        errors   可选：字段级错误数组（校验失败时用；RFC 9457 的扩展成员）

    【为什么 title 与 detail 必须分开】title 可以进入前端文案表被翻译与复用；
    detail 含本次请求信息（哪个字段、哪个 id），只适合展示或写日志。
    合成一个字段是本仓 6 种错误结构里最常见的形态，也是前端最难统一处理的形态。
    """
    slug, default_title = _PROBLEM_TABLE.get(status) or _fallback_slug(status)
    payload: Dict[str, Any] = {
        "type": PROBLEM_TYPE_BASE + (type_slug or slug),
        "title": title or default_title,
        "status": int(status),
        "detail": detail or _DEFAULT_DETAIL.get(status) or default_title,
        "instance": instance if instance is not None else request.path,
    }
    if errors:
        payload["errors"] = list(errors)

    resp = jsonify(payload)
    resp.status_code = int(status)
    # jsonify 会设成 application/json；RFC 9457 要求 application/problem+json。
    resp.mimetype = PROBLEM_CONTENT_TYPE
    resp.headers[ENVELOPE_HEADER] = ENVELOPE_VERSION
    for k, v in (headers or {}).items():
        resp.headers[k] = v
    return resp


def install_error_handlers(app, *, enabled: Optional[bool] = None) -> Dict[str, Any]:
    """注册全局 errorhandler，消灭 API 面的 HTML 回落。

    返回 `{"installed": bool, "scope": "/api/", "statuses": [...]}`（供启动日志与自检读取）。

    【为什么用 `HTTPException` 一条兜住 404/405/400/415…】
      Flask 允许按具体码注册，但那样"新增一个错误码就漏一个"；
      注册基类 `HTTPException` 后，任何 `abort(409)` 都自动走同一形状 ——
      这正是 K3「错误结构种数 = 1」要的**结构性**保证，而不是逐码补齐。
    【`Exception` 那一条的必要性】没有它，未捕获异常会被 WSGI 服务器兜成 HTML 500
      （waitress 默认），前端拿到的仍然不是 problem+json。
    """
    if enabled is None:
        import os
        enabled = str(os.environ.get("YUNSHU_RFC9457_ERRORS", "1")).strip().lower() \
            not in ("0", "false", "no", "off")
    if not enabled:
        logger.info("[Envelope] RFC 9457 错误模型已由 YUNSHU_RFC9457_ERRORS 关闭（回落 HTML）")
        return {"installed": False, "scope": "/api/", "statuses": []}

    def _is_api_path() -> bool:
        try:
            return request.path.startswith("/api/")
        except Exception:  # noqa: BLE001 无请求上下文（如离线调用）一律按 API 处理
            return True

    @app.errorhandler(HTTPException)
    def _handle_http_exception(exc: HTTPException):  # noqa: ANN202
        if not _is_api_path():
            # 页面/静态资源：HTML 404 才是浏览器语义，原样交回 Flask。
            return exc.get_response()
        status = int(exc.code or 500)
        # 仅当路由**自定义**过 description 时才采用它（Werkzeug 的英文默认值会被
        # problem() 里的中文默认 detail 取代，避免同一响应体混两种语言）。
        desc = exc.description or ""
        custom = desc if desc and desc != getattr(type(exc), "description", None) else ""
        return problem(status, detail=custom)

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):  # noqa: ANN202
        if isinstance(exc, HTTPException):
            return _handle_http_exception(exc)
        logger.exception("[Envelope] 未捕获异常 path=%s", getattr(request, "path", "?"))
        if not _is_api_path():
            # 保持既有的 HTML 500（页面请求不该因为这次改动改变渲染结果）
            return HTTPException(500).get_response()
        return problem(
            500,
            detail="服务器内部错误，请稍后重试或查看服务端日志。",
        )

    registered = sorted(_PROBLEM_TABLE)
    logger.info(
        "[Envelope] RFC 9457 错误模型已装载（scope=/api/，覆盖 %d 类状态码 + 未捕获异常；"
        "响应头 %s: %s）", len(registered), ENVELOPE_HEADER, ENVELOPE_VERSION)
    return {"installed": True, "scope": "/api/", "statuses": registered}


__all__ = [
    "ENVELOPE_HEADER", "ENVELOPE_VERSION", "PROBLEM_CONTENT_TYPE", "PROBLEM_TYPE_BASE",
    "install_error_handlers", "ok", "problem",
]
