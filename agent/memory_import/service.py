"""导入编排：preview（默认 dry-run）/ commit（显式确认 + 审计）/ rollback。

三段式与云枢既有治理口径一致（见 agent/ingestion/governance.py）：

    预览(只读)  ->  逐批确认  ->  写入(可回滚)

**三条硬约束**（交接单 §3.4 边界）：

1. preview 绝不写任何记忆/知识库；它只写"计划快照"（已脱敏的待办清单），
   供人工核对与 commit 时比对，属于**审计凭证**而不是记忆数据；
2. commit 默认 dry_run=True；真写入必须 confirm=True，否则抛
   ConfirmationRequired（不靠前端自觉）；
3. 每个批次单独写审计（来源、载体、条数、命中、ref），并落 batch 清单，
   使"按 batch 回滚"有据可依。

【不易】预览快照里**不存记录原文**：只存已掩码的 digest 与记录 id。
这样快照本身不会变成"明文转录的第二份副本"；也正因如此，commit 只依据
用户在预览里看到的 digest 写入，不会偷偷用原文。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from . import pipeline
from .models import (
    CARRIER_CARD, CARRIER_LABELS, CARRIER_MEMORY, SOURCE_CLAUDE, SOURCE_TRAE,
    SOURCES, Batch, ConfirmationRequired, MemoryRecord, PlanNotFound,
    UnknownBatch, digest_id, plan_id_for,
)
from .readers import claude as claude_reader
from .readers import trae as trae_reader
from .targets import default_writers

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "ingestion" / "memory_import"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def plan_dir(root: Optional[Path] = None) -> Path:
    return Path(root or DEFAULT_ROOT) / "plans"


def batch_dir(root: Optional[Path] = None) -> Path:
    return Path(root or DEFAULT_ROOT) / "batches"


def preview_dir(root: Optional[Path] = None) -> Path:
    return Path(root or DEFAULT_ROOT) / "previews"


# ════════════════════════════════════════════════════════════
#  采集
# ════════════════════════════════════════════════════════════


@dataclass
class CollectResult:
    records: List[MemoryRecord]
    reports: Dict[str, Any]


def collect(*, sources: Sequence[str] = SOURCES,
            claude_dir: Optional[Path] = None,
            appdata: Optional[Path] = None,
            include_transcripts: bool = True,
            max_records: Optional[int] = None) -> CollectResult:
    """只读采集（不写任何文件、不写任何记忆）。"""
    wanted = [str(s).strip().lower() for s in (sources or SOURCES)]
    unknown = [s for s in wanted if s not in SOURCES]
    if unknown:
        raise ValueError(f"未知来源: {unknown}")
    records: List[MemoryRecord] = []
    reports: Dict[str, Any] = {}
    if SOURCE_CLAUDE in wanted:
        result = claude_reader.read_claude(claude_dir=claude_dir,
                                           include_transcripts=include_transcripts,
                                           max_records=max_records)
        records.extend(result.records)
        reports[SOURCE_CLAUDE] = result.report
    if SOURCE_TRAE in wanted:
        result = trae_reader.read_trae(appdata=appdata, max_records=max_records)
        records.extend(result.records)
        reports[SOURCE_TRAE] = result.report
    return CollectResult(records=records, reports=reports)


def _source_digest(reports: Mapping[str, Any], records: Sequence[MemoryRecord]) -> str:
    """来源指纹：来源报告的规模指标 + 记录 id 集合（换成内容也不会撞）。"""
    parts: List[str] = []
    for name in sorted(reports):
        report = reports.get(name) or {}
        parts.append(f"{name}:{report.get('total_records', '')}"
                     f":{report.get('vscdb_keys', '')}"
                     f":{(report.get('sessions') or {}).get('files', '')}")
    parts.extend(sorted(record.record_id for record in records))
    return digest_id(*parts, length=32)


# ════════════════════════════════════════════════════════════
#  预览
# ════════════════════════════════════════════════════════════


def _public_reports(reports: Mapping[str, Any]) -> Dict[str, Any]:
    """裁掉报告里的逐文件细节（预览只需规模与告警）。"""
    out: Dict[str, Any] = {}
    for name, report in (reports or {}).items():
        data = dict(report or {})
        if isinstance(data.get("leveldb"), list):
            data["leveldb"] = [
                {k: v for k, v in item.items() if k != "sample"}
                for item in data["leveldb"]
            ][:10]
        warnings = list(data.get("warnings") or [])
        data["warnings"] = warnings[:20]
        out[name] = data
    return out


def preview(*, sources: Sequence[str] = SOURCES,
            claude_dir: Optional[Path] = None,
            appdata: Optional[Path] = None,
            include_transcripts: bool = True,
            root: Optional[Path] = None,
            save: bool = True,
            max_records: Optional[int] = None) -> Dict[str, Any]:
    """只读预览：采集 -> 去噪/去重/脱敏/分批 -> 计划快照。"""
    collected = collect(sources=sources, claude_dir=claude_dir, appdata=appdata,
                        include_transcripts=include_transcripts,
                        max_records=max_records)
    result = pipeline.run(collected.records)
    batches = result.batches
    plan_id = plan_id_for(_source_digest(collected.reports, result.kept), batches)
    generated = _now_iso()
    snapshot = {
        "plan_id": plan_id,
        "generated_at": generated,
        "sources": _public_reports(collected.reports),
        "stats": result.stats.to_dict(),
        "redaction": {k: v for k, v in result.redaction.items() if k != "blocked_samples"},
        "blocked_samples": result.redaction.get("blocked_samples") or [],
        "dropped": {"total": len(result.dropped),
                    "by_reason": _count_reasons(result.dropped)},
        "batches": [b.to_dict() for b in batches],
        "requires_confirm": True,
        "dry_run": True,
        "note": "快照只含已脱敏 digest 与记录 id，不含记录原文",
    }
    path = ""
    if save:
        target = plan_dir(root) / f"{plan_id}.json"
        _atomic_write(target, json.dumps(snapshot, ensure_ascii=False, indent=2))
        path = str(target)
    public = dict(snapshot)
    public["plan_path"] = path
    return public


def _count_reasons(dropped: Iterable[Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in dropped:
        reason = str(getattr(item, "reason", "") or item.get("reason", ""))
        out[reason] = out.get(reason, 0) + 1
    return out


def load_plan(plan_id: str, *, root: Optional[Path] = None) -> Dict[str, Any]:
    """读回计划快照；缺失即 PlanNotFound（commit 必须基于真实 preview）。"""
    path = plan_dir(root) / f"{plan_id}.json"
    if not path.is_file():
        raise PlanNotFound(
            f"找不到预览计划 {plan_id}，请先运行 preview",
            detail=f"期望文件: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlanNotFound(f"预览计划读取失败: {exc}", detail=str(path))


# ════════════════════════════════════════════════════════════
#  提交
# ════════════════════════════════════════════════════════════


def _flush_audit() -> bool:
    """把审计链的待入库记录落盘（一次性进程必须做）。

    【不易】审计是"先写预留日志 + 入队、由后台线程批量入库"：长驻服务里
    没问题，但 CLI 进程写完就退，队列里的最后几条会留在 .seqjournal 里
    （实测 2026-10-24：14 批里 5 批的审计只到日志、没进 audit_chain 表）。
    flush() 同时负责"队列入库"和"预留日志收敛"两件事，缺一不可。
    """
    try:
        from agent.audit.facade import audit

        chain = getattr(audit, "chain", None)
        if chain is None or not hasattr(chain, "flush"):
            return False
        return bool(chain.flush())
    except Exception:  # noqa: BLE001 审计落盘失败不掩盖业务结果
        return False


def _audit(batch: Batch, outcome: Mapping[str, Any], plan_id: str) -> Dict[str, Any]:
    """按批次写审计；审计不可用不阻断已成功的写入（best-effort）。"""
    try:
        from agent.audit.facade import audit

        entry = audit.record(
            "memory_import.commit", actor="agent.memory_import.service",
            subject=f"batch:{batch.batch_id}",
            payload={"plan_id": plan_id, "batch_id": batch.batch_id,
                     "carrier": batch.carrier, "source": batch.source,
                     "records": len(batch.record_ids), "chars": batch.chars,
                     "ref": str(outcome.get("ref") or ""),
                     "ok": bool(outcome.get("ok"))},
            source="agent",
        )
        if entry is None:
            return {"seq": 0, "self_hash": ""}
        return {"seq": int(getattr(entry, "seq", 0) or 0),
                "self_hash": str(getattr(entry, "self_hash", "") or "")}
    except Exception:  # noqa: BLE001 审计失败不掩盖业务结果
        return {"seq": 0, "self_hash": ""}


def commit(plan_id: str, batch_ids: Optional[Sequence[str]] = None, *,
           confirm: bool = False, dry_run: bool = True,
           writers: Optional[Mapping[str, Callable[[Batch], Dict[str, Any]]]] = None,
           root: Optional[Path] = None,
           status_note: str = "") -> Dict[str, Any]:
    """写入选定批次：默认 dry-run；真写必须 confirm=True 并写审计。"""
    snapshot = load_plan(plan_id, root=root)
    all_batches = [Batch.from_dict(item) for item in snapshot.get("batches") or []]
    wanted = [str(b) for b in (batch_ids or []) if str(b)]
    if wanted:
        known = {b.batch_id for b in all_batches}
        missing = [b for b in wanted if b not in known]
        if missing:
            raise UnknownBatch(f"计划中没有这些批次: {missing}",
                               detail=f"可用批次: {sorted(known)[:20]}")
        selected = [b for b in all_batches if b.batch_id in wanted]
    else:
        selected = list(all_batches)

    if dry_run:
        return {
            "ok": True, "dry_run": True, "plan_id": plan_id,
            "would": [{"batch_id": b.batch_id, "carrier": b.carrier,
                       "carrier_label": CARRIER_LABELS.get(b.carrier, b.carrier),
                       "title": b.title, "records": len(b.record_ids),
                       "chars": b.chars} for b in selected],
            "requires_confirm": True,
            "applied": False,
        }
    if confirm is not True:
        raise ConfirmationRequired(
            "真写入外部记忆需要显式 confirm=true",
            detail="先看 preview 的 batches，再按 batch_id 逐批确认；"
                   "dry_run=true 可只回放将写什么。")

    handlers: Dict[str, Callable[[Batch], Dict[str, Any]]] = dict(writers or default_writers())
    committed: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for batch in selected:
        handler = handlers.get(batch.carrier)
        if handler is None:
            failed.append({"batch_id": batch.batch_id, "carrier": batch.carrier,
                           "error": f"没有载体的写入器: {batch.carrier}"})
            continue
        try:
            outcome = handler(batch) or {}
            outcome = {"ok": True, **dict(outcome)}
        except Exception as exc:  # noqa: BLE001 单批失败不阻断其余批次
            failed.append({"batch_id": batch.batch_id, "carrier": batch.carrier,
                           "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not outcome.get("ok"):
            failed.append({"batch_id": batch.batch_id, "carrier": batch.carrier,
                           "error": str(outcome.get("error") or "写入失败")})
            continue
        audit_ref = _audit(batch, outcome, plan_id)
        entry = {
            "batch_id": batch.batch_id, "carrier": batch.carrier,
            "carrier_label": CARRIER_LABELS.get(batch.carrier, batch.carrier),
            "title": batch.title, "records": len(batch.record_ids),
            "chars": batch.chars, "ref": str(outcome.get("ref") or ""),
            "detail": dict(outcome.get("detail") or {}),
            "audit": audit_ref, "committed_at": _now_iso(),
            "plan_id": plan_id, "note": status_note,
        }
        committed.append(entry)
        manifest = batch_dir(root) / f"{batch.batch_id}.json"
        _atomic_write(manifest, json.dumps(entry, ensure_ascii=False, indent=2))

    audited = _flush_audit()
    return {
        "audit_flushed": audited,
        "ok": not failed and bool(committed),
        "dry_run": False,
        "plan_id": plan_id,
        "committed": committed,
        "failed": failed,
        "requires_confirm": False,
        "applied": bool(committed),
    }


# ════════════════════════════════════════════════════════════
#  回滚（按 batch）
# ════════════════════════════════════════════════════════════


def rollback(batch_id: str, *, confirm: bool = False, dry_run: bool = True,
             store: Any = None, root: Optional[Path] = None) -> Dict[str, Any]:
    """撤销一个批次的写入（当前只覆盖知识卡片；长期记忆需人工删）。

    【边界】长期记忆没有按 id 删除的稳定入口，回滚只做"能确证删除"的载体，
    其余如实报出"需要人工处理"，绝不假装删掉了。
    """
    manifest = batch_dir(root) / f"{batch_id}.json"
    if not manifest.is_file():
        raise UnknownBatch(f"没有批次清单: {batch_id}", detail=str(manifest))
    entry = json.loads(manifest.read_text(encoding="utf-8"))
    ref = str(entry.get("ref") or "")
    carrier = str(entry.get("carrier") or "")
    if carrier != CARRIER_CARD or not ref:
        return {"ok": False, "dry_run": dry_run, "batch_id": batch_id,
                "carrier": carrier, "manual": True,
                "error": "该批次的载体无法自动回滚（长期记忆需人工删除）"}
    if dry_run:
        return {"ok": True, "dry_run": True, "batch_id": batch_id,
                "would_delete": ref, "carrier": carrier}
    if confirm is not True:
        raise ConfirmationRequired("回滚需要显式 confirm=true",
                                   detail="dry_run=true 可先看将删除哪张卡片")
    if store is None:
        from agent.knowledge.card import CardStore
        store = CardStore()
    deleted = bool(store.delete(ref))
    audit_ref = {}
    try:
        from agent.audit.facade import audit
        audit_ref = _audit(Batch(batch_id=batch_id, carrier=carrier, title=str(entry.get("title") or ""),
                                 source=str((entry.get("detail") or {}).get("source") or ""),
                                 digest="", record_ids=[]),
                           {"ok": deleted, "ref": ref}, str(entry.get("plan_id") or ""))
    except Exception:  # noqa: BLE001
        audit_ref = {}
    if deleted:
        manifest.unlink(missing_ok=True)
    _flush_audit()
    return {"ok": deleted, "dry_run": False, "batch_id": batch_id,
            "deleted": ref, "audit": audit_ref}


__all__ = [
    "DEFAULT_ROOT", "CollectResult", "batch_dir", "collect", "commit",
    "load_plan", "plan_dir", "preview", "preview_dir", "rollback",
]
