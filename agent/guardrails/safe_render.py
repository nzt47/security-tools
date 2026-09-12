"""UI 安全渲染 —— 注入防御机制 6（TASK-S4-03 步骤 4 / v7.2 §5.7）

【机制原文（§5.7 表第 6 行）】
    6  UI 安全渲染｜HTML 白名单/script 全禁/外链图片代理/iframe sandbox/CSP；
       **系统级信息只走结构化槽位，永不渲染外来文本**；审批按钮区 DOM 隔离固定 zIndex；
       **TaintBadge 恒带底色徽章**

【本任务的范围（任务书：本任务出**后端约束与校验**，配合 S6-01 前端）】
    本模块落地五条**后端**可判定、可测试的约束，并给出前端必须遵守的常量：
      1. `sanitize_html()`      —— HTML 白名单（**默认拒绝**：白名单外标签连内容一起转义）
      2. `csp_policy()`         —— CSP 指令集（script 全禁）
      3. `proxy_external_image()` / `is_external_url()` —— 外链图片代理
      4. `sandbox_iframe()`     —— iframe 强制 sandbox
      5. `render_structured_slot()` / `validate_system_render()` —— 结构化槽位；
         系统级信息**永不**渲染外来文本（与 `foreign_taint` 联动判定）
      另加 `taint_badge()` —— TaintBadge 恒带底色徽章的后端契约（class/attribute 常量）
      与 `APPROVAL_ZONE_STYLE` —— 审批按钮区 DOM 隔离固定 zIndex 的后端契约。

【为什么"白名单外连内容一起转义"而不是"删掉标签"】
    删标签会把 `<script>alert(1)</script>` 变成 `alert(1)`——**内容被渲染了**。
    对不可信内容，唯一安全的默认是"标签与内容都当文本"。这就是本模块
    `mode="strict"` 的语义；`mode="text"` 则把一切当纯文本（用于 TaintBadge 槽位）。

【为什么不用正则去"清洗" HTML】
    少量正则做 HTML 清洗是**已知不可靠**的做法（属性引号/实体/畸形标签都能绕过）。
    本模块改用标准库 `html.parser` 做**词法**切分，再按白名单重建——
    不引入第三方依赖（`bleach` 未必可用），且"重建"而非"删除"从结构上排除了
    "删掉标签留下内容"这类绕过。

【不易】默认拒绝；`javascript:`/`data:`/`vbscript:` 等危险 scheme 一律拒；
       系统级槽位不接受外来文本（由 `foreign_taint` 判定，不是文案约定）。
【变易】`ALLOWED_TAGS` / `ALLOWED_ATTRS` / `URL_SCHEME_ALLOWLIST` 是数据。
【简易】纯标准库（`html.parser` / `html.escape`）。
"""

from __future__ import annotations

import html as _html
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger("agent.guardrails.safe_render")

# ════════════════════════════════════════════════════════════
#  白名单（数据）
# ════════════════════════════════════════════════════════════

#: 允许保留的标签（**默认拒绝**：不在此列的一律转义）
ALLOWED_TAGS: Tuple[str, ...] = (
    "p", "br", "hr", "span", "div", "strong", "em", "b", "i", "u", "code", "pre",
    "ul", "ol", "li", "blockquote", "table", "thead", "tbody", "tr", "th", "td",
    "h1", "h2", "h3", "h4", "h5", "h6", "a", "img", "details", "summary",
)

#: 允许保留的属性（按标签细化；`*` 为通用集）
ALLOWED_ATTRS: Dict[str, Tuple[str, ...]] = {
    "*": ("class", "title", "role", "aria-label", "data-cp-slot"),
    "a": ("href", "target", "rel"),
    "img": ("src", "alt", "width", "height", "loading"),
    "td": ("colspan", "rowspan"),
    "th": ("colspan", "rowspan", "scope"),
}

#: 一律拒绝的属性（事件处理器 + 危险属性；即便在允许集里也先过这一关）
FORBIDDEN_ATTR_RE = re.compile(r"^(?:on[a-z]+|style|xlink:href|formaction|srcdoc)$", re.I)

