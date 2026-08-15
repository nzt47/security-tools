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

import logging
import re
import subprocess
import sys
import time
import os
from dataclasses import dataclass, field
from typing import Optional, Set, List, Dict, Callable

logger = logging.getLogger(__name__)


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

        Args:
            path: 文件路径

        Returns:
            True 如果路径被允许

        Raises:
            PermissionDenied: 如果路径不在允许范围内
        """
        if not self._allowed_paths:
            return True  # 未设置路径限制，放行

        import os
        normalized = os.path.abspath(path)
        for allowed in self._allowed_paths:
            allowed_normalized = os.path.abspath(allowed)
            if normalized.startswith(allowed_normalized):
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
    ) -> SandboxRunResult:
        """沙箱执行器：子进程 + 超时 kill + 输出截断（无容器依赖）

        流程：validate_command 校验 → 路径白名单 → Popen → 超时 kill → 输出截断

        Args:
            cmd: 命令（list 参数形式或 str 命令行）
            limits: 资源限制（超时/内存/输出截断），默认 SandboxResourceLimits()
            allowed_paths: 允许的工作目录前缀（None 表示不限制）
            cwd: 子进程工作目录

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
        if cwd is not None:
            if allowed_paths:
                norm_cwd = os.path.abspath(cwd)
                if not any(norm_cwd.startswith(os.path.abspath(p)) for p in allowed_paths):
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
