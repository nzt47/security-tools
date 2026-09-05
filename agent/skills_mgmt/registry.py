"""统一技能注册表 — 技能启停状态的单一查询/写入入口。

背景（legacy 迁移）:
    历史上技能启停状态分散在 3 处——主轨 skills_mgmt.json（技能管理 v1）、
    文件轨 skills_repo/<id>/skill.md front matter（含内置 persona 技能）、
    legacy data/skills.json（SkillsManager/旧 UI）。digital_life_persona 等
    运行时直接读 data/skills.json 判断 persona 技能开关（self_reflection/
    voice_interaction/…），导致"改主轨/文件轨不影响 persona、legacy 残留
    又污染 UI"的双向断裂。

本模块把三源合并为统一视图，迁移后：
    - 读：主轨 → 文件轨 front matter（不再直接读 data/skills.json）
    - 写：主轨有→改主轨；否则改文件轨 front matter（persona 技能落文件轨）
    - legacy data/skills.json 降级为只读兼容快照（可最终废弃）

用法：
    from agent.skills_mgmt.registry import SkillRegistry
    reg = SkillRegistry()
    reg.is_enabled("self_reflection")          # True/False
    reg.set_enabled("self_reflection", False)  # 落文件轨 front matter
    reg.list_enabled_ids()                     # 全部启用技能 id
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)


class SkillRegistry:
    """统一技能注册表（主轨 JSON + 文件轨 skill.md 双源合并视图）。"""

    def __init__(self, service: Optional[Any] = None):
        """Args:
            service: SkillsMgmtService 实例（None 时懒加载默认实例）。
        """
        self._service = service

    def _svc(self) -> Any:
        if self._service is None:
            from agent.skills_mgmt.service import SkillsMgmtService
            self._service = SkillsMgmtService()
        return self._service

    # ─── 读 ───

    def is_enabled(self, skill_id: str) -> bool:
        """技能是否启用：主轨 Skill.enabled → 文件轨 front matter enabled。

        默认 True（历史语义：缺失视为启用）。异常不影响主流程。
        """
        svc = self._svc()
        # 1) 主轨（权威）
        try:
            if svc.store.get(skill_id) is not None:
                skill = svc.get(skill_id)
                return bool(getattr(skill, "enabled", True))
        except Exception:  # noqa: BLE001
            pass
        # 2) 文件轨 front matter（persona 内置技能等）
        try:
            meta = svc.file_store.get_metadata(skill_id)
            if meta is not None:
                return bool(meta.get("enabled", True))
        except Exception:  # noqa: BLE001
            pass
        return True

    def get_description(self, skill_id: str) -> str:
        """技能描述：主轨 → 文件轨 → 空串。"""
        svc = self._svc()
        try:
            if svc.store.get(skill_id) is not None:
                skill = svc.get(skill_id)
                return str(getattr(skill, "description", "") or "")
        except Exception:  # noqa: BLE001
            pass
        try:
            meta = svc.file_store.get_metadata(skill_id)
            if meta is not None:
                return str(meta.get("description", "") or "")
        except Exception:  # noqa: BLE001
            pass
        return ""

    def list_skill_ids(self) -> Set[str]:
        """全部已知技能 id：主轨 ∪ 文件轨。"""
        svc = self._svc()
        ids: Set[str] = set()
        try:
            ids.update(svc.store._load().keys())
        except Exception:  # noqa: BLE001
            pass
        try:
            meta_idx = svc.file_store.load_metadata_index(refresh=False)
            ids.update(meta_idx.keys())
        except Exception:  # noqa: BLE001
            pass
        return ids

    def list_enabled_ids(self) -> List[str]:
        """启用状态的技能 id 列表。"""
        out = []
        for sid in sorted(self.list_skill_ids()):
            try:
                if self.is_enabled(sid):
                    out.append(sid)
            except Exception:  # noqa: BLE001
                continue
        return out

    # ─── 写 ───

    def set_enabled(self, skill_id: str, enabled: bool) -> Dict[str, Any]:
        """设置技能启停：主轨有→改主轨；否则改文件轨 front matter。

        Returns: {ok, id, enabled, track: "main"|"file_track"}
        """
        svc = self._svc()
        # 1) 主轨存在 → 主轨权威
        try:
            if svc.store.get(skill_id) is not None:
                svc.set_enabled(skill_id, enabled)
                return {"ok": True, "id": skill_id, "enabled": enabled,
                        "track": "main"}
        except Exception as e:  # noqa: BLE001
            logger.warning("[Registry] 主轨 set_enabled 失败 %s: %s",
                           skill_id, e)
        # 2) 文件轨存在（persona 内置技能）→ 改 front matter
        try:
            meta = svc.file_store.get_metadata(skill_id)
            if meta is not None:
                svc.file_store.update_meta(skill_id, {"enabled": enabled})
                return {"ok": True, "id": skill_id, "enabled": enabled,
                        "track": "file_track"}
        except Exception as e:  # noqa: BLE001
            logger.warning("[Registry] 文件轨 set_enabled 失败 %s: %s",
                           skill_id, e)
        return {"ok": False, "id": skill_id,
                "error": f"未知技能: {skill_id}"}

    def toggle(self, skill_id: str) -> Dict[str, Any]:
        """切换技能启停。"""
        cur = self.is_enabled(skill_id)
        return self.set_enabled(skill_id, not cur)

    # ─── 兼容视图（供旧 UI 只读，不再写入 legacy） ───

    def as_legacy_rows(self) -> List[Dict[str, Any]]:
        """输出与旧 data/skills.json 行同构的只读列表（id/name/enabled/
        description/params），供需要旧格式的下游消费。"""
        svc = self._svc()
        rows: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        # 主轨
        try:
            for skill in svc.store.list_all():
                sid = skill.id
                if sid in seen:
                    continue
                seen.add(sid)
                rows.append({
                    "id": sid,
                    "name": getattr(skill, "name", sid),
                    "enabled": bool(getattr(skill, "enabled", True)),
                    "description": str(getattr(skill, "description", "")
                                       or ""),
                    "params": dict(getattr(skill, "default_params", {})
                                   or {}),
                })
        except Exception:  # noqa: BLE001
            pass
        # 文件轨独有（persona 内置等，主轨未注册）
        try:
            meta_idx = svc.file_store.load_metadata_index(refresh=False)
            for sid, meta in sorted(meta_idx.items()):
                if sid in seen:
                    continue
                seen.add(sid)
                rows.append({
                    "id": sid,
                    "name": str(meta.get("name") or sid),
                    "enabled": bool(meta.get("enabled", True)),
                    "description": str(meta.get("description", "") or ""),
                    "params": {},
                })
        except Exception:  # noqa: BLE001
            pass
        return rows