#: 允许的 URL scheme（其余一律拒；相对路径与锚点另行放行）
URL_SCHEME_ALLOWLIST: Tuple[str, ...] = ("http", "https")

#: 明确危险的 scheme（拒；命中即拒绝整条 URL）
DANGEROUS_SCHEMES: Tuple[str, ...] = (
    "javascript", "data", "vbscript", "file", "blob", "filesystem",
)

#: script 全禁（§5.7 逐字）：这些标签**永不允许**出现，且其内容必须转义保留
SCRIPT_TAGS: Tuple[str, ...] = (
    "script", "style", "iframe", "object", "embed", "applet", "frame", "frameset",
    "form", "input", "button", "textarea", "select", "option", "link", "meta", "base",
)

#: CSP 指令集（script 全禁：`script-src 'none'`）
CSP_DIRECTIVES: Dict[str, str] = {
    "default-src": "'none'",
    "script-src": "'none'",
    "style-src": "'self' 'unsafe-inline'",   # 内联样式仅用于既有面板样式，不含脚本
    "img-src": "'self' data:",               # 外链图片必须走代理 → 代理后同源
    "font-src": "'self'",
    "connect-src": "'self'",
    "frame-src": "'none'",
    "frame-ancestors": "'none'",
    "object-src": "'none'",
    "base-uri": "'none'",
    "form-action": "'none'",
}

#: iframe sandbox 强制值（§5.7：iframe sandbox）
IFRAME_SANDBOX_VALUE = "allow-same-origin"

#: 外链图片代理前缀（渲染层把外部图片重写到本前缀下）
IMAGE_PROXY_PREFIX = "/api/cp/image-proxy?url="

#: 渲染模式
MODE_STRICT = "strict"     # 白名单标签保留，其余连内容一起转义
MODE_TEXT = "text"         # 一切当纯文本

#: TaintBadge 后端契约（§5.7 + P7.2-24：**恒带底色徽章**）
TAINT_BADGE_CLASS = "cp-taint-badge"
TAINT_BADGE_BASE_CLASS = "cp-taint-badge--has-bg"   # 恒带底色（不允许只有描边）
TAINT_BADGE_ATTR = "data-cp-taint-source"

#: 审批按钮区 DOM 隔离的后端契约（§5.7⑦ + P7.2-24：固定 zIndex）
APPROVAL_ZONE_STYLE: Dict[str, str] = {
    "position": "fixed",
    "z-index": "2147483000",     # 固定值：不得由内容/主题覆盖
    "isolation": "isolate",      # DOM 隔离（配合 Shadow DOM）
    "pointer-events": "auto",
}
APPROVAL_ZONE_CLASS = "cp-approval-zone"
APPROVAL_ZONE_SHADOW_ROOT = True

#: 结构化槽位（系统级信息**只走**这些槽位）
SYSTEM_SLOTS: Tuple[str, ...] = (
    "system.status", "system.reason", "system.next_action",
    "system.incident", "system.tenant", "system.version",
)


class SafeRenderError(Exception):
    """安全渲染层基类异常"""


class UnsafeRenderError(SafeRenderError):
    """渲染内容违反 §5.7 机制 6 约束——**拒绝渲染**

    Attributes:
        reasons: 违规原因清单。
        slot: 相关槽位（系统级槽位时给出）。
    """

    def __init__(self, message: str, *, reasons: Optional[Sequence[str]] = None,
                 slot: str = "") -> None:
        self.reasons = list(reasons or [])
        self.slot = str(slot or "")
        super().__init__(message)


@dataclass
class SanitizeReport:
    """清洗报告（诊断/审计用）"""

    removed_tags: List[str] = field(default_factory=list)
    removed_attrs: List[str] = field(default_factory=list)
    rejected_urls: List[str] = field(default_factory=list)
    modified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"removed_tags": sorted(set(self.removed_tags)),
                "removed_attrs": sorted(set(self.removed_attrs)),
                "rejected_urls": sorted(set(self.rejected_urls)),
                "modified": self.modified}


