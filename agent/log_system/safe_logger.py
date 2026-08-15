"""安全日志模块

敏感信息脱敏、审计日志、安全监控等安全相关的日志功能。
"""
import os
import sys
import re
import json
import uuid
import logging
import threading
from datetime import datetime
from typing import Optional, Callable, Any, Dict, Pattern, List

from agent.utils.sensitive_data_filter import (
    SensitiveDataFilter as _UnifiedSensitiveDataFilter,
)

logger = logging.getLogger(__name__)

# SingletonManager 统一收口（保留 fallback 变量向后兼容）
# 注：safe_logger 的审计/安全日志格式与 logging_utils 不同（module_name/action/字段名），
# 独立注册唯一命名单例，避免共享实例改变日志语义。
try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = None

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class SensitiveDataFilter(_UnifiedSensitiveDataFilter):
    """
    日志敏感信息自动脱敏过滤器（向后兼容层）
    
    核心功能已迁移至 agent.utils.sensitive_data_filter。
    本类提供与原有 API 完全兼容的接口。
    """
    
    def __init__(self):
        super().__init__()
    
    def _sanitize(self, text: str) -> str:
        """脱敏文本中的敏感信息（向后兼容别名）"""
        return self.mask(text)
    
    def _sanitize_dict(self, data: Dict) -> Dict:
        """递归脱敏字典中的敏感信息（向后兼容别名）"""
        return self.filter_dict(data)


# ─────────────────────────────────────────────────
# 审计日志
# ─────────────────────────────────────────────────



class AuditLogger:
    """
    权限操作审计日志记录器
    
    记录所有安全相关操作，包括：
    - 配置访问/修改
    - 权限变更
    - 敏感信息访问
    """
    
    def __init__(self):
        self._logger = logging.getLogger("agent.audit")
        self._logger.setLevel(logging.INFO)
        
        # 确保审计日志有独立处理器（输出到单独文件）
        # Why: FileHandler 不创建父目录，CI 全新 checkout 无 logs/ 目录时抛
        # FileNotFoundError（2026-08-10 实测 Shard 1 test_audit_safety_logging_singleton
        # 失败）。对齐 logging_utils.AuditLogger 的 makedirs 模式（agent/logging_utils.py）。
        if not self._logger.handlers:
            log_path = os.path.join(os.path.dirname(__file__), '..', '..', 'logs', 'audit.log')
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            handler = logging.FileHandler(log_path, encoding='utf-8')
            handler.setFormatter(logging.Formatter(
                '%(asctime)s [%(levelname)s] %(message)s',
                '%Y-%m-%d %H:%M:%S'
            ))
            self._logger.addHandler(handler)
            self._logger.propagate = False
    
    def log_config_access(self, config_key: str, user: str = "system"):
        """
        记录配置访问
        
        Args:
            config_key: 访问的配置键
            user: 访问用户（默认为系统）
        """
        self._logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "safe_logger", "action": "config_access.user.user", "msg": f"CONFIG_ACCESS | user={user} | key={config_key}"}, ensure_ascii=False))
    
    def log_config_modification(self, config_key: str, user: str = "system"):
        """
        记录配置修改
        
        Args:
            config_key: 修改的配置键
            user: 修改用户（默认为系统）
        """
        self._logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "safe_logger", "action": "config_modify.user.user", "msg": f"CONFIG_MODIFY | user={user} | key={config_key}"}, ensure_ascii=False))
    
    def log_secure_config_access(self, config_key: str, success: bool, user: str = "system"):
        """
        记录安全配置访问
        
        Args:
            config_key: 访问的安全配置键
            success: 是否成功
            user: 访问用户（默认为系统）
        """
        status = "SUCCESS" if success else "FAILED"
        self._logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "safe_logger", "action": "secure_config_access.user.user", "msg": f"SECURE_CONFIG_ACCESS | user={user} | key={config_key} | status={status}"}, ensure_ascii=False))
    
    def log_encryption_key_access(self, success: bool, user: str = "system"):
        """
        记录加密密钥访问
        
        Args:
            success: 是否成功
            user: 访问用户（默认为系统）
        """
        status = "SUCCESS" if success else "FAILED"
        self._logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "safe_logger", "action": "encryption_key_access.user.user", "msg": f"ENCRYPTION_KEY_ACCESS | user={user} | status={status}"}, ensure_ascii=False))
    
    def log_permission_change(self, action: str, resource: str, user: str = "system"):
        """
        记录权限变更
        
        Args:
            action: 操作类型（grant/revoke/modify）
            resource: 资源名称
            user: 操作用户（默认为系统）
        """
        self._logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "safe_logger", "action": "permission_change.user.user", "msg": f"PERMISSION_CHANGE | user={user} | action={action} | resource={resource}"}, ensure_ascii=False))
    
    def log_authentication(self, username: str, success: bool, ip_address: str = None):
        """
        记录认证尝试
        
        Args:
            username: 用户名
            success: 是否成功
            ip_address: 客户端IP地址（可选）
        """
        status = "SUCCESS" if success else "FAILED"
        ip_info = f" | ip={ip_address}" if ip_address else ""
        self._logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "safe_logger", "action": "authentication.username.username", "msg": f"AUTHENTICATION | username={username} | status={status}{ip_info}"}, ensure_ascii=False))
    
    def log_sensitive_operation(self, operation: str, details: dict = None, user: str = "system"):
        """
        记录敏感操作
        
        Args:
            operation: 操作类型
            details: 操作详情（将被脱敏）
            user: 操作用户（默认为系统）
        """
        details_str = ""
        if details:
            sanitizer = SensitiveDataFilter()
            sanitized_details = sanitizer._sanitize_dict(details)
            details_str = f" | details={json.dumps(sanitized_details, ensure_ascii=False)}"
        
        self._logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "safe_logger", "action": "sensitive_operation.user.user", "msg": f"SENSITIVE_OPERATION | user={user} | operation={operation}{details_str}"}, ensure_ascii=False))




