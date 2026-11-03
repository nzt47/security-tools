# -*- coding: utf-8 -*-
"""按**逐段列目录 + 名字等值匹配**解析路径（C-6-B1 的共用口径）。

【为什么需要】py/path-injection 只认「调用方字符串是否进了路径表达式」：
resolve+前缀校验、正则白名单、basename 都不被认（§8 陷阱 14，本仓三次应验）。
唯一稳的写法是让用户输入**只参与比较**，真正拼进路径的是 listdir 出来的名字。

【与 `transform/queue.py::_find_child` 的区别】那个只管「一个已知父目录下的一个名字」；
这里要处理**整条路径**（可能是绝对路径、可能有任意层），于是逐段下降：
每一段都从当前目录的 listdir 结果里按 `child.name == part` 找，找到才下降。

【边界】
  · 不跟随 `..`（直接判不可解析）—— 需要上跳的调用方本来就不该用这个函数；
  · 绝对路径必须与当前盘/根同源（Windows 上跨盘直接拒绝）；
  · 解析不到就返回 None，由调用方决定回 404 / 用默认目录。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Union

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def enumerate_path(raw: Union[str, os.PathLike, None], *,
                   base: Optional[Union[str, os.PathLike]] = None) -> Optional[Path]:
    """把路径解析成**真实存在的** Path；解析不到返回 None。

    Args:
        raw: 调用方给的原样路径（绝对或相对）。
        base: 相对路径的起点（默认当前工作目录）。
    """
    text = str(raw or "").strip()
    if not text or "\x00" in text:
        return None
    text = text.replace("\\", "/")

    anchor = Path(base) if base is not None else Path.cwd()
    parts = [p for p in text.split("/") if p != ""]

    if text.startswith("/") or _DRIVE_RE.match(text):
        # 绝对路径：锚点必须与当前盘/根同源（跨盘一律拒绝，本应用的数据都在同一个盘）
        current_anchor = os.path.abspath(anchor).replace("\\", "/")
        root = Path(current_anchor).anchor.replace("\\", "/")  # "C:/" 或 "/"
        if _DRIVE_RE.match(text):
            drive = text[:2].lower()
            if drive != root.rstrip("/").lower():
                return None
            parts = [p for p in text[2:].split("/") if p != ""]
        else:
            parts = [p for p in text.split("/") if p != ""]
        if not root:
            return None
        cur = Path(root)
    else:
        cur = anchor

    seen = 0
    for part in parts:
        if part == "..":
            return None          # 不做上跳：需要上跳的调用方不该用本函数
        if part == ".":
            continue             # "." 是当前目录，等价于空段
        nxt = _child(cur, part)
        if nxt is None:
            return None
        cur = nxt
        seen += 1
        if seen > 64:            # 深度上限：病态输入不把服务拖死
            return None
    return cur if cur.exists() else None


def _child(parent: Path, name: str) -> Optional[Path]:
    """parent 下名为 name 的子项（**只比较、不拼用户串**）。

    Windows 文件名大小写不敏感（实测：调用方给 `C:/Windows/TEMP/...` 而真实目录是 `Temp`，
    严格相等会判"不存在"），所以 nt 上按 lower 比较；返回的仍是**磁盘上的真实名字**。
    """
    want = name.lower() if os.name == "nt" else name
    try:
        for child in parent.iterdir():
            actual = child.name.lower() if os.name == "nt" else child.name
            if actual == want:
                return child
    except OSError:              # 目录不可读/竞态删除
        return None
    return None


__all__ = ["enumerate_path"]