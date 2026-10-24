"""归一化流水线：去噪 -> 去重 -> 脱敏 -> 分批。

顺序是刻意定的，且每个阶段都只做一件事（口径与交接单 §3.3(4) 一致）：

1. **去噪**先做：模板回显、错误回显、纯短确认、巨型日志块本身不是"记忆"，
   先把它们剔掉，去重统计才不会被噪音污染；
2. **去重**用「角色 + 归一化文本」：完全重复只留首次，跨会话重复保留 1 条；
3. **脱敏**在去重之后：同一段密钥文本只掩一次，命中计数不会因重复而虚高；
4. **分批**按"载体 + 来源 + 项目 + 周期"切。

【不易】去噪只丢弃、不改写；脱敏只掩码、不改语义。任何"顺手摘要"都可能
改变用户原意，摘要留给批次构建阶段，且只在 digest 里做（原文仍在记录里）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .models import (
    CARRIER_CARD, CARRIER_MEMORY, KIND_MESSAGE, KIND_PREFERENCE, KIND_PROMPT,
    ROLE_ASSISTANT, ROLE_USER, SHORT_TEXT_CHARS, STATUS_BLOCKED,
    Batch, DroppedRecord, MemoryRecord, digest_id, normalize_text,
)
from .redact import redact_text

#: 模板块（成对出现，可整段剥掉）
_TEMPLATE_BLOCKS = [
    re.compile(r"<ide_opened_file>.*?</ide_opened_file>", re.S),
    re.compile(r"<system-reminder>.*?</system-reminder>", re.S),
    re.compile(r"<command-name>.*?</command-name>", re.S),
    re.compile(r"<command-message>.*?</command-message>", re.S),
    re.compile(r"<command-args>.*?</command-args>", re.S),
    re.compile(r"<tool_use_error>.*?</tool_use_error>", re.S),
    re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S),
]
#: 单行即可判定的噪音标记
_NOISE_MARKERS = (
    "system-reminder", "<command-name>", "<tool_use_error>",
    "[Request interrupted", "<local-command-stdout>", "<ide_opened_file>",
)
#: 纯确认/寒暄（<40 字且命中才算噪音；不含疑问句）
_CONFIRMATIONS = {
    "好的", "好", "嗯", "嗯嗯", "可以", "行", "继续", "收到", "明白", "了解",
    "谢谢", "多谢", "ok", "okay", "yes", "y", "no", "继续吧", "开始吧", "可以了",
    "好 的", "没问题", "辛苦了", "谢谢啦", "再来", "接着来", "好的谢谢",
}
#: 日志堆栈/二进制块特征（命中过半即判为工具结果回显）
_BLOB_LINES = (
    "Traceback (most recent call last)", "File \"", "  at ", "ERROR ", "WARN ",
    "at Object.", "node_modules", "SyntaxError", "ValueError", "TypeError",
)
_BASE64_RE = re.compile(r"[A-Za-z0-9+/]{120,}={0,2}")


@dataclass
class PipelineStats:
    scanned: int = 0
    denoised: int = 0
    duplicates: int = 0
    kept: int = 0
    blocked: int = 0
    redacted_records: int = 0
    redaction_hits: Dict[str, int] = field(default_factory=dict)
    denoise_reasons: Dict[str, int] = field(default_factory=dict)
    chars_kept: int = 0
    chars_dropped: int = 0

    def to_dict(self) -> Dict[str, object]:
        return {
            "scanned": self.scanned, "denoised": self.denoised,
            "duplicates": self.duplicates, "kept": self.kept,
            "blocked": self.blocked,
            "redacted_records": self.redacted_records,
            "redaction_hits": dict(self.redaction_hits),
            "denoise_reasons": dict(self.denoise_reasons),
            "chars_kept": self.chars_kept, "chars_dropped": self.chars_dropped,
        }


def strip_templates(text: str) -> str:
    """剥掉已知模板块与噪音标记行（不改其余正文）。"""
    out = str(text or "")
    for pattern in _TEMPLATE_BLOCKS:
        out = pattern.sub(" ", out)
    lines = [line for line in out.splitlines()
             if not any(marker in line for marker in _NOISE_MARKERS)]
    return "\n".join(lines).strip()


def _looks_like_blob(text: str) -> bool:
    """工具结果回显/日志堆栈/二进制块：>2 KB 且特征过半。"""
    if len(text) < 2000:
        return False
    if _BASE64_RE.search(text):
        return True
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 8:
        return False
    hits = sum(1 for line in lines if any(mark in line for mark in _BLOB_LINES))
    return hits / len(lines) >= 0.5


def _is_short_confirmation(text: str) -> bool:
    value = normalize_text(text)
    if not value or len(value) >= SHORT_TEXT_CHARS:
        return False
    return value.strip("。.!！？?~ ") in _CONFIRMATIONS


def denoise_reason(record: MemoryRecord) -> str:
    """返回噪音原因（空串表示保留）。"""
    text = str(record.text or "").strip()
    if not text:
        return "empty"
    if _looks_like_blob(text):
        return "tool_result_blob"
    if _is_short_confirmation(text):
        return "short_confirmation"
    stripped = strip_templates(text)
    if not stripped:
        return "template"
    if stripped != text:
        # 模板只包在前后时剥掉即可（不算噪音）；剥完没内容才算
        record.text = stripped
    return ""


def denoise(records: Sequence[MemoryRecord]) -> Tuple[List[MemoryRecord], List[DroppedRecord], Dict[str, int]]:
    """去噪：丢弃模板/错误回显/短确认/巨型日志块。"""
    kept: List[MemoryRecord] = []
    dropped: List[DroppedRecord] = []
    reasons: Dict[str, int] = {}
    for record in records:
        reason = denoise_reason(record)
        if reason:
            record.denoise_reason = reason
            reasons[reason] = reasons.get(reason, 0) + 1
            dropped.append(DroppedRecord(record.record_id, record.source, reason))
            continue
        kept.append(record)
    return kept, dropped, reasons


def dedupe(records: Sequence[MemoryRecord]) -> Tuple[List[MemoryRecord], List[DroppedRecord], Dict[str, int]]:
    """去重：角色 + 归一化文本；完全重复只留首次（跨会话也只留 1 条）。"""
    seen: Dict[str, str] = {}
    kept: List[MemoryRecord] = []
    dropped: List[DroppedRecord] = []
    groups: Dict[str, int] = {}
    for record in records:
        key = record.dedupe_key
        if key in seen:
            groups[key] = groups.get(key, 1) + 1
            dropped.append(DroppedRecord(record.record_id, record.source,
                                         "duplicate", seen[key]))
            continue
        seen[key] = record.record_id
        kept.append(record)
    stats = {"groups": len(groups), "extra": len(dropped)}
    return kept, dropped, stats


def redact_records(records: Iterable[MemoryRecord]) -> Tuple[List[MemoryRecord], Dict[str, object]]:
    """逐条掩码；掩码后仍过不了闸门的记录置 blocked（不参与分批）。"""
    kept: List[MemoryRecord] = []
    blocked: List[MemoryRecord] = []
    hits: Dict[str, int] = {}
    redacted = 0
    for record in records:
        outcome = redact_text(record.text)
        if outcome.hits:
            redacted += 1
            record.text = outcome.text
            record.redactions = dict(outcome.hits)
            for name, count in outcome.hits.items():
                hits[name] = hits.get(name, 0) + count
        if outcome.blocked:
            record.status = STATUS_BLOCKED
            record.meta = {**record.meta, "blocked_reason": outcome.reason,
                           "gate_forms": outcome.gate_forms}
            blocked.append(record)
            continue
        kept.append(record)
    return kept, {"masked_records": redacted, "hits": hits,
                  "blocked": len(blocked),
                  "blocked_samples": [r.to_public(limit=0) for r in blocked[:5]],
                  "blocked_reasons": sorted({str(r.meta.get("blocked_reason", ""))
                                             for r in blocked})}


def period_of(ts: str) -> str:
    """时间戳 -> 周期键（YYYY-MM）；空/异常归 unknown。"""
    text = str(ts or "")
    if len(text) >= 7 and text[4] == "-":
        return text[:7]
    return "unknown"


def _summary_line(text: str, limit: int = 60) -> str:
    single = " ".join(str(text or "").split())
    if len(single) > limit:
        single = single[:limit] + "…"
    return single


def _batch_id(carrier: str, source: str, scope: str, period: str) -> str:
    return "mb_" + digest_id(carrier, source, scope, period, length=12)


def build_batches(records: Sequence[MemoryRecord], *,
                  max_items: int = 8, digest_chars: int = 6000) -> List[Batch]:
    """把记录分批：偏好->卡片；提问轨迹/会话->长期记忆（按项目+周期）。"""
    groups: Dict[Tuple[str, str, str, str], List[MemoryRecord]] = {}
    for record in records:
        if record.kind == KIND_PREFERENCE:
            carrier, scope, period = CARRIER_CARD, "user", "static"
        elif record.kind == KIND_PROMPT:
            carrier, scope, period = CARRIER_MEMORY, "prompts", period_of(record.ts)
        else:
            carrier, scope, period = CARRIER_MEMORY, record.project or "unknown", period_of(record.ts)
        groups.setdefault((carrier, record.source, scope, period), []).append(record)

    batches: List[Batch] = []
    for (carrier, source, scope, period), items in sorted(groups.items()):
        items = sorted(items, key=lambda r: (r.ts or "", r.record_id))
        digest = _compose_digest(carrier, source, scope, period, items,
                                 limit=digest_chars)
        role_mix: Dict[str, int] = {}
        for record in items:
            role_mix[record.role] = role_mix.get(record.role, 0) + 1
        if carrier == CARRIER_CARD:
            title = f"{source} 偏好与规则（外部记忆导入）"
        elif scope == "prompts":
            title = f"{source} 提问轨迹 {period}"
        else:
            title = f"{source} 会话摘要 {scope} {period}"
        batches.append(Batch(
            batch_id=_batch_id(carrier, source, scope, period),
            carrier=carrier, title=title, source=source, digest=digest,
            record_ids=[r.record_id for r in items], project=scope, date=period,
            role_mix=role_mix,
            items=[r.to_public(limit=120) for r in items[:max_items]],
            stats={"records": len(items), "chars": sum(r.chars for r in items),
                   "prompts": sum(1 for r in items if r.kind == KIND_PROMPT),
                   "messages": sum(1 for r in items if r.kind == KIND_MESSAGE),
                   "preferences": sum(1 for r in items if r.kind == KIND_PREFERENCE)},
            tags=[source, carrier, scope, period],
        ))
    return batches


def _compose_digest(carrier: str, source: str, scope: str, period: str,
                    items: Sequence[MemoryRecord], *, limit: int = 3500) -> str:
    """规则式摘要（不调 LLM）：保留可核对的原句，不编造结论。"""
    head = (f"# {source} {scope} {period}（外部记忆导入）\n\n"
            f"> 共 {len(items)} 条；本段由规则汇总，可直接删除单条后再提交。\n")
    lines: List[str] = []
    if carrier == CARRIER_CARD:
        for record in items:
            body = str(record.text or "").strip()
            if body:
                lines.append(body)
    elif scope == "prompts":
        for record in items:
            ts = (record.ts or "")[11:16]
            lines.append(f"- {ts} {_summary_line(record.text, 70)}")
    else:
        per_day: Dict[str, List[MemoryRecord]] = {}
        for record in items:
            per_day.setdefault((record.ts or "")[:10] or "unknown", []).append(record)
        for day, day_items in sorted(per_day.items()):
            users = [r for r in day_items if r.role == ROLE_USER]
            assistants = [r for r in day_items if r.role == ROLE_ASSISTANT]
            asks = [_summary_line(r.text, 80) for r in users[:3]]
            # 【口径】每天最多保留 3 条原话：再多就成了"搬运转录"，不是摘要
            detail = " / ".join(a for a in asks if a)
            lines.append(f"- {day}：user {len(users)} / assistant {len(assistants)}"
                         + (f"；诉求：{detail}" if detail else ""))
    text = head + "\n" + "\n".join(lines) + "\n"
    if len(text) <= limit:
        return text
    # 截断按行边界切，并在结尾如实报出"还剩多少没进正文"
    kept_lines: List[str] = []
    size = len(head) + 2
    for line in lines:
        if size + len(line) + 1 > limit - 80:
            break
        kept_lines.append(line)
        size += len(line) + 1
    omitted = len(lines) - len(kept_lines)
    tail = f"\n…（受篇幅限制，另有 {omitted} 行未列入本段；记录仍在批次里可核对）\n"
    return head + "\n" + "\n".join(kept_lines) + "\n" + tail


@dataclass
class PipelineResult:
    kept: List[MemoryRecord] = field(default_factory=list)
    blocked: List[MemoryRecord] = field(default_factory=list)
    dropped: List[DroppedRecord] = field(default_factory=list)
    batches: List[Batch] = field(default_factory=list)
    stats: PipelineStats = field(default_factory=PipelineStats)
    redaction: Dict[str, object] = field(default_factory=dict)


def run(records: Sequence[MemoryRecord]) -> PipelineResult:
    """完整流水线（去噪 -> 去重 -> 脱敏 -> 分批）。"""
    stats = PipelineStats(scanned=len(records))
    denoised, dropped_noise, reasons = denoise(records)
    stats.denoised = len(dropped_noise)
    stats.denoise_reasons = dict(reasons)
    stats.chars_dropped += sum(r.chars for r in records if r.denoise_reason)
    dropped: List[DroppedRecord] = list(dropped_noise)

    unique, dropped_dup, dup_stats = dedupe(denoised)
    stats.duplicates = int(dup_stats.get("extra", 0))
    dropped.extend(dropped_dup)

    kept, redaction = redact_records(unique)
    stats.kept = len(kept)
    stats.blocked = int(redaction.get("blocked", 0))
    stats.redacted_records = int(redaction.get("masked_records", 0))
    stats.redaction_hits = dict(redaction.get("hits") or {})
    stats.chars_kept = sum(r.chars for r in kept)
    blocked = [r for r in unique if r.is_blocked]
    batches = build_batches(kept)
    return PipelineResult(kept=kept, blocked=blocked, dropped=dropped,
                          batches=batches, stats=stats, redaction=redaction)


__all__ = [
    "PipelineResult", "PipelineStats", "build_batches", "dedupe",
    "denoise", "denoise_reason", "period_of", "redact_records", "run",
    "strip_templates",
]
