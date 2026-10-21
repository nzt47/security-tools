"""任意文档 / 纯文本适配器（L2 原料的兜底入口）

适用：单篇 markdown/txt、整段粘贴的提示词、只有 README 的目录、只有 scripts/
没有说明的目录。它**不做任何能力推断**，只把原文与脚本收集起来；是否落成
云枢技能由 service 判定（P0 会判 L2 并拒绝提交，等 P2 的 LLM 重写层）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import (
    DOC_SUFFIXES,
    MAX_BODY_CHARS,
    Adapter,
    ExtractedAsset,
    collect_doc_files,
    collect_scripts,
    read_text_file,
)


class DocsAdapter(Adapter):
    name = "docs"
    priority = 10

    def sniff(self, source: Any) -> int:
        kind = getattr(source, "kind", "")
        if kind == "text":
            return 10
        if kind != "path":
            return 0
        root = Path(source.path)
        if root.is_file():
            return 15 if root.suffix.lower() in DOC_SUFFIXES else 0
        if not root.is_dir():
            return 0
        if collect_doc_files(root):
            return 20
        scripts_dir = root / "scripts"
        if scripts_dir.is_dir() and any(scripts_dir.glob("*.py")):
            return 18
        return 0

    def extract(self, source: Any) -> ExtractedAsset:
        warnings: list[str] = []
        if getattr(source, "kind", "") == "text":
            body = str(source.text or "")
            if len(body) > MAX_BODY_CHARS:
                warnings.append(f"原文超过 {MAX_BODY_CHARS} 字，已截断")
                body = body[:MAX_BODY_CHARS]
            return ExtractedAsset(
                adapter=self.name,
                source_kind="text",
                body=body,
                warnings=warnings,
                meta={"doc_files": [], "chars": len(body)},
            )

        root = Path(source.path)
        if root.is_file():
            text, warns, _ = read_text_file(root)
            warnings.extend(warns)
            if len(text) > MAX_BODY_CHARS:
                warnings.append(f"文件超过 {MAX_BODY_CHARS} 字，已截断")
                text = text[:MAX_BODY_CHARS]
            return ExtractedAsset(
                adapter=self.name,
                source_kind="file",
                body=text,
                warnings=warnings,
                primary_file=root.name,
                meta={"doc_files": [root.name], "chars": len(text)},
            )

        doc_files = collect_doc_files(root)
        chunks: list[str] = []
        names: list[str] = []
        budget = MAX_BODY_CHARS
        for path in doc_files:
            if budget <= 0:
                warnings.append(f"文档总长度超过 {MAX_BODY_CHARS} 字，后续文件未读")
                break
            try:
                text, warns, _ = read_text_file(path, max_bytes=max(4096, budget))
            except OSError as exc:
                warnings.append(f"文档读取失败（{path.name}）：{exc}")
                continue
            warnings.extend(warns)
            text = text[:budget]
            budget -= len(text)
            names.append(path.name)
            chunks.append(f"## {path.name}\n\n{text.strip()}")
        files, script_warns = collect_scripts(root)
        warnings.extend(script_warns)
        if not names and files:
            warnings.append("目录只有脚本、没有说明文档（将生成脚本清单骨架，不编造能力）")
        body = "\n\n".join(chunks)
        return ExtractedAsset(
            adapter=self.name,
            source_kind="directory",
            body=body,
            files=files,
            warnings=warnings,
            canonical_layout=False,
            primary_file=names[0] if names else "",
            meta={"doc_files": names, "chars": len(body)},
        )
