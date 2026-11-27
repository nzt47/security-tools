"""E4-1 脚本编排（P4）：独立 node 子进程 + JSONL 工具 RPC。

入口：agent.orchestration.runtime.run（唯一实现）与 .whitelist.resolve（白名单）。
设计稿：docs/plan/2026-11-27-p4-编排安全设计.md。
"""
from __future__ import annotations

from agent.orchestration.runtime import (  # noqa: F401
    CODE_MESSAGES,
    enabled,
    limits,
    node_binary,
    probe_node,
    reset_probe_cache,
    run,
)
from agent.orchestration.whitelist import NEVER_ALLOWED, STATIC_ALLOWED, resolve  # noqa: F401

__all__ = [
    "CODE_MESSAGES", "NEVER_ALLOWED", "STATIC_ALLOWED", "enabled", "limits",
    "node_binary", "probe_node", "reset_probe_cache", "resolve", "run",
]
