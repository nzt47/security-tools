"""Sandbox — 分身沙箱隔离骨架

基于配置的显式权限声明，对分身的工具调用和执行操作进行约束。

设计思想（设计文档 6.1）：
- 默认拒绝：所有操作默认被拒绝，除非显式授权
- 最小权限：每个分身只拥有完成任务所需的最小权限集
- 适配器模式：预留 Docker/WebAssembly 沙箱适配位

权限级别（由 SubagentConfig.permissions 控制）：
- 'read': 读取文件/信息
- 'write': 写入/修改
- 'execute': 执行命令/代码
- 'network': 网络访问
- 'system': 系统级操作
"""

from __future__ import annotations

import contextvars
import logging
import re
import subprocess
import sys
import time
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional, Set, List, Dict, Callable, Iterator

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
#  路径包含判定（TASK-07 第 4 步第 4 项）
# ════════════════════════════════════════════════════════════════════
#
# 【为什么必须单独抽一个函数】改动前有两处**各自实现**的路径前缀判定，两处都错：
#   ① `Sandbox.check_path()`：`normalized.startswith(allowed_normalized)` —— **裸前缀**，
#      无分隔符边界 ⇒ 放行 `C:\work\a` 即放行 `C:\work\ab`；
#   ② `Sandbox.run_sandboxed()` 的工作目录检查：同一个裸 `startswith` 写法。
# 两处各写一遍还会让"修了一处漏另一处"变成常态，故收口到本函数。


def normalize_path(path: Any) -> str:
    """路径归一：**解 symlink/junction + 展开 8.3 短名**后再比

    【为什么不能只用 `abspath+normpath`】两者都是**纯字符串**运算：
      · 它们不解析符号链接 ⇒ `C:\\work\\link`（指向 `C:\\Windows`）在字符串上看仍在
        `C:\\work` 之内，实际却写到系统目录；
      · 它们不展开 Windows 8.3 短名 ⇒ `C:\\PROGRA~1` 与 `C:\\Program Files` 是两个
        不同的字符串，前缀比较会把同一条路径判成"在范围内 / 不在范围内"两种结论。
    故先 `os.path.realpath`（POSIX 与 Windows 上都解链接），再用
    `GetLongPathNameW` 展开短名（`ctypes` 实现，**不引入新依赖**；非 Windows 或调用
    失败时原样返回 —— 那一侧的路径本来就没有短名概念，见 D7）。
    """
    text = str(path or "")
    if not text:
        return ""
    try:
        absolute = os.path.abspath(text)
        resolved = _resolve_existing_prefix(absolute)
    except Exception:  # noqa: BLE001  归一失败 ⇒ 退回纯字符串（不抛）
        resolved = os.path.abspath(text)
    if os.name == "nt":
        resolved = _windows_long_path(resolved)
    return resolved


def _resolve_existing_prefix(path: str) -> str:
    """解析路径中**已存在的最长前缀**，再把剩余部分接回去

    【为什么不能直接 `os.path.realpath(path)`】实测（Windows，Python 3.12）：
    对**不存在**的路径，`realpath` 依赖 `nt._getfinalpathname`，该 API 要求路径存在，
    于是它**原样返回未解析的路径** —— 换句话说 `root/link/secret.txt`（`link` 指向
    `root` 之外，但 `secret.txt` 尚不存在）在 realpath 之后**仍然看起来在 root 内**，
    路径逃逸守卫被一条"文件还不存在"绕开。而"读一个还不存在的文件"恰恰是
    攻击者探测路径的常见第一步。
    修法：逐级上溯到第一个**存在**的祖先，对它做 realpath，再把剩余尾部拼回。
    这样 `link/secret.txt` 会先解析 `link` → `outside`，得到 `outside/secret.txt`。
    """
    head = path
    tail_parts: list = []
    while head and not os.path.exists(head):
        parent, name = os.path.split(head)
        if not name or parent == head:
            return path
        tail_parts.insert(0, name)
        head = parent
    if not head:
        return path
    resolved = os.path.realpath(head)
    return os.path.join(resolved, *tail_parts) if tail_parts else resolved


def _windows_long_path(path: str) -> str:
    """Windows：把 8.3 短名展开为长名（失败原样返回）"""
    try:
        import ctypes
        from ctypes import wintypes
        get_long = ctypes.windll.kernel32.GetLongPathNameW
        get_long.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_long.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        written = get_long(path, buffer, len(buffer))
        if written and 0 < written < len(buffer):
            return buffer.value
    except Exception:  # noqa: BLE001  非 Windows / API 不可用 / 路径不存在
        pass
    return path