# 全局审计日志实例
_audit_logger: Optional[AuditLogger] = None  # 保留作为 fallback
# [2026-08-13 并发审计] fallback 单例双检锁：AuditLogger 含 FileHandler，
# 双实例会泄漏文件句柄
_audit_logger_lock = threading.Lock()


def _create_audit_logger(config=None):
    """AuditLogger 工厂（供 SingletonManager 使用）"""
    return AuditLogger()


def get_audit_logger() -> AuditLogger:
    """获取全局审计日志记录器（单例）"""
    if _SINGLETON_AVAILABLE:
        return get_singleton("safe_logger_audit_logger")
    global _audit_logger
    if _audit_logger is None:
        # [2026-08-13 并发审计] fallback 双检锁：防并发首调创建多个
        # AuditLogger（含 FileHandler，双实例泄漏句柄）
        with _audit_logger_lock:
            if _audit_logger is None:
                _audit_logger = _create_audit_logger()
    return _audit_logger


def reset_audit_logger():
    """重置全局审计日志单例（仅用于测试）"""
    global _audit_logger
    if _SINGLETON_AVAILABLE:
        reset_singleton("safe_logger_audit_logger")
    _audit_logger = None



class AgentTimeoutException(Exception):
    """Agent 操作超时异常"""
    pass


class AgentLoopException(Exception):
    """Agent 循环检测异常"""
    pass


class AgentStateStuckException(Exception):
    """Agent 状态卡死异常"""
    pass




