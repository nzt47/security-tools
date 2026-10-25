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
import logging
import os
import re
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

logger = logging.getLogger(__name__)

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
            max_records: Optional[int] = None,
            use_llm: bool = False,
            llm: Optional[Any] = None) -> Dict[str, Any]:
    """只读预览：采集 -> 去噪/去重/脱敏/分批 -> 计划快照。

    use_llm=True 时尝试用 LLM 改写**会话批次**的 digest（失败/超预算一律回退规则式，
    原因写进快照的 digest.reason 与每个批次的 digest_meta）。默认 False：不产生任何
    外部调用与费用，输出与历史版本逐字节相同。
    """
    collected = collect(sources=sources, claude_dir=claude_dir, appdata=appdata,
                        include_transcripts=include_transcripts,
                        max_records=max_records)
    digest_fn, digest_info = _build_digest_fn(use_llm=use_llm, llm=llm)
    result = pipeline.run(collected.records, digest_fn=digest_fn)
    batches = result.batches
    digest_info = _finish_digest_info(digest_info, batches)
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
        "digest": digest_info,
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


def _build_digest_fn(*, use_llm: bool,
                     llm: Optional[Any] = None) -> tuple:
    """按需构造 LLM 摘要器：返回 (digest_fn|None, digest_info)。

    【为什么把「未配置」也算正常返回】预览的首要职责是"能把东西摆出来给人看"，
    LLM 只是可选的加工方式；缺配置不是错误，但**必须如实告知**（reason 进快照）。
    """
    info: Dict[str, Any] = {"requested": bool(use_llm), "mode": "rule",
                            "llm_available": False, "reason": "",
                            "llm_batches": 0, "rule_batches": 0, "budget": {}}
    if not use_llm:
        return None, info
    from . import summarize  # 惰性：不在包初始化时牵出 LLM 依赖

    client = llm
    if client is None:
        available, reason = summarize.llm_available()
        info["llm_available"] = bool(available or llm is not None)
        if not available:
            info["reason"] = reason
            return None, info
        client = summarize.build_default_llm()
        if client is None:
            info["reason"] = summarize.REASON_UNCONFIGURED
            return None, info
    info["llm_available"] = True
    fn = summarize.make_digest_fn(client, budget=summarize.budget_from_env())
    # 预算是**活对象**：调用次数要等批次跑完才准，故先挂在 info 上，
    # 由 _finish_digest_info 统一转成可 JSON 的 dict 后移除。
    info["_budget"] = getattr(fn, "budget", None)
    return fn, info


def _finish_digest_info(info: Dict[str, Any], batches: Sequence[Batch]) -> Dict[str, Any]:
    """补齐摘要统计：几批走了 LLM、几批回退、预算用了多少。"""
    llm_batches = sum(1 for b in batches if getattr(b, "digest_source", "rule") == "llm")
    out = dict(info)
    budget = out.pop("_budget", None)
    if budget is not None and hasattr(budget, "to_dict"):
        out["budget"] = budget.to_dict()
    out["llm_batches"] = int(llm_batches)
    out["rule_batches"] = int(len(batches) - llm_batches)
    out["mode"] = "llm" if llm_batches else "rule"
    if out.get("requested") and not llm_batches and not out.get("reason"):
        # 请求了但没有一批用上：给出可执行的下一步（不是内部错误码堆砌）
        reasons = {str((getattr(b, "digest_meta", {}) or {}).get("reason") or "")
                   for b in batches}
        reasons.discard("")
        if not reasons:
            out["reason"] = "llm_no_message_batch"
        elif "llm_budget_exhausted" in reasons:
            out["reason"] = "llm_budget_exhausted"
        elif "llm_input_too_large" in reasons:
            out["reason"] = "llm_input_too_large"
        else:
            out["reason"] = "llm_fallback"
    return out


