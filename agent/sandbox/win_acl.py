"""Windows 写身份与 ACL 授权（E0-1）。

【为什么是一个本地组，而不是 DSH 的 `S-1-4-x-y`】DSH 用「工作区路径派生 SID」当写身份，
授权走 `SetEntriesInAclW`（原生 API 不需要名字解析）。云枢这里要的是**可递归、可运维、可撤销**：
`icacls` 只认 LSA 能解析的名字/SID（实测 `icacls /grant *S-1-4-12-34` 直接报
"没有帐户名和安全 ID 之间的映射"），所以用一个**无成员本地组** `YunshuSandboxW` 当 SID 载体：
- 组只用来承载 ACL 里的写 ACE，没有任何成员，不参与登录/授权；
- 授权 = `icacls <root> /grant YunshuSandboxW:(OI)(CI)(F) /T`，递归由 icacls 原生完成（20k 文件实测 ~10s）；
- 撤销 = `icacls /remove:g` + `net localgroup /delete`。

【standing vs per-run】工作区根是 standing（跨会话复用，marker 命中即 O(1)）；私有 temp 是 per-run
（调用方建/删，授权/撤销都在本次运行内）。

【fail-closed】组/SID/授权任何一步失败都抛 `SandboxUnavailable`，调用方不得降级执行。
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

GROUP_NAME = "YunshuSandboxW"
GROUP_COMMENT = "Yunshu E0-1 sandbox write identity (no members)"

#: 递归授权/撤销的超时。**本机实测 80 万文件（含 .worktrees/data/.pytest_tmp）>30 分钟**，
#: 2026-11-24 首次预热就因为 1800s 上限被 timeout 杀掉（ACL 已大部分落地、marker 没写）。
#: 这是一次性运维动作，给足 2 小时；常规仓库 20k 文件约 10s。
_RECURSIVE_TIMEOUT_SEC = 7200.0
#: 单目录 icacls 查询 / 私有 temp（空目录）授权
_QUICK_TIMEOUT_SEC = 120.0
_NET_TIMEOUT_SEC = 60.0
_SECURITY_MAX_SID_SIZE = 68
_SID_TYPE_ALIAS = 4
_ERROR_NONE_MAPPED = 1332

_STATE_FILE = "grant_state.json"

advapi32 = ctypes.WinDLL("advapi32", use_last_error=True) if sys.platform == "win32" else None
_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None


class SandboxError(RuntimeError):
    """沙箱基础设施错误（令牌/授权/启动）。`error_code` 给 API 层判类型用。"""

    error_code = "SANDBOX_ERROR"

    def __init__(self, message: str, *, error_code: Optional[str] = None):
        super().__init__(message)
        #: 已知领域异常的可回给调用方的说明（CodeQL 铁律：响应体只能拿 `.message`，
        #: 不能直接 `str(exc)` —— 那是 py/stack-trace-exposure 的判定形态）
        self.message = message
        if error_code:
            self.error_code = error_code


class SandboxUnavailable(SandboxError):
    error_code = "SANDBOX_UNAVAILABLE"


class SandboxNotReady(SandboxError):
    """预设需要围栏，但写身份/授权还没就绪（fail-closed，不是降级）。"""

    error_code = "SANDBOX_NOT_READY"


def is_windows() -> bool:
    return sys.platform == "win32"


def state_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "sandbox" / _STATE_FILE


def _load_state() -> Dict[str, Any]:
    path = state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            grants = data.get("grants")
            if isinstance(grants, dict):
                return data
    except (OSError, ValueError):
        pass
    return {"grants": {}}


def _save_state(data: Dict[str, Any]) -> None:
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:  # marker 写不进去只影响「下次是否重授」，不阻断本次执行
        logger.warning("[sandbox] 写 grant marker 失败: %s", exc)


def _run(cmd: list, *, timeout: float) -> tuple:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding="mbcs", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return int(proc.returncode), (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"{type(exc).__name__}"


def group_sid(name: str = GROUP_NAME) -> Optional[str]:
    """本地组的 SID 字符串（不存在/无法解析返回 None）。"""
    if advapi32 is None:
        return None
    size = wintypes.DWORD(_SECURITY_MAX_SID_SIZE)
    sid = ctypes.create_string_buffer(_SECURITY_MAX_SID_SIZE)
    domain = ctypes.create_unicode_buffer(256)
    dom_size = wintypes.DWORD(256)
    use = wintypes.DWORD(0)
    ok = advapi32.LookupAccountNameW(None, name, ctypes.cast(sid, ctypes.c_void_p),
                                     ctypes.byref(size), domain, ctypes.byref(dom_size),
                                     ctypes.byref(use))
    if not ok:
        return None
    out = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(ctypes.cast(sid, ctypes.c_void_p),
                                           ctypes.byref(out)):
        return None
    try:
        return out.value or None
    finally:
        if _KERNEL32 is not None:
            _KERNEL32.LocalFree(out)


def ensure_group(name: str = GROUP_NAME) -> str:
    """确保写身份组存在，返回 SID；创建失败（无管理员权限等）抛 SandboxUnavailable。"""
    sid = group_sid(name)
    if sid:
        return sid
    if not is_windows():
        raise SandboxUnavailable("当前平台不是 Windows")
    rc, out = _run(["net", "localgroup", name, "/add", "/comment:" + GROUP_COMMENT],
                   timeout=_NET_TIMEOUT_SEC)
    sid = group_sid(name)
    if sid:
        logger.info("[sandbox] 已创建写身份组 %s (%s)", name, sid)
        return sid
    raise SandboxUnavailable(
        "无法创建沙箱写身份组（需要管理员权限）："
        f"{GROUP_NAME}；net rc={rc} out={out.strip()[:200]}")


def delete_group(name: str = GROUP_NAME) -> bool:
    """删除写身份组（回滚用；不存在视为成功）。"""
    if group_sid(name) is None:
        return True
    rc, _out = _run(["net", "localgroup", name, "/delete"], timeout=_NET_TIMEOUT_SEC)
    return rc == 0


def _norm_root(root: Any) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(root)))


def has_write_ace(root: Any) -> bool:
    """目标上是否**真的**有写身份 ACE（防「marker 说授过、目录其实被重建过」）。

    marker 只按路径 + SID 记账；工作区被删掉重建（新 clone、手工清理）后路径不变但 ACE 没了。
    只信 marker 会让受限子进程得到一句莫名其妙的 "Access is denied"；因此在 marker 命中时
    再对根做一次单目录 `icacls` 校验（不递归，几十毫秒）。
    """
    if not is_windows():
        return True
    rc, out = _run(["icacls", str(root)], timeout=_QUICK_TIMEOUT_SEC)
    if rc != 0:
        return False
    if GROUP_NAME in out:
        return True
    sid = str((_load_state().get("group_sid") or ""))
    return bool(sid) and sid in out


def is_granted(root: Any, *, sid: Optional[str] = None) -> bool:
    """marker 命中**且** ACE 仍在，才算已授权。"""
    data = _load_state()
    entry = (data.get("grants") or {}).get(_norm_root(root))
    if not isinstance(entry, dict):
        return False
    current_sid = sid or data.get("group_sid")
    if not entry.get("sid") or entry.get("sid") != current_sid:
        return False
    return has_write_ace(root)


def grant(root: Any, *, sid: Optional[str] = None, standing: bool = True,
          timeout: float = _RECURSIVE_TIMEOUT_SEC) -> str:
    """给 root（递归）授写 ACE；返回组 SID。任何失败抛 SandboxUnavailable。"""
    if not is_windows():
        raise SandboxUnavailable("当前平台不是 Windows")
    target = Path(root)
    if not target.exists():
        raise SandboxUnavailable(f"授权根不存在: {target.name}")
    resolved_sid = sid or ensure_group()
    spec = f"{GROUP_NAME}:(OI)(CI)(F)"
    rc, out = _run(["icacls", str(target), "/grant", spec, "/T", "/Q", "/C"],
                   timeout=timeout)
    if rc != 0:
        raise SandboxUnavailable(f"ACL 授权失败（icacls rc={rc}）: {out.strip()[:200]}")
    if standing:
        data = _load_state()
        data["group_sid"] = resolved_sid
        data.setdefault("grants", {})[_norm_root(target)] = {
            "sid": resolved_sid,
            "granted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "files": "recursive",
        }
        _save_state(data)
    return resolved_sid


def revoke(root: Any, *, standing: bool = True,
           timeout: float = _RECURSIVE_TIMEOUT_SEC) -> bool:
    """撤掉写 ACE（递归）；返回是否成功。不存在/无 ACE 也视为成功（幂等）。"""
    if not is_windows():
        return True
    rc, _out = _run(["icacls", str(root), "/remove:g", GROUP_NAME, "/T", "/Q", "/C"],
                    timeout=timeout)
    if standing:
        data = _load_state()
        (data.get("grants") or {}).pop(_norm_root(root), None)
        _save_state(data)
    return rc == 0


def ensure_grant(root: Any, *, sid: Optional[str] = None) -> str:
    """standing 根：marker 命中直接返回，否则现场授权（首次可能很慢，见设计页）。"""
    resolved_sid = sid or group_sid() or ensure_group()
    if is_granted(root, sid=resolved_sid):
        return resolved_sid
    return grant(root, sid=resolved_sid, standing=True)


def status() -> Dict[str, Any]:
    """给探针/诊断看的事实（不触发任何创建/授权）。"""
    data = _load_state()
    return {
        "platform": "windows" if is_windows() else sys.platform,
        "group": GROUP_NAME,
        "group_sid": group_sid(),
        "state_path": str(state_path()),
        "grants": sorted((data.get("grants") or {}).keys()),
    }


__all__ = [
    "GROUP_NAME", "SandboxError", "SandboxUnavailable", "SandboxNotReady",
    "is_windows", "state_path", "group_sid", "ensure_group", "delete_group",
    "is_granted", "grant", "revoke", "ensure_grant", "status",
]

