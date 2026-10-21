"""P6 存量资产治理：整理 / 合并 / 拆分 / 剔除 / 重构。

设计边界：
- **只做计划与显式执行**：scan 只读；plan 只产建议；apply 必须 confirm=True，
  默认 dry_run=True。默认动作是归档/草稿，物理删除只走既有 cleanup/orphans。
- **复用既有技能域**：duplicate 检测用 SkillReviewer，merge 用
  SkillsMgmtService.merge_duplicate_skills，评审批量用 curate_skills 的同一口径；
  本模块不重建技能存储，也不另造一套相似度。
- **拆分只落草稿**：父技能不动，子草稿写到 data/ingestion/splits/<plan_id>/，
  不注册、不启用；人工审阅后再走吞噬安装链路。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .adapters.base import first_paragraph
from .models import ConfirmationRequired

DEFAULT_SPLIT_DIR = Path(__file__).resolve().parents[2] / "data" / "ingestion" / "splits"
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _svc(skills_service: Any = None) -> Any:
    if skills_service is not None:
        return skills_service
    from agent.state_manager import get_skills_mgmt_service
    return get_skills_mgmt_service()


def _plain(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _usage(skill: Any) -> int:
    metrics = getattr(skill, "metrics", None)
    try:
        return int(getattr(metrics, "usage_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _headings(content: str) -> List[str]:
    return [m.group(1).strip() for m in _HEADING_RE.finditer(str(content or ""))
            if m.group(1).strip()]


def _content_sections(content: str) -> List[Dict[str, str]]:
    """按 H2 切正文；无 H2 时整篇作为一节。保留原文（不重写）。"""
    lines = str(content or "").splitlines()
    sections: List[Dict[str, str]] = []
    title = "正文"
    body: List[str] = []
    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            if body:
                sections.append({"title": title, "body": "\n".join(body).strip()})
            title = m.group(1).strip()
            body = []
        else:
            body.append(line)
    if body:
        sections.append({"title": title, "body": "\n".join(body).strip()})
    return [s for s in sections if s["body"]]


def _dup_map(dups: Iterable[Any]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for d in dups or []:
        a = str(d.get("skill_a") or "")
        b = str(d.get("skill_b") or "")
        if not a or not b:
            continue
        sim = float(d.get("jaccard") or 0.0)
        for me, other, oname in ((a, b, d.get("name_b")), (b, a, d.get("name_a"))):
            out.setdefault(me, []).append({
                "other_id": other, "other_name": str(oname or other),
                "jaccard": round(sim, 4),
                "recommend_action": str(d.get("recommend_action") or "review"),
            })
    return out


def scan(*, skills_service: Any = None, limit: int = 500,
         min_jaccard: float = 0.75) -> Dict[str, Any]:
    """只读盘点：技能 + 重复 + 孤儿/无用 + 每条的结构化问题。"""
    svc = _svc(skills_service)
    skills = list(svc.store.list_all())
    dups: List[Dict[str, Any]] = []
    try:
        dups = list(svc.reviewer.find_duplicates(skills, min_jaccard=min_jaccard))
    except Exception:  # noqa: BLE001 去重失败不阻断盘点
        dups = []
    report: Dict[str, Any] = {}
    try:
        report = svc.cleanup_report() or {}
    except Exception:  # noqa: BLE001
        report = {}
    orphan_ids = {str(o.get("id")) for o in (report.get("orphans") or []) if o.get("id")}
    unused_ids = {str(u.get("id")) for u in (report.get("unused") or []) if u.get("id")}
    by_id = _dup_map(dups)
    items: List[Dict[str, Any]] = []
    for s in skills[:max(1, int(limit))]:
        sid = str(getattr(s, "id", "") or "")
        if not sid:
            continue
        content = str(getattr(s, "content", "") or "")
        desc = str(getattr(s, "description", "") or "")
        headings = _headings(content)
        issues: List[str] = []
        if by_id.get(sid):
            issues.append("duplicate")
        if len(content) > 12000 and len(headings) >= 3:
            issues.append("split")
        if not desc.strip():
            issues.append("refactor")
        if not bool(getattr(s, "enabled", True)) and _usage(s) == 0:
            issues.append("archive")
        if sid in orphan_ids:
            issues.append("orphan")
        if sid in unused_ids:
            issues.append("unused")
        primary = ""
        for cand in ("duplicate", "archive", "split", "refactor", "orphan", "unused"):
            if cand in issues:
                primary = cand
                break
        items.append({
            "id": sid,
            "name": str(getattr(s, "name", "") or sid),
            "status": _plain(getattr(s, "status", "")),
            "enabled": bool(getattr(s, "enabled", True)),
            "version": str(getattr(s, "version", "") or ""),
            "source": str(getattr(s, "source", "") or ""),
            "category": _plain(getattr(s, "category", "")),
            "usage_count": _usage(s),
            "description_chars": len(desc),
            "content_chars": len(content),
            "headings": headings[:20],
            "issues": issues,
            "primary_issue": primary,
            "duplicates": by_id.get(sid, []),
        })
    return {
        "ok": True,
        "generated_at": _now_iso(),
        "total": len(skills),
        "items": items,
        "duplicates": dups,
        "orphans": report.get("orphans") or [],
        "unused": report.get("unused") or [],
        "summary": {
            "scanned": len(items),
            "duplicate": sum(1 for i in items if "duplicate" in i["issues"]),
            "archive": sum(1 for i in items if "archive" in i["issues"]),
            "split": sum(1 for i in items if "split" in i["issues"]),
            "refactor": sum(1 for i in items if "refactor" in i["issues"]),
            "orphan": len(orphan_ids),
            "unused": len(unused_ids),
        },
    }


def _plan_id(actions: List[Dict[str, Any]]) -> str:
    raw = json.dumps(actions, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return "gp_" + hashlib.sha256(raw).hexdigest()[:24]


def plan(*, skills_service: Any = None, scan_result: Optional[Dict[str, Any]] = None,
         limit: int = 500) -> Dict[str, Any]:
    """把盘点结果转成确定性治理计划；同一批输入 plan_id 稳定。"""
    data = scan_result if scan_result is not None else scan(
        skills_service=skills_service, limit=limit)
    items = {str(i["id"]): i for i in data.get("items") or []}
    actions: List[Dict[str, Any]] = []

    # 重复对：每个 pair 只产一条 merge，保留使用量/版本更高的一方
    seen_pairs = set()
    for sid, info in items.items():
        for dup in info.get("duplicates") or []:
            other = str(dup.get("other_id") or "")
            pair = tuple(sorted((sid, other)))
            if not other or pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            a = items.get(sid, {})
            b = items.get(other, {})
            if (b.get("usage_count", 0), b.get("version", "")) > (
                    a.get("usage_count", 0), a.get("version", "")):
                src, dst = sid, other
            else:
                src, dst = other, sid
            actions.append({
                "action": "merge", "target": src,
                "title": f"合并 {src} → {dst}",
                "reason": f"内容重复（Jaccard {dup.get('jaccard')}），保留使用更多/版本更高的一方",
                "evidence": {"jaccard": dup.get("jaccard"),
                             "src_id": src, "dst_id": dst},
                "payload": {"src_id": src, "dst_id": dst, "strategy": "auto"},
                "destructive": False,
            })

    for sid, item in items.items():
        if "archive" in item["issues"]:
            actions.append({
                "action": "archive", "target": sid,
                "title": f"归档 {sid}",
                "reason": "停用且零使用；只归档不删除，内容仍可恢复",
                "evidence": {"usage_count": item["usage_count"], "enabled": item["enabled"]},
                "payload": {"skill_id": sid},
                "destructive": False,
            })
        if "split" in item["issues"]:
            actions.append({
                "action": "split", "target": sid,
                "title": f"拆分 {sid}",
                "reason": f"正文 {item['content_chars']} 字符且含 {len(item['headings'])} 个 H2 主题；建议按主题拆成子草稿",
                "evidence": {"headings": item["headings"]},
                "payload": {"skill_id": sid, "sections": item["headings"]},
                "destructive": False,
            })
        if "refactor" in item["issues"]:
            actions.append({
                "action": "refactor", "target": sid,
                "title": f"补描述 {sid}",
                "reason": "缺少 description；复用技能域写路径补中文说明并重跑评审",
                "evidence": {"name": item["name"]},
                "payload": {"skill_id": sid, "patch": {}},
                "destructive": False,
            })

    orphans = data.get("orphans") or []
    if orphans:
        actions.append({
            "action": "delete_orphans", "target": "orphans",
            "title": f"清理孤儿（{len(orphans)} 条）",
            "reason": "索引有、文件无；复用既有 cleanup/orphans 物理清理，需二次确认",
            "evidence": {"ids": [o.get("id") for o in orphans][:50]},
            "payload": {"dry_run": False},
            "destructive": True,
        })

    actions.sort(key=lambda a: (a["action"], a["target"]))
    counts: Dict[str, int] = {}
    for a in actions:
        counts[a["action"]] = counts.get(a["action"], 0) + 1
    return {
        "ok": True,
        "plan_id": _plan_id(actions),
        "generated_at": _now_iso(),
        "actions": actions,
        "summary": {"actions": len(actions), "by_action": counts},
        "scan_summary": data.get("summary") or {},
        "requires_confirm": True,
    }


def _audit(action: str, target: str, payload: Dict[str, Any],
           result: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from agent.audit.facade import audit
        entry = audit.record(
            "ingestion.governance.apply", actor="agent.ingestion.governance",
            subject=f"skill:{target}",
            payload={"action": action, "target": target, "payload": payload,
                     "result": result},
            source="agent")
        if entry is None:
            return {"seq": 0, "self_hash": ""}
        return {"seq": int(getattr(entry, "seq", 0) or 0),
                "self_hash": str(getattr(entry, "self_hash", "") or "")}
    except Exception:  # noqa: BLE001 审计失败不阻断已执行动作
        return {"seq": 0, "self_hash": ""}


def _write_split(skill: Any, *, plan_id: str, draft_dir: Any = None) -> Dict[str, Any]:
    base = Path(draft_dir) if draft_dir else DEFAULT_SPLIT_DIR
    root = base / plan_id / str(skill.id)
    sections = _content_sections(str(getattr(skill, "content", "") or ""))
    if not sections:
        sections = [{"title": "正文", "body": str(getattr(skill, "description", "") or "")}]
    files: List[str] = []
    for i, sec in enumerate(sections, start=1):
        child = f"{skill.id}-part-{i:02d}"
        title = str(sec.get("title") or f"第 {i} 部分")
        text = (
            "---\n"
            f"id: {child}\n"
            f"name: {skill.name or skill.id} - {title}\n"
            f"parent_id: {skill.id}\n"
            f"description: 从 {skill.id} 拆分的子草稿（待人工审阅）\n"
            "status: draft\n"
            "source: ingestion-split\n"
            "---\n\n"
            f"# {title}\n\n{sec.get('body', '')}\n"
        )
        path = root / f"{child}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        files.append(str(path.relative_to(root).as_posix()))
    manifest = {
        "parent_id": skill.id,
        "plan_id": plan_id,
        "created_at": _now_iso(),
        "parts": files,
        "note": "子草稿未注册/未启用；人工审阅后走吞噬安装链路",
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"draft_dir": str(root), "parts": files, "count": len(files)}


def apply(*, plan_id: str, action: str, target: str, confirm: bool = False,
          dry_run: bool = True, skills_service: Any = None,
          split_dir: Any = None, plan_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """显式执行一条治理动作；默认 dry-run，真执行必须 confirm=True。"""
    svc = _svc(skills_service)
    data = plan_result if plan_result is not None else plan(skills_service=svc)
    if str(data.get("plan_id")) != str(plan_id):
        raise ValueError(f"plan_id 不匹配（当前 {data.get('plan_id')}）")
    found = next((a for a in (data.get("actions") or [])
                  if a.get("action") == action and a.get("target") == target), None)
    if found is None:
        raise ValueError(f"计划中没有该动作：{action} / {target}")
    if dry_run:
        return {"ok": True, "dry_run": True, "plan_id": plan_id,
                "action": action, "target": target,
                "would": found.get("payload") or {}, "applied": False}
    if confirm is not True:
        raise ConfirmationRequired(
            "治理动作需要显式 confirm=true（先看 plan 的 evidence/reason）",
            detail="dry_run=true 可先看将做什么；真执行会改主轨/归档/写草稿。")

    payload = dict(found.get("payload") or {})
    result: Dict[str, Any] = {}
    if action == "merge":
        result = svc.merge_duplicate_skills(
            str(payload["src_id"]), str(payload["dst_id"]), strategy="auto")
    elif action == "archive":
        skill = svc.get(str(payload["skill_id"]))
        from agent.skills_mgmt.models import SkillStatus
        skill.status = SkillStatus.ARCHIVED
        skill.enabled = False
        skill.touch()
        svc.store.upsert(skill)
        result = {"skill_id": skill.id, "status": "archived", "enabled": False}
    elif action == "refactor":
        sid = str(payload["skill_id"])
        skill = svc.get(sid)
        patch = dict(payload.get("patch") or {})
        if not patch.get("description"):
            patch["description"] = (
                first_paragraph(str(getattr(skill, "content", "") or ""), 300)
                or f"{skill.name or sid} 的说明")
            patch["description_zh"] = patch["description"]
        updated = svc.update(sid, {k: v for k, v in patch.items()
                                   if k in ("name", "tags", "config_schema")})
        try:
            svc.file_store.update_meta(sid, {
                "description": patch.get("description", ""),
                "description_zh": patch.get("description_zh") or patch.get("description", ""),
            })
        except Exception:  # noqa: BLE001 文件轨不可用时主轨 patch 仍然有效
            pass
        try:
            svc.review_skill(sid)
        except Exception:  # noqa: BLE001 评审 advisory
            pass
        result = {"skill_id": updated.id, "name": updated.name,
                  "patch": patch}
    elif action == "split":
        skill = svc.get(str(payload["skill_id"]))
        result = _write_split(skill, plan_id=plan_id, draft_dir=split_dir)
    elif action == "delete_orphans":
        result = svc.cleanup_orphans(dry_run=False)
    else:
        raise ValueError(f"未知治理动作：{action}")

    audit = _audit(action, target, payload, result)
    return {"ok": True, "dry_run": False, "plan_id": plan_id,
            "action": action, "target": target, "applied": True,
            "result": result, "audit": audit}


__all__ = ["DEFAULT_SPLIT_DIR", "apply", "plan", "scan"]
