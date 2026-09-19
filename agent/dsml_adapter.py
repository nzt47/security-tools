# -*- coding: utf-8 -*-
"""DSML 文本协议适配器 —— 把上游「当正文吐出来的工具调用标记」还原为结构化 tool_calls。

为什么必须放在这一层（解析层唯一入口）
--------------------------------------
实测（``TASK-01a-DSML实测特征与更正.md`` + 本轮补证）表明：上游在
**「模型知道自己有工具（提示词里列了工具名）但请求未走结构化 tools 通路」** 时，
会把 **DSML**（DeepSeek 原生 agent 标记）当纯文本塞进
``choices[0].message.content``，同时 ``finish_reason="stop"``、``tool_calls=None``。

平台原有的唯一 XML-tool 提取器 ``agent/tool_calling.py::_extract_xml_tool_calls``
只管 ``<tool_calls>`` / ``<prefix:tool_calls>`` 形态，对 DSML **完全失配**，
三层原因叠加：

1. 分隔符是**全角竖线 U+FF5C**（实测全仓 393 处 / 4 文件；半角 U+007C 为 0 处），
   而原提取器要求半角冒号的命名空间前缀；
2. 外层标签是 **``calls``**（实测含 ``calls``=True、含 ``tool_calls``=False），
   而原提取器与 orchestrator 的门控正则都只认 ``tool_calls``；
3. 全仓 ``git grep DSML -- "*.py"`` **零命中** ⇒ 平台从未实现过 DSML 适配器。

所以本模块是一个**纯函数模块**，被放在「上游响应 → 编排器」之间的同一个收敛点上
（``agent/tool_calling.py::_extract_xml_tool_calls`` 与
``agent/orchestrator/orchestrator.py`` 的 XML 分支共用它）：

* **不新增第二真相源**：输出与 OpenAI ``tool_calls`` **完全同构**，编排器 / 工具闸门 /
  审计链一行都不用改；
* **不静默**：参数类型还原失败一律产 ``validation_error``，绝不把字符串悄悄塞进
  一个声明为 ``integer`` 的参数；
* **不猜工具体**：抓到的名字不在注册表里就走 ``unknown_tool`` 错误 ——
  实测抓到的是 ``mcp__tools__shell_execute``，而注册表里是 ``shell_execute``。
  **静默改名是安全隐患**（可能让"猜到 MCP 工具"的调用落到一个语义不同的真实工具上），
  因此这里只报错、不映射；
* **有界**：未闭合标记不会无限缓冲（体积上限 + 时间上限），见 ``DSMLStreamGuard``。

约定：本文件内**不写裸标记文本**，一律用 ``FULLWIDTH_PIPE * 2`` / 转义构造 ——
实测把裸标记写进工具参数会触发解析器误判。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "FULLWIDTH_PIPE",
    "HALFWIDTH_PIPE",
    "DEFAULT_MAX_PAYLOAD_CHARS",
    "DEFAULT_DEADLINE_MS",
    "DSMLParseResult",
    "DSMLStreamGuard",
    "marker_token",
    "has_marker",
    "normalize_markers",
    "strip_markers",
    "sanitize_visible_text",
    "restore_arguments",
    "extract",
    "degraded_message",
]

# ════════════════════════════════════════════════════════════════════
# 0. 字符常量（**不要**在别处写裸标记）
# ════════════════════════════════════════════════════════════════════

#: 全角竖线 U+FF5C —— 实测真实数据**只有**这一种（393 处 / 4 文件）
FULLWIDTH_PIPE = "\uff5c"
#: 半角竖线 U+007C —— 实测 0 处；只为容错上游变体而接受
HALFWIDTH_PIPE = "|"

_PIPE_CLASS = "[" + FULLWIDTH_PIPE + HALFWIDTH_PIPE + "]"
_MARKER_SRC = _PIPE_CLASS + r"{2}[ \t]*DSML[ \t]*" + _PIPE_CLASS + r"{2}[ \t]*"
#: 开标签：真实数据里标记**前面还有一个半角 `<`**（实测原文形如 半角< + 全角全角DSML全角全角 + 空格 + calls>）。
#: 那个 `<` 必须一起吃掉，否则归一化后残留一个孤儿 `<` 泄漏进用户可见文本。
_MARKER_OPEN_RE = re.compile(r"<?" + _MARKER_SRC, re.IGNORECASE)
_MARKER_CLOSE_RE = re.compile(r"</" + _MARKER_SRC, re.IGNORECASE)

#: 未闭合标记的最大缓冲体积（字符）—— 超过即放弃解析、原样放行并标记 unclosed
DEFAULT_MAX_PAYLOAD_CHARS = 64 * 1024
#: 单次解析的时间预算（毫秒）—— 超过即停止继续解析 invoke
DEFAULT_DEADLINE_MS = 200.0


def marker_token(fullwidth: bool = True) -> str:
    """返回标记前缀字符串（两个竖线 + DSML + 两个竖线）

    做成函数而不是模块级常量，是为了让调用方**显式**选择全角/半角，
    而不是把某一形态当成"唯一真相"抄进代码。
    """
    p = FULLWIDTH_PIPE if fullwidth else HALFWIDTH_PIPE
    return p * 2 + "DSML" + p * 2


#: 流式守卫用：所有可能的标记起始（含 `<` 前缀形态），按长度降序
_MARKER_STARTS: tuple[str, ...] = tuple(sorted({
    marker_token(True), marker_token(False),
    "<" + marker_token(True), "<" + marker_token(False),
    "<tool_calls", "<calls", "<dsml:tool_calls", "<dsml:calls",
    "</tool_calls", "</calls",
}, key=len, reverse=True))
_MAX_MARKER_START = max(len(s) for s in _MARKER_STARTS)


def _partial_marker_suffix(text: str) -> int:
    """``text`` 尾部有多少字符是**某个标记起始的真前缀**

    流式守卫靠它决定"要不要扣住这几个字符等下一片"。返回值上限是
    ``_MAX_MARKER_START - 1``，因此**不会**造成无界扣留。
    """
    max_k = min(len(text), _MAX_MARKER_START - 1)
    for k in range(max_k, 0, -1):
        tail = text[-k:]
        for s in _MARKER_STARTS:
            if len(s) > k and s.startswith(tail):
                return k
    return 0


# ════════════════════════════════════════════════════════════════════
# 1. 归一化 / 检测 / 剥离
# ════════════════════════════════════════════════════════════════════

_LEGACY_OUTER_OPEN = re.compile(
    r"<\s*(?:\w+\s*:\s*)?(?:tool_calls|calls)\b[^>]*>", re.IGNORECASE)
_LEGACY_OUTER_CLOSE = re.compile(
    r"<\s*/\s*(?:\w+\s*:\s*)?(?:tool_calls|calls)\s*>", re.IGNORECASE)
#: 孤儿标记标签（没有外层包裹时的兜底剥离）
_ORPHAN_TAG = re.compile(
    r"<\s*/?\s*(?:\w+\s*:\s*)?(?:tool_calls|calls|invoke|parameter)\b[^>]*>",
    re.IGNORECASE)


def normalize_markers(text: str) -> str:
    """把 DSML 标记归一化成既有 XML 形态（纯文本替换，O(n)）

    闭合形态（``</`` + 标记 + `` invoke>``）先收敛成 ``</invoke>``，
    剩余的开标签形态（标记 + `` invoke name="x">``）再收敛成 ``<invoke name="x">``。
    顺序不能反 —— 否则闭合标签会变成 ``</<invoke>``。

    归一化的好处：下游解析/剥离只需要**一套** XML 正则，
    既支持全角/半角两种分隔符，也支持 ``calls`` / ``tool_calls`` 两种标签名。
    """
    if not text:
        return text
    text = _MARKER_CLOSE_RE.sub("</", text)
    return _MARKER_OPEN_RE.sub("<", text)


def has_marker(text: str) -> bool:
    """文本里是否存在 DSML 标记或既有 XML 工具调用包裹（**廉价**门控）

    这是 ``_extract_xml_tool_calls`` 与 orchestrator 门控的唯一判据，
    替换原先只认 ``tool_calls`` 的失配正则。
    """
    if not text:
        return False
    if _MARKER_OPEN_RE.search(text):
        return True
    return bool(_LEGACY_OUTER_OPEN.search(text))


def strip_markers(text: str) -> tuple[str, int]:
    """剥离整段工具调用包裹，返回 ``(干净文本, 剥离的块数)``

    未闭合时从开标签一路剥到文末（因为"标记之后的一切都是标记体"，
    留着只会把它泄漏给用户）。
    """
    if not text:
        return text, 0
    norm = normalize_markers(text)
    if not _LEGACY_OUTER_OPEN.search(norm):
        return norm, 0
    parts: list[str] = []
    pos = 0
    removed = 0
    while True:
        m = _LEGACY_OUTER_OPEN.search(norm, pos)
        if not m:
            break
        parts.append(norm[pos:m.start()])
        close = _LEGACY_OUTER_CLOSE.search(norm, m.end())
        pos = close.end() if close else len(norm)
        removed += 1
    parts.append(norm[pos:])
    return "".join(parts), removed


def sanitize_visible_text(text: str) -> tuple[str, int]:
    """用户可见文本的**最后一道兜底消毒闸**（防御性，不是主要修法）

    只在 ``has_marker`` 为真时才动手，避免误删正常正文里出现的
    ``<parameter>`` 之类字面量（例如用户就是在讨论 XML）。

    Returns:
        ``(消毒后文本, 剥离次数)``；剥离次数 > 0 时调用方**必须**记
        WARN 结构化日志 ``event=dsml_leak_blocked``。
    """
    if not text or not has_marker(text):
        return text, 0
    clean, n = strip_markers(text)
    clean2, n2 = _ORPHAN_TAG.subn("", clean)
    return clean2.strip(), n + n2


# ════════════════════════════════════════════════════════════════════
# 2. 参数类型还原（**禁止静默传字符串**）
# ════════════════════════════════════════════════════════════════════

_TYPE_ALIASES = {
    "int": "integer", "float": "number", "double": "number",
    "bool": "boolean", "str": "string", "list": "array", "dict": "object",
}
_INT_RE = re.compile(r"[+-]?\d+")
_NUM_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_TRUE_WORDS = frozenset({"true", "1", "yes", "y", "on"})
_FALSE_WORDS = frozenset({"false", "0", "no", "n", "off"})


def _autoparse(raw: str) -> tuple[Any, bool]:
    """schema 未声明类型时的尽力还原：能当 JSON 字面量读出来就用它

    Returns:
        ``(值, 是否发生了解析)``。解析不了就**保留字符串** ——
        没有 schema 就没有"应该是什么类型"的判据，此时猜测比保留更危险。
    """
    s = raw.strip()
    if not s:
        return raw, False
    if s in ("null", "None"):
        return None, True
    if s.lower() in _TRUE_WORDS | _FALSE_WORDS:
        return s.lower() in _TRUE_WORDS, True
    if _INT_RE.fullmatch(s):
        return int(s), True
    if _NUM_RE.fullmatch(s):
        return float(s), True
    if s[0] in "[{":
        try:
            return json.loads(s), True
        except (ValueError, TypeError):
            return raw, False
    return raw, False


def _coerce_one(raw: str, declared: str) -> tuple[Any, str | None]:
    """按单个 JSON Schema ``type`` 还原；失败返回错误说明（**不抛异常**）"""
    t = _TYPE_ALIASES.get(declared, declared)
    if t == "string":
        return raw, None
    if t == "integer":
        s = raw.strip()
        if _INT_RE.fullmatch(s):
            return int(s), None
        return None, "期望 integer，实到 %r" % (raw[:80],)
    if t == "number":
        s = raw.strip()
        if _NUM_RE.fullmatch(s):
            return float(s), None
        return None, "期望 number，实到 %r" % (raw[:80],)
    if t == "boolean":
        s = raw.strip().lower()
        if s in _TRUE_WORDS:
            return True, None
        if s in _FALSE_WORDS:
            return False, None
        return None, "期望 boolean，实到 %r" % (raw[:80],)
    if t in ("array", "object", "null"):
        try:
            v = json.loads(raw)
        except (ValueError, TypeError):
            return None, "期望 %s，实到非 JSON 文本 %r" % (t, raw[:80],)
        if t == "array" and not isinstance(v, list):
            return None, "期望 array，实到 %s" % type(v).__name__
        if t == "object" and not isinstance(v, dict):
            return None, "期望 object，实到 %s" % type(v).__name__
        if t == "null" and v is not None:
            return None, "期望 null，实到 %s" % type(v).__name__
        return v, None
    # 未知 type：**不猜**，原样保留（并留 note 由上层记录）
    return raw, None


def restore_arguments(
    params: Mapping[str, str],
    schema: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """按目标工具的 JSON Schema 还原参数类型

    DSML 的 ``parameter`` 值**永远是文本**，因此必须做一次显式类型还原；
    这一步是"能不能真的把工具跑起来"的关键。

    Returns:
        ``(args, errors, notes)``

        * ``errors`` 非空 ⇒ **调用方必须放弃这次调用**（不允许带着错误参数执行）；
        * ``notes`` 是软性提示（例如 schema 未声明类型、按 JSON 字面量尽力还原），
          需要记录但不阻断。
    """
    errors: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    out: dict[str, Any] = {}

    props = None
    required: Sequence[str] = ()
    additional_allowed = True
    if isinstance(schema, Mapping):
        raw_props = schema.get("properties")
        if isinstance(raw_props, Mapping):
            props = raw_props
        raw_req = schema.get("required")
        if isinstance(raw_req, (list, tuple)):
            required = [str(x) for x in raw_req]
        if schema.get("additionalProperties") is False:
            additional_allowed = False

    for name, raw in params.items():
        spec = props.get(name) if isinstance(props, Mapping) else None
        if not isinstance(spec, Mapping):
            if props is not None and not additional_allowed:
                errors.append({
                    "code": "validation_error", "parameter": name,
                    "message": "参数 %s 不在工具 schema 中，且 additionalProperties=false"
                               % (name,),
                })
                continue
            value, parsed = _autoparse(raw)
            out[name] = value
            if not parsed:
                notes.append({
                    "code": "type_unverified", "parameter": name,
                    "message": "工具 schema 未声明参数 %s 的类型；无法还原，按原文保留字符串"
                               % (name,),
                })
            continue

        declared = spec.get("type")
        types: list[str] = []
        if isinstance(declared, str):
            types = [declared]
        elif isinstance(declared, (list, tuple)):
            types = [str(x) for x in declared]
        # 只声明 enum 而没声明 type：按字符串取值并校验枚举
        if not types:
            v = raw.strip()
            enum = spec.get("enum")
            if isinstance(enum, (list, tuple)) and v not in enum:
                errors.append({
                    "code": "validation_error", "parameter": name,
                    "message": "参数 %s 取值 %r 不在枚举 %r 中" % (name, v, list(enum)),
                })
                continue
            out[name] = v
            continue

        value: Any = None
        last_msg: str | None = None
        for t in types:
            value, last_msg = _coerce_one(raw, t)
            if last_msg is None:
                break
        if last_msg is not None:
            errors.append({
                "code": "validation_error", "parameter": name,
                "message": "参数 %s 类型还原失败: %s" % (name, last_msg),
            })
            continue

        enum = spec.get("enum")
        if isinstance(enum, (list, tuple)) and value not in enum:
            errors.append({
                "code": "validation_error", "parameter": name,
                "message": "参数 %s 取值 %r 不在枚举 %r 中" % (name, value, list(enum)),
            })
            continue
        out[name] = value

    for name in required:
        if name not in out:
            errors.append({
                "code": "validation_error", "parameter": name,
                "message": "缺少必填参数 %s" % (name,),
            })
    return out, errors, notes


# ════════════════════════════════════════════════════════════════════
# 3. 解析结果与提取
# ════════════════════════════════════════════════════════════════════

@dataclass
class DSMLParseResult:
    """一次解析的完整结果（调用方据此决定：执行 / 降级 / 记日志）"""

    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    content: str = ""
    errors: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    found: bool = False
    unclosed: bool = False
    truncated: bool = False
    parse_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.tool_calls)

    def log_fields(self) -> dict[str, Any]:
        """结构化日志字段（**只取叶子字段**，不外传活对象）"""
        return {
            "event": "dsml_parsed" if self.ok else "dsml_parse_error",
            "found": self.found,
            "tool_calls": len(self.tool_calls),
            "errors": len(self.errors),
            "error_codes": [str(e.get("code", "")) for e in self.errors],
            "tools": [str(t.get("function", {}).get("name", "")) for t in self.tool_calls],
            "unclosed": self.unclosed,
            "truncated": self.truncated,
            "parse_ms": round(self.parse_ms, 3),
        }


_INVOKE_RE = re.compile(
    r"<\s*(?:\w+\s*:\s*)?invoke\s+(?P<attrs>[^>]*)>"
    r"(?P<body>.*?)"
    r"</\s*(?:\w+\s*:\s*)?invoke\s*>",
    re.DOTALL | re.IGNORECASE)
_PARAM_RE = re.compile(
    r"<\s*(?:\w+\s*:\s*)?parameter\s+(?P<attrs>[^>]*?)"
    r"(?:/\s*>|>"
    r"(?P<body>.*?)"
    r"</\s*(?:\w+\s*:\s*)?parameter\s*>)",
    re.DOTALL | re.IGNORECASE)
_ATTR_RE = re.compile(r"([\w:.-]+)\s*=\s*[\"']([^\"']*)[\"']")


def _attrs(text: str) -> dict[str, str]:
    return {k.lower(): v for k, v in _ATTR_RE.findall(text or "")}


def _default_schema_resolver(name: str) -> Mapping[str, Any] | None:
    """默认 schema 来源：平台运行时注册表（失败即返回 None，绝不抛）

    ``get_tool_schema`` 返回 ``None`` 表示"注册表里没有这个工具"，
    与"工具有但没 schema"在下游用 ``name_resolver`` 区分。
    """
    try:
        from agent.tools import get_tool_schema
        return get_tool_schema(name)
    except Exception:  # noqa: BLE001 解析层不得因取 schema 失败而崩
        return None


_AUTO = object()


def _default_known_tools() -> frozenset[str] | None:
    """当前注册表里的工具名

    **注册表为空时返回 None**（= 不做"未知工具"判定）。理由：裸进程/单测里
    ``agent.tools._registry`` 本来就是空的（只有 DigitalLife 启动时
    ``lifecycle_manager._register_builtin_tools`` 才填充），此时"不在表里"
    无从判定；真正的运行时注册表是满的（实测 86 个），判定照常生效。
    """
    try:
        from agent.tools import list_tools
        names = frozenset(str(t.get("name", "")) for t in list_tools())
        return names or None
    except Exception:  # noqa: BLE001
        return None


def extract(
    text: str,
    *,
    schema_resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
    known_tools: Iterable[str] | None | object = _AUTO,
    max_payload_chars: int = DEFAULT_MAX_PAYLOAD_CHARS,
    deadline_ms: float = DEFAULT_DEADLINE_MS,
    id_prefix: str = "dsml",
) -> DSMLParseResult:
    """从模型输出文本中提取 DSML / XML 工具调用

    Args:
        text: 上游 ``content`` 原文
        schema_resolver: 工具名 → JSON Schema；默认走平台注册表
        known_tools: 已知工具名集合；默认走平台注册表（空表 ⇒ 不判定）。
            显式传 ``None`` 表示**关闭**未知工具判定。
        max_payload_chars: 未闭合标记的最大解析体积，超过即截断并标记
            ``truncated``（**不得无限缓冲**）
        deadline_ms: 解析时间预算（毫秒），超预算即停止后续 invoke 解析
        id_prefix: 生成的 tool_call id 前缀

    Returns:
        :class:`DSMLParseResult`
    """
    started = time.monotonic()
    res = DSMLParseResult(content=text or "")
    if not text:
        res.parse_ms = (time.monotonic() - started) * 1000.0
        return res

    if not has_marker(text):
        res.parse_ms = (time.monotonic() - started) * 1000.0
        return res

    res.found = True
    norm = normalize_markers(text)

    # ── 取工具调用体 ──
    open_m = _LEGACY_OUTER_OPEN.search(norm)
    if open_m:
        close_m = _LEGACY_OUTER_CLOSE.search(norm, open_m.end())
        if close_m:
            body = norm[open_m.end():close_m.start()]
        else:
            # 未闭合：标记之后的一切都视为标记体。**有界**——先截断再解析，
            # 避免上游吐一个残缺标记就让整条链路陪着它一起膨胀。
            res.unclosed = True
            body = norm[open_m.end():]
    else:
        # 没有外层包裹但确实带 DSML 标记（孤儿 invoke）：宽容处理，
        # 否则这些调用会既不被执行、也不被剥离。
        res.unclosed = True
        body = norm

    if len(body) > max_payload_chars:
        body = body[:max_payload_chars]
        res.truncated = True
        res.unclosed = True
        res.errors.append({
            "code": "payload_too_large",
            "message": "工具调用体超过 %d 字符上限，已截断（防止未闭合标记无限缓冲）"
                       % (max_payload_chars,),
        })

    if not isinstance(known_tools, frozenset) and known_tools is not _AUTO and known_tools is not None:
        known_tools = frozenset(str(x) for x in known_tools)
    if known_tools is _AUTO:
        known_tools = _default_known_tools()

    # ── 未知工具判定**只对 DSML 文本协议通路生效** ──
    # 【为什么】既有 `<tool_calls>` XML 通路在本任务之前就存在，其工具名一直是由
    # 下游工具闸门（`agent/tools/__init__.py::call` 抛 ToolError）判否的，行为受
    # 既有单测锁定，不能因为解析层新增一层判定而改变（D2 向后兼容）。
    # 【顺带修掉一个真实缺陷】若判定同时作用于 XML 通路，解析结果就会**依赖运行时
    # 注册表是否已被 DigitalLife 填充**（`lifecycle_manager._register_builtin_tools`）
    # —— 同一段文本在"注册表空"与"注册表满"的进程里结果不同，这是不可接受的状态
    # 相关行为（实测：全量单测中 `test_6` 因此偶发失败，单跑却通过）。
    if not _MARKER_OPEN_RE.search(text):
        known_tools = None

    resolver = schema_resolver or _default_schema_resolver

    deadline = started + max(0.0, deadline_ms) / 1000.0
    idx = 0
    for m in _INVOKE_RE.finditer(body):
        if time.monotonic() > deadline:
            res.errors.append({
                "code": "deadline_exceeded",
                "message": "解析超过 %.0fms 预算，剩余 invoke 未解析" % (deadline_ms,),
            })
            break
        attrs = _attrs(m.group("attrs"))
        name = (attrs.get("name") or "").strip()
        if not name:
            res.errors.append({
                "code": "validation_error",
                "message": "invoke 缺少 name 属性（无法判定目标工具）",
            })
            continue
        if isinstance(known_tools, (set, frozenset)) and known_tools and name not in known_tools:
            res.errors.append({
                "code": "unknown_tool", "tool": name,
                "message": "工具 %s 不在当前注册表中；**不猜测、不改名**（静默映射可能把调用"
                           "落到语义不同的真实工具上）" % (name,),
            })
            continue

        raw_params: dict[str, str] = {}
        for pm in _PARAM_RE.finditer(m.group("body") or ""):
            pattrs = _attrs(pm.group("attrs"))
            pname = (pattrs.get("name") or "").strip()
            if not pname:
                continue
            pbody = pm.group("body")
            if pbody is None:
                pbody = pattrs.get("value", "")
            raw_params[pname] = pbody

        schema = None
        try:
            schema = resolver(name)
        except Exception as exc:  # noqa: BLE001
            res.notes.append({
                "code": "schema_unavailable", "tool": name,
                "message": "取 %s 的 JSON Schema 失败: %s；退化为「按 JSON 字面量尽力还原」"
                           % (name, exc),
            })
        if schema is None:
            res.notes.append({
                "code": "schema_missing", "tool": name,
                "message": "工具 %s 无 JSON Schema；退化为「按 JSON 字面量尽力还原」" % (name,),
            })

        args, errs, notes = restore_arguments(raw_params, schema)
        for e in errs:
            e.setdefault("tool", name)
        for nn in notes:
            nn.setdefault("tool", name)
        res.errors.extend(errs)
        res.notes.extend(notes)
        if errs:
            # **不执行**：带着错误参数的调用比不调用更危险
            continue

        res.tool_calls.append({
            "id": "%s_%d" % (id_prefix, idx),
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        })
        idx += 1

    # 未闭合 ⇒ **必须留明确错误**。此时不产出"半个 invoke"的调用
    # （``_INVOKE_RE`` 要求显式 ``</invoke>``）：响应被截断时宁可降级，
    # 也不要把一个可能不完整的调用真的执行掉。
    if res.unclosed and not res.tool_calls and not any(
            e.get("code") == "unclosed_marker" for e in res.errors):
        res.errors.append({
            "code": "unclosed_marker",
            "message": "工具调用标记未闭合（响应可能在流中被截断）；未产出可执行调用",
        })

    res.content = strip_markers(text)[0]
    res.parse_ms = (time.monotonic() - started) * 1000.0
    return res


def degraded_message(result: DSMLParseResult) -> str:
    """把解析失败翻译成**用户可读**的降级文案（禁止空串、禁止泄漏标记）

    只在"确实发现了工具调用标记、但一个都没解析成功"时使用。
    """
    if result.tool_calls:
        return ""
    reasons: list[str] = []
    for e in result.errors[:3]:
        code = str(e.get("code", ""))
        tool = str(e.get("tool", "") or "")
        if code == "unknown_tool":
            reasons.append("模型请求的工具 `%s` 不在当前可用工具清单中" % (tool,))
        elif code == "validation_error":
            reasons.append(str(e.get("message", "参数校验未通过")))
        elif code == "payload_too_large":
            reasons.append("工具调用内容过长，已截断")
        elif code == "deadline_exceeded":
            reasons.append("工具调用解析超时")
        else:
            reasons.append(str(e.get("message", code) or code))
    if not reasons:
        reasons.append("工具调用标记不完整")
    return ("⚠️ 上游以**文本协议**返回了工具调用，但平台未能将其解析为可执行调用：%s。\n"
            "原始标记已从回复中剥离（不会显示给你）；请重试，或改用支持结构化 "
            "tool_calls 的模型/网关。" % ("；".join(reasons),))


# ════════════════════════════════════════════════════════════════════
# 4. 流式守卫（把被 SSE 分片切开的标记攒齐；**有界**）
# ════════════════════════════════════════════════════════════════════

class DSMLStreamGuard:
    """流式出口守卫：把可能被分片切断的 DSML 标记攒齐后再判定

    为什么不能只在每个 chunk 上跑正则：实测流式响应有 400 个 chunk，
    标记会被任意切断（TASK-01 §3 第 5 步用例 7）。chunk 级正则既漏检
    （标题被切开）又可能误删（删掉半个标记，把正文切碎）。

    为什么**不会**无限缓冲（TASK-01 明文禁令）：

    * 空闲态只在"当前尾部是某个标记起始的**真前缀**"时扣留字符，
      扣留长度 ≤ 最长标记起始的长度（十几个字符）——**有界**；
    * 一旦进入标记体，累计扣留超过 ``max_buffer_chars`` 或停留超过
      ``timeout_s``，立即**原样吐出**并置 ``overflowed``，绝不继续攒。

    用法::

        guard = DSMLStreamGuard()
        for piece in stream:
            for out in guard.feed(piece):
                yield out            # 直接给用户看的安全文本
        for out in guard.flush():
            yield out
        for call in guard.take_tool_calls():
            ...                      # OpenAI 同构，交给既有工具链路
    """

    def __init__(self, *, max_buffer_chars: int = 16384,
                 timeout_s: float = 5.0,
                 schema_resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
                 known_tools: Iterable[str] | None | object = _AUTO,
                 id_prefix: str = "dsml_stream"):
        self.max_buffer_chars = int(max_buffer_chars)
        self.timeout_s = float(timeout_s)
        self._schema_resolver = schema_resolver
        self._known_tools = known_tools
        self._id_prefix = id_prefix
        self._pending = ""            # 空闲态扣留的少量字符
        self._capture: list[str] = []  # 标记体
        self._capture_started = 0.0
        self._capture_len = 0
        self.overflowed = False
        self.markers_seen = 0
        self.result = DSMLParseResult()

    # ── 内部 ──
    def _inside_capture(self) -> bool:
        return bool(self._capture)

    def _finish_capture(self) -> list[str]:
        """标记结束：解析它。**不把标记回吐给用户**"""
        raw = "".join(self._capture)
        self._capture = []
        self._capture_len = 0
        self.markers_seen += 1
        parsed = extract(
            raw, schema_resolver=self._schema_resolver,
            known_tools=self._known_tools, id_prefix=self._id_prefix)
        self.result.tool_calls.extend(parsed.tool_calls)
        self.result.errors.extend(parsed.errors)
        self.result.notes.extend(parsed.notes)
        self.result.found = True
        self.result.content = ""
        # 标记体外还可能有正文（例如 invoke 闭标签之后紧跟的解释文字）
        return [parsed.content] if parsed.content.strip() else []

    def _capture_exceeded(self) -> bool:
        """是否超出体积/时间上限（**有界性的唯一判据**）"""
        return (self._capture_len > self.max_buffer_chars
                or time.monotonic() - self._capture_started > self.timeout_s)

    def _overflow_capture(self, buf: str) -> None:
        """超限收口：原样放行 + 留明确记账（绝不继续攒）"""
        self.overflowed = True
        self.result.errors.append({
            "code": "stream_buffer_overflow",
            "message": "流式标记体超过上限（%d 字符 / %.1fs），已原样放行"
                       % (self.max_buffer_chars, self.timeout_s),
        })
        self._capture = []
        self._capture_len = 0

    # ── 对外 ──
    def feed(self, piece: str) -> list[str]:
        """喂入一个分片，返回**可以立即安全外发**的文本片段列表"""
        out: list[str] = []
        if not piece:
            return out
        if self._inside_capture():
            self._capture.append(piece)
            self._capture_len += len(piece)
            buf = "".join(self._capture)
            if _LEGACY_OUTER_CLOSE.search(normalize_markers(buf)):
                out.extend(self._finish_capture())
            elif self._capture_exceeded():
                # 有界：超限就原样放行，绝不继续攒
                out.append(buf)
                self._overflow_capture(buf)
            return out

        buf = self._pending + piece
        self._pending = ""
        # 找第一个可能的标记起始
        start = -1
        scan = 0
        while True:
            idx = buf.find(FULLWIDTH_PIPE, scan)
            if idx < 0:
                for cand in ("<", HALFWIDTH_PIPE):
                    idx2 = buf.find(cand, scan)
                    if idx2 >= 0 and (idx < 0 or idx2 < idx):
                        idx = idx2
                if idx < 0:
                    break
            if has_marker(buf[idx:]) or _looks_like_marker_start(buf, idx):
                start = idx
                break
            scan = idx + 1

        if start < 0:
            hold = _partial_marker_suffix(buf)
            if hold:
                self._pending = buf[-hold:]
                buf = buf[: len(buf) - hold]
            if buf:
                out.append(buf)
            return out

        if start > 0:
            out.append(buf[:start])
        rest = buf[start:]
        if has_marker(rest) or _LEGACY_OUTER_OPEN.search(normalize_markers(rest)):
            self._capture = [rest]
            self._capture_len = len(rest)
            self._capture_started = time.monotonic()
            # 标记可能已经完整到达
            if _LEGACY_OUTER_CLOSE.search(normalize_markers(rest)):
                out.extend(self._finish_capture())
            elif self._capture_exceeded():
                # 单片就超限（上游一次吐一个巨大残缺标记）⇒ 立即原样放行
                out.append("".join(self._capture))
                self._overflow_capture("")
        else:
            # 尾部是标记起始的真前缀 → 扣留等下片
            hold = _partial_marker_suffix(rest)
            if hold:
                self._pending = rest[-hold:]
                head = rest[: len(rest) - hold]
            else:
                head = rest
            if head:
                out.append(head)
        return out

    def flush(self) -> list[str]:
        """流结束时调用：把未闭合/超限的残留**原样或消毒后**放行"""
        out: list[str] = []
        if self._capture:
            raw = "".join(self._capture)
            self._capture = []
            self._capture_len = 0
            clean, n = sanitize_visible_text(raw)
            if clean:
                out.append(clean)
            if n:
                self.result.found = True
                self.result.errors.append({
                    "code": "unclosed_marker",
                    "message": "流结束时标记仍未闭合，已按兜底消毒闸剥离",
                })
            self.result.unclosed = True
        if self._pending:
            # 残留前缀：不是真标记，原样放行（否则会吞掉正文里的半个字符）
            out.append(self._pending)
            self._pending = ""
        return out

    def take_result(self) -> DSMLParseResult:
        """取累计结果（工具调用 + 错误）"""
        return self.result


def _looks_like_marker_start(buf: str, idx: int) -> bool:
    """``buf[idx:]`` 是否是某个标记起始的前缀（可能还没到齐）"""
    tail = buf[idx:]
    for s in _MARKER_STARTS:
        if s.startswith(tail) or tail.startswith(s):
            return True
    return False
