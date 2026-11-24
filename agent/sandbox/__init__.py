"""沙箱能力包（E0-1）：权限预设 + 文件围栏 + Windows 受限令牌执行。

对外稳定入口（其余模块请只从这里取，不要直接 import 子模块内部函数）：

- `permission_preset()` / `status()`：当前预设与可写边界事实；
- `check_write(path, tool)`：宿主内写前的文件围栏（None=放行，dict=拒绝）；
- `run_captured(argv, cwd, timeout)` / `spawn_detached(argv, cwd)`：受限子进程执行；
- `ready()`：围栏是否可执行（fail-closed 判据）。

设计页见 `docs/plan/2026-11-24-e0-1-permission-preset-design.md`。
"""
from __future__ import annotations

from typing import Any, Dict

from agent.sandbox.fs_fence import DENIED_TEMPLATE, check_write, is_write_allowed
from agent.sandbox.preset import (
    DEFAULT_PRESET, FULL_ACCESS, PRESET_ENV, PRESETS, READ_ONLY, ROOT_ENV, WORKSPACE_WRITE,
    approval_policy, is_confined, permission_preset, sandbox_root, temp_root, writable_roots,
)
from agent.sandbox.shell_run import ready, run_captured, spawn_detached
from agent.sandbox.win_acl import SandboxError, SandboxNotReady, SandboxUnavailable


def status() -> Dict[str, Any]:
    """聚合事实（API/探针/健康检查用；不做授权、不起进程）。"""
    import agent.sandbox.preset as _preset
    import agent.sandbox.win_acl as win_acl
    import agent.sandbox.win_token as win_token
    ok, reason = ready()
    base = _preset.status()
    base.update({
        "os_sandbox": {
            "platform": "windows" if win_token.is_windows() else "other",
            "available": win_token.is_available(),
            "ready": ok,
            "reason": reason,
            "identity": win_acl.status()["group_sid"],
        },
        "fs_fence": {
            "denied_marker": DENIED_TEMPLATE.format(mode=_preset.permission_preset()),
            "enforced": _preset.permission_preset() != FULL_ACCESS,
        },
    })
    return base


__all__ = [
    "PRESET_ENV", "ROOT_ENV", "READ_ONLY", "WORKSPACE_WRITE", "FULL_ACCESS", "PRESETS",
    "DEFAULT_PRESET", "DENIED_TEMPLATE", "SandboxError", "SandboxNotReady",
    "SandboxUnavailable", "approval_policy", "check_write", "is_confined",
    "is_write_allowed", "permission_preset", "ready", "run_captured", "sandbox_root",
    "spawn_detached", "status", "temp_root", "writable_roots",
]