# ════════════════════════════════════════════════════════════
#  URL 判定
# ════════════════════════════════════════════════════════════

_WS_CTRL_RE = re.compile(r"[\x00-\x20\x7f]+")


def _normalize_url(url: Any) -> str:
    """URL 规范化（去空白/控制字符——`java\\nscript:` 这类混淆的常见手法）"""
    return _WS_CTRL_RE.sub("", str(url or "")).strip()


def url_scheme(url: Any) -> str:
    """取 URL scheme（空 = 相对/锚点）"""
    try:
        return str(urlsplit(_normalize_url(url)).scheme or "").lower()
    except ValueError:
        return ""


def is_dangerous_url(url: Any) -> bool:
    """URL 是否危险（危险 scheme / 协议相对外链 / 混淆手法）"""
    raw = _normalize_url(url)
    if not raw:
        return False
    low = raw.lower()
    if low.startswith("//"):     # 协议相对 → 继承当前 scheme，视为外部
        return True
    if low.startswith("&"):      # 实体混淆（`&#106;avascript:`）
        return True
    scheme = url_scheme(raw)
    if scheme in DANGEROUS_SCHEMES:
        return True
    if scheme and scheme not in URL_SCHEME_ALLOWLIST:
        return True
    return False


def is_external_url(url: Any) -> bool:
    """URL 是否指向外部（http/https 且带主机名）"""
    raw = _normalize_url(url)
    try:
        parts = urlsplit(raw)
    except ValueError:
        return True
    return str(parts.scheme or "").lower() in ("http", "https") and bool(parts.hostname)


def proxy_external_image(url: Any, *, prefix: str = IMAGE_PROXY_PREFIX) -> str:
    """把外链图片重写为**代理地址**（§5.7：外链图片代理）

    相对路径/同源图片原样返回；危险 URL 返回空串（调用方应丢弃该图片）。
    """
    raw = _normalize_url(url)
    if not raw or is_dangerous_url(raw):
        return ""
    if not is_external_url(raw):
        return raw
    from urllib.parse import quote
    return f"{prefix}{quote(raw, safe='')}"


# ════════════════════════════════════════════════════════════
#  HTML 清洗（词法重建，默认拒绝）
# ════════════════════════════════════════════════════════════


def _allowed_attrs_for(tag: str) -> Set[str]:
    return set(ALLOWED_ATTRS.get("*", ())) | set(ALLOWED_ATTRS.get(tag, ()))