def path_within(path: Any, root: Any) -> bool:
    """`path` 是否等于 `root` 或位于 `root` **之下**（带分隔符边界 + 解链接）

    【为什么用 `commonpath` 而不是"前缀 + os.sep"】`commonpath` 由标准库处理
    大小写/分隔符/驱动器盘符的差异（Windows 上 `C:/a` 与 `C:\\a` 同一个路径），
    自己拼 `os.sep` 又会在"root 以分隔符结尾"这类输入上出错。
    """
    target = normalize_path(path)
    base = normalize_path(root)
    if not target or not base:
        return False
    if target == base:
        return True
    try:
        return os.path.commonpath([target, base]) == base
    except ValueError:
        # 不同驱动器 / 混合绝对相对路径 ⇒ 一定不在范围内
        return False


class PermissionDenied(Exception):
    """权限拒绝异常

    当分身尝试执行未授权的操作时抛出。

    Attributes:
        permission: 被拒绝的权限名称
        operation: 被拒绝的操作描述
    """

    def __init__(self, permission: str, operation: str = ""):
        self.permission = permission
        self.operation = operation
        msg = f"权限拒绝: {permission}"
        if operation:
            msg += f" (操作: {operation})"
        super().__init__(msg)


# ════════════════════════════════════════════════════════════════════
#  执行校验数据结构（任务 7：沙箱执行策略落地，进程级隔离，无容器依赖）
# ════════════════════════════════════════════════════════════════════

# 危险命令补充模式（在 permission_system 黑名单之上，守「默认拒绝」语义）
# 元组: (编译正则, 拦截说明)。permission_system.BLACKLIST 已覆盖 rm -rf /、
# dd 写盘、format C:、fork 炸弹；本模块补充 SQL 破坏、Python 代码执行后门等。
SANDBOX_DANGEROUS_PATTERNS: List[tuple] = [
    (re.compile(r"\bdrop\s+table\b", re.IGNORECASE), "SQL DROP TABLE 破坏数据库"),
    (re.compile(r"\bdelete\s+from\b", re.IGNORECASE), "SQL DELETE 批量删除数据"),
    (re.compile(r"\bos\.system\s*\(", re.IGNORECASE), "Python os.system 执行任意命令"),
    (re.compile(r"\b(eval|exec)\s*\(", re.IGNORECASE), "Python eval/exec 任意代码执行"),
    (re.compile(r"\bsubprocess\.(call|run|popen)\s*\(", re.IGNORECASE), "Python subprocess 执行任意命令"),
    (re.compile(r"\bmkfs\b", re.IGNORECASE), "创建文件系统(格式化)"),
    (re.compile(r"\breg\s+delete\b", re.IGNORECASE), "注册表键删除"),
    (re.compile(r"-EncodedCommand|\bpowershell\s+-enc\b", re.IGNORECASE), "PowerShell 编码命令(混淆规避)"),
]


@dataclass
class CommandVerdict:
    """命令校验结果

    Attributes:
        allowed: 是否允许执行（默认拒绝语义：未通过校验即拒绝）
        reason: 拒绝原因（allowed=True 时为放行说明）
        matched_pattern: 命中的危险命令正则（仅拒绝时非空）
    """
    allowed: bool
    reason: str = ""
    matched_pattern: Optional[str] = None


@dataclass
class SandboxResourceLimits:
    """资源限制配置（由调用方传入，执行器强制）

    Attributes:
        timeout_s: 执行超时（秒），超时 kill 子进程
        max_memory_mb: 最大内存（MB）。POSIX 平台经 RLIMIT_AS 强制，
            Windows 无等价 rlimit，依赖超时兜底（进程级隔离的固有限制）
        max_output_bytes: 输出截断上限（默认 64KB）
    """
    timeout_s: float = 30.0
    max_memory_mb: int = 256
    max_output_bytes: int = 65536


@dataclass
class SandboxRunResult:
    """沙箱执行结果

    Attributes:
        allowed: 是否通过校验并执行（False 表示命令被拒绝，未启动子进程）
        reason: 拒绝原因或执行说明
        returncode: 子进程退出码（未启动为 None）
        stdout/stderr: 截断后的输出
        timed_out: 是否超时被杀
        duration_ms: 执行耗时（毫秒）
        error: 启动/执行异常信息
    """
    allowed: bool = True
    reason: str = ""
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_ms: float = 0.0
    error: Optional[str] = None


