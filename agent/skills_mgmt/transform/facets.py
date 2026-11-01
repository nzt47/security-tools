# -*- coding: utf-8 -*-
"""内涵切分：把一份技能文本拆成五个面（触发 / 步骤 / 参数 / 示例 / 约束）。

【口径】只用**原文里已有的标题与段落**，不生成、不改写（重写在 S3，本阶段只读）。
标题关键词与 agent/skills_mgmt/capability_cards.py 同源，避免两套口径。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from agent.skills_mgmt.capability_cards import FACETS, _facet_text, _sections, _split_fm, _bullets

_MAX_CHARS = 800


def load_text(path) -> str:
    """读一份 artifact：支持 .md 文件或目录（取其 skill.md / SKILL.md / 第一个 .md）。"""
    p = Path(path)
    if p.is_dir():
        for name in ("skill.md", "SKILL.md"):
            if (p / name).is_file():
                return (p / name).read_text(encoding="utf-8", errors="replace")
        mds = sorted(p.glob("*.md"))
        if not mds:
            raise FileNotFoundError("目录里没有 .md: %s" % p)
        return mds[0].read_text(encoding="utf-8", errors="replace")
    if not p.is_file():
        raise FileNotFoundError("文件不存在: %s" % p)
    return p.read_text(encoding="utf-8", errors="replace")


def extract_facets(text: str) -> Dict[str, object]:
    """五面 + 元数据 + 目录。抽不到就是空（不编造）。"""
    meta, body = _split_fm(text)
    sections = _sections(body)
    out: Dict[str, object] = {
        "meta": {k: meta.get(k, "") for k in ("id", "name", "description", "description_zh", "category", "tags")},
        "sections": [s.get("title") for s in sections if s.get("title")],
        "chars": len(text),
    }
    for facet, keys in FACETS.items():
        raw = _facet_text(sections, keys)
        if facet == "example":
            out[facet] = raw[: _MAX_CHARS]
        else:
            out[facet] = _bullets(raw, limit=8)
    out["body"] = body
    return out
