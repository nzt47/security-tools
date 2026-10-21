"""适配器基类与只读取证工具（P0）

适配器的**唯一职责**：把一种外来源读成 `ExtractedAsset`，绝不落库、绝不执行
来源里的脚本。判定（L0/L1/L2）与归一化在 `agent.ingestion.service` 里统一做，
避免每个生态适配器各写一套"算不算符合"的口径。

【为什么 sniff 与 extract 分开】registry 要按内容特征给所有适配器打分后选最高
分者（而不是按生态名硬编码）；sniff 必须便宜（只读少量标记文件），extract 才
做完整读取。单个适配器 sniff 抛错只记 0 分，不阻断其它适配器。
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: 单文件最多读 2 MiB（超过则截断并告警；不把大文件整个拉进内存）
MAX_FILE_BYTES = 2 * 1024 * 1024
#: 单个脚本最多 512 KiB（与 install_from_zip 的收集口径一致）
MAX_SCRIPT_BYTES = 512 * 1024
#: 目录适配器最多收集的脚本数 / 文档数
MAX_SCRIPTS = 50
MAX_DOC_FILES = 20
#: 单次抽取的正文上限（防止把整本手册塞进计划）
MAX_BODY_CHARS = 200_000

#: 云枢技能 ID 契约：^[a-z0-9][a-z0-9_-]*$
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")
_ID_BAD = re.compile(r"[^a-z0-9]+")

#: 目录扫描时跳过的名字（不跟随 symlink；这些目录只会在无意义地拖慢/放大）
_SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".svn", ".hg", "dist", "build"}
#: 文档后缀（"任意 markdown/文档"的最低识别面）
DOC_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".text"}


def slugify(raw: str) -> str:
    """目录名/标题 → 合法云枢技能 ID；不可转换时返回空串。"""
    slug = _ID_BAD.sub("-", str(raw or "").strip().lower()).strip("-")
    if not slug:
        return ""
    if not slug[0].isalnum():
        slug = "s" + slug
    return slug


def is_valid_skill_id(raw: str) -> bool:
    return bool(_ID_RE.match(str(raw or "")))


def read_text_file(path: Path, *, max_bytes: int = MAX_FILE_BYTES) -> Tuple[str, List[str], bool]:
    """只读文本文件 → (文本, 告警, 是否截断)。

    - 先读 `max_bytes + 1` 字节，避免 `read_text()` 把超大文件整个吃进内存；
    - 解码失败不抛错（errors="replace"），并在告警里留证 —— 一个坏字节不该让整次吞噬失败。
    """
    warnings: List[str] = []
    try:
        with Path(path).open("rb") as fh:
            raw = fh.read(max_bytes + 1)
    except OSError as exc:
        raise OSError(f"读取失败: {path}: {exc}") from exc
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
        warnings.append(f"文件超过 {max_bytes} 字节，已截断：{path}")
    text = raw.decode("utf-8", errors="replace")
    if text and "\ufffd" in text:
        warnings.append(f"文件含非 UTF-8 字节，已按替换字符解码：{path}")
    return text, warnings, truncated


def read_json_file(path: Path) -> Tuple[Optional[Any], List[str]]:
    """读 JSON；坏 JSON 返回 (None, 告警) —— 由适配器决定降级还是拒绝。"""
    text, warnings, _ = read_text_file(path)
    try:
        return json.loads(text), warnings
    except (ValueError, TypeError) as exc:
        warnings.append(f"JSON 解析失败（{path.name}）：{exc}")
        return None, warnings


def _tolerant_front_matter(text: str) -> Tuple[Dict[str, Any], str, List[str]]:
    """坏 YAML 的按行容错解析（只取顶层 `key: value`，不猜嵌套结构）。

    实测（2026-10-21）：trae/openclaw 的 `code-refactor` 技能 description 里
    含未加引号的 `Chinese triggers: 重构、...` ⇒ PyYAML ScannerError，整段
    front matter 被丢弃。按行恢复至少能保住 name/description/license 等标量，
    比"整段忽略"更接近原文；嵌套 metadata 等复杂结构仍交给 YAML 解析器。
    """
    lines = str(text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, str(text or ""), []
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, str(text or ""), []
    meta: Dict[str, Any] = {}
    for line in lines[1:end_idx]:
        match = re.match(r"^([A-Za-z0-9_][A-Za-z0-9_\-.]*)\s*:\s*(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if not value:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        meta[key] = value
    body = "\n".join(lines[end_idx + 1:]).strip()
    return meta, body, []


def split_front_matter(text: str) -> Tuple[Dict[str, Any], str, List[str]]:
    """拆 SKILL.md 的 YAML front matter。

    【复用不重写】直接调用 `agent.skills_mgmt.claude_import._split_front_matter`：
    它已处理"未闭合/不是对象/坏 YAML"三种坏输入，且是本仓 claude 导入的既有口径。
    这里在**函数体内**导入，保持 `agent.ingestion` 的叶子包属性（导入期零重依赖）。
    """
    if not str(text or "").startswith("---"):
        return {}, str(text or ""), []
    try:
        from agent.skills_mgmt.claude_import import _split_front_matter
    except Exception as exc:  # noqa: BLE001 解析器不可用时如实降级，不阻断
        return {}, str(text or ""), [f"front matter 解析器不可用（{type(exc).__name__}），按纯正文处理"]
    meta, body, warnings = _split_front_matter(str(text))
    meta = dict(meta or {})
    warnings = list(warnings or [])
    if not meta and any("解析失败" in warning for warning in warnings):
        tolerant, tolerant_body, _ = _tolerant_front_matter(str(text))
        if tolerant:
            meta = tolerant
            body = tolerant_body
            warnings.append(f"front matter YAML 解析失败，已按行容错恢复 {len(tolerant)} 个字段")
    return meta, body, warnings


def first_heading(body: str) -> str:
    for line in str(body or "").splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def first_paragraph(body: str, limit: int = 200) -> str:
    for para in re.split(r"\n\s*\n", str(body or "")):
        para = para.strip()
        if para and not para.startswith("#"):
            return re.sub(r"\s+", " ", para)[:limit]
    return ""


def path_ecosystem(path: Path) -> str:
    """从路径推断生态（仅作**默认扫描位置**的提示，不替代内容特征）。

    实测（2026-10-21）：
      - `~/.claude/skills/<id>/SKILL.md`：front matter 只有 name/description；
      - `~/.openclaw/workspace/skills/<id>/SKILL.md`：可能带 `metadata.openclaw`、
        `homepage`、`license`，并带 `_meta.json` / `skill-card.md`；
      - `~/.trae-cn/skills/<id>/SKILL.md`：同样带 `_meta.json` / `skill-card.md`
        （ClawHub 形态），无独立 front matter 键；
      - `~/Clawdbot/moltbot/skills/<id>`：openclaw 系。
    """
    parts = [part.lower() for part in Path(path).parts]
    if ".claude" in parts:
        return "claude"
    if ".openclaw" in parts or "moltbot" in parts:
        return "openclaw"
    if ".trae" in parts or ".trae-cn" in parts or ".trae-aicc" in parts:
        return "trae"
    if ".hermes" in parts:
        return "hermes"
    return ""


def source_ecosystem(source: Any, path: Path) -> str:
    """生态提示：显式 `source.ecosystem` 优先，其次路径推断。"""
    hint = str(getattr(source, "ecosystem", "") or "").strip().lower()
    return hint or path_ecosystem(path)


def find_skill_md(root: Path) -> Optional[Path]:
    """目录根下的技能入口文件（skill.md 优先，其次 SKILL.md）。"""
    root = Path(root)
    for name in ("skill.md", "SKILL.md"):
        candidate = root / name
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    # 大小写变体（Linux 上才可能不同；Windows 上前面已命中）
    try:
        for child in root.iterdir():
            if child.is_file() and child.name.lower() == "skill.md" and not child.is_symlink():
                return child
    except OSError:
        return None
    return None


def collect_scripts(root: Path) -> Tuple[Dict[str, str], List[str]]:
    """收集 `scripts/*.py`（只读；与 install_from_zip 的收集口径一致）。"""
    scripts: Dict[str, str] = {}
    warnings: List[str] = []
    scripts_dir = Path(root) / "scripts"
    if not scripts_dir.is_dir():
        return scripts, warnings
    try:
        files = sorted(scripts_dir.glob("*.py"))
    except OSError as exc:
        return scripts, [f"脚本目录读取失败: {exc}"]
    for path in files[:MAX_SCRIPTS]:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            if path.stat().st_size > MAX_SCRIPT_BYTES:
                warnings.append(f"脚本超过 {MAX_SCRIPT_BYTES} 字节，已跳过：{path.name}")
                continue
            text, warns, _ = read_text_file(path, max_bytes=MAX_SCRIPT_BYTES)
            warnings.extend(warns)
            scripts[path.name] = text
        except OSError as exc:
            warnings.append(f"脚本读取失败（{path.name}）：{exc}")
    if len(files) > MAX_SCRIPTS:
        warnings.append(f"脚本数超过 {MAX_SCRIPTS}，只收集前 {MAX_SCRIPTS} 个")
    return scripts, warnings


def collect_doc_files(root: Path) -> List[Path]:
    """目录根下的文档文件（不递归；只收一层，避免误把整棵仓库当技能）。"""
    out: List[Path] = []
    try:
        children = sorted(Path(root).iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return out
    for path in children:
        if path.name in _SKIP_DIRS or path.name.startswith("."):
            continue
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in DOC_SUFFIXES:
            out.append(path)
        if len(out) >= MAX_DOC_FILES:
            break
    return out


@dataclass
class ExtractedAsset:
    """适配器抽取结果（**纯数据**；不含判定结论）。"""

    adapter: str
    source_kind: str  # "directory" | "file" | "text"
    manifest: Optional[Dict[str, Any]] = None
    manifest_kind: str = ""  # "skill_json" | "manifest_json" | ""
    front_matter: Dict[str, Any] = field(default_factory=dict)
    body: str = ""
    files: Dict[str, str] = field(default_factory=dict)  # 相对路径 -> 文本（只含 scripts/*.py）
    warnings: List[str] = field(default_factory=list)
    canonical_layout: bool = False
    primary_file: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_manifest(self) -> bool:
        return isinstance(self.manifest, dict) and bool(self.manifest)

    @property
    def has_front_matter(self) -> bool:
        return isinstance(self.front_matter, dict) and bool(self.front_matter)

    @property
    def has_scripts(self) -> bool:
        return bool(self.files)

    @property
    def body_chars(self) -> int:
        return len(str(self.body or ""))


class Adapter(ABC):
    """内容特征适配器。"""

    #: 稳定名字（写入计划，供前端/审计识别）
    name: str = ""
    #: 同分时的次序（大者优先）；默认 0，生态专用适配器可调高
    priority: int = 0

    @abstractmethod
    def sniff(self, source: Any) -> int:
        """返回 0-100 的匹配分；0 = 不识别。不得落库、不得执行来源内容。"""

    @abstractmethod
    def extract(self, source: Any) -> ExtractedAsset:
        """读取来源并返回抽取结果；读取失败可抛 OSError（由 service 统一转错误）。"""
