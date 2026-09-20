"""统一错误语义（v1.4 §8.2 的 14 个错误码）—— 能力层的**唯一异常映射点**

## 为什么需要它

能力一旦同时被人（CLI）、被 CI（HTTP）、被模型（tool calling）三条链路调用，
"调用失败"就必须有一个**跨链路一致**的表达：CI 要凭它决定是否阻断构建，
人要看懂它，模型只能在**安全描述**下看到它。此前仓库里每层各自抛各自的
异常（`ToolError` / `SkillMcpError` / `requests` 的 `Timeout` / 裸 `Exception`），
调用方只能 `str(e)`，于是：

1. **CI 无法判定**（没有稳定的机器可读字段）；
2. **异常原文会进入 LLM 上下文**（v1.4 §8.2 明令禁止：可能带本地路径、
   HTML 错误页、上游内网地址 —— 既是噪声也是信息泄漏）。

## 设计取舍

- **不替换既有异常体系**：本模块**不**改 `ToolError` / `SkillMcpError` 的定义，
  只在**出口处**做一次映射（`from_exception()`）。既有 `except ToolError`
  等调用点行为不变（D2）。
- **不新增依赖**：只做纯映射表，无 IO、无网络。
- **未知异常一律 `internal_error`**：宁可说"内部错误"，也不把原文透出去。
- **`to_llm_safe()` 是唯一允许进入 LLM 上下文的形态**：它只输出
  `code` + 固定的人类可读描述；连 `detail` 都要经过 `redact()` 清洗。

参见 `docs/rfc/CapabilitySpec规范.md` 与 `agent/capregistry/contract.py`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

__all__ = [
    "ERROR_CODES",
    "ErrorCodeMeta",
    "CODE_META",
    "OK",
    "RETRYABLE_CODES",
    "LLM_VISIBLE_CODES",
    "CapabilityError",
    "CapabilityResult",
    "from_exception",
    "redact",
    "to_llm_safe",
    "ok_result",
    "err_result",
]

# ── 14 个错误码（v1.4 §8.2 逐条对齐；顺序即文档顺序，勿重排）──
OK = "ok"

ERROR_CODES: tuple = (
    "ok",
    "timeout",
    "denied",
    "schema_error",
    "validation_error",
    "unhealthy",
    "not_found",
    "quota_exceeded",
    "llm_unavailable",
    "upstream_error",
    "cancelled",
    "deadline_exceeded",
    "conflict",
    "internal_error",
)


@dataclass(frozen=True)
class ErrorCodeMeta:
    """单个错误码的语义（`retryable` 供 CI/调用方决定是否重试）"""

    code: str
    retryable: bool
    http_status: int
    #: 给 LLM 看的**固定**描述（不含任何运行时数据，故不可能泄漏）
    llm_hint: str


CODE_META: Dict[str, ErrorCodeMeta] = {
    "ok": ErrorCodeMeta("ok", False, 200, "调用成功"),
    "timeout": ErrorCodeMeta(
        "timeout", True, 504, "能力执行超时，可稍后重试"),
    "denied": ErrorCodeMeta(
        "denied", False, 403,
        "该调用被治理策略拒绝（权限或审批边界），不应原样重试；"
        "如确需执行，请人工在审批收件箱批准后原样重试一次"),
    "schema_error": ErrorCodeMeta(
        "schema_error", False, 400,
        "入参不符合该能力的 JSON Schema，需按契约修正参数后重试"),
    "validation_error": ErrorCodeMeta(
        "validation_error", False, 400, "入参未通过业务校验，需修正参数后重试"),
    "unhealthy": ErrorCodeMeta(
        "unhealthy", False, 503, "该能力当前不可用（连接或健康检查未通过）"),
    "not_found": ErrorCodeMeta(
        "not_found", False, 404, "能力不存在或未注册，请检查能力名"),
    "quota_exceeded": ErrorCodeMeta(
        "quota_exceeded", True, 429, "触发限流或配额上限，需等待后重试"),
    "llm_unavailable": ErrorCodeMeta(
        "llm_unavailable", True, 503, "模型服务不可用；本能力依赖模型，故当前不可用"),
    "upstream_error": ErrorCodeMeta(
        "upstream_error", True, 502, "上游服务返回错误，可稍后重试"),
    "cancelled": ErrorCodeMeta(
        "cancelled", False, 499, "调用已被取消"),
    "deadline_exceeded": ErrorCodeMeta(
        "deadline_exceeded", True, 504, "超过了调用方给定的截止时间"),
    "conflict": ErrorCodeMeta(
        "conflict", False, 409, "与当前状态冲突（如并发修改），需先解决冲突"),
    "internal_error": ErrorCodeMeta(
        "internal_error", False, 500, "内部错误，已脱敏；请查看服务端日志定位"),
}

#: 可重试集合（派生自 CODE_META，勿单独维护）
RETRYABLE_CODES: frozenset = frozenset(
    c for c, m in CODE_META.items() if m.retryable)

#: 允许出现在 LLM 工具结果里的错误码（= 全部：每个码的 `llm_hint` 都是固定串）
LLM_VISIBLE_CODES: frozenset = frozenset(CODE_META)


class CapabilityError(Exception):
    """能力层的统一异常（**不替换**既有异常，只在出口处构造）

    `detail` 是**原始**信息，**禁止**直接给 LLM；`to_llm_safe()` 才会清洗。
    """

    def __init__(self, code: str, message: str = "", *,
                 detail: str = "", capability: str = "",
                 retryable: Optional[bool] = None) -> None:
        code = code if code in CODE_META else "internal_error"
        super().__init__(message or CODE_META[code].llm_hint)
        self.code = code
        self.message = message or CODE_META[code].llm_hint
        self.detail = detail
        self.capability = capability
        self._retryable = retryable

    @property
    def retryable(self) -> bool:
        if self._retryable is not None:
            return bool(self._retryable)
        return CODE_META[self.code].retryable

    @property
    def http_status(self) -> int:
        return CODE_META[self.code].http_status

    def to_llm_safe(self) -> Dict[str, Any]:
        """**唯一**允许进入 LLM 上下文的形态（固定描述 + 脱敏后的短摘要）"""
        return to_llm_safe(self.code, capability=self.capability, detail=self.detail)


# ─────────────────────────────────────────────────────────────
#  脱敏（"异常原文不得喂给 LLM"的实现点）
# ─────────────────────────────────────────────────────────────

#: 需要抹掉的模式：绝对路径 / URL / 邮件 / 长十六进制与疑似密钥
#
# 【不易·为什么不用 `SENSITIVE_DATA_MASKING_BEST_PRACTICES.md` 里的完整规则集】
#   那份规则集面向"日志与上报"，会保留可读性；而 LLM 上下文的要求更严：
#   **宁可信息量为零，也不得泄漏**。故这里用的是更激进的白名单式截断 +
#   模式替换，且结果长度硬上限 200 字符。
_REDACT_PATTERNS: tuple = (
    # 【最先做】HTML 标签整体抹掉：错误页是"最典型的泄漏源 + 最典型的噪声源"，
    # 而且保留标记毫无价值（LLM 不需要看到 `</html>`）。
    (re.compile(r"<!DOCTYPE[^>]*>", re.IGNORECASE), " "),
    (re.compile(r"</?[a-zA-Z][^<>]*>"), " "),
    (re.compile(r"&[a-zA-Z#0-9]{2,8};"), " "),
    # traceback 骨架：`Traceback (most recent call last):` 与 `File "...", line N`
    # 【为什么单独一条】traceback 是**结构化调试信息**，对 LLM 的唯一作用是
    #   教它去猜实现细节（进而产生幻觉式的"修复建议"）；而它携带的路径/行号
    #   正是泄漏面。整个骨架抹掉，只留"发生了什么"的语义码。
    (re.compile(r"Traceback \(most recent call last\):", re.IGNORECASE), " "),
    (re.compile(r'File "[^"]*", line \d+[^\n]*', re.IGNORECASE), " "),
    (re.compile(r"^\s*raise\b.*$", re.MULTILINE), " "),
    # Windows / POSIX 绝对路径（含盘符与 UNC）
    (re.compile(r"[A-Za-z]:\\[^\s\"']*"), "<path>"),
    (re.compile(r"\\\\[^\s\"']+"), "<path>"),
    (re.compile(r"/(?:home|Users|root|var|etc|opt|tmp)/[^\s\"']*"), "<path>"),
    # URL（保留 scheme，抹掉主机与路径——上游内网地址是典型泄漏面）
    (re.compile(r"https?://[^\s\"']+"), "<url>"),
    # URL 的**路径部分**（不带 scheme 的 `url: /api/v1/...` 形态）
    (re.compile(r"/(?:api|v\d|internal|admin|messages|sse|token|oauth)"
                r"(?:/[\w.\-]+)+"), "<urlpath>"),
    # **不带 scheme 的内网主机名**（实测形态：`host='internal.corp.local'`）
    #   【不易】只抹"私域 TLD"，不抹通用 TLD：若把 `[\w.-]+\.(com|cn|net)`
    #   也抹掉，会把误报率推到很高（正常文本里的域名/包名都会被吃）。
    (re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:local|internal|corp|lan|intra|"
                r"localhost|localdomain|test|example)\b", re.IGNORECASE), "<host>"),
    # 邮箱
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    # 长十六进制 / base64 风格串（疑似 token / 摘要 / 密钥）
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "<hex>"),
    (re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"), "<opaque>"),
    # 常见密钥前缀
    (re.compile(r"\b(?:sk|ghp|gho|xox[baprs])-[A-Za-z0-9_\-]{8,}"), "<secret>"),
    # 行号/内存地址这类调试噪声
    (re.compile(r"0x[0-9a-fA-F]{6,}"), "<addr>"),
)

#: 脱敏后的硬上限（LLM 上下文是稀缺资源，且长文本更可能含未命中模式的敏感信息）
_MAX_DETAIL = 200


def redact(text: Any) -> str:
    """把任意文本清洗成"可安全放入 LLM 上下文"的短串

    【简易】任何异常一律**不抛**：脱敏自身失败时返回空串（宁可没有信息，
            也不能把未清洗的原文透出去 —— fail-closed 于本函数）。
    """
    try:
        raw = "" if text is None else str(text)
    except Exception:  # noqa: BLE001  __str__ 自身抛异常
        return ""
    try:
        for pattern, repl in _REDACT_PATTERNS:
            raw = pattern.sub(repl, raw)
        raw = " ".join(raw.split())          # 压平空白（含换行）
        return raw[:_MAX_DETAIL]
    except Exception:  # noqa: BLE001
        return ""


def to_llm_safe(code: str, *, capability: str = "", detail: str = "") -> Dict[str, Any]:
    """构造可进入 LLM 上下文的错误对象

    只含三样东西：**错误码**、**固定描述**、**脱敏后的短摘要**。
    没有 `type(e).__name__`、没有 traceback、没有原始 message。HTML 错误页
    会因 `redact()` 的空白压平与长度截断而失去结构，且 URL 已被抹掉。
    """
    meta = CODE_META.get(code) or CODE_META["internal_error"]
    out: Dict[str, Any] = {"code": meta.code, "message": meta.llm_hint}
    if capability:
        out["capability"] = str(capability)
    safe_detail = redact(detail)
    if safe_detail:
        out["detail"] = safe_detail
    out["retryable"] = meta.retryable
    return out


# ─────────────────────────────────────────────────────────────
#  Python 异常 → 错误码（唯一映射点）
# ─────────────────────────────────────────────────────────────

#: 按**异常类限定名**匹配（不用 `isinstance`，以免 import 一大堆可选依赖）
_BY_QUALNAME: Dict[str, str] = {
    # 标准库超时/取消
    "TimeoutError": "timeout",
    "socket.timeout": "timeout",
    "asyncio.TimeoutError": "timeout",
    "TimeoutExpired": "timeout",
    "CancelledError": "cancelled",
    "KeyboardInterrupt": "cancelled",
    "concurrent.futures.TimeoutError": "deadline_exceeded",
    # 取值/契约
    "ValueError": "validation_error",
    "TypeError": "validation_error",
    "KeyError": "validation_error",
    "jsonschema.ValidationError": "schema_error",
    "jsonschema.exceptions.ValidationError": "schema_error",
    # 权限/治理
    "PermissionError": "denied",
    "PermissionDenied": "denied",
    "ToolError": "internal_error",       # 见下方 `_tool_error_code()` 覆盖
    # 找不到
    "FileNotFoundError": "not_found",
    "ModuleNotFoundError": "not_found",
    "KeyNotFound": "not_found",
    # 冲突
    "FileExistsError": "conflict",
    "CardConflictError": "conflict",
    # 上游 / 网络
    "ConnectionError": "upstream_error",
    "ConnectionResetError": "upstream_error",
    "ConnectionRefusedError": "upstream_error",
    "requests.exceptions.ConnectionError": "upstream_error",
    "requests.exceptions.Timeout": "timeout",
    "requests.exceptions.RequestException": "upstream_error",
    "CircuitBreakerError": "unhealthy",
    "HTTPError": "upstream_error",
    "SkillMcpError": "upstream_error",
}

#: 关键词兜底（异常类名/消息里出现这些词时如何归码）
_KEYWORD_RULES: tuple = (
    ("ratelimit", "quota_exceeded"),
    ("rate limit", "quota_exceeded"),
    ("调用频率过高", "quota_exceeded"),
    ("quota", "quota_exceeded"),
    ("throttl", "quota_exceeded"),
    ("approval_required", "denied"),
    ("approval_rejected", "denied"),
    ("permission_denied", "denied"),
    ("未在本服务端暴露", "not_found"),
    ("未知工具", "not_found"),
    ("not found", "not_found"),
    ("unhealthy", "unhealthy"),
    ("circuit", "unhealthy"),
    ("schema", "schema_error"),
    ("validation", "validation_error"),
    ("llm", "llm_unavailable"),
    ("model", "llm_unavailable"),
    ("timeout", "timeout"),
    ("timed out", "timeout"),
    ("超时", "timeout"),
    ("cancel", "cancelled"),
    ("已取消", "cancelled"),
    ("conflict", "conflict"),
    ("冲突", "conflict"),
)


def _qualname(obj: Any) -> str:
    """取 `模块后缀.类名`（用于查表；`ToolError` → `ToolError`）"""
    cls = type(obj) if not isinstance(obj, type) else obj
    mod = str(getattr(cls, "__module__", "") or "")
    name = str(getattr(cls, "__name__", "") or "")
    short_mod = mod.split(".")[-1] if mod else ""
    return f"{short_mod}.{name}" if short_mod else name


def _tool_error_code(exc: BaseException) -> Optional[str]:
    """`ToolError` 是 `agent/tools/__init__.py` 的**通用包装**，不能一概而论

    它的 message 形如 `工具 'x' 执行失败: <原始异常>` 或 `未知工具: 'x'`。
    真正的语义在 message 里 —— 故对 `ToolError` 走关键词兜底而不是固定归码，
    否则"未知工具"会被报成 `internal_error`（CI 无法据此区分"能力不存在"
    与"能力坏了"，而这两者的处置完全不同）。
    """
    if type(exc).__name__ != "ToolError":
        return None
    msg = str(exc).lower()
    if "未知工具" in str(exc) or "unknown tool" in msg:
        return "not_found"
    return None


def from_exception(exc: BaseException, *,
                   capability: str = "") -> CapabilityError:
    """把任意 Python 异常映射成 `CapabilityError`（**唯一映射点**）

    判定顺序（先精确、后兜底，避免"想当然的归错码"）：
        1. 已是 `CapabilityError` ⇒ 原样返回（保幂等）；
        2. `ToolError` 的特判（见 `_tool_error_code`）；
        3. 异常类限定名查 `_BY_QUALNAME`（含 MRO 逐级回退）；
        4. 类名 / 消息的关键词兜底（`_KEYWORD_RULES`）；
        5. 一律 `internal_error`。

    **绝不**把 `str(exc)` 直接作为 `message` —— 原始文本只进 `detail`，
    由 `to_llm_safe()` 负责脱敏。
    """
    if isinstance(exc, CapabilityError):
        return exc

    raw = ""
    try:
        raw = str(exc)
    except Exception:  # noqa: BLE001
        raw = ""

    def _mk(code: str) -> CapabilityError:
        return CapabilityError(
            code, "", detail=raw, capability=capability,
            retryable=CODE_META[code].retryable)

    special = _tool_error_code(exc)
    if special:
        return _mk(special)

    for klass in type(exc).__mro__:
        key = _qualname(klass)
        if key in _BY_QUALNAME:
            return _mk(_BY_QUALNAME[key])
        if klass.__name__ in _BY_QUALNAME:
            return _mk(_BY_QUALNAME[klass.__name__])

    haystack = f"{type(exc).__name__} {raw}".lower()
    for needle, code in _KEYWORD_RULES:
        if needle in haystack:
            return _mk(code)

    return _mk("internal_error")


# ─────────────────────────────────────────────────────────────
#  统一结果信封（HTTP / CLI / 模型三条链路**同一个结构**）
# ─────────────────────────────────────────────────────────────


@dataclass
class CapabilityResult:
    """一次能力调用的统一结果（v1.4 §5.3：CI 可凭 `status` 阻断）

    字段就是对外契约，**不得各链路各拼一份**：
        `status` ∈ {"ok","error"}  —— CI 唯一需要看的字段
        `code`   —— 14 个错误码之一（`status=ok` 时恒为 `ok`）
        `data`   —— 成功时的结构化结果（失败恒为 None）
        `error`  —— 失败时的 `to_llm_safe()` 结构（成功恒为 None）
        `meta`   —— 非载荷元数据（含**唯一**易变字段 `timing`）
    """

    status: str = "ok"
    code: str = OK
    data: Any = None
    error: Optional[Dict[str, Any]] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> Dict[str, Any]:
        # 【D2】键序与键集固定，便于 CLI/HTTP 逐字段对拍
        return {
            "status": self.status,
            "code": self.code,
            "data": self.data,
            "error": self.error,
            "meta": dict(self.meta),
        }

    def http_status(self) -> int:
        if self.status == "ok":
            return 200
        return CODE_META.get(self.code, CODE_META["internal_error"]).http_status


def ok_result(data: Any, meta: Optional[Dict[str, Any]] = None) -> CapabilityResult:
    return CapabilityResult(status="ok", code=OK, data=data, error=None,
                            meta=dict(meta or {}))


def err_result(err: CapabilityError,
               meta: Optional[Dict[str, Any]] = None) -> CapabilityResult:
    return CapabilityResult(
        status="error", code=err.code, data=None,
        error=err.to_llm_safe(), meta=dict(meta or {}))
