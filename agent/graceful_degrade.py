#!/usr/bin/env python3
"""优雅降级 — 依赖故障下的多级回退机制

当外部依赖（Schema 验证、Critic 评估、Memory 查询、Dashboard 加载）不可用时，
按预设的多级降级策略保证核心链路可用，避免雪崩。

降级策略（与项目 memory 一致）：
- Schema 验证失败：重试 → 宽松验证 → 纯文本响应
- Critic 不可用：自动跳过评估（不阻断主流程）
- Memory 查询超时：返回空结果
- Dashboard 加载失败：展示缓存数据

可观测性约束：
- 所有降级动作输出结构化 JSON 日志（trace_id/module_name/action/duration_ms）
- 降级埋点上报 BusinessMetricsCollector（吞掉异常不影响主流程）
"""

from __future__ import annotations

import json
import logging
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")


def set_trace_id(trace_id: str) -> None:
    _trace_id_ctx.set(trace_id or "")


def get_trace_id() -> str:
    return _trace_id_ctx.get()


class DegradeLevel(str, Enum):
    """降级级别"""
    NORMAL = "normal"              # 正常
    RETRY = "retry"                # 重试中
    RELAXED = "relaxed"            # 宽松模式
    FALLBACK = "fallback"          # 回退到缓存/默认值
    DISABLED = "disabled"          # 完全禁用该依赖


class DegradeModule(str, Enum):
    """可降级模块枚举（与 output_schema/critic/memory/dashboard 对齐）"""
    SCHEMA = "schema"                  # Schema 验证
    CRITIC = "critic"                  # Critic 评审
    MEMORY = "memory"                  # Memory 路由
    DASHBOARD = "dashboard"            # Dashboard 加载
    TOOL_CALLING = "tool_calling"      # 工具调用
    LLM_ROUTER = "llm_router"          # LLM 路由


class DegradeError(Exception):
    """降级失败时抛出的业务错误（带明确业务错误码）"""

    def __init__(self, message: str, error_code: str = "DEGRADE_FAILED",
                 component: str = ""):
        super().__init__(message)
        self.error_code = error_code
        self.component = component


@dataclass
class DegradeState:
    """单个组件的降级状态"""
    component: str
    level: DegradeLevel = DegradeLevel.NORMAL
    failure_count: int = 0
    last_failure_time: float = 0.0
    last_fallback_value: Any = None
    degrade_until: float = 0.0  # 降级到期时间戳


