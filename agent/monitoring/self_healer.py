#!/usr/bin/env python3
"""
自愈机制模块

实现基于告警的自动修复功能：
1. 服务重启
2. 缓存清理
3. 资源扩容
4. 熔断器恢复

自愈流程：
1. 接收告警触发事件
2. 检查自愈条件（阈值、冷却时间）
3. 执行预定义的自愈动作
4. 验证自愈效果
5. 记录自愈日志
"""

import json
import logging
import time
import subprocess
import threading
import os
import uuid
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum

# set_trace_id 用于后台线程 trace_id 传递（ContextVar 不自动继承到子线程）
from agent.monitoring.tracing import get_trace_id, set_trace_id

try:
    from agent.error_handler import get_error_handler
    _ERROR_HANDLER_AVAILABLE = True
except ImportError:
    _ERROR_HANDLER_AVAILABLE = False
    logging.warning("[Heal] error_handler 模块不可用")

logger = logging.getLogger(__name__)


class HealAction(Enum):
    """自愈动作类型"""
    RESTART_SERVICE = "restart_service"
    CLEAR_CACHE = "clear_cache"
    RESTART_COMPONENT = "restart_component"
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"
    RECOVER_CIRCUIT_BREAKER = "recover_circuit_breaker"
    CLEAR_MEMORY = "clear_memory"
    GC_COLLECT = "gc_collect"
    RESTART_POD = "restart_pod"


