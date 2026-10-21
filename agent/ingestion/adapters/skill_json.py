"""云枢 JSON 清单适配器（skill.json / manifest.json）

识别两种 JSON：
- `skill.json`：云枢旧版/紧凑技能清单（`id` + `name` + `content`）；
- `manifest.json`：zip 技能包的清单（`id` + `name`，正文在 `skill.md`）。

两者都只**读**，不执行、不写回。`skill.json` 缺 `content` 或 `manifest.json`
缺正文时仍可被识别，但会落成 L1（由 service 决定），而不是在这里猜内容。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import (
    Adapter,
    ExtractedAsset,
    collect_scripts,
    find_skill_md,
    read_json_file,
    read_text_file,
    split_front_matter,
)


class SkillJsonAdapter(Adapter):
    name = "skill_json"
    priority = 30

    def sniff(self, source: Any) -> int:
        if getattr(source, "kind", "") != "path":
            return 0
        root = Path(source.path)
        if root.is_file():
            if root.suffix.lower() != ".json":
                return 0
            data, _ = read_json_file(root)
            if not isinstance(data, dict):
                return 0
            if data.get("id") and ("content" in data or "name" in data):
                return 70 if "content" in data else 60
            return 0
        if not root.is_dir():
            return 0
        skill_json = root / "skill.json"
        if skill_json.is_file():
            data, _ = read_json_file(skill_json)
            if isinstance(data, dict):
                score = 82
                if data.get("id"):
                    score += 5
                if data.get("content"):
                    score += 8
                if find_skill_md(root) is not None:
                    score += 3
                return min(score, 100)
            return 0
        manifest_json = root / "manifest.json"
        if manifest_json.is_file():
            data, _ = read_json_file(manifest_json)
            if isinstance(data, dict) and data.get("id"):
                return 55
        return 0

    def extract(self, source: Any) -> ExtractedAsset:
        root = Path(source.path)
        warnings: list[str] = []
        files: dict[str, str] = {}
        if root.is_file():
            manifest, warns = read_json_file(root)
            warnings.extend(warns)
            if not isinstance(manifest, dict):
                manifest = None
            body = ""
            if isinstance(manifest, dict) and isinstance(manifest.get("content"), str):
                body = manifest["content"]
            return ExtractedAsset(
                adapter=self.name,
                source_kind="file",
                manifest=manifest,
                manifest_kind="skill_json" if isinstance(manifest, dict) and "content" in manifest
                else "manifest_json",
                body=body,
                warnings=warnings,
                canonical_layout=False,
                primary_file=root.name,
                meta={"source_file": root.name},
            )

        manifest = None
        manifest_kind = ""
        primary = ""
        for filename, kind in (("skill.json", "skill_json"), ("manifest.json", "manifest_json")):
            candidate = root / filename
            if candidate.is_file():
                data, warns = read_json_file(candidate)
                warnings.extend(warns)
                if isinstance(data, dict):
                    manifest = data
                    manifest_kind = kind
                    primary = filename
                    break

        front_matter: dict[str, Any] = {}
        body = ""
        canonical_layout = False
        skill_md = find_skill_md(root)
        if skill_md is not None:
            text, warns, _ = read_text_file(skill_md)
            warnings.extend(warns)
            front_matter, body, fm_warns = split_front_matter(text)
            warnings.extend(fm_warns)
            primary = primary or skill_md.name
            canonical_layout = skill_md.parent == root
        if not body and isinstance(manifest, dict) and isinstance(manifest.get("content"), str):
            body = manifest["content"]

        if root.is_dir():
            files, script_warns = collect_scripts(root)
            warnings.extend(script_warns)

        return ExtractedAsset(
            adapter=self.name,
            source_kind="directory",
            manifest=manifest,
            manifest_kind=manifest_kind,
            front_matter=front_matter,
            body=body,
            files=files,
            warnings=warnings,
            canonical_layout=canonical_layout,
            primary_file=primary,
            meta={"source_file": primary},
        )
