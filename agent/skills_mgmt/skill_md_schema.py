# -*- coding: utf-8 -*-
"""skill.md 形状门禁 —— 把「文件轨该长什么样」变成可执行判定（2026-10-29）。

【为什么需要】§11.15 调查结论：文件轨 36/36 有 skill.md，但格式完全由**导入通道**决定 ——
解析器对坏 front matter 是宽容的（解析失败就把整篇当正文），分离符粘连（`---# 标题`）
会让元数据索引整条静默跳过（陷阱 17），而 CI 里没有任何一处会因此变红。
结果是「看上去 36 条都在」与「实际能被索引/检索的是哪几条」不是同一件事。

【口径】
- **error（必须为 0，否则门禁红）**：front matter 可解析；9 个必填键非空
  （id/name/description/author/category/source/tags/content_type/enabled）；
  id 合法且与目录名一致（目录名就是主轨 key，不一致会让双轨对账对不上）。
- **warning（只提示，不阻断）**：建议键（status/description_zh/version）、
  正文结构小节（何时使用 / 示例）、YAML 层面的告警。
  存量 36 份里只有少数有「示例」，一刀切会逼着补文档，所以只作提示。

判定与脚本共用同一实现：`scripts/validate_skill_md.py`（本机/CI 都可跑），
`tests/unit/test_skill_md_schema.py` 里那条"真实仓库 0 error"就是 CI 门禁。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .claude_import import _split_front_matter

#: 必填键（2026-10-29 实测：存量 36 份 100% 都有这 9 个，所以门禁不会制造历史债）。
REQUIRED_KEYS = (
    "id", "name", "description", "author", "category",
    "source", "tags", "content_type", "enabled",
)

#: 建议键：缺了不影响索引，但会影响列表展示/版本核对。
RECOMMENDED_KEYS = ("status", "description_zh", "version")

#: 结构小节同义词（只匹配标题行；大小写不敏感）。
#: 「适用场景 / 适用条件 / 触发条件」也算 —— pd-* 技能用这几个标题写触发面，
#: 早先只认「何时使用/使用场景」会把它们误判成缺失（2026-10-29 实测 29 条里多份有「触发条件」）。
USAGE_SECTIONS = ("何时使用", "使用场景", "使用时机", "适用场景", "适用条件", "触发条件",
                  "使用说明", "when to use", "usage")
EXAMPLE_SECTIONS = ("示例", "例子", "范例", "example", "examples")

#: 参考资料型技能的 content_type（2026-11-02）：这类是**手册/参考**，不是"可被模型执行的步骤"，
#: 强求「何时使用 / 示例」等于把手册伪装成技能（gh-cli 实测五面全空）。
#: 对它们只放宽这两条 warning，必填键与 id 契约照旧。
REFERENCE_CONTENT_TYPES = ("reference", "manual", "handbook")

#: 技能 ID 契约（与 file_store._validate_skill_id / claude_import._ID_RE 同口径）。
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")

#: skill.md 的入口文件名（官方 SKILL.md，兼容小写）。
_SKILL_FILENAMES = ("SKILL.md", "skill.md")


def _headings(body: str) -> List[str]:
    """正文里所有标题行的文本（去 `#`、去首尾空白、小写）。"""
    out: List[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            out.append(stripped.lstrip("#").strip().lower())
    return out


def _has_section(headings: List[str], keywords: tuple) -> bool:
    return any(any(k in h for k in keywords) for h in headings)


def validate_text(text: str, *, skill_id: str = "", dir_name: str = "") -> Dict[str, Any]:
    """校验一份 skill.md 文本，返回 {skill_id, errors, warnings, ok}。"""
    meta, body, split_warnings = _split_front_matter(text)
    errors: List[str] = []
    warnings: List[str] = list(split_warnings)

    if not meta:
        errors.append("缺少可解析的 front matter（首行必须是独占一行的 `---`，"
                      "且与结束行成对；`---# 标题` 这种粘连会让整段元数据被静默跳过）")
    for key in REQUIRED_KEYS:
        if not isinstance(meta, dict) or key not in meta:
            errors.append("缺少必填键 %s" % key)
            continue
        value = meta.get(key)
        if value is None or (isinstance(value, str) and not value.strip()) or \
                (isinstance(value, (list, dict)) and not value):
            # 键在、值为空：存量 12 份这样的（author: '' / tags: 空序列）。
            # 门禁只拦「键缺失」，避免为了让门禁变绿去编造作者/标签。
            warnings.append("必填键 %s 的值为空" % key)

    declared_id = str((meta or {}).get("id") or "").strip()
    if declared_id:
        if not _ID_RE.match(declared_id):
            errors.append("id 不合法：%r（应匹配 ^[a-z0-9][a-z0-9_-]*$）" % declared_id)
        if dir_name and declared_id != dir_name:
            errors.append("id 与目录名不一致：%r != %r（目录名就是主轨 key，"
                          "不一致会让双轨对账/补登记对不上）" % (declared_id, dir_name))

    for key in RECOMMENDED_KEYS:
        value = (meta or {}).get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            warnings.append("建议补 %s（列表展示/版本核对会用到）" % key)

    # 参考资料型（content_type: reference/manual/handbook）不按"技能"要求这两节
    # —— 它们是手册，硬补只会催生编造（见 REFERENCE_CONTENT_TYPES 的注释）。
    ctype = str((meta or {}).get("content_type") or "").strip().lower()
    if ctype not in REFERENCE_CONTENT_TYPES:
        headings = _headings(body)
        if not _has_section(headings, USAGE_SECTIONS):
            warnings.append("正文缺少「何时使用 / 使用场景」小节")
        if not _has_section(headings, EXAMPLE_SECTIONS):
            warnings.append("正文缺少「示例」小节")

    sid = skill_id or declared_id or dir_name
    return {"skill_id": sid, "errors": errors, "warnings": warnings, "ok": not errors}


def validate_file(path: Path, *, dir_name: Optional[str] = None) -> Dict[str, Any]:
    """校验单个 skill.md；读不到文件按 error 返回（不抛异常）。"""
    p = Path(path)
    name = dir_name if dir_name is not None else p.parent.name
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"skill_id": name, "path": str(p),
                "errors": ["读取失败: %s" % type(exc).__name__],
                "warnings": [], "ok": False}
    result = validate_text(text, dir_name=name)
    result["path"] = str(p)
    return result


def scan_root(base: Path) -> Dict[str, Any]:
    """扫描文件轨根目录：每个 <id>/skill.md 一条结果（跳过 .index 等点目录）。"""
    root = Path(base)
    items: List[Dict[str, Any]] = []
    if not root.is_dir():
        return {"root": str(root), "checked": 0, "ok_count": 0, "error_count": 0,
                "warning_count": 0, "items": [], "missing_root": True}
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        md = next((child / n for n in _SKILL_FILENAMES if (child / n).is_file()), None)
        if md is None:
            continue
        items.append(validate_file(md, dir_name=child.name))
    return {
        "root": str(root),
        "checked": len(items),
        "ok_count": sum(1 for i in items if i["ok"]),
        "error_count": sum(1 for i in items if not i["ok"]),
        "warning_count": sum(1 for i in items if i["warnings"]),
        "items": items,
    }
