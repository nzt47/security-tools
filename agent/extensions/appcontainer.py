"""Windows AppContainer 启动器（P2，2026-11-23）。

【解决什么】
- 第三方插件包在独立子进程里跑（已有进程边界），本模块再给它一个 **AppContainer**
  令牌：默认无网络、读不到用户目录，只能访问显式 ACL 授权的插件目录/数据目录。
- 环境变量由调用方显式构造（最小白名单），不继承宿主 .env。

【不解决什么】
- 不是虚拟机：System32 等系统目录仍可读（ALL APPLICATION PACKAGES 默认权限）。
- 不覆盖 pip 依赖供应链；签名也不覆盖 .deps（见 plugin_signature 的边界说明）。
- 仅 Windows 10+；非 Windows 上 is_available() 返回 False，调用方应拒绝启动（fail-closed）。

【开关】plugin_policy.sandbox_mode()：off（默认）/ appcontainer。
不可用时绝不静默回退到无沙箱进程。
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

_IS_WINDOWS = sys.platform == "win32"
_SANDBOX_ROOT = Path(__file__).resolve().parents[2] / "agent" / "data" / "extensions_packages" / ".sandbox"

#: AppContainer 下 CreateProcessW 需要的最小 env（缺 USERPROFILE 等会返回 error 203）。
#: 逐条字面量读取：扫描器不允许 for key in tuple + os.environ.get(key) 的动态家族。

CREATE_UNICODE_ENVIRONMENT = 0x00000400
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_NO_WINDOW = 0x08000000
STARTF_USESTDHANDLES = 0x00000100
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
HANDLE_FLAG_INHERIT = 0x00000001
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
STILL_ACTIVE = 259
INFINITE = 0xFFFFFFFF
ERROR_ALREADY_EXISTS_HR = 0x800700B7

#: icacls 首次给 Python 目录授权会递归现有子文件，给足时间；调用参数不写裸数字。
_ICACLS_TIMEOUT_SEC = 120.0


class AppContainerUnavailable(RuntimeError):
    error_code = "PLUGIN_SANDBOX_UNAVAILABLE"


class AppContainerLaunchError(RuntimeError):
    error_code = "PLUGIN_SANDBOX_LAUNCH_FAILED"


def sandbox_data_dir(plugin_id: str) -> Path:
    """每插件独立数据目录（runner、TEMP、可写文件都放这里）。"""
    return _SANDBOX_ROOT / profile_name(plugin_id) / "data"


def profile_name(plugin_id: str) -> str:
    """稳定、可读的 AppContainer profile 名（同一插件复用，便于清理）。"""
    raw = str(plugin_id or "plugin")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")[:40] or "plugin"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return ("YunshuPlugin-" + safe + "-" + digest)[:64]


def build_environment(data_dir: Path, deps_dir: Optional[Path] = None,
                      extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """构造最小环境变量：不复制 .env 密钥；TEMP/HOME 指向插件自己的数据目录。"""
    data = str(data_dir)
    candidates = (
        ("PATH", os.environ.get("PATH")),
        ("SYSTEMROOT", os.environ.get("SYSTEMROOT")),
        ("SYSTEMDRIVE", os.environ.get("SYSTEMDRIVE")),
        ("WINDIR", os.environ.get("WINDIR")),
        ("TEMP", os.environ.get("TEMP")),
        ("TMP", os.environ.get("TMP")),
        ("COMSPEC", os.environ.get("COMSPEC")),
        ("USERPROFILE", os.environ.get("USERPROFILE")),
        ("APPDATA", os.environ.get("APPDATA")),
        ("LOCALAPPDATA", os.environ.get("LOCALAPPDATA")),
        ("HOMEDRIVE", os.environ.get("HOMEDRIVE")),
        ("HOMEPATH", os.environ.get("HOMEPATH")),
        ("USERNAME", os.environ.get("USERNAME")),
        ("USERDOMAIN", os.environ.get("USERDOMAIN")),
        ("PATHEXT", os.environ.get("PATHEXT")),
        ("OS", os.environ.get("OS")),
        ("NUMBER_OF_PROCESSORS", os.environ.get("NUMBER_OF_PROCESSORS")),
        ("PROCESSOR_ARCHITECTURE", os.environ.get("PROCESSOR_ARCHITECTURE")),
    )
    env: Dict[str, str] = {k: v for k, v in candidates if v is not None}
    env["TEMP"] = data
    env["TMP"] = data
    env["HOME"] = data
    env["USERPROFILE"] = data
    env["APPDATA"] = data
    env["LOCALAPPDATA"] = data
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["CP_PLUGIN_PROCESS_ISOLATED"] = "1"
    env["PYTHONPATH"] = str(deps_dir) if deps_dir is not None and Path(deps_dir).is_dir() else ""
    if extra:
        for key, value in extra.items():
            env[str(key)] = str(value)
    return env


def build_env_block(env: Mapping[str, str]) -> bytes:
    """CreateProcessW 的 UTF-16 环境块（排序 + 双 NUL）。"""
    text = "\0".join(f"{k}={v}" for k, v in sorted(env.items())) + "\0"
    return text.encode("utf-16-le") + b"\0\0"


def ensure_runtime_access() -> None:
    """一次性把 Python 运行目录授予 ALL APPLICATION PACKAGES 只读执行。

    为什么不按插件 SID 逐个授权：`icacls (OI)(CI)` 会递归现有子文件，
    每建一个新 profile 都要 20–60s；ALL APPLICATION PACKAGES 是标准 AppContainer 组，
    只需一次。插件代码/数据仍按各自 SID 授权，互不可读。
    """
    if not _IS_WINDOWS:
        raise AppContainerUnavailable("当前平台不是 Windows")
    target = Path(sys.executable).resolve().parent
    marker = _SANDBOX_ROOT / ".python_runtime_acl.json"
    try:
        if marker.is_file():
            import json
            payload = json.loads(marker.read_text(encoding="utf-8"))
            if payload.get("python_dir") == str(target):
                return
    except Exception:
        pass
    marker.parent.mkdir(parents=True, exist_ok=True)
    if _icacls(target, ["/grant", "*S-1-15-2-1:(OI)(CI)(RX)"]) != 0:
        raise AppContainerLaunchError("Python 运行目录授权失败")
    import json
    marker.write_text(json.dumps({"python_dir": str(target)}, ensure_ascii=False),
                      encoding="utf-8")


def is_available() -> bool:
    if not _IS_WINDOWS:
        return False
    try:
        _load_libraries()
        return True
    except Exception:
        return False


def unavailable_reason() -> str:
    if not _IS_WINDOWS:
        return "当前平台不是 Windows"
    try:
        _load_libraries()
        return ""
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__


# ── Win32 绑定（仅 Windows 加载）─────────────────────────────────
if _IS_WINDOWS:
    from ctypes import wintypes

    LPVOID = ctypes.c_void_p
    DWORD = wintypes.DWORD
    BOOL = wintypes.BOOL
    HANDLE = wintypes.HANDLE
    LPWSTR = wintypes.LPWSTR

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", DWORD), ("lpSecurityDescriptor", LPVOID),
                    ("bInheritHandle", BOOL)]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", DWORD), ("lpReserved", LPWSTR), ("lpDesktop", LPWSTR), ("lpTitle", LPWSTR),
            ("dwX", DWORD), ("dwY", DWORD), ("dwXSize", DWORD), ("dwYSize", DWORD),
            ("dwXCountChars", DWORD), ("dwYCountChars", DWORD), ("dwFillAttribute", DWORD),
            ("dwFlags", DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", HANDLE), ("hStdOutput", HANDLE), ("hStdError", HANDLE),
        ]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", LPVOID)]

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", LPVOID), ("Attributes", DWORD)]

    class _SECURITY_CAPABILITIES(ctypes.Structure):
        _fields_ = [("AppContainerSid", LPVOID),
                    ("Capabilities", ctypes.POINTER(_SID_AND_ATTRIBUTES)),
                    ("CapabilityCount", DWORD), ("Reserved", DWORD)]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", HANDLE), ("hThread", HANDLE),
                    ("dwProcessId", DWORD), ("dwThreadId", DWORD)]

    _libs: Optional[Dict[str, Any]] = None

    def _load_libraries() -> Dict[str, Any]:
        global _libs
        if _libs is not None:
            return _libs
        userenv = ctypes.WinDLL("userenv", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        userenv.CreateAppContainerProfile.argtypes = [LPWSTR, LPWSTR, LPWSTR, LPVOID, DWORD,
                                                      ctypes.POINTER(LPVOID)]
        userenv.CreateAppContainerProfile.restype = ctypes.c_long
        userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [LPWSTR, ctypes.POINTER(LPVOID)]
        userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
        userenv.DeleteAppContainerProfile.argtypes = [LPWSTR]
        userenv.DeleteAppContainerProfile.restype = ctypes.c_long
        advapi32.ConvertSidToStringSidW.argtypes = [LPVOID, ctypes.POINTER(LPWSTR)]
        advapi32.ConvertSidToStringSidW.restype = BOOL

        kernel32.CreatePipe.argtypes = [ctypes.POINTER(HANDLE), ctypes.POINTER(HANDLE),
                                        ctypes.POINTER(_SECURITY_ATTRIBUTES), DWORD]
        kernel32.CreatePipe.restype = BOOL
        kernel32.SetHandleInformation.argtypes = [HANDLE, DWORD, DWORD]
        kernel32.SetHandleInformation.restype = BOOL
        kernel32.CloseHandle.argtypes = [HANDLE]
        kernel32.CloseHandle.restype = BOOL
        kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
        kernel32.WaitForSingleObject.restype = DWORD
        kernel32.GetExitCodeProcess.argtypes = [HANDLE, ctypes.POINTER(DWORD)]
        kernel32.GetExitCodeProcess.restype = BOOL
        kernel32.TerminateProcess.argtypes = [HANDLE, ctypes.c_uint]
        kernel32.TerminateProcess.restype = BOOL
        kernel32.InitializeProcThreadAttributeList.argtypes = [LPVOID, DWORD, DWORD,
                                                               ctypes.POINTER(ctypes.c_size_t)]
        kernel32.InitializeProcThreadAttributeList.restype = BOOL
        kernel32.UpdateProcThreadAttribute.argtypes = [LPVOID, DWORD, ctypes.c_size_t, LPVOID,
                                                       ctypes.c_size_t, LPVOID, LPVOID]
        kernel32.UpdateProcThreadAttribute.restype = BOOL
        kernel32.DeleteProcThreadAttributeList.argtypes = [LPVOID]
        kernel32.CreateProcessW.argtypes = [LPWSTR, LPWSTR, ctypes.POINTER(_SECURITY_ATTRIBUTES),
                                            ctypes.POINTER(_SECURITY_ATTRIBUTES), BOOL, DWORD,
                                            LPVOID, LPWSTR, LPVOID,
                                            ctypes.POINTER(_PROCESS_INFORMATION)]
        kernel32.CreateProcessW.restype = BOOL
        _libs = {"userenv": userenv, "kernel32": kernel32, "advapi32": advapi32}
        return _libs

    def _hval(handle: Any) -> int:
        return int(handle.value if hasattr(handle, "value") else handle)

    def _check_hr(hr: int, what: str) -> None:
        if hr != 0:
            raise AppContainerLaunchError(
                "%s failed hr=0x%08x err=%s" % (what, hr & 0xFFFFFFFF, ctypes.get_last_error())
            )

    def _sid_string(sid: Any) -> str:
        libs = _load_libraries()
        out = LPWSTR()
        if not libs["advapi32"].ConvertSidToStringSidW(sid, ctypes.byref(out)):
            raise AppContainerLaunchError("ConvertSidToStringSidW failed")
        value = out.value or ""
        libs["kernel32"].LocalFree(out)
        return value

    def _icacls(path: Path, args: Sequence[str]) -> int:
        cmd = ["icacls", str(path), *args]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=_ICACLS_TIMEOUT_SEC,
                                    encoding="mbcs", errors="replace",
                                    creationflags=CREATE_NO_WINDOW)
            return int(result.returncode)
        except Exception:  # noqa: BLE001
            return 1

    class SandboxProcess:
        """Popen 兼容的最小适配：stdin/stdout/stderr、poll/wait/terminate/kill。"""

        def __init__(self, args: Sequence[str], pid: int, handle: Any,
                     stdin: Any, stdout: Any, stderr: Any, sandbox: "AppContainerSandbox"):
            self.args = list(args)
            self.pid = int(pid)
            self.stdin = stdin
            self.stdout = stdout
            self.stderr = stderr
            self.returncode: Optional[int] = None
            self._handle = handle
            self._sandbox = sandbox

        def poll(self) -> Optional[int]:
            libs = _load_libraries()
            code = DWORD(0)
            if not libs["kernel32"].GetExitCodeProcess(self._handle, ctypes.byref(code)):
                return self.returncode
            if code.value == STILL_ACTIVE:
                return None
            self.returncode = int(code.value)
            return self.returncode

        def wait(self, timeout: Optional[float] = None) -> int:
            libs = _load_libraries()
            ms = INFINITE if timeout is None else max(0, int(float(timeout) * 1000))
            rc = libs["kernel32"].WaitForSingleObject(self._handle, ms)
            if rc == WAIT_TIMEOUT:
                raise subprocess.TimeoutExpired(self.args, timeout)
            code = DWORD(0)
            libs["kernel32"].GetExitCodeProcess(self._handle, ctypes.byref(code))
            self.returncode = int(code.value)
            return self.returncode

        def terminate(self) -> None:
            libs = _load_libraries()
            if self.poll() is None:
                libs["kernel32"].TerminateProcess(self._handle, 1)

        def kill(self) -> None:
            self.terminate()

        def close(self) -> None:
            for stream in (self.stdin, self.stdout, self.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
            libs = _load_libraries()
            try:
                libs["kernel32"].CloseHandle(self._handle)
            except Exception:
                pass

    class AppContainerSandbox:
        """一个插件一个 profile；grant_paths 是 (path, "RX"|"F", inherit) 列表。"""

        def __init__(self, plugin_id: str, data_dir: Path,
                     grant_paths: Iterable[Tuple[Path, str, bool]] = ()):
            if not is_available():
                raise AppContainerUnavailable(
                    "AppContainer 不可用（%s）" % (unavailable_reason() or "unknown"))
            self.plugin_id = str(plugin_id)
            self.name = profile_name(plugin_id)
            self.data_dir = Path(data_dir)
            self._granted: list[Tuple[Path, str]] = []
            self._created = False
            self.sid = LPVOID()
            self.sid_string = ""
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._create_profile()
            # 插件数据目录：可写；cwd/TEMP 都指这里
            self._grant(self.data_dir, "F", inherit=True)
            for path, rights, inherit in grant_paths:
                self._grant(Path(path), rights, inherit=inherit)

        def _create_profile(self) -> None:
            libs = _load_libraries()
            hr = libs["userenv"].CreateAppContainerProfile(
                self.name, self.name, "yunshu plugin sandbox", None, 0,
                ctypes.byref(self.sid))
            if hr == 0:
                self._created = True
            elif (hr & 0xFFFFFFFF) == ERROR_ALREADY_EXISTS_HR:
                hr2 = libs["userenv"].DeriveAppContainerSidFromAppContainerName(
                    self.name, ctypes.byref(self.sid))
                _check_hr(hr2, "DeriveAppContainerSid")
            else:
                _check_hr(hr, "CreateAppContainerProfile")
            self.sid_string = _sid_string(self.sid)

        def _grant(self, path: Path, rights: str, *, inherit: bool) -> None:
            if not path.exists():
                return
            spec = "*" + self.sid_string + (":(OI)(CI)" if inherit else ":") + "(" + rights + ")"
            if _icacls(path, ["/grant", spec]) != 0:
                raise AppContainerLaunchError("ACL 授权失败: %s" % path.name)
            self._granted.append((path, spec))

        def close(self) -> None:
            for path, spec in reversed(self._granted):
                _icacls(path, ["/remove:g", "*" + self.sid_string])
            self._granted.clear()
            if self._created:
                try:
                    libs = _load_libraries()
                    libs["userenv"].DeleteAppContainerProfile(self.name)
                except Exception:
                    pass
                self._created = False

        def spawn(self, cmd: Sequence[str], cwd: Path, env: Mapping[str, str]) -> SandboxProcess:
            libs = _load_libraries()
            kernel32 = libs["kernel32"]
            si = _STARTUPINFOEXW()
            si.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
            si.StartupInfo.dwFlags = STARTF_USESTDHANDLES

            def make_pipe(parent_end: str):
                sa = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), None, True)
                r, w = HANDLE(), HANDLE()
                if not kernel32.CreatePipe(ctypes.byref(r), ctypes.byref(w),
                                           ctypes.byref(sa), 0):
                    raise AppContainerLaunchError("CreatePipe failed")
                # parent_end='read'：父进程读 r，子进程写 w；parent_end='write' 反之
                if parent_end == "read":
                    kernel32.SetHandleInformation(r, HANDLE_FLAG_INHERIT, 0)
                else:
                    kernel32.SetHandleInformation(w, HANDLE_FLAG_INHERIT, 0)
                return r, w

            stdin_r, stdin_w = make_pipe("write")
            stdout_r, stdout_w = make_pipe("read")
            stderr_r, stderr_w = make_pipe("read")
            si.StartupInfo.hStdInput = stdin_r
            si.StartupInfo.hStdOutput = stdout_w
            si.StartupInfo.hStdError = stderr_w

            size = ctypes.c_size_t(0)
            kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            attr_buf = ctypes.create_string_buffer(size.value)
            attr_list = ctypes.cast(attr_buf, LPVOID)
            if not kernel32.InitializeProcThreadAttributeList(attr_list, 1, 0,
                                                              ctypes.byref(size)):
                raise AppContainerLaunchError("InitializeProcThreadAttributeList failed")
            caps = _SECURITY_CAPABILITIES(ctypes.cast(self.sid, LPVOID), None, 0, 0)
            if not kernel32.UpdateProcThreadAttribute(
                    attr_list, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                    ctypes.byref(caps), ctypes.sizeof(caps), None, None):
                raise AppContainerLaunchError("UpdateProcThreadAttribute failed")
            si.lpAttributeList = attr_list

            env_buf = ctypes.create_string_buffer(build_env_block(env))
            cmd_buf = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(cmd)))
            pi = _PROCESS_INFORMATION()
            ok = kernel32.CreateProcessW(
                None, cmd_buf, None, None, True,
                EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
                ctypes.cast(env_buf, LPVOID), str(cwd),
                ctypes.byref(si), ctypes.byref(pi))
            kernel32.DeleteProcThreadAttributeList(attr_list)
            if not ok:
                for h in (stdin_r, stdin_w, stdout_r, stdout_w, stderr_r, stderr_w):
                    try:
                        kernel32.CloseHandle(h)
                    except Exception:
                        pass
                raise AppContainerLaunchError(
                    "CreateProcessW failed err=%s" % ctypes.get_last_error())

            # 父进程只留 stdin_w / stdout_r / stderr_r；关掉子进程端
            for h in (stdin_r, stdout_w, stderr_w):
                kernel32.CloseHandle(h)
            import msvcrt
            stdin_fd = msvcrt.open_osfhandle(_hval(stdin_w), os.O_WRONLY)
            stdout_fd = msvcrt.open_osfhandle(_hval(stdout_r), os.O_RDONLY)
            stderr_fd = msvcrt.open_osfhandle(_hval(stderr_r), os.O_RDONLY)
            stdin_file = os.fdopen(stdin_fd, "w", encoding="utf-8", newline="\n", buffering=1)
            stdout_file = os.fdopen(stdout_fd, "r", encoding="utf-8", errors="replace", newline="")
            stderr_file = os.fdopen(stderr_fd, "r", encoding="utf-8", errors="replace", newline="")
            return SandboxProcess(cmd, int(pi.dwProcessId), pi.hProcess,
                                  stdin_file, stdout_file, stderr_file, self)

else:
    class AppContainerSandbox:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise AppContainerUnavailable("当前平台不是 Windows")

    SandboxProcess = None  # type: ignore[assignment]
