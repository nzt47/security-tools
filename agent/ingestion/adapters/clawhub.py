"""ClawHub 系（openclaw / trae）公共读取层（P1）

实测（2026-10-21）本机真实结构：

- `~/.openclaw/workspace/skills/<id>/`：`SKILL.md` + `_meta.json` + `skill-card.md`；
  front matter 可能带 `metadata.openclaw`、`homepage`、`license`；
- `~/.trae-cn/skills/<id>/`：同样是 `SKILL.md` + `_meta.json` + `skill-card.md`
  （ClawHub 形态），front matter 与 claude 相同（name/description）；
- `_meta.json` 字段：`ownerId` / `slug` / `version` / `publishedAt`；
- 少数技能（如 `flow`）在正文开头还有**第二段 front matter**（summary/tags），
  通用解析器只会吃掉第一段，本层把第二段合并进元数据并把它从正文剥离。

**只读取，不执行**。生态适配器只在有生态证据时认领：路径提示（默认扫描目录）
或内容特征（openclaw 的 metadata.openclaw）；否则交给通用 `skill_md`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from .base import (
    find_skill_md,
    read_json_file,
    read_text_file,
    source_ecosystem,
    split_front_matter,
)
from .skill_md import SkillMarkdownAdapter

#: skill-card.md 只取前 600 字进计划（它不是权威说明，只作来源证据）
SKILL_CARD_PREVIEW = 600


def split_trailing_front_matter(body: str) -> Tuple[Dict[str, Any], str, List[str]]:
    """处理 openclaw/trae 的「第二段 front matter」。

    形态（实测 `flow/SKILL.md`）::

        ---
        name: flow
        description: ...
        ---
        ---
        summary: ...
        tags: [...]
        ---

        # Flow

    通用 `split_front_matter` 第一次已吃掉第一段，剩下的 body 以 `---` 开头；
    本函数再解析一次，返回 (合并元数据, 去头正文, 告警)。
    """
    if not str(body or "").startswith("---"):
        return {}, str(body or ""), []
    meta, rest, warnings = split_front_matter(str(body))
    if not meta:
        return {}, str(body or ""), warnings
    return dict(meta), rest, warnings + ["检测到第二段 front matter（openclaw/trae 变体），已合并"]


class ClawHubAdapter(SkillMarkdownAdapter):
    """openclaw / trae 共用的读取逻辑；子类只声明生态与识别证据。"""

    ecosystem = ""
    #: 子类是否要求路径提示（trae 没有独立内容键，只能靠默认扫描位置）
    requires_path_hint = False

    def _evidence_score(self, source: Any, path: Path) -> Tuple[int, Dict[str, Any]]:
        if not path.is_dir() or find_skill_md(path) is None:
            return 0, {}
        hint = source_ecosystem(source, path) == self.ecosystem
        if self.requires_path_hint and not hint:
            return 0, {}
        meta_path = path / "_meta.json"
        meta = {}
        if meta_path.is_file():
            meta, _ = read_json_file(meta_path)
        if not isinstance(meta, dict):
            meta = {}
        card = (path / "skill-card.md").is_file()
        score = 0
        if hint:
            score += 45
        fm: Dict[str, Any] = {}
        skill_md = find_skill_md(path)
        if skill_md is not None:
            text, _, _ = read_text_file(skill_md, max_bytes=16 * 1024)
            fm, _, _ = split_front_matter(text)
        nested = fm.get("metadata")
        has_openclaw_meta = isinstance(nested, dict) and "openclaw" in nested
        if self.ecosystem == "openclaw" and (has_openclaw_meta or "homepage" in fm):
            score += 30
        if isinstance(meta.get("slug"), str) and isinstance(meta.get("ownerId"), str):
            score += 12
        if card:
            score += 5
        return score, {"meta": meta, "card": card, "fm": fm}

    def sniff(self, source: Any) -> int:
        if getattr(source, "kind", "") != "path":
            return 0
        path = Path(source.path)
        evidence, _ = self._evidence_score(source, path)
        if evidence <= 0:
            return 0
        base = super().sniff(source)
        return min(100, base + evidence) if base else 0

    def extract(self, source: Any):
        asset = super().extract(source)
        path = Path(source.path)
        meta: Dict[str, Any] = {}
        meta_path = path / "_meta.json"
        if meta_path.is_file():
            data, warns = read_json_file(meta_path)
            asset.warnings.extend(warns)
            if isinstance(data, dict):
                meta = data
        card_path = path / "skill-card.md"
        card_text = ""
        if card_path.is_file():
            card_text, warns, _ = read_text_file(card_path, max_bytes=64 * 1024)
            asset.warnings.extend(warns)

        trailing, body, warns = split_trailing_front_matter(asset.body)
        asset.warnings.extend(warns)
        if body != asset.body:
            asset.body = body
        if trailing:
            merged = dict(asset.front_matter or {})
            for key, value in trailing.items():
                merged.setdefault(key, value)
            asset.front_matter = merged

        # _meta.json 是 ClawHub 的 id/version 事实源；front matter 有值时不覆盖
        if isinstance(meta.get("slug"), str) and meta["slug"].strip():
            asset.front_matter.setdefault("id", meta["slug"].strip())
        if isinstance(meta.get("version"), str) and meta["version"].strip():
            asset.front_matter.setdefault("version", meta["version"].strip())

        asset.meta["ecosystem"] = self.ecosystem
        asset.meta["clawhub_meta"] = {
            key: meta.get(key) for key in ("slug", "version", "ownerId", "publishedAt")
            if meta.get(key) is not None
        }
        asset.meta["skill_card"] = bool(card_text)
        if card_text:
            asset.meta["skill_card_preview"] = card_text[:SKILL_CARD_PREVIEW]
        return asset


class OpenClawAdapter(ClawHubAdapter):
    name = "openclaw"
    ecosystem = "openclaw"
    priority = 36
    requires_path_hint = False


class TraeAdapter(ClawHubAdapter):
    name = "trae"
    ecosystem = "trae"
    priority = 37
    requires_path_hint = True
