# -*- coding: utf-8 -*-
"""存量脏工作流退役 — 只标记不删除 + 追加式审计台账

口径（对齐仓库既有"归档不删证据"）
----------------------------------
- **只标记不删除**：把命中结构性准入否决（单字触发词 / 步骤数下限）的存量条目
  的 `status` 置为 `archived`，**不删条目、不删字段、不改写历史**；
  `converted_to_skill_id` 等既成事实原样保留（它是"曾经被转换过"的证据）。
- **追加式台账**：每次"状态迁移"追加一行 JSONL 到
  `data/learned_workflows_retired.jsonl`（与 `data/evolution_archive.jsonl`、
  `data/approval_records.jsonl` 同构）。台账只追加、不重写、不删除。
- **可复算**：`scan_dirty_workflows()` 与实际退役共用同一判定源
  （`agent.workflow_learning.admission`），退役返回 before/after 数字。
- **幂等**：已是 `archived` 的条目不再重复迁移、不再重复记账。

被退役条目的可及性
------------------
退役 ≠ 失效：`execute_by_id`（人工按 ID 触发）不经过匹配器，仍可执行；
被挡住的只有"自动进入匹配候选"与"自动升格为 Skill"。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .admission import check_structure, MIN_STEPS, MIN_TRIGGER_CHARS
from .models import WorkflowStatus
from .repository import WorkflowRepository

logger = logging.getLogger("agent.workflow_learning")

#: 审计台账文件名（与 learned_workflows.json 同目录）
DEFAULT_LEDGER_NAME = "learned_workflows_retired.jsonl"

#: 台账事件名
EVENT_RETIRED = "workflow_retired"

__all__ = [
    "DEFAULT_LEDGER_NAME",
    "EVENT_RETIRED",
    "default_ledger_path",
    "dirty_evidence",
    "scan_dirty_workflows",
    "retire_dirty_workflows",
    "WORKFLOW_REPO_PATH",
]

#: 与 repository._DEFAULT_REPO_PATH 一致（用于推导默认台账路径）
WORKFLOW_REPO_PATH = (Path(__file__).parent.parent.parent
                      / "data" / "learned_workflows.json")


def default_ledger_path(repo_path: Optional[str] = None) -> Path:
    """台账默认路径 = 工作流仓库文件同目录下的 `learned_workflows_retired.jsonl`"""
    base = Path(repo_path) if repo_path else WORKFLOW_REPO_PATH
    return base.parent / DEFAULT_LEDGER_NAME


def dirty_evidence(wf: Any) -> Dict[str, Any]:
    """证据快照 —— 只取判定/追溯所需的叶子字段（不 dump 活对象）"""
    steps = list(getattr(wf, "steps", None) or [])
    status = getattr(wf, "status", None)
    return {
        "workflow_id": getattr(wf, "id", ""),
        "name": getattr(wf, "name", ""),
        "status": str(getattr(status, "value", status)),
        "enabled": bool(getattr(wf, "enabled", False)),
        "step_count": len(steps),
        "tool_chain": [getattr(s, "tool_name", "") for s in steps][:10],
        "trigger_patterns": list(getattr(wf, "trigger_patterns", None) or []),
        "task_signature": getattr(wf, "task_signature", ""),
        "source_session_id": getattr(wf, "source_session_id", ""),
        "source_user_input": getattr(wf, "source_user_input", ""),
        "success_count": int(getattr(wf, "success_count", 0) or 0),
        "failure_count": int(getattr(wf, "failure_count", 0) or 0),
        "confidence": float(getattr(wf, "confidence", 0.0) or 0.0),
        "priority": int(getattr(wf, "priority", 0) or 0),
        "converted_to_skill_id": getattr(wf, "converted_to_skill_id", ""),
        "created_at": getattr(wf, "created_at", ""),
        "updated_at": getattr(wf, "updated_at", ""),
    }


def scan_dirty_workflows(repo: WorkflowRepository) -> List[Dict[str, Any]]:
    """扫描命中结构性准入否决的存量条目（不修改任何状态）

    Returns:
        [{workflow, decision, evidence, already_archived}, ...]，按 id 排序
    """
    found: List[Dict[str, Any]] = []
    for wf in sorted(repo.list_all(), key=lambda w: w.id):
        decision = check_structure(steps=wf.steps,
                                   trigger_patterns=wf.trigger_patterns)
        if decision.admitted:
            continue
        status = getattr(wf.status, "value", wf.status)
        found.append({
            "workflow": wf,
            "decision": decision,
            "evidence": dirty_evidence(wf),
            "already_archived": str(status) == WorkflowStatus.ARCHIVED.value,
        })
    return found


def _append_ledger(ledger_path: Path, records: List[Dict[str, Any]]) -> None:
    """追加式写入台账（不重写既有行；失败即抛，不静默丢证据）"""
    if not records:
        return
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
        for r in records
    )
    # 追加写：单独打开，避免任何"整文件重写"路径
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())


def retire_dirty_workflows(repo: WorkflowRepository, *,
                           apply: bool = False,
                           ledger_path: Optional[Path] = None,
                           now: Optional[str] = None) -> Dict[str, Any]:
    """把存量脏工作流退役为 `archived`（默认 dry-run）

    Args:
        repo: 工作流仓库
        apply: False（默认）= 只扫描出清单；True = 真正标记 + 记账
        ledger_path: 审计台账路径（默认仓库文件同目录的 jsonl）
        now: 覆盖时间戳（测试用）

    Returns:
        {
          "applied": bool, "checked": n, "dirty_before": n, "dirty_after": n,
          "retired": [ {workflow_id, name, codes, reasons, step_count, triggers} ],
          "already_archived": [ids], "ledger_path": str, "policy": {...}
        }
    """
    repo_file = str(getattr(repo, "_path", "") or "") or None
    ledger = Path(ledger_path) if ledger_path else default_ledger_path(repo_file)
    scanned = scan_dirty_workflows(repo)
    dirty_before = len(scanned)
    ts = now or datetime.now().isoformat()

    plan: List[Dict[str, Any]] = []
    already: List[str] = []
    ledger_records: List[Dict[str, Any]] = []
    for item in scanned:
        wf = item["workflow"]
        ev = item["evidence"]
        if item["already_archived"]:
            already.append(wf.id)
            continue
        plan.append({
            "workflow_id": wf.id,
            "name": ev["name"],
            "codes": list(item["decision"].codes),
            "reasons": list(item["decision"].reasons),
            "step_count": ev["step_count"],
            "trigger_patterns": ev["trigger_patterns"],
            "source_session_id": ev["source_session_id"],
            "converted_to_skill_id": ev["converted_to_skill_id"],
        })
        if apply:
            wf.status = WorkflowStatus.ARCHIVED
            wf.touch()
            repo.upsert(wf)
            ledger_records.append({
                "event": EVENT_RETIRED,
                "workflow_id": wf.id,
                "retired_at": ts,
                "from_status": ev["status"],
                "to_status": WorkflowStatus.ARCHIVED.value,
                "codes": list(item["decision"].codes),
                "reasons": list(item["decision"].reasons),
                "policy": {
                    "MIN_STEPS": MIN_STEPS,
                    "MIN_TRIGGER_CHARS": MIN_TRIGGER_CHARS,
                },
                "evidence": ev,
                "actor": "agent.workflow_learning.retirement",
                "deleted": False,
            })

    if apply:
        _append_ledger(ledger, ledger_records)
        dirty_after = len([i for i in scan_dirty_workflows(repo)
                           if not i["already_archived"]])
    else:
        dirty_after = dirty_before

    summary = {
        "applied": bool(apply),
        "checked": len(repo.list_all()),
        "dirty_before": dirty_before,
        "dirty_after": dirty_after,
        "retired": plan,
        "already_archived": already,
        "ledger_path": str(ledger),
        "ledger_records": len(ledger_records),
        "policy": {"MIN_STEPS": MIN_STEPS,
                   "MIN_TRIGGER_CHARS": MIN_TRIGGER_CHARS},
    }
    if plan:
        logger.info(
            "[Retirement] %s: 检查=%d 脏(结构性否决)=%d → 退役后残留=%d "
            "台账=%s(+%d 行)",
            "已执行" if apply else "试运行", summary["checked"],
            dirty_before, dirty_after, ledger, len(ledger_records))
    return summary
