"""Claude 技能包导入 —— ~/.claude/skills → 云枢技能仓库

【为什么需要这一层转换】
技能中心已经能导入 .zip（POST /api/skills-mgmt/upload → `install_from_zip`），
但 `install_from_zip` 要求包内有 **manifest.json**（agent/skills_mgmt/skill_manager.py:186-230）；
Claude 技能包只有 `SKILL.md`，也没有 manifest —— 直接把目录丢进去必失败。
所以这里只做"格式转换 + 落库"：读 SKILL.md（front matter 取 name/description，
正文作为说明层），在临时目录拼出 `manifest.json` + `skill.md`，再复用**同一个**
`install_from_zip`，不另开一条安装链路。

【与已删除的 extension type `claude_skill` 的关系】
旧实现（agent/extensions/skills_installer.py）只是把包拷进 `~/.claude/skills/`
再在扩展存储里登记一条记录，云枢自身**从不读**那个目录 —— 装了等于没装。
本模块把"导入"改成**复制语义**：读源目录 → 生成云枢技能资产（skill.md）→ 落
`data/skills_repo/`，从此由技能中心/SkillRegistry 统一管理。

【边界（如实标注）】
· 只读 `<base>/<skill-dir>/SKILL.md`，对源目录**只读不写**（不删、不改名）；
· HTTP 路由**不接受**目录参数（固定 ~/.claude/skills）；`base_dir` 仅供测试/内部调用，
  否则等于开一个「读本机任意路径」的入口（CodeQL py/path-injection 实测拦过）；
· 只带 `scripts/*.py`（与 install_from_zip 的收集口径一致），其余文件不搬；
· 逐条返回 ok/error：单个技能失败不影响其它技能。
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

#: 默认扫描目录（Claude Code 的技能目录）
DEFAULT_CLAUDE_SKILLS_DIR = Path.home() / ".claude" / "skills"

#: 技能入口文件名（官方是 SKILL.md；兼容小写写法）
_SKILL_FILENAMES = ("SKILL.md", "skill.md")

#: 云枢技能 ID 契约：^[a-z0-9][a-z0-9_-]*$（见 file_store._validate_skill_id）
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")
_INVALID_ID_CHARS = re.compile(r"[^a-z0-9]+")

#: 导入后写入 manifest 的来源标记（category=claude 会命中外来技能安全预检）
IMPORT_SOURCE = "claude-skills"
IMPORT_CATEGORY = "claude"


def _slug(raw: str) -> str:
    """目录名 → 合法技能 ID（小写 + 非法字符折成 '-'，去掉首尾 '-'）"""
    slug = _INVALID_ID_CHARS.sub("-", (raw or "").strip().lower()).strip("-")
    if not slug:
        return ""
    if not slug[0].isalnum():  # 理论上 strip('-') 已保证，留作兜底
        slug = "s" + slug
    return slug


def _split_front_matter(text: str) -> Tuple[Dict[str, Any], str, List[str]]:
    """拆分 SKILL.md → (front matter dict, body, warnings)

    容忍坏 front matter：解析失败时把整篇当正文（warnings 里留证），
    不让一个格式怪异的技能包阻断整批导入。
    """
    warnings: List[str] = []
    if not text.startswith("---"):
        return {}, text, warnings

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, warnings
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        warnings.append("front matter 未闭合，已按纯正文处理")
        return {}, text, warnings

    block = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1:]).strip()
    try:
        import yaml  # 延迟导入：缺 PyYAML 时仍可导入无 front matter 的技能
        meta = yaml.safe_load(block) or {}
        if not isinstance(meta, dict):
            warnings.append("front matter 不是对象，已忽略")
            return {}, body, warnings
        return meta, body, warnings
    except Exception as e:  # noqa: BLE001 坏 YAML ⇒ 不阻断，正文照收
        warnings.append(f"front matter 解析失败（{type(e).__name__}），已忽略")
        return {}, body, warnings


def _first_heading(body: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _first_paragraph(body: str) -> str:
    """取第一段非标题正文，截 200 字（只用于缺 description 时的兜底）。"""
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if para and not para.startswith("#"):
            return re.sub(r"\s+", " ", para)[:200]
    return ""


def scan_claude_skills(base_dir: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    """扫描 ~/.claude/skills/*/SKILL.md，返回可导入候选（不落库）

    每项：{id, dir_name, name, description, path, source_dir, ready, warnings, error?}
    """
    root = Path(base_dir) if base_dir else DEFAULT_CLAUDE_SKILLS_DIR
    items: List[Dict[str, Any]] = []
    if not root.is_dir():
        return items

    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.is_symlink():
            continue
        skill_file = next((child / n for n in _SKILL_FILENAMES if (child / n).is_file()), None)
        if skill_file is None:
            continue

        item: Dict[str, Any] = {
            "dir_name": child.name,
            "path": str(skill_file),
            "source_dir": str(child),
        }
        try:
            text = skill_file.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            item.update({"id": _slug(child.name) or child.name, "ready": False,
                         "error": f"读取失败: {e}"})
            items.append(item)
            continue

        meta, body, warnings = _split_front_matter(text)
        skill_id = _slug(child.name)
        if not skill_id:
            item.update({"id": child.name, "ready": False,
                         "error": "目录名无法转换为合法技能 ID（kebab_case）"})
            items.append(item)
            continue

        name = str(meta.get("name") or _first_heading(body) or child.name).strip()
        description = str(meta.get("description") or _first_paragraph(body) or "").strip()
        item.update({
            "id": skill_id,
            "name": name,
            "description": description,
            "version": str(meta.get("version") or "1.0.0"),
            "author": str(meta.get("author") or ""),
            "body_chars": len(body),
            "warnings": warnings,
            "ready": True,
        })
        if not _ID_RE.match(skill_id):  # 兜底，理论上 _slug 已保证
            item.update({"ready": False, "error": f"技能 ID 不合法: {skill_id}"})
        items.append(item)

    return items


def _build_zip(item: Dict[str, Any], out_path: Path, body: str,
               scripts: Dict[str, str]) -> None:
    """把一条候选技能打成 install_from_zip 认识的结构"""
    manifest = {
        "id": item["id"],
        "name": item["name"],
        "description": item["description"],
        "version": item.get("version") or "1.0.0",
        "author": item.get("author") or "",
        "category": IMPORT_CATEGORY,
        "source": IMPORT_SOURCE,
        "tags": ["claude-skills"],
        "content_type": "markdown",
        # 不写 enabled：与既有 .zip 导入保持一致（缺省=启用）。启停交给技能中心。
    }
    # skill.md 用与 file_store 同一套序列化，避免手拼 YAML 出界
    from .file_store import SkillMDParser
    meta = {
        "id": item["id"],
        "name": item["name"],
        "description": item["description"],
        "version": manifest["version"],
        "author": manifest["author"],
        "category": IMPORT_CATEGORY,
        "source": IMPORT_SOURCE,
        "tags": ["claude-skills"],
        "content_type": "markdown",
    }
    skill_md = SkillMDParser.serialize(meta, body or item["description"])

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr("skill.md", skill_md)
        for fname, content in scripts.items():
            zf.writestr("scripts/" + fname, content)


def _collect_scripts(source_dir: Path) -> Dict[str, str]:
    """只收 scripts/*.py（与 install_from_zip 的收集口径一致）"""
    out: Dict[str, str] = {}
    scripts_dir = source_dir / "scripts"
    if not scripts_dir.is_dir():
        return out
    for py in sorted(scripts_dir.glob("*.py")):
        if py.is_file() and py.stat().st_size <= 512 * 1024:
            try:
                out[py.name] = py.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return out


def import_claude_skills(svc: Any, ids: Optional[List[str]] = None,
                         base_dir: Optional[Path | str] = None) -> Dict[str, Any]:
    """把候选技能转成 zip 并走 install_from_zip 落库

    Args:
        svc: SkillsMgmtService（需有 install_from_zip）
        ids: 只导入这些技能 ID（None = 全部 ready 的候选）
        base_dir: 覆盖默认扫描目录（测试用）

    Returns:
        {ok, base_dir, total, imported: [...], failed: [...]}
    """
    candidates = scan_claude_skills(base_dir)
    wanted = set(ids or [])
    if wanted:
        candidates = [c for c in candidates if c["id"] in wanted]

    imported: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    for item in candidates:
        if not item.get("ready"):
            failed.append({"id": item["id"], "error": item.get("error") or "不可导入"})
            continue
        try:
            text = Path(item["path"]).read_text(encoding="utf-8", errors="replace")
            _, body, _warnings = _split_front_matter(text)
            scripts = _collect_scripts(Path(item["source_dir"]))
            with tempfile.TemporaryDirectory(prefix="claude_skill_import_") as tmp:
                zip_path = Path(tmp) / f"{item['id']}.zip"
                _build_zip(item, zip_path, body, scripts)
                result = svc.install_from_zip(str(zip_path))
            imported.append({
                "id": result.get("skill_id", item["id"]),
                "name": result.get("name", item["name"]),
                "scripts_count": result.get("scripts_count", 0),
                "source_dir": item["source_dir"],
            })
            logger.info(log_dict({'module_name': 'claude_import',
                                  'action': 'import.ok',
                                  'skill_id': result.get("skill_id", item["id"]),
                                  'msg': f"[Claude 导入] 已导入 {item['id']}"}))
        except Exception as e:  # noqa: BLE001 单条失败不阻断整批
            failed.append({"id": item["id"], "error": f"{type(e).__name__}: {e}"})
            logger.warning(log_dict({'module_name': 'claude_import',
                                     'action': 'import.failed',
                                     'skill_id': item["id"],
                                     'msg': f"[Claude 导入] {item['id']} 失败: {e}"}))

    return {
        "ok": not failed,
        "base_dir": str(Path(base_dir) if base_dir else DEFAULT_CLAUDE_SKILLS_DIR),
        "total": len(candidates),
        "imported": imported,
        "failed": failed,
    }
