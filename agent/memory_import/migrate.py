"""把 §10 之前写入消息流的「外部记忆导入」迁到向量记忆（迁移器）。

背景（交接单 §10）：外部记忆导入最初写的是长期记忆消息流（`/api/memory/manual`），
落盘 role=user。那等于把外部转录伪装成"用户在本机说过的话"，会被 get_context /
压缩器当成本机对话折进滚动摘要，且语义检索搜不到。载体已改为向量记忆，
本模块负责把**已经写进 messages.jsonl 的存量**搬过去：

    读向量 → 登记 vector_ids → 删原消息 → 标 migrated → 写审计

【不双写】同一时刻只允许一份事实源：
1. 先写向量并**立刻**把 vector_ids 记进批次清单（崩溃后仍可按批回滚）；
2. 再按 msg_id 物理删原消息；
3. 删除失败则**补偿删除刚写的向量**并如实报 manual，清单保持"未迁移"（可重试）。
中间态用 `migration_state` 标明（pending_delete / done / failed），
不让"看起来成功"的状态留在清单里。

【为什么不用"按内容重算 digest"】迁移要搬的是**当时写进去的正文**，不是现在的规则式
重算结果：重算会悄悄改内容（摘要规则变过、LLM 摘要开关变过），等于二次创作。
故以 messages.jsonl 里的实际文本为准（去掉 `[手动记忆·优先级:low] ` 前缀）。

【重复时间戳】msg_id 就是落盘 timestamp，而时间戳可能撞（实测 2026-10-24 那批里
mb_c1624e74c7dd 与 mb_02fde7f96c1b 同一条 timestamp）。按 id 删除会一次删掉两行，
故"删掉的条数 > 1"时其余批次会读不到自己的行 —— 这时如实标 migrated（它的正文
已经不在消息流里了）并记 `shared_timestamp`，不假装是它自己删的。
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import service, targets
from .models import CARRIER_MEMORY

logger = logging.getLogger(__name__)

#: messages.jsonl 里手动记忆的统一前缀（`/api/memory/manual` 写入时加的）
MANUAL_PREFIX_RE = re.compile(r"^\[手动记忆·优先级:[^\]]*\]\s*")

#: 导入正文的头：`[外部记忆导入 mb_xxx | 标题]`
IMPORT_HEAD_RE = re.compile(r"^\[外部记忆导入 (mb_[0-9a-f]{12})\s*\|\s*([^\]]*)\]\s*")

DEFAULT_MESSAGES = Path(__file__).resolve().parents[2] / "data" / "messages.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_messages(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """读 messages.jsonl（坏行跳过：不能因为一行脏数据就整库迁不了）。"""
    target = Path(path) if path else DEFAULT_MESSAGES
    rows: List[Dict[str, Any]] = []
    if not target.is_file():
        return rows
    with open(target, "r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                rows.append(json.loads(text))
            except json.JSONDecodeError:
                continue
    return rows


def split_import_content(content: str) -> Optional[Dict[str, str]]:
    """把一条手动记忆拆成 (batch_id, title, body)；不是导入的返回 None。"""
    body = MANUAL_PREFIX_RE.sub("", str(content or ""))
    head = IMPORT_HEAD_RE.match(body)
    if head is None:
        return None
    batch_id, title = head.group(1), head.group(2)
    return {"batch_id": batch_id, "title": title.strip(),
            "body": body[head.end():].lstrip("\n")}


def index_messages(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """batch_id -> {msg_id, content, chars}（同一批次出现多次时保留最后一条）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        parsed = split_import_content(str(row.get("content") or ""))
        if parsed is None:
            continue
        out[parsed["batch_id"]] = {
            "batch_id": parsed["batch_id"],
            "msg_id": str(row.get("timestamp") or ""),
            "title": parsed["title"],
            "content": parsed["body"],
            "chars": len(parsed["body"]),
        }
    return out


def _plan_batch_meta(plan_id: str, batch_id: str, root: Optional[Path]) -> Dict[str, Any]:
    """从计划快照取 source/period/digest_source（老清单里没有这三样）。"""
    if not plan_id:
        return {}
    try:
        plan = service.load_plan(plan_id, root=root)
    except Exception:  # noqa: BLE001 快照缺失不是致命：退回从标题解析
        return {}
    for item in plan.get("batches") or []:
        if str(item.get("batch_id") or "") == batch_id:
            return {"origin": str(item.get("source") or ""),
                    "period": str(item.get("date") or ""),
                    "digest_source": str(item.get("digest_source") or "rule")}
    return {}


