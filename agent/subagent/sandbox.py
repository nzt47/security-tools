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
from typing import Optional, Set

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
    ):
        """
        Args:
            allowed_permissions: 允许的权限集合（默认只允许 'read'）
            allowed_paths: 允许的文件路径前缀列表（留空表示不限制）
        """
        self._allowed_permissions: set[str] = allowed_permissions or {"read"}
        self._allowed_paths: list[str] = allowed_paths or []

        logger.debug("[Sandbox] 初始化: permissions=%s, paths=%s",
                     self._allowed_permissions, self._allowed_paths)

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
            "docker_available": False,
            "wasm_available": False,
        }

    def __repr__(self) -> str:
        return f"<Sandbox permissions={self._allowed_permissions}>"
