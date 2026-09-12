"""面板口径纪律与溯源契约（TASK-S6-01 §0.3）

【为什么单独一个模块】
    「别让看板说谎」（§7 UI 五坑⑤）不是文案要求，而是**可机检的约束**：
    不可追溯的"97%"比没有数字更危险。故把三条纪律落成函数，让所有面板数据
    必须经过同一出口，无法"顺手"塞一个裸数字或一个来源缺失的百分比。

【三条纪律】
    ① 样本 < ``MIN_DISCLOSURE_SAMPLE``（20）→ 只披露不考核
       （``sample_discipline()`` 自动补 ``insufficient_sample`` /
       ``disclosure_only`` / ``note``，前端据此渲染"仅披露"角标）；
    ② 数据源缺位**一律记 ``None``**，绝不以 0 冒充（``absent()``）；
    ③ 每个数字带数据源与公式（``metric()``）——**不可追溯的百分比禁止上屏**。

【与 S5-02 的关系】
    ``MIN_DISCLOSURE_SAMPLE`` 与 S5-02 §6.7 指标字典的口径同源（S5-03
    `cost_brake.MIN_DISCLOSURE_SAMPLE` 亦为 20）；本模块**不复制**该值，
    而是从既有模块读取，既有模块缺位时回退到契约值并如实标注回退来源。
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

#: 样本披露阈值（§0.3：样本 < 20 只披露不考核）
DEFAULT_MIN_DISCLOSURE_SAMPLE = 20

#: 披露口径文案（各面板共用，避免各写一份漂移）
DISCLOSURE_NOTE = (
    "样本 < {min_sample} 时只披露不考核（§0.3）；"
    "数据源缺位记 None，不以 0 冒充"
)

#: 面板优先级（§7：P0 默认展开 / P1 折叠 / P2 折叠；自愈事故有事故时自动展开）
PANEL_PRIORITY: Dict[str, str] = {
    "digestion_pipeline": "P0",
    "approval_inbox": "P0",
    "capability_map": "P1",
    "roi": "P1",
    "incident": "P1",
    "memory_skills": "P2",
    # TASK-S7-01 开关中心：治理动作（改开关）的入口，与审批收件箱同为 P0
    "settings_center": "P0",
}

#: 面板 → 数据源（**验收用**：面板上屏的每个数字都能在此找到出处）
PANEL_DATASOURCES: Dict[str, Tuple[str, ...]] = {
    "digestion_pipeline": (
        "agent/digestion/stage.py::StageLedger（stage 台账）",
        "agent/digestion/shadow.py::ShadowLedger / ShadowReport.to_dict()",
        "agent/digestion/internalize.py::InternalizeDecision.to_dict() / PromotePR.to_dict()",
        "agent/digestion/shadow.py::ManualReviewQueue.summary()",
        "agent/observability/events.py::EV_DIGEST_STAGE（digest.stage 事件流）",
    ),
    "capability_map": (
        "agent/descriptors/registry.py::DescriptorRegistry.list_with_trust()",
    ),
    "approval_inbox": (
        "agent/skills_mgmt/approval.py::ApprovalFlow.list/approve/reject/get",
        "agent/security/actor_matrix.py::PermissionDecision.to_dict()",
        "agent/security/alerts.py::report_denial/get_denial_stats/recent_denials",
    ),
    "roi": (
        "agent/observability/utc.py::utc_daily/utc_weekly/utc_snapshot/coefficient_table",
        "agent/monitoring/cost_brake.py::cost_daily_view/shadow_overhead_audit/"
        "approval_decay_rate/state",
    ),
    "incident": (
        "agent/self_healing/levels.py::IncidentCard.to_dict()/list_incidents()",
        "agent/observability/events.py::EV_HEALING_TRIGGERED（mttd_ms/mttr_ms）",
        "agent/observability/events.py::EV_BACKUP_HEALTH",
    ),
    "memory_skills": (
        "agent/memory/layered_store.py::LayeredMemoryStore.recall()",
        "agent/memory/taxonomy.py::recall_priority_key()",
    ),
    "acr_utc": (
        "agent/observability/acr.py::acr_snapshot/acr_daily/acr_weekly",
        "agent/observability/utc.py::utc_snapshot",
    ),
    "model_degrade": (
        "agent/observability/model_degrade.py::degrade_summary()",
    ),
    "escape": (
        "agent/observability/escape.py::escape_summary()",
    ),
    "audit_export": (
        "agent/audit/chain.py::verify_chain()/AuditChain.entries()/chain_head()",
    ),
    "security_render": (
        "agent/guardrails/safe_render.py::safe_render_state()",
        "agent/guardrails/boundary_words.py::boundary_state()",
        "agent/guardrails/injection_defense.py::defense_status()",
    ),
    "authz_alerts": (
        "agent/security/alerts.py::get_denial_stats()/recent_denials()",
    ),
    # TASK-S7-01 开关中心：四层来源并列披露（口径不同，不得混用）
    "settings_center": (
        "agent/settings/registry.py::all_specs()（开关注册表：元数据/默认值/风险级）",
        "agent/settings/resolver.py::resolve_all()（四层来源解析结果）",
        "agent/settings/overrides.py::OverrideStore（覆盖层 data/ui_settings.json）",
        "agent/settings/service.py::SettingsService（变更与审计回执）",
    ),
}


def min_disclosure_sample() -> Tuple[int, str]:
    """披露阈值 + 来源（**不复制常量**：优先读既有模块，缺失则回退契约值）

    Returns:
        (阈值, 来源说明)。来源说明会进面板响应，供"这个 20 从哪来"的追问。
    """
    try:  # S5-03 成本刹车的同口径常量
        from agent.monitoring.cost_brake import MIN_DISCLOSURE_SAMPLE as _v
        return int(_v), "agent.monitoring.cost_brake.MIN_DISCLOSURE_SAMPLE"
    except Exception:  # noqa: BLE001 既有模块缺位 → 回退契约值并如实标注
        return DEFAULT_MIN_DISCLOSURE_SAMPLE, (
            "契约回退值（cost_brake 不可用）：DEFAULT_MIN_DISCLOSURE_SAMPLE")


def absent(reason: str = "数据源缺位") -> Dict[str, Any]:
    """数据源缺位占位（**纪律②**：记 ``None``，不以 0 冒充）

    返回 ``{"value": None, "available": False, "reason": ...}``；调用方必须
    原样透传给前端，前端遇到 ``value is None`` 渲染"—"而非 0。
    """
    return {"value": None, "available": False, "reason": str(reason or "数据源缺位")}


def metric(value: Any, *, source: str, formula: str = "", unit: str = "",
           sample_size: Optional[int] = None, dataset: str = "",
           note: str = "") -> Dict[str, Any]:
    """**唯一**的上屏数字出口（纪律③：每个数字带数据源与公式）

    Args:
        value: 指标值；``None`` 表示数据源缺位（与 0 严格区分）。
        source: 数据源（文件/模块::函数——可追溯）。
        formula: 计算口径（人能据此复算）。
        unit: 单位（cents / ms / ratio / count …）。
        sample_size: 样本量；给定且 < 阈值时自动加披露标记。
        dataset: 数据集/窗口标识（口径定位）。
        note: 补充说明（口径冲突时**必须**写明，如 U8 的本体 vs 全量埋点）。

    Returns:
        可直接序列化的指标对象，含 ``traceable``（是否满足纪律③）。
    """
    out: Dict[str, Any] = {
        "value": value,
        "available": value is not None,
        "source": str(source or ""),
        "formula": str(formula or ""),
        "unit": str(unit or ""),
        "dataset": str(dataset or ""),
        "note": str(note or ""),
    }
    if sample_size is not None:
        out["sample_size"] = int(sample_size)
        out.update(sample_discipline(int(sample_size)))
    if note:
        # 调用方补充的口径说明**不得覆盖**披露纪律文案（两者都要保留）
        base = str(out.get("note") or "")
        out["note"] = f"{base}；{note}" if base and note not in base else (note or base)
    # 纪律③自查：无 source 的**百分比/比率**一律不可追溯 ⇒ 显式标记
    out["traceable"] = bool(str(source or "").strip())
    if isinstance(value, float) and 0.0 <= value <= 1.0 and unit in ("ratio", "rate"):
        out["traceable"] = bool(str(source or "").strip()
                                and str(formula or "").strip())
    return out


def sample_discipline(sample_size: int, *,
                      min_sample: Optional[int] = None) -> Dict[str, Any]:
    """样本纪律标记（纪律①）

    样本 < 阈值 ⇒ ``insufficient_sample=True`` + ``disclosure_only=True``；
    达标 ⇒ 两者皆为 False（可考核）。前端据此决定是否渲染"仅披露"角标。
    """
    threshold = int(min_sample) if min_sample is not None else min_disclosure_sample()[0]
    n = max(0, int(sample_size or 0))
    insufficient = n < threshold
    return {
        "sample_size": n,
        "min_sample": threshold,
        "insufficient_sample": insufficient,
        "disclosure_only": insufficient,
        "note": (DISCLOSURE_NOTE.format(min_sample=threshold) if insufficient else ""),
    }


def panel_map(panel: str, *, extra: Sequence[str] = ()) -> Dict[str, Any]:
    """面板元信息（优先级 + 数据源清单；验收报告与前端"来源"入口共用）"""
    key = str(panel or "")
    return {
        "panel": key,
        "priority": PANEL_PRIORITY.get(key, "P1"),
        "datasources": list(PANEL_DATASOURCES.get(key, ())) + list(extra or ()),
        "min_sample": min_disclosure_sample()[0],
        "min_sample_source": min_disclosure_sample()[1],
        "disclosure_note": DISCLOSURE_NOTE.format(min_sample=min_disclosure_sample()[0]),
    }


#: 上游**原样透传**的区段前缀：这些数字由下游既有模块（S2-03/S5-03/S5-02）按其自身
#: 口径纪律产出（各自带 source/formula/samples），不在本任务重新标注。
#: `untraceable_scan` 跳过这些区段，避免把"别人的合规数字"误报成"本面板的裸比率"。
OPAQUE_PREFIXES: Tuple[str, ...] = (
    "acr", "acr_daily", "acr_weekly", "utc", "utc_weekly", "utc_snapshot",
    "daily", "shadow", "items", "incidents", "entries", "slo_metrics.metrics",
    "injection_defense", "safe_render", "durable.recent", "realtime",
    "stage_events", "lanes.*.items",
)


def untraceable_scan(payload: Mapping[str, Any], *,
                     opaque: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """**五坑⑤红线自查**：扫描响应里是否存在不可追溯的百分比

    判定：
        - 面向用户的比率型叶子（``unit`` 为 ratio/rate）必须带 ``source`` +
          ``formula``，否则计入 ``violations``；
        - **未被 `metric()` 出口包裹的裸比率**（0..1 浮点叶子）一律点名——
          这是纪律③唯一出口的机器化检查；
        - 落在 `metric()` 对象**内部**的 ``value`` 不算裸比率（已由该对象的
          ``source`` 背书）；
        - ``opaque`` 前缀下的区段**整段跳过**（上游原样透传，其口径由产出方负责）。

    Args:
        payload: 面板响应（可嵌套 dict/list）。
        opaque: 跳过的路径前缀；缺省用 `OPAQUE_PREFIXES`。

    Returns:
        ``{"ok": bool, "checked": int, "violations": [路径...], "opaque": [...]}``
    """
    skips = tuple(opaque) if opaque is not None else OPAQUE_PREFIXES
    violations: list = []
    checked = 0

    def _skipped(path: str) -> bool:
        head = path.split(".", 1)[0]
        for pref in skips:
            top = pref.split(".", 1)[0]
            if head == top:
                return True
            if path.startswith(pref):
                return True
        return False

    def _walk(node: Any, path: str, *, inside_metric: bool = False) -> None:
        nonlocal checked
        if path and _skipped(path):
            return
        if isinstance(node, Mapping):
            unit = str(node.get("unit") or "")
            is_metric_obj = "value" in node and "source" in node
            if is_metric_obj:
                checked += 1
                if node.get("value") is not None \
                        and not str(node.get("source") or "").strip():
                    violations.append(path or "<root>")
                if unit in ("ratio", "rate") and node.get("value") is not None \
                        and not str(node.get("formula") or "").strip():
                    violations.append(f"{path or '<root>'}.formula")
            for k, v in node.items():
                _walk(v, f"{path}.{k}" if path else str(k),
                      inside_metric=is_metric_obj)
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]", inside_metric=inside_metric)
        elif isinstance(node, float):
            # 裸比率叶子（未被 metric() 包裹）——不可追溯，必须点名
            if not inside_metric and 0.0 <= node <= 1.0:
                violations.append(f"{path or '<root>'}（裸比率，未经 metric() 出口）")

    _walk(payload, "")
    return {"ok": not violations, "checked": checked,
            "violations": sorted(set(violations)), "opaque": list(skips)}


__all__ = [
    "DEFAULT_MIN_DISCLOSURE_SAMPLE", "DISCLOSURE_NOTE", "PANEL_PRIORITY",
    "PANEL_DATASOURCES", "OPAQUE_PREFIXES",
    "min_disclosure_sample", "absent", "metric", "sample_discipline",
    "panel_map", "untraceable_scan",
]