def _all_manifests(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """全部批次清单（不论载体/迁移状态）；损坏或文件名不合规的跳过。"""
    directory = service.batch_dir(root)
    out: List[Dict[str, Any]] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("mb_*.json")):
        if not path.is_file() or not service.BATCH_ID_RE.match(path.stem):
            continue
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def _pending_manifests(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """尚未迁到向量的 memory_note 批次清单（读 batches/*.json）。"""
    directory = service.batch_dir(root)
    out: List[Dict[str, Any]] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("mb_*.json")):
        if not path.is_file() or not service.BATCH_ID_RE.match(path.stem):
            continue
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(entry.get("carrier") or "") != CARRIER_MEMORY:
            continue
        detail = dict(entry.get("detail") or {})
        if detail.get("vector_ids") or entry.get("migrated"):
            continue  # 已经是向量态（或已迁移完），不要重复搬
        entry["_path"] = str(path)
        out.append(entry)
    return out


def scan(*, root: Optional[Path] = None,
         messages_path: Optional[Path] = None) -> Dict[str, Any]:
    """只读盘点：哪些批次要迁、迁什么、有没有对不上的孤儿。

    【为什么把"清单"和"消息"分开数】两边都可能有单边残留：
    清单说写过、消息里却没有（可能已被按 id 删掉）；消息里有导入前缀、
    清单却没有（手工复制/旧版本）。前者只能报 manual，后者不归本模块管。
    """
    batches = []
    missing_in_messages = []
    for entry in _pending_manifests(root):
        batch_id = str(entry.get("batch_id") or "")
        found = index_messages(read_messages(messages_path)).get(batch_id)
        if not found:
            missing_in_messages.append(batch_id)
            continue
        meta = _plan_batch_meta(str(entry.get("plan_id") or ""), batch_id, root)
        batches.append({
            "batch_id": batch_id,
            "title": str(entry.get("title") or found["title"]),
            "msg_id": found["msg_id"],
            "chars": found["chars"],
            "records": int(entry.get("records") or 0),
            "origin": meta.get("origin") or "",
            "period": meta.get("period") or "",
            "digest_source": meta.get("digest_source") or str(entry.get("digest_source") or "rule"),
        })
    # 孤儿判定要用**全部**清单（含已迁移/已有 vector_ids 的），否则迁移完成的批次
    # 会被自己的 dry-run 报成"消息有、清单没有"
    manifest_ids = {str(e.get("batch_id") or "") for e in _all_manifests(root)}
    orphans = sorted(bid for bid in index_messages(read_messages(messages_path))
                     if bid not in manifest_ids)
    return {
        "pending": batches,
        "missing_in_messages": sorted(missing_in_messages),
        "messages_without_manifest": orphans,
        "count": len(batches),
    }


def _metadata(batch: Dict[str, Any]) -> Dict[str, Any]:
    """向量 metadata：**与 §10 第 2 步写入口径一致**，多带迁移溯源键。"""
    return {
        "source": targets.VECTOR_SOURCE,
        "batch_id": batch["batch_id"],
        "origin": batch.get("origin") or "",
        "period": batch.get("period") or "",
        "digest_source": batch.get("digest_source") or "rule",
        "title": batch.get("title") or "",
        "migrated_from": "messages.jsonl",
    }


