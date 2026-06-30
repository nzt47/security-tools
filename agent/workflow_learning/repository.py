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
