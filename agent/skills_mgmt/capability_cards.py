# -*- coding: utf-8 -*-
"""能力卡（Capability Card）——把「一条技能」压成界面能一眼看懂的一张卡。

【为什么】2026-11-01 Owner：「UI 界面必须列出能力卡，使我能知道自身有什么能力」。
现状的「统一表」只有 id / 描述 / 状态，看不出**这条能力到底怎么用**；
转化器方案（docs/rfc/技能转化器_2026-11-01.md §3）把加载分成 L0 摘要 / L1 能力卡 / L2 全文 / L3 资源，
本模块负责 **L1**：有 CARD.md 就直接读，没有就从 skill.md **降级派生**（不要求老技能补文件）。

【边界】只读、不写任何文件；派生只用正文既有标题与段落，**不编造**。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = ROOT / "data" / "skills_repo"

#: facet → 标题关键词（与 skill_md_schema.USAGE_SECTIONS 同源口径，另加步骤/参数/约束）
FACETS = {
    "trigger": ("何时使用", "使用场景", "使用时机", "适用场景", "适用条件", "触发条件", "when to use", "usage"),
    "steps": ("步骤", "流程", "操作", "做法", "how to"),
    "params": ("参数", "配置", "选项", "输入"),
    "example": ("示例", "例子", "范例", "example"),
    "guard": ("约束", "边界", "反例", "注意", "坑"),
}

#: 每面最多取多少字符（卡片是 L1，不是全文）
_MAX_CHARS = 600


def _split_fm(text: str):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            meta = {}
            for line in lines[1:i]:
                m = re.match(r"^([A-Za-z_][A-Za-z0-9_\-]*):[ \t]*(.*)$", line)
                if m:
                    meta[m.group(1)] = m.group(2).strip().strip("'\"")
            return meta, "\n".join(lines[i + 1:])
    return {}, text


def _sections(body: str) -> List[Dict[str, object]]:
    """按二级/三级标题切段；返回 [{title, text}]，代码块内的 # 不算标题。"""
    out: List[Dict[str, object]] = []
    title = ""
    buf: List[str] = []
    in_code = False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        if not in_code and re.match(r"^#{2,3}\s+", line):
            if title or buf:
                out.append({"title": title, "text": "\n".join(buf).strip()})
            title = line.lstrip("#").strip()
            buf = []
            continue
        buf.append(line)
    if title or buf:
        out.append({"title": title, "text": "\n".join(buf).strip()})
    return out


def _clip(text: str, limit: int = _MAX_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _facet_text(sections: List[Dict[str, object]], keys) -> str:
    picked = []
    for sec in sections:
        title = str(sec.get("title") or "").lower()
        if title and any(k.lower() in title for k in keys):
            body = str(sec.get("text") or "").strip()
            if body:
                picked.append(body)
    return _clip("\n".join(picked))


def _bullets(text: str, limit: int = 6) -> List[str]:
    """把一段正文抽成要点：优先列表项，其次短句。"""
    items = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("```"):
            continue
        if re.match(r"^([-*+]|\d+[.)])\s+", s):
            s = re.sub(r"^([-*+]|\d+[.)])\s+", "", s)
        if len(s) > 120:
            s = s[:120].rstrip() + "…"
        items.append(s)
        if len(items) >= limit:
            break
    return items


def build_card(skill_dir: Path) -> Optional[Dict[str, object]]:
    """单条技能 → 一张卡。有 CARD.md 用它（layer=card），否则从 skill.md 派生（layer=derived）。"""
    md = skill_dir / "skill.md"
    card_md = skill_dir / "CARD.md"
    if not md.is_file() and not card_md.is_file():
        return None
    src = card_md if card_md.is_file() else md
    raw = src.read_text(encoding="utf-8", errors="replace")
    meta, body = _split_fm(raw)
    sections = _sections(body)
    desc = meta.get("description_zh") or meta.get("description") or ""
    summary = desc.strip()
    if len(summary) > 60:
        summary = summary[:60].rstrip() + "…"
    card = {
        "skill_id": skill_dir.name,
        "name": meta.get("name") or skill_dir.name,
        "summary": summary,
        "category": meta.get("category") or "",
        "tags": [t.strip() for t in str(meta.get("tags") or "").strip("[]").split(",") if t.strip()],
        "layer": "card" if card_md.is_file() else "derived",
        "trigger": _bullets(_facet_text(sections, FACETS["trigger"])),
        "steps": _bullets(_facet_text(sections, FACETS["steps"])),
        "params": _bullets(_facet_text(sections, FACETS["params"])),
        "example": _clip(_facet_text(sections, FACETS["example"]), 400),
        "guard": _bullets(_facet_text(sections, FACETS["guard"]), 4),
        "sections": [s.get("title") for s in sections if s.get("title")][:12],
        "body_chars": len(raw),
    }
    return card


def build_cards(repo_path: Optional[Path] = None) -> List[Dict[str, object]]:
    repo = Path(repo_path) if repo_path else DEFAULT_REPO
    if not repo.is_dir():
        return []
    out: List[Dict[str, object]] = []
    for child in sorted(repo.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith("."):
            continue
        card = build_card(child)
        if card:
            out.append(card)
    return out
