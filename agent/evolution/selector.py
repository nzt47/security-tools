"""候选生成与筛选 — 任务6 步骤2（修复策略库）

候选生成三来源:
  a) 任务4 的 repair_hints（诊断驱动，规则兜底）
  b) 策略库相似案例复用（同 task_type/同工具的既有策略）
  c) 可选 LLM 生成（evolution_llm_generate 开关控制；无 LLM 环境自动降级）

筛选（安全优先 + 综合分）:
  1. 淘汰 safety < 0.6 的候选（安全红线，【不易】）
  2. 综合分 = 0.4*safety + 0.3*utility + 0.2*trajectory_efficiency + 0.1*(1-over_rejection)
  3. 排序取 top3（近似帕累托：不引入前沿算法，保持【简易】）

对外接口:
  - Strategy: 入库策略结构（策略库只追加不删除，status 可标记 deprecated）
  - generate_candidates(): 候选生成（同步规则 + 复用；LLM 走独立 async 入口）
  - generate_llm_candidates(): 可选 LLM 候选生成（async）
  - select_strategies(): 安全红线 + 综合分排序取 top3
  - composite_score(): 综合分计算
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .defect_case import FailureCase

# ═══════════════════════════════════════════════════════════════
#  筛选常量
# ═══════════════════════════════════════════════════════════════

SAFETY_RED_LINE = 0.6          # 安全红线：safety < 0.6 必淘汰（【不易】）
TOP_N = 3                      # 综合分排序后取 top3

_W_SAFETY = 0.4
_W_UTILITY = 0.3
_W_TRAJECTORY = 0.2
_W_OVER_REJECTION = 0.1

STATUS_ACTIVE = "active"
STATUS_DEPRECATED = "deprecated"


# ═══════════════════════════════════════════════════════════════
#  策略数据模型（任务6 规格结构）
# ═══════════════════════════════════════════════════════════════

@dataclass
class Strategy:
    """入库修复策略（只追加不删除，deprecated 标记淘汰）"""
    strategy_id: str
    case_id: str
    prompt_patch: str                 # 提示词补丁（注入内容）
    param_patch: Dict[str, Any] = field(default_factory=dict)  # 参数补丁（如 {"fallback_tools": [...]}）
    scope: str = "global"             # global / task_type:<t> / tool:<工具名>
    source: str = "repair_hint"       # repair_hint / reuse / llm
    success_count: int = 0
    attempt_count: int = 0
    status: str = STATUS_ACTIVE       # active / deprecated
    created_at: float = field(default_factory=time.time)
    scores: Dict[str, float] = field(default_factory=dict)  # 生成时继承的案例评分（筛选用，不入库）

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "strategy_id": self.strategy_id,
            "case_id": self.case_id,
            "prompt_patch": self.prompt_patch,
            "param_patch": self.param_patch,
            "scope": self.scope,
            "source": self.source,
            "success_count": self.success_count,
            "attempt_count": self.attempt_count,
            "status": self.status,
            "created_at": self.created_at,
        }
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Strategy":
        return cls(
            strategy_id=data["strategy_id"],
            case_id=data.get("case_id", ""),
            prompt_patch=data.get("prompt_patch", ""),
            param_patch=data.get("param_patch", {}) or {},
            scope=data.get("scope", "global"),
            source=data.get("source", "repair_hint"),
            success_count=data.get("success_count", 0),
            attempt_count=data.get("attempt_count", 0),
            status=data.get("status", STATUS_ACTIVE),
            created_at=data.get("created_at", time.time()),
        )


# ═══════════════════════════════════════════════════════════════
#  综合分与筛选
# ═══════════════════════════════════════════════════════════════

def composite_score(scores: Dict[str, float]) -> float:
    """综合分 = 0.4*safety + 0.3*utility + 0.2*trajectory + 0.1*(1-over_rejection)"""
    safety = float(scores.get("safety", 0.0))
    utility = float(scores.get("utility", 0.0))
    trajectory = float(scores.get("trajectory_efficiency", 0.0))
    over_rejection = float(scores.get("over_rejection", 0.0))
    return (
        _W_SAFETY * safety
        + _W_UTILITY * utility
        + _W_TRAJECTORY * trajectory
        + _W_OVER_REJECTION * (1.0 - over_rejection)
    )


def select_strategies(
    candidates: List[Strategy],
    top_n: int = TOP_N,
    safety_red_line: float = SAFETY_RED_LINE,
) -> List[Strategy]:
    """安全红线 + 综合分排序取 top_n（【简易】近似帕累托）。

    【不易】safety < 安全红线的候选绝不返回（不进入 selected_strategies）。
    """
    survived = [c for c in candidates if c.scores.get("safety", 0.0) >= safety_red_line]
    survived.sort(key=lambda c: composite_score(c.scores), reverse=True)
    return survived[:top_n]


# ═══════════════════════════════════════════════════════════════
#  候选生成
# ═══════════════════════════════════════════════════════════════

def _new_strategy(
    case: FailureCase,
    prompt_patch: str,
    scope: str,
    source: str,
    param_patch: Optional[Dict[str, Any]] = None,
) -> Strategy:
    return Strategy(
        strategy_id=uuid.uuid4().hex[:12],
        case_id=case.case_id,
        prompt_patch=prompt_patch,
        param_patch=param_patch or {},
        scope=scope,
        source=source,
        scores=dict(case.scores),
    )


def _default_scope(case: FailureCase, tool_name: Optional[str]) -> str:
    """scope 推导：有工具名 → tool:<tool>；否则 task_type:<type>"""
    if tool_name:
        return f"tool:{tool_name}"
    return f"task_type:{case.task_type or 'unknown'}"


def generate_candidates(
    case: FailureCase,
    repair_hints: Optional[List[str]] = None,
    similar_strategies: Optional[List[Strategy]] = None,
    tool_name: Optional[str] = None,
) -> List[Strategy]:
    """候选生成（同步：来源 a 规则 repair_hints + 来源 b 库内相似复用）。

    LLM 候选（来源 c）走 generate_llm_candidates（async，由调用方在
    evolution_llm_generate=true 且持有 llm 时调用，结果追加后一并筛选）。
    """
    scope = _default_scope(case, tool_name)
    candidates: List[Strategy] = []

    # a) 诊断驱动的修复约束（repair_hints，规则兜底）
    hints = list(repair_hints or case.diagnosis.get("repair_hints") or [])
    for hint in hints:
        if hint and hint not in [c.prompt_patch for c in candidates]:
            candidates.append(
                _new_strategy(case, hint, scope, source="repair_hint")
            )

    # b) 相似案例复用（同 scope 的既有策略直接复用，不重复造轮子）
    seen = {c.prompt_patch for c in candidates}
    for s in similar_strategies or []:
        src = s if isinstance(s, Strategy) else Strategy.from_dict(s)
        if src.prompt_patch and src.prompt_patch not in seen and src.status != STATUS_DEPRECATED:
            candidates.append(
                _new_strategy(
                    case, src.prompt_patch,
                    scope, source="reuse",
                    param_patch=dict(src.param_patch),
                )
            )
            seen.add(src.prompt_patch)

    return candidates


async def generate_llm_candidates(
    case: FailureCase,
    llm_generator: Callable[[FailureCase], Any],
    tool_name: Optional[str] = None,
    max_candidates: int = 3,
) -> List[Strategy]:
    """候选生成（来源 c：可选 LLM 生成，evolution_llm_generate 开关控制）。

    llm_generator 为 async 函数：接收 FailureCase，返回策略文本列表（List[str]）。
    失败/异常不阻塞主流程（【变易】LLM 可关；【简易】规则兜底）。
    """
    scope = _default_scope(case, tool_name)
    try:
        raw = await llm_generator(case)
    except Exception:
        return []
    candidates: List[Strategy] = []
    if isinstance(raw, str):
        raw = [raw]
    for text in (raw or [])[:max_candidates]:
        text = str(text).strip()
        if text:
            candidates.append(
                _new_strategy(case, text, scope, source="llm")
            )
    return candidates
