"""技能清理服务 — 自动清除无用技能与孤儿残留（技能中心清理能力）。

背景（Why）:
    skills_mgmt 历史上"删除技能只删主轨"（store.remove），legacy
    （data/skills.json / agent/data/skills.json 旧副本）、文件轨
    （skills_repo/<id>/）、分类注册表、digest 事件均不同步 → UI 出现
    "该技能没有可查看的指令正文（仅运行时元数据）"的孤儿残留；
    且无"无用技能"的物理淘汰（lifecycle.py 只做状态迁移不删文件）。

本模块提供四层能力：
    1. remove_skill_everywhere：删除一个技能时同步清除全部存储轨
       （根治孤儿源头；service.delete 复用）
    2. scan_orphans / cleanup_orphans：扫描并清除"只在旧轨存在、主轨已无"
       的孤儿残留（含各类元数据引用）
    3. scan_unused / cleanup_unused：按闲置阈值找出并物理删除"长期零使用"
       的无用技能（默认保守：仅 ARCHIVED 且超期，或 usage_count==0 超期；
       dry_run 默认 true，仅报告不删）
    4. report：汇总扫描结果，供技能中心 UI 展示 / 人工一键确认

安全边界（【不易】）：
    - 默认 dry_run=true：任何清除操作默认只报告不执行；
    - 物理删除仅针对"孤儿残留"（主轨已无，必为脏数据）与"明确无用"
      （归档超期零使用）——不删任何主轨仍存在的有效技能；
    - 所有删除走原子写（临时文件 + os.replace），失败不阻断其它轨。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

# 状态值（与 models.SkillStatus 对齐）
_STATUS_ARCHIVED = "archived"
_STATUS_DEPRECATED = "deprecated"

# 默认阈值（env/config 可覆盖，见各函数）
_DEFAULT_UNUSED_DAYS = 90          # 零使用超此天数视为无用候选
_DEFAULT_ARCHIVED_DAYS = 180       # 已归档超此天数可物理删除
_DEFAULT_DRY_RUN = True


def _now() -> datetime:
    return datetime.now()


# ═══════════════════════════════════════════════════════════════
#  存储轨路径解析（与 store.py / file_store.py 同源）
# ═══════════════════════════════════════════════════════════════

def _legacy_paths(main_store_path: Optional[Path]) -> List[Path]:
    """legacy/兼容轨 JSON 文件路径列表。

    1. main_store_path.parent/skills.json — store.py 的权威同步目标
       （隔离测试时落在临时目录，天然隔离）；
    2. 仓库根 agent/data/skills.json — 更早一代的旧 UI 兼容副本，
       仅当主轨在默认生产位置（仓库根 data/）时才附加——避免隔离测试
       误扫生产旧副本。
    """
    out: List[Path] = []
    if main_store_path is not None:
        p = main_store_path.parent / "skills.json"
        if p not in out:
            out.append(p)
    root = Path(__file__).resolve().parent.parent.parent
    default_main = root / "data" / "skills_mgmt.json"
    if main_store_path is not None and \
            Path(main_store_path).resolve() == default_main.resolve():
        alt = root / "agent" / "data" / "skills.json"
        if alt not in out:
            out.append(alt)
    return out


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


# ═══════════════════════════════════════════════════════════════
#  1. 多轨删除（根治孤儿源头）
# ═══════════════════════════════════════════════════════════════

def remove_skill_everywhere(svc, skill_id: str) -> Dict[str, Any]:
    """从全部存储轨删除一个技能：主轨/legacy×2/文件轨/分类/digest。

    幂等：任何轨不存在该 id 都跳过不报错。返回各轨删除结果。
    """
    result: Dict[str, Any] = {"skill_id": skill_id, "removed": []}

    # 1) 主轨（权威）
    try:
        if svc.store.get(skill_id) is not None:
            svc.store.remove(skill_id)
            result["removed"].append("main")
    except Exception as e:  # noqa: BLE001
        logger.warning("[Cleanup] 主轨删除失败 %s: %s", skill_id, e)

    # 2) 文件轨 skills_repo/<id>/
    try:
        fp = svc.file_store._skill_dir(skill_id)
        if fp.exists():
            shutil.rmtree(fp)
            result["removed"].append("file_track")
            svc.file_store._meta_index = None
    except Exception as e:  # noqa: BLE001
        logger.warning("[Cleanup] 文件轨删除失败 %s: %s", skill_id, e)

    # 3) legacy / 兼容轨（json 内删 id 项）
    for lp in _legacy_paths(getattr(svc.store, "_path", None)):
        try:
            data = _read_json(lp)
            skills = data.get("skills", [])
            before = len(skills)
            skills = [s for s in skills
                      if str(s.get("id", "")) != skill_id]
            if len(skills) != before:
                data["skills"] = skills
                _write_json(lp, data)
                result["removed"].append(f"legacy:{lp.name}")
        except Exception as e:  # noqa: BLE001
            logger.warning("[Cleanup] legacy 删除失败 %s %s: %s",
                           lp, skill_id, e)

    # 4) 分类注册表
    try:
        registry = getattr(svc, "_class_registry", None)
        if registry is not None:
            reg_path = getattr(registry, "_path", None)
            if reg_path is not None and reg_path.exists():
                reg = _read_json(reg_path)
                text_before = json.dumps(reg, ensure_ascii=False)
                # 递归移除引用该 skill_id 的分类项
                reg = _strip_refs(reg, skill_id)
                if json.dumps(reg, ensure_ascii=False) != text_before:
                    _write_json(reg_path, reg)
                    result["removed"].append("classes")
    except Exception as e:  # noqa: BLE001
        logger.warning("[Cleanup] 分类清理失败 %s: %s", skill_id, e)

    # 5) digest 事件文件（逐行过滤）
    try:
        ev_path = Path("data/skills_digest_events.jsonl")
        if ev_path.exists():
            lines = ev_path.read_text(encoding="utf-8").splitlines()
            kept = [ln for ln in lines
                    if f'"{skill_id}"' not in ln and skill_id not in ln]
            if len(kept) != len(lines):
                ev_path.write_text("\n".join(kept) + ("\n" if kept else ""),
                                   encoding="utf-8")
                result["removed"].append("digest_events")
    except Exception as e:  # noqa: BLE001
        logger.warning("[Cleanup] digest 事件清理失败 %s: %s", skill_id, e)

    # 6) 扩展注册表 extensions.json（ExtensionStore；旧 /api/skills 从它补
    #    installed → 残留会让技能面板显示已删的"无正文"技能）
    try:
        from agent.extensions.base import ExtensionType
        from agent.extensions.store import ExtensionStore
        ExtensionStore().remove(ExtensionType.SKILL, skill_id)
        result["removed"].append("extensions")
    except Exception as e:  # noqa: BLE001
        logger.warning("[Cleanup] 扩展注册表清理失败 %s: %s", skill_id, e)

    logger.info(log_dict({'module_name': 'cleanup',
                          'action': 'cleanup.remove',
                          'skill_id': skill_id,
                          'removed': result["removed"]}))
    return result


def _strip_refs(obj: Any, ref: str) -> Any:
    """递归从 dict/list 中移除与 ref 等值的字符串项。"""
    if isinstance(obj, dict):
        return {k: _strip_refs(v, ref) for k, v in obj.items()
                if not (isinstance(v, str) and v == ref)
                and not (isinstance(v, list) and
                         any(x == ref for x in v))}
    if isinstance(obj, list):
        return [_strip_refs(x, ref) for x in obj
                if not (isinstance(x, str) and x == ref)]
    return obj


# ═══════════════════════════════════════════════════════════════
#  2. 孤儿扫描 / 清理
# ═══════════════════════════════════════════════════════════════

def _collect_legacy_ids(svc) -> Dict[str, str]:
    """收集 legacy/兼容轨中出现的 skill_id → 来源文件名。"""
    out: Dict[str, str] = {}
    for lp in _legacy_paths(getattr(svc.store, "_path", None)):
        data = _read_json(lp)
        for s in data.get("skills", []):
            sid = str(s.get("id", ""))
            if sid:
                out.setdefault(sid, lp.name)
    return out


def _collect_file_track_ids(svc) -> set:
    """文件轨 skills_repo 下存在的 skill 目录 id。"""
    repo_root = getattr(svc.file_store, "_repo", None)
    if repo_root is None:
        return set()
    root = Path(repo_root)
    if not root.exists():
        return set()
    return {p.name for p in root.iterdir()
            if p.is_dir() and not p.name.startswith(".")}


def scan_orphans(svc) -> List[Dict[str, Any]]:
    """扫描孤儿残留：legacy/文件轨中的**脏数据** id（主轨已无）。

    孤儿判定（排除合法项）：
        - legacy 出现、主轨无 → 真孤儿（legacy 只增不删的历史残留）；
          但若该 id 是文件轨存在的**内置 persona 技能**（非 pd 前缀，
          只走文件轨语义层、本就不注册主轨）则视为合法，排除；
        - 文件轨中 pd- 目录、主轨无 → 蒸馏产物孤儿；
        - 主轨仍存在 → 有效技能，排除。

    Returns: [{id, found_in: [..]}]
    """
    main = set(svc.store._load().keys())
    legacy = _collect_legacy_ids(svc)
    file_track = _collect_file_track_ids(svc)

    orphans: Dict[str, set] = {}
    # legacy 里主轨已无
    for sid in legacy:
        if sid not in main:
            # 文件轨存在的内置技能(非 pd)被旧系统同步进 legacy 是历史行为
            if sid in file_track and not sid.startswith("pd-"):
                continue
            orphans.setdefault(sid, set()).add(f"legacy:{legacy[sid]}")
    # 文件轨 pd- 目录主轨已无 → 蒸馏产物孤儿
    for sid in file_track:
        if sid.startswith("pd-") and sid not in main:
            orphans.setdefault(sid, set()).add("file_track")

    out = [{"id": sid, "found_in": sorted(where)}
           for sid, where in sorted(orphans.items())]
    return out


def cleanup_orphans(svc, *, dry_run: bool = True) -> Dict[str, Any]:
    """清除孤儿残留（dry_run=True 只报告不删）。"""
    orphans = scan_orphans(svc)
    cleaned: List[Dict[str, Any]] = []
    for o in orphans:
        if dry_run:
            continue
        res = remove_skill_everywhere(svc, o["id"])
        cleaned.append({"id": o["id"], **res})
    return {
        "dry_run": dry_run,
        "found": len(orphans),
        "orphans": orphans,
        "cleaned": cleaned,
    }


# ═══════════════════════════════════════════════════════════════
#  3. 无用技能扫描 / 物理淘汰
# ═══════════════════════════════════════════════════════════════

def _idle_days(skill: Any, now: datetime) -> Optional[int]:
    """技能闲置天数：metrics.last_used_at（真实使用时间）距今；
    缺失回退 created_at。"""
    ts = None
    metrics = getattr(skill, "metrics", None)
    if metrics is not None:
        ts = getattr(metrics, "last_used_at", None)
    if not ts:
        ts = getattr(skill, "created_at", None)
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return max(0, int((now - dt).total_seconds() // 86400))
    except (ValueError, TypeError):
        return None


def scan_unused(svc, *, unused_days: int = _DEFAULT_UNUSED_DAYS,
                archived_days: int = _DEFAULT_ARCHIVED_DAYS,
                ) -> List[Dict[str, Any]]:
    """扫描无用技能候选。

    两类视为无用：
      - 状态已 ARCHIVED 且闲置 > archived_days → 物理删除候选
      - 从未使用（usage_count==0）且闲置 > unused_days 且非 enabled → 候选
    返回 [{id, status, usage_count, idle_days, reason}]（不删除）
    """
    now = _now()
    candidates: List[Dict[str, Any]] = []
    for s in svc.store.list_all():
        usage = getattr(getattr(s, "metrics", None), "usage_count", 0) \
            if getattr(s, "metrics", None) is not None else 0
        usage = usage or 0
        status = getattr(s, "status", "")
        enabled = getattr(s, "enabled", True)
        idle = _idle_days(s, now)
        if idle is None:
            continue
        if status == _STATUS_ARCHIVED and idle > archived_days:
            candidates.append({
                "id": s.id, "status": status, "usage_count": usage,
                "idle_days": idle,
                "reason": f"已归档且闲置 {idle} 天(>{archived_days})",
            })
        elif usage == 0 and not enabled and idle > unused_days:
            candidates.append({
                "id": s.id, "status": status, "usage_count": usage,
                "idle_days": idle,
                "reason": f"零使用且停用闲置 {idle} 天(>{unused_days})",
            })
    return candidates


def cleanup_unused(svc, *, dry_run: bool = True,
                   unused_days: int = _DEFAULT_UNUSED_DAYS,
                   archived_days: int = _DEFAULT_ARCHIVED_DAYS,
                   ) -> Dict[str, Any]:
    """物理删除无用技能（dry_run=True 只报告不删）。"""
    candidates = scan_unused(svc, unused_days=unused_days,
                             archived_days=archived_days)
    removed: List[Dict[str, Any]] = []
    for c in candidates:
        if dry_run:
            continue
        res = remove_skill_everywhere(svc, c["id"])
        removed.append({"id": c["id"], **res})
    return {
        "dry_run": dry_run,
        "found": len(candidates),
        "candidates": candidates,
        "removed": removed,
    }


# ═══════════════════════════════════════════════════════════════
#  4. 汇总报告
# ═══════════════════════════════════════════════════════════════

def report(svc) -> Dict[str, Any]:
    """技能中心清理总览报告（不删除任何东西）。"""
    orphans = scan_orphans(svc)
    unused = scan_unused(svc)
    total = len(svc.store.list_all())
    return {
        "ok": True,
        "total_skills": total,
        "orphans": orphans,
        "orphan_count": len(orphans),
        "unused": unused,
        "unused_count": len(unused),
        "note": "调用 cleanup/execute 时请先 review orphans/unused 列表",
    }