class Sandbox:
    """分身沙箱

    基于显式权限声明的执行隔离。
    遵循"默认拒绝"原则——所有操作默认被拒绝，除非在 allowed_permissions 中显式授权。

    用法:
        sandbox = Sandbox(allowed_permissions={"read", "write"})
        sandbox.check_permission("read")       # OK
        sandbox.check_permission("network")    # → PermissionDenied
    """

    # 权限依赖图：某些高级权限隐含低级权限
    PERMISSION_HIERARCHY: dict[str, set[str]] = {
        "system": {"read", "write", "execute", "network"},
        "write": {"read"},
        "execute": {"read"},
        "network": {"read"},
    }

    def __init__(
        self,
        allowed_permissions: Optional[Set[str]] = None,
        allowed_paths: Optional[list[str]] = None,
        allowed_network_domains: Optional[List[str]] = None,
    ):
        """
        Args:
            allowed_permissions: 允许的权限集合（默认只允许 'read'）
            allowed_paths: 允许的文件路径前缀列表（留空表示不限制）
            allowed_network_domains: 网络写操作白名单域名（任务 7），
                默认拒绝所有外网写操作（POST/PUT/DELETE/PATCH），读操作放行
        """
        self._allowed_permissions: set[str] = allowed_permissions or {"read"}
        self._allowed_paths: list[str] = allowed_paths or []
        self._allowed_network_domains: List[str] = list(allowed_network_domains or [])

        logger.debug("[Sandbox] 初始化: permissions=%s, paths=%s, network_domains=%s",
                     self._allowed_permissions, self._allowed_paths,
                     self._allowed_network_domains)

    # ── 权限检查 ──

    def check_permission(self, permission: str) -> bool:
        """检查是否拥有指定权限

        Args:
            permission: 权限名称

        Returns:
            True 如果拥有该权限

        Raises:
            PermissionDenied: 如果没有该权限
        """
        # 直接检查
        if permission in self._allowed_permissions:
            return True

        # 层级检查：高级权限隐含低级权限
        for high_perm, implied in self.PERMISSION_HIERARCHY.items():
            if high_perm in self._allowed_permissions and permission in implied:
                return True

        raise PermissionDenied(permission)

    def check_path(self, path: str) -> bool:
        """检查文件路径是否在允许范围内

        【TASK-07 第 4 步第 4 项修复】原实现是 `normalized.startswith(allowed)`:
          · **无分隔符边界** ⇒ 放行 `C:\\work\\a` 即放行 `C:\\work\\ab`；
          · **不解析链接** ⇒ `C:\\work\\link`（→ `C:\\Windows`）被判在 `C:\\work` 内。
        现统一走 `path_within()`（realpath + 8.3 展开 + `commonpath` 边界）。

        Args:
            path: 文件路径

        Returns:
            True 如果路径被允许

        Raises:
            PermissionDenied: 如果路径不在允许范围内
        """
        if not self._allowed_paths:
            return True  # 未设置路径限制，放行

        for allowed in self._allowed_paths:
            if path_within(path, allowed):
                return True

        raise PermissionDenied("path", f"路径不在允许范围内: {path}")

    def check_execute(self, task: str) -> bool:
        """检查是否可以执行任务

        对任务的初步安全检查（骨架实现）。

        Args:
            task: 任务描述

        Returns:
            True 如果允许执行
        """
        # 预留：可在此处添加更复杂的安全检查逻辑
        # 如检测代码注入、敏感操作等
        return True

    def check_tool_call(self, tool_name: str, tool_args: dict) -> bool:
        """检查是否可以调用指定工具

        基于权限声明检查工具调用是否被允许。
        工具名称的后缀命名约定：
        - _read / _get / _list → 需要 'read' 权限
        - _write / _save / _set → 需要 'write' 权限
        - _exec / _run / _execute → 需要 'execute' 权限
        - _network / _fetch / _download → 需要 'network' 权限
        - _system / _config → 需要 'system' 权限

        Args:
            tool_name: 工具名称
            tool_args: 工具参数

        Returns:
            True 如果允许调用

        Raises:
            PermissionDenied: 如果不允许调用
        """
        # 基于工具名称推断所需权限
        name_lower = tool_name.lower()

        required_permission = "read"  # 默认：读取权限

        # 写操作
        if any(kw in name_lower for kw in ("write", "save", "set", "create", "update", "delete", "remove", "upload")):
            required_permission = "write"

        # 执行操作
        if any(kw in name_lower for kw in ("exec", "run", "execute", "shell", "command", "bash", "cmd")):
            required_permission = "execute"

        # 网络操作
        if any(kw in name_lower for kw in ("network", "fetch", "download", "http", "web", "curl", "api_call")):
            required_permission = "network"

        # 系统操作
        if any(kw in name_lower for kw in ("system", "config", "admin", "sudo", "install")):
            required_permission = "system"

        return self.check_permission(required_permission)

    # ── 执行校验（任务 7：进程级执行隔离，无容器依赖） ──

    def _log_intercept(self, stage: str, subject, reason: str, matched_pattern: Optional[str] = None):
        """记录拦截日志（排查误拦截）：具体原因 + 匹配模式 + 调用栈

        Args:
            stage: 拦截阶段（如 cmd_permission / cmd_dangerous / network_write）
            subject: 被拦截对象（命令字符串或 URL）
            reason: 拦截原因（含匹配的具体规则）
            matched_pattern: 命中的正则模式（危险命令/协议等）
        """
        try:
            # 向上回溯调用栈（sys._getframe 逐帧上溯，跳过本 helper），取最近 2 个调用帧
            frames: List[str] = []
            f = sys._getframe(1).f_back
            for _ in range(2):
                if f is None:
                    break
                code = f.f_code
                frames.append(
                    f"{os.path.basename(code.co_filename)}:{f.f_lineno}({code.co_name})"
                )
                f = f.f_back
            caller = " <- ".join(frames) or "unknown"
        except Exception:
            caller = "unknown"
        logger.warning(
            "[Sandbox] 拦截 stage=%s subject=%r reason=%s matched_pattern=%s caller=%s",
            stage, subject, reason, matched_pattern, caller,
        )

    @staticmethod
    def _dangerous_patterns() -> List[tuple]:
        """合并危险命令模式：permission_system 黑名单 + 本模块补充

        permission_system.BLACKLIST 是类级裸编译正则列表（无描述），
        统一转 (pattern, desc) 元组格式后再与 SANDBOX_DANGEROUS_PATTERNS 合并。

        Returns:
            [(编译正则, 拦截说明), ...]
        """
        try:
            from agent.permission_system import PermissionSystem
            base = [(p, "权限系统黑名单规则") for p in PermissionSystem.BLACKLIST]
        except Exception:
            base = []
        return base + list(SANDBOX_DANGEROUS_PATTERNS)

    def validate_command(self, cmd: str) -> CommandVerdict:
        """校验命令是否允许执行（默认拒绝 + 危险命令拦截）

        规则：
        1. 无 execute 权限 → 拒绝（默认拒绝语义，未显式授权即拒绝）
        2. 空/类型非法命令 → 拒绝
        3. 命中危险模式（权限系统黑名单 + 本模块补充）→ 拒绝

        Args:
            cmd: 待执行命令

        Returns:
            CommandVerdict（allowed=False 时 reason/matched_pattern 非空）
        """
        # 1. execute 权限（默认拒绝：未显式授权即拒绝）
        try:
            self.check_permission("execute")
        except PermissionDenied as e:
            verdict = CommandVerdict(False, f"未授权 execute 权限: {e}", "execute_permission")
            self._log_intercept("cmd_permission", cmd, verdict.reason)
            return verdict

        # 2. 空/非法命令
        if cmd is None or not isinstance(cmd, str) or not cmd.strip():
            verdict = CommandVerdict(False, "命令为空或类型非法", "empty_command")
            self._log_intercept("cmd_empty", cmd, verdict.reason)
            return verdict

        # 3. 危险模式匹配（权限系统黑名单 + 本模块补充）
        for pattern, desc in self._dangerous_patterns():
            if pattern.search(cmd):
                verdict = CommandVerdict(False, f"危险命令被拦截: {desc}", pattern.pattern)
                self._log_intercept("cmd_dangerous", cmd, verdict.reason, verdict.matched_pattern)
                return verdict

        return CommandVerdict(True, "命令通过校验")

    def validate_network(self, url: str, method: str) -> CommandVerdict:
        """校验网络访问（默认拒绝外网写操作，白名单域名放行）

        规则：
        1. 仅 http/https 协议（拦截 file:// 等本地访问）
        2. 写方法（POST/PUT/DELETE/PATCH）默认拒绝，白名单域名放行
        3. 读方法（GET/HEAD）放行

        Args:
            url: 目标 URL
            method: HTTP 方法（大写）

        Returns:
            CommandVerdict
        """
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            scheme = (parsed.scheme or "").lower()
            if scheme not in ("http", "https"):
                verdict = CommandVerdict(False, f"非 http(s) 协议被拦截: {scheme or '无协议'}", "scheme")
                self._log_intercept("network_scheme", url, verdict.reason)
                return verdict
            domain = (parsed.hostname or "").lower()
            if not domain:
                verdict = CommandVerdict(False, "URL 缺少主机名", "no_host")
                self._log_intercept("network_no_host", url, verdict.reason)
                return verdict
        except Exception as e:
            verdict = CommandVerdict(False, f"URL 解析失败: {e}", "parse_error")
            self._log_intercept("network_parse_error", url, verdict.reason)
            return verdict

        method_upper = (method or "").upper()
        # 写方法：默认拒绝，白名单域名放行
        if method_upper in ("POST", "PUT", "DELETE", "PATCH"):
            for allowed in self._allowed_network_domains:
                allowed_lower = allowed.lower()
                if domain == allowed_lower or domain.endswith("." + allowed_lower):
                    return CommandVerdict(True, f"白名单域名 {allowed} 放行写操作")
            verdict = CommandVerdict(
                False,
                f"外网写操作默认拒绝: {method_upper} {domain}（白名单: {self._allowed_network_domains or '无'}）",
                "network_write",
            )
            self._log_intercept("network_write", url, verdict.reason)
            return verdict

        # 读方法放行
        if method_upper in ("GET", "HEAD"):
            return CommandVerdict(True, f"读操作放行: {method_upper} {domain}")
        verdict = CommandVerdict(False, f"不支持的 HTTP 方法: {method_upper}", "method")
        self._log_intercept("network_method", url, verdict.reason)
        return verdict

    def run_sandboxed(
        self,
        cmd,
        limits: Optional[SandboxResourceLimits] = None,
        allowed_paths: Optional[List[str]] = None,
        cwd: Optional[str] = None,
        creationflags: int = 0,
    ) -> SandboxRunResult:
        """沙箱执行器：子进程 + 超时 kill + 输出截断（无容器依赖）

        流程：validate_command 校验 → 路径白名单 → Popen → 超时 kill → 输出截断

        Args:
            cmd: 命令（list 参数形式或 str 命令行）
            limits: 资源限制（超时/内存/输出截断），默认 SandboxResourceLimits()
            allowed_paths: 允许的工作目录前缀（None 表示不限制）
            cwd: 子进程工作目录
            creationflags: 透传给 `subprocess.Popen`（Windows 上用于
                `CREATE_NO_WINDOW`，避免工具执行时弹出控制台窗口；缺省 0 =
                与改动前行为一致，D2）

        Returns:
            SandboxRunResult
        """
        limits = limits or SandboxResourceLimits()
        start_time = time.time()

        # 1. 命令校验（危险命令在启动子进程前被拒）
        if isinstance(cmd, str):
            verdict = self.validate_command(cmd)
        else:
            # list 形式：join 后校验
            verdict = self.validate_command(" ".join(cmd) if cmd else "")
        if not verdict.allowed:
            return SandboxRunResult(
                allowed=False, reason=verdict.reason, duration_ms=0.0,
            )

        # 2. 工作目录路径白名单
        # 【TASK-07 修复】原判据是裸 `startswith`（同 `check_path` 的缺陷），
        # 现统一走 `path_within`（解链接 + 分隔符边界）。
        if cwd is not None:
            if allowed_paths:
                if not any(path_within(cwd, p) for p in allowed_paths):
                    return SandboxRunResult(
                        allowed=False,
                        reason=f"工作目录不在允许范围内: {cwd}",
                        duration_ms=0.0,
                    )
            else:
                return SandboxRunResult(
                    allowed=False,
                    reason="未配置允许路径，拒绝指定工作目录执行",
                    duration_ms=0.0,
                )

        # 3. 子进程执行（超时 kill + 输出截断）
        proc = None
        timed_out = False
        try:
            # list 直接传递；str 用 shlex 分词（避免 shell=True 引入注入面）
            if isinstance(cmd, str):
                import shlex
                try:
                    argv = shlex.split(cmd)
                except ValueError:
                    argv = cmd
            else:
                argv = list(cmd)

            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
                creationflags=creationflags,
            )
            try:
                stdout, stderr = proc.communicate(timeout=limits.timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                timed_out = True
                logger.warning(
                    "[Sandbox] run_sandboxed 超时 kill cmd=%r timeout_s=%.1f duration_ms=%.1f",
                    cmd, limits.timeout_s, (time.time() - start_time) * 1000,
                )

            duration_ms = (time.time() - start_time) * 1000
            return SandboxRunResult(
                allowed=True,
                reason="执行超时被杀" if timed_out else "执行完成",
                returncode=proc.returncode,
                stdout=(stdout or "")[:limits.max_output_bytes],
                stderr=(stderr or "")[:limits.max_output_bytes],
                timed_out=timed_out,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.warning(
                "[Sandbox] run_sandboxed 执行失败 cmd=%r error=%s duration_ms=%.1f",
                cmd, e, duration_ms,
            )
            return SandboxRunResult(
                allowed=False,
                reason=f"执行失败: {e}",
                returncode=proc.returncode if proc else None,
                error=str(e),
                duration_ms=duration_ms,
            )

    # ── 适配器预留位 ──

    def get_docker_sandbox(self) -> Optional[object]:
        """获取 Docker 沙箱适配器

        TODO(P6.1): 实现 Docker 容器级隔离
        设计：每个分身在一个独立的 Docker 容器中执行
        """
        logger.warning("[Sandbox] Docker 沙箱尚未实现 — 预留适配位")
        return None

    def get_wasm_sandbox(self) -> Optional[object]:
        """获取 WebAssembly 沙箱适配器

        TODO(P6.1): 实现 WebAssembly 沙箱
        设计：工具调用通过 WASM 运行时隔离执行
        """
        logger.warning("[Sandbox] WASM 沙箱尚未实现 — 预留适配位")
        return None

    # ── 状态查询 ──

    def get_status(self) -> dict:
        """获取沙箱状态"""
        return {
            "allowed_permissions": list(self._allowed_permissions),
            "allowed_paths": list(self._allowed_paths),
            "allowed_network_domains": list(self._allowed_network_domains),
            "docker_available": False,
            "wasm_available": False,
        }

    def __repr__(self) -> str:
        return f"<Sandbox permissions={self._allowed_permissions}>"


# ════════════════════════════════════════════════════════════════════
#  受限会话（TASK-07 第 4 步第 2 项：`sandbox_allowed` 真正被消费）
# ════════════════════════════════════════════════════════════════════
#
# 【为什么要引入"受限会话"这个概念】`data/tool_definitions/*.yaml` 的
# `sandbox_allowed` 字段原本**没有任何运行时消费方**（只有声明/展示：`callability.py`、
# `sync_capability_manifest.py`、`routes_agent_lines.py`）。91 个 YAML 里 59 个为 false。
# 字段的语义（`callability.py:23` 原文）是"是否允许在沙箱（**受限会话/分身沙箱**，
# 默认只读）中执行"——所以要消费它，就必须先有"当前是不是受限会话"这个事实。
#
# 【为什么用 contextvars 而不是环境变量】**环境变量是进程级的**：把进程标成"受限"
# 会让**所有人**（含人机对话主链路）一起受限。`contextvars` 按执行上下文生效，
# 于是同进程内"分身/沙箱会话"与"主对话会话"可以各有各的判定 ——
# 这与 `tool_gate.py::set_session_source` 的理由完全相同（那里也踩过同一个坑）。
# 注意：contextvars **不跨线程继承**，设置点必须在真正执行工具的那个线程内。
_RESTRICTED: contextvars.ContextVar = contextvars.ContextVar(
    "cp_restricted_session", default=None)


@contextmanager
def restricted_session(name: str = "sandbox", *,
                       permissions: Optional[Set[str]] = None,
                       allowed_paths: Optional[List[str]] = None,
                       sandbox: Optional["Sandbox"] = None) -> Iterator["Sandbox"]:
    """进入**受限会话**作用域（分身沙箱 / 只读会话）

    在作用域内：
      · `agent.tool_gate` 会拒绝 `sandbox_allowed: false` 的工具；
      · `current_restriction()` 报告当前受限事实（供工具与审计读取）。

    Yields:
        本作用域生效的 `Sandbox` 实例（缺省用 `get_tool_sandbox()` 的只读实例）。
    """
    active = sandbox or Sandbox(allowed_permissions=permissions or {"read"},
                                allowed_paths=allowed_paths)
    token = _RESTRICTED.set({
        "name": str(name or "sandbox"),
        "sandbox": active,
        "allowed_paths": list(allowed_paths or []),
    })
    try:
        yield active
    finally:
        try:
            _RESTRICTED.reset(token)
        except Exception:  # noqa: BLE001  跨上下文重置失败 ⇒ 显式清空
            _RESTRICTED.set(None)


def current_restriction() -> Optional[Dict]:
    """当前受限会话事实（不在受限会话内 ⇒ None）"""
    return _RESTRICTED.get()


def in_restricted_session() -> bool:
    """当前是否处于受限会话（供 `tool_gate` 与诊断使用）"""
    return _RESTRICTED.get() is not None


def sandbox_allowed_for(tool_name: str) -> bool:
    """工具的 YAML `sandbox_allowed` 声明（**唯一权威**；读不到时按 True 放行）

    【为什么读不到时放行】与 `agent/tools/__init__.py::_internal_tool_names()` 同款
    纪律：元数据加载失败**不得**让工具静默消失（那会让"YAML 读失败"变成一条
    关停能力集的路径）。缺省值取 `True`（与 `ToolMeta.sandbox_allowed` 的默认一致）。
    """
    try:
        from agent.lines.models import load_tool_meta
        meta = (load_tool_meta() or {}).get(str(tool_name or ""))
        if meta is None:
            return True
        return bool(getattr(meta, "sandbox_allowed", True))
    except Exception as exc:  # noqa: BLE001  元数据不可用 ⇒ 不据此拒绝
        logger.debug("[Sandbox] sandbox_allowed 元数据读取失败（按允许处理）: %s", exc)
        return True


def guard_tool_sandbox_allowed(tool_name: str) -> Optional[str]:
    """受限会话内校验工具是否被允许；允许返回 None，否则返回拒绝原因

    【判据的三段】① 不在受限会话内 ⇒ 不参与判定（**零影响**）；
    ② 工具元数据 `sandbox_allowed` 为真 ⇒ 放行；
    ③ 为假 ⇒ 拒绝并给出来源（哪个 YAML）。
    """
    if not in_restricted_session():
        return None
    if sandbox_allowed_for(tool_name):
        return None
    return (f"工具 {tool_name} 声明 sandbox_allowed=false —— 不允许在受限会话"
            f"（沙箱/分身，默认只读）中执行（声明来源：data/tool_definitions/"
            f"{tool_name}.yaml）")


def isolation_level() -> str:
    """本仓**当前**提供的进程隔离级别（诚实标注：**没有**容器/WASM 隔离）

    TASK-07 §4 第 5 项与 E8 要求"不得声称已具备容器级隔离"。`get_docker_sandbox()`
    与 `get_wasm_sandbox()` 都返回 `None`（适配位预留），故这里的返回值恒为
    `"process"` —— 进程级（子进程 + 超时 kill + 输出截断 + 命令校验），
    **不是** container，也**不是** none（我们确实有进程级约束）。
    """
    return ISOLATION_LEVEL


#: 诚实标注的隔离级别（E8：`process` / `none`，本仓为进程级）
ISOLATION_LEVEL = "process"

#: 工具执行路径（`shell_execute` 等）使用的进程级沙箱
_TOOL_SANDBOX_LOCK = threading.RLock()
_TOOL_SANDBOX: Optional["Sandbox"] = None


def get_tool_sandbox() -> "Sandbox":
    """工具执行路径的进程级沙箱（进程内单例；**显式授予 read+execute**）

    【为什么这里要显式给 `execute`】`Sandbox` 的缺省权限是 `{"read"}`（默认拒绝
    语义），而 `validate_command()` 的第一步就是 `check_permission("execute")`
    ⇒ 用缺省实例接 `shell_execute` 会让**所有** shell 命令被拒（那不是安全，是停摆）。
    `shell_execute` 这个能力本身的存在意义就是执行命令，故此处**显式**授予 execute，
    真正的约束由 `validate_command()` 的危险模式判定 + `run_sandboxed()` 的
    子进程/超时/截断/路径白名单承担。
    """
    global _TOOL_SANDBOX
    with _TOOL_SANDBOX_LOCK:
        if _TOOL_SANDBOX is None:
            _TOOL_SANDBOX = Sandbox(allowed_permissions={"read", "execute"})
        return _TOOL_SANDBOX


def reset_tool_sandbox() -> None:
    """重建工具沙箱单例（测试隔离用）"""
    global _TOOL_SANDBOX
    with _TOOL_SANDBOX_LOCK:
        _TOOL_SANDBOX = None


# ════════════════════════════════════════════════════════════════════
#  第三方执行默认隔离（v7.2 §5.9 第 4 条）
# ════════════════════════════════════════════════════════════════════
#
# §5.9 原文：「第三方 MCP Server 默认隔离容器（无宿主网络 / 无 SSH agent /
# 无 $HOME）」。本段把它落成**执行配置默认值**：委派执行器在构造子进程环境时，
# 先取宿主环境，再叠加本段的隔离覆盖，最后叠加临时凭据（credentials.py）。
#
# 不易：三个「无」是默认值，不是可选项——除非显式声明信任（见 IsolationPolicy
#   .trusted），第三方执行一律走隔离默认值。
# 变易：新增隔离维度只需往 ISOLATION_ENV_OVERRIDES / ISOLATION_ENV_BLOCKLIST
#   加条目，apply_isolation_env 与全部调用点零改动。

#: 第三方执行默认**禁掉**的能力（§5.9 三个「无」）
THIRD_PARTY_DENIED_CAPABILITIES: tuple = (
    "host_network",   # 无宿主网络
    "ssh_agent",      # 无 SSH agent
    "home",           # 无 $HOME
)

#: 隔离覆盖：强制写入的环境变量（值刻意为空串——「存在但为空」比「不存在」
#: 更能挡住 `os.environ.get("SSH_AUTH_SOCK", default)` 一类回退默认值）
ISOLATION_ENV_OVERRIDES: dict = {
    # 无 $HOME：清空 HOME/USERPROFILE，并指到沙箱内不可用路径
    "HOME": "",
    "USERPROFILE": "",
    # 无 SSH agent：清空 agent socket 与 ssh 已知主机/配置入口
    "SSH_AUTH_SOCK": "",
    "SSH_AGENT_PID": "",
    "SSH_ASKPASS": "",
    "GIT_SSH_COMMAND": "",
    # 无宿主网络：清空全部代理出口，并显式置零宿主网络开关
    "HTTP_PROXY": "",
    "HTTPS_PROXY": "",
    "ALL_PROXY": "",
    "NO_PROXY": "",
    "http_proxy": "",
    "https_proxy": "",
    "all_proxy": "",
    "CP_SANDBOX_HOST_NETWORK": "0",
    "CP_SANDBOX_SSH_AGENT": "0",
    "CP_SANDBOX_HOME": "0",
}

#: 隔离**删除**的宿主凭据类环境变量（前缀命中即移除）——第三方子进程不该继承
#: 宿主的云凭据；凭据只经 credentials.py 的临时凭据通道注入
ISOLATION_ENV_BLOCKLIST_PREFIXES: tuple = (
    "AWS_", "AZURE_", "GOOGLE_", "GCP_", "KUBECONFIG",
    "DOCKER_", "GH_TOKEN", "GITHUB_TOKEN", "NPM_TOKEN",
    "CP_UI_TOKENS", "OPENAI_", "ANTHROPIC_",
)

#: 隔离策略的生命周期标识（审计载荷引用）
ISOLATION_POLICY_ID = "third_party_mcp_default"


def isolation_env_overrides(*, container_root: str = "") -> dict:
    """构造第三方执行的隔离环境覆盖（**默认值**，非可选）

    Args:
        container_root: 隔离容器内的工作根（非空时用作 HOME 指向，避免 HOME 为空串
            导致部分工具异常退出）。缺省为空 → HOME 显式为 ``""``（「无 $HOME」）。
    """
    env = dict(ISOLATION_ENV_OVERRIDES)
    if container_root:
        env["HOME"] = str(container_root)
        env["USERPROFILE"] = str(container_root)
    return env


def apply_isolation_env(base_env: dict, *,
                        container_root: str = "",
                        trusted: bool = False) -> dict:
    """把隔离默认值叠加到子进程环境上，返回**新字典**（不修改入参）

    顺序（后者覆盖前者）：宿主环境 → 隔离覆盖 → 删除宿主凭据类变量。
    删除放在最后：即便宿主环境里没有对应键，覆盖也已完成。

    Args:
        base_env: 宿主环境（通常 ``dict(os.environ)`` 的拷贝）。
        container_root: 隔离容器工作根（见 ``isolation_env_overrides``）。
        trusted: 显式声明为可信执行（**默认 False**）。True 时不施加隔离，仅用于
            云枢自有执行体；第三方 MCP / 外部 agent CLI 必须保持默认值。

    Returns:
        新的环境字典。
    """
    env = dict(base_env)
    if trusted:
        return env
    env.update(isolation_env_overrides(container_root=container_root))
    for key in list(env.keys()):
        if any(str(key).upper().startswith(p) for p in ISOLATION_ENV_BLOCKLIST_PREFIXES):
            del env[key]
    return env


def isolation_policy_report(*, container_root: str = "",
                            trusted: bool = False) -> dict:
    """隔离策略的机器可读声明（入审计载荷 / 验收报告证据）"""
    return {
        "policy_id": ISOLATION_POLICY_ID,
        "trusted": bool(trusted),
        "denied": list(THIRD_PARTY_DENIED_CAPABILITIES),
        "host_network": bool(trusted),
        "ssh_agent": bool(trusted),
        "home": bool(trusted),
        "container_root": str(container_root or ""),
        "env_overrides": ({} if trusted
                          else isolation_env_overrides(container_root=container_root)),
        "blocked_prefixes": list(ISOLATION_ENV_BLOCKLIST_PREFIXES),
    }
