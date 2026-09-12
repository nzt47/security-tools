"""外来文本污点标记 —— 注入防御机制 1（TASK-S4-03 步骤 4 / v7.2 §5.7）

【机制原文（§5.7 表第 1 行）】
    1  Taint 标记｜所有外来文本（MCP 返回/检索结果/子智能体输出/文件内容）打 taint，
       **禁入 system prompt 与决策分支，只进受沙箱槽位**

【与 `agent/policy/taint.py` 的边界（**两套 taint 不是一回事，勿混用**）】
    `agent/policy/taint.py`（S4-02）  = **密钥材料**污点：记录"本进程读过哪些密钥"，
                                        供**出域判定**拒绝"读密钥→外发"链路（§5.7 机制 4）。
                                        关注量：`secret_kinds` / `source_ref` / 进程作用域。
    本模块（S4-03）                    = **外来文本**污点：记录"这段文本来自不可信来源"，
                                        供**上下文组装**拒绝其进入 system prompt 与决策分支
                                        （§5.7 机制 1）。关注量：内容摘要 / 来源类型 / TTL。
    两者**语义正交、可同时命中**（一段从 MCP 取回、又恰好含密钥的文本，两边都要标记）。
    故本模块**不改动** `agent/policy/taint.py` 一行，也不复用它的账（复用它会让
    "出域拒绝"与"注入拒绝"两条判定互相污染）。

【怎么判定"这段文本是外来文本"——只存摘要，不存原文】
    标记时对**规范化全文**与**每一行（≥ `MIN_FRAGMENT_CHARS`）**各存一个 sha256 摘要；
    检查时同样切分候选文本再比对摘要。这样：
      - **不落原文**（隐私/留存纪律与 S2-02 一致）；
      - 能抓住真实攻击形态（外来文档被整段/整行拼进 system prompt）；
      - 不引入子串扫描的 O(n·m) 爆炸（按行对齐即可）。

【默认开启的理由（与 S4-02 `egress_guard` 同款口径）】
    总开关默认 **开**：**开启不等于收紧**——只有在调用方**显式标记过**外来文本之后，
    本模块才会拦；没有任何标记时所有判定都返回"放行"，既有行为逐字节不变。
    内部异常一律 fail-open（通用硬约束 1：新增机制失败不得阻断主流程），
    但**已判定为污染的内容绝不 fail-open**——那是本机制的全部意义。

【不易】不落原文；TTL 自动过期；账有容量上限（防长跑进程无限增长）。
【变易】`ForeignSource` 是数据：新增来源（如 `external_http`）加一枚举值 +
        `SOURCE_LABELS` 一项即可；`mark()` 接受任意字符串来源（未知来源按保守处理）。
【简易】纯标准库；进程内账（跨进程共享留 P5，与 §11.7 单机口径一致）。
"""

from __future__ import annotations

import enum
import hashlib
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("agent.guardrails.foreign_taint")

# ════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════

ENV_ENABLED = "CP_GUARDRAILS_FOREIGN_TAINT"
ENV_TTL_SECONDS = "CP_GUARDRAILS_FOREIGN_TAINT_TTL_SECONDS"
ENV_MAX_MARKS = "CP_GUARDRAILS_FOREIGN_TAINT_MAX_MARKS"

#: 标记默认存活时长（秒）——与 `agent/policy/taint.py` 同口径 900s
DEFAULT_TTL_SECONDS = 900

#: 账容量上限（超出后淘汰最旧；防长跑进程无限增长）
DEFAULT_MAX_MARKS = 2000

#: 单个标记最多保留的片段摘要数（防超长文档撑爆账）
MAX_FRAGMENTS_PER_MARK = 256

#: 片段最小字符数（短于此的行不单独建摘要——过短会误命中）
MIN_FRAGMENT_CHARS = 24

#: 单个标记的原文用于切片的最大长度（只读用于切片，不落盘）
MAX_SLICE_CHARS = 256 * 1024

#: 受沙箱槽位名（§5.7：外来文本"只进受沙箱槽位"）
SANDBOX_SLOT = "untrusted_slot"

#: 判定结论
VERDICT_ALLOW = "allow"
VERDICT_BLOCK = "block"

_WS_RE = re.compile(r"\s+")


