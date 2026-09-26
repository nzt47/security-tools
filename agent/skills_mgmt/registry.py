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
import os
from typing import Any, Dict, List, Optional, Set

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)


#: 【G1-B/M3 逃生开关】置 0 ⇒ `as_legacy_rows()` 的 `description` 回退到主轨文案
#: （= 本卡改造前的行为）。置 0 后 **`description_zh` 仍然读文件轨** ——
#: 因为 UI 文案（中文）与检索文案（英文）是两件事，回滚「英文从哪来」不应同时
#: 把界面文案弄丢。**不改代码即可回滚**。
_ENV_DESC_FROM_FILE_TRACK = "CP_SKILL_DESC_FROM_FILE_TRACK"


def _desc_from_file_track() -> bool:
    """描述是否取**文件轨优先**（默认 True；置 0/false/no/off 关闭）"""
    raw = os.environ.get(_ENV_DESC_FROM_FILE_TRACK)
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _audit_hooks() -> Any:
    """惰性取启停审计钩子：(set_enabled 动作名, toggle 动作名, 来源标记, 入链函数)

    Why 惰性：`agent/skills_mgmt/__init__.py` 的不变量要求轻量子模块（本模块此前只
    依赖 logging_utils）不被 service→models(pydantic) 重依赖绑架；enhancer 会拉入
    models/store，故只在真正启停时才导入。

    导入失败 → 返回 ("", "", None, None)：启停照常执行，仅本次不留痕并留 WARNING 日志。
    """
    try:
        from agent.skills_mgmt.enhancer import (
            AUDIT_ACTION_ENABLED_SET, AUDIT_ACTION_ENABLED_TOGGLE,
            enabled_audit_origin, record_enabled_audit)
        return (AUDIT_ACTION_ENABLED_SET, AUDIT_ACTION_ENABLED_TOGGLE,
                enabled_audit_origin, record_enabled_audit)
    except Exception as e:  # noqa: BLE001 审计不可用不得阻断启停
        logger.warning("[Registry] 启停审计钩子不可用（本次启停不留痕）: %s", e)
        return "", "", None, None


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

        两条分支的成功路径都会写审计链（动作名 `skill.registry.set_enabled`，
        payload 含 skill_id / previous_enabled / enabled / track / origin）。

        Returns: {ok, id, enabled, track: "main"|"file_track"}
        """
        return self._set_enabled(skill_id, enabled, toggle=False)

    def toggle(self, skill_id: str) -> Dict[str, Any]:
        """切换技能启停。

        返回结构与 `set_enabled` 相同；审计动作名为 `skill.registry.toggle`
        （payload 的 track/origin 同样标明实际走的轨与调用来源）。
        """
        cur = self.is_enabled(skill_id)
        return self._set_enabled(skill_id, not cur, toggle=True)

    def _set_enabled(self, skill_id: str, enabled: bool, *,
                     toggle: bool) -> Dict[str, Any]:
        """`set_enabled` / `toggle` 的共同实现（返回结构见 `set_enabled`）

        Args:
            toggle: True = 由 `toggle` 调用（审计动作名取 skill.registry.toggle）。
        """
        svc = self._svc()
        action_set, action_toggle, audit_origin, audit_record = _audit_hooks()
        action = action_toggle if toggle else action_set
        origin = "skill_registry.toggle" if toggle else "skill_registry.set_enabled"
        # 1) 主轨存在 → 主轨权威
        try:
            existing = svc.store.get(skill_id)
            if existing is not None:
                if audit_origin is not None:
                    # 主轨的链路留痕由主轨唯一落库点 SkillEnhancer.set_enabled 写出；
                    # 这里只透传动作名与调用来源 ⇒ 同一次启停只产生一条审计记录
                    with audit_origin(action, origin):
                        svc.set_enabled(skill_id, enabled)
                else:
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
                previous = bool(meta.get("enabled", True))
                svc.file_store.update_meta(skill_id, {"enabled": enabled})
                if audit_record is not None:
                    # 文件轨不经过 SkillEnhancer（直接改 front matter）→ 在此入链
                    audit_record(action, skill_id, previous_enabled=previous,
                                 enabled=bool(enabled), track="file_track",
                                 origin=origin)
                return {"ok": True, "id": skill_id, "enabled": enabled,
                        "track": "file_track"}
        except Exception as e:  # noqa: BLE001
            logger.warning("[Registry] 文件轨 set_enabled 失败 %s: %s",
                           skill_id, e)
        return {"ok": False, "id": skill_id,
                "error": f"未知技能: {skill_id}"}

    # ─── 兼容视图（供旧 UI 只读，不再写入 legacy） ───

    def as_legacy_rows(self) -> List[Dict[str, Any]]:
        """输出与旧 data/skills.json 行同构的只读列表（id/name/enabled/
        description/params），供需要旧格式的下游消费。

        【G1-B/M3 · 描述唯一事实源】本方法自 2026-09-26 起对 `description` 采用
        **文件轨优先、回落主轨**：

            description    = skill.md front matter 的 description（英文，检索 + 模型可见）
            description_zh = skill.md front matter 的 description_zh（中文，UI 展示）

        为什么必须改（G1-A C2 / R1 的根因）：本方法原来的合并规则是「主轨先占位、
        文件轨只补缺失」，于是 15 条 `pd-*` 的 `description` 取的是主轨的**中文译文**，
        而检索与模型读的是 skill.md 的**英文原文** ⇒ 同一字段两个消费者看到两份文案
        （实测 15/15 全分歧）。改判后「描述」只剩一份（skill.md），主轨 description
        降级为历史副本。

        [不易] 副作用与配套：UI 若直接显示 `description` 会从中文变英文 —— 故前端
        `skills.tsx` 同批改为优先读 `description_zh`（M4），中英各归其位。

        [变易] 逃生开关 `CP_SKILL_DESC_FROM_FILE_TRACK=0` ⇒ 描述回退主轨（旧行为），
        **不改代码即可回滚**；此时 `description_zh` 仍取文件轨（界面文案不陪葬）。
        """
        svc = self._svc()
        rows: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        from_file_track = _desc_from_file_track()
        # 文件轨元数据索引（`description_zh` 的唯一来源；同时供 description 优先取值）
        meta_idx: Dict[str, Dict[str, Any]] = {}
        try:
            meta_idx = svc.file_store.load_metadata_index(refresh=False) or {}
        except Exception:  # noqa: BLE001  文件轨读失败 ⇒ 退化为旧行为（主轨文案）
            meta_idx = {}
        # 主轨
        try:
            for skill in svc.store.list_all():
                sid = skill.id
                if sid in seen:
                    continue
                seen.add(sid)
                main_desc = str(getattr(skill, "description", "") or "")
                fmeta = meta_idx.get(sid) or {}
                rows.append({
                    "id": sid,
                    "name": getattr(skill, "name", sid),
                    "enabled": bool(getattr(skill, "enabled", True)),
                    # 文件轨优先（有该技能的 skill.md 就用它的 description）
                    "description": (str(fmeta.get("description") or "")
                                    if (from_file_track and sid in meta_idx)
                                    else main_desc),
                    "description_zh": str(fmeta.get("description_zh") or ""),
                    "params": dict(getattr(skill, "default_params", {})
                                   or {}),
                })
        except Exception:  # noqa: BLE001
            pass
        # 文件轨独有（persona 内置等，主轨未注册）
        try:
            for sid, meta in sorted(meta_idx.items()):
                if sid in seen:
                    continue
                seen.add(sid)
                rows.append({
                    "id": sid,
                    "name": str(meta.get("name") or sid),
                    "enabled": bool(meta.get("enabled", True)),
                    "description": str(meta.get("description", "") or ""),
                    # [不易] 文件轨分支是**显式键字典**，不加这行则两个分支的行形状
                    # 不一致（G1-A 曾误以为该分支会自动带上 description_zh）。
                    "description_zh": str(meta.get("description_zh") or ""),
                    "params": {},
                })
        except Exception:  # noqa: BLE001
            pass
        return rows
