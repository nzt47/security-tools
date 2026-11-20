# -*- coding: utf-8 -*-
"""UI 规范守卫工具（2026-11-19 / 2026-11-20）：检查（只读）+ 受控修复（默认停用）。

- ui_guard_check：只读，返回 file:line + 修复建议；
- ui_guard_fix：按白名单补 className（Button 对齐 / 标题字号 token），默认 dry_run，
  只有 dry_run=false 才写盘；YAML enabled:false（工具页手动启用），写入走工具层审批。
"""
from __future__ import annotations

import logging

from agent import tools as _tools

logger = logging.getLogger(__name__)


def register_all(dl=None):  # noqa: ARG001 动态加载器契约
    @_tools.register(
        "ui_guard_check",
        "检查云枢前端的 UI 封装规范：原生控件、任意值字号、Button 对齐、标题字号 token、"
        "标题是否在 Tab 上面。返回 file:line、规则、修复建议与基线回归；只读，不改文件。"
        "改前端前用它自检，或评审 UI diff 时用；落盘修复走工作台「治理与运营 → 规范守卫」。",
        schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["fast", "full"],
                         "description": "fast=纯 Python（默认）；full=额外跑 npm/vitest 对拍"},
                "rule": {"type": "string",
                         "description": "只看某条规则（逗号分隔），如 button.alignment,heading.scale"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                          "description": "返回的明细条数上限，默认 40"},
            },
        },
    )
    def _ui_guard_check(**kwargs):
        from agent.ui_guard import check, history

        mode = str(kwargs.get("mode") or "fast")
        if mode not in ("fast", "full"):
            mode = "fast"
        raw = str(kwargs.get("rule") or "")
        rules = [r.strip() for r in raw.split(",") if r.strip()] or None
        try:
            limit = int(kwargs.get("limit") or 40)
        except (TypeError, ValueError):
            limit = 40
        limit = max(1, min(limit, 200))
        report = check(mode=mode, rules=rules, limit=limit)
        # 工具调用也是「显式检查」⇒ 记一条趋势（面板与对话共用一个历史）
        history.record(report, source="tool")
        data = report.to_dict(limit=limit)
        # 工具结果给模型看：不放全量 300+ 条 CSS 颜色，只给计数 + 前 N 条 + 回归
        return {
            "ok": not data["baseline"]["regressions"] and not data["errors"],
            "counts": data["counts"],
            "by_rule": data["by_rule"],
            "baseline": data["baseline"],
            "errors": data["errors"],
            "findings": data["findings"],
            "findings_total": data["findings_total"],
            "scanned_files": data["scanned_files"],
            "duration_ms": data["duration_ms"],
            "hint": "修法见 docs/ui/封装规范.md；本工具只读，落盘修复用工作台「规范守卫」面板。",
        }

    @_tools.register(
        "ui_guard_fix",
        "按 UI 封装规范修两类低风险问题：Button 补 justify-*、标题补 text-*。只改字面量 className，"
        "跳过 cn(...)/变量与 .test. 文件；默认 dry_run=true 只回 diff，dry_run=false 才写盘"
        "（工具层审批，最多 20 文件/200 处）。工具默认停用。",
        schema={
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean",
                            "description": "默认 true：只返回 before/after，不写文件；false 才落盘（需审批）"},
                "rules": {"type": "array",
                          "items": {"type": "string", "enum": ["button.alignment", "heading.scale"]},
                          "description": "只修这些规则；省略=两类都修"},
                "files": {"type": "array", "items": {"type": "string"},
                          "description": "只修这些仓库相对路径（yunshu-ui/src 下 .tsx）；省略=全部"},
                "max_files": {"type": "integer", "minimum": 1, "maximum": 50,
                              "description": "最多改多少个文件，默认 20"},
            },
        },
    )
    def _ui_guard_fix(**kwargs):
        from agent.ui_guard import fixer

        dry_run = kwargs.get("dry_run", True)
        dry_run = True if dry_run is None else bool(dry_run)
        raw_rules = kwargs.get("rules")
        rules = [str(r) for r in raw_rules] if isinstance(raw_rules, list) else None
        raw_files = kwargs.get("files")
        files = [str(f) for f in raw_files] if isinstance(raw_files, list) else None
        try:
            max_files = int(kwargs.get("max_files") or 20)
        except (TypeError, ValueError):
            max_files = 20
        max_files = max(1, min(max_files, 50))
        result = fixer.fix(dry_run=dry_run, rules=rules, files=files, max_files=max_files)
        changes = list(result.get("changes") or [])
        # 结果给模型看：最多 40 条 diff（上限 200 处，避免撑爆上下文）
        return {
            **result,
            "changes": changes[:40],
            "changes_total": len(changes),
            "hint": "先 dry_run 看 diff；确认后再 dry_run=false（会弹审批）。"
                    "面板同名功能在「治理与运营 → 规范守卫」。",
        }
