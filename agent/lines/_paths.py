# -*- coding: utf-8 -*-
"""**跨盘也不抛**的仓库相对路径（Windows 上 tmp 在 C:、仓库在 D: 时的唯一稳妥写法）。

【为什么单独一个叶子模块】`agent/lines` 里 4 处 + `location.py` 1 处都要做同一件事
（把文件路径折成"相对仓库根"的展示/判定字符串）。放在 callability 会被 location 反向
依赖（两者都很大），放在 utils 会跨层；这里是 `agent/lines` 内部的共用叶子，无依赖。

【踩过的事故（2026-11-15）】Windows CI 上 pytest 的临时目录在 `C:/`、仓库在 `D:/`，
`os.path.relpath` 直接抛 `ValueError: path is on mount 'C:', start on mount 'D:'`。
调用方（`load_tool_docs` 建 `_declared_in`）没有单独的容错，异常一路冒到
`agent.tools._non_callable_names()` 的 `except Exception` 里被**降级成空集**
⇒ `test_get_tool_defs_隐藏声明判否的工具` 在 windows 上断言空集，而 ubuntu 全绿。
⇒ 这里**只把"跨盘"这一种情况**降级成绝对路径（信息量不丢，只是不再是相对形式），
其余异常照旧上抛（不吞编程错误）。
"""
from __future__ import annotations

import os

#: 仓库根（本文件在 `agent/lines/` 下）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def rel_to_repo(path: str, *, root: str = "") -> str:
    """`path` 相对仓库根的路径（正斜杠）；**跨盘**时退回绝对路径，不抛

    Args:
        path: 目标路径（可为相对当前工作目录）。
        root: 参照根（默认仓库根）。
    """
    base = os.path.abspath(root or REPO_ROOT)
    target = os.path.abspath(str(path))
    try:
        rel = os.path.relpath(target, base)
    except ValueError:      # 跨盘符（Windows）：relpath 无解，退回绝对路径
        rel = target
    return rel.replace(os.sep, "/")


__all__ = ["REPO_ROOT", "rel_to_repo"]