def _count_reasons(dropped: Iterable[Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in dropped:
        reason = str(getattr(item, "reason", "") or item.get("reason", ""))
        out[reason] = out.get(reason, 0) + 1
    return out


#: plan_id / batch_id 的合法形态（**路径安全闸门**）
#:
#: 【不易】这两个 id 会直接拼进文件路径。plan_id 来自 HTTP 请求体，未校验时
#: "../.." 一类取值可让读/写越出 data/ingestion/memory_import（CodeQL
#: "Uncontrolled data used in path expression"，本 PR 的 CI 实测告警）。
#: 格式是确定性的（见 models.plan_id_for / Batch 的 _batch_id），故用白名单正则。
PLAN_ID_RE = re.compile(r"^mp_[0-9a-f]{24}$")
BATCH_ID_RE = re.compile(r"^mb_[0-9a-f]{12}$")


def _find_by_stem(directory: Path, pattern: str, stem: str) -> Optional[Path]:
    """在目录里按文件名主干精确匹配 —— **不把调用方字符串拼进路径**。

    【为什么用"列目录 + 比较"而不是 base / f"{id}.json"】id 来自 HTTP 请求体。
    正则白名单 + resolve 前缀校验在语义上足够，但静态分析（CodeQL
    py/path-injection）不认这两种 sanitizer，会持续报"用户输入参与路径构造"。
    改成"路径来自 glob、用户输入只做等值比较"后，用户输入**不进入**路径表达式，
    语义也更强：只有真实存在的快照才会被打开。
    """
    if not directory.is_dir():
        return None
    for candidate in sorted(directory.glob(pattern)):
        if candidate.is_file() and candidate.stem == stem:
            return candidate
    return None


def _plan_path(plan_id: str, root: Optional[Path] = None) -> Path:
    """plan_id -> 快照路径（格式闸门 + 目录内精确匹配；不存在即 PlanNotFound）。"""
    if not PLAN_ID_RE.match(str(plan_id or "")):
        raise PlanNotFound(
            f"plan_id 格式非法: {str(plan_id)[:40]!r}",
            detail="plan_id 应形如 mp_<24 位十六进制>（由 preview 生成）")
    found = _find_by_stem(plan_dir(root), "mp_*.json", str(plan_id))
    if found is None:
        raise PlanNotFound(f"找不到预览计划 {plan_id}，请先运行 preview",
                           detail="计划快照不存在（可能已清理）")
    return found


def _batch_manifest(batch_id: str, root: Optional[Path] = None) -> Path:
    """batch_id -> 批次清单路径（格式闸门 + 目录内精确匹配）。"""
    if not BATCH_ID_RE.match(str(batch_id or "")):
        raise UnknownBatch(
            f"batch_id 格式非法: {str(batch_id)[:40]!r}",
            detail="batch_id 应形如 mb_<12 位十六进制>（由 preview 生成）")
    found = _find_by_stem(batch_dir(root), "mb_*.json", str(batch_id))
    if found is None:
        raise UnknownBatch(f"没有批次清单: {batch_id}", detail="批次清单不存在")
    return found


def load_plan(plan_id: str, *, root: Optional[Path] = None) -> Dict[str, Any]:
    """读回计划快照；缺失即 PlanNotFound（commit 必须基于真实 preview）。"""
    path = _plan_path(plan_id, root)
    if not path.is_file():
        raise PlanNotFound(
            f"找不到预览计划 {plan_id}，请先运行 preview",
            detail=f"期望文件: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # 异常原文只进日志：HTTP 层会把它当"栈信息暴露"（CodeQL）看
        logger.warning("预览计划读取失败 %s: %s", path.name, exc)
        raise PlanNotFound("预览计划读取失败", detail="快照文件损坏或不可读")


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
           status_note: str = "",
           explain_errors: bool = True) -> Dict[str, Any]:
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
                       "chars": b.chars,
                       "digest_source": getattr(b, "digest_source", "rule")}
                      for b in selected],
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
            # explain_errors=False：HTTP 层用，避免把异常文本（可能含路径/键名）
            # 回给客户端（CodeQL: information exposure through an exception）；
            # 完整原因进服务日志。CLI 保留原文，排障需要。
            logger.exception("批次写入失败 batch=%s", batch.batch_id)
            failed.append({
                "batch_id": batch.batch_id, "carrier": batch.carrier,
                "error": (f"{type(exc).__name__}: {exc}" if explain_errors
                          else f"{type(exc).__name__}（详见服务日志）"),
            })
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
            # 摘要来源随批次清单一起落盘：几个月后回看"这条记忆是谁写的"，
            # 必须能区分"用户原话汇总"与"LLM 改写"，否则无法追责/复核。
            "digest_source": getattr(batch, "digest_source", "rule"),
            "digest_meta": dict(getattr(batch, "digest_meta", {}) or {}),
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


