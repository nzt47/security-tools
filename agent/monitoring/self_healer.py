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
import traceback
import os
import socket
import importlib
import uuid
from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum

# set_trace_id 用于后台线程 trace_id 传递（ContextVar 不自动继承到子线程）
from agent.monitoring.tracing import get_trace_id, set_trace_id
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

# SingletonManager 统一收口（保留 fallback 变量 _self_healer 向后兼容）
try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton, is_initialized,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = is_initialized = None


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

        # 配置化超时（支持热加载，每次初始化时读取最新值）
        try:
            from agent.monitoring.observability_config import (
                get_self_healer_restart_timeout,
                get_self_healer_sync_timeout,
                get_self_healer_verify_timeout,
                get_self_healer_thread_join_timeout,
            )
            self._restart_timeout = get_self_healer_restart_timeout()
            self._sync_timeout = get_self_healer_sync_timeout()
            self._verify_timeout = get_self_healer_verify_timeout()
            self._thread_join_timeout = get_self_healer_thread_join_timeout()
        except Exception:
            self._restart_timeout = 60
            self._sync_timeout = 5
            self._verify_timeout = 60
            self._thread_join_timeout = 5

        # 危险动作保护（守安全红线）：
        # - _cache_whitelist: 允许清理的缓存路径白名单，空列表 = 禁用通配清理
        # - _allow_drop_caches: Linux /proc/sys/vm/drop_caches 写入默认禁用
        heal_config = self.config.get("self_healing", {})
        self._cache_whitelist = list(
            heal_config.get("clear_cache", {}).get("cache_whitelist", [])
        )
        self._allow_drop_caches = bool(
            heal_config.get("clear_memory", {}).get("allow_drop_caches", False)
        )
        # 验证阈值：真实健康分低于此值视为验证失败（默认 0.7）
        self._verify_score_threshold = float(
            self.config.get("verify_score_threshold", 0.7)
        )
        # 内存类动作验证阈值（默认 1MB）
        self._verify_mem_delta_mb = float(
            self.config.get("verify_mem_delta_mb", 1.0)
        )

        # 验证状态：execute_action 记录上下文与基线，verify_action 读取
        self._last_context: Dict[str, Dict[str, Any]] = {}
        self._verify_state: Dict[str, Dict[str, Any]] = {}

        logger.info(log_dict({'module_name': 'self_healer', 'action': 'init', 'enabled': self._enabled, 'policies': list(self._policies.keys()), 'cache_whitelist': self._cache_whitelist}))

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
                        logger.info(log_dict({'module_name': 'self_healer', 'action': 'cooldown_check', 'heal_action': action, 'elapsed_seconds': round(elapsed, 1), 'cooldown_seconds': policy.cooldown, 'remaining_seconds': round(policy.cooldown - elapsed, 1), 'blocked': True}))
                        return False
                    # 冷却已过：记录通过，便于确认未被冷却拦截
                    logger.info(log_dict({'module_name': 'self_healer', 'action': 'cooldown_check', 'heal_action': action, 'elapsed_seconds': round(elapsed, 1), 'cooldown_seconds': policy.cooldown if policy else None, 'blocked': False}))
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
                logger.warning(log_dict({'module_name': 'self_healer', 'action': 'rate_limit_check', 'heal_action': action, 'recent_count': recent_count, 'limit': policy.max_per_hour, 'blocked': True}))
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
            policy = self._policies.get(action)
            logger.warning(log_dict({'module_name': 'self_healer', 'action': 'heal_skipped', 'heal_action': action, 'reason': '动作在冷却时间内', 'cooldown_seconds': policy.cooldown if policy else None}))
            return HealResult(action, HealStatus.SKIPPED, "动作在冷却时间内", 0)

        # 检查频率限制
        if not self._check_rate_limit(action):
            policy = self._policies.get(action)
            logger.warning(log_dict({'module_name': 'self_healer', 'action': 'heal_skipped', 'heal_action': action, 'reason': '超过执行频率限制', 'max_per_hour': policy.max_per_hour if policy else None}))
            return HealResult(action, HealStatus.SKIPPED, "超过执行频率限制", 0)

        # 获取执行锁
        action_lock = self._get_action_lock(action)
        if not action_lock.acquire(blocking=False):
            return HealResult(action, HealStatus.SKIPPED, "动作正在执行中", 0)

        start_time = time.time()
        # [2026-08-13 并发审计] 回调记录锁内构建、锁外触发：用户回调可能阻塞，
        # 若在 action 锁内执行会卡死其他线程的自愈请求
        callback_record = None
        try:
            logger.info(log_dict({'module_name': 'self_healer', 'action': 'heal_start', 'heal_action': action, 'context': context or {}}))

            # 记录本次上下文与验证基线（verify_action 后续读取）
            self._last_context[action] = context or {}
            self._verify_state.setdefault(action, {})

            # 根据动作类型执行
            if action == "restart_service":
                result = self._restart_service(context)
            elif action == "restart_component":
                result = self._restart_component(context)
            elif action == "clear_cache":
                result = self._clear_cache(context)
            elif action == "recover_circuit_breaker":
                result = self._recover_circuit_breaker(context)
            elif action == "gc_collect":
                result = self._gc_collect(context)
            elif action == "clear_memory":
                result = self._clear_memory(context)
            else:
                # 未实现动作 → SKIPPED（原因明确），未知动作 → FAILED
                from agent.self_healing.policy import get_action_status
                if get_action_status(action) == "unimplemented":
                    result = HealResult(action, HealStatus.SKIPPED, f"动作 {action} 未实现", 0)
                else:
                    result = HealResult(action, HealStatus.FAILED, f"未知动作: {action}", 0)

            duration_ms = (time.time() - start_time) * 1000
            result.duration_ms = duration_ms

            # 记录执行
            self._record_execution(action, result, context)

            # 自愈完成日志（含 duration_ms，便于排查执行耗时与成功率）
            logger.info(log_dict({'module_name': 'self_healer', 'action': 'heal_complete', 'heal_action': action, 'status': result.status.value, 'message': result.message, 'verified': result.verified}))

            # 锁内仅构建回调记录
            if self._on_heal_executed:
                callback_record = SelfHealRecord(
                    alert_name=context.get("alert_name", "") if context else "",
                    action=action,
                    status=result.status,
                    executed_at=start_time,
                    duration_ms=duration_ms,
                    message=result.message
                )

        finally:
            action_lock.release()

        # 锁外触发回调（回调内可能做外部 I/O/通知，不能持锁）
        if callback_record is not None and self._on_heal_executed:
            try:
                self._on_heal_executed(callback_record)
            except Exception as e:
                logger.error(log_dict({'module_name': 'self_healer', 'action': 'heal_callback_error', 'heal_action': action, 'error': str(e)}))

        return result

    def _restart_service(self, context: Optional[Dict[str, Any]]) -> HealResult:
        """重启服务

        【不易】修复 D9：不再"假成功"——
        - Windows 下未提供 restart_command 且无可用服务管理工具 → SKIPPED("未提供可执行的重启方式")
        - 执行后必须经动作验证器确认（端口可连接），验证失败 → FAILED 并携带证据
        - Linux 保持 systemctl/service 探测，但同样以验证结果为准

        Args:
            context: 执行上下文（service_name / restart_command / ports）

        Returns:
            执行结果
        """
        try:
            ctx = context or {}
            service_name = ctx.get("service_name", "yunshu")
            restart_command = ctx.get("restart_command")
            ports = ctx.get("ports", [])

            # 记录验证基线（verify_action 读取：端口列表）
            self._verify_state["restart_service"] = {
                "service_name": service_name,
                "ports": list(ports),
            }

            executed = False
            if restart_command:
                # 显式重启命令（跨平台；字符串按 shell 执行，列表按 argv 执行）
                try:
                    logger.info(log_dict({'module_name': 'self_healer', 'action': 'restart_service.run_command', 'heal_action': 'restart_service', 'service_name': service_name, 'restart_command': restart_command if isinstance(restart_command, str) else list(restart_command), 'timeout_seconds': self._restart_timeout}))
                    result = subprocess.run(
                        restart_command,
                        shell=isinstance(restart_command, str),
                        capture_output=True,
                        text=True,
                        timeout=self._restart_timeout
                    )
                    if result.returncode == 0:
                        executed = True
                        logger.info(log_dict({'module_name': 'self_healer', 'action': 'restart_service.command_ok', 'heal_action': 'restart_service', 'service_name': service_name, 'returncode': result.returncode}))
                    else:
                        evidence = (result.stderr or result.stdout or "").strip()
                        logger.warning(log_dict({'module_name': 'self_healer', 'action': 'restart_service.command_failed', 'heal_action': 'restart_service', 'service_name': service_name, 'returncode': result.returncode, 'evidence': evidence[:500]}))
                        return HealResult(
                            "restart_service",
                            HealStatus.FAILED,
                            f"重启命令执行失败，退出码 {result.returncode}: {evidence}",
                            0
                        )
                except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                    logger.warning(log_dict({'module_name': 'self_healer', 'action': 'restart_service.command_error', 'heal_action': 'restart_service', 'service_name': service_name, 'error': str(e)}))
                    return HealResult(
                        "restart_service",
                        HealStatus.FAILED,
                        f"重启命令执行异常: {e}",
                        0
                    )
            elif os.name == "nt":
                # Windows：无重启方式（此前直接 Restart-Service 'yunshu' 大概率无此服务）
                logger.warning(log_dict({'module_name': 'self_healer', 'action': 'restart_service.no_command_windows', 'heal_action': 'restart_service', 'service_name': service_name, 'reason': '未提供 restart_command，Windows 下无可用服务管理工具'}))
                return HealResult(
                    "restart_service",
                    HealStatus.SKIPPED,
                    "未提供可执行的重启方式(restart_command)",
                    0
                )
            else:
                # Linux：探测 systemctl / service
                for cmd_prefix in (["systemctl", "restart"], ["service", "restart"]):
                    try:
                        result = subprocess.run(
                            cmd_prefix + [service_name],
                            capture_output=True,
                            text=True,
                            timeout=self._restart_timeout
                        )
                        if result.returncode == 0:
                            executed = True
                            logger.info(log_dict({'module_name': 'self_healer', 'action': 'restart_service.linux_ok', 'heal_action': 'restart_service', 'service_name': service_name, 'command': cmd_prefix}))
                            break
                        logger.info(log_dict({'module_name': 'self_healer', 'action': 'restart_service.linux_probe_failed', 'heal_action': 'restart_service', 'service_name': service_name, 'command': cmd_prefix, 'returncode': result.returncode}))
                    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                        logger.info(log_dict({'module_name': 'self_healer', 'action': 'restart_service.linux_probe_error', 'heal_action': 'restart_service', 'service_name': service_name, 'command': cmd_prefix, 'error': str(e)}))
                        continue
                if not executed:
                    logger.warning(log_dict({'module_name': 'self_healer', 'action': 'restart_service.no_tool_found', 'heal_action': 'restart_service', 'service_name': service_name, 'reason': '未找到可用的服务管理工具'}))
                    return HealResult(
                        "restart_service",
                        HealStatus.SKIPPED,
                        "未找到可用的服务管理工具",
                        0
                    )

            # 执行后必须验证（单次检查；长时间轮询由 verify_heal 承担）
            verified, reason = self.verify_action("restart_service")
            if verified:
                logger.info(log_dict({'module_name': 'self_healer', 'action': 'restart_service.verified', 'heal_action': 'restart_service', 'service_name': service_name, 'reason': reason, 'verified': True}))
                return HealResult(
                    "restart_service",
                    HealStatus.SUCCESS,
                    f"服务 {service_name} 重启成功并验证通过",
                    0,
                    verified=True
                )
            logger.warning(log_dict({
                'module_name': 'self_healer',
                'action': 'restart_service.unverified',
                'heal_action': 'restart_service',
                'service_name': service_name,
                'reason': reason,
                'verified': False,
                # 定位信息：失败验证器名 + 验证依据（端口/服务名）+ 当前调用栈摘要
                'verifier': 'verify_action(restart_service) -> _verify_restart_service',
                'verify_state': self._verify_state.get("restart_service"),
                'call_stack': " <- ".join(f"{f.name}@{f.lineno}" for f in traceback.extract_stack(limit=6)[:-1]),
            }))
            return HealResult(
                "restart_service",
                HealStatus.FAILED,
                f"服务 {service_name} 重启后验证失败: {reason}",
                0
            )

        except Exception as e:
            logger.error(log_dict({'module_name': 'self_healer', 'action': 'restart_service_failed', 'error': str(e)}))
            return HealResult("restart_service", HealStatus.FAILED, str(e), 0)

    def _restart_component(self, context: Optional[Dict[str, Any]]) -> HealResult:
        """进程内模块热重启（补全 D9 的 restart_component 动作）

        context 需提供 target_module（模块名）；未提供 → SKIPPED。
        使用 importlib.reload 实现进程内热重载，验证模块可重新导入。

        Args:
            context: 执行上下文（target_module）

        Returns:
            执行结果
        """
        ctx = context or {}
        module_name = ctx.get("target_module")
        if not module_name:
            return HealResult(
                "restart_component",
                HealStatus.SKIPPED,
                "未提供目标模块(target_module)",
                0
            )
        try:
            module = importlib.import_module(module_name)
            importlib.reload(module)
            # 验证基线：模块已可导入（verify_action 复查导入成功）
            self._verify_state["restart_component"] = {"module_name": module_name}
            return HealResult(
                "restart_component",
                HealStatus.SUCCESS,
                f"组件 {module_name} 热重载完成",
                0,
                verified=True
            )
        except Exception as e:
            logger.error(log_dict({'module_name': 'self_healer', 'action': 'restart_component_failed', 'target_module': module_name, 'error': str(e)}))
            return HealResult("restart_component", HealStatus.FAILED, str(e), 0)

    def _clear_cache(self, context: Optional[Dict[str, Any]]) -> HealResult:
        """清理缓存

        【不易】守安全红线（危险动作保护）——
        - 路径必须在 _cache_whitelist 白名单内，越权路径 → SKIPPED("路径不在白名单")
        - pattern 为 "*" → SKIPPED("禁止全量清理")
        - 白名单为空 → SKIPPED（通配清理默认禁用，不清理 ~/.cache 等越权路径）

        Args:
            context: 执行上下文（cache_patterns / cache_paths）

        Returns:
            执行结果
        """
        try:
            ctx = context or {}
            patterns = ctx.get("cache_patterns", [])
            paths = ctx.get("cache_paths", [])
            whitelist = self._cache_whitelist

            if not whitelist:
                logger.warning(log_dict({'module_name': 'self_healer', 'action': 'clear_cache.whitelist_empty', 'heal_action': 'clear_cache', 'patterns': patterns, 'paths': paths, 'reason': '白名单为空，通配清理默认禁用'}))
                return HealResult(
                    "clear_cache",
                    HealStatus.SKIPPED,
                    "缓存白名单为空，通配清理已禁用",
                    0
                )

            # 禁止通配全清
            if any(p == "*" for p in patterns):
                logger.warning(log_dict({'module_name': 'self_healer', 'action': 'clear_cache.wildcard_rejected', 'heal_action': 'clear_cache', 'patterns': patterns, 'reason': "禁止全量清理(pattern='*')"}))
                return HealResult(
                    "clear_cache",
                    HealStatus.SKIPPED,
                    "禁止全量清理(pattern='*')",
                    0
                )

            # 解析目标路径并校验必须在白名单内（防 ../ 逃逸）
            targets = list(paths)
            for pattern in patterns:
                for base in whitelist:
                    targets.append(os.path.join(base, pattern))
            for target in targets:
                norm = os.path.abspath(os.path.expanduser(target))
                if not self._in_cache_whitelist(target):
                    logger.warning(log_dict({'module_name': 'self_healer', 'action': 'clear_cache.out_of_whitelist', 'heal_action': 'clear_cache', 'path': target, 'normalized_path': norm, 'whitelist': whitelist, 'reason': '路径不在白名单'}))
                    return HealResult(
                        "clear_cache",
                        HealStatus.SKIPPED,
                        f"路径 {target} 不在白名单",
                        0
                    )
                logger.info(log_dict({'module_name': 'self_healer', 'action': 'clear_cache.whitelist_ok', 'heal_action': 'clear_cache', 'path': target, 'normalized_path': norm}))

            # 记录清理前总大小（verify_action 验证缓存目录下降）
            total_before = 0
            for target in targets:
                if os.path.exists(target):
                    total_before += self._dir_size(target)
            self._verify_state["clear_cache"] = {
                "cache_dirs": list(targets),
                "total_before_bytes": total_before,
            }
            logger.info(log_dict({'module_name': 'self_healer', 'action': 'clear_cache.start', 'heal_action': 'clear_cache', 'targets': targets, 'total_before_bytes': total_before}))

            cleared_count = 0
            for cache_path in targets:
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
                        logger.warning(log_dict({'module_name': 'self_healer', 'action': 'clear_cache_item_failed', 'cache_path': cache_path, 'error': str(e)}))

            total_after = sum(self._dir_size(d) for d in targets if os.path.exists(d))
            logger.info(log_dict({'module_name': 'self_healer', 'action': 'clear_cache.complete', 'heal_action': 'clear_cache', 'cleared_count': cleared_count, 'total_before_bytes': total_before, 'total_after_bytes': total_after, 'freed_bytes': total_before - total_after}))

            return HealResult(
                "clear_cache",
                HealStatus.SUCCESS,
                f"缓存清理完成，清理了 {cleared_count} 个项目",
                0
            )

        except Exception as e:
            logger.error(log_dict({'module_name': 'self_healer', 'action': 'clear_cache_failed', 'error': str(e)}))
            return HealResult("clear_cache", HealStatus.FAILED, str(e), 0)

    def _in_cache_whitelist(self, path: str) -> bool:
        """校验路径是否落在缓存白名单内（规范化后判断，防 ../ 逃逸）"""
        norm = os.path.abspath(os.path.expanduser(path))
        for base in self._cache_whitelist:
            base_norm = os.path.abspath(os.path.expanduser(base))
            if norm == base_norm or norm.startswith(base_norm + os.sep):
                return True
        return False

    def _dir_size(self, path: str) -> int:
        """统计文件/目录总字节数"""
        try:
            if os.path.isfile(path):
                return os.path.getsize(path)
            total = 0
            for root, _dirs, files in os.walk(path):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        continue
            return total
        except OSError:
            return 0

    def _recover_circuit_breaker(self, context: Optional[Dict[str, Any]]) -> HealResult:
        """恢复熔断器

        【不易】修复 D11：禁止直改私有字段（._state =），一律走公开 API——
        - 通过 agent.circuit_breaker.get_all_circuit_breaker_status() 定位 OPEN 熔断器
        - 调用 get_circuit_breaker(name).force_close() 恢复
        - 记录恢复前/后状态到日志与自愈记录

        Args:
            context: 执行上下文（circuit_breaker_name，默认 "*" 全量）

        Returns:
            执行结果
        """
        try:
            from agent.circuit_breaker import (
                get_all_circuit_breaker_status,
                get_circuit_breaker,
            )

            cb_name = context.get("circuit_breaker_name", "*") if context else "*"
            cb_status = get_all_circuit_breaker_status()
            logger.info(log_dict({'module_name': 'self_healer', 'action': 'recover_circuit_breaker.scan', 'heal_action': 'recover_circuit_breaker', 'target': cb_name, 'breaker_count': len(cb_status), 'status_snapshot': {k: v.get("state") for k, v in cb_status.items()}}))

            recovered = []
            for name, status in cb_status.items():
                if cb_name != "*" and cb_name != name:
                    continue
                if status.get("state") == "open":
                    before_state = status.get("state")
                    breaker = get_circuit_breaker(name)
                    breaker.force_close()
                    after_status = breaker.get_status()
                    recovered.append(name)
                    logger.info(log_dict({
                        'module_name': 'self_healer', 'action': 'circuit_breaker_recovered',
                        'breaker_name': name, 'before_state': before_state,
                        'after_state': after_status.get("state"),
                    }))
                else:
                    logger.info(log_dict({'module_name': 'self_healer', 'action': 'circuit_breaker_skip', 'breaker_name': name, 'state': status.get("state"), 'reason': '非 OPEN，无需恢复'}))

            if recovered:
                return HealResult(
                    "recover_circuit_breaker",
                    HealStatus.SUCCESS,
                    f"熔断器 {recovered} 已通过公开 API 恢复为关闭状态",
                    0,
                    verified=True
                )
            logger.warning(log_dict({'module_name': 'self_healer', 'action': 'recover_circuit_breaker.no_open', 'heal_action': 'recover_circuit_breaker', 'target': cb_name, 'reason': '没有 OPEN 状态的熔断器需要恢复'}))
            return HealResult(
                "recover_circuit_breaker",
                HealStatus.SKIPPED,
                "没有需要恢复的熔断器",
                0
            )

        except Exception as e:
            logger.error(log_dict({'module_name': 'self_healer', 'action': 'recover_circuit_breaker_failed', 'error': str(e)}))
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

            # 记录验证基线（verify_action 验证 RSS/对象数下降）
            self._verify_state["gc_collect"] = {
                "mem_mb_before": before_mem,
                "objects_before": before_count,
            }

            logger.info(log_dict({'module_name': 'self_healer', 'action': 'gc_collect_complete', 'collected': collected, 'freed_objects': freed_count, 'freed_memory_mb': round(freed_mem, 2)}))

            return HealResult(
                "gc_collect",
                HealStatus.SUCCESS,
                f"回收了 {collected} 个对象，释放约 {freed_mem:.1f} MB 内存",
                0
            )

        except Exception as e:
            logger.error(log_dict({'module_name': 'self_healer', 'action': 'gc_collect_failed', 'error': str(e)}))
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
                    subprocess.run(
                        ["sync"],
                        capture_output=True,
                        timeout=self._sync_timeout
                    )
                    # /proc/sys/vm/drop_caches 写入默认禁用（配置 allow_drop_caches=True 才允许）
                    # 【不易】守安全红线：容器/共享主机上清空页缓存可能影响其他进程
                    if self._allow_drop_caches:
                        with open("/proc/sys/vm/drop_caches", "w") as f:
                            f.write("3")
                except (PermissionError, FileNotFoundError, subprocess.TimeoutExpired):
                    pass

            after_mem = self._get_memory_usage()
            freed_mem = before_mem - after_mem

            # 记录验证基线（verify_action 验证 RSS 下降）
            self._verify_state["clear_memory"] = {
                "mem_mb_before": before_mem,
            }

            return HealResult(
                "clear_memory",
                HealStatus.SUCCESS,
                f"释放了约 {freed_mem:.1f} MB 内存",
                0
            )

        except Exception as e:
            logger.error(log_dict({'module_name': 'self_healer', 'action': 'clear_memory_failed', 'error': str(e)}))
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

    def verify_heal(self, action: str, timeout: Optional[float] = None) -> bool:
        """验证自愈效果

        【不易】修复 D7：不再依赖无参 assess()（返回默认满分 1.0），改为——
        1. 动作专属验证器（verify_action）：进程/端口/缓存大小/熔断状态/内存
        2. 真实健康分：agent.health.assessor 的最新评分（get_history(1)），
           评分为 None（无数据禁假满分）或低于阈值（默认 0.7）视为验证失败

        Args:
            action: 执行的动作
            timeout: 验证超时时间（None 时从 Config 读取，支持热加载）

        Returns:
            验证是否成功
        """
        # 配置化超时（支持热加载，None 时从 Config 读取）
        if timeout is None:
            timeout = self._verify_timeout
        start_time = time.time()
        last_reason = "未知原因"

        while time.time() - start_time < timeout:
            try:
                # 1. 动作专属验证器（读 execute_action 记录的上下文与基线）
                ok, reason = self.verify_action(action)
                logger.info(log_dict({'module_name': 'self_healer', 'action': 'verify_action.result', 'heal_action': action, 'ok': ok, 'reason': reason, 'elapsed_ms': round((time.time() - start_time) * 1000, 1)}))
                if not ok:
                    last_reason = reason
                    time.sleep(5)
                    continue

                # 2. 真实健康分（任务 1 产物；无参 assess 的假满分不可用）
                from agent.health.assessor import health_assessor
                history = health_assessor.get_history(1)
                if not history or history[0].overall is None:
                    last_reason = "无真实健康评分数据(health_assessor.get_history 为空或评分为 None)"
                    logger.warning(log_dict({'module_name': 'self_healer', 'action': 'verify_health.no_data', 'heal_action': action, 'history_count': len(history) if history else 0}))
                    time.sleep(5)
                    continue
                health = history[0]
                logger.info(log_dict({'module_name': 'self_healer', 'action': 'verify_health.read', 'heal_action': action, 'history_count': len(history), 'overall': health.overall, 'threshold': self._verify_score_threshold}))
                if health.overall >= self._verify_score_threshold:
                    duration_ms = (time.time() - start_time) * 1000
                    logger.info(log_dict({'module_name': 'self_healer', 'action': 'heal_verified', 'heal_action': action, 'health_score': health.overall, 'duration_ms': round(duration_ms, 1)}))
                    return True
                last_reason = f"健康分 {health.overall:.2f} 低于验证阈值 {self._verify_score_threshold}"
                time.sleep(5)

            except Exception as e:
                last_reason = str(e)
                logger.warning(log_dict({'module_name': 'self_healer', 'action': 'verify_check_failed', 'heal_action': action, 'error': str(e)}))
                time.sleep(5)

        logger.warning(log_dict({'module_name': 'self_healer', 'action': 'heal_verify_timeout', 'heal_action': action, 'timeout': timeout, 'reason': last_reason}))
        return False

    def verify_action(self, action: str) -> Tuple[bool, str]:
        """动作专属验证器分发

        【不易】按动作类型定义验证指标，杜绝"假成功"：
        - restart_service：目标端口可连接（端口列表从 execute_action context 传入）
        - restart_component：目标模块可重新导入
        - clear_cache：缓存目录总大小下降
        - recover_circuit_breaker：目标熔断器状态非 OPEN（读公开 get_status()）
        - gc_collect / clear_memory：RSS 下降 > 阈值（默认 1MB）或对象数下降
        - 其余动作无专属验证器 → 仅依赖健康分（返回 True，原因注明）

        Args:
            action: 动作名称

        Returns:
            (是否通过, 原因)
        """
        logger.info(log_dict({'module_name': 'self_healer', 'action': 'verify_action.dispatch', 'heal_action': action}))
        if action == "restart_service":
            return self._verify_restart_service()
        if action == "restart_component":
            return self._verify_restart_component()
        if action == "clear_cache":
            return self._verify_clear_cache()
        if action == "recover_circuit_breaker":
            return self._verify_circuit_breaker()
        if action in ("gc_collect", "clear_memory"):
            return self._verify_memory(action)
        return (True, "该动作无专属验证器，仅依赖健康分")

    def _verify_restart_service(self) -> Tuple[bool, str]:
        """重启服务验证：端口可连接（首选）或 PID 变更"""
        state = self._verify_state.get("restart_service", {})
        ports = state.get("ports", [])
        service_name = state.get("service_name", "yunshu")

        if ports:
            # per-port 探测结果：便于定位具体哪个端口未恢复
            port_results = {p: self._check_port_open(p) for p in ports}
            failed_ports = [p for p, ok in port_results.items() if not ok]
            logger.info(log_dict({'module_name': 'self_healer', 'action': 'restart_service.verify_ports', 'heal_action': 'restart_service', 'service_name': service_name, 'ports': list(ports), 'port_results': port_results, 'failed_ports': failed_ports}))
            if not failed_ports:
                return (True, f"端口 {ports} 已恢复可连接")
            return (False, f"端口 {failed_ports} 仍不可连接")
        logger.warning(log_dict({'module_name': 'self_healer', 'action': 'restart_service.verify_no_basis', 'heal_action': 'restart_service', 'service_name': service_name, 'reason': '未提供验证依据(ports)，无法确认重启生效', 'verify_state': self._verify_state.get("restart_service")}))
        return (False, f"服务 {service_name} 未提供验证依据(ports)，无法确认重启生效")

    def _verify_restart_component(self) -> Tuple[bool, str]:
        """组件热重启验证：目标模块可重新导入"""
        state = self._verify_state.get("restart_component", {})
        module_name = state.get("module_name")
        if not module_name:
            return (False, "未记录目标模块，无法验证")
        try:
            importlib.import_module(module_name)
            return (True, f"模块 {module_name} 导入正常")
        except Exception as e:
            return (False, f"模块 {module_name} 导入失败: {e}")

    def _verify_clear_cache(self) -> Tuple[bool, str]:
        """缓存清理验证：目标目录总大小下降"""
        state = self._verify_state.get("clear_cache", {})
        cache_dirs = state.get("cache_dirs", [])
        total_before = state.get("total_before_bytes", 0)
        if not cache_dirs:
            return (False, "未记录缓存目录，无法验证")
        total_after = 0
        for d in cache_dirs:
            if os.path.exists(d):
                total_after += self._dir_size(d)
        freed = total_before - total_after
        logger.info(log_dict({'module_name': 'self_healer', 'action': 'clear_cache.verify', 'heal_action': 'clear_cache', 'cache_dirs': list(cache_dirs), 'total_before_bytes': total_before, 'total_after_bytes': total_after, 'freed_bytes': freed}))
        if total_after < total_before:
            return (True, f"缓存大小下降 {freed} 字节")
        return (False, f"缓存大小未下降(前 {total_before} 后 {total_after} 字节)")

    def _verify_circuit_breaker(self) -> Tuple[bool, str]:
        """熔断恢复验证：目标熔断器状态非 OPEN（读公开 get_status()）"""
        from agent.circuit_breaker import get_circuit_breaker
        ctx = self._last_context.get("recover_circuit_breaker", {})
        cb_name = ctx.get("circuit_breaker_name", "*")
        if cb_name == "*":
            return (True, "全量恢复无单点验证目标，依赖健康分")
        try:
            status = get_circuit_breaker(cb_name).get_status()
            state = status.get("state")
            logger.info(log_dict({'module_name': 'self_healer', 'action': 'recover_circuit_breaker.verify', 'heal_action': 'recover_circuit_breaker', 'breaker_name': cb_name, 'state': state, 'open': state == "open"}))
            if state != "open":
                return (True, f"熔断器 {cb_name} 状态为 {state}，非 OPEN")
            return (False, f"熔断器 {cb_name} 仍为 OPEN")
        except Exception as e:
            logger.warning(log_dict({'module_name': 'self_healer', 'action': 'recover_circuit_breaker.verify_error', 'heal_action': 'recover_circuit_breaker', 'breaker_name': cb_name, 'error': str(e)}))
            return (False, f"熔断器 {cb_name} 状态读取失败: {e}")

    def _verify_memory(self, action: str) -> Tuple[bool, str]:
        """内存类动作验证：RSS 下降 > 阈值或对象数下降"""
        state = self._verify_state.get(action, {})
        mem_before = state.get("mem_mb_before")
        if mem_before is None:
            return (False, "未记录动作前内存基线，无法验证")
        mem_now = self._get_memory_usage()
        freed = mem_before - mem_now
        if freed > self._verify_mem_delta_mb:
            return (True, f"RSS 下降 {freed:.2f} MB(阈值 {self._verify_mem_delta_mb} MB)")
        objects_before = state.get("objects_before")
        if action == "gc_collect" and objects_before is not None:
            objects_now = len(__import__("gc").get_objects())
            if objects_now < objects_before:
                return (True, f"对象数下降 {objects_before - objects_now}")
        return (False, f"RSS 未明显下降(前 {mem_before:.2f} 后 {mem_now:.2f} MB)")

    def _check_port_open(self, port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> bool:
        """检查目标端口是否可连接（进程重启/端口恢复的验证依据）"""
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        except (OSError, ValueError):
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
                logger.error(log_dict({'module_name': 'self_healer', 'action': 'health_check_loop_error', 'error': str(e)}))
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

        logger.info(log_dict({'module_name': 'self_healer', 'action': 'start', 'health_check_interval': self._health_check_interval}))

    def stop(self):
        """停止自愈管理器"""
        self._running = False
        if self._health_check_thread:
            self._health_check_thread.join(timeout=self._thread_join_timeout)

        logger.info(log_dict({'module_name': 'self_healer', 'action': 'stop'}))


# 全局单例
_self_healer: Optional[SelfHealer] = None  # 保留作为 fallback
# [2026-08-13 并发审计] fallback 单例双检锁：防并发首调创建多个实例
_self_healer_lock = threading.Lock()


def _create_self_healer(config=None):
    """SelfHealer 工厂（供 SingletonManager 使用）

    config 可能以两种形态传入，需区分：
    - SingletonManager dict 通道：{"self_healer_config": <原配置>}，需解包
    - 直接传入的自愈配置（dict 或 None）：原样传给 SelfHealer
    """
    if isinstance(config, dict) and "self_healer_config" in config:
        config = config["self_healer_config"]
    return SelfHealer(config)


def _cleanup_self_healer(healer):
    """清理钩子：停止自愈健康检查线程（仅测试重置时调用）"""
    if healer is not None and healer._running:
        healer.stop()


def get_self_healer(config: Optional[Dict[str, Any]] = None) -> SelfHealer:
    """获取全局自愈管理器

    Args:
        config: 自愈配置

    Returns:
        SelfHealer 实例
    """
    if _SINGLETON_AVAILABLE:
        if config is not None and not is_initialized("self_healer"):
            return get_singleton("self_healer", {"self_healer_config": config})
        return get_singleton("self_healer")
    global _self_healer
    if _self_healer is None:
        # [2026-08-13 并发审计] fallback 双检锁：防并发首调创建多个实例
        with _self_healer_lock:
            if _self_healer is None:
                _self_healer = _create_self_healer(config)
    return _self_healer


def reset_self_healer():
    """重置全局自愈管理器单例（仅用于测试）"""
    global _self_healer
    if _SINGLETON_AVAILABLE:
        reset_singleton("self_healer")
    _self_healer = None


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


# 注册单例工厂（置于文件末尾，确保类已定义）
if _SINGLETON_AVAILABLE:
    register_singleton("self_healer", _create_self_healer, cleanup_fn=_cleanup_self_healer)
