"""消化链相关开关的**只读快照**与前后对比（TASK-S7-05 步骤 1 / 步骤 5）

【为什么需要它】

v7.2 的硬约束是"**一切自动化开关默认关闭**"，而内化演示必须**显式开启**灰度
开关。开关一旦开了就必须能证明"**开了什么、生效值是什么、结束时是否复位**"——
只记录"我设了 X=1"不够：`shadow.shadow_enabled()` / `budget_from_env()` /
`resolve_gray_policy()` / `resolve_judge()` 都会对非法值**回退默认**，真正的
生效值必须由引擎自己算出来才算证据（延续 S7-01「不谎报开关」纪律）。

本模块因此提供：

- `switch_snapshot()`：原始 env 值 + **引擎算出的生效值**（含回退说明）双列；
- `diff_snapshots()`：前后对比（新增/删除/变更），供"演示后已复位"留证；
- `format_markdown()`：可直接贴进报告的表格。

**只读**：本模块不设置、不修改、不写入任何开关，也不 import 会写运行时的模块。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: 本演示关心的开关（**逐条给出归属模块与默认值**，便于复核"默认关闭"）
SWITCH_KEYS: Sequence[str] = (
    # 灰度 / 内化链
    "CP_DIGESTION_SHADOW_ENABLED",
    "CP_DIGESTION_SHADOW_BUDGET_RATIO",
    "CP_DIGESTION_SHADOW_BUDGET_CAP",
    "CP_DIGESTION_SHADOW_MIN_BUDGET",
    "CP_DIGESTION_GRAY_ENABLED",
    "CP_DIGESTION_GRAY_RATIO",
    "CP_DIGESTION_JUDGE",
    "CP_DIGESTION_JUDGE_PROVIDER",
    "CP_DIGESTION_JUDGE_MODEL",
    "CP_DIGESTION_REPROBE_ENABLED",
    "CP_DIGESTION_INTERNALIZE_ENABLED",
    "CP_DIGESTION_NATIVE_UNIT_COST_CENTS",
    "CP_DIGESTION_NATIVE_INVESTMENT_CENTS",
    # 回放沙箱配额
    "CP_DIGESTION_SANDBOX_DENY_EXTERNAL",
    "CP_DIGESTION_SANDBOX_MAX_STEPS",
    "CP_DIGESTION_SANDBOX_MAX_EXTERNAL_CALLS",
    # 运行时目录（演示必须显式传入，不得依赖隐式默认）
    "CP_EVENTS_DIR",
    "CP_DIGESTION_CASE_DIR",
    "CP_DIGESTION_CASE_BACKEND",
    "CP_DIGESTION_SHADOW_DIR",
    "CP_DIGESTION_PROMOTE_DIR",
    # 审计链
    "AUDIT_CHAIN_ENABLED",
    "AUDIT_DB_PATH",
    # 成本刹车联动（S5-03；默认不启用 ⇒ 预算系数恒 1.0）
    "CP_BUDGET_FASTING",
    "CP_BUDGET_SHADOW_FACTOR_FASTING",
)

#: 各开关的归属与默认（写进快照，使"默认关闭"这件事可被独立核对）
SWITCH_OWNERS: Dict[str, Tuple[str, str]] = {
    "CP_DIGESTION_SHADOW_ENABLED": ("agent/digestion/shadow.py::SHADOW_ENABLE_ENV",
                                    "false（默认关闭）"),
    "CP_DIGESTION_SHADOW_BUDGET_RATIO": ("agent/digestion/shadow.py::SHADOW_BUDGET_RATIO_ENV",
                                         "0.15"),
    "CP_DIGESTION_SHADOW_BUDGET_CAP": ("agent/digestion/shadow.py::SHADOW_BUDGET_CAP_ENV",
                                       "50"),
    "CP_DIGESTION_SHADOW_MIN_BUDGET": ("agent/digestion/shadow.py::SHADOW_MIN_BUDGET_ENV",
                                       "1"),
    "CP_DIGESTION_GRAY_ENABLED": ("agent/digestion/shadow.py::GRAY_ENABLE_ENV",
                                  "false（未显式给出灰度阈值即不启用）"),
    "CP_DIGESTION_GRAY_RATIO": ("agent/digestion/shadow.py::GRAY_RATIO_ENV", "0.05"),
    "CP_DIGESTION_JUDGE": ("agent/digestion/shadow.py::JUDGE_MODE_ENV", "auto"),
    "CP_DIGESTION_JUDGE_PROVIDER": ("agent/digestion/shadow.py::JUDGE_PROVIDER_ENV", "（空）"),
    "CP_DIGESTION_JUDGE_MODEL": ("agent/digestion/shadow.py::JUDGE_MODEL_ENV", "（空）"),
    "CP_DIGESTION_REPROBE_ENABLED": ("agent/digestion/gate.py::REPROBE_ENABLE_ENV",
                                     "false（默认关闭）"),
    "CP_DIGESTION_INTERNALIZE_ENABLED": ("agent/digestion/internalize.py::SCHEDULE_ENABLE_ENV",
                                         "false（默认关闭）"),
    "CP_DIGESTION_NATIVE_UNIT_COST_CENTS": ("agent/digestion/internalize.py::NATIVE_UNIT_COST_ENV",
                                            "0.0"),
    "CP_DIGESTION_NATIVE_INVESTMENT_CENTS": ("agent/digestion/internalize.py::INVESTMENT_ENV",
                                             "0.0"),
    "CP_DIGESTION_SANDBOX_DENY_EXTERNAL": ("agent/digestion/sandbox.py::SandboxQuota.from_env",
                                           "false（未设置即允许模拟外部调用）"),
    "CP_DIGESTION_SANDBOX_MAX_STEPS": ("agent/digestion/sandbox.py::SandboxQuota", "32"),
    "CP_DIGESTION_SANDBOX_MAX_EXTERNAL_CALLS": ("agent/digestion/sandbox.py::SandboxQuota", "8"),
    "CP_EVENTS_DIR": ("agent/observability/events.py::ENV_DIR",
                      "<repo>/data/events（相对代码根，非 cwd）"),
    "CP_DIGESTION_CASE_DIR": ("agent/digestion/cases.py::CASE_ROOT_ENV",
                              "<repo>/data/digestion/cases"),
    "CP_DIGESTION_CASE_BACKEND": ("agent/digestion/cases.py::CASE_BACKEND_ENV", "json"),
    "CP_DIGESTION_SHADOW_DIR": ("agent/digestion/shadow.py::SHADOW_DIR_ENV",
                                "<repo>/data/digestion/shadow"),
    "CP_DIGESTION_PROMOTE_DIR": ("agent/digestion/internalize.py::PROMOTE_DIR_ENV",
                                 "<repo>/data/digestion/promote_pr"),
    "AUDIT_CHAIN_ENABLED": ("agent/audit/facade.py::_ENV_ENABLED", "1（默认开启）"),
    "AUDIT_DB_PATH": ("agent/audit/facade.py::_ENV_DB_PATH",
                      "<repo>/data/audit/audit_chain.db"),
    "CP_BUDGET_FASTING": ("agent/monitoring/cost_brake.py", "false（默认关闭）"),
    "CP_BUDGET_SHADOW_FACTOR_FASTING": ("agent/monitoring/cost_brake.py", "0.0"),
}


def _raw_value(env: Mapping[str, str], key: str) -> Optional[str]:
    """原始 env 值（**未设置**记 ``None``，不记空串 —— 二者含义不同）"""
    if key not in env:
        return None
    return str(env.get(key))


def effective_switches(*, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """**引擎算出的生效值**（含回退说明；引擎不可用即如实标注，不臆造）

    刻意调用引擎自己的解析函数（而不是在本模块里重算），使"生效来源"与真实
    运行路径逐字一致 —— 这正是 S7-01「不谎报开关」纪律的落地方式。
    """
    source = dict(os.environ) if env is None else dict(env)
    out: Dict[str, Any] = {}
    try:
        from .shadow import (JUDGE_MODE_ENV, budget_from_env, resolve_gray_policy,
                             resolve_judge, shadow_enabled)
        enabled, reason = shadow_enabled(reason=True, env=source)
        out["shadow_enabled"] = {"value": bool(enabled), "source": "shadow.shadow_enabled()",
                                 "detail": reason}
        out["shadow_budget"] = {"value": budget_from_env(source), "source":
                                "shadow.budget_from_env()",
                                "detail": "min(日均 × ratio, cap)，低流量保底 min_budget"}
        out["gray_policy"] = {"value": resolve_gray_policy(env=source),
                              "source": "shadow.resolve_gray_policy()",
                              "detail": "未显式给出 shadow_config 时按 env/默认"}
        judge = resolve_judge(str(source.get(JUDGE_MODE_ENV, "") or ""), env=source)
        out["judge_kind"] = {"value": judge.kind, "source": "shadow.resolve_judge()",
                             "detail": judge.detail}
    except Exception as e:  # noqa: BLE001  引擎不可用 ⇒ 如实标注，不猜
        out["shadow_enabled"] = {"value": None, "source": "unavailable",
                                 "detail": f"{type(e).__name__}: {e}"}
    try:
        from . import internalize as I
        out["internalize_schedule_enabled"] = {
            "value": str(source.get(I.SCHEDULE_ENABLE_ENV, "") or "").strip().lower()
            in ("1", "true", "yes", "on"),
            "source": "internalize.SCHEDULE_ENABLE_ENV（默认关闭）", "detail": ""}
        out["digest_thresholds"] = {
            "value": {"digest_count_min": I.DIGEST_COUNT_MIN,
                      "monthly_samples_min": I.MONTHLY_SAMPLES_MIN,
                      "success_rate_ratio": I.SUCCESS_RATE_RATIO,
                      "p99_ratio": I.P99_RATIO,
                      "veto_conditions": list(I.VETO_CONDITIONS)},
            "source": "internalize 模块常量（六条件门槛，不随演示下调）", "detail": ""}
    except Exception as e:  # noqa: BLE001
        out["digest_thresholds"] = {"value": None, "source": "unavailable",
                                    "detail": f"{type(e).__name__}: {e}"}
    try:
        from .gate import GATE_REPLAY_MIN, GATE_SUCCESS_RATE_RATIO
        out["gate_thresholds"] = {
            "value": {"replay_min": GATE_REPLAY_MIN,
                      "success_rate_ratio": GATE_SUCCESS_RATE_RATIO},
            "source": "gate 模块常量（验收门四条件，不随演示下调）", "detail": ""}
    except Exception as e:  # noqa: BLE001
        out["gate_thresholds"] = {"value": None, "source": "unavailable",
                                  "detail": f"{type(e).__name__}: {e}"}
    try:
        from .shadow import CLOCK_WALL
        out["shadow_clock"] = {"value": CLOCK_WALL,
                               "source": "shadow.CLOCK_WALL（M2 真实墙钟口径）",
                               "detail": ""}
    except Exception:  # noqa: BLE001
        out["shadow_clock"] = {"value": "", "source": "unavailable", "detail": ""}
    return out


def switch_snapshot(*, env: Optional[Mapping[str, str]] = None,
                    label: str = "") -> Dict[str, Any]:
    """完整快照：``{label, raw, unset, effective, owners}``"""
    source = dict(os.environ) if env is None else dict(env)
    raw = {key: _raw_value(source, key) for key in SWITCH_KEYS}
    effective = effective_switches(env=source)
    return {
        "label": str(label or ""),
        "raw": raw,
        "set": {k: v for k, v in raw.items() if v is not None},
        "unset": [k for k, v in raw.items() if v is None],
        "effective": effective,
        "owners": {k: {"ref": SWITCH_OWNERS[k][0], "default": SWITCH_OWNERS[k][1]}
                   for k in SWITCH_KEYS if k in SWITCH_OWNERS},
    }


def diff_snapshots(before: Mapping[str, Any],
                   after: Mapping[str, Any]) -> Dict[str, Any]:
    """两次快照对比（新增/删除/变更；``identical`` 即"已复位"的机器判据）"""
    b = dict(before.get("raw") or {})
    a = dict(after.get("raw") or {})
    keys = sorted(set(b) | set(a))
    added = [k for k in keys if b.get(k) is None and a.get(k) is not None]
    removed = [k for k in keys if b.get(k) is not None and a.get(k) is None]
    changed = [{"key": k, "before": b.get(k), "after": a.get(k)}
               for k in keys if b.get(k) is not None and a.get(k) is not None
               and b.get(k) != a.get(k)]
    eff_b = dict(before.get("effective") or {})
    eff_a = dict(after.get("effective") or {})
    eff_changed = [{"name": k, "before": (eff_b.get(k) or {}).get("value"),
                    "after": (eff_a.get(k) or {}).get("value")}
                   for k in sorted(set(eff_b) | set(eff_a))
                   if (eff_b.get(k) or {}).get("value")
                   != (eff_a.get(k) or {}).get("value")]
    return {
        "identical": not added and not removed and not changed and not eff_changed,
        "added": added, "removed": removed, "changed": changed,
        "effective_changed": eff_changed,
        "before_label": str(before.get("label") or ""),
        "after_label": str(after.get("label") or ""),
    }


def format_markdown(snapshot: Mapping[str, Any]) -> str:
    """快照 → 报告用 Markdown 表（原始值 / 生效值 / 归属逐行给出）"""
    raw = dict(snapshot.get("raw") or {})
    eff = dict(snapshot.get("effective") or {})
    owners = dict(snapshot.get("owners") or {})
    lines: List[str] = []
    if snapshot.get("label"):
        lines.append(f"**快照标签**：{snapshot['label']}")
        lines.append("")
    lines.append("| 开关 | 原始 env | 归属（默认） |")
    lines.append("|---|---|---|")
    for key in SWITCH_KEYS:
        value = raw.get(key)
        shown = "（未设置）" if value is None else f"`{value}`"
        owner = owners.get(key) or {}
        lines.append(f"| `{key}` | {shown} | "
                     f"{owner.get('ref', '—')}｜{owner.get('default', '—')} |")
    lines.append("")
    lines.append("| 生效值（引擎算出） | 值 | 来源 |")
    lines.append("|---|---|---|")
    for name in sorted(eff):
        item = dict(eff.get(name) or {})
        value = item.get("value")
        lines.append(f"| `{name}` | `{value}` | {item.get('source', '')} |")
    return "\n".join(lines) + "\n"


__all__ = [
    "SWITCH_KEYS", "SWITCH_OWNERS", "switch_snapshot", "effective_switches",
    "diff_snapshots", "format_markdown",
]
