# -*- coding: utf-8 -*-
"""成本/预算护栏「读不到钱」时的**自诊断**文本（只在断言失败时拼进消息）。

【为什么需要】2026-11-15 的 windows CI 上红过 7 条（`test_s5_03_cost_brake::
TestDailyBreaker` ×5 + `test_judge_llm_runtime::TestBudgetGuardSeesItsOwnSpend` ×2），
断言只说"护栏看不见刚花的钱（spent=0.0）"，**没说是写歪了还是读歪了** ——
一条红要再猜一轮。本模块把这些事实一次拼进断言消息：

  · 请求的事件目录 + `enumerate_path()` 的解析结果（None = 路径判定就失败了）；
  · `cwd` 与两边的**盘符**（Windows 上跨盘是最常见的失效形态）；
  · 写侧 `EventStore` 的真实路径 + 该目录的 `os.listdir()`（**写到哪**）；
  · `event_files()` / `iter_events(directory=…)` 读到的文件与条数（**读的是哪**）。

【口径】诊断里只有路径 / 文件名 / 计数 —— 不放事件载荷、不放令牌。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


def _drive_of(path: str) -> str:
    try:
        return os.path.splitdrive(str(path or ""))[0] or "(none)"
    except (TypeError, ValueError):
        return "(bad)"


def _listing(path: str, *, limit: int = 12) -> str:
    """目录的 `os.listdir` 读数（**绕开** enumerate_path，直接看盘上有什么）"""
    if not path:
        return "(no path)"
    try:
        if not os.path.isdir(path):
            return "目录不存在"
        names = sorted(os.listdir(path))
    except OSError as exc:                       # 权限/占用/竞态
        return f"listdir 失败（{type(exc).__name__}）"
    head = names[:limit]
    tail = "" if len(names) <= limit else f" …共 {len(names)} 项"
    return f"{len(names)} 项 {head}{tail}"


def _writer_path(store: Any) -> str:
    """写侧 `EventStore` 的真实路径（拿不到就如实说拿不到）"""
    if store is None:
        return "(未注入 store)"
    for attr in ("_path", "path"):
        value = getattr(store, attr, None)
        if value:
            return str(value)
    return f"(未知类型 {type(store).__name__})"


def _events_files(directory: str) -> List[str]:
    from agent.observability.events import event_files

    try:
        return [os.path.basename(p) for p in event_files(directory)]
    except Exception as exc:                     # noqa: BLE001 诊断本身不许再炸
        return [f"(event_files 抛 {type(exc).__name__})"]


def _enumerated(directory: str) -> str:
    from agent.safe_paths import enumerate_path

    try:
        got = enumerate_path(directory)
    except Exception as exc:                     # noqa: BLE001
        return f"(enumerate_path 抛 {type(exc).__name__})"
    return "(None)" if got is None else str(got)


def _iterated(directory: str) -> str:
    from agent.observability.events import iter_events

    try:
        rows = iter_events(directory=directory)
    except Exception as exc:                     # noqa: BLE001
        return f"iter_events 抛出 {type(exc).__name__}"
    types: Dict[str, int] = {}
    for env in rows:
        key = str(getattr(env, "type", ""))
        types[key] = types.get(key, 0) + 1
    return f"{len(rows)} 条 {types}"


def ledger_blob(*, directory: str, store: Any = None,
                guard: Any = None, extra: Optional[Dict[str, Any]] = None) -> str:
    """拼一段**可直接贴进断言消息**的台账诊断（缩进两行，便于在 CI 里读）

    Args:
        directory: 被测代码请求的事件目录（读侧的 `events_dir`）。
        store: 写侧 `EventStore`（`seed_cost` 用的那个单例）。
        guard: 有 `_writer_store()` 的护栏对象（`JudgeBudgetGuard`）——
            给了就以**护栏自己解析出来的**写侧路径为准。
        extra: 额外想看的字段（如 `status`），值用 `repr` 截断后附上。
    """
    directory = str(directory or "")
    writer_store = store
    if guard is not None:
        try:
            writer_store = guard._writer_store()      # noqa: SLF001 诊断就为看这个
        except Exception as exc:                      # noqa: BLE001
            writer_store = f"(解析写侧抛 {type(exc).__name__})"
    writer = _writer_path(writer_store)
    writer_dir = os.path.dirname(writer) if writer and not writer.startswith("(") else ""

    lines = [
        f"  events_dir   = {directory}",
        f"  enumerate    = {_enumerated(directory)}",
        f"  cwd          = {os.getcwd()}（盘 {_drive_of(os.getcwd())}）",
        f"  dir 盘       = {_drive_of(directory)}",
        f"  listdir(dir) = {_listing(directory)}",
        f"  writer path  = {writer}",
        f"  listdir(w)   = {_listing(writer_dir)}",
        f"  event_files  = {_events_files(directory)}",
        f"  iter_events  = {_iterated(directory)}",
    ]
    for key, value in (extra or {}).items():
        text = repr(value)
        lines.append(f"  {key} = {text[:400]}{' …' if len(text) > 400 else ''}")
    return "\n台账自诊断（写到哪 / 读的是哪）:\n" + "\n".join(lines)
