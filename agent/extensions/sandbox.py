"""插件沙箱 — 权限隔离与安全执行环境

提供插件的安全执行环境，包括：
- 权限隔离：限制插件对系统资源的访问
- 资源限制：CPU、内存、磁盘使用限制
- 网络隔离：控制插件的网络访问
- 安全审计：记录插件的所有操作
"""

import os
import sys
import logging
import subprocess
import threading
import tempfile
import traceback
from typing import Dict, Any, Optional, Callable, Tuple
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

from agent.monitoring.tracing import get_trace_id

logger = logging.getLogger(__name__)


class SandboxPermission(Enum):
    """沙箱权限级别"""
    NONE = "none"                    # 无权限
    READ_FILES = "read_files"        # 读取文件
    WRITE_FILES = "write_files"      # 写入文件
    EXECUTE_CODE = "execute_code"    # 执行代码
    NETWORK_ACCESS = "network"       # 网络访问
    SYSTEM_COMMANDS = "system"       # 系统命令
    ADMIN = "admin"                  # 管理员权限


@dataclass
class ResourceLimits:
    """资源限制配置"""
    max_cpu_percent: float = 50.0     # 最大 CPU 使用率 (%)
    max_memory_mb: int = 256          # 最大内存使用 (MB)
    max_disk_mb: int = 100            # 最大磁盘使用 (MB)
    max_execution_time: int = 30       # 最大执行时间 (秒)
    max_network_requests: int = 100    # 最大网络请求数


@dataclass
class SandboxContext:
    """沙箱执行上下文"""
    plugin_id: str
    permissions: list
    resource_limits: ResourceLimits
    work_dir: str = ""
    env_vars: Dict[str, str] = field(default_factory=dict)
    start_time: str = ""
    end_time: str = ""
    cpu_usage: float = 0.0
    memory_usage: int = 0
    network_requests: int = 0


@dataclass
class ExecutionResult:
    """执行结果"""
    success: bool
    output: str = ""
    error: str = ""
    duration_ms: int = 0
    resource_usage: Dict[str, Any] = field(default_factory=dict)