class ForeignSource(str, enum.Enum):
    """外来文本来源（§5.7 机制 1 逐字四类）"""

    MCP = "mcp"                # MCP 返回
    RETRIEVAL = "retrieval"    # 检索结果（知识库/向量/搜索）
    SUBAGENT = "subagent"      # 子智能体输出
    FILE = "file"              # 文件内容

    #: 扩展来源（非 §5.7 四类，但同属外来；显式命名以便审计区分）
    EXTERNAL_HTTP = "external_http"    # 外部 HTTP 响应（web 工具）
    TOOL_RESULT = "tool_result"        # 其他工具返回
    UNKNOWN = "unknown"                # 未声明来源（按最保守处理）


#: 来源 → 中文标签（审计/错误文案）
SOURCE_LABELS: Dict[str, str] = {
    ForeignSource.MCP.value: "MCP 返回",
    ForeignSource.RETRIEVAL.value: "检索结果",
    ForeignSource.SUBAGENT.value: "子智能体输出",
    ForeignSource.FILE.value: "文件内容",
    ForeignSource.EXTERNAL_HTTP.value: "外部 HTTP 响应",
    ForeignSource.TOOL_RESULT.value: "工具返回",
    ForeignSource.UNKNOWN.value: "未声明来源",
}

#: §5.7 逐字的四类外来来源（验收核对用）
CANONICAL_SOURCES: Tuple[str, ...] = (
    ForeignSource.MCP.value, ForeignSource.RETRIEVAL.value,
    ForeignSource.SUBAGENT.value, ForeignSource.FILE.value,
)

#: 受沙箱槽位不得进入的目的地（§5.7：禁入 system prompt 与决策分支）
DEST_SYSTEM_PROMPT = "system_prompt"
DEST_DECISION_BRANCH = "decision_branch"
DEST_SANDBOX_SLOT = "sandbox_slot"

#: 禁止目的地集合（判定为污染即拒）
FORBIDDEN_DESTINATIONS: Tuple[str, ...] = (DEST_SYSTEM_PROMPT, DEST_DECISION_BRANCH)
ALLOWED_DESTINATIONS: Tuple[str, ...] = (DEST_SANDBOX_SLOT,)


# ════════════════════════════════════════════════════════════
#  异常与判定结果
# ════════════════════════════════════════════════════════════


class TaintError(Exception):
    """外来文本污点层基类异常"""


class TaintedContentError(TaintError):
    """外来文本试图进入禁止目的地（system prompt / 决策分支）——**拒绝**

    Attributes:
        destination: 目标目的地。
        marks: 命中的标记清单（摘要形态）。
    """

    def __init__(self, message: str, *, destination: str = "",
                 marks: Optional[Sequence["ForeignMark"]] = None) -> None:
        self.destination = destination
        self.marks = list(marks or [])
        super().__init__(message)


@dataclass
class TaintVerdict:
    """判定结果（**不抛异常**的形态；`enforce=True` 时由调用方转异常）"""

    allowed: bool
    destination: str = ""
    reason: str = ""
    marks: List["ForeignMark"] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "destination": self.destination,
            "reason": self.reason,
            "mark_count": len(self.marks),
            "mark_ids": [m.mark_id for m in self.marks],
            "sources": list(self.sources),
        }


# ════════════════════════════════════════════════════════════
#  摘要与切片
# ════════════════════════════════════════════════════════════


def normalize_text(text: Any) -> str:
    """规范化文本（空白折叠 + 去首尾）——保证"同内容同摘要" """
    return _WS_RE.sub(" ", str(text or "")).strip()


def digest_text(text: Any) -> str:
    """文本摘要（sha256；**只存摘要不存原文**）"""
    return "sha256:" + hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def slice_fragments(text: Any) -> List[str]:
    """把文本切成用于建摘要的片段（全文 + 每个足够长的行）

    Returns:
        规范化后的片段列表（按长度降序、去重；空文本返回 []）。
    """
    raw = str(text or "")[:MAX_SLICE_CHARS]
    normalized_full = normalize_text(raw)
    if not normalized_full:
        return []
    fragments = [normalized_full]
    for line in raw.splitlines():
        normalized_line = normalize_text(line)
        if len(normalized_line) >= MIN_FRAGMENT_CHARS:
            fragments.append(normalized_line)
    seen: List[str] = []
    for frag in sorted(set(fragments), key=len, reverse=True)[:MAX_FRAGMENTS_PER_MARK]:
        seen.append(frag)
    return seen