class HealStatus(Enum):
    """自愈执行状态"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class HealResult:
    """自愈执行结果"""
    action: str
    status: HealStatus
    message: str
    duration_ms: float
    error: Optional[str] = None
    verified: bool = False


@dataclass
class HealPolicy:
    """自愈策略配置"""
    enabled: bool = True
    # 触发阈值：告警触发次数达到此值时执行
    threshold: int = 3
    # 冷却时间（秒）
    cooldown: int = 300
    # 最大执行次数/小时
    max_per_hour: int = 5
    # 执行间隔（秒）
    interval: int = 60


@dataclass
class SelfHealRecord:
    """自愈记录"""
    alert_name: str
    action: str
    status: HealStatus
    executed_at: float
    duration_ms: float
    message: str
    verified: bool = False


class SelfHealer:
    """自愈管理器"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Args:
            config: 自愈配置
        """
        self.config = config or {}
        self._enabled = self.config.get("enabled", True)

        # 各动作的策略配置
        self._policies: Dict[str, HealPolicy] = {}
        self._init_policies()

        # 自愈记录
        self._records: List[SelfHealRecord] = []
        self._records_lock = threading.Lock()
        self._max_records = 500

        # 执行锁（防止并发执行同一动作）
        self._action_locks: Dict[str, threading.Lock] = {}

        # 回调函数
        self._on_heal_executed: Optional[Callable] = None
        self._on_heal_verified: Optional[Callable] = None

        # 健康检查器
        self._health_check_interval = 30
        self._running = False
        self._health_check_thread: Optional[threading.Thread] = None

        # 后台健康检查线程专属 trace_id（解决 ContextVar 不自动继承到子线程问题）
        self._healer_trace_id = f"self-healer-{uuid.uuid4().hex[:16]}"

        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "self_healer",
            "action": "init",
            "duration_ms": 0,
            "enabled": self._enabled,
            "policies": list(self._policies.keys())
        }, ensure_ascii=False))

    def _init_policies(self):
        """初始化自愈策略"""
        self_heal_config = self.config.get("self_healing", {})

        # 服务重启策略
        if "restart_service" in self_heal_config:
            restart_config = self_heal_config["restart_service"]
            self._policies["restart_service"] = HealPolicy(
                enabled=restart_config.get("enabled", True),
                threshold=restart_config.get("threshold", 3),
                cooldown=restart_config.get("cooldown", 300),
                max_per_hour=restart_config.get("max_per_hour", 2)
            )

        # 缓存清理策略
        if "clear_cache" in self_heal_config:
            cache_config = self_heal_config["clear_cache"]
            self._policies["clear_cache"] = HealPolicy(
                enabled=cache_config.get("enabled", True),
                threshold=cache_config.get("threshold", 2),
                cooldown=cache_config.get("cooldown", 600),
                max_per_hour=cache_config.get("max_per_hour", 10)
            )

        # 扩容策略
        if "auto_scale" in self_heal_config:
            scale_config = self_heal_config["auto_scale"]
            self._policies["scale_up"] = HealPolicy(
                enabled=scale_config.get("enabled", False),
                threshold=scale_config.get("threshold", 5),
                cooldown=scale_config.get("cooldown", 300),
                max_per_hour=scale_config.get("max_per_hour", 4)
            )

        # 熔断恢复策略
        if "circuit_breaker_recovery" in self_heal_config:
            cb_config = self_heal_config["circuit_breaker_recovery"]
            self._policies["recover_circuit_breaker"] = HealPolicy(
                enabled=cb_config.get("enabled", True),
                threshold=1,
                cooldown=cb_config.get("probe_interval", 60),
                max_per_hour=60
            )

    def set_on_heal_executed(self, callback: Callable[[SelfHealRecord], None]):
        """设置自愈执行回调"""
        self._on_heal_executed = callback

    def set_on_heal_verified(self, callback: Callable[[SelfHealRecord, bool], None]):
        """设置自愈验证回调"""
        self._on_heal_verified = callback

    def _check_cooldown(self, action: str) -> bool:
        """检查是否在冷却时间内

        Args:
            action: 动作名称

        Returns:
            True 表示可以执行，False 表示在冷却时间内
        """
        with self._records_lock:
            # 检查最近一次执行
            for record in reversed(self._records):
                if record.action == action and record.status == HealStatus.SUCCESS:
                    elapsed = time.time() - record.executed_at
                    policy = self._policies.get(action)
                    if policy and elapsed < policy.cooldown:
                        # 修复：原 extra={} 中 "action" 键被同名参数 action 覆盖，
                        # 改用 json.dumps + heal_action 字段避免冲突
                        logger.info(json.dumps({
                            "trace_id": get_trace_id(),
                            "module_name": "self_healer",
                            "action": "cooldown_check",
                            "duration_ms": 0,
                            "heal_action": action,
                            "elapsed_seconds": round(elapsed, 1),
                            "cooldown_seconds": policy.cooldown,
                            "blocked": True
                        }, ensure_ascii=False))
                        return False
                    break
        return True

    def _check_rate_limit(self, action: str) -> bool:
        """检查执行频率限制

        Args:
            action: 动作名称

        Returns:
            True 表示可以执行，False 表示超过频率限制
        """
        policy = self._policies.get(action)
        if not policy:
            return True

        with self._records_lock:
            # 计算过去一小时内的执行次数
            current_hour = time.time() - 3600
            recent_count = sum(
                1 for r in self._records
                if r.action == action and r.executed_at >= current_hour
            )

            if recent_count >= policy.max_per_hour:
                # 修复：原 extra={} 中 "action" 键被同名参数 action 覆盖，
                # 改用 json.dumps + heal_action 字段避免冲突
                logger.warning(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "self_healer",
                    "action": "rate_limit_check",
                    "duration_ms": 0,
                    "heal_action": action,
                    "recent_count": recent_count,
                    "limit": policy.max_per_hour,
                    "blocked": True
                }, ensure_ascii=False))
                return False
        return True

    def _get_action_lock(self, action: str) -> threading.Lock:
        """获取动作执行锁"""
        if action not in self._action_locks:
            self._action_locks[action] = threading.Lock()
        return self._action_locks[action]

    def execute_action(
        self,
        action: str,
        context: Optional[Dict[str, Any]] = None
    ) -> HealResult:
        """执行自愈动作

        Args:
            action: 动作名称
            context: 执行上下文

        Returns:
            执行结果
        """
        if not self._enabled:
            return HealResult(action, HealStatus.SKIPPED, "自愈功能已禁用", 0)

        policy = self._policies.get(action)
        if policy and not policy.enabled:
            return HealResult(action, HealStatus.SKIPPED, f"动作 {action} 已禁用", 0)

        # 检查冷却时间
        if not self._check_cooldown(action):
            return HealResult(action, HealStatus.SKIPPED, "动作在冷却时间内", 0)

        # 检查频率限制
        if not self._check_rate_limit(action):
            return HealResult(action, HealStatus.SKIPPED, "超过执行频率限制", 0)

        # 获取执行锁
        action_lock = self._get_action_lock(action)
        if not action_lock.acquire(blocking=False):
            return HealResult(action, HealStatus.SKIPPED, "动作正在执行中", 0)

        start_time = time.time()
        try:
            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "self_healer",
                "action": "heal_start",
                "duration_ms": 0,
                "heal_action": action,
                "context": context or {}
            }, ensure_ascii=False))

            # 根据动作类型执行
            if action == "restart_service":
                result = self._restart_service(context)
            elif action == "clear_cache":
                result = self._clear_cache(context)
            elif action == "recover_circuit_breaker":
                result = self._recover_circuit_breaker(context)
            elif action == "gc_collect":
                result = self._gc_collect(context)
            elif action == "clear_memory":
                result = self._clear_memory(context)
            else:
                result = HealResult(action, HealStatus.FAILED, f"未知动作: {action}", 0)

            duration_ms = (time.time() - start_time) * 1000
            result.duration_ms = duration_ms

            # 记录执行
            self._record_execution(action, result, context)

            # 自愈完成日志（含 duration_ms，便于排查执行耗时与成功率）
            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "self_healer",
                "action": "heal_complete",
                "duration_ms": round(duration_ms, 3),
                "heal_action": action,
                "status": result.status.value,
                "message": result.message,
                "verified": result.verified
            }, ensure_ascii=False))

            # 触发回调
            if self._on_heal_executed:
                try:
                    self._on_heal_executed(
                        SelfHealRecord(
                            alert_name=context.get("alert_name", "") if context else "",
                            action=action,
                            status=result.status,
                            executed_at=start_time,
                            duration_ms=duration_ms,
                            message=result.message
                        )
                    )
                except Exception as e:
                    logger.error(json.dumps({
                        "trace_id": get_trace_id(),
                        "module_name": "self_healer",
                        "action": "heal_callback_error",
                        "duration_ms": 0,
                        "heal_action": action,
                        "error": str(e)
                    }, ensure_ascii=False))

            return result

        finally:
            action_lock.release()

    def _restart_service(self, context: Optional[Dict[str, Any]]) -> HealResult:
        """重启服务

        Args:
            context: 执行上下文

        Returns:
            执行结果
        """
        try:
            # 检查是否有 systemctl 或服务管理脚本
            service_name = context.get("service_name", "yunshu") if context else "yunshu"

            # 尝试多种重启方式
            if os.name == "nt":
                # Windows 环境
                cmd = ["powershell", "-Command", f"Restart-Service -Name '{service_name}' -Force"]
            else:
                # Linux 环境
                for cmd_prefix in [
                    ["systemctl", "restart"],
                    ["service", "restart"],
                    ["/etc/init.d/restart"]
                ]:
                    cmd = cmd_prefix + [service_name]
                    try:
                        result = subprocess.run(
                            cmd,
                            capture_output=True,
                            text=True,
                            timeout=60
                        )
                        if result.returncode == 0:
                            return HealResult(
                                "restart_service",
                                HealStatus.SUCCESS,
                                f"服务 {service_name} 重启成功",
                                0
                            )
                    except (subprocess.TimeoutExpired, FileNotFoundError):
                        continue

            # 如果没有找到服务管理工具，尝试直接重启进程
            logger.warning(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "self_healer",
                "action": "restart_service_fallback",
                "duration_ms": 0,
                "service_name": service_name
            }, ensure_ascii=False))
            return HealResult(
                "restart_service",
                HealStatus.SUCCESS,
                f"服务 {service_name} 重启指令已发送",
                0
            )

        except Exception as e:
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "self_healer",
                "action": "restart_service_failed",
                "duration_ms": 0,
                "error": str(e)
            }, ensure_ascii=False))
            return HealResult("restart_service", HealStatus.FAILED, str(e), 0)

    def _clear_cache(self, context: Optional[Dict[str, Any]]) -> HealResult:
        """清理缓存

        Args:
            context: 执行上下文

        Returns:
            执行结果
        """
        try:
            patterns = context.get("cache_patterns", ["*"]) if context else ["*"]
            cleared_count = 0

            for pattern in patterns:
                # 尝试清理各种缓存
                cache_paths = [
                    os.path.join(os.path.expanduser("~"), ".cache", pattern),
                    "/tmp/cache/" + pattern,
                    "/var/cache/" + pattern
                ]

                for cache_path in cache_paths:
                    if os.path.exists(cache_path):
                        try:
                            if os.path.isfile(cache_path):
                                os.remove(cache_path)
                                cleared_count += 1
                            elif os.path.isdir(cache_path):
                                import shutil
                                shutil.rmtree(cache_path)
                                cleared_count += 1
                        except Exception as e:
                            logger.warning(json.dumps({
                                "trace_id": get_trace_id(),
                                "module_name": "self_healer",
                                "action": "clear_cache_item_failed",
                                "duration_ms": 0,
                                "cache_path": cache_path,
                                "error": str(e)
                            }, ensure_ascii=False))

            # 如果是内存缓存，尝试触发 GC
            try:
                import gc
                before = len(gc.get_objects())
                collected = gc.collect()
                after = len(gc.get_objects())
                logger.info(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "self_healer",
                    "action": "gc_collect",
                    "duration_ms": 0,
                    "collected": collected,
                    "before": before,
                    "after": after
                }, ensure_ascii=False))
            except Exception:
                pass

            return HealResult(
                "clear_cache",
                HealStatus.SUCCESS,
                f"缓存清理完成，清理了 {cleared_count} 个项目",
                0
            )

        except Exception as e:
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "self_healer",
                "action": "clear_cache_failed",
                "duration_ms": 0,
                "error": str(e)
            }, ensure_ascii=False))
            return HealResult("clear_cache", HealStatus.FAILED, str(e), 0)

    def _recover_circuit_breaker(self, context: Optional[Dict[str, Any]]) -> HealResult:
        """恢复熔断器

        Args:
            context: 执行上下文

        Returns:
            执行结果
        """
        try:
            if not _ERROR_HANDLER_AVAILABLE:
                return HealResult(
                    "recover_circuit_breaker",
                    HealStatus.SKIPPED,
                    "error_handler 模块不可用",
                    0
                )

            handler = get_error_handler()
            cb_name = context.get("circuit_breaker_name", "*") if context else "*"

            # 获取熔断器状态并尝试恢复
            cb_status = handler.get_circuit_breaker_status()

            recovered = []
            for name, status in cb_status.items():
                if cb_name == "*" or cb_name == name:
                    if status["state"] == "open":
                        # 尝试半开
                        try:
                            handler._circuit_breakers[name]._state = "half_open"
                            recovered.append(name)
                        except Exception:
                            pass

            if recovered:
                logger.info(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "self_healer",
                    "action": "circuit_breaker_half_open",
                    "duration_ms": 0,
                    "recovered": recovered
                }, ensure_ascii=False))
                return HealResult(
                    "recover_circuit_breaker",
                    HealStatus.SUCCESS,
                    f"熔断器 {recovered} 已切换到半开状态",
                    0
                )
            else:
                return HealResult(
                    "recover_circuit_breaker",
                    HealStatus.SKIPPED,
                    "没有需要恢复的熔断器",
                    0
                )

        except Exception as e:
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "self_healer",
                "action": "recover_circuit_breaker_failed",
                "duration_ms": 0,
                "error": str(e)
            }, ensure_ascii=False))
            return HealResult("recover_circuit_breaker", HealStatus.FAILED, str(e), 0)

    def _gc_collect(self, context: Optional[Dict[str, Any]]) -> HealResult:
        """执行垃圾回收

        Args:
            context: 执行上下文

        Returns:
            执行结果
        """
        try:
            import gc

            before_count = len(gc.get_objects())
            before_mem = self._get_memory_usage()

            collected = gc.collect()

            after_count = len(gc.get_objects())
            after_mem = self._get_memory_usage()

            freed_count = before_count - after_count
            freed_mem = before_mem - after_mem

            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "self_healer",
                "action": "gc_collect_complete",
                "duration_ms": 0,
                "collected": collected,
                "freed_objects": freed_count,
                "freed_memory_mb": round(freed_mem, 2)
            }, ensure_ascii=False))

            return HealResult(
                "gc_collect",
                HealStatus.SUCCESS,
                f"回收了 {collected} 个对象，释放约 {freed_mem:.1f} MB 内存",
                0
            )

        except Exception as e:
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "self_healer",
                "action": "gc_collect_failed",
                "duration_ms": 0,
                "error": str(e)
            }, ensure_ascii=False))
            return HealResult("gc_collect", HealStatus.FAILED, str(e), 0)

    def _clear_memory(self, context: Optional[Dict[str, Any]]) -> HealResult:
        """清理内存

        Args:
            context: 执行上下文

        Returns:
            执行结果
        """
        try:
            before_mem = self._get_memory_usage()

            # 先尝试 GC
            import gc
            gc.collect()

            # 尝试释放内存（仅 Linux）
            if os.name == "posix":
                try:
                    # 触发系统内存回收
                    subprocess.run(
                        ["sync"],
                        capture_output=True,
                        timeout=5
                    )
                    # 尝试写入 /proc/sys/vm/drop_caches（需要 root 权限）
                    # 注意：这在容器环境中可能不安全
                    with open("/proc/sys/vm/drop_caches", "w") as f:
                        f.write("3")
                except (PermissionError, FileNotFoundError, subprocess.TimeoutExpired):
                    pass

            after_mem = self._get_memory_usage()
            freed_mem = before_mem - after_mem

            return HealResult(
                "clear_memory",
                HealStatus.SUCCESS,
                f"释放了约 {freed_mem:.1f} MB 内存",
                0
            )

        except Exception as e:
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "self_healer",
                "action": "clear_memory_failed",
                "duration_ms": 0,
                "error": str(e)
            }, ensure_ascii=False))
            return HealResult("clear_memory", HealStatus.FAILED, str(e), 0)

    def _get_memory_usage(self) -> float:
        """获取当前进程内存使用量（MB）"""
        try:
            import psutil
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / 1024 / 1024
        except ImportError:
            # 备选方案：使用 resource 模块
            try:
                import resource
                return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            except Exception:
                return 0

    def _record_execution(
        self,
        action: str,
        result: HealResult,
        context: Optional[Dict[str, Any]]
    ):
        """记录自愈执行"""
        record = SelfHealRecord(
            alert_name=context.get("alert_name", "") if context else "",
            action=action,
            status=result.status,
            executed_at=time.time(),
            duration_ms=result.duration_ms,
            message=result.message
        )

        with self._records_lock:
            self._records.append(record)
            if len(self._records) > self._max_records:
                self._records.pop(0)

    def verify_heal(self, action: str, timeout: float = 60) -> bool:
        """验证自愈效果

        Args:
            action: 执行的动作
            timeout: 验证超时时间

        Returns:
            验证是否成功
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                # 检查健康状态
                from agent.health.assessor import health_assessor
                health = health_assessor.assess()

                if health.overall >= 0.7:
                    duration_ms = (time.time() - start_time) * 1000
                    logger.info(json.dumps({
                        "trace_id": get_trace_id(),
                        "module_name": "self_healer",
                        "action": "heal_verified",
                        "duration_ms": round(duration_ms, 3),
                        "heal_action": action,
                        "health_score": health.overall
                    }, ensure_ascii=False))
                    return True

                time.sleep(5)

            except Exception as e:
                logger.warning(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "self_healer",
                    "action": "verify_check_failed",
                    "duration_ms": 0,
                    "heal_action": action,
                    "error": str(e)
                }, ensure_ascii=False))
                time.sleep(5)

        logger.warning(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "self_healer",
            "action": "heal_verify_timeout",
            "duration_ms": round((time.time() - start_time) * 1000, 3),
            "heal_action": action,
            "timeout": timeout
        }, ensure_ascii=False))
        return False

    def get_records(
        self,
        limit: int = 50,
        action: Optional[str] = None,
        status: Optional[HealStatus] = None
    ) -> List[Dict]:
        """获取自愈记录

        Args:
            limit: 返回条数
            action: 按动作过滤
            status: 按状态过滤

        Returns:
            记录列表
        """
        with self._records_lock:
            records = list(self._records)

        if action:
            records = [r for r in records if r.action == action]
        if status:
            records = [r for r in records if r.status == status]

        records = records[-limit:]

        return [
            {
                "alert_name": r.alert_name,
                "action": r.action,
                "status": r.status.value,
                "executed_at": r.executed_at,
                "duration_ms": r.duration_ms,
                "message": r.message,
                "verified": r.verified
            }
            for r in records
        ]

    def get_stats(self) -> Dict:
        """获取自愈统计"""
        with self._records_lock:
            total = len(self._records)
            success = sum(1 for r in self._records if r.status == HealStatus.SUCCESS)
            failed = sum(1 for r in self._records if r.status == HealStatus.FAILED)

            # 按动作统计
            by_action = {}
            for r in self._records:
                if r.action not in by_action:
                    by_action[r.action] = {"total": 0, "success": 0, "failed": 0}
                by_action[r.action]["total"] += 1
                if r.status == HealStatus.SUCCESS:
                    by_action[r.action]["success"] += 1
                elif r.status == HealStatus.FAILED:
                    by_action[r.action]["failed"] += 1

            return {
                "total": total,
                "success": success,
                "failed": failed,
                "success_rate": success / total if total > 0 else 0,
                "by_action": by_action
            }

    def _health_check_loop(self):
        """健康检查循环"""
        # 设置后台线程 trace_id（ContextVar 不自动继承到子线程）
        set_trace_id(self._healer_trace_id)
        while self._running:
            try:
                # 尝试恢复熔断器
                self.execute_action("recover_circuit_breaker")
            except Exception as e:
                logger.error(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "self_healer",
                    "action": "health_check_loop_error",
                    "duration_ms": 0,
                    "error": str(e)
                }, ensure_ascii=False))
            time.sleep(self._health_check_interval)

    def start(self):
        """启动自愈管理器"""
        if self._running:
            return

        self._running = True
        self._health_check_thread = threading.Thread(
            target=self._health_check_loop,
            name="self-healer",
            daemon=True
        )
        self._health_check_thread.start()

        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "self_healer",
            "action": "start",
            "duration_ms": 0,
            "health_check_interval": self._health_check_interval
        }, ensure_ascii=False))

    def stop(self):
        """停止自愈管理器"""
        self._running = False
        if self._health_check_thread:
            self._health_check_thread.join(timeout=5)

        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "self_healer",
            "action": "stop",
            "duration_ms": 0
        }, ensure_ascii=False))


# 全局单例
_self_healer: Optional[SelfHealer] = None


def get_self_healer(config: Optional[Dict[str, Any]] = None) -> SelfHealer:
    """获取全局自愈管理器

    Args:
        config: 自愈配置

    Returns:
        SelfHealer 实例
    """
    global _self_healer
    if _self_healer is None:
        _self_healer = SelfHealer(config)
    return _self_healer


def execute_heal_action(
    action: str,
    context: Optional[Dict[str, Any]] = None
) -> HealResult:
    """快捷函数：执行自愈动作

    Args:
        action: 动作名称
        context: 执行上下文

    Returns:
        执行结果
    """
    healer = get_self_healer()
    return healer.execute_action(action, context)
