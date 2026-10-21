"""本机「吞噬源」探测（P1）

用途：`GET /api/ingestion/sources` 把**已配置的默认扫描位置**摊开给前端，
让用户从 claude / openclaw / trae 等真实目录里挑一个技能去预览，而不是让
用户猜路径。设计边界：

- **只读**：只列出目录与标记文件，不读正文、不执行、不写任何文件；
- **只扫配置根**：默认几个已知目录，可用 `INGEST_SCAN_ROOTS` 覆盖
  （`os.pathsep` 分隔；每项可写 `label=path`，label 用于生态归属）；
- **不做递归搜索**：只扫根的**直接子项**（技能目录），避免把整个 home 拖进来；
- **缺失根如实报**：`exists=false` + `error=not-found`，不静默当成 0 个；
- **单一候选口径**：候选必须能看到可预览标记（SKILL.md / skill.json /
  manifest.json / zip / JS 插件标记 / 文档），否则 `previewable=false`。

实测默认根（2026-10-21 本机）：

| 生态 | 路径 | 实测 |
|---|---|---|
| claude | `~/.claude/skills` | 存在，`<id>/SKILL.md`（name/description） |
| openclaw | `~/.openclaw/workspace/skills` | 存在，SKILL.md + `_meta.json` + `skill-card.md` |
| openclaw | `~/.openclaw/skills` | 存在，多为符号链接（lark-*） |
| trae | `~/.trae-cn/skills` | 存在，ClawHub 形态 |
| trae | `~/.trae/skills` | 存在（多为链接/少量） |
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .adapters.base import DOC_SUFFIXES, path_ecosystem

#: 每个根最多返回的候选数（防超大目录把响应撑爆）
MAX_CANDIDATES_PER_ROOT = 200
#: 目录扫描时跳过的名字
_SKIP_NAMES = {"node_modules", "__pycache__", ".git", ".svn", ".hg"}

#: 默认扫描根（生态, 相对 home 的路径）
_DEFAULT_SPECS: Tuple[Tuple[str, str], ...] = (
    ("claude", "~/.claude/skills"),
    ("openclaw", "~/.openclaw/workspace/skills"),
    ("openclaw", "~/.openclaw/skills"),
    ("trae", "~/.trae-cn/skills"),
    ("trae", "~/.trae/skills"),
)

#: 技能目录里出现即视为"可预览标记"的文件名
_MARKER_FILES = ("SKILL.md", "skill.md", "skill.json", "manifest.json",
                 "package.json", "_meta.json", "skill-card.md")


@dataclass(frozen=True)
class RootSpec:
    ecosystem: str
    path: str
    source: str = "env"


def _expand(path: str) -> str:
    text = os.path.expandvars(os.path.expanduser(str(path or "").strip()))
    if not text:
        return ""
    try:
        return str(Path(text).resolve(strict=False))
    except (OSError, RuntimeError):
        return text


def parse_scan_roots(raw: Optional[str] = None) -> List[RootSpec]:
    """解析 `INGEST_SCAN_ROOTS`；空值 ⇒ 默认根。

    语法：`os.pathsep` 分隔；每项 `path` 或 `label=path`（label 建议
    claude/openclaw/trae/hermes/generic）。Windows 路径的盘符冒号不会被误判，
    因为分隔符是 `;` 且 label 只按第一个 `=` 切。
    """
    text = os.environ.get("INGEST_SCAN_ROOTS", "") if raw is None else raw
    specs: List[RootSpec] = []
    if str(text or "").strip():
        for chunk in str(text).split(os.pathsep):
            item = chunk.strip()
            if not item:
                continue
            label = ""
            value = item
            if "=" in item:
                left, right = item.split("=", 1)
                left, right = left.strip(), right.strip()
                if left and right and not any(ch in left for ch in ("/", "\\", ":")):
                    label, value = left.lower(), right
            path = _expand(value)
            if not path:
                continue
            ecosystem = label or path_ecosystem(Path(path)) or "generic"
            specs.append(RootSpec(ecosystem=ecosystem, path=path, source="env"))
    else:
        for ecosystem, rel in _DEFAULT_SPECS:
            specs.append(RootSpec(ecosystem=ecosystem, path=_expand(rel), source="default"))
    # 去重（同一路径只保留第一次出现；生态以显式 label 优先）
    seen: set = set()
    out: List[RootSpec] = []
    for spec in specs:
        key = (spec.ecosystem, spec.path)
        if key in seen:
            continue
        seen.add(key)
        out.append(spec)
    return out


def _markers(directory: Path) -> Dict[str, bool]:
    found: Dict[str, bool] = {}
    try:
        for child in directory.iterdir():
            if child.is_file() and child.name in _MARKER_FILES:
                found[child.name] = True
    except OSError:
        return found
    return found


def _candidate(path: Path, root: RootSpec) -> Optional[Dict[str, Any]]:
    name = path.name
    if name.startswith(".") or name in _SKIP_NAMES:
        return None
    warnings: List[str] = []
    symlink = path.is_symlink()
    if symlink:
        warnings.append("符号链接：预览时按链接目标读取")
    formats: List[str] = []
    kind = "unknown"
    previewable = False
    if path.is_dir():
        markers = _markers(path)
        formats = [m for m in _MARKER_FILES if markers.get(m)]
        if markers.get("SKILL.md") or markers.get("skill.md"):
            kind = "skill-md"
            previewable = True
        elif markers.get("skill.json"):
            kind = "skill-json"
            previewable = True
        elif markers.get("manifest.json"):
            kind = "skill-json"
            previewable = True
        elif markers.get("package.json"):
            plugin_markers = [d for d in ("commands", "skills", "plugin.json")
                              if (path / d).exists()]
            if plugin_markers:
                kind = "js-plugin"
                previewable = True
                formats.append("js-plugin")
            else:
                kind = "package"
        if not previewable and any((path / d).is_dir() for d in ("commands", "skills")):
            kind = "js-plugin"
            previewable = True
        if not previewable:
            try:
                has_doc = any(
                    child.is_file() and child.suffix.lower() in DOC_SUFFIXES
                    for child in path.iterdir())
            except OSError:
                has_doc = False
            if has_doc:
                kind = "doc"
                previewable = True
        if not previewable and not formats:
            warnings.append("没有可识别的技能标记")
        if markers.get("_meta.json") and not (markers.get("SKILL.md") or markers.get("skill.md")):
            warnings.append("只有 _meta.json/skill-card.md，缺少 SKILL.md")
    elif path.is_file():
        suffix = path.suffix.lower()
        formats = [path.name]
        if suffix == ".zip":
            kind = "zip"
            previewable = True
        elif suffix in DOC_SUFFIXES:
            kind = "doc"
            previewable = True
        else:
            warnings.append("不支持的单文件类型")
    else:
        return None
    if kind == "unknown":
        return None
    return {
        "root": root.path,
        "ecosystem": root.ecosystem,
        "name": name,
        "path": str(path),
        "kind": kind,
        "formats": formats,
        "previewable": bool(previewable),
        "symlink": bool(symlink),
        "warnings": warnings,
    }


def _scan_root(spec: RootSpec, limit: int) -> List[Dict[str, Any]]:
    root = Path(spec.path)
    out: List[Dict[str, Any]] = []
    try:
        if root.is_file():
            candidate = _candidate(root, spec)
            return [candidate] if candidate else []
        children = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return out
    for child in children:
        if len(out) >= limit:
            break
        candidate = _candidate(child, spec)
        if candidate is not None:
            out.append(candidate)
    return out


def discover(roots: Optional[Sequence[Any]] = None,
             limit_per_root: int = MAX_CANDIDATES_PER_ROOT) -> Dict[str, Any]:
    """扫描配置根并返回 `{roots, candidates, stats}`。

    Args:
        roots: 可传 `RootSpec` / `{"ecosystem","path"}` / `(ecosystem,path)`；
               None ⇒ 读 `INGEST_SCAN_ROOTS`（空则默认根）。
        limit_per_root: 每个根最多候选数。
    """
    specs: List[RootSpec]
    if roots is None:
        specs = parse_scan_roots()
    else:
        specs = []
        for item in roots:
            if isinstance(item, RootSpec):
                specs.append(item)
            elif isinstance(item, dict):
                raw_path = str(item.get("path") or "")
                ecosystem = str(item.get("ecosystem") or path_ecosystem(Path(raw_path)) or "generic")
                specs.append(RootSpec(
                    ecosystem=ecosystem,
                    path=_expand(raw_path),
                    source=str(item.get("source") or "custom"),
                ))
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                specs.append(RootSpec(ecosystem=str(item[0]), path=_expand(str(item[1])), source="custom"))
            else:
                specs.append(RootSpec(ecosystem=path_ecosystem(Path(str(item))) or "generic",
                                      path=_expand(str(item)), source="custom"))

    root_rows: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    by_ecosystem: Dict[str, int] = {}
    for spec in specs:
        root = Path(spec.path)
        exists = root.exists()
        error = "" if exists else "not-found"
        items: List[Dict[str, Any]] = []
        if exists:
            items = _scan_root(spec, max(1, int(limit_per_root)))
        root_rows.append({
            "ecosystem": spec.ecosystem,
            "path": spec.path,
            "source": spec.source,
            "exists": bool(exists),
            "error": error,
            "count": len(items),
        })
        candidates.extend(items)
        by_ecosystem[spec.ecosystem] = by_ecosystem.get(spec.ecosystem, 0) + len(items)

    candidates.sort(key=lambda c: (c.get("ecosystem", ""), c.get("path", "")))
    missing = sum(1 for row in root_rows if not row["exists"])
    return {
        "roots": root_rows,
        "candidates": candidates,
        "stats": {
            "roots": len(root_rows),
            "missing_roots": missing,
            "candidates": len(candidates),
            "previewable": sum(1 for c in candidates if c.get("previewable")),
            "by_ecosystem": by_ecosystem,
        },
    }


def discover_sources(roots: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
    """`discover` 的语义别名（HTTP 层用这个名字）。"""
    return discover(roots=roots)


__all__ = [
    "MAX_CANDIDATES_PER_ROOT",
    "RootSpec",
    "discover",
    "discover_sources",
    "parse_scan_roots",
]
