"""技能管理持久化存储

设计:
    - 单文件 JSON 存储 (data/skills_mgmt.json)，原子写入 (临时文件 + os.replace)
    - 内存缓存 + 文件回写，避免高频读盘
    - 与现有 agent/extensions/store.py 互操作: 同步技能状态到 ExtensionStore，
      使旧 routes_skills.py 仍能看到技能启用状态
"""

from __future__ import annotations
import json
import os
import tempfile
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Skill, SkillStatus
from .observability import logger

# 默认存储位置 — 与项目其他 data 文件对齐
_DEFAULT_STORE_PATH = Path(__file__).parent.parent.parent / "data" / "skills_mgmt.json"


class SkillStore:
    """技能持久化存储 (线程安全)"""

    def __init__(self, path: Optional[str] = None):
        self._path = Path(path) if path else _DEFAULT_STORE_PATH
        self._lock = threading.RLock()
        self._cache: Optional[Dict[str, dict]] = None

    # ──────────────────────────────────────────
    # 底层 IO
    # ──────────────────────────────────────────

    def _load(self) -> Dict[str, dict]:
        """加载全部技能 (带缓存)"""
        with self._lock:
            if self._cache is not None:
                return self._cache
            if not self._path.exists():
                self._cache = {}
                self._persist()
                logger.info("[SkillStore] 初始化存储: %s", self._path)
                return self._cache
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
                if not isinstance(self._cache, dict):
                    raise ValueError("存储文件根节点必须是对象")
            except (json.JSONDecodeError, ValueError, OSError) as e:
                # 边界显性化: 文件损坏时不可静默丢弃，先备份再重置
                backup = self._path.with_suffix(".corrupted.json")
                try:
                    self._path.rename(backup)
                    logger.warning("[SkillStore] 存储损坏已备份到 %s: %s", backup, e)
                except OSError:
                    pass
                self._cache = {}
                self._persist()
            return self._cache

    def _persist(self) -> None:
        """原子写入 (临时文件 + os.replace)"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False,
            dir=str(self._path.parent), suffix=".tmp",
        ) as tmp:
            json.dump(self._cache or {}, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, self._path)

    def _invalidate(self) -> None:
        self._cache = None

    # ──────────────────────────────────────────
    # 公开 API
    # ──────────────────────────────────────────

    def list_all(self) -> List[Skill]:
        """返回所有技能"""
        data = self._load()
        return [Skill.from_storage_dict(v) for v in data.values()]

    def get(self, skill_id: str) -> Optional[Skill]:
        data = self._load()
        if skill_id not in data:
            return None
        return Skill.from_storage_dict(data[skill_id])

    def upsert(self, skill: Skill) -> None:
        """新增或更新 (按 id)"""
        with self._lock:
            data = self._load()
            data[skill.id] = skill.to_storage_dict()
            self._persist()

    def remove(self, skill_id: str) -> bool:
        with self._lock:
            data = self._load()
            if skill_id not in data:
                return False
            del data[skill_id]
            self._persist()
            return True

    def count(self) -> int:
        return len(self._load())

    def clear(self) -> None:
        """清空存储 (谨慎使用)"""
        with self._lock:
            self._cache = {}
            self._persist()

    # ──────────────────────────────────────────
    # 与旧系统互操作
    # ──────────────────────────────────────────

    def sync_to_legacy_skills_json(self) -> int:
        """把启用状态的技能同步到 data/skills.json，保持向后兼容

        旧 SkillsManager / routes_skills.py 仍读取 data/skills.json，
        此方法确保新系统的技能在旧 UI 也能看到。
        Returns: 同步的技能数量
        """
        try:
            legacy_path = self._path.parent / "skills.json"
            with open(legacy_path, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            if not isinstance(legacy, dict):
                legacy = {"skills": []}
            legacy.setdefault("skills", [])
            existing_ids = {s.get("id") for s in legacy["skills"]}

            count = 0
            for skill_dict in self._load().values():
                if skill_dict.get("id") in existing_ids:
                    continue
                # 仅同步启用且非草稿的技能，避免污染旧 UI
                if (skill_dict.get("enabled", True)
                        and skill_dict.get("status", SkillStatus.DRAFT.value)
                        in (SkillStatus.APPROVED.value,
                            SkillStatus.PUBLISHED.value,
                            SkillStatus.DRAFT.value)):
                    legacy["skills"].append({
                        "id": skill_dict["id"],
                        "name": skill_dict.get("name", skill_dict["id"]),
                        "enabled": True,
                        "description": skill_dict.get("description", ""),
                        "params": skill_dict.get("default_params", {}),
                    })
                    count += 1
            if count > 0:
                with open(legacy_path, "w", encoding="utf-8") as f:
                    json.dump(legacy, f, ensure_ascii=False, indent=2)
                logger.info("[SkillStore] 同步 %d 个技能到 legacy skills.json", count)
            return count
        except Exception as e:  # noqa: BLE001  向后兼容同步失败不阻塞主流程
            logger.warning("[SkillStore] 同步 legacy 失败: %s", e)
            return 0

    def health(self) -> Dict[str, Any]:
        """健康检查 (供 /api/skills-mgmt/health 调用)"""
        try:
            count = self.count()
            writable = os.access(self._path.parent, os.W_OK)
            return {
                "ok": True,
                "store_path": str(self._path),
                "skill_count": count,
                "writable": bool(writable),
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
