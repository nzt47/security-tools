"""受限 shell / 进程执行（E0-1 的编排层）。

把三件事拼起来：**预设**（能写哪里）→ **ACL 授权**（写身份在哪有 ACE）→ **受限令牌**（内核强制）。

对外只有两个入口：
- `run_captured(argv, cwd, timeout)`：等价 `subprocess.run(capture_output=True)` 的受限版（shell_execute 用）；
- `spawn_detached(argv, cwd)`：起独立受限进程并返回 pid（run_program 用）。

【fail-closed】`ready()` 不通过（非 Windows / 组建不出来 / standing 授权缺失）时直接抛
`SandboxNotReady`，由调用方把错误回给模型；**绝不**“失败就退回普通 subprocess”。
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import agent.sandbox.preset as _preset
import agent.sandbox.win_acl as win_acl
import agent.sandbox.win_token as win_token
from agent.sandbox.win_acl import SandboxNotReady, SandboxUnavailable

logger = logging.getLogger(__name__)

#: 受限子进程环境变量的白名单说明（**逐条字面量读取**，扫描器不接受动态家族——
#: `for key in TUPLE: os.environ.get(key)` 会被 settings 零缺口门禁判成未声明家族）
_RUN_DIR_PREFIX = "run-"

#: 小目录（私有 temp）授权的超时：空目录递归 <1s，给 60s 足够
_FAST_GRANT_TIMEOUT = 60.0

_ENV_KEYS_NOTE = "PATH/SYSTEMROOT/... 为系统与工具链必需项；不复制宿主 .env 密钥"


def build_env(temp_dir: Path) -> Dict[str, str]:
    """构造受限子进程环境：白名单复制 + TEMP/TMP/HOME 指到本次运行的私有临时目录。"""
    env: Dict[str, str] = {}
    _pairs = (
        ("PATH", os.environ.get("PATH")),
        ("SYSTEMROOT", os.environ.get("SYSTEMROOT")),
        ("SYSTEMDRIVE", os.environ.get("SYSTEMDRIVE")),
        ("WINDIR", os.environ.get("WINDIR")),
        ("COMSPEC", os.environ.get("COMSPEC")),
        ("USERPROFILE", os.environ.get("USERPROFILE")),
        ("HOMEDRIVE", os.environ.get("HOMEDRIVE")),
        ("HOMEPATH", os.environ.get("HOMEPATH")),
        ("USERNAME", os.environ.get("USERNAME")),
        ("USERDOMAIN", os.environ.get("USERDOMAIN")),
        ("APPDATA", os.environ.get("APPDATA")),
        ("LOCALAPPDATA", os.environ.get("LOCALAPPDATA")),
        ("OS", os.environ.get("OS")),
        ("NUMBER_OF_PROCESSORS", os.environ.get("NUMBER_OF_PROCESSORS")),
        ("PROCESSOR_ARCHITECTURE", os.environ.get("PROCESSOR_ARCHITECTURE")),
        ("PATHEXT", os.environ.get("PATHEXT")),
    )
    for key, value in _pairs:
        if value is not None:
            env[key] = value
    sandbox_temp = str(temp_dir)
    env["TEMP"] = sandbox_temp
    env["TMP"] = sandbox_temp
    env["HOME"] = sandbox_temp
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["CP_SANDBOX_CHILD"] = "1"  # 给脚本/测试一个“我在沙箱里”的可观测事实
    return env


def env_block(env: Mapping[str, str]) -> bytes:
    text = "\0".join(f"{k}={v}" for k, v in sorted(env.items())) + "\0"
    return text.encode("utf-16-le") + b"\0\0"


def ready() -> tuple:
    """围栏是否可执行：返回 (ok, reason)。read-only 不要求工作区授权。"""
    if not win_token.is_available():
        return False, win_token.unavailable_reason() or "不支持当前平台"
    mode = _preset.permission_preset()
    if mode == _preset.FULL_ACCESS:
        return True, ""
    if mode == _preset.READ_ONLY:
        return True, ""
    root = _preset.sandbox_root()
    try:
        sid = win_acl.group_sid()
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}"
    if not sid:
        return False, "写身份组未创建（运行 scripts/sandbox_grant.py 初始化）"
    if not win_acl.is_granted(root, sid=sid):
        return False, f"工作区根尚未授权（运行 scripts/sandbox_grant.py --root {root}）"
    return True, ""


def _run_dir() -> Path:
    run_dir = _preset.temp_root() / f"{_RUN_DIR_PREFIX}{os.getpid()}-{int(time.time() * 1000)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _cleanup(run_dir: Optional[Path]) -> None:
    if run_dir is None:
        return
    shutil.rmtree(run_dir, ignore_errors=True)


def run_captured(argv: Sequence[str], cwd: str, timeout: float, *,
                 preset: Optional[str] = None) -> Dict[str, Any]:
    """受限执行一个 argv（不经过 shell 再解析），返回与 legacy 执行器同形的字典。

    Raises:
        SandboxNotReady: 预设要求围栏但围栏不可用（fail-closed）
        SandboxUnavailable: 令牌/授权/启动失败
    """
    mode = preset or _preset.permission_preset()
    if mode == _preset.FULL_ACCESS:
        raise SandboxUnavailable("full-access 不应走受限执行器")
    ok, reason = ready() if preset is None else _ready_for(mode)
    if not ok:
        raise SandboxNotReady(f"沙箱不可用：{reason}")

    write_sid: Optional[str] = None
    if mode == _preset.WORKSPACE_WRITE:
        write_sid = win_acl.ensure_grant(_preset.sandbox_root())

    run_dir = _run_dir()
    try:
        temp_dir = run_dir / "tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        if mode == _preset.WORKSPACE_WRITE:
            win_acl.grant(temp_dir, standing=False, timeout=_FAST_GRANT_TIMEOUT)
        env = build_env(temp_dir)
        with win_token.RestrictedToken(write_sid) as token:
            result = win_token.spawn_captured(
                token, list(argv), str(Path(cwd).resolve()), env_block(env),
                timeout=float(timeout), work_dir=run_dir)
    finally:
        _cleanup(run_dir)

    result["sandbox_preset"] = mode
    result["writable_roots"] = [str(p) for p in _preset.writable_roots(mode)]
    _preset.audit("sandbox_shell_confined", subject="shell", payload={
        "mode": mode,
        "exit_code": result.get("exit_code"),
        "timed_out": bool(result.get("timed_out")),
        "output_bytes": len(result.get("stdout") or "") + len(result.get("stderr") or ""),
        "write_sid": bool(write_sid),
    })
    return result


def _ready_for(mode: str) -> tuple:
    """`ready()` 的显式档位版本（探针在同一进程里切档用）。"""
    if not win_token.is_available():
        return False, win_token.unavailable_reason() or "不支持当前平台"
    if mode == _preset.READ_ONLY or mode == _preset.FULL_ACCESS:
        return True, ""
    root = _preset.sandbox_root()
    sid = win_acl.group_sid()
    if not sid:
        return False, "写身份组未创建（运行 scripts/sandbox_grant.py 初始化）"
    if not win_acl.is_granted(root, sid=sid):
        return False, f"工作区根尚未授权（运行 scripts/sandbox_grant.py --root {root}）"
    return True, ""


def spawn_detached(argv: Sequence[str], cwd: str, *,
                   preset: Optional[str] = None) -> int:
    """起一个受限的独立进程，返回 pid（run_program 用）。异常即 fail-closed。

    【临时目录为什么不像 `run_captured` 那样 per-run】独立进程活过本次调用，删掉它的
    TEMP 会让它后续写临时文件失败。这里用固定的 `tmp/detached`（首次创建即 standing 授权），
    代价是多次 run_program 共享同一个临时目录（这些进程本来就是同一个宿主发起的白名单程序）。
    """
    mode = preset or _preset.permission_preset()
    if mode == _preset.FULL_ACCESS:
        raise SandboxUnavailable("full-access 不应走受限执行器")
    ok, reason = _ready_for(mode)
    if not ok:
        raise SandboxNotReady(f"沙箱不可用：{reason}")
    write_sid = win_acl.ensure_grant(_preset.sandbox_root()) \
        if mode == _preset.WORKSPACE_WRITE else None
    temp_dir = _preset.temp_root() / "detached"
    temp_dir.mkdir(parents=True, exist_ok=True)
    if mode == _preset.WORKSPACE_WRITE and not win_acl.is_granted(temp_dir, sid=write_sid):
        win_acl.grant(temp_dir, sid=write_sid, standing=True, timeout=_FAST_GRANT_TIMEOUT)
    env = build_env(temp_dir)
    with win_token.RestrictedToken(write_sid) as token:
        pid = win_token.spawn_detached(
            token, list(argv), str(Path(cwd).resolve()), env_block(env))
    _preset.audit("sandbox_shell_confined", subject="run_program", payload={
        "mode": mode, "pid": pid, "write_sid": bool(write_sid)})
    return pid


__all__ = ["build_env", "env_block", "ready", "run_captured", "spawn_detached"]