# ════════════════════════════════════════════════════════════
#  标记
# ════════════════════════════════════════════════════════════


@dataclass
class ForeignMark:
    """一条外来文本标记（**只存摘要**，不存原文）

    【`digests` 是摘要，不是原文（实现期修正）】第一版把切片后的**规范化明文**
    存在 `fragments` 里，与模块自己"只存摘要不存原文"的隐私声明直接矛盾——
    等于把 MCP 返回/检索结果/子代理输出/文件内容在账里留了一份副本。
    现改为 `digests`：每个片段一个 `sha256:…`，**账中不含任何原文**。
    索引键即这些摘要，判定时对候选文本同样取摘要后查表。
    """

    source: str
    ref: str = ""                       # 来源引用（如 mcp:filesystem / file:docs/a.md）
    digests: Tuple[str, ...] = ()       # 片段摘要（sha256:…；**不含原文**）
    chars: int = 0
    ts: float = field(default_factory=time.time)
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    trace_id: str = ""
    mark_id: str = field(default_factory=lambda: "ftm-" + uuid.uuid4().hex[:12])
    #: 是否可安全进入 system prompt（**恒 False**——§5.7 机制 1 的硬约束）
    system_prompt_allowed: bool = False

    def expires_at(self) -> float:
        return self.ts + max(0, int(self.ttl_seconds))

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at()

    def label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)

    def audit_leaves(self) -> Dict[str, Any]:
        """审计叶子（**无原文、无摘要全文**——只留来源与规模）"""
        return {
            "mark_id": self.mark_id,
            "source": self.source,
            "source_label": self.label(),
            "ref": self.ref,
            "chars": self.chars,
            "fragment_count": len(self.digests),
            "ttl_seconds": self.ttl_seconds,
            "trace_id": self.trace_id,
            "system_prompt_allowed": self.system_prompt_allowed,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {**self.audit_leaves(), "ts": self.ts, "expires_at": self.expires_at()}


# ════════════════════════════════════════════════════════════
#  账
# ════════════════════════════════════════════════════════════


def _env_flag(name: str, default: str = "1") -> bool:
    return str(os.environ.get(name, default)).strip().lower() \
        not in ("0", "false", "no", "off")


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


class ForeignTaintLedger:
    """外来文本污点账（进程内；有容量上限与 TTL）

    【不易】`mark()` 从不抛异常、从不阻断调用方——标记失败最坏是"没标上"，
    而标记本身发生在**已经拿到外来文本之后**，此时阻断只会把"注入风险"换成
    "功能不可用"。真正的拒绝发生在 `guard_*` 判定点。
    """

    def __init__(self, *, enabled: Optional[bool] = None,
                 ttl_seconds: Optional[int] = None,
                 max_marks: Optional[int] = None) -> None:
        self._enabled = _env_flag(ENV_ENABLED) if enabled is None else bool(enabled)
        self._ttl = int(ttl_seconds if ttl_seconds is not None
                       else _env_int(ENV_TTL_SECONDS, DEFAULT_TTL_SECONDS))
        self._max_marks = int(max_marks if max_marks is not None
                              else _env_int(ENV_MAX_MARKS, DEFAULT_MAX_MARKS))
        self._marks: List[ForeignMark] = []
        #: 片段摘要 → 标记 id 集合（判定索引）
        self._index: Dict[str, List[str]] = {}
        self._lock = threading.RLock()
        self._blocked_count = 0

    # ── 基本属性 ──

    @property
    def enabled(self) -> bool:
        return self._enabled

    def ttl_seconds(self) -> int:
        return self._ttl

    # ── 标记 ──

    def mark(self, text: Any, source: Any = ForeignSource.UNKNOWN, *,
             ref: str = "", trace_id: str = "",
             ttl_seconds: Optional[int] = None) -> Optional[ForeignMark]:
        """标记一段外来文本

        Args:
            text: 外来文本原文（**只取摘要，不留原文**）。
            source: `ForeignSource` 或字符串。
            ref: 来源引用（定位用）。
            trace_id: 关联 trace。
            ttl_seconds: 覆盖默认 TTL。

        Returns:
            新标记；`enabled=False` 或文本为空时返回 None（**绝不抛**）。
        """
        if not self._enabled:
            return None
        try:
            # 就地取摘要：明文只在本次调用栈内存活，**不进入标记与索引**
            digests = tuple(digest_text(frag) for frag in slice_fragments(text))
            if not digests:
                return None
            mark = ForeignMark(
                source=_source_value(source),
                ref=str(ref or ""),
                digests=digests,
                chars=len(str(text or "")),
                ts=time.time(),
                ttl_seconds=int(ttl_seconds if ttl_seconds is not None else self._ttl),
                trace_id=str(trace_id or ""),
            )
            with self._lock:
                self._prune_locked()
                self._marks.append(mark)
                for digest in mark.digests:
                    self._index.setdefault(digest, []).append(mark.mark_id)
                if len(self._marks) > self._max_marks:
                    self._evict_oldest_locked(len(self._marks) - self._max_marks)
            _audit_mark(mark)
            return mark
        except Exception as exc:  # noqa: BLE001 标记失败不得阻断调用方
            logger.warning("外来文本标记失败（不影响主路径）: %s: %s",
                           type(exc).__name__, exc)
            return None

    # ── 查询 ──

    def find(self, text: Any) -> List[ForeignMark]:
        """找出与给定文本重合的标记（**判定入口**）

        比对粒度：候选文本的规范化全文 + 每个足够长的行，逐个查摘要索引。
        """
        if not self._enabled:
            return []
        try:
            candidates = slice_fragments(text)
            if not candidates:
                return []
            with self._lock:
                self._prune_locked()
                hit_ids: List[str] = []
                for frag in candidates:
                    for mark_id in self._index.get(digest_text(frag), ()):
                        if mark_id not in hit_ids:
                            hit_ids.append(mark_id)
                by_id = {m.mark_id: m for m in self._marks}
                return [by_id[i] for i in hit_ids if i in by_id]
        except Exception as exc:  # noqa: BLE001 判定失败 → fail-open（返回空）
            logger.warning("外来文本判定失败（按未污染处理）: %s: %s",
                           type(exc).__name__, exc)
            return []

    def is_tainted(self, text: Any) -> bool:
        """给定文本是否含已标记的外来内容"""
        return bool(self.find(text))

    def marks(self) -> List[ForeignMark]:
        """当前全部未过期标记（副本）"""
        with self._lock:
            self._prune_locked()
            return list(self._marks)

    def stats(self) -> Dict[str, Any]:
        """账快照（面板/诊断）"""
        with self._lock:
            self._prune_locked()
            by_source: Dict[str, int] = {}
            for mark in self._marks:
                by_source[mark.source] = by_source.get(mark.source, 0) + 1
            return {
                "enabled": self._enabled,
                "mark_count": len(self._marks),
                "by_source": by_source,
                "index_size": len(self._index),
                "ttl_seconds": self._ttl,
                "max_marks": self._max_marks,
                "blocked_count": self._blocked_count,
            }

    # ── 清理 ──

    def clear(self) -> int:
        """清空账（用例隔离；返回清除条数）"""
        with self._lock:
            count = len(self._marks)
            self._marks.clear()
            self._index.clear()
            return count

    def forget(self, mark_id: str) -> bool:
        """撤销单个标记（如来源被判定为可信）"""
        with self._lock:
            target = next((m for m in self._marks if m.mark_id == mark_id), None)
            if target is None:
                return False
            self._marks = [m for m in self._marks if m.mark_id != mark_id]
            self._reindex_locked()
            return True

    # ── 内部 ──

    def _prune_locked(self) -> int:
        """淘汰已过期标记（返回淘汰数）"""
        now = time.time()
        expired = [m for m in self._marks if m.is_expired(now)]
        if not expired:
            return 0
        expired_ids = {m.mark_id for m in expired}
        self._marks = [m for m in self._marks if m.mark_id not in expired_ids]
        self._reindex_locked()
        return len(expired_ids)

    def _evict_oldest_locked(self, count: int) -> None:
        """容量淘汰（最旧优先）"""
        if count <= 0:
            return
        self._marks = self._marks[count:]
        self._reindex_locked()

    def _reindex_locked(self) -> None:
        """重建摘要索引（只在不变量可能被破坏后调用）"""
        index: Dict[str, List[str]] = {}
        for mark in self._marks:
            for digest in mark.digests:
                index.setdefault(digest, []).append(mark.mark_id)
        self._index = index

    def _note_block(self) -> None:
        with self._lock:
            self._blocked_count += 1


def _source_value(source: Any) -> str:
    """来源归一（枚举 → 值；未知字符串保留原样并标注）"""
    if isinstance(source, enum.Enum):
        return str(source.value)
    text = str(source or "").strip().lower()
    if text in SOURCE_LABELS:
        return text
    return text or ForeignSource.UNKNOWN.value


def _audit_mark(mark: ForeignMark) -> None:
    """标记入审计（best-effort，只写叶子）"""
    try:
        from agent.audit.facade import audit
        audit.record("guardrails.foreign_text_marked",
                     actor="guardrails.foreign_taint",
                     subject=f"taint:{mark.source}:{mark.ref or '-'}",
                     payload=mark.audit_leaves())
    except Exception as exc:  # noqa: BLE001
        logger.debug("外来文本标记审计写入失败（不影响标记）: %s", exc)


# ════════════════════════════════════════════════════════════
#  进程级单例
# ════════════════════════════════════════════════════════════

_LEDGER_LOCK = threading.RLock()
_LEDGER: Optional[ForeignTaintLedger] = None


def get_foreign_taint() -> ForeignTaintLedger:
    """进程级账（惰性创建）"""
    global _LEDGER
    with _LEDGER_LOCK:
        if _LEDGER is None:
            _LEDGER = ForeignTaintLedger()
        return _LEDGER


def set_foreign_taint(ledger: Optional[ForeignTaintLedger]) -> Optional[ForeignTaintLedger]:
    """替换进程级账（用例注入；返回旧账）"""
    global _LEDGER
    with _LEDGER_LOCK:
        old, _LEDGER = _LEDGER, ledger
        return old


def reset_foreign_taint() -> None:
    """清空并丢弃进程级账（用例隔离）"""
    global _LEDGER
    with _LEDGER_LOCK:
        if _LEDGER is not None:
            _LEDGER.clear()
        _LEDGER = None


def mark_foreign(text: Any, source: Any = ForeignSource.UNKNOWN, *,
                 ref: str = "", trace_id: str = "",
                 ledger: Optional[ForeignTaintLedger] = None) -> Optional[ForeignMark]:
    """便捷入口：标记外来文本（走进程级账）"""
    return (ledger or get_foreign_taint()).mark(
        text, source, ref=ref, trace_id=trace_id)


def mark_foreign_file(path: Any, content: Any, *,
                      ledger: Optional[ForeignTaintLedger] = None,
                      trace_id: str = "") -> Optional[ForeignMark]:
    """便捷入口：标记**文件内容**（§5.7 四类来源之一）"""

    return (ledger or get_foreign_taint()).mark(
        content, ForeignSource.FILE, ref=str(path or ""), trace_id=trace_id)


def mark_subagent_output(text: Any, *, agent_id: str = "",
                         ledger: Optional[ForeignTaintLedger] = None,
                         trace_id: str = "") -> Optional[ForeignMark]:
    """便捷入口：标记**子智能体输出**（§5.7 四类来源之一）"""
    return (ledger or get_foreign_taint()).mark(
        text, ForeignSource.SUBAGENT, ref=str(agent_id or ""), trace_id=trace_id)


def mark_retrieval(text: Any, *, ref: str = "",
                   ledger: Optional[ForeignTaintLedger] = None,
                   trace_id: str = "") -> Optional[ForeignMark]:
    """便捷入口：标记**检索结果**（§5.7 四类来源之一）"""
    return (ledger or get_foreign_taint()).mark(
        text, ForeignSource.RETRIEVAL, ref=str(ref or ""), trace_id=trace_id)


def mark_mcp_result(text: Any, *, server: str = "",
                    ledger: Optional[ForeignTaintLedger] = None,
                    trace_id: str = "") -> Optional[ForeignMark]:
    """便捷入口：标记 **MCP 返回**（§5.7 四类来源之一）"""
    return (ledger or get_foreign_taint()).mark(
        text, ForeignSource.MCP, ref=str(server or ""), trace_id=trace_id)


# ════════════════════════════════════════════════════════════
#  判定与执行（禁入 system prompt / 决策分支）
# ════════════════════════════════════════════════════════════


def _dest_is_forbidden(destination: str) -> bool:
    return str(destination or "") in FORBIDDEN_DESTINATIONS


def check_text(text: Any, *, destination: str, ledger: Optional[ForeignTaintLedger] = None,
               surface: str = "", enforced: bool = False) -> TaintVerdict:
    """判定 `text` 能否进入 `destination`

    Args:
        text: 待判定文本（要被植入目标位置的**完整文本**）。
        destination: `DEST_SYSTEM_PROMPT` / `DEST_DECISION_BRANCH` / `DEST_SANDBOX_SLOT`。
        surface: 调用点标识（审计/错误文案定位用）。
        enforced: **本模块是否已经阻断了动作**。本函数只出判定，故默认 `False`；
            仅 `guard_*(enforce=True)` 抛异常的真实阻断路径传 `True`。
            该值只影响审计字段（见 `_audit_block`），不影响判定与返回值。

    Returns:
        `TaintVerdict`（**不抛**）。
    """
    active = ledger or get_foreign_taint()
    if not active.enabled or not _dest_is_forbidden(destination):
        return TaintVerdict(allowed=True, destination=str(destination or ""),
                            reason="非受限目的地或污点账未启用")
    marks = active.find(text)
    if not marks:
        return TaintVerdict(allowed=True, destination=str(destination or ""))
    sources = sorted({m.source for m in marks})
    labels = "、".join(SOURCE_LABELS.get(s, s) for s in sources)
    reason = (
        f"外来文本禁入 {destination}（§5.7 机制 1）：命中 {len(marks)} 条 taint 标记"
        f"（来源：{labels}）——外来文本只可进受沙箱槽位 `{SANDBOX_SLOT}`"
        + (f"；调用点={surface}" if surface else "")
    )
    active._note_block()
    _audit_block(destination=destination, marks=marks, surface=surface,
                 enforced=enforced)
    return TaintVerdict(allowed=False, destination=str(destination or ""),
                        reason=reason, marks=marks, sources=sources)


def guard_system_prompt(text: Any, *, ledger: Optional[ForeignTaintLedger] = None,
                        surface: str = "", enforce: bool = False) -> TaintVerdict:
    """**机制 1 落点**：外来文本禁入 system prompt

    Args:
        enforce: True → 命中即抛 `TaintedContentError`；False → 只返回判定。

    Raises:
        TaintedContentError: `enforce=True` 且判定为污染。
    """
    verdict = check_text(text, destination=DEST_SYSTEM_PROMPT, ledger=ledger,
                         surface=surface, enforced=enforce)
    if enforce and not verdict.allowed:
        raise TaintedContentError(verdict.reason, destination=DEST_SYSTEM_PROMPT,
                                  marks=verdict.marks)
    return verdict


def guard_decision_branch(text: Any, *, ledger: Optional[ForeignTaintLedger] = None,
                          surface: str = "", enforce: bool = False) -> TaintVerdict:
    """**机制 1 落点**：外来文本禁入决策分支

    "决策分支"指会改变**行为走向**的判定输入（路由选择、工具选择、参数取值、
    审批结论）。外来文本可以**被看到**（进沙箱槽位），但不得直接参与分支取值。
    """
    verdict = check_text(text, destination=DEST_DECISION_BRANCH, ledger=ledger,
                         surface=surface, enforced=enforce)
    if enforce and not verdict.allowed:
        raise TaintedContentError(verdict.reason, destination=DEST_DECISION_BRANCH,
                                  marks=verdict.marks)
    return verdict


def wrap_untrusted(text: Any, source: Any = ForeignSource.UNKNOWN, *,
                   ref: str = "", ledger: Optional[ForeignTaintLedger] = None,
                   trace_id: str = "") -> Dict[str, Any]:
    """把外来文本包成**受沙箱槽位**载荷（唯一被允许的去处）

    Returns:
        {slot, source, source_label, ref, mark_id, text}——`slot` 恒为
        `SANDBOX_SLOT`；消费方必须按槽位处理（渲染侧恒带 TaintBadge，见
        `agent.guardrails.safe_render`）。
    """
    active = ledger or get_foreign_taint()
    mark = active.mark(text, source, ref=ref, trace_id=trace_id)
    return {
        "slot": SANDBOX_SLOT,
        "source": _source_value(source),
        "source_label": SOURCE_LABELS.get(_source_value(source), _source_value(source)),
        "ref": str(ref or ""),
        "mark_id": mark.mark_id if mark else "",
        "tainted": mark is not None,
        "text": str(text or ""),
    }


def _audit_block(*, destination: str, marks: Sequence[ForeignMark],
                 surface: str = "", enforced: bool = False) -> None:
    """拦截入审计（best-effort；**不含原文**）

    【三个字段的语义必须分清（实现期复核修正）】
    第一版硬编码 `"enforced": True`——但 `check_text()` 与 `guard_*(enforce=False)`
    **只出判定、不执行阻断**（阻断由调用方按 `allowed=False` 落实）。在审计里把
    "已判定"写成"已执行"，正是 §7 UI 五坑⑤「别让看板说谎」要禁的那类失真——
    审计链是系统的事实来源，这一字之差会让事后复查把"没人拦"读成"拦住了"。
    现拆成三个互不冒充的字段：
        `verdict`                 = 判定结论（本模块的看法）
        `caller_action_required`  = 调用方是否**必须**据此阻断
        `enforced`                = 本模块是否**已经**阻断了动作（抛异常路径）
    """
    try:
        from agent.audit.facade import audit
        audit.record("guardrails.taint_blocked",
                     actor="guardrails.foreign_taint",
                     subject=f"destination:{destination}",
                     payload={"destination": destination,
                              "surface": str(surface or ""),
                              "mark_count": len(marks),
                              "sources": sorted({m.source for m in marks}),
                              "mark_ids": [m.mark_id for m in marks][:20],
                              "verdict": VERDICT_BLOCK,
                              "caller_action_required": True,
                              "enforced": bool(enforced)})
    except Exception as exc:  # noqa: BLE001
        logger.debug("taint 拦截审计写入失败: %s", exc)


def taint_state(ledger: Optional[ForeignTaintLedger] = None) -> Dict[str, Any]:
    """当前污点状态快照（诊断/面板）"""
    active = ledger or get_foreign_taint()
    return {
        **active.stats(),
        "sandbox_slot": SANDBOX_SLOT,
        "forbidden_destinations": list(FORBIDDEN_DESTINATIONS),
        "allowed_destinations": list(ALLOWED_DESTINATIONS),
        "canonical_sources": list(CANONICAL_SOURCES),
    }


__all__ = [
    # 常量
    "ENV_ENABLED", "ENV_TTL_SECONDS", "ENV_MAX_MARKS", "DEFAULT_TTL_SECONDS",
    "DEFAULT_MAX_MARKS", "MAX_FRAGMENTS_PER_MARK", "MIN_FRAGMENT_CHARS",
    "SANDBOX_SLOT", "DEST_SYSTEM_PROMPT", "DEST_DECISION_BRANCH", "DEST_SANDBOX_SLOT",
    "FORBIDDEN_DESTINATIONS", "ALLOWED_DESTINATIONS", "CANONICAL_SOURCES",
    "SOURCE_LABELS", "VERDICT_ALLOW", "VERDICT_BLOCK",
    # 异常与结果
    "TaintError", "TaintedContentError", "TaintVerdict",
    # 摘要与切片
    "normalize_text", "digest_text", "slice_fragments",
    # 标记与账
    "ForeignSource", "ForeignMark", "ForeignTaintLedger",
    "get_foreign_taint", "set_foreign_taint", "reset_foreign_taint",
    # 标记入口
    "mark_foreign", "mark_foreign_file", "mark_subagent_output",
    "mark_retrieval", "mark_mcp_result",
    # 判定
    "check_text", "guard_system_prompt", "guard_decision_branch",
    "wrap_untrusted", "taint_state",
]
