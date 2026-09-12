"""预算令牌与轮次上限（TASK-S7-02 边界 ④）

【任务定位】
    任务书 §一 边界 ④：「单次修复有 token 预算与最多 N 轮（**防止无限自我修改**）」。
    这是自修复这件事**最重要的安全阀**——没有它，「能修」会退化成「一直改到看起来
    能过为止」，而那正是把自欺写成自动化的方式。

【不易（超限即停，不是"尽力而为"）】
    - ``MAX_ROUNDS`` 是硬上限：``next_round()`` 到达上限时**抛异常**，不是返回
      False 让调用方有机会忽略。
    - ``BUDGET_TOKENS`` 同理：``consume()`` 超额抛 ``BudgetExceeded``。
    - 两条超限都携带**可审计的原因码**（``models.REASON_BUDGET_EXCEEDED`` /
      ``REASON_ROUNDS_EXCEEDED``），以便「超限即停并留证」在报告里是机械可查的。

【变易】
    预算是**可变状态**（与 policy 的不可变阈值分开）：``RepairPolicy`` 是「规则」，
    ``RepairBudget`` 是「这一次的消耗」。混在一起会让策略对象带状态，进而在并行
    修复之间串味。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.repair.models import (
    REASON_BUDGET_EXCEEDED,
    REASON_ROUNDS_EXCEEDED,
)
from agent.repair.policy import RepairPolicy


class BudgetExceeded(RuntimeError):
    """预算/轮次超限（**硬停**）"""

    def __init__(self, code: str, message: str, *, detail: Optional[Dict[str, Any]] = None):
        self.code = str(code)
        self.detail: Dict[str, Any] = dict(detail or {})
        super().__init__(message)


@dataclass
class RepairBudget:
    """单次修复的预算与轮次账本

    Attributes:
        token_limit: token 上限（来自策略）。
        max_rounds: 轮次上限（来自策略）。
        tokens_used: 已消耗 token。
        rounds_used: 已开始轮次数。
        notes: 消耗过程中的如实记录（超限/回退/未计量披露）。
    """

    token_limit: int = 0
    max_rounds: int = 1
    tokens_used: int = 0
    rounds_used: int = 0
    notes: List[str] = field(default_factory=list)

    @classmethod
    def from_policy(cls, policy: RepairPolicy) -> "RepairBudget":
        """按策略建立账本"""
        return cls(token_limit=int(policy.budget_tokens),
                   max_rounds=int(policy.max_rounds))

    # ── 读 ──

    @property
    def tokens_left(self) -> int:
        return max(0, int(self.token_limit) - int(self.tokens_used))

    @property
    def rounds_left(self) -> int:
        return max(0, int(self.max_rounds) - int(self.rounds_used))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "token_limit": int(self.token_limit),
            "tokens_used": int(self.tokens_used),
            "tokens_left": self.tokens_left,
            "max_rounds": int(self.max_rounds),
            "rounds_used": int(self.rounds_used),
            "rounds_left": self.rounds_left,
            "notes": list(self.notes),
        }

    # ── 写（硬闸）──

    def start_round(self) -> int:
        """开始新一轮；超上限抛 ``BudgetExceeded``

        Returns:
            本轮序号（1 基）。
        """
        if int(self.rounds_used) + 1 > int(self.max_rounds):
            raise BudgetExceeded(
                REASON_ROUNDS_EXCEEDED,
                f"轮次超限：已达上限 {self.max_rounds} 轮（防无限自我修改）",
                detail={"rounds_used": int(self.rounds_used),
                        "max_rounds": int(self.max_rounds)})
        self.rounds_used = int(self.rounds_used) + 1
        return self.rounds_used

    def consume(self, tokens: Any, *, source: str = "") -> int:
        """记账 token 消耗；超额抛 ``BudgetExceeded``

        Args:
            tokens: 本次消耗（非整数/负数/None → 记 0 并留 note，**不猜**）。
            source: 消耗来源（``subagent`` / ``llm`` 等，便于人读）。

        Returns:
            本次实际记账的 token 数。
        """
        try:
            amount = int(tokens)
        except (TypeError, ValueError):
            self.notes.append(f"token 消耗不可解析（{source or '未知来源'}={tokens!r}）→ 记 0")
            amount = 0
        if amount < 0:
            self.notes.append(f"token 消耗为负（{source or '未知来源'}={amount}）→ 记 0")
            amount = 0
        if amount == 0:
            self.notes.append(f"token 消耗未计量（{source or '未知来源'}）——如实记 0，不估算")
        self.tokens_used = int(self.tokens_used) + amount
        if int(self.tokens_used) > int(self.token_limit):
            raise BudgetExceeded(
                REASON_BUDGET_EXCEEDED,
                f"token 预算超限：已用 {self.tokens_used} > 上限 {self.token_limit}"
                f"（来源 {source or '未知'}）",
                detail={"tokens_used": int(self.tokens_used),
                        "token_limit": int(self.token_limit), "source": source})
        return amount

    def check_headroom(self, *, needed: int = 1) -> None:
        """派工前预检（余额不足即抛，避免"派出去才发现没钱"）"""
        if int(self.token_limit) - int(self.tokens_used) < int(needed):
            raise BudgetExceeded(
                REASON_BUDGET_EXCEEDED,
                f"token 余额不足：剩余 {self.tokens_left} < 需要 {needed}",
                detail={"tokens_left": self.tokens_left, "needed": int(needed)})


__all__ = ["BudgetExceeded", "RepairBudget", "REASON_BUDGET_EXCEEDED",
           "REASON_ROUNDS_EXCEEDED"]
