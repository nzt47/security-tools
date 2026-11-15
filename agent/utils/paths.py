# -*- coding: utf-8 -*-
"""**跨盘也能用**的仓库相对路径（Windows 上 tmp 在 C:、仓库在 D: 时的唯一稳妥写法）。

【为什么单独一个叶子模块】多处（`agent/lines` 的 5 处、`scripts/freeze_eval_anchor.py`）
都要把文件路径折成"相对仓库根"的展示/判定字符串；直接 `os.path.relpath` 在 Windows 上
跨盘会抛 `ValueError: path is on mount 'C:', start on mount 'D:'`。

【踩过的事故（2026-11-15 windows CI）】
  · `load_tool_docs` 建 `_declared_in` 时抛 ⇒ 被 `agent.tools._non_callable_names()` 的
    `except Exception` 静默降级成空集 ⇒ `test_get_tool_defs_隐藏声明判否的工具` 断言 `[] == ['manual_b']`；
  · `freeze_eval_anchor.collect_layer_hashes` 直接抛 ⇒ `test_collect_layer_hashes_reports_missing_layer` 红。
两处都是「pytest 的 tmp 在 C:、仓库在 D:」。

【口径】只把"跨盘"这一种情况降级成绝对路径（信息量不丢，只是不再是相对形式）；
其余异常照旧上抛（不吞编程错误）。
"""
from __future__ import annotations

import os

#: 仓库根（本文件在 `agent/utils/` 下）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def rel_to_repo(path: str, *, root: str = "") -> str:
    """`path` 相对仓库根的路径（正斜杠）；**跨盘**时退回绝对路径，不抛

    Args:
        path: 目标路径（可为相对当前工作目录）。
        root: 参照根（默认仓库根）。
    """
    base = root or REPO_ROOT
    base = base if os.path.isabs(base) else os.path.join(REPO_ROOT, base)
    target = os.path.abspath(str(path))
    base = os.path.abspath(base)
    try:
        rel = os.path.relpath(target, base)
    except ValueError:      # 跨盘符（Windows）：relpath 无解，退回绝对路径
        rel = target
    return rel.replace(os.sep, "/")


__all__ = ["REPO_ROOT", "rel_to_repo"]