def list_batches(*, root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """列出已写入批次（读 batches/*.json 清单，按提交时间倒序）。

    【为什么单独有这个视图】回滚必须以"批次清单"为依据：清单里有 ref、载体、
    条数、审计 seq 与 plan_id，界面据此展示"这条到底写了什么、能不能自动回滚"。
    目录不存在时返回空列表（从未导入过是正常态，不是错误）。
    """
    directory = batch_dir(root)
    out: List[Dict[str, Any]] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("mb_*.json")):
        if not path.is_file() or not BATCH_ID_RE.match(path.stem):
            continue
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        carrier = str(entry.get("carrier") or "")
        detail = dict(entry.get("detail") or {})
        vector_ids = [str(v) for v in (detail.get("vector_ids") or []) if str(v)]
        msg_id = str(detail.get("msg_id") or "")
        out.append({
            "batch_id": str(entry.get("batch_id") or path.stem),
            "carrier": carrier,
            "carrier_label": CARRIER_LABELS.get(carrier, carrier),
            "title": str(entry.get("title") or ""),
            "records": int(entry.get("records") or 0),
            "chars": int(entry.get("chars") or 0),
            "ref": str(entry.get("ref") or ""),
            "plan_id": str(entry.get("plan_id") or ""),
            "committed_at": str(entry.get("committed_at") or ""),
            "audit_seq": int((entry.get("audit") or {}).get("seq") or 0),
            # 卡片凭 ref 可删；向量记忆凭写入回执里的 vector_ids 可删（§10 第 2 步）；
            # 老批次只有 msg_id（写进消息流的那些）⇒ 迁移前仍按 msg_id 删
            "rollback": ("auto"
                         if (carrier == CARRIER_CARD and entry.get("ref"))
                         or (carrier == CARRIER_MEMORY and (vector_ids or msg_id))
                         else "manual"),
            "vector_ids": vector_ids,
            # migrated=True：这一批本来是消息流，§10 第 3 步把它迁到了向量库
            "migrated": bool(entry.get("migrated")),
            "origin": str(detail.get("origin") or ""),
            "period": str(detail.get("period") or ""),
            "digest_source": str(entry.get("digest_source") or "rule"),
        })
    out.sort(key=lambda item: item.get("committed_at") or "", reverse=True)
    return out


