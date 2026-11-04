# -*- coding: utf-8 -*-
"""S3/S5 落地：把只读计划变成**可 review 的产物**（P2）。

【边界】没有 LLM 就不「重写」正文 —— P2 只做三件确定性的事：
  1. 把本次转化落成 `data/ingestion/transforms/<transform_id>/`（原文归档 + 对比 + 审核 + 卡片）；
  2. 生成 **CARD.md**（L1 能力卡，从五面确定性抽取，不编造）；
  3. 按 `--into <id>` 并入既有技能，或 `--new <id>` 建新技能目录，再复用 intake 的 git 流程与 open_pr 开 PR。

「用自己的表述重写正文」需要 LLM + 引用校验，属 P5；本模块**不假装**做了那一步。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Dict, Optional

from .facets import extract_facets, load_text
from .plan import build_plan

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
TRANSFORMS = Path("data") / "ingestion" / "transforms"
SKILLS_REPO = Path("data") / "skills_repo"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")
_STEP_RE = re.compile(r"^(?:\d+[.、)]|[-*])\s+")


def transform_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def render_card(card: Dict[str, object]) -> str:
    """把卡片草案渲染成 CARD.md（L1：≤150 行，只有原文抽出来的东西）。"""
    def _ul(items):
        return "\n".join("- %s" % x for x in items) if items else "（原文没有这一节）"

    return (
        "# %s\n\n%s\n\n## 何时使用\n\n%s\n\n## 步骤\n\n%s\n\n"
        "## 参数\n\n%s\n\n## 约束 / 反例\n\n%s\n\n## 示例\n\n%s\n"
        % (card.get("name") or "", card.get("summary") or "",
           _ul(card.get("trigger") or []), _ul(card.get("steps") or []),
           _ul(card.get("params") or []), _ul(card.get("guard") or []),
           (card.get("example") or "（原文没有示例）"))
    )


def _retain_days() -> int:
    """隔离副本保留天数（与 ingestion.rewrite 同一开关；默认 0 = 不保留）"""
    from agent.ingestion.rewrite import retain_quarantine_copy_days
    return retain_quarantine_copy_days()


def _evidence(text: str, plan: Dict[str, object]) -> Dict[str, object]:
    """存档证据：指纹 + 抽取要点 + 逐字引用（**确定性，不改写原文**）

    为什么不再写 raw.md：S5 的口径是"读完即弃" —— 外来原文不落盘，只留可核对的证据。
    quotes 只收**能在原文逐字找到**的卡片条目，读档时能证明"这句确实来自原文"。
    """
    from datetime import datetime, timedelta, timezone

    draft = plan.get("card_draft") or {}
    items = []
    for key in ("trigger", "steps", "params", "guard"):
        for item in (draft.get(key) or []):
            items.append(str(item).strip())
    compact = " ".join(str(text).split())
    quotes = []
    for item in items:
        if len(item) < 4:      # 中文条目短但信息密度高（"先做什么" = 4 字）
            continue
        if " ".join(item.split()) not in compact:
            continue
        if item in quotes:
            continue
        quotes.append(item[:200])
        if len(quotes) >= 20:
            break
    key_points = []
    for line in str(text or "").splitlines():
        s = line.strip()
        if s.startswith("#"):
            point = s.lstrip("#").strip()
        elif s[:2] in ("- ", "* ") or _STEP_RE.match(s):
            point = _STEP_RE.sub("", s).strip()
        else:
            continue
        if point and point not in key_points:
            key_points.append(point[:120])
        if len(key_points) >= 12:
            break
    days = _retain_days()
    evidence: Dict[str, object] = {
        "sha256": plan.get("sha256"),
        "chars": len(str(text or "")),
        "key_points": key_points,
        "quotes": quotes,
        "retained": days > 0,
    }
    if days > 0:
        evidence["retain_until"] = (datetime.now(timezone.utc)
                                    + timedelta(days=days)).replace(microsecond=0).isoformat()
    return evidence


def _front_matter(name: str, skill_id: str, desc: str) -> str:
    desc = (desc or "").replace("\n", " ")[:200]
    return ("---\nid: %s\nname: %s\ndescription: %s\n"
            "author: yunshu-intake\ncategory: custom\nsource: intake\ntags: []\n"
            "content_type: markdown\nenabled: true\nversion: 0.1.0\nstatus: draft\n---\n\n"
            % (skill_id, name, desc))


def apply_plan(artifact, *, into: Optional[str] = None, new: Optional[str] = None,
               execute: bool = False, root: Optional[Path] = None) -> Dict[str, object]:
    """落产物（execute=False 只算路径与内容，不写盘）。

    into: 并入既有技能 id（只写 CARD.md）；new: 新建技能 id（写 skill.md + CARD.md）。
    两者都不给 ⇒ 只落 transforms/ 归档，不动 skills_repo。
    """
    base = Path(root) if root else ROOT
    text = load_text(artifact)
    plan = build_plan(artifact)
    tid = transform_id(text)
    tdir = base / TRANSFORMS / tid

    card_md = render_card(plan["card_draft"])
    # 【S5 2026-11-04「读完即弃」】存档拆成两半：
    #   · evidence.json —— **永久**留存：sha256 指纹 + 抽取要点 + 逐字引用（可核对、不可还原）
    #   · raw.md        —— **隔离副本**：只服务"候选 → 已登记"之间的 LLM 重写；
    #                      作业进入 registered/enabled 时由 jobs 清掉（见 jobs._purge_raw_copy），
    #                      开关 CP_RETAIN_QUARANTINE_COPY_DAYS>0 才按天保留（查纠纷用）
    evidence = _evidence(text, plan)
    files = {
        "evidence.json": json.dumps(evidence, ensure_ascii=False, indent=1),
        "raw.md": text,
        "card.md": card_md,
        "compare.json": json.dumps(plan["compare"], ensure_ascii=False, indent=1),
        "review.json": json.dumps(plan["review"], ensure_ascii=False, indent=1),
        "manifest.json": json.dumps({
            "transform_id": tid,
            "artifact": str(artifact),
            "sha256": plan["sha256"],
            "verdict": plan["compare"]["verdict"],
            "review": plan["review"]["verdict"],
            "retained": bool(evidence["retained"]),
            "retain_until": evidence.get("retain_until"),
            "into": into, "new": new,
            "absorbed_into": (plan["compare"].get("best") or {}).get("skill_id"),
            "next": plan["next"],
        }, ensure_ascii=False, indent=1),
    }

    written = []
    targets = []
    if into:
        if not _ID_RE.match(into):
            return {"ok": False, "error": "非法 into id"}
        targets.append(base / SKILLS_REPO / into / "CARD.md")
    elif new:
        if not _ID_RE.match(new):
            return {"ok": False, "error": "非法 new id"}
        skill_dir = base / SKILLS_REPO / new
        name = str((plan.get("meta") or {}).get("name") or new)
        desc = str((plan.get("meta") or {}).get("description_zh")
                   or (plan.get("meta") or {}).get("description") or "")
        body = _front_matter(name, new, desc) + text
        targets.append(skill_dir / "skill.md")
        files["skill.md"] = body
        targets.append(skill_dir / "CARD.md")

    if execute:
        tdir.mkdir(parents=True, exist_ok=True)
        for fname, content in files.items():
            (tdir / fname).write_text(content, encoding="utf-8")
            written.append(str((tdir / fname).relative_to(base)))
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            content = card_md if target.name == "CARD.md" else files.get("skill.md", text)
            target.write_text(content, encoding="utf-8")
            written.append(str(target.relative_to(base)))
        # S1：落盘后登记进**统一转化作业台账**（best-effort，台账坏了不影响落地）
        try:
            from . import jobs as _jobs
            landing = into or new or ""
            _jobs.upsert_job(
                source="transform", source_ref=tid, form="skill",
                title=str((plan.get("meta") or {}).get("name") or landing or tid),
                artifacts=str(tdir),
                target=({"kind": "skill", "id": landing} if landing else {}),
                status=("registered" if landing else "reviewed"),
                note=str(plan["next"] or ""),
                root=base,   # 台账跟着产物根走（CLI --root / 单测 tmp 都不污染仓库 data/）
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[transform] 作业台账登记失败 tid=%s: %s", tid, exc)

    return {
        "ok": True,
        "transform_id": tid,
        "transform_dir": str(tdir),
        "planned_files": sorted(files) + ([str(t.relative_to(base)) for t in targets] if not execute else []),
        "written": written,
        "verdict": plan["compare"]["verdict"],
        "review_verdict": plan["review"]["verdict"],
        "blocked": plan["review"]["verdict"] == "block",
        "next": plan["next"],
    }
