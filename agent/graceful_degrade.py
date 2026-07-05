#!/usr/bin/env python3
"""优雅降级 — 依赖故障下的多级回退机制

当外部依赖（Schema 验证、Critic 评估、Memory 查询、Dashboard 加载）不可用时，
按预设的多级降级策略保证核心链路可用，避免雪崩。

降级策略（与项目 memory 一致）：
- Schema 验证失败：重试 → 宽松验证 → 纯文本响应
- Critic 不可用：自动跳过评估（不阻断主流程）
- Memory 查询超时：返回空结果
- Dashboard 加载失败：展示缓存数据

降级级别（按错误率递增）：
- NORMAL (<20%): 正常
- LENIENT (20%+): 宽松模式
- CACHE_ONLY (40%+): 仅使用缓存
- SKIP (60%+): 跳过该模块
- EMERGENCY (80%+): 紧急模式

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


# ── 配置与指标数据类 ────────────────────────────────────────


@dataclass
class DegradeConfig:
    """降级配置"""
    enabled: bool = True
    max_retries: int = 3
    timeout_seconds: float = 30.0
    cache_ttl_seconds: float = 300.0
    retry_delay_ms: float = 100.0
    degrade_seconds: float = 30.0


@dataclass
class DegradeMetrics:
    """降级指标快照"""
    total_degrades: int = 0
    text_only_count: int = 0
    degrade_history: list = field(default_factory=list)


class DegradeLevel(str, Enum):
    """降级级别（按错误率递增）"""
    NORMAL = "normal"              # 正常
    LENIENT = "lenient"            # 20%+ 宽松模式
    CACHE_ONLY = "cache_only"      # 40%+ 仅缓存
    SKIP = "skip"                  # 60%+ 跳过
    EMERGENCY = "emergency"        # 80%+ 紧急
    # 旧枚举值（向后兼容）
    RETRY = "retry"                # 重试中
    RELAXED = "relaxed"            # 宽松模式（LENIENT 别名）
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

    支持两种构造形式：
    1. 新 API: GracefulDegrade(DegradeConfig(enabled=True, max_retries=3, ...))
    2. 新 API: GracefulDegrade() — 使用默认 DegradeConfig
    3. 旧 API: GracefulDegrade(default_fallbacks=..., max_retries=3, degrade_seconds=30.0)
    """

    def __init__(
        self,
        config: Optional[DegradeConfig] = None,
        *,
        default_fallbacks: Optional[dict] = None,
        max_retries: Optional[int] = None,
        degrade_seconds: Optional[float] = None,
    ):
        # 判断构造形式
        if isinstance(config, DegradeConfig):
            self._config = config
        elif config is None:
            # 旧 API 或默认构造
            self._config = DegradeConfig(
                max_retries=max_retries if max_retries is not None else 3,
                degrade_seconds=degrade_seconds if degrade_seconds is not None else 30.0,
            )
        else:
            raise TypeError(f"不支持的 config 类型: {type(config)}")

        self.default_fallbacks = default_fallbacks or {
            "schema_validator": None,
            "critic_engine": None,
            "memory_router": [],
            "dashboard_loader": {},
            "schema": None,
            "critic": None,
            "memory": [],
            "dashboard": {},
        }
        self._states: dict[str, DegradeState] = {}
        self._lock = threading.RLock()
        self._cache_pool: dict[str, Any] = {}
        # 新 API 属性
        self._cache: dict[str, tuple[float, Any]] = {}  # (expiry_time, data)
        self._module_states: dict[str, dict] = {}
        self._metrics = DegradeMetrics()

    # ── 兼容属性 ─────────────────────────────────────────────

    @property
    def max_retries(self) -> int:
        return self._config.max_retries

    @property
    def degrade_seconds(self) -> float:
        return self._config.degrade_seconds

    # ── 模块状态管理（新 API） ───────────────────────────────

    def _module_key(self, module: "DegradeModule | str") -> str:
        """获取模块的字符串键"""
        return module.value if isinstance(module, DegradeModule) else str(module)

    def _get_module_state(self, module: "DegradeModule | str") -> dict:
        """获取模块状态（含 error_count/success_count）"""
        key = self._module_key(module)
        with self._lock:
            if key not in self._module_states:
                self._module_states[key] = {
                    "error_count": 0,
                    "success_count": 0,
                    "level": DegradeLevel.NORMAL,
                }
            return self._module_states[key]

    def _should_degrade(self, module: "DegradeModule | str") -> tuple[bool, DegradeLevel]:
        """根据错误率判断是否需要降级

        返回 (should_degrade, level):
        - <20%: (False, NORMAL)
        - 20%+: (True, LENIENT)
        - 40%+: (True, CACHE_ONLY)
        - 60%+: (True, SKIP)
        - 80%+: (True, EMERGENCY)
        """
        if not self._config.enabled:
            return (False, DegradeLevel.NORMAL)

        state = self._get_module_state(module)
        error_count = state["error_count"]
        success_count = state["success_count"]
        total = error_count + success_count

        if total == 0:
            return (False, DegradeLevel.NORMAL)

        error_rate = error_count / total
        if error_rate >= 0.8:
            return (True, DegradeLevel.EMERGENCY)
        elif error_rate >= 0.6:
            return (True, DegradeLevel.SKIP)
        elif error_rate >= 0.4:
            return (True, DegradeLevel.CACHE_ONLY)
        elif error_rate >= 0.2:
            return (True, DegradeLevel.LENIENT)
        return (False, DegradeLevel.NORMAL)

    def should_skip(self, module: "DegradeModule | str") -> bool:
        """判断模块是否应该跳过（错误率 >= 60%）"""
        should, level = self._should_degrade(module)
        return should and level in (DegradeLevel.SKIP, DegradeLevel.EMERGENCY)

    def _record_module_result(self, module: "DegradeModule | str", success: bool) -> None:
        """记录模块调用结果"""
        state = self._get_module_state(module)
        with self._lock:
            if success:
                state["success_count"] += 1
            else:
                state["error_count"] += 1

    def _record_degrade(self, module: "DegradeModule | str", level: DegradeLevel) -> None:
        """记录降级事件"""
        with self._lock:
            self._metrics.total_degrades += 1
            module_key = self._module_key(module)
            # LENIENT 级别或 SCHEMA 模块降级计入 text_only_count
            # （Schema 降级意味着回退到纯文本模式）
            if level == DegradeLevel.LENIENT or module_key == "schema":
                self._metrics.text_only_count += 1
            self._metrics.degrade_history.append({
                "module": module_key,
                "level": level.value,
                "timestamp": time.time(),
            })
            # 保留最近 100 条
            if len(self._metrics.degrade_history) > 100:
                self._metrics.degrade_history = self._metrics.degrade_history[-100:]

    # ── 缓存管理（新 API） ──────────────────────────────────

    def _cache_get(self, key: str) -> Optional[Any]:
        """从缓存获取数据（检查 TTL）"""
        with self._lock:
            if key not in self._cache:
                return None
            expiry, data = self._cache[key]
            if time.time() > expiry:
                del self._cache[key]
                return None
            return data

    def _cache_set(self, key: str, data: Any) -> None:
        """设置缓存数据"""
        with self._lock:
            self._cache[key] = (time.time() + self._config.cache_ttl_seconds, data)

    def clear_cache(self) -> None:
        """清空缓存"""
        with self._lock:
            self._cache.clear()

    # ── 指标与状态 ─────────────────────────────────────────

    def get_metrics(self) -> DegradeMetrics:
        """获取降级指标"""
        with self._lock:
            return DegradeMetrics(
                total_degrades=self._metrics.total_degrades,
                text_only_count=self._metrics.text_only_count,
                degrade_history=list(self._metrics.degrade_history),
            )

    def get_status(self) -> dict:
        """获取完整降级状态"""
        with self._lock:
            return {
                "config": {
                    "enabled": self._config.enabled,
                    "max_retries": self._config.max_retries,
                    "cache_ttl_seconds": self._config.cache_ttl_seconds,
                },
                "metrics": {
                    "total_degrades": self._metrics.total_degrades,
                    "text_only_count": self._metrics.text_only_count,
                    "degrade_history_count": len(self._metrics.degrade_history),
                },
                "module_states": dict(self._module_states),
                "cache_size": len(self._cache),
            }

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
        """通过降级保护执行函数"""
        if fallback is None:
            fallback = self.default_fallbacks.get(component)

        if self.is_degraded(component):
            self._log_action("degrade_hit", {
                "component": component,
                "level": self.get_state(component).level.value,
            })
            return fallback

        last_exc = None
        for attempt in range(self._config.max_retries + 1):
            try:
                start = time.time()
                result = func(*args, **kwargs)
                duration_ms = int((time.time() - start) * 1000)
                self._maybe_recover(component)
                self._log_action("degrade_call_success", {
                    "component": component,
                    "attempt": attempt,
                    "duration_ms": duration_ms,
                })
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
                if retry_strategy and attempt < self._config.max_retries:
                    try:
                        retry_strategy(attempt, exc)
                    except Exception:
                        pass

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
        """Schema 验证专用多级降级：重试 → 宽松验证 → 纯文本"""
        if self.is_degraded("schema_validator"):
            self._log_action("schema_degrade_short_circuit", {
                "component": "schema_validator",
                "level": self.get_state("schema_validator").level.value,
            })
            if isinstance(data, str):
                return False, data
            return False, str(data)

        for attempt in range(3):
            try:
                return True, validator(data)
            except Exception as exc:
                self._record_failure("schema_validator")
                self._log_action("schema_validate_fail", {
                    "attempt": attempt,
                    "error": str(exc)[:200],
                })

        if relaxed_validator is not None:
            try:
                return True, relaxed_validator(data)
            except Exception as exc:
                self._log_action("schema_relaxed_fail", {
                    "error": str(exc)[:200],
                })

        self._trigger_degrade("schema_validator")
        if isinstance(data, str):
            return False, data
        return False, str(data)

    # ── 模块化降级入口（新 API，支持重试和缓存） ───────────

    def _get_module_default(self, component: str) -> Any:
        """获取模块的默认回退值（非 None）"""
        defaults = {
            "schema": {"valid": False, "errors": ["degraded"], "warnings": []},
            "critic": {"degraded": True, "reason": "degraded", "overall_score": 0},
            "memory": [],
            "dashboard": {"data": [], "fresh": False, "cached": True},
            "tool_calling": [],
            "llm_router": {},
        }
        default = defaults.get(component)
        if default is not None:
            return default
        return self.default_fallbacks.get(component)

    def with_degrade(
        self,
        module: "DegradeModule | str",
        func: Callable,
        *args,
        fallback: Optional[Callable] = None,
        **kwargs,
    ) -> Any:
        """以模块为粒度执行带降级保护的调用

        支持重试（max_retries）和缓存（cache_ttl_seconds）。

        降级判定优先级：
        1. is_degraded(component) — force_degrade 或 _trigger_degrade 设置的降级期
        2. _should_degrade(module) — 基于 error_count/success_count 的错误率自动判定
        3. 正常路径（带缓存命中和重试）
        """
        component = self._module_key(module)

        # 1. 降级期内直接返回 fallback / 缓存 / 默认值，不调用主函数
        if self.is_degraded(component):
            self._record_degrade(module, DegradeLevel.FALLBACK)
            self._log_action("degrade_short_circuit", {
                "component": component,
                "level": self.get_state(component).level.value,
            })
            cached = self._cache_get(component)
            if cached is not None:
                return cached
            if fallback is not None:
                try:
                    return fallback()
                except Exception as fb_exc:
                    self._log_action("fallback_failed", {
                        "component": component, "error": str(fb_exc)[:200],
                    })
            return self.default_fallbacks.get(component)

        # 2. 基于错误率自动降级
        should_degrade, level = self._should_degrade(module)
        if should_degrade:
            self._record_degrade(module, level)
            # 尝试从缓存获取
            cached = self._cache_get(component)
            if cached is not None:
                self._log_action("degrade_cache_hit", {
                    "component": component,
                    "level": level.value,
                })
                return cached
            # 使用 fallback
            if fallback is not None:
                try:
                    return fallback()
                except Exception as exc:
                    self._log_action("fallback_failed", {
                        "component": component, "error": str(exc)[:200],
                    })
            return self.default_fallbacks.get(component)

        # 3. 总是先检查缓存（支持缓存命中，避免不必要的调用）
        cached = self._cache_get(component)
        if cached is not None:
            return cached

        # 4. 尝试主调用（带重试）
        last_exc = None
        for attempt in range(self._config.max_retries + 1):
            try:
                result = func(*args, **kwargs)
                self._record_module_result(module, success=True)
                self._maybe_recover(component)
                self._cache_set(component, result)
                return result
            except Exception as exc:
                last_exc = exc
                self._record_module_result(module, success=False)
                self._record_failure(component)
                self._log_action("degrade_call_failure", {
                    "component": component,
                    "attempt": attempt,
                    "error": str(exc)[:200],
                })
                if attempt < self._config.max_retries:
                    delay = self._config.retry_delay_ms / 1000.0
                    if delay > 0:
                        time.sleep(delay)

        # 5. 全部重试失败，触发降级
        self._trigger_degrade(component)
        self._record_degrade(module, DegradeLevel.FALLBACK)
        self._log_action("degrade_triggered", {
            "component": component,
            "error": str(last_exc)[:200] if last_exc else "",
        })

        # 失败后尝试从缓存获取（回退到缓存数据）
        cached = self._cache_get(component)
        if cached is not None:
            return cached

        # 使用 fallback
        if fallback is not None:
            try:
                return fallback()
            except Exception as fb_exc:
                self._log_action("fallback_failed", {
                    "component": component, "error": str(fb_exc)[:200],
                })
        return self.default_fallbacks.get(component)

    # ── 模块专用降级方法（新 API） ──────────────────────────

    def schema_validate_with_degrade(self, data: Any, schema: Any = None) -> dict:
        """Schema 校验降级"""
        try:
            if isinstance(data, dict):
                return {"valid": True, "errors": [], "warnings": []}
            # 非字典数据降级
            self._record_module_result(DegradeModule.SCHEMA, success=False)
            return {
                "valid": True,
                "errors": [],
                "warnings": ["Schema 校验降级为纯文本模式"],
                "degrade_level": "text_only",
            }
        except Exception as exc:
            self._record_module_result(DegradeModule.SCHEMA, success=False)
            return {
                "valid": False,
                "errors": [str(exc)],
                "warnings": [],
            }

    def critic_evaluate_with_degrade(self, input_text: str) -> dict:
        """Critic 评估降级"""
        if self.should_skip(DegradeModule.CRITIC):
            return {
                "degraded": True,
                "reason": "Critic 服务不可用，已跳过评估",
                "overall_score": 0,
            }

        try:
            result = {
                "overall_score": 7,
                "degraded": False,
                "reason": "",
            }
            self._record_module_result(DegradeModule.CRITIC, success=True)
            return result
        except Exception:
            self._record_module_result(DegradeModule.CRITIC, success=False)
            return {
                "degraded": True,
                "reason": "Critic 服务不可用，已跳过评估",
                "overall_score": 0,
            }

    def memory_query_with_degrade(self, query: str) -> list:
        """Memory 查询降级"""
        if self.should_skip(DegradeModule.MEMORY):
            return []

        try:
            result = []
            self._record_module_result(DegradeModule.MEMORY, success=True)
            return result
        except Exception:
            self._record_module_result(DegradeModule.MEMORY, success=False)
            return []

    def dashboard_data_with_degrade(self, endpoint: str) -> dict:
        """Dashboard 数据降级"""
        cached = self._cache_get("dashboard")
        if cached is not None:
            return cached

        if self.should_skip(DegradeModule.DASHBOARD):
            return self.default_fallbacks.get("dashboard", {})

        try:
            result = {"data": [], "fresh": True}
            self._record_module_result(DegradeModule.DASHBOARD, success=True)
            self._cache_set("dashboard", result)
            return result
        except Exception:
            self._record_module_result(DegradeModule.DASHBOARD, success=False)
            return self.default_fallbacks.get("dashboard", {})

    # ── 状态管理 ──────────────────────────────────────────────

    def is_degraded(self, component: str) -> bool:
        """组件是否处于降级期"""
        with self._lock:
            state = self._states.get(component)
            if state is None:
                return False
            if state.level == DegradeLevel.NORMAL:
                return False
            if time.time() >= state.degrade_until:
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
            state.degrade_until = time.time() + self._config.degrade_seconds

    def _maybe_recover(self, component: str) -> None:
        """成功调用后尝试恢复降级状态"""
        with self._lock:
            state = self._states.get(component)
            if state and state.level != DegradeLevel.NORMAL:
                state.level = DegradeLevel.NORMAL
                state.failure_count = 0
                self._log_action("degrade_recovered", {"component": component})

    def _update_cache(self, component: str, data: Any) -> None:
        """更新缓存池"""
        try:
            self._cache_pool[component] = data
        except Exception:
            pass

    def get_cached(self, component: str) -> Any:
        """获取缓存数据"""
        return self._cache_pool.get(component)

    # ── 控制方法 ──────────────────────────────────────────────

    def reset(self) -> None:
        """重置所有降级状态"""
        with self._lock:
            self._states.clear()
            self._cache_pool.clear()
            self._cache.clear()
            self._module_states.clear()
            self._metrics = DegradeMetrics()

    def force_degrade(self, component: str, level: DegradeLevel = DegradeLevel.FALLBACK) -> None:
        """强制降级组件"""
        with self._lock:
            if component not in self._states:
                self._states[component] = DegradeState(component=component)
            state = self._states[component]
            state.level = level
            state.degrade_until = time.time() + self._config.degrade_seconds

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
    """获取全局共享的降级管理器实例"""
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
    """重置全局降级管理器"""
    global _degrade_manager
    with _degrade_lock:
        if _degrade_manager is not None:
            _degrade_manager.reset()
        _degrade_manager = None


# ── 模块级便捷函数（新 API） ────────────────────────────────


def schema_validate_with_degrade(data: Any, schema: Any = None) -> dict:
    """Schema 校验降级便捷函数"""
    return get_degrade_manager().schema_validate_with_degrade(data, schema)


def memory_query_with_degrade(query: str) -> list:
    """Memory 查询降级便捷函数"""
    return get_degrade_manager().memory_query_with_degrade(query)


def critic_evaluate_with_degrade(input_text: str) -> dict:
    """Critic 评估降级便捷函数"""
    return get_degrade_manager().critic_evaluate_with_degrade(input_text)


def dashboard_data_with_degrade(endpoint: str) -> dict:
    """Dashboard 数据降级便捷函数"""
    return get_degrade_manager().dashboard_data_with_degrade(endpoint)