class AgentSafetyMonitor:
    """
    Agent 安全监控器

    防止死循环、状态卡死等异常情况
    """

    def __init__(
        self,
        max_iterations_per_minute: int = 100,
        state_stuck_threshold_seconds: int = 10,
    ):
        """
        初始化安全监控器

        Args:
            max_iterations_per_minute: 每分钟最大迭代次数
            state_stuck_threshold_seconds: 状态卡死阈值（秒）
        """
        self._lock = threading.Lock()
        self._iteration_count = {}
        self._last_state = {}
        self._state_change_time = {}

        self.max_iterations_per_minute = max_iterations_per_minute
        self.state_stuck_threshold = state_stuck_threshold_seconds

        self.logger = logging.getLogger("agent.safety")
        self.logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "safe_logger", "action": "log", "msg": "安全监控器已初始化"}, ensure_ascii=False))

    def record_iteration(self, identifier: str) -> bool:
        """
        记录一次迭代，检查是否异常

        Args:
            identifier: 任务标识符

        Returns:
            是否正常（未检测到异常）
        """
        fast_loop_count = None
        with self._lock:
            current_time = datetime.now()

            if identifier not in self._iteration_count:
                self._iteration_count[identifier] = {
                    'total': 0,
                    'window_start': current_time,
                    'window_count': 0,
                }

            record = self._iteration_count[identifier]
            time_diff = (current_time - record['window_start']).total_seconds()

            # 每分钟重置窗口计数器
            if time_diff >= 60:
                record['window_start'] = current_time
                record['window_count'] = 0
            else:
                record['window_count'] += 1

                # 检测快速循环（锁内仅记录计数，logger 移出锁外）
                if record['window_count'] > self.max_iterations_per_minute:
                    fast_loop_count = record['window_count']

            if fast_loop_count is None:
                record['total'] += 1

        # [2026-08-13 并发审计] logger 移出锁外：日志写盘不受锁内竞争影响
        if fast_loop_count is not None:
            self.logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "safe_logger", "action": "identifier", "msg": f"⚠️ 检测到快速循环: {identifier}, "
                f"1分钟内迭代 {fast_loop_count} 次"}, ensure_ascii=False))
            return False
        return True

    def check_state(self, identifier: str, state: str) -> bool:
        """
        检查状态变化，检测是否卡死

        Args:
            identifier: 任务标识符
            state: 当前状态

        Returns:
            是否正常（未检测到卡死）
        """
        stuck_info = None
        with self._lock:
            current_time = datetime.now()

            if identifier not in self._last_state:
                self._last_state[identifier] = state
                self._state_change_time[identifier] = current_time
                return True

            old_state = self._last_state[identifier]

            if old_state == state:
                # 状态未变化，检查卡死时间
                stuck_time = (
                    current_time - self._state_change_time[identifier]
                ).total_seconds()

                if stuck_time > self.state_stuck_threshold:
                    stuck_info = (identifier, state, stuck_time)
            else:
                # 状态变化了，更新记录
                self._last_state[identifier] = state
                self._state_change_time[identifier] = current_time

        # [2026-08-13 并发审计] logger 移出锁外
        if stuck_info is not None:
            identifier, state, stuck_time = stuck_info
            self.logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "safe_logger", "action": "identifier", "msg": f"⚠️ 检测到状态卡死: {identifier}, "
                f"状态 '{state}' 保持 {stuck_time:.1f} 秒"}, ensure_ascii=False))
            return False
        return True

    def reset(self, identifier: str = None):
        """重置监控数据"""
        with self._lock:
            if identifier:
                self._iteration_count.pop(identifier, None)
                self._last_state.pop(identifier, None)
                self._state_change_time.pop(identifier, None)
            else:
                self._iteration_count.clear()
                self._last_state.clear()
                self._state_change_time.clear()

    def get_stats(self) -> dict:
        """获取监控统计"""
        with self._lock:
            return {
                'tracked_identifiers': len(self._iteration_count),
                'max_iterations_per_minute': self.max_iterations_per_minute,
                'state_stuck_threshold': self.state_stuck_threshold,
            }




# 全局安全监控器实例
_safety_monitor: Optional[AgentSafetyMonitor] = None  # 保留作为 fallback


def _create_safety_monitor(config=None):
    """AgentSafetyMonitor 工厂（供 SingletonManager 使用）"""
    return AgentSafetyMonitor()


