# -*- coding: utf-8 -*-
"""工作台对话前缀登记 —— 让「压缩」能复用「对话」的前缀缓存（叶子模块，只依赖标准库）

背景（2026-11-08 实测，见 scripts/probe_compress_prefix_reuse.py 与交接文档 §11.71）
--------------------------------------------------------------------------------
压缩（摘要）调用与对话调用**构造上不是同一段前缀**：压缩是「摘要指令 + 最近 100 条、无 tools」，
对话是「工作台 system + tools + 预算窗口 N 条」⇒ 第 0 个 token 就不同，实测 hit=0/57,385，
每次压缩都按全价付一次普通对话的钱。

把压缩请求改成「与本轮对话**逐字相同**的 system+tools+消息切片 + 摘要指令放末尾」，
实测命中 99.8%（87,552/87,701）。拆开看，代价与边界：
  · 收益只在**缓存热**时成立：前缀被淘汰后全价 122k ≈ 现状的 1.85 倍 ⇒ 必须紧跟那轮对话；
  · 消息切片必须是**对话真正发出去的那一份**（含易变尾簇），落盘消息取不到这份逐字副本；
  · 输入可能顶到窗口 ⇒ 调用方还要按窗口留余量，装不下就回落旧路径。
本模块只负责第 2 条：把「刚刚真正发出去的前缀」按会话存一小段（有界、按时间淘汰）。

【为什么是叶子模块】plugins/chat 与 memory/memory_manager 都要用它：放进任一侧都会形成
chat → memory 或 memory → chat 的依赖边（arch_rules 判高严重度）。这里只 import 标准库。

开关：CP_COMPRESS_REUSE_CHAT_PREFIX（默认关 ⇒ 逐字旧行为；登记在 agent/settings/registry.py）。
"""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict

#: 开关名（默认关；置 1/true/yes/on 才启用）
ENV_FLAG = "CP_COMPRESS_REUSE_CHAT_PREFIX"
#: 最多留几个会话的前缀（每个会话一份；超出按最久未用淘汰）
MAX_SESSIONS = 8
#: 前缀的新鲜度上限（秒）：超时就当缓存冷了，压缩回落旧路径（冷缓存 = 全价 1.85 倍）
MAX_AGE_SECONDS = 1800
#: 真值解析：与仓里其它 CP_ 开关同一口径
_TRUE = ("1", "true", "yes", "on")

_lock = threading.Lock()
_store: "OrderedDict[str, dict]" = OrderedDict()


def _now() -> float:
    """单调时钟（可被单测替换；不随系统时间跳变）"""
    return time.monotonic()


def enabled() -> bool:
    """开关是否打开（缺省/拼错一律关 ⇒ 旧行为）"""
    return str(os.environ.get(ENV_FLAG, "")).strip().lower() in _TRUE


def record(session_id: str, system_prompt: str, tools, messages, window_tokens: int = 0,
           model: str = "") -> bool:
    """登记「本轮真正发出去的对话前缀」。失败/无会话一律静默返回 False（绝不击穿对话）。

    Args:
        session_id: 会话 id；空串表示未分会话（不登记，避免各会话互相污染）。
        system_prompt: 本轮定稿后的 system（已过 tools 对齐守卫）。
        tools: 本轮下发的 tool_defs（None/空 = 不带工具）。
        messages: 本轮真正下发的 messages（调用方传入的活列表会被**复制**，
            因为工具循环后续还会往里 append）。
        window_tokens: 本轮编排窗口（0 = 未测）。
        model: 本轮真正用的模型（会话绑定分身时可能与 .env 的 LLM_MODEL 不同）。
            压缩侧模型不一致 ⇒ 缓存键不同、命中恒 0，所以必须存下来供比对。
    """
    sid = str(session_id or "")
    if not sid:
        return False
    try:
        snapshot = {
            "session_id": sid,
            "system_prompt": str(system_prompt or ""),
            "tools": list(tools) if tools else None,
            "messages": [dict(m) for m in (messages or []) if isinstance(m, dict)],
            "window_tokens": int(window_tokens or 0),
            "model": str(model or ""),
            "updated_at": _now(),
        }
    except Exception:  # noqa: BLE001 登记失败不得影响对话主链路
        return False
    with _lock:
        _store[sid] = snapshot
        _store.move_to_end(sid)
        while len(_store) > MAX_SESSIONS:
            _store.popitem(last=False)
    return True


def peek(session_id: str):
    """取该会话最近一次登记的前缀（不判新鲜度；副本，调用方改了也不影响登记）。"""
    sid = str(session_id or "")
    if not sid:
        return None
    with _lock:
        item = _store.get(sid)
        if not item:
            return None
        _store.move_to_end(sid)
        return {
            "session_id": item["session_id"],
            "system_prompt": item["system_prompt"],
            "tools": list(item["tools"]) if item.get("tools") else None,
            "messages": [dict(m) for m in item.get("messages") or []],
            "window_tokens": int(item.get("window_tokens") or 0),
            "model": str(item.get("model") or ""),
            "updated_at": item["updated_at"],
        }


def get_if_fresh(session_id: str, max_age: float = MAX_AGE_SECONDS):
    """取前缀，但**过期就返回 None**（冷缓存下复用反而更贵，见模块 docstring）。"""
    item = peek(session_id)
    if not item:
        return None
    age = _now() - float(item.get("updated_at") or 0.0)
    if age < 0 or age > float(max_age):
        return None
    return item


def reset() -> None:
    """清空登记（单测 / 热重载用）。"""
    with _lock:
        _store.clear()


__all__ = ["ENV_FLAG", "MAX_SESSIONS", "MAX_AGE_SECONDS", "enabled",
           "record", "peek", "get_if_fresh", "reset"]
