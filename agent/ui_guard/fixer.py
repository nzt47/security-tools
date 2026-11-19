# -*- coding: utf-8 -*-
"""ui_guard 的白名单修复：只做两类可逆的 className 补写。

为什么只做两类：
    - 补 justify-*（Button 满宽 / text-left）——只改 className，不改结构；
    - 补 text-*（标题字号）——只改 className，不改标签层级。
    其余（Tabs 顺序、原生控件替换、CSS 颜色）需要人判断，只出 fix_hint，不自动改。

安全阀：
    - 默认 dry_run=True（只返回 before/after）；
    - 只允许改 <repo>/yunshu-ui/src 下的 .tsx/.ts；
    - className 是表达式（cn(...) / 变量）时跳过——看不见就不猜；
    - max_files / max_edits 有上限；写盘用临时文件 + 原子替换。
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from agent.ui_guard.engine import (FIXABLE_RULES, REPO_ROOT, RULE_BUTTON,
                                   RULE_HEADING, _CLASS_ATTR, _TAG_BUTTON,
                                   _TAG_HEADING, _line_of, check)

_TITLE_TOKEN = {"1": "text-title", "2": "text-ui"}
_LITERAL_CLASS = re.compile(r"""className=(?:"([^"]*)"|'([^']*)')""")


def _literal_class(tag: str) -> Optional[str]:
    m = _LITERAL_CLASS.search(tag)
    if not m:
        return None
    return m.group(1) if m.group(1) is not None else (m.group(2) or "")


def _insert_class(tag: str, token: str) -> Optional[str]:
    """在标签的 className 里追加 token；没有 className 就新建；表达式则跳过。"""
    m = _LITERAL_CLASS.search(tag)
    if m:
        old = m.group(1) if m.group(1) is not None else (m.group(2) or "")
        if token in old.split():
            return None
        quote = '"' if m.group(1) is not None else "'"
        new = old + ("" if not old or old.endswith(" ") else " ") + token
        return tag[: m.start()] + f"className={quote}{new}{quote}" + tag[m.end():]
    if "className=" in tag:
        # className={cn(...)} / 变量：看不见就不猜（与 vitest 守卫的局限一致）
        return None
    end = tag.rfind(">")
    if end < 0:  # pragma: no cover - 正则保证有 >
        return None
    return tag[:end] + f' className="{token}"' + tag[end:]


def _edits_for_text(text: str, rel: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in _TAG_HEADING.finditer(text):
        tag = m.group(0)
        if "text-" in tag:
            continue
        token = _TITLE_TOKEN.get(tag[2], "text-body")
        new_tag = _insert_class(tag, token)
        if new_tag and new_tag != tag:
            out.append({"rule": RULE_HEADING, "file": rel, "line": _line_of(text, m.start()),
                        "start": m.start(), "before": tag, "after": new_tag, "token": token})
    for m in _TAG_BUTTON.finditer(text):
        tag = m.group(0)
        cls = _literal_class(tag)
        if cls is None:
            continue
        wide = ("w-full" in cls) or ("flex-1" in cls)
        needs = (wide and "justify-" not in cls) or ("text-left" in cls and "justify-start" not in cls)
        if not needs:
            continue
        token = "justify-center" if "text-center" in cls else "justify-start"
        new_tag = _insert_class(tag, token)
        if new_tag and new_tag != tag:
            out.append({"rule": RULE_BUTTON, "file": rel, "line": _line_of(text, m.start()),
                        "start": m.start(), "before": tag, "after": new_tag, "token": token})
    return out


def _apply_to_text(text: str, edits: List[Dict[str, Any]]) -> str:
    """按 start 倒序替换（同一标签只改一次；避免相同标签被替换到别处）。"""
    for e in sorted(edits, key=lambda x: x["start"], reverse=True):
        idx = e["start"]
        if text[idx: idx + len(e["before"])] != e["before"]:  # pragma: no cover - 防御
            continue
        text = text[:idx] + e["after"] + text[idx + len(e["before"]):]
    return text


def fix(*, dry_run: bool = True, rules: Optional[Sequence[str]] = None,
        files: Optional[Sequence[str]] = None, max_files: int = 20,
        max_edits: int = 200, root: Optional[Path] = None) -> Dict[str, Any]:
    """执行白名单修复；dry_run=True 只返回 before/after。"""
    root = Path(root) if root else REPO_ROOT
    ui = root / "yunshu-ui" / "src"
    wanted = set(rules) if rules else set(FIXABLE_RULES)
    wanted &= set(FIXABLE_RULES)
    changes: List[Dict[str, Any]] = []
    errors: List[str] = []
    if not wanted:
        return {"ok": True, "applied": False, "changes": [], "errors": ["没有可自动修复的规则"]}

    touched_files: Dict[str, List[Dict[str, Any]]] = {}
    for p in sorted(ui.rglob("*")):
        if p.suffix not in (".tsx", ".ts") or not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if files and rel not in files:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"读失败 {rel}: {exc}")
            continue
        edits = [e for e in _edits_for_text(text, rel) if e["rule"] in wanted]
        if not edits:
            continue
        if len(touched_files) >= max_files and rel not in touched_files:
            errors.append(f"达到 max_files={max_files}，剩余文件未处理")
            break
        if len(changes) + len(edits) > max_edits:
            edits = edits[: max(0, max_edits - len(changes))]
            errors.append(f"达到 max_edits={max_edits}，部分修改未处理")
        new_text = _apply_to_text(text, edits)
        touched_files[rel] = edits
        for e in edits:
            changes.append({k: v for k, v in e.items() if k != "start"})

        if not dry_run:
            try:
                fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".ui_guard.tmp")
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                    fh.write(new_text)
                os.replace(tmp, p)
            except OSError as exc:
                errors.append(f"写失败 {rel}: {exc}")

    result: Dict[str, Any] = {
        "ok": not errors,
        "applied": (not dry_run) and bool(changes),
        "dry_run": bool(dry_run),
        "rules": sorted(wanted),
        "changes": changes,
        "change_count": len(changes),
        "files": sorted(touched_files),
        "errors": errors,
    }
    if not dry_run and changes:
        report = check(root=root, limit=0)
        result["report_after"] = {
            "counts": report.counts, "baseline": report.baseline,
            "duration_ms": report.duration_ms}
    return result
