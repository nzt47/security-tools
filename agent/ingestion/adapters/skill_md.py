"""Markdown 技能入口适配器（SKILL.md / skill.md）

这是"任意 markdown/文档"的主入口：只要目录根或单文件里有 SKILL.md/skill.md，
就由本适配器读取 front matter 与正文。它**不判断**是否符合云枢 schema ——
front matter 键名可以完全不同（claude/openclaw/trae 形态），判定与键映射
统一交给 `service.classify()` / `service.normalize()`，这样新增生态只需要
补 sniff 特征，不必复制一套判定逻辑。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import (
    Adapter,
    ExtractedAsset,
    collect_scripts,
    find_skill_md,
    read_text_file,
    split_front_matter,
)


class SkillMarkdownAdapter(Adapter):
    name = "skill_md"
    priority = 20

    def sniff(self, source: Any) -> int:
        if getattr(source, "kind", "") != "path":
            return 0
        root = Path(source.path)
        if root.is_file():
            if root.suffix.lower() not in (".md", ".markdown"):
                return 0
            text, _, _ = read_text_file(root, max_bytes=16 * 1024)
            fm, _, _ = split_front_matter(text)
            score = 25
            if fm:
                score += 10
            if fm.get("name") or fm.get("title"):
                score += 10
            if fm.get("description") or fm.get("summary"):
                score += 5
            if fm.get("id"):
                score += 5
            return min(score, 55)
        if root.is_dir() and find_skill_md(root) is not None:
            text, _, _ = read_text_file(find_skill_md(root), max_bytes=16 * 1024)
            fm, _, _ = split_front_matter(text)
            score = 60
            if fm:
                score += 8
            if fm.get("name") or fm.get("title"):
                score += 6
            if fm.get("description") or fm.get("summary"):
                score += 4
            if fm.get("id"):
                score += 2
            return min(score, 80)
        return 0

    def extract(self, source: Any) -> ExtractedAsset:
        root = Path(source.path)
        warnings: list[str] = []
        files: dict[str, str] = {}
        if root.is_file():
            text, warns, _ = read_text_file(root)
            warnings.extend(warns)
            fm, body, fm_warns = split_front_matter(text)
            warnings.extend(fm_warns)
            return ExtractedAsset(
                adapter=self.name,
                source_kind="file",
                front_matter=fm,
                body=body,
                warnings=warnings,
                canonical_layout=False,
                primary_file=root.name,
                meta={"source_file": root.name},
            )

        skill_md = find_skill_md(root)
        if skill_md is None:
            raise OSError(f"目录下没有 SKILL.md/skill.md: {root}")
        text, warns, _ = read_text_file(skill_md)
        warnings.extend(warns)
        fm, body, fm_warns = split_front_matter(text)
        warnings.extend(fm_warns)
        files, script_warns = collect_scripts(root)
        warnings.extend(script_warns)
        return ExtractedAsset(
            adapter=self.name,
            source_kind="directory",
            front_matter=fm,
            body=body,
            files=files,
            warnings=warnings,
            canonical_layout=True,
            primary_file=skill_md.name,
            meta={"source_file": skill_md.name},
        )
