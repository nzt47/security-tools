# -*- coding: utf-8 -*-
"""技能**分层加载**（L0/L1/L2/L3）—— 对应 docs/rfc/技能转化器_2026-11-01.md §3。

为什么需要：一条 40 KB 的技能一次全塞进上下文，会挤掉别的内容；而多数时候模型只需要
「什么时候用 + 怎么做」这一层。分层后按需逐层展开，并且**每一层都要能被显式展开**（不许藏内容）。

| 层 | 内容 | 预算 |
|---|---|---|
| L0 | name + description + tags（索引里已常驻） | 200 token |
| L1 | CARD.md；没有就**降级**成「标题骨架 + 前 N 行」 | 2k token |
| L2 | skill.md 全文 | 20k token |
| L3 | scripts/ 清单（真执行才取内容） | 单文件 8k |

【降级兼容】老技能没有 CARD.md 也能只加载 L1 —— 不要求先补文件。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from agent.skills_mgmt.skill_md_schema import _split_front_matter

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = ROOT / "data" / "skills_repo"

#: 各层字符预算（按 1 token ≈ 3 字符粗算；超了截断并置 truncated=True）
BUDGET_CHARS = {
    "L0": 600,
    "L1": 6000,
    "L2": 60000,
    "L3": 24000,
}

#: L1 降级时取正文前多少行（再有标题骨架兜底）
_DERIVED_HEAD_LINES = 40


def _skill_dir(skill_id: str, repo: Optional[Path] = None) -> Optional[Path]:
    base = Path(repo) if repo else DEFAULT_REPO
    if not base.is_dir():
        return None
    for child in base.iterdir():
        if child.is_dir() and child.name == skill_id:
            return child
    return None


def _headings(body: str) -> List[str]:
    out = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            out.append(s.lstrip("#").strip())
    return out


def _clip(text: str, layer: str) -> Dict[str, object]:
    budget = BUDGET_CHARS.get(layer, 6000)
    if len(text) <= budget:
        return {"text": text, "truncated": False, "chars": len(text)}
    return {"text": text[:budget].rstrip() + "\n\n…（已截断，展开下一层看全文）",
            "truncated": True, "chars": len(text)}


def load_layer(skill_id: str, layer: str = "L1", repo: Optional[Path] = None) -> Dict[str, object]:
    """取某一层的文本。返回 {ok, skill_id, layer, source, text, truncated, chars}。

    source: card（有 CARD.md）/ derived（从 skill.md 降级）/ full / meta / scripts。
    """
    d = _skill_dir(skill_id, repo)
    if d is None:
        return {"ok": False, "skill_id": skill_id, "layer": layer, "error": "技能不存在"}
    md = d / "skill.md"
    if not md.is_file():
        return {"ok": False, "skill_id": skill_id, "layer": layer, "error": "缺少 skill.md"}
    raw = md.read_text(encoding="utf-8", errors="replace")
    meta, body, _warnings = _split_front_matter(raw)
    meta = meta or {}

    if layer == "L0":
        text = "%s\n%s" % (meta.get("name") or skill_id, (meta.get("description_zh")
                                    or meta.get("description") or ""))
        return {"ok": True, "skill_id": skill_id, "layer": "L0", "source": "meta",
                **_clip(text, "L0")}

    if layer == "L1":
        card = d / "CARD.md"
        if card.is_file():
            return {"ok": True, "skill_id": skill_id, "layer": "L1", "source": "card",
                    **_clip(card.read_text(encoding="utf-8", errors="replace"), "L1")}
        # 降级：标题骨架 + 正文前 N 行（不生成新文件，也不假装是卡片）
        head = "\n".join(body.splitlines()[:_DERIVED_HEAD_LINES]).strip()
        skeleton = " › ".join(_headings(body)[:12])
        text = "%s\n\n[目录] %s\n\n[正文前 %d 行]\n%s" % (
            meta.get("name") or skill_id, skeleton, _DERIVED_HEAD_LINES, head)
        return {"ok": True, "skill_id": skill_id, "layer": "L1", "source": "derived",
                **_clip(text, "L1")}

    if layer == "L2":
        return {"ok": True, "skill_id": skill_id, "layer": "L2", "source": "full",
                **_clip(body, "L2")}

    if layer == "L3":
        scripts = []
        sdir = d / "scripts"
        if sdir.is_dir():
            scripts = sorted(p.name for p in sdir.iterdir() if p.is_file())
        text = "脚本：%s" % (", ".join(scripts) if scripts else "（无）")
        return {"ok": True, "skill_id": skill_id, "layer": "L3", "source": "scripts",
                "scripts": scripts, **_clip(text, "L3")}

    return {"ok": False, "skill_id": skill_id, "layer": layer, "error": "未知层：%s" % layer}
