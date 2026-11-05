# -*- coding: utf-8 -*-
"""学习来源的草稿落地：记忆抽象 → **隔离区草稿 + 统一作业台账**（source=learning）。

【为什么不再直接注册】2026-11-05 IA 决定（docs/ia/2026-11-05_能力获取与技能中心_IA决定.md §4）：
同一个"生成草稿"的动作，三条入口的落库语义原来各不相同 —— 摄取走隔离区草稿 + 台账，
对话生成走草稿 + 自动评审，唯独**记忆抽象直接 create_manual 写技能库、且不进台账**。
后果是"学习到的东西"能绕过审核与登记记录直接生效，而人看不到它从哪来。

从此统一：学习只产出**草稿**（与摄取同一种产物、同一本台账），
登记/启用仍由「能力获取 → 转化队列」的人来决定。

【产物形状】与 `agent/ingestion/drafts.write_skill_draft` 对齐（SKILL.md 9 键 + CARD.md + manifest），
这样队列里的「登记为技能」对两种来源一视同仁（register_skill_job 只认 SKILL.md + manifest.skill_id）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DRAFT_DIR = ROOT / "data" / "ingestion" / "drafts"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")
_STEP_RE = re.compile(r"^(?:\d+[.、)]|[-*])\s+(.+)$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _draft_id(skill_id: str, source_ref: str) -> str:
    seed = f"{skill_id}|{source_ref}".encode("utf-8")
    return "learn_" + hashlib.sha256(seed).hexdigest()[:16]


def _front_matter(*, skill_id: str, name: str, description: str,
                  tags: List[str], version: str, source: str) -> str:
    tag_text = ", ".join(t for t in tags if str(t).strip())
    return (
        "---\n"
        f"id: {skill_id}\n"
        f"name: {name}\n"
        f"description: {(description or name).replace(chr(10), ' ')[:200]}\n"
        "author: yunshu-learning\n"
        "category: learned\n"
        f"source: {source}\n"
        f"tags: [{tag_text}]\n"
        "content_type: markdown\n"
        "enabled: false\n"
        f"version: {version}\n"
        "status: draft\n"
        "---\n\n"
    )


def _card(skill_id: str, name: str, description: str, body: str) -> str:
    """CARD.md：只用**确定性抽取**（不调 LLM、不编造），与摄取草稿同一口径。"""
    steps: List[str] = []
    in_steps = False
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_steps = stripped.lstrip("#").strip().lower() in (
                "步骤", "steps", "执行步骤", "workflow")
            continue
        if in_steps:
            match = _STEP_RE.match(stripped)
            if match:
                steps.append(match.group(1).strip())
    lines = [
        "# " + (name or skill_id), "",
        (description or "（来源未提供说明）"), "",
        "## 何时使用", "",
        "- 见 SKILL.md 正文（本卡由记忆聚类确定性抽取，未改写原文）", "",
        "## 步骤", "",
    ]
    if steps:
        lines.extend("- " + s for s in steps[:20])
    else:
        lines.append("（原文没有可识别的步骤）")
    lines.extend(["", "## 参数", "", "（见 SKILL.md）", "",
                  "## 约束 / 反例", "", "（见 SKILL.md）", "",
                  "## 示例", "", "（无）", ""])
    return "\n".join(lines)


def write_learning_draft(*, skill_id: str, name: str, description: str, body: str,
                         tags: Optional[List[str]] = None, source_ref: str = "",
                         version: str = "0.1.0", source: str = "learning",
                         draft_dir: Optional[Any] = None) -> Dict[str, Any]:
    """把一条学习草稿写进隔离区，并在统一台账里记 candidate。

    Raises:
        ValueError: skill_id 非法（上层据此如实报错，不写半成品）
    """
    sid = str(skill_id or "").strip()
    if not _ID_RE.match(sid):
        raise ValueError(f"技能 id 非法：{sid!r}")
    base = Path(draft_dir) if draft_dir else DEFAULT_DRAFT_DIR
    did = _draft_id(sid, source_ref)
    root = base / did
    root.mkdir(parents=True, exist_ok=True)

    skill_md = _front_matter(skill_id=sid, name=name, description=description,
                             tags=list(tags or []), version=version, source=source) \
        + str(body or "")
    (root / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (root / "CARD.md").write_text(
        _card(sid, name, description, body), encoding="utf-8")
    warnings = [
        "本目录不会被任何加载器扫描：不登记就不会出现在技能列表里。",
        "登记/启用请走「能力获取 → 转化队列」（来源标 learning）。",
    ]
    manifest = {
        "schema_version": 1,
        "draft_id": did,
        "form": "skill",
        "status": "draft",
        "enabled": False,
        "plan_id": "",
        "source_ref": source_ref or ("learning:" + sid),
        "adapter": "learning",
        "skill_id": sid,
        "skill_name": name,
        "created_at": _now_iso(),
        "files": ["SKILL.md", "CARD.md", "manifest.json"],
        "warnings": warnings,
        "review": [
            "人工审阅 SKILL.md / CARD.md（内容是否可内化）",
            "按需登记到技能库（转化队列 → 登记）",
            "走审批后再启用（学习不自动注册）",
        ],
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 统一作业台账（best-effort：台账坏了不许让学习失败）
    try:
        from agent.skills_mgmt.transform import jobs as _jobs
        _jobs.upsert_job(source="learning", source_ref=did, form="skill",
                         title=name or sid, artifacts=str(root), status="candidate")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[learning] 作业台账登记失败（不影响草稿）: %s", exc)
    return {"draft_id": did, "draft_dir": str(root), "skill_id": sid,
            "files": manifest["files"], "warnings": warnings}


__all__ = ["DEFAULT_DRAFT_DIR", "write_learning_draft"]
