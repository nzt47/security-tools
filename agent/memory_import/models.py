"""外部记忆导入（memory_import）域模型。

「外部记忆导入」是**与吞噬（ingestion）并列的另一个资产入口**：
吞噬吸收技能/文档/插件包；本包吸收 Claude / Trae 的偏好、规则与会话轨迹。
两者的共同纪律：**先只读预览，再逐批显式确认**，绝不静默写库。

本模块只承载纯数据（无 IO、无重依赖），字段全部可 JSON 序列化，
供 HTTP 层 / CLI / 前端直接消费：

- MemoryRecord：一条归一化后的外部记忆（来源 + 会话 + 角色 + 时间 + 正文）；
- Batch：提交单位（一个载体 + 一段可读正文 + 若干记录 id）；
- ImportPlan：一次预览的完整结果（统计、脱敏命中、分批、plan_id）。

【不易】plan_id 由「来源指纹 + 记录 id 集合 + 分批正文摘要」算出，
同一次输入重复 preview 得到同一个 plan_id；commit 时凭 plan_id 回读
快照，避免"预览的是 A、提交的是 B"（与 ingestion 的 plan-stale 同源）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ════════════════════════════════════════════════════════════
#  常量（单点定义，供 reader / pipeline / service / 测试共用）
# ════════════════════════════════════════════════════════════

SOURCE_CLAUDE = "claude"
SOURCE_TRAE = "trae"
SOURCES: Tuple[str, ...] = (SOURCE_CLAUDE, SOURCE_TRAE)

#: 记录类型：偏好/规则、用户输入历史、会话消息
KIND_PREFERENCE = "preference"
KIND_PROMPT = "prompt"
KIND_MESSAGE = "message"
KINDS: Tuple[str, ...] = (KIND_PREFERENCE, KIND_PROMPT, KIND_MESSAGE)

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_NOTE = "note"

#: 目标载体（按类型分流：规则/偏好 → 知识卡片；会话 → 长期记忆）
CARRIER_CARD = "knowledge_card"
CARRIER_MEMORY = "memory_note"
CARRIERS: Tuple[str, ...] = (CARRIER_CARD, CARRIER_MEMORY)
CARRIER_LABELS: Dict[str, str] = {
    CARRIER_CARD: "知识卡片",
    CARRIER_MEMORY: "长期记忆",
}

#: 记录处置状态
STATUS_KEEP = "keep"
STATUS_BLOCKED = "blocked"  # 命中闸门且无法可靠脱敏 => 拒绝该条

#: 极短消息阈值（与只读盘点口径一致：<40 字且无信息量判为噪音）
SHORT_TEXT_CHARS = 40


# ════════════════════════════════════════════════════════════
#  错误（HTTP 层只做映射，不在域模块 import flask）
# ════════════════════════════════════════════════════════════


class MemoryImportError(Exception):
    """导入域错误基类（status/code/title 供 HTTP 层直接映射）。"""

    status: int = 500
    code: str = "memory-import-error"
    title: str = "外部记忆导入失败"

    def __init__(self, message: str, *, detail: str = "",
                 status: Optional[int] = None, code: Optional[str] = None,
                 title: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = str(message)
        self.detail = str(detail or message)
        if status is not None:
            self.status = int(status)
        if code is not None:
            self.code = str(code)
        if title is not None:
            self.title = str(title)


class SourceNotFound(MemoryImportError):
    status = 404
    code = "source-not-found"
    title = "来源不存在"


class PlanNotFound(MemoryImportError):
    """预览快照缺失/过期：commit 必须基于一次真实 preview。"""

    status = 409
    code = "plan-not-found"
    title = "预览计划不存在"


class ConfirmationRequired(MemoryImportError):
    """真写入必须 confirm=True；默认 dry-run 只回放将做什么。"""

    status = 400
    code = "confirmation-required"
    title = "需要显式确认"


class UnknownBatch(MemoryImportError):
    status = 400
    code = "unknown-batch"
    title = "批次不存在"


# ════════════════════════════════════════════════════════════
#  记录
# ════════════════════════════════════════════════════════════


def digest_id(*parts: Any, length: int = 16) -> str:
    """稳定短 id：同一组输入恒等（用于 record_id / batch_id）。"""
    raw = "\x1f".join(str(p) for p in parts).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:length]


def normalize_text(text: str) -> str:
    """去重键用的归一化：小写 + 压空白（**不改原文**）。"""
    return " ".join(str(text or "").lower().split())


def text_chars(text: str) -> int:
    return len(str(text or ""))


@dataclass
class MemoryRecord:
    """一条归一化后的外部记忆。

    origin 只记录**来源标识**（文件路径 / 键名前缀），不携带正文；
    status / denoise_reason / redactions 是处置轨迹，preview 必须
    如实展示（命中数可见、明文不可见）。
    """

    source: str
    session_id: str
    ts: str
    role: str
    text: str
    project: str = ""
    kind: str = KIND_MESSAGE
    origin: str = ""
    status: str = STATUS_KEEP
    denoise_reason: str = ""
    redactions: Dict[str, int] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.source = str(self.source or "").strip().lower()
        self.kind = str(self.kind or KIND_MESSAGE).strip().lower()
        self.role = str(self.role or ROLE_USER).strip().lower()
        if self.source not in SOURCES:
            raise ValueError(f"未知来源: {self.source!r}")
        if self.kind not in KINDS:
            raise ValueError(f"未知记录类型: {self.kind!r}")
        self.text = str(self.text or "")
        self.record_id = self.compute_id()

    # -- 身份 / 去重键 --

    def compute_id(self) -> str:
        return "mr_" + digest_id(self.source, self.session_id, self.ts, self.role,
                                 self.origin, self.text)

    @property
    def dedupe_key(self) -> str:
        """去重键 = 角色 + 归一化文本（跨会话重复只保留 1 条）。"""
        return f"{self.role}\x1f{normalize_text(self.text)}"

    @property
    def chars(self) -> int:
        return text_chars(self.text)

    @property
    def is_blocked(self) -> bool:
        return self.status == STATUS_BLOCKED

    def to_public(self, *, limit: int = 0) -> Dict[str, Any]:
        """对外投影（limit>0 时截断样例；调用方决定是否显示正文）。"""
        text = self.text if limit <= 0 else self.text[:limit]
        return {
            "record_id": self.record_id,
            "source": self.source,
            "kind": self.kind,
            "role": self.role,
            "session_id": self.session_id,
            "ts": self.ts,
            "project": self.project,
            "origin": self.origin,
            "chars": self.chars,
            "status": self.status,
            "denoise_reason": self.denoise_reason,
            "redactions": dict(self.redactions),
            "text": text,
        }

    def to_snapshot(self) -> Dict[str, Any]:
        """预览快照用的完整投影（commit 据此重建记录）。"""
        data = self.to_public()
        data["meta"] = dict(self.meta)
        return data

    @classmethod
    def from_snapshot(cls, data: Dict[str, Any]) -> "MemoryRecord":
        return cls(
            source=str(data.get("source") or ""),
            session_id=str(data.get("session_id") or ""),
            ts=str(data.get("ts") or ""),
            role=str(data.get("role") or ROLE_USER),
            text=str(data.get("text") or ""),
            project=str(data.get("project") or ""),
            kind=str(data.get("kind") or KIND_MESSAGE),
            origin=str(data.get("origin") or ""),
            status=str(data.get("status") or STATUS_KEEP),
            denoise_reason=str(data.get("denoise_reason") or ""),
            redactions={str(k): int(v) for k, v in (data.get("redactions") or {}).items()},
            meta=dict(data.get("meta") or {}),
        )


@dataclass
class DroppedRecord:
    """因去重/去噪被丢弃的记录（只留证据，不留正文）。"""

    record_id: str
    source: str
    reason: str
    duplicate_of: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"record_id": self.record_id, "source": self.source,
                "reason": self.reason, "duplicate_of": self.duplicate_of}


# ════════════════════════════════════════════════════════════
#  批次
# ════════════════════════════════════════════════════════════


@dataclass
class Batch:
    """提交单位：一个载体 + 一段正文 + 覆盖的记录。

    digest 是**已经脱敏**的提案正文，直接决定落库内容；
    items 是逐条样例（截断），供人工逐批确认时核对。
    """

    batch_id: str
    carrier: str
    title: str
    source: str
    digest: str
    record_ids: List[str] = field(default_factory=list)
    project: str = ""
    date: str = ""
    role_mix: Dict[str, int] = field(default_factory=dict)
    items: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    #: digest 的来源：rule（规则式，默认）/ llm（可选 LLM 摘要）
    #: 【为什么必须是字段而不是"推断"】用户要在预览里一眼看出"这段是机器改写过的
    #: 还是原句汇总"；靠正文里的一句话去猜，等于把可用性交给文案。
    digest_source: str = "rule"
    #: 摘要元数据（模型名、输入/输出字符数、降级原因码）——只放**可对外**的键，
    #: 异常原文一律只进日志（py/stack-trace-exposure）
    digest_meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.carrier not in CARRIERS:
            raise ValueError(f"未知载体: {self.carrier!r}")

    @property
    def chars(self) -> int:
        return text_chars(self.digest)

    def to_dict(self, *, item_limit: int = 0) -> Dict[str, Any]:
        items = self.items if item_limit <= 0 else self.items[:item_limit]
        return {
            "batch_id": self.batch_id,
            "carrier": self.carrier,
            "carrier_label": CARRIER_LABELS.get(self.carrier, self.carrier),
            "title": self.title,
            "source": self.source,
            "project": self.project,
            "date": self.date,
            "chars": self.chars,
            "records": len(self.record_ids),
            "record_ids": list(self.record_ids),
            "role_mix": dict(self.role_mix),
            "tags": list(self.tags),
            "stats": dict(self.stats),
            "digest": self.digest,
            "digest_source": self.digest_source,
            "digest_meta": dict(self.digest_meta),
            "items": items,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Batch":
        return cls(
            batch_id=str(data.get("batch_id") or ""),
            carrier=str(data.get("carrier") or CARRIER_CARD),
            title=str(data.get("title") or ""),
            source=str(data.get("source") or ""),
            digest=str(data.get("digest") or ""),
            record_ids=[str(x) for x in (data.get("record_ids") or [])],
            project=str(data.get("project") or ""),
            date=str(data.get("date") or ""),
            role_mix={str(k): int(v) for k, v in (data.get("role_mix") or {}).items()},
            items=list(data.get("items") or []),
            stats=dict(data.get("stats") or {}),
            tags=[str(t) for t in (data.get("tags") or [])],
            digest_source=str(data.get("digest_source") or "rule"),
            digest_meta=dict(data.get("digest_meta") or {}),
        )


def plan_id_for(source_digest: str, batches: Sequence[Batch]) -> str:
    """plan_id = 来源指纹 + 分批（id/载体/正文）的摘要。"""
    payload = {
        "source": str(source_digest),
        "batches": [{"batch_id": b.batch_id, "carrier": b.carrier,
                     "digest": digest_id(b.digest, length=16)} for b in batches],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "mp_" + hashlib.sha256(raw).hexdigest()[:24]


__all__ = [
    "CARRIERS", "CARRIER_CARD", "CARRIER_LABELS", "CARRIER_MEMORY",
    "KINDS", "KIND_MESSAGE", "KIND_PREFERENCE", "KIND_PROMPT",
    "ROLE_ASSISTANT", "ROLE_NOTE", "ROLE_USER",
    "SHORT_TEXT_CHARS", "SOURCES", "SOURCE_CLAUDE", "SOURCE_TRAE",
    "STATUS_BLOCKED", "STATUS_KEEP",
    "Batch", "ConfirmationRequired", "DroppedRecord", "MemoryImportError",
    "MemoryRecord", "PlanNotFound", "SourceNotFound", "UnknownBatch",
    "digest_id", "normalize_text", "plan_id_for", "text_chars",
]