def rollback(batch_id: str, *, confirm: bool = False, dry_run: bool = True,
             store: Any = None, root: Optional[Path] = None) -> Dict[str, Any]:
    """撤销一个批次的写入（知识卡片按 ref / 向量记忆按 vector_ids / 老批次按 msg_id）。

    【边界】只做"能确证删除"的载体，其余如实报出"需要人工处理"，绝不假装删掉了。
    §10 之后新写入的批次把向量 id 记在 detail.vector_ids；删除失败时**不删清单**，
    否则界面会立刻"看不见"这条还没回滚成功的批次。
    """
    manifest = _batch_manifest(batch_id, root)
    if not manifest.is_file():
        raise UnknownBatch(f"没有批次清单: {batch_id}", detail=str(manifest))
    entry = json.loads(manifest.read_text(encoding="utf-8"))
    ref = str(entry.get("ref") or "")
    carrier = str(entry.get("carrier") or "")
    detail = dict(entry.get("detail") or {})
    msg_id = str(detail.get("msg_id") or "")
    vector_ids = [str(v) for v in (detail.get("vector_ids") or []) if str(v)]
    if carrier == CARRIER_MEMORY and vector_ids:
        # 【2026-10-25 · §10 第 2 步】导入改走向量记忆后，回滚就是按 id 真删这些向量。
        # 404（本来就不在）算"目标状态已达成"，单独记进 missing，不冒充"删掉了"。
        if dry_run:
            # 【2026-10-25】回滚是物理删除：dry-run 必须让人**看得见内容**再确认。
            # 逐条只读回读（GET /api/vector/item/<id>）；预览失败不阻断 dry-run。
            from . import targets as _targets

            previews = []
            for item_id in vector_ids:
                try:
                    info = _targets.read_vector_item(item_id, limit=160)
                    previews.append({"id": item_id, "chars": int(info.get("chars") or 0),
                                     "preview": str(info.get("preview") or ""),
                                     "metadata": dict(info.get("metadata") or {})})
                except Exception:  # noqa: BLE001 预览失败不阻断 dry-run
                    previews.append({"id": item_id, "chars": 0, "preview": "",
                                     "metadata": {}})
            return {"ok": True, "dry_run": True, "batch_id": batch_id,
                    "carrier": carrier, "vector_ids": vector_ids,
                    "items_preview": previews,
                    "would_delete": "vector:" + ",".join(vector_ids)}
        if confirm is not True:
            raise ConfirmationRequired(
                "回滚向量记忆需要显式 confirm=true",
                detail="dry_run=true 可先看将删除哪些向量（按 vector_ids）")
        from . import targets

        deleted: List[str] = []
        missing: List[str] = []
        failed: List[str] = []
        for item_id in vector_ids:
            try:
                outcome = targets.delete_vector_item(item_id)
            except Exception as exc:  # noqa: BLE001 失败要如实报，不假装删掉了
                logger.warning("向量回滚删除失败 batch=%s id=%s", batch_id, item_id,
                               exc_info=True)
                failed.append(f"{item_id}({type(exc).__name__})")
                continue
            (deleted if outcome.get("deleted") else missing).append(item_id)
        ok = not failed
        audit_ref = _audit(Batch(batch_id=batch_id, carrier=carrier,
                                 title=str(entry.get("title") or ""),
                                 source=str(detail.get("origin") or ""),
                                 digest="", record_ids=[]),
                          {"ok": ok, "ref": "vector:" + ",".join(deleted or vector_ids)},
                          str(entry.get("plan_id") or ""))
        if ok:
            manifest.unlink(missing_ok=True)
        _flush_audit()
        result: Dict[str, Any] = {
            "ok": ok, "dry_run": False, "batch_id": batch_id, "carrier": carrier,
            "deleted": ("vector:" + ",".join(deleted)) if deleted else "",
            "missing": missing, "failed": failed, "vector_ids": vector_ids,
            "audit": audit_ref,
        }
        if not ok:
            result["manual"] = True
            result["error"] = (f"{len(failed)} 条向量删除失败；批次清单已保留，可重试"
                               "（失败的 id 见 failed）")
        return result
    if carrier == CARRIER_MEMORY and msg_id:
        # 【2026-10-25 · T3 遗留收口】长期记忆现在也能自动回了：写入时若记下了
        # msg_id（#1295 之后 `/api/memory/manual` 会回），就能按 id 物理删。
        # 老批次没有 msg_id（写入时该能力还不存在）⇒ 仍然如实报 manual，不假装能删。
        if dry_run:
            return {"ok": True, "dry_run": True, "batch_id": batch_id,
                    "carrier": carrier, "would_delete": "memory:" + msg_id}
        if confirm is not True:
            raise ConfirmationRequired(
                "回滚长期记忆需要显式 confirm=true",
                detail="dry_run=true 可先看将删除哪条记忆（按 msg_id）")
        from . import targets

        try:
            outcome = targets.delete_memory_note(msg_id)
        except Exception as exc:  # noqa: BLE001 失败要如实报，不假装删掉了
            return {"ok": False, "dry_run": False, "batch_id": batch_id,
                    "carrier": carrier, "manual": True,
                    "error": f"按 id 删除长期记忆失败：{type(exc).__name__}"}
        audit_ref = _audit(Batch(batch_id=batch_id, carrier=carrier,
                                 title=str(entry.get("title") or ""),
                                 source=str(detail.get("source") or ""),
                                 digest="", record_ids=[]),
                          {"ok": bool(outcome.get("ok")), "ref": "memory:" + msg_id},
                          str(entry.get("plan_id") or ""))
        if outcome.get("ok"):
            manifest.unlink(missing_ok=True)
        _flush_audit()
        # 404 现在是"已经不在了"：目标状态已达成，但不能写成"刚删掉"
        missing = bool(outcome.get("missing"))
        return {"ok": bool(outcome.get("ok")), "dry_run": False, "batch_id": batch_id,
                "carrier": carrier,
                "deleted": "" if missing else "memory:" + msg_id,
                "missing": [msg_id] if missing else [],
                "audit": audit_ref}
    if carrier != CARRIER_CARD or not ref:
        return {"ok": False, "dry_run": dry_run, "batch_id": batch_id,
                "carrier": carrier, "manual": True,
                "error": "该批次无法自动回滚：清单里既没有 vector_ids 也没有 msg_id"
                         "（§10 之前的老批次），需人工删除"}
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
    "list_batches", "load_plan", "plan_dir", "preview", "preview_dir",
    "rollback",
]