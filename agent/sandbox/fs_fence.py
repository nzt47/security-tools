"""文件写围栏（E0-1，策略层）——与 DSH `fs-sandbox` 同构。

【这是策略围栏，不是内核边界】DSH 自己的 README 写得很直白：文件工具在可信代码里检查
「模型控制的路径」，解析到系统调用之间的 TOCTOU 只能缩小不能消除；真正的内核边界是 shell
子进程（见 `win_token.py` 的受限令牌）。照抄这个分层，不假装文件工具是 OS 级沙箱。

【唯一入口】所有宿主内写路径都调 `check_write()`；它返回 None（放行）或一个**结构化拒绝字典**。
- 拒绝文案固定为 `[sandbox: file access denied under <mode> mode]`（与 DSH 的模型可见标记一致），
  不带路径原文/异常文本（CodeQL 三条铁律：异常原文不进响应体）；
- 拒绝必审计（`sandbox_fs_denied`，只记工具名 + 路径指纹）。

【可写根与 shell 同源】`preset.writable_roots()` 同时喂给本围栏与 Windows ACL 授权；
两处各写一份「能写哪里」就是本仓明令避免的第二份口径。
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, Optional, Tuple

import agent.sandbox.preset as _preset

DENIED_TEMPLATE = "[sandbox: file access denied under {mode} mode]"

#: 允许在围栏下创建的目标必须是这些工具之一（只用于审计载荷，不做白名单拒绝）
_WRITE_TOOLS = frozenset({
    "write_file", "edit", "apply_patch", "workspace_write", "workspace_delete",
    "compress", "decompress", "move_file", "copy_file",
})


def _norm(path: Any) -> Optional[str]:
    """realpath + 大小写归一（Windows）。解析失败返回 None（调用方按拒绝处理）。"""
    try:
        raw = os.fspath(path)
    except TypeError:
        return None
    if not isinstance(raw, str) or not raw:
        return None
    try:
        # realpath 会解析符号链接/junction/8.3 短名，是「越界写」最容易被绕过的三个点
        real = os.path.realpath(raw)
    except (OSError, ValueError):
        return None
    return os.path.normcase(os.path.normpath(real))


def _under(child: str, root: str) -> bool:
    """child 是否等于 root 或在其子树内（按路径分量比较，不做字符串前缀）。"""
    if child == root:
        return True
    try:
        return os.path.commonpath([child, root]) == root
    except ValueError:
        # 不同盘符 / UNC 与本地路径混比：不可能包含
        return False


def _roots_norm(preset: Optional[str]) -> Tuple[str, ...]:
    out = []
    for root in _preset.writable_roots(preset):
        norm = _norm(root)
        if norm:
            out.append(norm)
    return tuple(out)


def is_write_allowed(path: Any, preset: Optional[str] = None) -> bool:
    """纯判定（无审计）：给测试与「先问再写」的调用方用。"""
    mode = preset or _preset.permission_preset()
    if mode == _preset.FULL_ACCESS:
        return True
    if mode == _preset.READ_ONLY:
        return False
    target = _norm(path)
    if target is None:
        return False
    return any(_under(target, root) for root in _roots_norm(mode))


def _audit_denied(tool: str, path: Any, mode: str) -> None:
    try:
        raw = os.fspath(path)
    except TypeError:
        raw = ""
    digest = hashlib.sha256(str(raw).encode("utf-8", errors="replace")).hexdigest()[:32]
    _preset.audit("sandbox_fs_denied", subject=tool, payload={
        "tool": tool if tool in _WRITE_TOOLS else str(tool),
        "mode": mode,
        "path_digest": "sha256:" + digest,
        "path_chars": len(str(raw or "")),
        "path_recorded": False,
    })


def check_write(path: Any, tool: str, *, preset: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """写前检查：放行返回 None，拒绝返回可直接回给模型的字典。

    Args:
        path: 目标路径（文件或目录；不要求已存在）
        tool: 发起写的工具名（`write_file` / `edit` / `apply_patch` / `workspace_delete` …）
        preset: 覆盖当前预设（测试/探针用；生产不传）

    Returns:
        None 或 ``{"ok": False, "blocked": True, "sandbox": <mode>, "error": <固定文案>}``
    """
    mode = preset or _preset.permission_preset()
    if mode == _preset.FULL_ACCESS:
        return None
    if is_write_allowed(path, mode):
        return None
    _audit_denied(tool, path, mode)
    return {
        "ok": False,
        "blocked": True,
        "sandbox": mode,
        "error": DENIED_TEMPLATE.format(mode=mode),
    }


def status() -> Dict[str, Any]:
    """围栏事实（API/探针用）：只给根与档位，不回显模型侧路径。"""
    mode = _preset.permission_preset()
    return {
        "preset": mode,
        "writable_roots": [str(p) for p in _preset.writable_roots(mode)],
        "denied_marker": DENIED_TEMPLATE.format(mode=mode),
        "enforced": mode != _preset.FULL_ACCESS,
    }


__all__ = ["DENIED_TEMPLATE", "check_write", "is_write_allowed", "status"]


