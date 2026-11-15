# -*- coding: utf-8 -*-
"""主线（分身）的**默认上下文窗口**解析 —— 单一事实源。

【为什么需要】不同分身干的事差一个量级，窗口需求也差一个量级：
日常对话 16K 就够（历史太多反而稀释注意力），代码/知识线要 64K–128K。
把默认窗口**声明在主线档案里**（`data/agent_lines/*.yaml` 的 `context_window_tokens`）
比"每个会话手调滑块"可复现，也让「换了分身为什么上下文变短」能在来源里说清。

【优先级（高 → 低）】
  1. **运行时覆盖**：面板滑块 / `POST /api/context/config` / 环境变量 —— 进程内生效，重启回落；
  2. **本主线声明**：`context_window_tokens`（本模块负责取，并按模型能力夹紧）；
  3. **内置默认**：`_MEMORY_TOKEN_LIMIT_DEFAULT`（131072）。

【为什么不在这里改"实际生效值"】本模块只做**纯解析**（不 import app_server / orchestrator）：
"把值推给正在跑的编排器"是调用方的事（见 `plugins/memory._push_runtime_window` 与
`server_routes/routes_agent_lines.py` 的激活主线切换）。这样解析逻辑可单测，也不会形成反向依赖。
"""
from __future__ import annotations

from typing import Optional, Tuple

#: 窗口下界（低于它连系统提示词 + 最近几轮都装不下；实测本部署一次请求约 8.8k）
WINDOW_MIN = 8192
#: 窗口上界（= deepseek-flash 实测 context_window；超过模型的标称长度没有意义）
WINDOW_MAX = 1048576


def clamp_window(value: int) -> int:
    """把窗口值夹进 [WINDOW_MIN, WINDOW_MAX]（非正数视为不可用 ⇒ 返回下界）"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return WINDOW_MIN
    return max(WINDOW_MIN, min(WINDOW_MAX, n))


def line_window(line_id: Optional[str] = None) -> Tuple[int, str]:
    """某条主线声明的窗口 → `(值, 来源)`；未声明/读不到 → `(0, "")`

    读不到（档案不存在 / YAML 坏 / 字段类型错）**一律当未声明**，绝不猜：
    窗口这种"用错了就是烧钱或截断"的参数，宁可回落全局默认也要能说清来源。
    """
    try:
        from agent.lines.registry import get_line_registry
        lid = str(line_id or "").strip() or (get_line_registry().get_active() or "")
        if not lid:
            return 0, ""
        profile = get_line_registry().load(lid)
        if profile is None:
            return 0, ""
        raw = int(getattr(profile, "context_window_tokens", 0) or 0)
        if raw <= 0:
            return 0, ""
        return clamp_window(raw), f"line:{lid}({clamp_window(raw)})"
    except Exception:       # noqa: BLE001 解析失败 ⇒ 如实回落"未声明"
        return 0, ""


def resolve_window(runtime_override: Optional[int], default: int,
                   line_id: Optional[str] = None) -> Tuple[int, str]:
    """按优先级给出**生效窗口**与**可复核来源**

    Args:
        runtime_override: 面板/接口/环境变量给的覆盖值（None 或 0 ⇒ 没有覆盖）。
        default: 内置默认（`_MEMORY_TOKEN_LIMIT_DEFAULT`）。
        line_id: 指定主线（默认取 `_active.json` 的激活主线）。
    """
    if runtime_override:
        value = int(runtime_override)
        return value, f"runtime_override({value})"
    declared, source = line_window(line_id)
    if declared:
        return declared, source
    return int(default), f"builtin_default({int(default)})"


def apply_window_to(orchestrator: object, default: int) -> Optional[Tuple[int, str]]:
    """把「当前激活主线声明的窗口」推给**正在跑的**编排器（鸭子类型，不 import 主对象）

    【为什么要有这一步】窗口在 `lifecycle_manager` 里是**启动时**算出来的属性；
    切换主线（`POST /api/agent-lines/active`）不重启进程，若不同步推一次，
    面板显示的来源会变、但真正组装上下文用的还是旧值 —— 同一个旋钮两套账。

    Returns:
        `(值, 来源)` = 已推；`None` = 没推（编排器不可用 / 用户设了运行时覆盖）。
    """
    if orchestrator is None or not hasattr(orchestrator, "_memory_token_limit"):
        return None
    if getattr(orchestrator, "_memory_token_limit_override", False):
        return None         # 运行时覆盖优先：切主线不该把人手调的窗口改回去
    value, source = resolve_window(None, default)
    try:
        orchestrator._memory_token_limit = int(value)
        orchestrator._memory_token_limit_source = source
        memory = getattr(orchestrator, "_memory", None)
        if memory is not None and hasattr(memory, "_token_limit"):
            memory._token_limit = int(value)
    except Exception:       # noqa: BLE001 推送失败不得让接口 500（值仍会在下次启动生效）
        return None
    return int(value), source


__all__ = ["WINDOW_MIN", "WINDOW_MAX", "clamp_window", "line_window",
           "resolve_window", "apply_window_to"]