class _Rebuilder(HTMLParser):
    """按白名单**重建** HTML（未允许的标签连内容一起转义保留）

    【三条不变量（实现期实测修正，勿改回）】

    1. **被禁标签区间内的一切都转义**（`_escape_depth > 0`）。
       第一版只在 `handle_data` 里看 `_escape_depth`，`handle_starttag` 却直接按
       白名单放行——于是 `<form><p>x</p></form>` 里的 `<p>` 被**原样输出**，违反了
       "连内容一起转义"的声明。现在三个回调**一律先看 `_escape_depth`**。

    2. **`handle_data` 恒转义，不看 `_escape_depth`**。这是一个**真实 XSS 漏洞**的修复：
       `convert_charrefs=True` 会把实体解码进 `handle_data`，第一版在
       `_escape_depth == 0` 时**原样输出**该文本——于是
       `&#60;script&#62;alert(1)&#60;/script&#62;` 被还原成**可执行的** `<script>`。
       文本就是文本：无论在哪一层，`handle_data` 都必须转义。
       （与 `convert_charrefs=True` 配合是自洽的：`&amp;` → `&` → 再转义回 `&amp;`。）

    3. **注释一律丢弃**：注释里藏指令是注入的常见手法，且注释对渲染无意义。
    """

    #: 自闭合/空元素（不产生结束标签）
    VOID_TAGS = frozenset({"br", "hr", "img", "input", "meta", "link", "base"})

    def __init__(self, report: SanitizeReport) -> None:
        super().__init__(convert_charrefs=True)
        self._report = report
        self._out: List[str] = []
        self._escape_depth = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        name = str(tag or "").lower()
        raw_tag = self.get_starttag_text() or f"<{name}>"
        is_void = name in self.VOID_TAGS
        # 不变量 1：已在被禁区间内 ⇒ 一切按文本，并按嵌套配对递增
        if self._escape_depth > 0:
            self._out.append(_html.escape(raw_tag))
            if not is_void:
                self._escape_depth += 1
            return
        if name in SCRIPT_TAGS or name not in ALLOWED_TAGS:
            self._report.removed_tags.append(name)
            self._report.modified = True
            self._out.append(_html.escape(raw_tag))
            if not is_void:
                self._escape_depth += 1
            return
        cleaned = self._clean_attrs(name, attrs)
        rendered = "".join(f' {k}="{_html.escape(v, quote=True)}"'
                           for k, v in cleaned)
        if is_void:
            self._out.append(f"<{name}{rendered}>")
        else:
            self._out.append(f"<{name}{rendered}>")

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        """自闭合写法 `<x/>`：**不改变** `_escape_depth`（它自己开自己合，净零）

        注意不能直接转调 `handle_starttag`——那会为一个自闭合的非空元素多算一层嵌套，
        导致后续内容被无限期地当成文本（实现期实测到的真实缺陷：`<script>` 之后的
        `<img>` 全被转义，正是嵌套计数不平衡的表现）。
        """
        name = str(tag or "").lower()
        raw_tag = self.get_starttag_text() or f"<{name}/>"
        if self._escape_depth > 0 or name in SCRIPT_TAGS or name not in ALLOWED_TAGS:
            if name in SCRIPT_TAGS or name not in ALLOWED_TAGS:
                self._report.removed_tags.append(name)
                self._report.modified = True
            self._out.append(_html.escape(raw_tag))
            return
        cleaned = self._clean_attrs(name, attrs)
        rendered = "".join(f' {k}="{_html.escape(v, quote=True)}"' for k, v in cleaned)
        self._out.append(f"<{name}{rendered}>")

    def handle_endtag(self, tag: str) -> None:
        name = str(tag or "").lower()
        # 不变量 1：在被禁区间内 ⇒ 转义，并按嵌套配对递减（**与 start 严格对称**）
        if self._escape_depth > 0:
            if name not in self.VOID_TAGS:
                self._escape_depth = max(0, self._escape_depth - 1)
            self._out.append(_html.escape(f"</{name}>"))
            return
        if name in SCRIPT_TAGS or name not in ALLOWED_TAGS or name in self.VOID_TAGS:
            self._out.append(_html.escape(f"</{name}>"))
            return
        self._out.append(f"</{name}>")

    def handle_data(self, data: Any) -> None:
        # 不变量 2：**恒转义**——文本就是文本，任何位置都不能当成标记输出
        self._out.append(_html.escape(str(data or "")))

    def handle_comment(self, data: Any) -> None:
        """注释一律去掉（注释里藏指令是注入的常见手法）"""
        self._report.modified = True

    def handle_decl(self, decl: Any) -> None:
        self._report.modified = True

    def handle_pi(self, data: Any) -> None:
        self._report.modified = True

    def unknown_decl(self, data: Any) -> None:
        self._report.modified = True

    def _clean_attrs(self, tag: str,
                     attrs: Sequence[Tuple[str, Optional[str]]]) -> List[Tuple[str, str]]:
        allowed = _allowed_attrs_for(tag)
        cleaned: List[Tuple[str, str]] = []
        for key, value in attrs:
            name = str(key or "").lower()
            raw_value = "" if value is None else str(value)
            if name not in allowed or FORBIDDEN_ATTR_RE.match(name):
                self._report.removed_attrs.append(f"{tag}@{name}")
                self._report.modified = True
                continue
            if name in ("href", "src"):
                if is_dangerous_url(raw_value):
                    self._report.rejected_urls.append(f"{tag}@{name}:{raw_value[:80]}")
                    self._report.modified = True
                    continue
                if tag == "img" and name == "src":
                    proxied = proxy_external_image(raw_value)
                    if not proxied:
                        self._report.rejected_urls.append(f"{tag}@{name}:{raw_value[:80]}")
                        self._report.modified = True
                        continue
                    raw_value = proxied
                if tag == "a" and name == "href":
                    raw_value = sanitize_link(raw_value)
            cleaned.append((name, raw_value))
        if tag == "a":
            cleaned = _force_link_safety(cleaned, self._report)
        if tag == "iframe":       # 理论上到不了（iframe 在 SCRIPT_TAGS 中）
            cleaned = _force_iframe_sandbox(cleaned)
        return cleaned

    def result(self) -> str:
        return "".join(self._out)


