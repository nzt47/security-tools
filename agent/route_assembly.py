"""路由装配失败登记与结算（2026-10-03 · 审计 K8 / H-2 机制化修复）。

【解决什么】app_server.py 里路由模块逐个 try/except 注册，失败只打一行 logger.error 就继续
启动。实测本仓有 **20 个**这样的失败点，而当前实际失败数为 **0** —— 也就是说这条路径平时
完全静默，没人知道它一旦触发会怎样。而它真的触发过：agent/api_gateway_flask.py 缺失导致
/api/open/* 与 /api/docs **整个 API 面没注册**，进程却照样报告健康（审计 H-2）。

【怎么做】20 处失败统一登记，装配结束时一次性结算：
  - 默认 strict：有失败即抛错 —— 宁可不启动，也不要「看起来健康但少了一半 API」；
  - YUNSHU_ROUTE_ASSEMBLY_STRICT=0 显式降级：打印完整清单后继续启动（留给排障）。

【为什么单独成模块】本模块不 import 任何业务依赖，可被单测直接导入（约 0ms），
无需付 app_server 的 53.5s 导入代价 —— 与 agent/server_auth.path_is_allowlisted 同一考虑，
其 docstring 已记录过这条理由。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

__all__ = ["record", "failures", "reset", "strict_enabled", "finalize", "summary"]

#: 装配期失败登记（顺序即发生顺序）
_FAILURES: List[Dict[str, str]] = []

_STRICT_ENV = "YUNSHU_ROUTE_ASSEMBLY_STRICT"
_FALSY = ("0", "false", "no", "off")


def record(label: str, exc: BaseException) -> None:
    """登记一处装配失败。label 用可读域名（如「技能管理路由」），不要传异常对象。"""
    _FAILURES.append({"label": str(label), "error": type(exc).__name__ + ": " + str(exc)})


def failures() -> List[Dict[str, str]]:
    """返回失败清单的**副本**（调用方改不动内部状态）。"""
    return [dict(x) for x in _FAILURES]


def reset() -> None:
    """清空登记（测试隔离用；生产路径不需要）。"""
    _FAILURES.clear()


def strict_enabled() -> bool:
    """默认 **strict**；只有显式写 0 / false / no / off 才降级。

    【为什么默认从严】失败即意味着某个 API 面整体缺失。让它静默继续，正是 H-2 的成因。
    """
    return str(os.environ.get(_STRICT_ENV, "1")).strip().lower() not in _FALSY


def finalize(strict: Optional[bool] = None) -> Dict[str, object]:
    """装配结束结算。

    Returns:
        {"total": int, "strict": bool, "failed": [...]}

    Raises:
        RuntimeError: strict 且存在失败时（含完整清单，便于一眼定位）。
    """
    use_strict = strict_enabled() if strict is None else bool(strict)
    items = failures()
    result: Dict[str, object] = {"total": len(items), "strict": use_strict, "failed": items}
    if not items:
        return result
    detail = "; ".join(x["label"] + " -> " + x["error"] for x in items)
    if use_strict:
        raise RuntimeError(
            "路由装配失败 " + str(len(items)) + " 处，且 " + _STRICT_ENV + " 未显式降级 ⇒ 拒绝启动。"
            "失败清单：" + detail +
            "。若确认可接受缺失，请设 " + _STRICT_ENV + "=0 后重启（会打印完整清单并继续）。"
        )
    return result


def summary() -> str:
    """一行摘要（供启动日志）。"""
    items = failures()
    if not items:
        return "全部路由模块装配成功（0 处失败）"
    return "路由装配失败 " + str(len(items)) + " 处：" + "、".join(x["label"] for x in items)