def get_safety_monitor() -> AgentSafetyMonitor:
    """获取全局安全监控器（单例）"""
    if _SINGLETON_AVAILABLE:
        return get_singleton("safe_logger_safety_monitor")
    global _safety_monitor
    if _safety_monitor is None:
        _safety_monitor = _create_safety_monitor()
    return _safety_monitor


def reset_safety_monitor():
    """重置全局安全监控器单例（仅用于测试）"""
    global _safety_monitor
    if _SINGLETON_AVAILABLE:
        reset_singleton("safe_logger_safety_monitor")
    _safety_monitor = None



def safe_execute(
    func: Callable,
    timeout: float = 30.0,
    default_return: Any = None,
    identifier: str = None,
) -> Any:
    """
    带超时保护的函数执行包装器

    Args:
        func: 要执行的函数
        timeout: 超时时间（秒）
        default_return: 超时时的默认返回值
        identifier: 任务标识符（用于监控）

    Returns:
        函数返回值或默认值

    Example:
        >>> def my_task():
        ...     return "完成"
        >>> result = safe_execute(my_task, timeout=10)
        >>> print(result)
        完成
    """
    logger = logging.getLogger("agent.safety.safe_execute")

    # 检查安全监控
    monitor = get_safety_monitor()
    task_id = identifier or f"task_{datetime.now().timestamp()}"

    if not monitor.record_iteration(task_id):
        logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "safe_logger", "action": "task_id", "msg": f"⚠️ 安全监控拒绝执行: {task_id}"}, ensure_ascii=False))
        return default_return

    # 使用线程执行，实现超时保护
    result_container = {'value': None, 'exception': None}

    def target():
        try:
            result_container['value'] = func()
        except Exception as e:
            result_container['exception'] = e
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "safe_logger", "action": "log", "msg": f"执行异常: {e}"}, ensure_ascii=False))

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "safe_logger", "action": "timeout.task_id", "msg": f"⏱️ 执行超时（{timeout}秒）: {task_id}"}, ensure_ascii=False))
        return default_return

    if result_container['exception']:
        raise result_container['exception']

    return result_container['value']



def safe_execute_async(
    func: Callable,
    timeout: float = 30.0,
    identifier: str = None,
) -> tuple[Any, Optional[Exception]]:
    """
    带超时保护的异步函数执行（返回异常）

    Args:
        func: 要执行的异步函数
        timeout: 超时时间（秒）
        identifier: 任务标识符

    Returns:
        (结果, 异常对象如果有)

    Example:
        >>> async def my_task():
        ...     return "完成"
        >>> result, error = safe_execute_async(my_task, timeout=10)
        >>> if error:
        ...     print(f"错误: {error}")
        >>> else:
        ...     print(f"结果: {result}")
    """
    import asyncio
    logger = logging.getLogger("agent.safety.safe_execute_async")

    task_id = identifier or f"async_{datetime.now().timestamp()}"

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    result_container = {'value': None, 'exception': None}

    async def async_target():
        try:
            result_container['value'] = await func()
        except Exception as e:
            result_container['exception'] = e

    future = asyncio.ensure_future(async_target())

    try:
        loop.run_until_complete(asyncio.wait_for(future, timeout=timeout))
    except asyncio.TimeoutError:
        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "safe_logger", "action": "timeout.task_id", "msg": f"⏱️ 异步执行超时（{timeout}秒）: {task_id}"}, ensure_ascii=False))
        future.cancel()
        return None, AgentTimeoutException(f"执行超时（{timeout}秒）")
    except Exception as e:
        logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "safe_logger", "action": "log", "msg": f"异步执行异常: {e}"}, ensure_ascii=False))
        return None, e

    if result_container['exception']:
        return None, result_container['exception']

    return result_container['value'], None


# 注册单例工厂（置于文件末尾，确保类已定义）
# 唯一命名：safe_logger 的日志格式与 logging_utils 不同，独立注册避免冲突
if _SINGLETON_AVAILABLE:
    register_singleton("safe_logger_audit_logger", _create_audit_logger)
    register_singleton("safe_logger_safety_monitor", _create_safety_monitor)

