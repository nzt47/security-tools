# -*- coding: utf-8 -*-
"""进程内「当前会话」指针（叶子模块，只依赖标准库）

【为什么需要】摘要按会话分家后，**读侧**（get_context / load_summary）必须知道「现在是哪个会话」，
而 memory/ 不能反过来 import agent/session_manager（会形成 memory → 会话管理 → … 的耦合）。
本模块只存一个 sid 字符串：
  · SessionManager.set_current 发布（UI 选中会话时）；
  · memory.MemoryManager 读取（session_id=None 的缺省值）；
不缓存任何会话内容，也不做存在性校验（那是 SessionManager 的事）。

【为什么不是 contextvar】本部署的「当前会话」本来就是进程级的单选态（SessionManager._current_id），
写成 contextvar 反而会让「同一个 UI 当前会话」在不同线程里读到不同值。
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_current = ""


def set_current(session_id: str) -> None:
    """发布当前会话（空串 = 清空）。"""
    global _current
    with _lock:
        _current = str(session_id or "")


def get_current() -> str:
    """当前会话 id；没有就返回空串（调用方自行决定回退成全局）。"""
    with _lock:
        return _current


def clear() -> None:
    """清空（单测用）。"""
    set_current("")


__all__ = ["set_current", "get_current", "clear"]
