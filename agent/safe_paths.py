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
  · 盘符只能取**系统上真实存在的**那几个（用户串里的盘符只与 OS 枚举结果比较，
    见 `_drive_root`；不存在的盘一律不可解析）；
  · Windows 上每一段同时认**大小写**与 **8.3 短名**（`RUNNER~1` 与 `runneradmin`
    是同一个目录，见 `_child`）；
  · 解析不到就返回 None，由调用方决定回 404 / 用默认目录。
"""
from __future__ import annotations

import functools
import os
import re
from pathlib import Path
from typing import Any, List, Optional, Union

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
        if _DRIVE_RE.match(text):
            # 绝对路径带盘符：根**从 OS 枚举里挑**（用户串只与根名比较）
            cur = _drive_root(text[:2])
            if cur is None:
                return None
            parts = [p for p in text[2:].split("/") if p != ""]
        else:
            current_anchor = os.path.abspath(anchor).replace("\\", "/")
            root = Path(current_anchor).anchor.replace("\\", "/")  # "C:/" 或 "/"
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


def _drive_roots() -> List[str]:
    """系统上真实存在的盘符根（`"C:/"`、`"D:/"` …）—— **来自 OS 枚举**

    【为什么不沿用「与当前盘同源」】原实现（C-6-B1）要求绝对路径与 `cwd` 同盘，
    理由是"本应用的数据都在同一个盘"。实测这条会**静默吃掉读侧数据**：
    Windows CI 上仓库在 `D:`、pytest 的临时目录在 `C:`（TEMP 默认在用户目录）
    ⇒ 事件目录被判成"不存在" ⇒ `event_files()` 返回空 ⇒ 成本护栏读不到自己刚写的
    账（`spent` 恒 0，日预算熔断永不触发，且不报错）。故改为按 OS 枚举挑根：
    用户串里的盘符**只参与比较**，真正拼进路径的是枚举出来的根。
    """
    if os.name != "nt":
        return []
    lister = getattr(os, "listdrives", None)
    roots: List[str] = []
    if lister is not None:
        try:
            for root in lister():
                text = str(root or "").replace("\\", "/")
                if _DRIVE_RE.match(text):
                    roots.append(text[:2] + "/")
        except OSError:              # 枚举失败退回逐字母探测
            roots = []
    if not roots:
        for code in range(ord("A"), ord("Z") + 1):
            probe = f"{chr(code)}:/"
            try:
                if os.path.exists(probe):
                    roots.append(probe)
            except OSError:
                continue
    return roots


def _drive_root(letter: str) -> Optional[Path]:
    """`"C:"` → 该盘的根 Path（系统上没有这个盘 ⇒ None）"""
    want = str(letter or "").strip().lower().rstrip("/")
    if len(want) < 2:
        return None
    want = want[:2]
    for root in _drive_roots():
        if root[:2].lower() == want:
            return Path(root)
    return None


#: 短名缓存上限（**模块常量**：调用参数里不写数字字面量，见口径 §4）
_SHORT_NAME_CACHE = 1024

#: `GetShortPathNameW`（Windows 专用）。首次使用时解析一次；不可用 ⇒ None。
_SHORT_PATH_FN: Any = None
_SHORT_PATH_READY = False


def _short_path_func() -> Any:
    global _SHORT_PATH_FN, _SHORT_PATH_READY
    if _SHORT_PATH_READY:
        return _SHORT_PATH_FN
    _SHORT_PATH_READY = True
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        fn = ctypes.windll.kernel32.GetShortPathNameW
        fn.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        fn.restype = wintypes.DWORD
        _SHORT_PATH_FN = fn
    except Exception:           # noqa: BLE001 受限环境没有该 API ⇒ 按"无短名"处理
        _SHORT_PATH_FN = None
    return _SHORT_PATH_FN


@functools.lru_cache(maxsize=_SHORT_NAME_CACHE)
def _short_name(path: str) -> str:
    """路径的 8.3 短名（`"C:/Users/RUNNER~1"`；取不到 ⇒ 空串）

    【为什么需要】Windows 上同一个目录有**两种合法写法**：长名 `runneradmin` 与
    短名 `RUNNER~1`。GitHub Actions 的 `TEMP` 就是短名形式（实测日志里创建的是
    `C:/Users/RUNNER~1/AppData/Local/Temp/...`）⇒ 逐段按名字相等匹配会把短名写法
    判成"不存在"，读侧看到空目录（与 `_drive_roots` 那条同一个失效模式）。
    **只用 listdir 出来的路径**去问 OS 要短名，用户输入仍只参与比较。
    """
    fn = _short_path_func()
    if fn is None:
        return ""
    try:
        import ctypes

        need = int(fn(str(path), None, 0))
        if need <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(need)
        if not int(fn(str(path), buf, need)):
            return ""
        return str(buf.value).replace("\\", "/").lower()
    except Exception:           # noqa: BLE001 取不到就算了（不影响长名解析）
        return ""


def _child(parent: Path, name: str) -> Optional[Path]:
    """parent 下名为 name 的子项（**只比较、不拼用户串**）。

    Windows 文件名有两种"同一性"要照顾：
      · **大小写不敏感**（实测：调用方给 `C:/Windows/TEMP/...` 而真实目录是 `Temp`，
        严格相等会判"不存在"）；
      · **8.3 短名**（`RUNNER~1` ↔ `runneradmin`；见 `_short_name`）。
    两者都只改"比较"，返回的仍是**磁盘上的真实名字**。
    """
    want = str(name)
    lowered = want.lower() if os.name == "nt" else want
    check_short = os.name == "nt" and "~" in lowered
    try:
        for child in parent.iterdir():
            actual = child.name.lower() if os.name == "nt" else child.name
            if actual == lowered:
                return child
            if check_short and _short_name(str(child)).rsplit("/", 1)[-1] == lowered:
                return child
    except OSError:              # 目录不可读/竞态删除
        return None
    return None


__all__ = ["enumerate_path"]