def migrate(*, commit: bool = False, confirm: bool = False,
            root: Optional[Path] = None,
            messages_path: Optional[Path] = None,
            vector_sink: Optional[Callable[..., Any]] = None,
            delete_message: Optional[Callable[..., Any]] = None,
            delete_vector: Optional[Callable[[str], Any]] = None,
            probe_message: Optional[Callable[[str], Any]] = None,
            audit_fn: Optional[Callable[..., Any]] = None,
            flush: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    """执行迁移。默认 dry-run；真迁必须 `commit=True` 且 `confirm=True`。

    【顺序为什么是"先写向量、再登记、再删消息"】三步都可能崩：
    写向量后没登记 ⇒ 回滚找不到它；登记后没删消息 ⇒ 双写。
    故每次写把状态落到清单（pending_delete），失败时按状态补偿，不靠"应该没问题"。
    """
    plan = scan(root=root, messages_path=messages_path)
    if not commit:
        return {"ok": True, "dry_run": True, "would": plan["pending"],
                "missing_in_messages": plan["missing_in_messages"],
                "messages_without_manifest": plan["messages_without_manifest"],
                "requires_confirm": True, "applied": False}
    if confirm is not True:
        from .models import ConfirmationRequired
        raise ConfirmationRequired(
            "迁移存量批次需要显式 confirm=true",
            detail="先用 dry-run 核对 would 清单；迁移会物理删除 messages.jsonl 里的原行")

    # 正文只在服务端内存里从 messages.jsonl 取：scan 的输出会进 dry-run 响应，
    # 不能把明文记忆再吐一遍（与 preview 的"快照只存脱敏 digest"同一条纪律）
    msg_index = index_messages(read_messages(messages_path))
    sink = vector_sink or targets._default_vector_sink
    deleter = delete_message or targets.delete_memory_note
    vector_eraser = delete_vector or targets.delete_vector_item
    # 探针 = 只读的按 id 查找（DELETE 的 dry-run 形态），用于补偿删向量前确认
    # "原消息到底还在不在"。默认复用 delete_memory_note(dry_run=True)。
    prober = probe_message or (lambda msg_id: targets.delete_memory_note(msg_id, dry_run=True))
    audited = audit_fn or _default_audit
    flushed = flush or service._flush_audit

    migrated: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for batch in plan["pending"]:
        entry_path = service.batch_dir(root) / f"{batch['batch_id']}.json"
        try:
            entry = json.loads(entry_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            failed.append({"batch_id": batch["batch_id"], "error": "批次清单不可读"})
            continue
        detail = dict(entry.get("detail") or {})
        meta = _metadata(batch)
        content = str((msg_index.get(batch["batch_id"]) or {}).get("content") or "")
        if not content:
            failed.append({"batch_id": batch["batch_id"], "error": "消息正文为空，跳过"})
            continue
        try:
            written = sink(content, metadata=meta) or {}
        except Exception as exc:  # noqa: BLE001 写失败不阻断其余批次
            logger.warning("迁移：写向量失败 batch=%s", batch["batch_id"], exc_info=True)
            failed.append({"batch_id": batch["batch_id"],
                           "error": f"向量写入失败：{type(exc).__name__}"})
            continue
        vector_ids = [str(v) for v in ((written.get("detail") or {}).get("vector_ids") or []) if str(v)]
        if not vector_ids:
            failed.append({"batch_id": batch["batch_id"], "error": "向量写入未返回 item_id"})
            continue
        # ① 先登记：从这一刻起这条向量可被按批回滚
        detail["vector_ids"] = vector_ids
        detail["origin"] = meta["origin"]
        detail["period"] = meta["period"]
        entry["detail"] = detail
        entry["migrated"] = False
        entry["migration_state"] = "pending_delete"
        entry["migration_vectors"] = vector_ids
        _atomic_write(entry_path, json.dumps(entry, ensure_ascii=False, indent=2))
        # ② 再删原消息
        try:
            deleted = deleter(batch["msg_id"])
        except Exception as exc:  # noqa: BLE001 删除失败要补偿，不许留双写
            logger.warning("迁移：删原消息失败 batch=%s", batch["batch_id"], exc_info=True)
            # 【补偿前必须确认原消息是否还在】DELETE 可能"已生效但响应超时/断连"，
            # 此时消息已经消失；若照旧补偿删掉向量，这一批的正文就两边都没了
            # （2026-10-25 首次迁移就是这样丢了一条，见模块 docstring 与 §8 陷阱 28）。
            still_there = True
            try:
                probe = prober(batch["msg_id"]) or {}
                still_there = (bool(probe.get("found", True))
                               if isinstance(probe, dict) else True)
            except Exception:  # noqa: BLE001 探不到按"还在"处理（宁可留双写，也不删唯一副本）
                logger.warning("迁移：探测原消息失败 batch=%s（按仍在处理）",
                               batch["batch_id"], exc_info=True)
                still_there = True
            if not still_there:
                # 消息其实已经没了 ⇒ 目标状态达成：保留向量、按完成收尾，不补偿
                entry["migrated"] = True
                entry["migration_state"] = "done"
                entry["migrated_at"] = _now_iso()
                entry["ref"] = "vector/" + vector_ids[0]
                detail["note"] = (f"delete_uncertain_but_gone: {type(exc).__name__}；"
                                  "原消息已不在，保留向量")
                entry["detail"] = detail
                _atomic_write(entry_path, json.dumps(entry, ensure_ascii=False, indent=2))
                audit_ref = audited(batch, vector_ids, False)
                migrated.append({"batch_id": batch["batch_id"], "vector_ids": vector_ids,
                                 "msg_id": batch["msg_id"], "deleted": False,
                                 "shared_timestamp": True, "audit": audit_ref})
                continue
            compensated = _compensate(vector_ids, vector_eraser)
            failed.append({"batch_id": batch["batch_id"],
                           "error": (f"删除原消息失败：{type(exc).__name__}；"
                                     + ("已回滚刚写的向量" if compensated
                                        else "向量回滚也失败，需人工处理（见 migration_state）"))})
            entry["migration_state"] = "failed" if compensated else "failed_needs_manual"
            if compensated:
                detail.pop("vector_ids", None)
                entry["detail"] = detail
            _atomic_write(entry_path, json.dumps(entry, ensure_ascii=False, indent=2))
            continue
        # ③ 收尾：标 migrated + 审计
        entry["migrated"] = True
        entry["migration_state"] = "done"
        entry["migrated_at"] = _now_iso()
        # ref 也跟着搬家：界面要知道"这条批次现在落在哪"，不能还指向已删的消息流
        entry["ref"] = "vector/" + vector_ids[0]
        _atomic_write(entry_path, json.dumps(entry, ensure_ascii=False, indent=2))
        # 删除回执可能是信封 dict，也可能只是布尔；两种都认，未知按"已删"记
        deleted_ok = (bool(deleted.get("deleted")) if isinstance(deleted, dict)
                      else bool(deleted))
        audit_ref = audited(batch, vector_ids, deleted_ok)
        migrated.append({
            "batch_id": batch["batch_id"], "vector_ids": vector_ids,
            "msg_id": batch["msg_id"],
            # deleted=False 多半是"同一 timestamp 已被别的批次删掉"（见模块 docstring）。
            # 这不是失败：正文确实已不在消息流里；单独记 shared_timestamp 便于事后核对
            "deleted": deleted_ok,
            "shared_timestamp": (not deleted_ok),
            "audit": audit_ref,
        })
    flushed()
    return {
        "ok": not failed and bool(migrated),
        "dry_run": False, "migrated": migrated, "failed": failed,
        "missing_in_messages": plan["missing_in_messages"],
        "requires_confirm": False, "applied": bool(migrated),
    }


def _compensate(vector_ids: Sequence[str],
                eraser: Optional[Callable[[str], Any]] = None) -> bool:
    """补偿删除刚写的向量（删除原消息失败时用）。全部成功才返回 True。"""
    erase = eraser or targets.delete_vector_item
    ok = True
    for item_id in vector_ids:
        try:
            erase(item_id)
        except Exception:  # noqa: BLE001 补偿失败要如实反映
            logger.warning("迁移补偿：删除向量失败 id=%s", item_id, exc_info=True)
            ok = False
    return ok


def _default_audit(batch: Dict[str, Any], vector_ids: Sequence[str],
                   deleted: bool) -> Dict[str, Any]:
    """写审计：迁移是"移动事实源"，必须能回答"哪一批、搬到哪、原件删没删"。"""
    try:
        from agent.audit.facade import audit

        entry = audit.record(
            "memory_import.migrate", actor="agent.memory_import.migrate",
            subject=f"batch:{batch['batch_id']}",
            payload={"batch_id": batch["batch_id"], "vector_ids": list(vector_ids),
                     "msg_id": batch["msg_id"], "records": int(batch.get("records") or 0),
                     "chars": int(batch.get("chars") or 0),
                     "origin": batch.get("origin") or "",
                     "period": batch.get("period") or "",
                     "message_deleted": bool(deleted)},
            source="agent",
        )
        if entry is None:
            return {"seq": 0}
        return {"seq": int(getattr(entry, "seq", 0) or 0)}
    except Exception:  # noqa: BLE001 审计失败不掩盖业务结果
        return {"seq": 0}


__all__ = ["DEFAULT_MESSAGES", "index_messages", "migrate", "read_messages", "scan",
           "split_import_content"]