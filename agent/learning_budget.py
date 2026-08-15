"""学习动作成本预算护栏 — TASK-03

把 configs/models.yaml 的 cost_limits 从"纯声明"变为"可执行"：

- 单次学习动作 token 上限（max_single_action_tokens，默认取 cost_limits.max_per_request_tokens）；
- 日预算（max_daily_tokens，默认取 cost_limits.max_daily_tokens）——耗尽即熔断后续学习动作；
- 熔断复用既有原语，不新造语义：
    * 日预算使用 rate_limiter.TokenBucket（capacity=日预算，refill_rate=日预算/86400，令牌桶语义）；
    * 熔断状态使用 circuit_breaker.CircuitBreaker（OPEN 状态 + cooldown 自动半开放行探测）。
- mode:
    * warn_only（默认）：超限仅 WARNING 记录，不拦截——防止上线即改行为；
    * enforce：超限 raise LearningBudgetExceeded（调用方可捕获后降级/跳过）。

【不易】不修改 rate_limiter / circuit_breaker 任何语义，仅按现有 API 组装；
        配置优先级：环境变量 > config.yaml learning.budget > models.yaml cost_limits > 硬编码默认值。
【变易】bucket/breaker 可注入（默认走全局熔断器注册表），便于测试隔离与扩展。
【简易】with_budget(action, estimated_tokens) 上下文管理器即全部对外契约。
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

from agent.circuit_breaker import get_circuit_breaker
from agent.rate_limiter import TokenBucket

logger = logging.getLogger(__name__)

# ── 硬编码默认值（三层优先级最低层） ──────────────────────────
_DEFAULT_MODE = "warn_only"
_DEFAULT_MAX_SINGLE_ACTION_TOKENS = 4000
_DEFAULT_MAX_DAILY_TOKENS = 1_000_000
_DEFAULT_RECOVERY_SECONDS = 3600

_VALID_MODES = ("warn_only", "enforce")


class LearningBudgetExceeded(Exception):
    """学习动作超出预算（单次上限 / 日预算耗尽 / 熔断拦截）

    Attributes:
        reason: 触发原因，枚举 "single_action_limit" / "daily_exhausted" / "circuit_open"
        action_name: 被拦截的学习动作名
        tokens: 本次预估消耗 token
        limit: 触发的上限值（单次上限 / 日预算 / 恢复秒数）
        error_code: 统一业务错误码
    """

    def __init__(
        self,
        reason: str,
        action_name: str,
        tokens: int,
        limit: float,
        error_code: str = "LEARNING_BUDGET_EXCEEDED",
    ):
        super().__init__(
            f"学习动作 [{action_name}] 超出预算: reason={reason} "
            f"tokens={tokens} limit={limit} mode=enforce"
        )
        self.reason = reason
        self.action_name = action_name
        self.tokens = tokens
        self.limit = limit
        self.error_code = error_code


def _load_budget_config() -> Dict[str, Any]:
    """读取预算配置 — 优先级: 环境变量 > config.yaml learning.budget > models.yaml cost_limits > 硬编码默认值

    【不易】config.yaml / models.yaml 缺失或解析失败均不影响主链路（降级到低一层级）。
    """
    cfg: Dict[str, Any] = {
        "mode": _DEFAULT_MODE,
        "max_single_action_tokens": _DEFAULT_MAX_SINGLE_ACTION_TOKENS,
        "max_daily_tokens": _DEFAULT_MAX_DAILY_TOKENS,
        "recovery_seconds": _DEFAULT_RECOVERY_SECONDS,
    }

    # 1) models.yaml cost_limits（第二低优先级：仅回填 token 上限，覆盖默认值）
    try:
        mpath = Path(__file__).resolve().parent.parent / "configs" / "models.yaml"
        if mpath.exists():
            import yaml as _yaml
            with open(mpath, "r", encoding="utf-8") as f:
                mdata = _yaml.safe_load(f) or {}
            cost = (mdata.get("cost_limits") or {}) or {}
            if cost.get("max_daily_tokens"):
                cfg["max_daily_tokens"] = int(cost["max_daily_tokens"])
            if cost.get("max_per_request_tokens"):
                cfg["max_single_action_tokens"] = int(cost["max_per_request_tokens"])
    except Exception as e:
        logger.debug("[学习预算] models.yaml cost_limits 读取失败，降级到默认值: %s", e)

    # 2) config.yaml learning.budget（覆盖 models.yaml）
    try:
        cpath = Path(__file__).resolve().parent.parent / "config.yaml"
        if cpath.exists():
            import yaml as _yaml
            with open(cpath, "r", encoding="utf-8") as f:
                data = _yaml.safe_load(f) or {}
            budget_cfg = (data.get("learning") or {}).get("budget") or {}
            for key in ("mode", "max_single_action_tokens", "max_daily_tokens", "recovery_seconds"):
                if key in budget_cfg and budget_cfg[key] is not None:
                    cfg[key] = budget_cfg[key]
    except Exception as e:
        logger.debug("[学习预算] config.yaml learning.budget 读取失败，降级到默认值: %s", e)

    # 3) 环境变量（最高优先级）
    env_map = {
        "mode": os.environ.get("LEARNING_BUDGET_MODE"),
        "max_single_action_tokens": os.environ.get("LEARNING_BUDGET_MAX_SINGLE_ACTION_TOKENS"),
        "max_daily_tokens": os.environ.get("LEARNING_BUDGET_MAX_DAILY_TOKENS"),
        "recovery_seconds": os.environ.get("LEARNING_BUDGET_RECOVERY_SECONDS"),
    }
    for key, raw in env_map.items():
        if raw is None or not str(raw).strip():
            continue
        try:
            cfg[key] = str(raw).strip().lower() if key == "mode" else float(str(raw).strip())
        except (ValueError, TypeError):
            logger.warning("[学习预算] 环境变量 %s 非法值已忽略: %s",
                           {"mode": "LEARNING_BUDGET_MODE",
                            "max_single_action_tokens": "LEARNING_BUDGET_MAX_SINGLE_ACTION_TOKENS",
                            "max_daily_tokens": "LEARNING_BUDGET_MAX_DAILY_TOKENS",
                            "recovery_seconds": "LEARNING_BUDGET_RECOVERY_SECONDS"}[key], raw)

    return cfg


class LearningBudget:
    """学习动作成本预算护栏

    用法（生产，经模块级单例获取）:
        from agent.learning_budget import get_learning_budget
        with get_learning_budget().with_budget("offline_evolve", estimated_tokens=1200):
            ...  # 学习动作（超限在 enforce 模式抛 LearningBudgetExceeded）

    用法（测试，直接构造注入 bucket/breaker，避免全局状态跨测试污染）:
        lb = LearningBudget(config={"mode": "enforce", "max_daily_tokens": 100})
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        bucket: Any = None,
        breaker: Any = None,
        breaker_name: str = "learning_budget",
    ):
        cfg = dict(config or {})
        for key, default in (
            ("mode", _DEFAULT_MODE),
            ("max_single_action_tokens", _DEFAULT_MAX_SINGLE_ACTION_TOKENS),
            ("max_daily_tokens", _DEFAULT_MAX_DAILY_TOKENS),
            ("recovery_seconds", _DEFAULT_RECOVERY_SECONDS),
        ):
            cfg.setdefault(key, default)

        self.mode = str(cfg["mode"]).strip().lower()
        if self.mode not in _VALID_MODES:
            self.mode = _DEFAULT_MODE
        self.max_single_action_tokens = max(0, int(cfg["max_single_action_tokens"]))
        self.max_daily_tokens = max(1, int(cfg["max_daily_tokens"]))
        self.recovery_seconds = max(0.0, float(cfg["recovery_seconds"]))

        # 日预算令牌桶（复用 rate_limiter.TokenBucket 语义，refill_rate=日预算/86400）
        if bucket is None:
            self._bucket = TokenBucket(
                capacity=self.max_daily_tokens,
                refill_rate=self.max_daily_tokens / 86400.0,
            )
        else:
            self._bucket = bucket

        # 熔断器（复用 circuit_breaker 现有 API：OPEN 状态 + cooldown 自动半开放行探测）
        if breaker is None:
            self._breaker = get_circuit_breaker(
                breaker_name,
                failure_threshold=1.0,   # 仅日预算耗尽显式触发，不按错误率误开
                cooldown_seconds=self.recovery_seconds,
                min_calls=1,
                half_open_max_calls=1,
                half_open_success_threshold=1,
            )
        else:
            self._breaker = breaker

        self._lock = threading.RLock()
        self._tripped = False

    # ════════════════════════════════════════════════════════════
    #  对外契约
    # ════════════════════════════════════════════════════════════

    @contextmanager
    def with_budget(self, action_name: str, estimated_tokens: int = 0) -> Iterator["LearningBudget"]:
        """学习动作预算上下文

        - enforce 模式超限 → raise LearningBudgetExceeded（调用方可捕获后降级/跳过）；
        - warn_only 模式 → WARNING 记录后照常执行（不拦截不熔断，仅观察）；
        - 正文抛异常 → 释放已预留的日预算 token（防失败动作白占预算）。
        """
        estimated = max(0, int(estimated_tokens or 0))
        logger.debug(
            "[学习预算] 进入预算评估: action=%s estimated=%d mode=%s",
            action_name, estimated, self.mode,
        )
        reason = self._evaluate(action_name, estimated)
        reserved = 0 if reason is not None else estimated  # 仅放行时预留了日预算
        if reason is not None:
            reason_str, limit = reason
            self._log_exceeded(action_name, estimated, reason_str, limit)
            if self.mode == "enforce":
                logger.warning(
                    "[学习预算] 拦截学习动作（enforce）: action=%s reason=%s tokens=%d limit=%s",
                    action_name, reason_str, estimated, limit,
                )
                raise LearningBudgetExceeded(
                    reason_str, action_name=action_name, tokens=estimated, limit=limit,
                )
            # warn_only: 仅记录，照常执行
            logger.warning(
                "[学习预算] 超限但 warn_only 放行: action=%s reason=%s tokens=%d limit=%s",
                action_name, reason_str, estimated, limit,
            )
        else:
            logger.debug(
                "[学习预算] 预算放行: action=%s estimated=%d（已预留日预算 %d）",
                action_name, estimated, reserved,
            )
        try:
            yield self
        except BaseException:
            # 正文异常不触发熔断（熔断仅由预算耗尽驱动）；释放预留防白占
            if reserved:
                self._safe_release(reserved)
                logger.info(
                    "[学习预算] 正文异常释放预留日预算: action=%s reserved=%d（不触发熔断）",
                    action_name, reserved,
                )
            raise

    def spend(self, tokens: int) -> bool:
        """记录实际 token 消耗（learning.budget.spent 指标）

        超出日预算时触发熔断并返回 False（enforce 模式下由调用方决定是否中断）；
        正常消耗返回 True。埋点异常吞掉，不影响调用方。
        """
        tokens = max(0, int(tokens or 0))
        try:
            from agent.monitoring.metrics import get_metrics_collector
            get_metrics_collector().increment_counter("learning.budget.spent", value=tokens)
        except Exception:
            pass
        if tokens <= 0:
            logger.debug("[学习预算] spend 无消耗（tokens<=0），直接放行")
            return True
        if not self._bucket.try_acquire(tokens):
            self._trip_daily()
            logger.warning(
                "[学习预算] 实际消耗 %d token 超出日预算，触发熔断（action=spend）", tokens)
            return False
        logger.debug(
            "[学习预算] 实际消耗已记账: tokens=%d 日预算剩余=%s",
            tokens, self._bucket.to_dict().get("tokens"),
        )
        return True

    def get_status(self) -> Dict[str, Any]:
        """预算护栏状态快照（含令牌桶与熔断器视图）"""
        with self._lock:
            return {
                "mode": self.mode,
                "max_single_action_tokens": self.max_single_action_tokens,
                "max_daily_tokens": self.max_daily_tokens,
                "recovery_seconds": self.recovery_seconds,
                "tripped": self._tripped,
                "daily_bucket": self._bucket.to_dict(),
                "breaker": self._breaker.get_status(),
            }

    def reset(self) -> None:
        """重置预算护栏（测试 / 单例清理用）"""
        with self._lock:
            try:
                self._bucket.reset()
            except Exception:
                pass
            try:
                self._breaker.reset()
            except Exception:
                pass
            self._tripped = False

    # ════════════════════════════════════════════════════════════
    #  内部实现
    # ════════════════════════════════════════════════════════════

    def _evaluate(self, action_name: str, estimated: int) -> Optional[Tuple[str, float]]:
        """三重检查（返回触发原因与上限值；None 表示放行）

        顺序: 单次上限 → 熔断状态 → 日预算（日预算耗尽才显式熔断）。
        半开探测的成败由预算判定驱动：日预算充足 → record_result(True) 使
        熔断器 CLOSED 恢复；耗尽 → _trip_daily() 重新 OPEN。正文异常不参与
        熔断判定（预算护栏只关心"成本是否超限"，不关心动作本身成败）。
        """
        # 1) 单次动作 token 上限（0 表示不设限）
        if self.max_single_action_tokens > 0 and estimated > self.max_single_action_tokens:
            logger.debug(
                "[学习预算] 分支1 单次上限拦截: action=%s estimated=%d max_single=%d",
                action_name, estimated, self.max_single_action_tokens,
            )
            return ("single_action_limit", float(self.max_single_action_tokens))
        # 2) 熔断状态（OPEN → cooldown 到期自动 HALF_OPEN 放行探测）
        if not self._breaker.allow_request():
            logger.debug(
                "[学习预算] 分支2 熔断拦截: action=%s estimated=%d breaker=%s",
                action_name, estimated, self._breaker.get_status(),
            )
            return ("circuit_open", self.recovery_seconds)
        # 3) 日预算（令牌桶不足 → 耗尽，显式熔断）
        if not self._bucket.try_acquire(estimated):
            logger.debug(
                "[学习预算] 分支3 日预算耗尽: action=%s estimated=%d max_daily=%d bucket=%s",
                action_name, estimated, self.max_daily_tokens, self._bucket.to_dict(),
            )
            self._trip_daily()
            return ("daily_exhausted", float(self.max_daily_tokens))
        # 日预算充足：若为半开探测，标记成功 → 熔断器恢复 CLOSED
        self._safe_record_result(True)
        logger.debug(
            "[学习预算] 三分支全部通过: action=%s estimated=%d（半开探测成功将恢复 CLOSED）",
            action_name, estimated,
        )
        return None

    def _trip_daily(self) -> None:
        """日预算耗尽 → 熔断器 OPEN（仅 enforce 模式改变状态；warn_only 只观察）"""
        if self.mode != "enforce":
            logger.debug(
                "[学习预算] 日预算耗尽但 mode=warn_only，不触发熔断（仅观察）")
            return
        try:
            self._breaker.force_open()
            self._breaker.record_failure()
        except Exception:
            pass
        with self._lock:
            self._tripped = True
        logger.warning(
            "[学习预算] 触发日预算熔断: force_open+record_failure（recovery_seconds=%s）",
            self.recovery_seconds,
        )

    def _safe_release(self, tokens: int) -> None:
        try:
            self._bucket.release(tokens)
        except Exception:
            pass

    def _safe_record_result(self, success: bool) -> None:
        """记录预算判定结果（半开探测成功 → CLOSED 恢复；耗尽 → 重新 OPEN）"""
        try:
            self._breaker.record_result(success)
        except Exception:
            pass

    def _log_exceeded(self, action_name: str, tokens: int, reason: str, limit: float) -> None:
        logger.warning(
            "[学习预算] 学习动作超限（mode=%s）: action=%s tokens=%d reason=%s limit=%s",
            self.mode, action_name, tokens, reason, limit,
        )


# ════════════════════════════════════════════════════════════
#  模块级单例
# ════════════════════════════════════════════════════════════

_global_budget: Optional[LearningBudget] = None
_global_budget_lock = threading.Lock()


def get_learning_budget() -> LearningBudget:
    """获取全局学习预算护栏单例（懒加载，配置按三层优先级读取）"""
    global _global_budget
    if _global_budget is None:
        with _global_budget_lock:
            if _global_budget is None:
                _global_budget = LearningBudget(config=_load_budget_config())
    return _global_budget


def reset_learning_budget() -> None:
    """重置全局学习预算单例（仅测试使用）"""
    global _global_budget
    with _global_budget_lock:
        if _global_budget is not None:
            try:
                _global_budget.reset()
            except Exception:
                pass
        _global_budget = None


__all__ = [
    "LearningBudget",
    "LearningBudgetExceeded",
    "get_learning_budget",
    "reset_learning_budget",
]
