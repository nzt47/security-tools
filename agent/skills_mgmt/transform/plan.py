# -*- coding: utf-8 -*-
"""把 S1 切面 → S2 对比 → S3 卡片草案 → S4 审核 串成一份**只读计划**。

输出给 CLI / API 用；本模块不写任何文件（P1 的硬约束：跑完 git status 必须为空）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Optional

from .compare import compare_with_repo
from .facets import extract_facets, load_text
from .review import review_text


def _card_draft(facets: Dict[str, object]) -> Dict[str, object]:
    meta = facets.get("meta") or {}
    desc = meta.get("description_zh") or meta.get("description") or ""
    if len(desc) > 60:
        desc = desc[:60].rstrip() + "…"
    return {
        "name": meta.get("name") or meta.get("id") or "",
        "summary": desc,
        "trigger": (facets.get("trigger") or [])[:4],
        "steps": (facets.get("steps") or [])[:6],
        "params": (facets.get("params") or [])[:6],
        "example": (facets.get("example") or "")[:400],
        "guard": (facets.get("guard") or [])[:4],
    }


def build_plan(artifact, repo_path: Optional[Path] = None, top: int = 5) -> Dict[str, object]:
    """artifact: 文件或目录路径。返回完整计划（dict），不改任何状态。"""
    text = load_text(artifact)
    facets = extract_facets(text)
    compare = compare_with_repo(facets, repo_path=repo_path, top=top)
    review = review_text(text, name=str(artifact))
    verdict = compare["verdict"]
    best = compare.get("best") or {}
    has_substance = any((facets.get(k) or []) for k in ("trigger", "steps", "params", "guard")) or bool(facets.get("example"))

    actions = []
    if not has_substance:
        actions.append("**没有可提取的内涵**（正文不是技能形状：无触发/步骤/参数/示例/约束）→ 退回，先由人补卡片或拒收")
    if review["verdict"] == "block":
        actions.append("先处理 block 级发现，转化不启动")
    if verdict == "duplicate":
        actions.append("整体重复：不新建；只把新增示例/参数以 overlay 并入 %s" % best.get("skill_id"))
    elif verdict == "incremental":
        actions.append("只重写低相似面，并入 %s 的新小节（absorbed_into）" % best.get("skill_id"))
    elif verdict == "conflict":
        actions.append("同面冲突（%s）：交人看并排 diff，选 merge / keep / variant" % best.get("skill_id"))
    else:
        actions.append("不相关：重写成新能力")
    if review["verdict"] == "warn":
        actions.append("审核有 warn：人确认后才允许 --apply")
    actions.append("源技能转化后归档到 data/ingestion/transforms/，退出检索面与能力清单")

    return {
        "artifact": str(artifact),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "chars": len(text),
        "meta": facets.get("meta"),
        "sections": facets.get("sections"),
        "facets": {k: facets.get(k) for k in ("trigger", "steps", "params", "example", "guard")},
        "compare": compare,
        "review": review,
        "card_draft": _card_draft(facets),
        "next": actions,
    }