class GracefulDegrade:
    """优雅降级管理器

    管理多个组件的降级状态，提供统一的回退调用接口。

    Args:
        default_fallbacks: 各组件的默认回退值 {component: value}
        max_retries: 单次降级前最大重试次数
        degrade_seconds: 降级持续时间（秒），到期后尝试恢复
    """

    def __init__(
        self,
        default_fallbacks: Optional[dict] = None,
        max_retries: int = 3,
        degrade_seconds: float = 30.0,
    ):
        self.default_fallbacks = default_fallbacks or {
            "schema_validator": None,
            "critic_engine": None,  # None 表示跳过评估
            "memory_router": [],     # 空列表表示无历史记忆
            "dashboard_loader": {},  # 空字典表示无缓存数据
        }
        self.max_retries = max_retries
        self.degrade_seconds = degrade_seconds
        self._states: dict[str, DegradeState] = {}
        self._lock = threading.RLock()
        # 缓存数据池（用于 Dashboard 等场景）
        self._cache_pool: dict[str, Any] = {}

    # ── 降级调用入口 ─────────────────────────────────────────

    def call_with_fallback(
        self,
        component: str,
        func: Callable,
        *args,
        fallback: Optional[Any] = None,
        retry_strategy: Optional[Callable] = None,
        **kwargs,
    ) -> Any:
        """通过降级保护执行函数

        策略：
        1. 检查组件是否处于降级期，是则直接返回 fallback
        2. 否则尝试执行 func
        3. 失败时按 retry_strategy 重试
        4. 仍失败则触发降级，返回 fallback

        Args:
            component: 组件名称（schema_validator/critic_engine/...）
            func: 主调用函数
            fallback: 显式回退值（不传则使用 default_fallbacks）
            retry_strategy: 自定义重试策略（默认指数退避）
        """
        if fallback is None:
            fallback = self.default_fallbacks.get(component)

        # 检查是否在降级期
        if self.is_degraded(component):
            self._log_action("degrade_hit", {
                "component": component,
                "level": self.get_state(component).level.value,
            })
            return fallback

        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                start = time.time()
                result = func(*args, **kwargs)
                duration_ms = int((time.time() - start) * 1000)
                # 成功则尝试恢复降级状态
                self._maybe_recover(component)
                self._log_action("degrade_call_success", {
                    "component": component,
                    "attempt": attempt,
                    "duration_ms": duration_ms,
                })
                # 更新缓存池
                self._update_cache(component, result)
                return result
            except Exception as exc:
                last_exc = exc
                self._record_failure(component)
                self._log_action("degrade_call_failure", {
                    "component": component,
                    "attempt": attempt,
                    "error": str(exc)[:200],
                })
                # 调用自定义重试策略
                if retry_strategy and attempt < self.max_retries:
                    try:
                        retry_strategy(attempt, exc)
                    except Exception:
                        pass

        # 全部重试失败，触发降级
        self._trigger_degrade(component)
        self._log_action("degrade_triggered", {
            "component": component,
            "failures": self.get_state(component).failure_count,
        })
        return fallback

    # ── Schema 验证专用多级降级 ──────────────────────────────

    def schema_validate_with_fallback(
        self,
        validator: Callable,
        data: Any,
        relaxed_validator: Optional[Callable] = None,
    ) -> tuple[bool, Any]:
        """Schema 验证专用多级降级：重试 → 宽松验证 → 纯文本

        Returns:
            (is_valid, result_or_text)
        """
        # 短路检查：若 schema_validator 已处于降级状态，直接返回纯文本，
        # 不再调用验证器（避免降级期间仍调用不可用的依赖）
        if self.is_degraded("schema_validator"):
            self._log_action("schema_degrade_short_circuit", {
                "component": "schema_validator",
                "level": self.get_state("schema_validator").level.value,
            })
            if isinstance(data, str):
                return False, data
            return False, str(data)

        # 第一级：标准验证（最多重试 3 次）
        for attempt in range(3):
            try:
                return True, validator(data)
            except Exception as exc:
                self._record_failure("schema_validator")
                self._log_action("schema_validate_fail", {
                    "attempt": attempt,
                    "error": str(exc)[:200],
                })

        # 第二级：宽松验证
        if relaxed_validator is not None:
            try:
                return True, relaxed_validator(data)
            except Exception as exc:
                self._log_action("schema_relaxed_fail", {
                    "error": str(exc)[:200],
                })

        # 第三级：降级为纯文本响应
        self._trigger_degrade("schema_validator")
        if isinstance(data, str):
            return False, data
        return False, str(data)

    # ── 状态管理 ──────────────────────────────────────────────

    def is_degraded(self, component: str) -> bool:
        """组件是否处于降级期"""
        with self._lock:
            state = self._states.get(component)
            if state is None:
                return False
            if state.level == DegradeLevel.NORMAL:
                return False
            # 检查降级是否到期
            if time.time() >= state.degrade_until:
                # 降级到期，尝试恢复
                state.level = DegradeLevel.NORMAL
                state.failure_count = 0
                return False
            return True

    def get_state(self, component: str) -> DegradeState:
        """获取组件降级状态"""
        with self._lock:
            if component not in self._states:
                self._states[component] = DegradeState(component=component)
            return self._states[component]

    def _record_failure(self, component: str) -> None:
        """记录失败"""
        with self._lock:
            if component not in self._states:
                self._states[component] = DegradeState(component=component)
            state = self._states[component]
            state.failure_count += 1
            state.last_failure_time = time.time()

    def _trigger_degrade(self, component: str) -> None:
        """触发降级"""
        with self._lock:
            if component not in self._states:
                self._states[component] = DegradeState(component=component)
            state = self._states[component]
            state.level = DegradeLevel.FALLBACK
            state.degrade_until = time.time() + self.degrade_seconds

    def _maybe_recover(self, component: str) -> None:
        """成功调用后尝试恢复降级状态"""
        with self._lock:
            state = self._states.get(component)
            if state and state.level != DegradeLevel.NORMAL:
                state.level = DegradeLevel.NORMAL
                state.failure_count = 0
                self._log_action("degrade_recovered", {"component": component})

    def _update_cache(self, component: str, data: Any) -> None:
        """更新缓存池（用于 Dashboard 等场景的回退数据）"""
        try:
            self._cache_pool[component] = data
        except Exception:
            pass

    def get_cached(self, component: str) -> Any:
        """获取缓存数据（Dashboard 加载失败时回退）"""
        return self._cache_pool.get(component)

    # ── 模块化降级入口（与 DegradeModule 枚举对齐） ─────────

    def with_degrade(
        self,
        module: "DegradeModule | str",
        func: Callable,
        *args,
        fallback: Optional[Callable] = None,
        **kwargs,
    ) -> Any:
        """以模块为粒度执行带降级保护的调用

        与 call_with_fallback 的差异：
        - 接受 DegradeModule 枚举（或字符串）作为模块标识
        - fallback 是 Callable（惰性求值），仅在主调用失败时执行
        - 失败时记录降级并返回 fallback 结果，不抛异常

        Args:
            module: DegradeModule 枚举值或字符串
            func: 主调用函数
            fallback: 回退函数（无参，返回回退值）
        """
        component = module.value if isinstance(module, DegradeModule) else str(module)

        # 已处于降级期：直接返回 fallback
        if self.is_degraded(component):
            if fallback is not None:
                try:
                    return fallback()
                except Exception as exc:
                    self._log_action("fallback_failed", {
                        "component": component, "error": str(exc)[:200],
                    })
                    return self.default_fallbacks.get(component)
            return self.default_fallbacks.get(component)

        # 尝试主调用
        try:
            result = func(*args, **kwargs)
            self._maybe_recover(component)
            return result
        except Exception as exc:
            self._record_failure(component)
            self._trigger_degrade(component)
            self._log_action("degrade_triggered", {
                "component": component,
                "error": str(exc)[:200],
            })
            if fallback is not None:
                try:
                    return fallback()
                except Exception as fb_exc:
                    self._log_action("fallback_failed", {
                        "component": component, "error": str(fb_exc)[:200],
                    })
            return self.default_fallbacks.get(component)

    def is_degraded(self, component: str) -> bool:
        """组件当前是否处于降级期"""
        return self.get_state(component).level != DegradeLevel.NORMAL

    # ── 控制方法 ──────────────────────────────────────────────

    def reset(self) -> None:
        """重置所有降级状态（主要用于测试）"""
        with self._lock:
            self._states.clear()
            self._cache_pool.clear()

    def force_degrade(self, component: str, level: DegradeLevel = DegradeLevel.FALLBACK) -> None:
        """强制降级组件"""
        with self._lock:
            if component not in self._states:
                self._states[component] = DegradeState(component=component)
            state = self._states[component]
            state.level = level
            state.degrade_until = time.time() + self.degrade_seconds

    # ── 可观测性 ──────────────────────────────────────────────

    def _log_action(self, action: str, payload: dict) -> None:
        """输出结构化 JSON 日志"""
        try:
            log_entry = {
                "trace_id": get_trace_id(),
                "module_name": "graceful_degrade",
                "action": action,
                "duration_ms": 0,
                "timestamp": time.time(),
                **payload,
            }
            logger.info(json.dumps(log_entry, ensure_ascii=False))
        except Exception as exc:
            logger.debug("降级日志记录失败: %s", exc)


