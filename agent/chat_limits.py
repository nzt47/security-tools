"""对话预算的**单一口径**：单次回复上限 + 上下文预算

【为什么必须收口】同一次对话有**两条入口**，各自算过一遍这两个数字：

  · 编排层（``/api/chat``）：``orchestrator._call_llm`` 里按模型名硬编码 8192/16384；
  · 工作台流式（``/api/chat/stream``，**主 UI 的对话链路**）：``plugins/chat.py`` 里把
    ``max_tokens=2048`` **写死**在请求里，上下文写死"最近 8 条消息"。

2026-10-02 实测后果：界面上把「单次回复」调到 16384、窗口调到 131072，
而**主 UI 的回复仍被卡在 2048**（模型的 1/192）、历史仍只带 8 条 ——
旋钮看着生效、实际只对另一条路生效。这正是本仓反复出现的"同一次对话换个入口就换一套口径"。

【不易】三条规则（与 orchestrator 的既有实现同源、逐字一致）：
  1. **配置优先**：``memory.per_message_recv_limit`` > 0 ⇒ 用它；否则按模型名分档；
  2. **单调不减**：结果不低于该分档下限 ⇒ 配置缺失**永远不会**让回复比以前更短；
  3. **硬上限收敛**：不超过 ``CP_CHAT_MAX_OUTPUT_CEILING``（缺省 131072）⇒ 一个手滑的
     大数字不会把 provider 打回 400（deepseek-flash 实测 ``max_output_tokens=393216``）。
"""
from __future__ import annotations

import os
from typing import Any, Optional

#: 命中即按"大输出模型"对待的模型名片段（沿用 orchestrator 原名单）
LARGE_OUTPUT_MODEL_HINTS = ("pro", "ultra", "reasoner", "opus",
                            "claude-4", "gpt-4-turbo", "o1", "o3")

#: 模型名分档的两档（原 orchestrator 取值，保留为**下限**）
MAX_OUTPUT_TIER_LARGE = 16384
MAX_OUTPUT_TIER_SMALL = 8192

#: 单次回复的硬上限缺省值（可用 ``CP_CHAT_MAX_OUTPUT_CEILING`` 覆盖）
MAX_OUTPUT_CEILING_DEFAULT = 131072

#: 上下文预算至少要给历史留出的量（窗口再小也要能接上"上一句话"）
MIN_CONTEXT_BUDGET = 1024


def tier_floor(model: Optional[str]) -> int:
    """按模型名给出单次回复的**下限**档位（8192 / 16384）"""
    name = (model or "").lower()
    return (MAX_OUTPUT_TIER_LARGE
            if any(k in name for k in LARGE_OUTPUT_MODEL_HINTS)
            else MAX_OUTPUT_TIER_SMALL)


def output_ceiling(env: Any = None) -> int:
    """单次回复硬上限（环境变量可覆盖；非法值回落缺省）"""
    source = os.environ if env is None else env
    try:
        return int(source.get("CP_CHAT_MAX_OUTPUT_CEILING") or MAX_OUTPUT_CEILING_DEFAULT)
    except (TypeError, ValueError, AttributeError):
        return MAX_OUTPUT_CEILING_DEFAULT


def resolve_max_output_tokens(configured: Any, model: Optional[str], *,
                              floor: Optional[int] = None,
                              ceiling: Optional[int] = None) -> int:
    """配置值 / 模型档位 → 可直接写进请求体的 ``max_tokens``

    Args:
        configured: ``memory.per_message_recv_limit``（非正整数/垃圾值一律按"未配置"处理）。
        model: 本次请求实际下发的模型名（用于分档下限）。
        floor: 覆盖下限；缺省用 :func:`tier_floor`。工作台流式路径的历史下限是 2048，
            但**有意**与编排层对齐到同一档（见模块 docstring）。
        ceiling: 覆盖硬上限；缺省取 :func:`output_ceiling`。

    Returns:
        恒为正整数。
    """
    try:
        want = int(configured or 0)
    except (TypeError, ValueError):
        want = 0
    low = int(floor) if floor is not None else tier_floor(model)
    high = int(ceiling) if ceiling is not None else output_ceiling()
    if high < low:
        # 荒谬配置（上限低于下限）不得把回复顶成 0：下限优先，保证"能正常回答"
        high = low
    if want <= 0:
        want = low
    return max(low, min(want, high))


def resolve_context_budget(window: Any, *, overhead_tokens: int = 0,
                           max_output_tokens: int = 0,
                           margin_tokens: int = 512) -> int:
    """组装窗口 → **留给对话历史**的 token 预算

    这是"窗口 → 预算"的推导关系（此前根本不存在，只有写死的"最近 8 条消息"）：

        budget = 窗口 − 固定开销(系统提示 + 工具 schema) − 单次回复上限 − 余量

    Args:
        window: 编排窗口（``_memory_token_limit``）。非正数视为不可得 ⇒ 返回 0（调用方
            据此**保持原行为**，不要拿一个假预算去截历史）。
        overhead_tokens: 系统提示 + 工具定义的实测 token（调用方负责量）。
        max_output_tokens: 本次请求的 ``max_tokens``（输出也要占窗口）。
        margin_tokens: 安全余量（各种口径差）。

    Returns:
        历史预算（>= :data:`MIN_CONTEXT_BUDGET`）；窗口不可得时返回 0。
    """
    try:
        win = int(window or 0)
    except (TypeError, ValueError):
        win = 0
    if win <= 0:
        return 0
    budget = win - max(0, int(overhead_tokens)) - max(0, int(max_output_tokens)) - max(0, int(margin_tokens))
    return max(MIN_CONTEXT_BUDGET, budget)


__all__ = ["LARGE_OUTPUT_MODEL_HINTS", "MAX_OUTPUT_TIER_LARGE", "MAX_OUTPUT_TIER_SMALL",
           "MAX_OUTPUT_CEILING_DEFAULT", "MIN_CONTEXT_BUDGET", "tier_floor",
           "output_ceiling", "resolve_max_output_tokens", "resolve_context_budget"]