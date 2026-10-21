"""zip 技能包适配器（P1）

复用既有 `install_from_zip` 的 zip 结构（manifest.json + skill.md + scripts/），
但预览阶段**只读不写**：不把 zip 解压到磁盘、不执行脚本，逐条校验后再读文本。
安全边界：

- 拒绝绝对路径 / 含 `..` 的条目 / 加密条目；
- 条目数、单文件、解压总量都有上限（防 zip bomb）；
- 只读 `manifest.json`、`skill.md`、`scripts/*.py` 与少量顶层文档；
- 判定与归一化仍由 service 统一做，本适配器不猜能力。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from .base import (
    MAX_FILE_BYTES,
    MAX_SCRIPT_BYTES,
    Adapter,
    ExtractedAsset,
    split_front_matter,
)

#: 单个 zip 最多接受的条目数 / 解压总量
MAX_ZIP_ENTRIES = 500
MAX_ZIP_TOTAL_BYTES = 50 * 1024 * 1024
#: 顶层文档最多读 10 个
MAX_ZIP_DOCS = 10


def _safe_name(raw: str) -> Tuple[str, str]:
    """返回 (规范化名字, 拒绝原因)；原因非空表示不安全。"""
    name = str(raw or "").replace("\\", "/").lstrip("/")
    if not name:
        return "", "空条目名"
    parts = PurePosixPath(name).parts
    if any(part == ".." for part in parts):
        return "", "含 .. 路径"
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return "", "绝对路径"
    return name, ""


def _read_text(zf: zipfile.ZipFile, name: str, *, limit: int = MAX_FILE_BYTES,
               warnings: List[str]) -> str:
    info = zf.getinfo(name)
    if info.file_size > limit:
        warnings.append(f"zip 内文件超过 {limit} 字节，已跳过：{name}")
        return ""
    raw = zf.read(name)
    text = raw.decode("utf-8", errors="replace")
    return text


def _find_prefix(names: List[str]) -> Tuple[str, Optional[str]]:
    """找 manifest.json 所在前缀（根或单层子目录），返回 (prefix, manifest_name)。"""
    candidates = [n for n in names if n == "manifest.json" or
                  (n.count("/") == 1 and n.endswith("/manifest.json"))]
    if not candidates:
        return "", None
    candidates.sort(key=lambda n: (n.count("/"), len(n), n))
    manifest = candidates[0]
    prefix = manifest[: -len("manifest.json")] if "/" in manifest else ""
    return prefix, manifest


def _pick(names: List[str], prefix: str, filename: str) -> Optional[str]:
    for candidate in (prefix + filename,):
        for name in names:
            if name.lower() == candidate.lower():
                return name
    return None


class ZipAdapter(Adapter):
    name = "zip"
    priority = 40

    def sniff(self, source: Any) -> int:
        if getattr(source, "kind", "") != "path":
            return 0
        path = Path(source.path)
        if not path.is_file() or path.suffix.lower() != ".zip":
            return 0
        try:
            return 92 if zipfile.is_zipfile(path) else 0
        except OSError:
            return 0

    def extract(self, source: Any) -> ExtractedAsset:
        path = Path(source.path)
        warnings: List[str] = []
        files: Dict[str, str] = {}
        manifest: Optional[Dict[str, Any]] = None
        front_matter: Dict[str, Any] = {}
        body = ""
        prefix = ""
        entries = 0
        with zipfile.ZipFile(path) as zf:
            infos = [info for info in zf.infolist() if not info.is_dir()]
            entries = len(infos)
            if entries > MAX_ZIP_ENTRIES:
                warnings.append(f"zip 条目数 {entries} 超过 {MAX_ZIP_ENTRIES}，只读取前 {MAX_ZIP_ENTRIES} 条")
                infos = infos[:MAX_ZIP_ENTRIES]
            safe_names: List[str] = []
            total = 0
            for info in infos:
                if info.flag_bits & 0x1:
                    warnings.append(f"zip 条目已加密，已跳过：{info.filename}")
                    continue
                name, reason = _safe_name(info.filename)
                if reason:
                    warnings.append(f"zip 条目不安全（{reason}），已跳过：{info.filename}")
                    continue
                if info.file_size > MAX_FILE_BYTES:
                    warnings.append(f"zip 条目过大，已跳过：{name}")
                    continue
                total += int(info.file_size)
                if total > MAX_ZIP_TOTAL_BYTES:
                    warnings.append(f"zip 解压总量超过 {MAX_ZIP_TOTAL_BYTES} 字节，后续条目已跳过")
                    break
                safe_names.append(name)

            prefix, manifest_name = _find_prefix(safe_names)
            if manifest_name:
                try:
                    data = json.loads(_read_text(zf, manifest_name, warnings=warnings))
                    if isinstance(data, dict):
                        manifest = data
                    else:
                        warnings.append("manifest.json 不是对象，已忽略")
                except (ValueError, TypeError) as exc:
                    warnings.append(f"manifest.json 解析失败：{exc}")

            skill_md = None
            for filename in ("skill.md", "SKILL.md"):
                skill_md = _pick(safe_names, prefix, filename)
                if skill_md:
                    break
            if skill_md is None and prefix:
                skill_md = _pick(safe_names, "", "skill.md") or _pick(safe_names, "", "SKILL.md")
            if skill_md:
                text = _read_text(zf, skill_md, warnings=warnings)
                front_matter, body, fm_warns = split_front_matter(text)
                warnings.extend(fm_warns)

            for name in safe_names:
                if not name.startswith(prefix + "scripts/") or not name.endswith(".py"):
                    continue
                if name.count("/") != prefix.count("/") + 1:
                    continue
                if len(files) >= 50:
                    warnings.append("zip 内脚本超过 50 个，只读取前 50 个")
                    break
                try:
                    files[PurePosixPath(name).name] = _read_text(
                        zf, name, limit=MAX_SCRIPT_BYTES, warnings=warnings)
                except (OSError, KeyError) as exc:
                    warnings.append(f"zip 内脚本读取失败（{name}）：{exc}")

            if not manifest and not front_matter and not body:
                docs: List[str] = []
                for name in safe_names:
                    if len(docs) >= MAX_ZIP_DOCS:
                        break
                    if not name.lower().endswith((".md", ".markdown", ".txt", ".rst")):
                        continue
                    if prefix and not name.startswith(prefix):
                        continue
                    text = _read_text(zf, name, limit=MAX_FILE_BYTES, warnings=warnings)
                    if text.strip():
                        docs.append(f"## {PurePosixPath(name).name}\n\n{text.strip()}")
                body = "\n\n".join(docs)
                if body:
                    warnings.append("zip 内没有 manifest/SKILL.md，已按文档集合处理（需 LLM 重写）")

        return ExtractedAsset(
            adapter=self.name,
            source_kind="zip",
            manifest=manifest,
            manifest_kind="manifest_json" if manifest else "",
            front_matter=front_matter,
            body=body,
            files=files,
            warnings=warnings,
            # 只有 manifest/SKILL.md 才算"目录结构合规"；纯文档 zip 不能因此升 L1
            canonical_layout=bool(manifest_name or skill_md),
            primary_file=skill_md or (manifest_name or path.name),
            meta={
                "packaging": "zip",
                "zip_name": path.name,
                "zip_entries": entries,
                "zip_prefix": prefix,
                "manifest_name": manifest_name or "",
            },
        )
