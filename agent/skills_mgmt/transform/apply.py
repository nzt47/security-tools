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
import re
from pathlib import Path
from typing import Dict, Optional

from .facets import extract_facets, load_text
from .plan import build_plan

ROOT = Path(__file__).resolve().parents[3]
TRANSFORMS = Path("data") / "ingestion" / "transforms"
SKILLS_REPO = Path("data") / "skills_repo"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")


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
    files = {
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
