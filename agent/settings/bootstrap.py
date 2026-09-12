"""覆盖层启动应用（TASK-S7-01；`needs_restart` 类开关的生效路径）

【为什么需要它】
    `hot` 类开关改完立即写运行态（`os.environ` / `ObservabilityConfig`）；
    `needs_restart` 类**不能**热改，只能等下一次进程启动。若没有本模块，
    这类开关就成了"改了但永远不生效"——正是任务书点名的 UI 五坑之一。

【零影响纪律】
    覆盖层文件默认不存在（gitignore 的运行时产物）。没有覆盖层时本模块
    **不做任何事**（不写 env、不动任何配置），因此对既有行为零影响
    （单测 `test_bootstrap_noop_without_overlay` 断言"未变更时零影响"）。

【调用点】
    `app_server.py` 注册路由前调用一次 `apply_overrides(log=True)`；
    幂等：同一进程重复调用只应用一次（除非 `force=True`）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from agent.settings.overrides import get_override_store
from agent.settings.registry import get_spec
from agent.settings.resolver import apply_override_to_runtime, resolve

logger = logging.getLogger(__name__)

#: 是否已应用（幂等标记）
_APPLIED = False


def overlay_exists() -> bool:
    """覆盖层文件是否存在（不存在 = 本功能对运行态零影响）"""
    return get_override_store().path.exists()


def apply_overrides(*, force: bool = False) -> Dict[str, Any]:
    """把覆盖层应用到运行态（幂等）

    Returns:
        `{"applied": [...], "skipped": [...], "overlay": path, "existed": bool}`
    """
    global _APPLIED
    store = get_override_store()
    result: Dict[str, Any] = {"overlay": str(store.path),
                              "existed": store.path.exists(),
                              "applied": [], "skipped": []}
    if _APPLIED and not force:
        result["note"] = "本进程已应用过（幂等跳过）"
        return result
    if not store.path.exists():
        _APPLIED = True
        result["note"] = ("未发现覆盖层文件 → 不做任何事"
                          "（对既有行为零影响）")
        return result

    for key in store.keys():
        spec = get_spec(key)
        if spec is None:
            result["skipped"].append({"key": key, "reason": "未登记（fail-closed）"})
            continue
        resolved = resolve(key, store=store)
        if resolved is None:                                 # pragma: no cover
            result["skipped"].append({"key": key, "reason": "解析失败"})
            continue
        if resolved.env_locked:
            # 运维显式注入的 env 优先于覆盖层 → 覆盖层被遮蔽（如实跳过）
            result["skipped"].append(
                {"key": key, "reason": f"被环境变量 {spec.env_name} 覆盖（env 优先）"})
            continue
        landing = apply_override_to_runtime(resolved, store=store)
        entry = {"key": key, "landing": landing.get("target", ""),
                 "applied": bool(landing.get("applied")),
                 "detail": landing.get("detail", "")}
        (result["applied"] if entry["applied"] else result["skipped"]).append(entry)

    _APPLIED = True
    if result["applied"] or result["skipped"]:
        logger.info("[Settings] 覆盖层启动应用：applied=%d skipped=%d",
                    len(result["applied"]), len(result["skipped"]))
    return result


def reset_bootstrap_state() -> None:
    """清掉幂等标记（测试隔离用）"""
    global _APPLIED
    _APPLIED = False


def overlay_env_summary() -> List[Dict[str, Any]]:
    """覆盖层对 env 的影响摘要（运维排查用；不含明文）"""
    store = get_override_store()
    out: List[Dict[str, Any]] = []
    for name in store.applied_env_names():
        out.append({"env_name": name,
                    "has_previous": store.env_backup(name) is not None})
    return out


__all__ = ["apply_overrides", "overlay_exists", "reset_bootstrap_state",
           "overlay_env_summary"]
