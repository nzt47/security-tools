"""本地工作流仓库 — 持久化存储 LearnedWorkflow"""

from __future__ import annotations
import json
import os
import tempfile
import threading
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import LearnedWorkflow, WorkflowStatus

logger = logging.getLogger("agent.workflow_learning")

_DEFAULT_REPO_PATH = Path(__file__).parent.parent.parent / "data" / "learned_workflows.json"

#: 【F11】`LearnedWorkflow.source_sessions` 保留的会话数上限：
#: 跨会话样本数只需判定 ">= admission.MIN_CROSS_SESSION_SUPPORT(2)"，
#: 无上限累积会让 json 随运行时长线性膨胀，故保留**最近** N 个（去重）。
SOURCE_SESSIONS_MAX = 20


def _sessions_of(entry: dict) -> set:
    """条目记录的来源会话集合（首次来源 + 后续观察到的会话，去空值）"""
    sessions = {str(entry.get("source_session_id") or "")}
    for s in (entry.get("source_sessions") or []):
        sessions.add(str(s or ""))
    sessions.discard("")
    return sessions


class WorkflowRepository:
    """工作流仓库 (线程安全)"""

    def __init__(self, path: Optional[str] = None):
        self._path = Path(path) if path else _DEFAULT_REPO_PATH
        self._lock = threading.RLock()
        self._cache: Optional[Dict[str, dict]] = None

    def _load(self) -> Dict[str, dict]:
        with self._lock:
            if self._cache is not None:
                return self._cache
            if not self._path.exists():
                self._cache = {}
                self._persist()
                logger.info("[WorkflowRepo] 初始化仓库: %s", self._path)
                return self._cache
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
                if not isinstance(self._cache, dict):
                    raise ValueError("仓库根节点必须是对象")
            except (json.JSONDecodeError, ValueError, OSError) as e:
                backup = self._path.with_suffix(".corrupted.json")
                try:
                    self._path.rename(backup)
                    logger.warning("[WorkflowRepo] 仓库损坏已备份: %s", backup)
                except OSError:
                    pass
                self._cache = {}
                self._persist()
            return self._cache

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False,
            dir=str(self._path.parent), suffix=".tmp",
        ) as tmp:
            json.dump(self._cache or {}, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, self._path)

    # ─── 公开 API ───

    def list_all(self, *, enabled_only: bool = False) -> List[LearnedWorkflow]:
        data = self._load()
        items = [LearnedWorkflow(**v) for v in data.values()]
        if enabled_only:
            items = [w for w in items if w.enabled
                     and w.status == WorkflowStatus.ACTIVE.value]
        return items

    def get(self, wf_id: str) -> Optional[LearnedWorkflow]:
        data = self._load()
        if wf_id not in data:
            return None
        return LearnedWorkflow(**data[wf_id])

    def upsert(self, wf: LearnedWorkflow) -> None:
        with self._lock:
            data = self._load()
            data[wf.id] = wf.model_dump()
            self._persist()

    def remove(self, wf_id: str) -> bool:
        with self._lock:
            data = self._load()
            if wf_id not in data:
                return False
            del data[wf_id]
            self._persist()
            return True

    def count(self) -> int:
        return len(self._load())

    def count_distinct_sessions(self, task_signature: str) -> int:
        """同一 `task_signature` 出现在多少个**不同会话**里（样本数）

        【TASK-S10-01】"单轮来源"的机器可判定形式：签名只在 1 个会话出现
        ⇒ 只有 1 个样本。自动升格为 Skill 要求 ≥
        `admission.MIN_CROSS_SESSION_SUPPORT`（见 admission 模块 §3）。
        注意：`learner._derive_id` 把 session_id 计入哈希，同一任务在不同会话
        会自动生成**不同 id**（这正是存量 `wf-f19dc52c` / `wf-c7499f27` 成对出现的
        原因），故支持数只能按 `task_signature` 聚合，不能按 id 去重。

        【F11】同签名**去重合并**后，一条条目会代表多次观察 ⇒ 样本数还要并入
        `source_sessions`（首次来源仍是 `source_session_id`，见 learner/service
        的合并逻辑）；否则去重会把跨会话门槛永久焊死在 1，自动升格再无可能。
        """
        if not task_signature:
            return 0
        data = self._load()
        sessions = set()
        for v in data.values():
            if str(v.get("task_signature") or "") != str(task_signature):
                continue
            sessions |= _sessions_of(v)
        sessions.discard("")
        return len(sessions)

    def signatures(self) -> Dict[str, int]:
        """{task_signature: 跨会话样本数}（可复算读数的唯一来源）

        【F11】与 `count_distinct_sessions` 同口径：样本数 = 该签名下所有条目的
        `source_session_id` ∪ `source_sessions` 去重计数。
        """
        data = self._load()
        buckets: Dict[str, set] = {}
        for v in data.values():
            sig = str(v.get("task_signature") or "")
            if not sig:
                continue
            buckets.setdefault(sig, set()).update(_sessions_of(v))
        return {sig: len(s) for sig, s in buckets.items()}

    def health(self) -> Dict[str, Any]:
        try:
            count = self.count()
            writable = os.access(self._path.parent, os.W_OK)
            return {
                "ok": True,
                "repo_path": str(self._path),
                "workflow_count": count,
                "writable": bool(writable),
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