# ── 全局降级管理器单例（按需创建，线程安全） ─────────────────
_degrade_manager: Optional["GracefulDegrade"] = None
_degrade_lock = threading.Lock()


def get_degrade_manager(
    default_fallbacks: Optional[dict] = None,
    max_retries: int = 3,
    degrade_seconds: float = 30.0,
    force_new: bool = False,
) -> "GracefulDegrade":
    """获取全局共享的降级管理器实例

    幂等：首次调用按参数创建，后续调用忽略参数返回同一实例。
    force_new=True 时强制创建新实例（测试场景使用，需配合 reset_degrade_manager）。

    边界显性化：参数非法时抛出 DegradeError 而非静默使用默认值。
    """
    global _degrade_manager
    if max_retries < 0:
        raise DegradeError(
            "max_retries 不能为负数",
            error_code="DEGRADE_INVALID_PARAM",
            component="degrade_manager",
        )
    if degrade_seconds <= 0:
        raise DegradeError(
            "degrade_seconds 必须为正数",
            error_code="DEGRADE_INVALID_PARAM",
            component="degrade_manager",
        )

    with _degrade_lock:
        if _degrade_manager is None or force_new:
            _degrade_manager = GracefulDegrade(
                default_fallbacks=default_fallbacks,
                max_retries=max_retries,
                degrade_seconds=degrade_seconds,
            )
        return _degrade_manager


def reset_degrade_manager() -> None:
    """重置全局降级管理器（测试用：在 fixture 中重置状态）"""
    global _degrade_manager
    with _degrade_lock:
        if _degrade_manager is not None:
            _degrade_manager.reset()
        _degrade_manager = None
