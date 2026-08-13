"""预算管理（阶段 3 / D13 规格化）

【不易】预算为新增策略层，默认预算（各维度 None）不限制时行为与重构前完全一致；
  预算状态为实例级（BudgetManager 每实例独立累计），多循环并发无共享可变状态。
【变易】PlanBudget 各维度可独立配置（steps/iterations/seconds/tokens/cost），
  超限策略由调用方决定（ReActLoop 终止/征求用户、PlanExecutor 正常收尾）。
【简易】薄记账模块：累计计数 + 单点 check() 判定 + snapshot() 透出，
  不耦合业务语义；token 计数复用系统 memory/token_counter.py（不可用时回退字符/3 近似）。
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class BudgetStatus(Enum):
    """预算判定结果（按维度优先级返回首个超限状态）"""
    OK = "ok"
    EXCEEDED_STEPS = "exceeded_steps"
    EXCEEDED_ITERATIONS = "exceeded_iterations"
    EXCEEDED_SECONDS = "exceeded_seconds"
    EXCEEDED_TOKENS = "exceeded_tokens"
    EXCEEDED_COST = "exceeded_cost"


@dataclass
class PlanBudget:
    """计划预算数据类（各维度 None = 不限制）

    与既有直连配置键的映射（向后兼容阶段 1/3 早期实现）：
      timeout_seconds   -> max_seconds
      token_budget      -> max_tokens
      cost_budget       -> max_cost
    """
    max_steps: Optional[int] = None
    max_iterations: Optional[int] = None
    max_seconds: Optional[float] = None
    max_tokens: Optional[int] = None
    max_cost: Optional[float] = None

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]]) -> "PlanBudget":
        """从配置构建预算（嵌套 budget 段优先，直连键兼容）

        回滚开关：`budget.enabled: false` 时整体关闭（各维度视为不限制，
        行为与重构前完全一致，供 `planning.budget.enabled=false` 回滚）。
        """
        config = config or {}
        budget_cfg = config.get("budget")
        if not isinstance(budget_cfg, dict):
            budget_cfg = config
        if budget_cfg.get("enabled") is False:
            logger.warning("[预算] budget.enabled=false，预算整体关闭（各维度不限制）")
            return cls()
        return cls(
            max_steps=budget_cfg.get("max_steps"),
            max_iterations=budget_cfg.get("max_iterations"),
            max_seconds=budget_cfg.get("max_seconds", budget_cfg.get("timeout_seconds")),
            max_tokens=budget_cfg.get("max_tokens", budget_cfg.get("token_budget")),
            max_cost=budget_cfg.get("max_cost", budget_cfg.get("cost_budget")),
        )

    @property
    def enabled(self) -> bool:
        """是否存在任一限制维度"""
        return any(v is not None for v in (
            self.max_steps, self.max_iterations,
            self.max_seconds, self.max_tokens, self.max_cost,
        ))


class BudgetManager:
    """预算管理器：记账 + 判定 + 快照

    由调用方在迭代入口 / 工具调用后调用 record_* 记账、check() 判定；
    snapshot() 结果写入 ReActResult.final_state 与 Plan.metadata（可观测）。
    """

    def __init__(self, budget: Optional[PlanBudget] = None, token_counter=None,
                 token_price_per_1k: float = 0.002):
        """
        Args:
            budget: 预算数据类（默认全 None = 不限制）
            token_counter: 复用系统 memory/token_counter.TokenCounter；
                           未注入或计数异常时回退字符/3 近似
            token_price_per_1k: 每千 token 单价（USD），成本 = tokens/1000 × 单价
        """
        self.budget = budget or PlanBudget()
        self._token_counter = token_counter
        self._token_price_per_1k = token_price_per_1k
        self._start = time.monotonic()
        self._steps = 0
        self._iterations = 0
        self._tokens = 0
        self._cost = 0.0
        logger.info(
            "[预算] 管理器创建 | 维度: steps=%s iterations=%s seconds=%s tokens=%s cost=%s"
            " | 每千token单价=$%s | 计数器=%s",
            self.budget.max_steps, self.budget.max_iterations, self.budget.max_seconds,
            self.budget.max_tokens, self.budget.max_cost,
            self._token_price_per_1k,
            "注入" if self._token_counter is not None else "未注入(字符/3近似)",
        )

    # ── 生命周期 ──
    def start(self) -> None:
        """重置单次执行的计时与步数（每次 run/execute_plan 调用时调用，
        deadline/steps 按单次执行计；token/cost 为实例生命周期累计）"""
        self._start = time.monotonic()
        self._steps = 0
        self._iterations = 0
        logger.info("[预算] 开始本次执行记账（steps/iterations/elapsed 已重置）")

    # ── 记账 ──
    def record_step(self, n: int = 1) -> None:
        """记录执行步数（PlanExecutor 每任务调用）"""
        self._steps += max(0, n)
        logger.debug("[预算] 记步 +%s → 累计 %s 步", max(0, n), self._steps)

    def record_iteration(self, n: int = 1) -> None:
        """记录迭代次数（ReActLoop 每轮迭代调用）"""
        self._iterations += max(0, n)
        logger.debug("[预算] 记迭代 +%s → 累计 %s 次", max(0, n), self._iterations)

    def record_tokens(self, token_count: int) -> None:
        """记录 token 消耗（整数直加，同步折算成本）"""
        self._tokens += max(0, int(token_count))
        self._cost = self._tokens / 1000.0 * self._token_price_per_1k
        logger.debug(
            "[预算] 记 token +%s → 累计 %s | 成本 $%.6f", max(0, int(token_count)),
            self._tokens, self._cost,
        )

    def record_text(self, text: Any) -> None:
        """记录文本 token 消耗（经计数器；未注入/异常时字符/3 近似）"""
        self.record_tokens(self._count_tokens(text))

    def _count_tokens(self, text: Any) -> int:
        if not text:
            return 0
        if self._token_counter is not None:
            try:
                return self._token_counter.count(str(text))
            except Exception as exc:
                logger.debug("[预算] token 计数器异常(%s)，回退字符/3 近似", exc)
        return max(1, len(str(text)) // 3)

    # ── 判定 ──
    def check(self) -> BudgetStatus:
        """按维度优先级返回首个超限状态；未超限返回 OK"""
        status = self._check_status()
        if status != BudgetStatus.OK:
            logger.warning(
                "[预算] 超限判定=%s | 当前 steps=%s iterations=%s elapsed=%.3fs tokens=%s cost=$%.6f"
                " | 上限 steps=%s iterations=%s seconds=%s tokens=%s cost=%s",
                status.value, self._steps, self._iterations, self.elapsed_seconds,
                self._tokens, self._cost,
                self.budget.max_steps, self.budget.max_iterations, self.budget.max_seconds,
                self.budget.max_tokens, self.budget.max_cost,
            )
        else:
            logger.debug(
                "[预算] 判定通过(OK) | steps=%s iterations=%s elapsed=%.3fs tokens=%s cost=$%.6f",
                self._steps, self._iterations, self.elapsed_seconds, self._tokens, self._cost,
            )
        return status

    def _check_status(self) -> BudgetStatus:
        if self.budget.max_steps is not None and self._steps >= self.budget.max_steps:
            return BudgetStatus.EXCEEDED_STEPS
        if self.budget.max_iterations is not None and self._iterations >= self.budget.max_iterations:
            return BudgetStatus.EXCEEDED_ITERATIONS
        if self.budget.max_seconds is not None and self.elapsed_seconds >= self.budget.max_seconds:
            return BudgetStatus.EXCEEDED_SECONDS
        if self.budget.max_tokens is not None and self._tokens >= self.budget.max_tokens:
            return BudgetStatus.EXCEEDED_TOKENS
        if self.budget.max_cost is not None and self._cost >= self.budget.max_cost:
            return BudgetStatus.EXCEEDED_COST
        return BudgetStatus.OK

    @property
    def exceeded(self) -> bool:
        """是否已超任一预算"""
        return self.check() != BudgetStatus.OK

    # ── 状态透出 ──
    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._start

    @property
    def steps(self) -> int:
        return self._steps

    @property
    def iterations(self) -> int:
        return self._iterations

    @property
    def tokens(self) -> int:
        return self._tokens

    @property
    def cost(self) -> float:
        return self._cost

    # ── 快照（写入 ReActResult.final_state / Plan.metadata）──
    def snapshot(self) -> Dict[str, Any]:
        return {
            "steps": self._steps,
            "iterations": self._iterations,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "tokens": self._tokens,
            "cost": round(self._cost, 6),
        }
