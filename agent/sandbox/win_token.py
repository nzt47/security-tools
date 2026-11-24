"""Windows 受限令牌子进程（E0-1）：`WRITE_RESTRICTED` + 写 SID 白名单。

【一句话】把当前进程的主令牌复制成**写受限令牌**，restricting SID 列表里只放
「logon SID + Everyone + 写身份 SID」，然后用 `CreateProcessAsUserW` 起子进程：
写操作只对 ACL 里带写身份 SID 的对象放行，其余一律 ACCESS_DENIED（含注册表、其它目录）。

【实测踩到的三个坑（都是本机真机结论，不要"优化"掉）】
1. **不能带 `CREATE_NO_WINDOW` / `CREATE_NEW_CONSOLE`**：受限令牌下子进程在 DLL 初始化阶段就
   `STATUS_DLL_INIT_FAILED(0xC0000142)`；子进程只能共享宿主控制台。stdout/stderr 因此用
   **继承的文件句柄**捕获（不是命名管道）。
2. **logon SID + Everyone 必须在 restricting 列表里**：缺了同样 0xC0000142（早期 DLL 初始化/CNG
   需要它们）；它们只是"保活组"，真正的写授权来自写身份 SID。
3. **默认 DACL 要合并写身份 ACE**：否则受限子进程创建匿名管道（孙进程 `capture_output`）
   被 pass-2 拒绝（Linux 上叫 EPERM）。读档合并 Everyone，写档合并写身份 SID。

【fail-closed】任何 Win32 调用失败都抛 `SandboxUnavailable`，绝不"失败后照常起进程"。
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from agent.sandbox.win_acl import SandboxError, SandboxUnavailable

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# ── 常量（Win32）──────────────────────────────────────────────────
_TOKEN_QUERY = 0x0008
_TOKEN_DUPLICATE = 0x0002
_TOKEN_ASSIGN_PRIMARY = 0x0001
_TOKEN_ADJUST_DEFAULT = 0x0080
_TOKEN_GROUPS = 2
_TOKEN_DEFAULT_DACL = 6
_SE_GROUP_LOGON_ID = 0xC0000000
_DISABLE_MAX_PRIVILEGE = 0x0001
_WRITE_RESTRICTED = 0x0008
_SE_GROUP_ENABLED = 0x00000004
_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_STARTF_USESTDHANDLES = 0x00000100
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_STILL_ACTIVE = 259
_INFINITE = 0xFFFFFFFF
_SECURITY_MAX_SID_SIZE = 68
_WIN_WORLD_SID = 1
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_CREATE_ALWAYS = 2
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_GRANT_ACCESS = 1
_NO_INHERITANCE = 0
_TRUSTEE_IS_SID = 0
_TRUSTEE_IS_UNKNOWN = 0
_ERROR_SUCCESS = 0

#: 受限子进程的进程数上限（含孙进程；防 fork 炸弹把 Job 之外的东西拖死）
_MAX_JOB_PROCESSES = 64
#: 输出上限（与 shell_tools._MAX_OUTPUT_BYTES 对齐；这里不 import tools，避免反向依赖）
_MAX_OUTPUT_BYTES = 102400
#: 捕获文件读取上限（多读一点再截断，保证截断标注用的是真实字节数）
_READ_LIMIT_BYTES = _MAX_OUTPUT_BYTES * 4

if _IS_WINDOWS:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    _HANDLE = wintypes.HANDLE
    _DWORD = wintypes.DWORD
    _BOOL = wintypes.BOOL
    _LPVOID = ctypes.c_void_p
    _LPWSTR = wintypes.LPWSTR

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", _DWORD), ("lpSecurityDescriptor", _LPVOID),
                    ("bInheritHandle", _BOOL)]

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", _LPVOID), ("Attributes", _DWORD)]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", _DWORD), ("lpReserved", _LPWSTR), ("lpDesktop", _LPWSTR),
            ("lpTitle", _LPWSTR), ("dwX", _DWORD), ("dwY", _DWORD),
            ("dwXSize", _DWORD), ("dwYSize", _DWORD), ("dwXCountChars", _DWORD),
            ("dwYCountChars", _DWORD), ("dwFillAttribute", _DWORD), ("dwFlags", _DWORD),
            ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", _HANDLE), ("hStdOutput", _HANDLE), ("hStdError", _HANDLE),
        ]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", _HANDLE), ("hThread", _HANDLE),
                    ("dwProcessId", _DWORD), ("dwThreadId", _DWORD)]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", _DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", _DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", _DWORD),
            ("SchedulingClass", _DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _TRUSTEE_W(ctypes.Structure):
        _fields_ = [("pMultipleTrustee", _LPVOID),
                    ("MultipleTrusteeOperation", ctypes.c_int),
                    ("TrusteeForm", ctypes.c_int),
                    ("TrusteeType", ctypes.c_int),
                    ("ptstrName", _LPVOID)]

    class _EXPLICIT_ACCESS_W(ctypes.Structure):
        _fields_ = [("grfAccessPermissions", _DWORD),
                    ("grfAccessMode", ctypes.c_int),
                    ("grfInheritance", _DWORD),
                    ("Trustee", _TRUSTEE_W)]

    def _bind() -> None:
        k, a = _kernel32, _advapi32
        k.GetCurrentProcess.argtypes = []
        k.GetCurrentProcess.restype = _HANDLE
        k.OpenProcessToken.argtypes = [_HANDLE, _DWORD, ctypes.POINTER(_HANDLE)]
        k.OpenProcessToken.restype = _BOOL
        k.CloseHandle.argtypes = [_HANDLE]
        k.CloseHandle.restype = _BOOL
        k.WaitForSingleObject.argtypes = [_HANDLE, _DWORD]
        k.WaitForSingleObject.restype = _DWORD
        k.GetExitCodeProcess.argtypes = [_HANDLE, ctypes.POINTER(_DWORD)]
        k.GetExitCodeProcess.restype = _BOOL
        k.TerminateProcess.argtypes = [_HANDLE, ctypes.c_uint]
        k.TerminateProcess.restype = _BOOL
        k.CreateFileW.argtypes = [_LPWSTR, _DWORD, _DWORD,
                                  ctypes.POINTER(_SECURITY_ATTRIBUTES), _DWORD, _DWORD,
                                  _HANDLE]
        k.CreateFileW.restype = _HANDLE
        k.CreateJobObjectW.argtypes = [_LPVOID, _LPWSTR]
        k.CreateJobObjectW.restype = _HANDLE
        k.SetInformationJobObject.argtypes = [_HANDLE, ctypes.c_int, _LPVOID, _DWORD]
        k.SetInformationJobObject.restype = _BOOL
        k.AssignProcessToJobObject.argtypes = [_HANDLE, _HANDLE]
        k.AssignProcessToJobObject.restype = _BOOL
        k.TerminateJobObject.argtypes = [_HANDLE, ctypes.c_uint]
        k.TerminateJobObject.restype = _BOOL
        k.ResumeThread.argtypes = [_HANDLE]
        k.ResumeThread.restype = _DWORD
        k.LocalFree.argtypes = [_HANDLE]
        k.LocalFree.restype = _HANDLE
        a.GetTokenInformation.argtypes = [_HANDLE, ctypes.c_int, _LPVOID, _DWORD,
                                          ctypes.POINTER(_DWORD)]
        a.GetTokenInformation.restype = _BOOL
        a.GetLengthSid.argtypes = [_LPVOID]
        a.GetLengthSid.restype = _DWORD
        a.CopySid.argtypes = [_DWORD, _LPVOID, _LPVOID]
        a.CopySid.restype = _BOOL
        a.CreateWellKnownSid.argtypes = [ctypes.c_int, _LPVOID, _LPVOID,
                                         ctypes.POINTER(_DWORD)]
        a.CreateWellKnownSid.restype = _BOOL
        a.ConvertStringSidToSidW.argtypes = [_LPWSTR, ctypes.POINTER(_LPVOID)]
        a.ConvertStringSidToSidW.restype = _BOOL
        a.CreateRestrictedToken.argtypes = [_HANDLE, _DWORD, _DWORD, _LPVOID, _DWORD,
                                            _LPVOID, _DWORD, _LPVOID, ctypes.POINTER(_HANDLE)]
        a.CreateRestrictedToken.restype = _BOOL
        a.SetEntriesInAclW.argtypes = [_DWORD, _LPVOID, _LPVOID, ctypes.POINTER(_LPVOID)]
        a.SetEntriesInAclW.restype = _DWORD
        a.SetTokenInformation.argtypes = [_HANDLE, ctypes.c_int, _LPVOID, _DWORD]
        a.SetTokenInformation.restype = _BOOL
        a.CreateProcessAsUserW.argtypes = [
            _HANDLE, _LPWSTR, _LPWSTR, _LPVOID, _LPVOID, _BOOL, _DWORD, _LPVOID, _LPWSTR,
            ctypes.POINTER(_STARTUPINFOW), ctypes.POINTER(_PROCESS_INFORMATION)]
        a.CreateProcessAsUserW.restype = _BOOL


def _hval(handle: Any) -> int:
    return int(handle.value if hasattr(handle, "value") else handle)


def is_windows() -> bool:
    return _IS_WINDOWS


def is_available() -> bool:
    """平台可用性（真正能不能起进程要看 `RestrictedToken` 的构造是否抛错）。"""
    return _IS_WINDOWS


def unavailable_reason() -> str:
    return "" if _IS_WINDOWS else "当前平台不是 Windows（E0-1 只落地 Windows 受限令牌）"


class RestrictedToken:
    """当前进程主令牌的写受限副本（用完 `close()`；也可作上下文管理器）。"""

    def __init__(self, write_sid: Optional[str] = None):
        if not _IS_WINDOWS:
            raise SandboxUnavailable(unavailable_reason())
        _bind()
        self.write_sid = (write_sid or "").strip() or None
        self._token = _HANDLE()
        self._keepalive: list = []
        self._create()

    # ── 构造 ────────────────────────────────────────────────
    def _create(self) -> None:
        tok = _HANDLE()
        if not _kernel32.OpenProcessToken(
                _kernel32.GetCurrentProcess(),
                _TOKEN_QUERY | _TOKEN_DUPLICATE | _TOKEN_ASSIGN_PRIMARY
                | _TOKEN_ADJUST_DEFAULT, ctypes.byref(tok)):
            raise SandboxUnavailable(f"OpenProcessToken 失败 err={ctypes.get_last_error()}")

        logon = self._logon_sid(tok)
        world = self._well_known(_WIN_WORLD_SID)
        restricting = [logon, world]
        write_sid_ptr = None
        if self.write_sid:
            write_sid_ptr = self._sid_from_string(self.write_sid)
            restricting.append(write_sid_ptr)

        array = (_SID_AND_ATTRIBUTES * len(restricting))()
        for index, sid_ptr in enumerate(restricting):
            array[index].Sid = sid_ptr
        new_token = _HANDLE()
        ok = _advapi32.CreateRestrictedToken(
            tok, _DISABLE_MAX_PRIVILEGE | _WRITE_RESTRICTED,
            0, None, 0, None, len(restricting), array, ctypes.byref(new_token))
        if not ok:
            err = ctypes.get_last_error()
            _kernel32.CloseHandle(tok)
            raise SandboxUnavailable(f"CreateRestrictedToken 失败 err={err}")
        # 默认 DACL：读档合并 Everyone，写档合并写身份 SID（见模块 docstring 第 3 条）
        self._merge_default_dacl(new_token, write_sid_ptr or world)
        _kernel32.CloseHandle(tok)
        self._token = new_token
        self._keepalive = [logon, world, write_sid_ptr, array]

    def _logon_sid(self, tok: Any) -> Any:
        needed = _DWORD(0)
        _advapi32.GetTokenInformation(tok, _TOKEN_GROUPS, None, 0, ctypes.byref(needed))
        if needed.value == 0:
            raise SandboxUnavailable(f"GetTokenInformation(TokenGroups) 失败 "
                                     f"err={ctypes.get_last_error()}")
        buf = ctypes.create_string_buffer(needed.value)
        if not _advapi32.GetTokenInformation(tok, _TOKEN_GROUPS, buf, needed.value,
                                             ctypes.byref(needed)):
            raise SandboxUnavailable("GetTokenInformation(TokenGroups) 失败")
        count = _DWORD.from_buffer(buf, 0).value
        stride = ctypes.sizeof(_SID_AND_ATTRIBUTES)
        for index in range(count):
            offset = 8 + index * stride  # TOKEN_GROUPS 的数组按指针对齐，从偏移 8 开始
            ptr = ctypes.c_void_p.from_buffer(buf, offset).value
            attrs = _DWORD.from_buffer(buf, offset + 8).value
            if not ptr or (attrs & _SE_GROUP_LOGON_ID) != _SE_GROUP_LOGON_ID:
                continue
            length = _advapi32.GetLengthSid(ctypes.c_void_p(ptr))
            copy = ctypes.create_string_buffer(length)
            if not _advapi32.CopySid(length, copy, ctypes.c_void_p(ptr)):
                raise SandboxUnavailable("CopySid(logon) 失败")
            return ctypes.cast(copy, _LPVOID)
        raise SandboxUnavailable("令牌里找不到 logon SID（CreateRestrictedToken 前置条件）")

    def _well_known(self, sid_type: int) -> Any:
        buf = ctypes.create_string_buffer(_SECURITY_MAX_SID_SIZE)
        size = _DWORD(_SECURITY_MAX_SID_SIZE)
        if not _advapi32.CreateWellKnownSid(sid_type, None, buf, ctypes.byref(size)):
            raise SandboxUnavailable(
                f"CreateWellKnownSid({sid_type}) 失败 err={ctypes.get_last_error()}")
        return ctypes.cast(buf, _LPVOID)

    def _sid_from_string(self, value: str) -> Any:
        out = _LPVOID()
        if not _advapi32.ConvertStringSidToSidW(value, ctypes.byref(out)):
            raise SandboxUnavailable(
                f"ConvertStringSidToSidW 失败 err={ctypes.get_last_error()}")
        return out

    def _merge_default_dacl(self, token: Any, sid_ptr: Any) -> None:
        """把 sid 的 F ACE 合并进令牌默认 DACL（新对象——匿名管道/文件——因此通过 pass-2）。"""
        needed = _DWORD(0)
        _advapi32.GetTokenInformation(token, _TOKEN_DEFAULT_DACL, None, 0,
                                      ctypes.byref(needed))
        if needed.value == 0:
            raise SandboxUnavailable("GetTokenInformation(TokenDefaultDacl) 失败")
        buf = ctypes.create_string_buffer(needed.value)
        if not _advapi32.GetTokenInformation(token, _TOKEN_DEFAULT_DACL, buf, needed.value,
                                             ctypes.byref(needed)):
            raise SandboxUnavailable("GetTokenInformation(TokenDefaultDacl) 失败")
        old_dacl = ctypes.c_void_p.from_buffer(buf, 0)
        if not old_dacl.value:
            raise SandboxUnavailable("令牌没有默认 DACL（无法保证孙进程管道可建）")
        ea = _EXPLICIT_ACCESS_W()
        ea.grfAccessPermissions = 0x1F01FF  # FILE_ALL_ACCESS
        ea.grfAccessMode = _GRANT_ACCESS
        ea.grfInheritance = _NO_INHERITANCE
        ea.Trustee.TrusteeForm = _TRUSTEE_IS_SID
        ea.Trustee.TrusteeType = _TRUSTEE_IS_UNKNOWN
        ea.Trustee.ptstrName = sid_ptr
        new_dacl = _LPVOID()
        rc = _advapi32.SetEntriesInAclW(1, ctypes.byref(ea), old_dacl,
                                        ctypes.byref(new_dacl))
        if rc != _ERROR_SUCCESS or not new_dacl.value:
            raise SandboxUnavailable(f"SetEntriesInAclW 失败 rc={rc}")
        try:
            info = ctypes.create_string_buffer(8)
            ctypes.c_uint64.from_buffer(info, 0).value = int(new_dacl.value)
            if not _advapi32.SetTokenInformation(token, _TOKEN_DEFAULT_DACL, info, 8):
                raise SandboxUnavailable(
                    f"SetTokenInformation(TokenDefaultDacl) 失败 "
                    f"err={ctypes.get_last_error()}")
        finally:
            _kernel32.LocalFree(new_dacl)

    # ── 生命周期 ────────────────────────────────────────────
    def close(self) -> None:
        if self._token and _hval(self._token):
            _kernel32.CloseHandle(self._token)
        self._token = _HANDLE()
        self._keepalive = []

    def __enter__(self) -> "RestrictedToken":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - 兜底
        try:
            self.close()
        except Exception:
            pass


class _Job:
    """kill-on-close + 进程数上限的 Job（超时/异常时一把收掉整棵子进程树）。"""

    def __init__(self) -> None:
        self.handle = _HANDLE()
        job = _kernel32.CreateJobObjectW(None, None)
        if not _hval(job):
            raise SandboxUnavailable(f"CreateJobObjectW 失败 err={ctypes.get_last_error()}")
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_ACTIVE_PROCESS | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        info.BasicLimitInformation.ActiveProcessLimit = _MAX_JOB_PROCESSES
        if not _kernel32.SetInformationJobObject(
                job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                ctypes.sizeof(info)):
            _kernel32.CloseHandle(job)
            raise SandboxUnavailable("SetInformationJobObject 失败")
        self.handle = job

    def assign(self, process: Any) -> None:
        if not _kernel32.AssignProcessToJobObject(self.handle, process):
            raise SandboxUnavailable(
                f"AssignProcessToJobObject 失败 err={ctypes.get_last_error()}")

    def kill(self) -> None:
        if self.handle and _hval(self.handle):
            _kernel32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle and _hval(self.handle):
            _kernel32.CloseHandle(self.handle)
        self.handle = _HANDLE()


def _create_capture_file(path: Path) -> Any:
    sa = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), None, True)
    handle = _kernel32.CreateFileW(
        str(path), _GENERIC_WRITE, _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        ctypes.byref(sa), _CREATE_ALWAYS, _FILE_ATTRIBUTE_NORMAL, None)
    if not _hval(handle):
        raise SandboxUnavailable(f"CreateFileW 失败 err={ctypes.get_last_error()}")
    return handle


def _create_nul_handle() -> Any:
    sa = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), None, True)
    handle = _kernel32.CreateFileW(
        "NUL", _GENERIC_READ, _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        ctypes.byref(sa), _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None)
    if not _hval(handle):
        raise SandboxUnavailable(f"CreateFileW(NUL) 失败 err={ctypes.get_last_error()}")
    return handle


def _read_capture(path: Path, max_bytes: int = 0) -> str:
    try:
        limit = int(max_bytes) if max_bytes else _MAX_OUTPUT_BYTES
        with open(path, "rb") as fh:
            raw = fh.read(max(limit * 4, _READ_LIMIT_BYTES))
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    limit = int(max_bytes) if max_bytes else _MAX_OUTPUT_BYTES
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) > limit:
        text = encoded[:limit].decode("utf-8", errors="replace")
        return text + f"\n...（输出已截断，共 {len(encoded)} 字节）"
    return text


def _create_process(token: RestrictedToken, cmd: Sequence[str], cwd: str,
                    env_block: bytes, *, std_in: Any, std_out: Any, std_err: Any,
                    job: Optional[_Job], suspended: bool) -> _PROCESS_INFORMATION:
    si = _STARTUPINFOW()
    si.cb = ctypes.sizeof(_STARTUPINFOW)
    si.dwFlags = _STARTF_USESTDHANDLES
    si.hStdInput, si.hStdOutput, si.hStdError = std_in, std_out, std_err
    pi = _PROCESS_INFORMATION()
    env_buf = ctypes.create_string_buffer(env_block)
    cmd_buf = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(cmd)))
    flags = _CREATE_UNICODE_ENVIRONMENT | (_CREATE_SUSPENDED if suspended else 0)
    ok = _advapi32.CreateProcessAsUserW(
        token._token, None, cmd_buf, None, None, True, flags,
        ctypes.cast(env_buf, _LPVOID), str(cwd), ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        raise SandboxUnavailable(
            f"CreateProcessAsUserW 失败 err={ctypes.get_last_error()}")
    return pi


def spawn_captured(token: RestrictedToken, cmd: Sequence[str], cwd: str, env_block: bytes,
                   *, timeout: float, work_dir: Path,
                   max_output_bytes: int = 0) -> Dict[str, Any]:
    """起一个受限子进程并等待结果（stdout/stderr 走继承文件句柄）。

    Returns:
        ``{"ok", "stdout", "stderr", "exit_code", "timed_out", "pid", "sandboxed": True}``
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{os.getpid()}_{int(time.time() * 1000)}"
    out_path = work_dir / f"{stamp}.out"
    err_path = work_dir / f"{stamp}.err"
    h_out = h_err = h_in = None
    job = None
    pi = None
    try:
        h_out = _create_capture_file(out_path)
        h_err = _create_capture_file(err_path)
        h_in = _create_nul_handle()
        job = _Job()
        pi = _create_process(token, cmd, cwd, env_block, std_in=h_in,
                             std_out=h_out, std_err=h_err, job=job, suspended=True)
        job.assign(pi.hProcess)
        _kernel32.ResumeThread(pi.hThread)
        _kernel32.CloseHandle(pi.hThread)
        pi.hThread = _HANDLE()
        wait_ms = int(max(0.0, float(timeout)) * 1000)
        rc = _kernel32.WaitForSingleObject(pi.hProcess, wait_ms)
        timed_out = rc == _WAIT_TIMEOUT
        if timed_out:
            job.kill()
            _kernel32.WaitForSingleObject(pi.hProcess, 5000)
        code = _DWORD(0)
        _kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        exit_code = int(code.value)
    finally:
        if pi is not None and _hval(pi.hProcess):
            _kernel32.CloseHandle(pi.hProcess)
        if job is not None:
            job.close()
        for handle in (h_out, h_err, h_in):
            if handle is not None and _hval(handle):
                _kernel32.CloseHandle(handle)

    # 句柄都关了再读文件（否则读到的是缓冲中的半截内容）
    stdout = _read_capture(out_path, max_output_bytes)
    stderr = _read_capture(err_path, max_output_bytes)
    for path in (out_path, err_path):
        try:
            path.unlink()
        except OSError:
            pass
    result: Dict[str, Any] = {
        "ok": exit_code == 0 and not timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": -1 if timed_out else exit_code,
        "timed_out": timed_out,
        "sandboxed": True,
    }
    if timed_out:
        result["error"] = f"命令执行超时（{int(timeout)}秒，已终止受限子进程树）"
    return result


def spawn_detached(token: RestrictedToken, cmd: Sequence[str], cwd: str,
                   env_block: bytes) -> int:
    """起一个受限的独立进程（`run_program` 用），返回 pid，不等待。"""
    pi = _create_process(token, cmd, cwd, env_block, std_in=None, std_out=None,
                         std_err=None, job=None, suspended=False)
    pid = int(pi.dwProcessId)
    if _hval(pi.hThread):
        _kernel32.CloseHandle(pi.hThread)
    if _hval(pi.hProcess):
        _kernel32.CloseHandle(pi.hProcess)
    return pid


__all__ = [
    "SandboxError", "SandboxUnavailable", "RestrictedToken", "spawn_captured",
    "spawn_detached", "is_windows", "is_available", "unavailable_reason",
]


