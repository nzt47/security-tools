"""Claude Skills 适配器（P1）

实测（2026-10-21）：`~/.claude/skills/<id>/SKILL.md` 的 front matter 只有
`name` / `description`（没有 `id`），目录名就是稳定 id。本适配器在
`skill_md` 的通用读取之上加两层识别：

- **路径提示**：来源位于 `.claude` 目录下（默认扫描位置），分数拉高；
- **内容特征**：front matter 出现 `allowed-tools`（Claude Code 常见扩展键）也算证据。

它**不执行**任何脚本；归一化仍由 service 统一做（补 manifest、按目录名生成 id）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import find_skill_md, read_text_file, source_ecosystem, split_front_matter
from .skill_md import SkillMarkdownAdapter


class ClaudeAdapter(SkillMarkdownAdapter):
    name = "claude"
    priority = 35

    def sniff(self, source: Any) -> int:
        if getattr(source, "kind", "") != "path":
            return 0
        path = Path(source.path)
        hint = source_ecosystem(source, path) == "claude"
        if hint:
            base = super().sniff(source)
            return min(100, base + 30) if base else 0
        # 非 .claude 路径：只认 Claude 特有键，避免把普通 SKILL.md 都当 claude
        if path.is_dir():
            skill_md = find_skill_md(path)
            if skill_md is None:
                return 0
            text, _, _ = read_text_file(skill_md, max_bytes=16 * 1024)
            fm, _, _ = split_front_matter(text)
            if "allowed-tools" in fm or "allowed_tools" in fm:
                return 85
        return 0

    def extract(self, source: Any):
        asset = super().extract(source)
        asset.meta["ecosystem"] = "claude"
        return asset