def _force_link_safety(attrs: List[Tuple[str, str]],
                       report: SanitizeReport) -> List[Tuple[str, str]]:
    """外链 `<a>` 强制 `rel="noopener noreferrer"` + `target="_blank"` """
    data = dict(attrs)
    if "href" in data and is_external_url(data["href"]):
        if data.get("target") != "_blank":
            data["target"] = "_blank"
        rel = set((data.get("rel") or "").split())
        rel |= {"noopener", "noreferrer"}
        data["rel"] = " ".join(sorted(r for r in rel if r))
        report.modified = True
    order = [k for k, _ in attrs if k in data]
    for key in data:
        if key not in order:
            order.append(key)
    return [(k, data[k]) for k in order]


def _force_iframe_sandbox(attrs: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """iframe 强制 sandbox（§5.7：iframe sandbox）"""
    data = dict(attrs)
    data["sandbox"] = IFRAME_SANDBOX_VALUE
    return list(data.items())


def sanitize_link(url: Any) -> str:
    """链接安全化（危险 scheme → 空；其余原样）"""
    raw = _normalize_url(url)
    if not raw or is_dangerous_url(raw):
        return ""
    return raw


def sanitize_html(source: Any, *, mode: str = MODE_STRICT) -> Tuple[str, SanitizeReport]:
    """清洗 HTML（**默认拒绝**；未允许的标签连内容一起转义）

    Args:
        source: 待清洗内容。
        mode: `MODE_STRICT`（白名单重建）或 `MODE_TEXT`（全文本）。

    Returns:
        (清洗后的 HTML, `SanitizeReport`)。
    """
    text = str(source or "")
    report = SanitizeReport()
    if mode == MODE_TEXT:
        report.modified = bool(text)
        return _html.escape(text), report
    parser = _Rebuilder(report)
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:  # noqa: BLE001 解析异常 → 退回全转义（保守）
        logger.warning("HTML 解析异常，退回全转义: %s: %s", type(exc).__name__, exc)
        report.modified = True
        return _html.escape(text), report
    out = parser.result()
    if out != text:
        report.modified = True
    return out, report


# ════════════════════════════════════════════════════════════
#  CSP / iframe / TaintBadge / 审批区
# ════════════════════════════════════════════════════════════


def csp_policy(*, overrides: Optional[Mapping[str, str]] = None) -> str:
    """生成 CSP 头值（**script 全禁**）

    Args:
        overrides: 覆盖/追加指令（`None` 值表示删除该指令）。
    """
    directives = dict(CSP_DIRECTIVES)
    for key, value in dict(overrides or {}).items():
        name = str(key).strip().lower()
        if value is None:
            directives.pop(name, None)
        else:
            directives[name] = str(value)
    return "; ".join(f"{k} {v}" for k, v in directives.items())


def csp_headers(*, overrides: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """可直接合并进响应的安全响应头集合"""
    return {
        "Content-Security-Policy": csp_policy(overrides=overrides),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
    }


def sandbox_iframe(html: Any) -> str:
    """给所有 iframe 强制加 sandbox（**注意**：`iframe` 在 `SCRIPT_TAGS` 中，
    经 `sanitize_html` 会被整段转义。本函数用于**明确可信**的宿主页面模板路径，
    不用于不可信内容。）"""
    if not str(html or "").strip():
        return ""
    return re.sub(
        r"(?i)<iframe\b([^>]*)>",
        lambda m: (f'<iframe sandbox="{IFRAME_SANDBOX_VALUE}"'
                   if "sandbox" in m.group(1).lower()
                   else f'<iframe sandbox="{IFRAME_SANDBOX_VALUE}"{m.group(1)}>'),
        str(html),
    )


def taint_badge(source: Any = "", *, label: str = "", mark_id: str = "") -> str:
    """TaintBadge 后端契约：**恒带底色徽章**（不允许只有描边）"""
    src = str(source or "").strip() or "unknown"
    text = str(label or "").strip() or src
    return (
        f'<span class="{TAINT_BADGE_CLASS} {TAINT_BADGE_BASE_CLASS}" '
        f'{TAINT_BADGE_ATTR}="{_html.escape(src, quote=True)}" '
        f'data-cp-taint-mark="{_html.escape(str(mark_id or ""), quote=True)}" '
        f'role="status" aria-label="{_html.escape(text, quote=True)}">'
        f'{_html.escape(text)}</span>'
    )


def approval_zone_style() -> Dict[str, str]:
    """审批按钮区 DOM 隔离的固定样式（zIndex **固定**，不由内容/主题覆盖）"""
    return dict(APPROVAL_ZONE_STYLE)


# ════════════════════════════════════════════════════════════
#  系统级信息只走结构化槽位
# ════════════════════════════════════════════════════════════


@dataclass
class SlotRender:
    """结构化槽位渲染结果"""

    slot: str
    value: str
    tainted: bool = False
    rejected: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"slot": self.slot, "value": self.value, "tainted": self.tainted,
                "rejected": self.rejected, "reason": self.reason}


def render_structured_slot(slot: str, value: Any, *,
                           tainted: bool = False,
                           allow_tainted: bool = False) -> SlotRender:
    """渲染**结构化槽位**（系统级信息唯一允许的通道）

    §5.7 机制 6："系统级信息只走结构化槽位，**永不渲染外来文本**"。
    故：槽位名必须在 `SYSTEM_SLOTS` 内；外来（tainted）内容默认**拒绝**渲染。

    Args:
        slot: 槽位名（须在 `SYSTEM_SLOTS` 内）。
        value: 槽位值（渲染为**纯文本**，恒转义）。
        tainted: 调用方标注该值来自外来文本。
        allow_tainted: 显式覆盖（**默认 False**；仅在确实需要展示外来文本摘要时使用，
            且此时值仍恒转义）。

    Returns:
        `SlotRender`（`rejected=True` 表示未渲染）。
    """
    name = str(slot or "").strip()
    if name not in SYSTEM_SLOTS:
        return SlotRender(slot=name, value="", rejected=True,
                          reason=f"未知系统槽位 {name!r}；合法槽位：{list(SYSTEM_SLOTS)}")
    if tainted and not allow_tainted:
        return SlotRender(
            slot=name, value="", tainted=True, rejected=True,
            reason="外来文本禁入系统级槽位（§5.7 机制 6：系统级信息只走结构化槽位）",
        )
    return SlotRender(slot=name, value=_html.escape(str(value if value is not None else "")),
                      tainted=bool(tainted))


def validate_system_render(payload: Any, *,
                           ledger: Optional[Any] = None) -> Dict[str, Any]:
    """校验一段**系统级**渲染载荷是否含外来文本（§5.7 机制 6 的校验入口）

    Args:
        payload: 待渲染文本或结构（dict/list 会被摊平成文本后判定）。
        ledger: 外来文本污点账（缺省进程级账）。

    Returns:
        {ok, reason, mark_count, sources}
    """
    text = _flatten(payload)
    if not text.strip():
        return {"ok": True, "reason": "", "mark_count": 0, "sources": []}
    try:
        from agent.guardrails.foreign_taint import (
            DEST_SYSTEM_PROMPT, check_text, get_foreign_taint)
        verdict = check_text(text, destination=DEST_SYSTEM_PROMPT,
                             ledger=ledger or get_foreign_taint(),
                             surface="safe_render.system_slot")
        return {"ok": bool(verdict.allowed), "reason": verdict.reason,
                "mark_count": len(verdict.marks), "sources": list(verdict.sources)}
    except Exception as exc:  # noqa: BLE001 校验器不可用 → 不阻断（fail-open）
        logger.warning("系统级渲染外来文本校验不可用: %s: %s", type(exc).__name__, exc)
        return {"ok": True, "reason": f"校验器不可用（{type(exc).__name__}）",
                "mark_count": 0, "sources": []}


def _flatten(value: Any, *, _depth: int = 0) -> str:
    """把结构摊平为文本（**只用于校验**，不用于渲染）"""
    if _depth > 8:
        return ""
    if isinstance(value, Mapping):
        return " ".join(_flatten(v, _depth=_depth + 1) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten(v, _depth=_depth + 1) for v in value)
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        return ""
    return str(value)


def render_safe(payload: Any, *, mode: str = MODE_STRICT,
                allow_tainted_system: bool = False) -> Dict[str, Any]:
    """**机制 6 总入口**：清洗 + 系统级外来文本校验 + 安全响应头

    Returns:
        {html, report, system_check, headers, ok}
    """
    html_out, report = sanitize_html(payload, mode=mode)
    system_check = validate_system_render(payload)
    if not system_check["ok"] and not allow_tainted_system:
        html_out, extra = sanitize_html(payload, mode=MODE_TEXT)
        report.modified = report.modified or extra.modified
    return {
        "html": html_out,
        "report": report.to_dict(),
        "system_check": system_check,
        "headers": csp_headers(),
        "ok": bool(system_check["ok"] or allow_tainted_system),
    }


def safe_render_state() -> Dict[str, Any]:
    """机制 6 状态快照（诊断/验收报告）"""
    return {
        "allowed_tags": list(ALLOWED_TAGS),
        "script_tags_banned": list(SCRIPT_TAGS),
        "dangerous_schemes": list(DANGEROUS_SCHEMES),
        "url_scheme_allowlist": list(URL_SCHEME_ALLOWLIST),
        "csp": CSP_DIRECTIVES,
        "iframe_sandbox": IFRAME_SANDBOX_VALUE,
        "image_proxy_prefix": IMAGE_PROXY_PREFIX,
        "system_slots": list(SYSTEM_SLOTS),
        "taint_badge": {"class": TAINT_BADGE_CLASS, "base_class": TAINT_BADGE_BASE_CLASS,
                        "attr": TAINT_BADGE_ATTR, "always_has_background": True},
        "approval_zone": {"class": APPROVAL_ZONE_CLASS,
                          "style": approval_zone_style(),
                          "shadow_root": APPROVAL_ZONE_SHADOW_ROOT},
    }


__all__ = [
    # 白名单与常量
    "ALLOWED_TAGS", "ALLOWED_ATTRS", "FORBIDDEN_ATTR_RE", "URL_SCHEME_ALLOWLIST",
    "DANGEROUS_SCHEMES", "SCRIPT_TAGS", "CSP_DIRECTIVES", "IFRAME_SANDBOX_VALUE",
    "IMAGE_PROXY_PREFIX", "MODE_STRICT", "MODE_TEXT",
    "TAINT_BADGE_CLASS", "TAINT_BADGE_BASE_CLASS", "TAINT_BADGE_ATTR",
    "APPROVAL_ZONE_STYLE", "APPROVAL_ZONE_CLASS", "APPROVAL_ZONE_SHADOW_ROOT",
    "SYSTEM_SLOTS",
    # 异常与报告
    "SafeRenderError", "UnsafeRenderError", "SanitizeReport", "SlotRender",
    # URL
    "url_scheme", "is_dangerous_url", "is_external_url",
    "proxy_external_image", "sanitize_link",
    # 清洗
    "sanitize_html",
    # CSP / iframe / 徽章
    "csp_policy", "csp_headers", "sandbox_iframe", "taint_badge",
    "approval_zone_style",
    # 槽位
    "render_structured_slot", "validate_system_render", "render_safe",
    "safe_render_state",
]
