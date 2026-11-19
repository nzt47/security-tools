# -*- coding: utf-8 -*-
"""UI 规范守卫工具（2026-11-19）：把 ui_guard 检查器接进模型可见集。

只读、低风险：返回 规范违规的 file:line + 修复建议，不改任何文件；
落盘修复走面板（/api/ui-guard/fix，需 confirm）或 apply_patch。
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
        from agent.ui_guard import check

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