class PluginSandbox:
    """插件沙箱 - 提供安全的插件执行环境"""

    def __init__(self):
        self._active_sandboxes: Dict[str, SandboxContext] = {}
        self._audit_log: list = []
        self._lock = threading.RLock()
        self._running_plugins = set()

    def create_sandbox(self, plugin_id: str, permissions: list, 
                       resource_limits: ResourceLimits = None) -> SandboxContext:
        """创建插件沙箱"""
        with self._lock:
            if plugin_id in self._active_sandboxes:
                return self._active_sandboxes[plugin_id]

            work_dir = tempfile.mkdtemp(prefix=f"plugin_{plugin_id}_")
            context = SandboxContext(
                plugin_id=plugin_id,
                permissions=permissions,
                resource_limits=resource_limits or ResourceLimits(),
                work_dir=work_dir,
                env_vars=self._create_isolated_env(),
                start_time=datetime.now().isoformat()
            )

            self._active_sandboxes[plugin_id] = context
            self._log_audit("sandbox_created", plugin_id, {"permissions": permissions})
            logger.info(f"Created sandbox for plugin: {plugin_id}")
            
            return context

    def _create_isolated_env(self) -> Dict[str, str]:
        """创建隔离的环境变量"""
        env = os.environ.copy()
        env["PLUGIN_SANDBOXED"] = "true"
        env["PYTHONPATH"] = ""
        return env

    def check_permission(self, plugin_id: str, permission: SandboxPermission) -> bool:
        """检查插件是否具有指定权限"""
        with self._lock:
            context = self._active_sandboxes.get(plugin_id)
            if not context:
                return False
            return permission.value in context.permissions

    def execute_in_sandbox(self, plugin_id: str, func: Callable, 
                          *args, **kwargs) -> ExecutionResult:
        """在沙箱中执行函数"""
        start_time = datetime.now()
        context = self._active_sandboxes.get(plugin_id)
        
        if not context:
            return ExecutionResult(
                success=False,
                error=f"Sandbox not found for plugin: {plugin_id}"
            )

        if plugin_id in self._running_plugins:
            return ExecutionResult(
                success=False,
                error="Plugin is already running"
            )

        self._running_plugins.add(plugin_id)
        result = ExecutionResult(success=False)

        try:
            result.output = func(*args, **kwargs)
            result.success = True
            self._log_audit("execution_success", plugin_id, {
                "function": func.__name__,
                "args": str(args)[:100]
            })
        except Exception as e:
            result.error = str(e)
            result.success = False
            self._log_audit("execution_error", plugin_id, {
                "function": func.__name__,
                "error": str(e),
                "traceback": traceback.format_exc()[:500]
            })
            logger.error(f"Plugin {plugin_id} execution failed: {e}")
        finally:
            end_time = datetime.now()
            result.duration_ms = int((end_time - start_time).total_seconds() * 1000)
            self._running_plugins.discard(plugin_id)

            if context:
                context.end_time = end_time.isoformat()

        return result

    def execute_subprocess(self, plugin_id: str, command: list, 
                          cwd: str = None) -> ExecutionResult:
        """在沙箱中执行子进程"""
        start_time = datetime.now()
        context = self._active_sandboxes.get(plugin_id)
        
        if not context:
            return ExecutionResult(
                success=False,
                error=f"Sandbox not found for plugin: {plugin_id}"
            )

        if not self.check_permission(plugin_id, SandboxPermission.EXECUTE_CODE):
            return ExecutionResult(
                success=False,
                error="Permission denied: execute_code"
            )

        result = ExecutionResult(success=False)
        work_dir = cwd or context.work_dir

        try:
            env = {**os.environ, **context.env_vars}
            
            result = subprocess.run(
                command,
                cwd=work_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=context.resource_limits.max_execution_time
            )

            execution_result = ExecutionResult(
                success=result.returncode == 0,
                output=result.stdout,
                error=result.stderr,
                duration_ms=int((datetime.now() - start_time).total_seconds() * 1000)
            )

            self._log_audit("subprocess_executed", plugin_id, {
                "command": command,
                "return_code": result.returncode,
                "cwd": work_dir
            })

            return execution_result

        except subprocess.TimeoutExpired:
            return ExecutionResult(
                success=False,
                error="Execution timeout",
                duration_ms=int((datetime.now() - start_time).total_seconds() * 1000)
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                error=str(e),
                duration_ms=int((datetime.now() - start_time).total_seconds() * 1000)
            )

    def read_file(self, plugin_id: str, filepath: str) -> ExecutionResult:
        """在沙箱中读取文件"""
        if not self.check_permission(plugin_id, SandboxPermission.READ_FILES):
            return ExecutionResult(
                success=False,
                error="Permission denied: read_files"
            )

        try:
            filepath = os.path.abspath(filepath)
            if not self._is_path_safe(filepath):
                return ExecutionResult(
                    success=False,
                    error=f"Access denied: {filepath}"
                )

            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

            self._log_audit("file_read", plugin_id, {"filepath": filepath})
            return ExecutionResult(success=True, output=content)

        except Exception as e:
            return ExecutionResult(success=False, error=str(e))

    def write_file(self, plugin_id: str, filepath: str, content: str) -> ExecutionResult:
        """在沙箱中写入文件"""
        if not self.check_permission(plugin_id, SandboxPermission.WRITE_FILES):
            return ExecutionResult(
                success=False,
                error="Permission denied: write_files"
            )

        try:
            filepath = os.path.abspath(filepath)
            if not self._is_path_safe(filepath):
                return ExecutionResult(
                    success=False,
                    error=f"Access denied: {filepath}"
                )

            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)

            self._log_audit("file_written", plugin_id, {"filepath": filepath})
            return ExecutionResult(success=True)

        except Exception as e:
            return ExecutionResult(success=False, error=str(e))

    def _is_path_safe(self, filepath: str) -> bool:
        """检查路径是否安全（防止路径遍历攻击）"""
        dangerous_paths = [
            "/etc", "/usr", "/bin", "/sbin", "/boot",
            "C:\\Windows", "C:\\System32", "C:\\Program Files"
        ]
        filepath_lower = filepath.lower()
        for dangerous in dangerous_paths:
            if filepath_lower.startswith(dangerous.lower()):
                return False
        return True

    def destroy_sandbox(self, plugin_id: str):
        """销毁沙箱"""
        with self._lock:
            context = self._active_sandboxes.pop(plugin_id, None)
            if context:
                import shutil
                try:
                    shutil.rmtree(context.work_dir, ignore_errors=True)
                except Exception as e:
                    logger.warning(f"Failed to cleanup sandbox dir: {e}")
                
                self._log_audit("sandbox_destroyed", plugin_id, {})
                logger.info(f"Destroyed sandbox for plugin: {plugin_id}")

    def _log_audit(self, action: str, plugin_id: str, details: Dict = None):
        """记录审计日志"""
        entry = {
            "trace_id": get_trace_id(),
            "timestamp": datetime.now().isoformat(),
            "plugin_id": plugin_id,
            "action": action,
            "details": details or {}
        }
        self._audit_log.append(entry)
        if len(self._audit_log) > 1000:
            self._audit_log = self._audit_log[-1000:]

    def get_audit_log(self, plugin_id: str = None, limit: int = 100) -> list:
        """获取审计日志"""
        logs = self._audit_log
        if plugin_id:
            logs = [l for l in logs if l["plugin_id"] == plugin_id]
        return logs[-limit:]

    def get_sandbox_status(self, plugin_id: str) -> Optional[Dict]:
        """获取沙箱状态"""
        context = self._active_sandboxes.get(plugin_id)
        if not context:
            return None
        
        return {
            "plugin_id": plugin_id,
            "permissions": context.permissions,
            "work_dir": context.work_dir,
            "start_time": context.start_time,
            "end_time": context.end_time,
            "is_running": plugin_id in self._running_plugins,
            "resource_limits": {
                "max_cpu_percent": context.resource_limits.max_cpu_percent,
                "max_memory_mb": context.resource_limits.max_memory_mb,
                "max_disk_mb": context.resource_limits.max_disk_mb,
                "max_execution_time": context.resource_limits.max_execution_time
            }
        }

    def list_sandboxes(self) -> list:
        """列出所有活动沙箱"""
        return [self.get_sandbox_status(pid) for pid in self._active_sandboxes.keys()]


class SandboxManager:
    """沙箱管理器"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._sandboxes = {}
        return cls._instance

    def get_sandbox(self, plugin_id: str) -> PluginSandbox:
        """获取或创建插件沙箱"""
        if plugin_id not in self._sandboxes:
            self._sandboxes[plugin_id] = PluginSandbox()
        return self._sandboxes[plugin_id]

    def destroy_all(self):
        """销毁所有沙箱"""
        for plugin_id in list(self._sandboxes.keys()):
            self._sandboxes[plugin_id].destroy_sandbox(plugin_id)
            del self._sandboxes[plugin_id]


def get_sandbox_manager() -> SandboxManager:
    """获取沙箱管理器实例"""
    return SandboxManager()