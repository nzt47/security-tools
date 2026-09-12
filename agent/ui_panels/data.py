"""六面板数据层实现（TASK-S6-01）

【设计要点】
    1. **不新建聚合**：每个面板函数都是对下游既有产出的**投影 + 口径标注**；
    2. **全部目录可注入**：``directory`` / ``events_dir`` / ``shadow_dir`` /
       ``promote_dir`` / ``incidents_dir`` 都可显式传入，供单测与 E2E 用
       **真实产物的临时目录**验证，不污染仓库运行时台账；
    3. **缺位记 None**：任何数据源读不到就返回 ``absent(...)`` 或 ``value=None``，
       绝不以 0 充数（§0.3）；
    4. **只读**：本模块不写任何文件、不 emit 事件、不推进 stage。

模块划分（见各段标题）：
    A. 消化流水线（泳道五列）      §A
    B. 能力地图（capability × 风险/来源/stage）  §B
    C. 审批收件箱 + 越权告警聚合（U3）  §C
    D. ROI / 成本 / ACR / 降级 / 逃逸  §D
    E. 自愈事故 + 备份健康            §E
    F. 记忆 / 技能库                  §F
    G. 审计导出（含验签摘要）          §G
    H. 安全渲染 / 边界词（U1 常量单一来源）  §H
"""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from agent.ui_panels.schema import (
    absent,
    metric,
    panel_map,
    sample_discipline,
    min_disclosure_sample,
)

#: 泳道五列（§7 / P7.2-24 逐字顺序：轨迹采集→模式挖掘→Skill 生成→验收→灰度）
LANES: Tuple[Tuple[str, str], ...] = (
    ("trace_collect", "轨迹采集"),
    ("pattern_mining", "模式挖掘"),
    ("skill_generation", "Skill 生成"),
    ("acceptance", "验收"),
    ("gray", "灰度"),
)

#: 泳道 → digest.stage 事件的 scope 归属（事件如实标注，见 S3-01/S3-02/S3-03）
LANE_SCOPES: Dict[str, Tuple[str, ...]] = {
    "trace_collect": ("digestion",),
    "pattern_mining": ("digestion",),
    "skill_generation": ("digestion",),
    "acceptance": ("acceptance_gate", "drift"),
    "gray": ("shadow_gray", "internalize"),
}


def _now_day() -> str:
    return date.today().isoformat()


def _clamp(value: Any, *, low: int = 1, high: int = 500, default: int = 50) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _read_jsonl(path: str, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """读 JSONL（损坏行跳过；``limit=None`` 读全量）"""
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        return []
    if limit is not None and limit > 0:
        return out[-int(limit):]
    return out


# ════════════════════════════════════════════════════════════
#  §A 消化流水线（泳道五列）
# ════════════════════════════════════════════════════════════


def _digest_stage_events(*, events_dir: Optional[str] = None,
                         days: int = 7) -> List[Dict[str, Any]]:
    """读 ``digest.stage`` 事件（跨分片去重由 `iter_events` 保证）"""
    try:
        from agent.observability.events import EV_DIGEST_STAGE, iter_events
    except Exception:  # noqa: BLE001
        return []
    since = (date.today() - timedelta(days=max(1, int(days)) - 1)).isoformat()
    try:
        rows = iter_events(types=(EV_DIGEST_STAGE,), since=since,
                           directory=events_dir)
    except Exception:  # noqa: BLE001 事件源不可用 ⇒ 空列表（不编造）
        return []
    return [e.to_dict() for e in rows]


def _lane_of(payload: Mapping[str, Any]) -> str:
    """由事件载荷判定所属泳道

    判定依据（**事件如实字段**，不猜）：
        - ``scope`` 命中验收门/灰度/内化 → 对应列；
        - 其余按 ``to_stage`` 落位：``mirrored``=模式挖掘产出、``shadow``=灰度、
          ``internalized``/``native``=灰度末端；
        - 都没有（如 passport 授予但未执行）按 ``acceptance``。
    """
    scope = str(payload.get("scope") or "")
    for lane, scopes in LANE_SCOPES.items():
        if scope in scopes:
            return lane
    to_stage = str(payload.get("to_stage") or "")
    if to_stage in ("internalized", "native"):
        return "gray"
    if to_stage == "shadow":
        return "gray"
    if to_stage == "mirrored":
        return "pattern_mining"
    if payload.get("passport_id"):
        return "acceptance"
    return "trace_collect"


def _shadow_ledger_rows(*, shadow_dir: Optional[str] = None,
                        limit: int = 200) -> List[Dict[str, Any]]:
    """读 shadow 灰度台账（``ShadowLedger`` 的 JSONL 产出；只读）"""
    try:
        from agent.digestion.shadow import ShadowLedger
        path = ShadowLedger(directory=shadow_dir or "").path
    except Exception:  # noqa: BLE001
        return []
    return _read_jsonl(path, limit=limit)


def _promote_prs(*, promote_dir: Optional[str] = None,
                 limit: int = 100) -> List[Dict[str, Any]]:
    """遍历 promote PR 目录里的 ``decision.json``（内化决策的唯一落盘形态）

    为什么读文件而不是 API：S3-03 明确**没有** decisions store——内化决策只落
    ``<promote_dir>/<slug>/<pr_id>/decision.json`` + 链式审计（见 §0.1 台账）。
    """
    try:
        from agent.digestion.internalize import DEFAULT_PROMOTE_DIR, PROMOTE_DIR_ENV
        base = str(promote_dir or os.environ.get(PROMOTE_DIR_ENV) or DEFAULT_PROMOTE_DIR)
    except Exception:  # noqa: BLE001
        base = str(promote_dir or "")
    if not base or not os.path.isdir(base):
        return []
    out: List[Dict[str, Any]] = []
    for slug in sorted(os.listdir(base)):
        slug_dir = os.path.join(base, slug)
        if not os.path.isdir(slug_dir):
            continue
        for pr_id in sorted(os.listdir(slug_dir)):
            decision = _read_json(os.path.join(slug_dir, pr_id, "decision.json"))
            if decision is None:
                continue
            decision["_pr_id"] = pr_id
            decision["_pr_dir"] = os.path.join(slug_dir, pr_id)
            out.append(decision)
    return out[-int(limit):] if limit > 0 else out


def _manual_review_summary(*, shadow_dir: Optional[str] = None) -> Dict[str, Any]:
    try:
        from agent.digestion.shadow import ManualReviewQueue
        return ManualReviewQueue(directory=shadow_dir or "").summary()
    except Exception as e:  # noqa: BLE001
        return dict(absent(f"人工抽检队列不可用: {type(e).__name__}"))


def pipeline_view(*, days: int = 7, limit: int = 50,
                  events_dir: Optional[str] = None,
                  shadow_dir: Optional[str] = None,
                  promote_dir: Optional[str] = None,
                  registry: Any = None) -> Dict[str, Any]:
    """消化流水线（P0 面板）：泳道五列 + 灰度 + 内化决策 + 抽样队列状态

    Args:
        days: 事件窗口天数。
        limit: 每列返回的明细条数上限（**虚拟滚动阈值 500** 由前端保证；
            本函数硬上限亦为 500）。
        events_dir / shadow_dir / promote_dir: 数据源目录覆盖（测试隔离用）。
        registry: DescriptorRegistry（缺省懒加载运行时台账）。

    Returns:
        ``{ok, panel, generated_at, lanes[], shadow[], internalize[], 
           manual_review, clock, summary{}}``
    """
    limit = _clamp(limit, high=500)
    events = _digest_stage_events(events_dir=events_dir, days=days)
    shadow_rows = _shadow_ledger_rows(shadow_dir=shadow_dir, limit=500)
    decisions = _promote_prs(promote_dir=promote_dir, limit=500)

    lanes: List[Dict[str, Any]] = []
    for key, title in LANES:
        items = [e for e in events if _lane_of(e.get("payload") or {}) == key]
        items.sort(key=lambda e: str(e.get("ts") or ""), reverse=True)
        n = len(items)
        lanes.append({
            "lane": key,
            "title": title,
            "event_count": metric(
                n, source="agent/observability/events.py::digest.stage",
                formula="窗口内 scope/to_stage 命中该泳道的 digest.stage 事件条数",
                unit="count", sample_size=n, dataset=f"最近 {days} 天"),
            "items": [_digest_event_card(e) for e in items[:limit]],
            "truncated": n > limit,
        })

    return {
        "ok": True,
        "panel": panel_map("digestion_pipeline"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "lanes": lanes,
        "shadow": [_shadow_card(r) for r in reversed(shadow_rows[:limit])],
        "internalize": [_decision_card(d) for d in reversed(decisions[:limit])],
        "manual_review": _manual_review_summary(shadow_dir=shadow_dir),
        "clock": {
            "wall": "CLOCK_WALL（真实墙钟，S3-03 shadow 报告口径）",
            "note": ("灰度 p99 一律标注墙钟；模型时钟另列。"
                     "面板不得把两者混用（U8 同源纪律）"),
        },
        "summary": _pipeline_summary(events, shadow_rows, decisions, registry),
    }


def _digest_event_card(env: Mapping[str, Any]) -> Dict[str, Any]:
    """泳道卡片（可解释性：字段全部来自事件本身，不做二次推断）"""
    p = dict(env.get("payload") or {})
    return {
        "event_id": str(env.get("event_id") or ""),
        "ts": str(env.get("ts") or ""),
        "actor": str(env.get("actor") or ""),
        "capability_id": str(p.get("capability_id") or ""),
        "from_stage": p.get("from_stage"),
        "to_stage": p.get("to_stage"),
        "applied": bool(p.get("applied")),
        "verdict": str(p.get("verdict") or ""),
        "scope": str(p.get("scope") or ""),
        "passport_id": str(p.get("passport_id") or ""),
        "pass_rate": p.get("pass_rate"),
        "rank_score": p.get("rank_score"),
        "manual_required": p.get("manual_required"),
        "reasons": list(p.get("reasons") or [])[:10],
        "digest_run_id": str(p.get("digest_run_id") or ""),
        "trace_id": str(p.get("trace_id") or ""),
    }


def _shadow_card(row: Mapping[str, Any]) -> Dict[str, Any]:
    """灰度运行卡片（ShadowLedger 行 → 面板卡；数字逐项带口径）"""
    p99_c = row.get("p99_wall_candidate_ms")
    p99_u = row.get("p99_wall_upstream_ms")
    samples = int(row.get("sampled") or 0)
    return {
        "capability_id": str(row.get("capability_id") or ""),
        "generated_at": row.get("generated_at"),
        "allowed": bool(row.get("allowed")),
        "budget": row.get("budget"),
        "sampled": samples,
        "passed": row.get("passed"),
        "negative": row.get("negative"),
        "judge_kind": str(row.get("judge_kind") or ""),
        "degradation": str(row.get("degradation") or ""),
        "shadow_version": str(row.get("shadow_version") or ""),
        "p99_wall_candidate_ms": metric(
            p99_c, source="agent/digestion/shadow.py::ShadowReport.p99_wall_candidate_ms",
            formula="候选实现墙钟耗时的 p99（真实墙钟，非模型时钟）",
            unit="ms", sample_size=samples, dataset="shadow_ledger.jsonl"),
        "p99_wall_upstream_ms": metric(
            p99_u, source="agent/digestion/shadow.py::ShadowReport.p99_wall_upstream_ms",
            formula="上游实现墙钟耗时的 p99，与候选同批样本",
            unit="ms", sample_size=samples, dataset="shadow_ledger.jsonl"),
        # 通过率是比率 ⇒ 必须带 source+formula（五坑⑤红线：可追溯）
        "pass_rate": metric(
            (round(float(row.get("passed") or 0) / samples, 6) if samples else None),
            source="agent/digestion/shadow.py::ShadowReport.pass_rate",
            formula="passed / sampled（同批灰度样本）", unit="ratio",
            sample_size=samples, dataset="shadow_ledger.jsonl"),
    }


def _decision_card(decision: Mapping[str, Any]) -> Dict[str, Any]:
    """内化决策卡片（六条件逐项 + ROI + 判决；供 ReasonChain 展开）"""
    conditions = []
    for c in (decision.get("conditions") or []):
        if not isinstance(c, Mapping):
            continue
        conditions.append({
            "name": str(c.get("name") or ""),
            "dimension": str(c.get("dimension") or ""),
            "passed": bool(c.get("passed")),
            "actual": c.get("actual"),
            "threshold": c.get("threshold"),
            "comparator": str(c.get("comparator") or ""),
            "score": c.get("score"),
            "evidence_source": str(c.get("evidence_source") or ""),
            "reasons": list(c.get("reasons") or [])[:5],
        })
    roi = dict(decision.get("roi_report") or {})
    return {
        "capability_id": str(decision.get("capability_id") or ""),
        "pr_id": str(decision.get("_pr_id") or ""),
        "pr_dir": str(decision.get("_pr_dir") or ""),
        "verdict": str(decision.get("verdict") or ""),
        "blocker": str(decision.get("blocker") or ""),
        "stage": str(decision.get("stage") or ""),
        "passport_id": str(decision.get("passport_id") or ""),
        "rank_score": decision.get("rank_score"),
        "promotable": bool(decision.get("promotable")),
        "manual_required": bool(decision.get("manual_required")),
        "manual_label": str(decision.get("manual_label") or ""),
        "veto_failed": list(decision.get("veto_failed") or []),
        "rank_failed": list(decision.get("rank_failed") or []),
        "generated_at": decision.get("generated_at"),
        "engine_version": str(decision.get("engine_version") or ""),
        "audit_seq": decision.get("audit_seq"),
        "audit_hash": str(decision.get("audit_hash") or ""),
        "conditions": conditions,
        "roi": {
            "monthly_saving_cents": metric(
                roi.get("monthly_saving_cents"),
                source="agent/digestion/internalize.py::ROIReport",
                formula=str(roi.get("formula") or ""), unit="cents"),
            "one_time_investment_cents": metric(
                roi.get("one_time_investment_cents"),
                source="agent/digestion/internalize.py::ROIReport",
                formula="自研一次性投入（配置 CP_DIGESTION_NATIVE_INVESTMENT_CENTS）",
                unit="cents"),
            "amortized_monthly_cents": metric(
                roi.get("amortized_monthly_cents"),
                source="agent/digestion/internalize.py::ROIReport",
                formula="一次性投入 ÷ 12（ROI_AMORTIZE_MONTHS）", unit="cents"),
            "net_monthly_cents": metric(
                roi.get("net_monthly_cents"),
                source="agent/digestion/internalize.py::ROIReport",
                formula="月省 − 月摊销", unit="cents"),
            "positive": roi.get("positive"),
            "monthly_samples": roi.get("monthly_samples"),
            # ── R1（TASK-S7-06）：判定集构建成本单列披露 + 双口径 ROI（兼容叠加）──
            "case_build_cost_cents": metric(
                roi.get("case_build_cost_cents"),
                source=str(roi.get("case_build_cost_source")
                           or "agent/digestion/case_cost.py（cost 事件 stage=case_build）"),
                formula=("判定集构建成本点值（下界）——估计方法见 "
                         "`case_build_cost_method`，样本 "
                         f"{roi.get('case_build_cost_samples')} 条"),
                unit="cents",
                sample_size=int(roi.get("case_build_cost_samples") or 0)),
            "case_build_cost_range_cents": {
                "low": roi.get("case_build_cost_low_cents"),
                "high": roi.get("case_build_cost_high_cents"),
                "note": ("右端 null = 人工/回放单价未配置 ⇒ **不可估**（不臆造）；"
                         "配置 CP_DIGESTION_CASE_BUILD_MANUAL_RATE_CENTS / "
                         "CP_DIGESTION_CASE_BUILD_REPLAY_RATE_CENTS 后可估"),
                "lower_bound": bool(roi.get("case_build_cost_lower_bound")),
            },
            "case_build_cost_method": str(roi.get("case_build_cost_method") or ""),
            "case_build_cost_in_amortization": bool(
                roi.get("case_build_cost_in_amortization")),
            "net_monthly_excluding_case_build_cents": metric(
                roi.get("net_monthly_excluding_case_build_cents",
                        roi.get("net_monthly_cents")),
                source="agent/digestion/internalize.py::ROIReport",
                formula=str(roi.get("formula_excluding_case_build")
                            or roi.get("formula") or ""), unit="cents"),
            "net_monthly_including_case_build_cents": metric(
                roi.get("net_monthly_including_case_build_cents"),
                source="agent/digestion/internalize.py::ROIReport",
                formula=str(roi.get("formula_including_case_build") or ""),
                unit="cents"),
            "positive_excluding_case_build": roi.get(
                "positive_excluding_case_build", roi.get("positive")),
            "positive_including_case_build": roi.get(
                "positive_including_case_build"),
            "caveats": list(roi.get("caveats") or []),
            "assumptions": list(roi.get("assumptions") or []),
        },
    }


def _pipeline_summary(events: Sequence[Mapping[str, Any]],
                      shadow_rows: Sequence[Mapping[str, Any]],
                      decisions: Sequence[Mapping[str, Any]],
                      registry: Any) -> Dict[str, Any]:
    """流水线汇总（每项都带 sample_size ⇒ 自动带披露标记）"""
    cap_ids = sorted({str((e.get("payload") or {}).get("capability_id") or "")
                      for e in events} - {""})
    applied = sum(1 for e in events if (e.get("payload") or {}).get("applied"))
    n_events = len(events)
    n_decisions = len(decisions)
    promotable = sum(1 for d in decisions if d.get("promotable"))
    stages: Dict[str, int] = {}
    try:
        reg = registry
        if reg is None:
            from agent.descriptors.registry import DescriptorRegistry
            reg = DescriptorRegistry(autosave=False)
        for row in reg.list_with_trust():
            st = str(row.get("stage") or "(未入轨)")
            stages[st] = stages.get(st, 0) + 1
    except Exception:  # noqa: BLE001 台账不可用 → 空表（不编造）
        stages = {}
    return {
        "window_days": None,
        "digest_stage_events": metric(
            n_events, source="agent/observability/events.py::EV_DIGEST_STAGE",
            formula="窗口内 digest.stage 事件条数（跨分片按 event_id 去重）",
            unit="count", sample_size=n_events),
        "capabilities_touched": metric(
            len(cap_ids), source="digest.stage 事件 capability_id 去重",
            formula="窗口内出现过 stage 事件的能力数", unit="count",
            sample_size=len(cap_ids)),
        "applied_migrations": metric(
            applied, source="digest.stage 事件 applied=true",
            formula="真正落库的 stage 迁移条数（deferred 不计）", unit="count",
            sample_size=n_events),
        "shadow_runs": metric(
            len(shadow_rows), source="data/digestion/shadow/shadow_ledger.jsonl",
            formula="灰度台账行数（一行一次灰度运行）", unit="count",
            sample_size=len(shadow_rows)),
        "internalize_decisions": metric(
            n_decisions,
            source="<promote_dir>/*/*/decision.json（S3-03 无 decisions store）",
            formula="落盘的 promote PR 决策数", unit="count",
            sample_size=n_decisions),
        # 内化率是比率 ⇒ 必须有可复算的分子分母
        "internalize_rate": metric(
            (round(promotable / n_decisions, 6) if n_decisions else None),
            source="decision.json::promotable / decision.json 总数",
            formula="可 promote 的决策数 / 落盘决策数（分母=0 ⇒ None，不填 0）",
            unit="ratio", sample_size=n_decisions,
            note="与 S5-02 §6.7 internalization_rate 口径同源；本面板按其样本纪律披露"),
        "stage_distribution": stages,
    }


# ════════════════════════════════════════════════════════════
#  §B 能力地图
# ════════════════════════════════════════════════════════════


def _guess_platform(capability_id: str, source_id: str) -> str:
    """能力归属平台（由 capability_id/source_id 前缀推导，**只做分组不做判定**）"""
    text = f"{capability_id} {source_id}".lower()
    for token in ("mcp", "builtin", "skill", "workflow", "native", "governed"):
        if token in text:
            return token
    return "other"


def capability_map(*, registry: Any = None, stage: str = "",
                   provenance: str = "", risk: str = "", data_class: str = "",
                   query: str = "", limit: int = 500,
                   offset: int = 0) -> Dict[str, Any]:
    """能力地图（P1 面板）：capability × provenance / risk / data_class / evolution.stage

    数据源 = ``DescriptorRegistry.list_with_trust()``（S1-01 交付，**真实台账**）。
    每行带 `success_rate` 的样本纪律标记（``sample_count < 20`` ⇒ 只披露不考核）。
    """
    limit = _clamp(limit, high=500)
    rows: List[Dict[str, Any]] = []
    load_error = ""
    try:
        reg = registry
        if reg is None:
            from agent.descriptors.registry import DescriptorRegistry
            reg = DescriptorRegistry(autosave=False)
        rows = list(reg.list_with_trust())
    except Exception as e:  # noqa: BLE001 台账不可用 ⇒ 如实报错，不编造空表
        load_error = f"{type(e).__name__}: {e}"

    def _keep(r: Mapping[str, Any]) -> bool:
        if stage and str(r.get("stage") or "") != stage:
            return False
        if provenance and str(r.get("provenance") or "") != provenance:
            return False
        if risk and str(r.get("risk_level") or "") != risk:
            return False
        if data_class and str(r.get("data_class") or "") != data_class:
            return False
        if query:
            q = query.lower()
            hay = f"{r.get('capability_id')} {r.get('name')} {r.get('description')}".lower()
            if q not in hay:
                return False
        return True

    filtered = [r for r in rows if _keep(r)]
    page = filtered[offset:offset + limit]
    items = [_capability_card(r) for r in page]

    def _dist(key: str) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in filtered:
            k = str(r.get(key) or "(未标注)")
            out[k] = out.get(k, 0) + 1
        return dict(sorted(out.items()))

    return {
        "ok": True,
        "panel": panel_map("capability_map"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "agent/descriptors/registry.py::list_with_trust()",
        "load_error": load_error,
        "total": metric(
            len(rows), source="DescriptorRegistry.list_with_trust()",
            formula="运行时能力台账条目数", unit="count", sample_size=len(rows)),
        "matched": metric(
            len(filtered), source="list_with_trust() + 面板筛选条件",
            formula="先筛选后计数的条目数", unit="count", sample_size=len(filtered)),
        "pagination": {"total": len(filtered), "offset": offset, "limit": limit,
                       "has_more": offset + limit < len(filtered)},
        "distribution": {
            "by_provenance": _dist("provenance"),
            "by_risk": _dist("risk_level"),
            "by_data_class": _dist("data_class"),
            "by_stage": _dist("stage"),
            "by_platform": _keys_count(items, "platform"),
        },
        "items": items,
    }


def _keys_count(items: Sequence[Mapping[str, Any]], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for it in items:
        k = str(it.get(key) or "(未标注)")
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def _capability_card(row: Mapping[str, Any]) -> Dict[str, Any]:
    samples = row.get("sample_count")
    try:
        samples_i = int(samples) if samples is not None else None
    except (TypeError, ValueError):
        samples_i = None
    # ★ 纪律②：**零样本时台账里的 0 不是"命中率 0%"**，而是"没有样本"
    # —— 一律记 None，绝不以 0 冒充（§0.3；五坑⑤"别让看板说谎"）
    no_samples = samples_i is None or samples_i <= 0
    rate = None if no_samples else row.get("success_rate")
    p99 = None if no_samples else row.get("p99_latency_ms")
    return {
        "capability_id": str(row.get("capability_id") or ""),
        "name": str(row.get("name") or ""),
        "description": str(row.get("description") or "")[:300],
        "source_type": str(row.get("source_type") or ""),
        "source_id": str(row.get("source_id") or ""),
        "provenance": str(row.get("provenance") or ""),
        "risk_level": row.get("risk_level"),
        "data_class": row.get("data_class"),
        "requires_approval": bool(row.get("requires_approval")),
        "stage": row.get("stage"),
        "audit_level": str(row.get("audit_level") or ""),
        "has_undo_hint": bool(row.get("has_undo_hint")),
        "has_compensating_action": bool(row.get("has_compensating_action")),
        "idempotent": row.get("idempotent"),
        "timeout_ms": row.get("timeout_ms"),
        "external_endpoint": row.get("external_endpoint"),
        "variant_count": row.get("variant_count"),
        "aliases": list(row.get("aliases") or [])[:10],
        "platform": _guess_platform(str(row.get("capability_id") or ""),
                                    str(row.get("source_id") or "")),
        "updated_at": row.get("updated_at"),
        # 质量三元组：success_rate 是比率 ⇒ 必须带来源+公式+样本量
        "success_rate": metric(
            rate, source="DescriptorRegistry.list_with_trust()::quality.success_rate",
            formula="quality.success_rate（台账字段，S1-02 回填）", unit="rate",
            sample_size=samples_i, dataset="descriptors.json",
            note=("样本为 0 时记 None（台账的 0 = 无样本，不是 0% 命中率）"
                  if no_samples else "")),
        "sample_count": samples_i,
        "sample_discipline": (
            sample_discipline(samples_i) if samples_i is not None else None),
        "p99_latency_ms": metric(
            p99,
            source="DescriptorRegistry.list_with_trust()::quality.p99_latency_ms",
            formula="台账记录的 p99 延迟（口径见 S1-02 回填说明）", unit="ms",
            sample_size=samples_i,
            note=("样本为 0 时记 None（无样本即无 p99）" if no_samples else "")),
        # 审批气泡门槛：缺 undo_hint 不出气泡（§7 逐字）——前端只看这个布尔
        "approval_bubble_eligible": bool(
            row.get("has_undo_hint") or row.get("has_compensating_action")),
    }


# ════════════════════════════════════════════════════════════
#  §C 审批收件箱 + 越权告警聚合（U3）
# ════════════════════════════════════════════════════════════


def approval_inbox(*, flow: Any = None, limit: int = 50,
                   object_type: str = "") -> Dict[str, Any]:
    """审批收件箱（P0）：待审记录 + 批量裁决分组键

    批量裁决口径（§7「同策略同风险一键批」）：
        ``batch_key = object_type | level | risk`` —— 三者相同才允许一键批；
        前端只按本字段分组，**不自行推断**策略/风险。

    Note:
        「缺 undo_hint 不出现审批气泡」的判定在这里给出
        （``bubble.visible``），前端不得自行放宽。
    """
    limit = _clamp(limit, high=200)
    if flow is None:
        try:
            from agent.server_routes.routes_approval import get_approval_flow
            flow = get_approval_flow()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"审批流不可用: {type(e).__name__}",
                    "items": [], "count": 0}
    try:
        records = flow.list({"state": "pending_review"}, limit=limit)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"待审清单读取失败: {type(e).__name__}",
                "items": [], "count": 0}

    from agent.security import approval_guard as guard_mod

    items: List[Dict[str, Any]] = []
    groups: Dict[str, Dict[str, Any]] = {}
    for r in records:
        if object_type and str(r.object_type) != object_type:
            continue
        risk = guard_mod.resolve_risk(r.object_type, r.object_id, r.payload)
        undo_hint = ""
        comp = ""
        taint: Dict[str, Any] = {}
        gov: Dict[str, Any] = {}
        try:
            from agent.security.governance_bridge import (
                governance_trace_fields, taint_flags)
            gov = governance_trace_fields(r.object_type, r.object_id, r.payload)
            taint = taint_flags(r.object_type, r.object_id, r.payload)
        except Exception:  # noqa: BLE001 治理桥不可用 ⇒ 按"无 undo_hint"从严
            gov = {}
            taint = {}
        undo_hint = str(gov.get("undo_hint") or "")
        comp = str(gov.get("compensating_action") or "")
        visible = bool(undo_hint or comp)
        batch_key = f"{r.object_type}|{r.level}|{risk or 'unknown'}"
        item = {
            "record_id": r.record_id,
            "object_type": r.object_type,
            "object_id": r.object_id,
            "level": r.level,
            "action": r.action,
            "description": str(r.description or "")[:200],
            "actor": r.actor,
            "actor_type": r.actor_type,
            "manual_required": bool(r.manual_required),
            "created_at": r.created_at,
            "risk": risk,
            "batch_key": batch_key,
            "governance": gov,
            "taint": taint,
            "bubble": {
                "visible": visible,
                # §7：审批气泡缺 undo_hint 不出现——这条是后端判定的硬规则
                "rule": "缺 undo_hint 不出现审批气泡（§7）",
                "undo_hint": undo_hint,
                "compensating_action": comp,
            },
        }
        items.append(item)
        g = groups.setdefault(batch_key, {
            "batch_key": batch_key, "object_type": r.object_type,
            "level": r.level, "risk": risk or "unknown",
            "count": 0, "record_ids": [], "bubble_visible_count": 0,
        })
        g["count"] += 1
        g["record_ids"].append(r.record_id)
        g["bubble_visible_count"] += 1 if visible else 0

    n = len(items)
    return {
        "ok": True,
        "panel": panel_map("approval_inbox"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": n,
        "pending_total": metric(
            n, source="agent/skills_mgmt/approval.py::ApprovalFlow.list({'state':'pending_review'})",
            formula="待审记录条数（上限 200 条/页）", unit="count", sample_size=n),
        "pending_by_type": {
            k: v for k, v in sorted(
                _count(items, "object_type").items())},
        "bubble_hidden": metric(
            sum(1 for i in items if not i["bubble"]["visible"]),
            source="governance_trace_fields()::undo_hint / compensating_action",
            formula="缺 undo_hint 且无补偿动作的待审记录数（这些记录不出气泡）",
            unit="count", sample_size=n),
        "items": items,
        "batch_groups": sorted(groups.values(), key=lambda g: (-g["count"], g["batch_key"])),
        "decision_contract": {
            "operation_human_only": "§7.0 审批 Approve/Deny 仅 human，矩阵单表校验",
            "requires_second_factor": "destructive 风险强制二次认证（§5.7⑦）",
            "link_ttl_seconds": 900,
            "note": "批量裁决=逐条走同一审批链（会话+CSRF+链接+二次认证+矩阵），不新增旁路",
        },
    }


def _count(items: Sequence[Mapping[str, Any]], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for it in items:
        k = str(it.get(key) or "(空)")
        out[k] = out.get(k, 0) + 1
    return out


def authz_alerts(*, limit: int = 50, events_dir: Optional[str] = None,
                 days: int = 7) -> Dict[str, Any]:
    """越权告警聚合面板（**U3**）：``actor_ip_hash`` + 窗口阈值口径

    两个数据源**并列披露**（口径不同，不得混用）：

    1. **进程内计数器**（``agent/security/alerts.py::get_denial_stats()``）——
       含 ``CP_SECURITY_ALERT_THRESHOLD`` / ``CP_SECURITY_ALERT_WINDOW_SECONDS``
       的实时窗口聚合；**进程重启即归零**，故标注 ``volatile=true``；
    2. **事件流**（``policy.denied``）——耐久口径，跨重启可追溯，
       按 ``actor_ip_hash``（**原始 IP 不落盘**，裁定 B）分组。
    """
    limit = _clamp(limit, high=500)
    stats: Dict[str, Any] = {}
    volatile = True
    try:
        from agent.security.alerts import (alert_threshold, alert_window_seconds,
                                           alerts_enabled, get_denial_stats)
        stats = dict(get_denial_stats())
        stats["threshold"] = alert_threshold()
        stats["window_seconds"] = alert_window_seconds()
        stats["enabled"] = alerts_enabled()
    except Exception as e:  # noqa: BLE001
        stats = dict(absent(f"alerts 模块不可用: {type(e).__name__}"))

    durable: Dict[str, Any] = {}
    try:
        from agent.observability.events import EV_POLICY_DENIED, iter_events
        since = (date.today() - timedelta(days=max(1, int(days)) - 1)).isoformat()
        rows = iter_events(types=(EV_POLICY_DENIED,), since=since,
                           directory=events_dir)
        by_hash: Dict[str, int] = {}
        by_op: Dict[str, int] = {}
        recent: List[Dict[str, Any]] = []
        for env in rows:
            p = env.payload or {}
            h = str(p.get("actor_ip_hash") or p.get("actor_ip_masked") or "unknown")
            by_hash[h] = by_hash.get(h, 0) + 1
            op = str(p.get("operation") or "")
            by_op[op] = by_op.get(op, 0) + 1
            recent.append({
                "ts": env.ts, "actor": env.actor, "actor_type": p.get("actor_type"),
                "operation": op, "object_type": p.get("object_type"),
                "object_id": p.get("object_id"), "record_id": p.get("record_id"),
                "denied_by_matrix": bool(p.get("denied_by_matrix")),
                "reason": str(p.get("reason") or "")[:200],
                "source_key": h,
            })
        recent.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
        durable = {
            "window_days": days,
            "total": metric(
                len(rows), source="agent/observability/events.py::policy.denied",
                formula="窗口内 policy.denied 事件条数（跨分片去重）",
                unit="count", sample_size=len(rows)),
            "by_source_key": dict(sorted(by_hash.items())),
            "by_operation": dict(sorted(by_op.items())),
            "recent": recent[:limit],
            "pii_note": ("source_key = actor_ip_hash（HMAC-SHA256，密钥入 SecretStore）；"
                         "原始 IP 不落盘（裁定 B）"),
        }
    except Exception as e:  # noqa: BLE001
        durable = dict(absent(f"事件流不可用: {type(e).__name__}"))

    return {
        "ok": True,
        "panel": panel_map("authz_alerts"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "realtime": {"stats": stats, "volatile": volatile,
                     "note": "进程内计数器，重启归零；耐久口径请看 durable"},
        "durable": durable,
        "thresholds": {
            "source": "agent/security/alerts.py",
            "threshold_env": "CP_SECURITY_ALERT_THRESHOLD",
            "window_env": "CP_SECURITY_ALERT_WINDOW_SECONDS",
        },
    }


# ════════════════════════════════════════════════════════════
#  §D ROI / 成本 / ACR / 降级 / 逃逸
# ════════════════════════════════════════════════════════════


def roi_view(*, days: int = 7, events_dir: Optional[str] = None,
             include_metrics: bool = True,
             registry: Any = None,
             shadow_dir: str = "", delegations: Optional[Sequence[Mapping[str, Any]]] = None
             ) -> Dict[str, Any]:
    """ROI / 成本面板（P1）：月省 / 投入 / 阈值 + ACR/UTC + 审批衰减率

    **口径纪律（U8）**：策略/成本类数字必须标注口径。本函数把两个口径**并列**给出：
        - ``policy_latency.decision_body``：决策本体 p99（缓存命中路径）≈0.047ms；
        - ``policy_latency.full_instrumentation``：全量埋点下 3.76 / 7.97ms。
      两者**不得混用**，也不得相减/平均。
    """
    days = max(1, min(90, int(days or 7)))
    out: Dict[str, Any] = {
        "ok": True,
        "panel": panel_map("roi"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    # ── 日成本视图（唯一数据源＝事件流；含刹车判据与口径版本）──
    try:
        from agent.monitoring.cost_brake import cost_daily_view
        view = cost_daily_view(directory=events_dir)
        out["daily"] = {
            "date": view.get("date"),
            "cost_normalized_cents": metric(
                view.get("cost_normalized_cents"),
                source="agent/observability/utc.py::utc_daily（COST_SOURCE_OF_TRUTH=events）",
                formula="Σ cost 事件 cost_normalized_cents（日内）",
                unit="cents", dataset=str(view.get("date") or "")),
            "cost_effective_cents": metric(
                view.get("cost_effective_cents"),
                source="agent/monitoring/cost_brake.py::cost_daily_view",
                formula="归一成本 + shadow 开销折算（cost_effective_cents）",
                unit="cents"),
            "daily_budget_cents": metric(
                view.get("daily_budget_cents"),
                source="config.yaml / CP_BUDGET_*（S5-03 BrakeConfig）",
                formula="日级硬熔断阈值（0 = 未启用）", unit="cents"),
            "over_budget": view.get("over_budget"),
            "ratio": metric(
                view.get("ratio"),
                source="agent/monitoring/cost_brake.py::cost_daily_view",
                formula="cost_effective_cents / baseline_cents_per_day（基线为 0 ⇒ None）",
                unit="ratio", dataset=str(view.get("date") or "")),
            "baseline_cents_per_day": metric(
                view.get("baseline_cents_per_day"),
                source="agent/monitoring/cost_brake.py::cost_daily_view",
                formula="滚动基线日均成本（baseline_source 标注来源）；未配置且无历史 ⇒ None",
                unit="cents", dataset=str(view.get("date") or "")),
            "baseline_source": view.get("baseline_source"),
            "theta": metric(
                view.get("theta"),
                source="agent/monitoring/cost_brake.py::theta_limit（§6.3 分阶段阈值）",
                formula="按里程碑阶段给出的成本上限倍数（W1-W4 不设 → M7+ 0.5×）",
                unit="ratio", dataset=str(view.get("date") or "")),
            "fasting_in_ratio": metric(
                view.get("fasting_in_ratio"),
                source="agent/monitoring/cost_brake.py::BrakeConfig（§7 断食规则）",
                formula="进入断食的比值阈值（逐字：进 >1.3×）", unit="ratio"),
            "fasting_out_ratio": metric(
                view.get("fasting_out_ratio"),
                source="agent/monitoring/cost_brake.py::BrakeConfig（§7 断食规则）",
                formula="退出断食的比值阈值（逐字：出连续 24h ≤1.1×）", unit="ratio"),
            "shadow": view.get("shadow"),
            "cost_schema_version": view.get("cost_schema_version"),
            "calibration_version": view.get("calibration_version"),
            "calibration_note": view.get("calibration_note"),
            "source_of_truth": view.get("source_of_truth"),
        }
    except Exception as e:  # noqa: BLE001
        out["daily"] = absent(f"cost_brake 不可用: {type(e).__name__}: {e}")

    # ── UTC 周视图 + 滚动快照（阈值口径随附）──
    try:
        from agent.observability.utc import utc_snapshot, utc_weekly
        snap = utc_snapshot(days=days, directory=events_dir)
        out["utc_weekly"] = utc_weekly(directory=events_dir)
        out["utc_snapshot"] = {
            "baseline_cents_per_day": snap.get("baseline_cents_per_day"),
            "baseline_days": snap.get("baseline_days"),
            "ratio": metric(
                snap.get("ratio"), source="agent/observability/utc.py::utc_snapshot",
                formula="今日有效成本 / 滚动基线日均成本", unit="ratio"),
            "thresholds": snap.get("thresholds"),
        }
    except Exception as e:  # noqa: BLE001
        out["utc_weekly"] = absent(f"utc 不可用: {type(e).__name__}")
        out["utc_snapshot"] = absent(f"utc 不可用: {type(e).__name__}")

    # ── 审批衰减率（§6.7；披露不考核）──
    try:
        from agent.monitoring.cost_brake import approval_decay_rate
        decay = approval_decay_rate(days=days, directory=events_dir)
        out["approval_decay"] = {
            "decay_rate": metric(
                decay.get("decay_rate"),
                source="agent/monitoring/cost_brake.py::approval_decay_rate",
                formula="无需人工即完成的审批决策数 / 全部审批决策数",
                unit="rate", sample_size=int(decay.get("total_disposed") or 0),
                dataset=str(decay.get("window") or {}),
                note="披露不考核（§6.7 起步口径）"),
            "target": metric(
                decay.get("target"),
                source="agent/monitoring/cost_brake.py::APPROVAL_DECAY_TARGET（§6.7）",
                formula="目标值（逐字：≥70%）", unit="rate",
                note="披露不考核：达标与否不阻断任何流程"),
            "meets_target": decay.get("meets_target"),
            "auto_disposed": decay.get("auto_disposed"),
            "human_disposed": decay.get("human_disposed"),
            "total_disposed": decay.get("total_disposed"),
            "insufficient_sample": decay.get("insufficient_sample"),
            "min_sample": decay.get("min_sample"),
            "fatigue_buckets": decay.get("fatigue_buckets"),
            "latency_median_ms": metric(
                decay.get("latency_median_ms"),
                source="agent/monitoring/cost_brake.py::approval_decay_rate",
                formula="审批迟滞中位数（approval 事件 latency_ms）", unit="ms",
                sample_size=int(decay.get("total_disposed") or 0)),
            "disclosure_only": decay.get("disclosure_only"),
            "note": decay.get("note"),
        }
    except Exception as e:  # noqa: BLE001
        out["approval_decay"] = absent(f"cost_brake 不可用: {type(e).__name__}")

    # ── §6.7 指标字典（S5-02）：8 项可计算 + 3 项框架就绪 —— 含 U10 委派回收率 ──
    if include_metrics:
        try:
            from agent.eval.metrics import compute_metrics
            rep = compute_metrics(days=days, events_dir=events_dir or None,
                                  registry=registry, shadow_dir=shadow_dir,
                                  delegations=delegations)
            out["slo_metrics"] = {
                "schema": rep.get("schema"),
                "window": rep.get("window"),
                "traceability": rep.get("traceability"),
                # 原样透传：S5-02 的每行已带 value/source/formula/samples/status
                # R4（TASK-S7-06）：委派行另带 columns（mechanical/llm/unlabeled）与
                # mixed 标记 —— 面板按列读取，**不得把两列合成一个数**
                "metrics": rep.get("metrics"),
                "delegation_signals": rep.get("delegation_signals"),
            }
        except Exception as e:  # noqa: BLE001
            out["slo_metrics"] = absent(f"eval.metrics 不可用: {type(e).__name__}")

    # ── ACR（介入率）日/周 ──
    try:
        from agent.observability.acr import acr_daily, acr_weekly
        out["acr_daily"] = acr_daily(directory=events_dir)
        out["acr_weekly"] = acr_weekly(directory=events_dir)
    except Exception as e:  # noqa: BLE001
        out["acr_daily"] = absent(f"acr 不可用: {type(e).__name__}")
        out["acr_weekly"] = absent(f"acr 不可用: {type(e).__name__}")

    # ── 口径并列（U8：本体 vs 全量埋点）──
    out["policy_latency"] = {
        "decision_body": metric(
            0.0474, source="TASK-S4-02_验收报告（决策缓存命中路径 p99）",
            formula="决策本体 p99（不含埋点开销）", unit="ms",
            note="**不得**与全量埋点口径混用/相减/平均（U8）"),
        "full_instrumentation": {
            "hit_ms": 3.764, "miss_ms": 7.97,
            "source": "TASK-S4-02_验收报告（全量埋点下三档实测）",
            "note": "含链式审计+埋点的端到端耗时；降噪开关 CP_POLICY_OBSERVE_SCOPE=governance",
        },
        "comparability": "two_scopes_not_interchangeable",
    }
    return out


def observability_stream(*, days: int = 7, limit: int = 200,
                         events_dir: Optional[str] = None) -> Dict[str, Any]:
    """事件流消费面板（S2-03 遗留 #12）：ACR/UTC + 模型降级拓扑 + 逃逸清单

    四类面板**均非 mock**：分别消费 ``acr.py`` / ``utc.py`` /
    ``model_degrade.py`` / ``escape.py`` 的既有聚合函数。
    """
    limit = _clamp(limit, high=500)
    out: Dict[str, Any] = {"ok": True, "generated_at":
                           datetime.now().isoformat(timespec="seconds")}

    try:
        from agent.observability.acr import acr_snapshot
        out["acr"] = acr_snapshot(days=days, directory=events_dir)
    except Exception as e:  # noqa: BLE001
        out["acr"] = absent(f"acr 不可用: {type(e).__name__}")
    try:
        from agent.observability.utc import utc_snapshot
        out["utc"] = utc_snapshot(days=days, directory=events_dir)
    except Exception as e:  # noqa: BLE001
        out["utc"] = absent(f"utc 不可用: {type(e).__name__}")
    try:
        from agent.observability.model_degrade import degrade_summary
        d = degrade_summary(directory=events_dir)
        out["model_degrade"] = {
            "total": metric(
                d.get("total"), source="agent/observability/model_degrade.py::degrade_summary",
                formula="窗口内 model.degraded 事件条数", unit="count",
                sample_size=int(d.get("total") or 0)),
            "edges": d.get("edges"),          # 降级链拓扑（from -> to 计数）
            "reasons": d.get("reasons"),
            "fallback_attempted": d.get("fallback_attempted"),
            "fallback_succeeded": d.get("fallback_succeeded"),
            "chain": d.get("chain"),
            "error_code": d.get("error_code"),
        }
    except Exception as e:  # noqa: BLE001
        out["model_degrade"] = absent(f"model_degrade 不可用: {type(e).__name__}")
    try:
        from agent.observability.escape import escape_summary
        esc = escape_summary(directory=events_dir)
        out["escape"] = {
            "total": metric(
                esc.get("total"), source="agent/observability/escape.py::escape_summary",
                formula="窗口内 escape 事件条数（受治理文件被绕过写入）",
                unit="count", sample_size=int(esc.get("total") or 0)),
            "by_path": esc.get("by_path"),
            "by_reason": esc.get("by_reason"),
            "ledger": esc.get("ledger"),
            "watched": esc.get("watched"),
        }
    except Exception as e:  # noqa: BLE001
        out["escape"] = absent(f"escape 不可用: {type(e).__name__}")
    return out


# ════════════════════════════════════════════════════════════
#  §E 自愈事故 + 备份健康
# ════════════════════════════════════════════════════════════


def incidents_view(*, incidents_dir: Optional[str] = None, limit: int = 100,
                   events_dir: Optional[str] = None, days: int = 7) -> Dict[str, Any]:
    """自愈事故面板（P1；**有事故自动展开**）

    数据源：``IncidentCard.to_dict()`` + ``list_incidents()``（S4-03）+
    ``healing.triggered`` 事件的 ``mttd_ms``/``mttr_ms``（S5-02 契约）。
    """
    limit = _clamp(limit, high=500)
    cards: List[Dict[str, Any]] = []
    err = ""
    try:
        from agent.self_healing.levels import list_incidents
        for c in list_incidents(directory=incidents_dir):
            cards.append(c.to_dict())
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    cards.sort(key=lambda c: str(c.get("created_at") or ""), reverse=True)
    open_cards = [c for c in cards if str(c.get("status")) == "open"]

    mtt = _healing_latency(events_dir=events_dir, days=days)
    auto_expand = len(open_cards) > 0
    return {
        "ok": True,
        "panel": panel_map("incident"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "load_error": err,
        # §7：自愈事故面板「有事故自动展开」——由后端给出该判定
        "auto_expand": auto_expand,
        "auto_expand_rule": "存在 status=open 的事故卡即自动展开（§7）",
        "open_count": metric(
            len(open_cards), source="agent/self_healing/levels.py::list_incidents",
            formula="status=open 的事故卡数", unit="count", sample_size=len(cards)),
        "total_count": metric(
            len(cards), source="agent/self_healing/levels.py::list_incidents",
            formula="事故卡总数（<incidents_dir>/inc-*.json）", unit="count",
            sample_size=len(cards)),
        "incidents": cards[:limit],
        "mttd_ms": mtt,
        "backup_health": _backup_health(),
    }


def _healing_latency(*, events_dir: Optional[str] = None,
                     days: int = 7) -> Dict[str, Any]:
    """MTTD / MTTR（取自 ``healing.triggered`` 事件，S5-02 契约字段）"""
    try:
        from agent.observability.events import EV_HEALING_TRIGGERED, iter_events
        since = (date.today() - timedelta(days=max(1, int(days)) - 1)).isoformat()
        rows = iter_events(types=(EV_HEALING_TRIGGERED,), since=since,
                           directory=events_dir)
    except Exception as e:  # noqa: BLE001
        return dict(absent(f"healing.triggered 读取失败: {type(e).__name__}"))
    mttd = [float(p["mttd_ms"]) for p in (r.payload or {} for r in rows)
            if isinstance(p.get("mttd_ms"), (int, float))]
    mttr = [float(p["mttr_ms"]) for p in (r.payload or {} for r in rows)
            if isinstance(p.get("mttr_ms"), (int, float))]
    levels: Dict[str, int] = {}
    for r in rows:
        lv = str((r.payload or {}).get("level") or "?")
        levels[lv] = levels.get(lv, 0) + 1
    n = len(rows)
    return {
        "events": n,
        "by_level": dict(sorted(levels.items())),
        "mttd_ms": metric(
            _median(mttd), source="agent/observability/events.py::healing.triggered.mttd_ms",
            formula="窗口内 mttd_ms 中位数（缺该字段的事件不计入，不补 0）",
            unit="ms", sample_size=len(mttd),
            dataset=f"最近 {days} 天"),
        "mttr_ms": metric(
            _median(mttr), source="agent/observability/events.py::healing.triggered.mttr_ms",
            formula="窗口内 mttr_ms 中位数（缺该字段的事件不计入，不补 0）",
            unit="ms", sample_size=len(mttr),
            dataset=f"最近 {days} 天"),
        "mttd_samples": len(mttd), "mttr_samples": len(mttr),
        "note": ("MTTD/MTTR 只在事件**带该字段**时计入；未观测到即为 None"
                 "（§0.3：不以 0 冒充）"),
    }


def _median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    xs = sorted(values)
    mid = len(xs) // 2
    return round(xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2.0, 3)


def _backup_health() -> Dict[str, Any]:
    """备份健康卡片（§8.6 / P7.1-14；**沿用既有** disaster_recovery）

    Note:
        ``backup.health`` 事件类型**已注册但无发射方**（S2-03 亦无），
        故本卡片直接消费既有 ``DisasterRecovery.get_status()``；
        事件缺位如实标注，不伪造 ``backup.health`` 记录。
    """
    try:
        from agent.disaster_recovery import get_disaster_recovery
        dr = get_disaster_recovery()
        status = dr.get_status()
        backups = dr.get_backup_list()
    except Exception as e:  # noqa: BLE001
        return dict(absent(f"disaster_recovery 不可用: {type(e).__name__}"))
    latest = status.get("latest_backup")
    latest_row = None
    for b in backups:
        if getattr(b, "backup_id", None) == latest:
            latest_row = {
                "backup_id": b.backup_id, "timestamp": b.timestamp,
                "backup_type": str(getattr(b.backup_type, "value", b.backup_type)),
                "checksum": b.checksum, "size": b.size,
                "providers": list(b.providers or []),
            }
            break
    return {
        "source": "agent/disaster_recovery.py::DisasterRecovery.get_status()（既有）",
        "config": status.get("config"),
        "backup_count": metric(
            status.get("backup_count"),
            source="agent/disaster_recovery.py::get_status()",
            formula="备份目录内可用备份数", unit="count",
            sample_size=int(status.get("backup_count") or 0)),
        "latest_backup": latest_row,
        "recovery_status": status.get("recovery_status"),
        "scheduler_running": status.get("scheduler_running"),
        "event_note": ("事件类型 backup.health 已注册但当前无发射方（S2-03 亦未发射），"
                       "故本卡片按既有 DR 子系统如实呈现，未伪造事件记录"),
    }


# ════════════════════════════════════════════════════════════
#  §F 记忆 / 技能库
# ════════════════════════════════════════════════════════════


def memory_skills_view(*, layers: Optional[Iterable[str]] = None,
                       tenant_id: str = "", query: str = "", limit: int = 50,
                       store: Any = None) -> Dict[str, Any]:
    """记忆 / 技能库面板（P2 折叠）

    U5「记忆→组装注入」接线：召回优先级由
    ``agent/memory/taxonomy.py::recall_priority_key()`` **单一定义**
    （策略 > 事实 > 偏好），本面板把它作为**可解释性字段**透出，
    并给出组装注入的顺序契约（``assembly_contract``）。
    """
    limit = _clamp(limit, high=200)
    out: Dict[str, Any] = {
        "ok": True,
        "panel": panel_map("memory_skills"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    # ── 优先级口径（U5：单一来源，前端不得自定义）──
    try:
        from agent.memory.taxonomy import LAYER_ORDER, recall_priority_key
        out["recall_priority"] = {
            "source": "agent/memory/taxonomy.py::recall_priority_key()",
            "order": [t.value for t in LAYER_ORDER],
            "formula": ("策略 > 事实 > 偏好（P7.2-10）；同层 project 事实 > global 偏好，"
                        "同级新者胜（§4.3）"),
            "key_fields": "recall_priority_key(entry) -> (层序, scope序, -created_at, id)",
            "contract": ("组装注入（P7.2-10 ContextAssembler system 区）按本顺序；"
                         "本面板只呈现，不改变召回结果"),
            "callable": callable(recall_priority_key),
        }
    except Exception as e:  # noqa: BLE001
        out["recall_priority"] = absent(f"taxonomy 不可用: {type(e).__name__}")

    # ── 分层统计（一次召回 + 按层分组；不跨租户串扰由 recall 内部保证）──
    stats: Dict[str, Any] = {}
    try:
        from agent.memory.layered_store import LayeredMemoryStore
        st = store if store is not None else LayeredMemoryStore()
        rows = _await_if_needed(st.recall(
            query or "", limit=limit, tenant_id=tenant_id or None))
        entries = [_memory_card(r) for r in (rows or [])]
        wanted = {str(x).lower() for x in (layers or [])}
        if wanted:
            entries = [e for e in entries if e["layer"].lower() in wanted]
        samples = len(entries)
        stats = {
            "by_layer": _count(entries, "layer"),
            "entries": entries[:limit],
            "total": metric(
                samples, source="agent/memory/layered_store.py::LayeredMemoryStore.recall()",
                formula="单次召回的返回条目数（已含隔离可见性过滤 + §4.3 优先级排序）",
                unit="count", sample_size=samples),
        }
    except Exception as e:  # noqa: BLE001
        stats = dict(absent(f"LayeredMemoryStore 不可用: {type(e).__name__}: {e}"))
    out["layers"] = stats
    return out


def _await_if_needed(value: Any) -> Any:
    """``recall()`` 为 async；同步路由内跑完（无事件循环时直接 run）"""
    if not hasattr(value, "__await__"):
        return value
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        # 已在事件循环内：交给新线程跑完（避免嵌套 loop 报错）
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, value).result()
    return asyncio.run(value)


def _memory_card(entry: Any) -> Dict[str, Any]:
    """记忆条目卡片（§3.11 字段逐字映射；``content_redacted`` 已是脱敏文本）

    注意：这里**不导出** ``content_redacted`` 全文之外的新字段，也不回填
    ``content_hash`` 之外的原文——脱敏发生在哈希之前（§3.4），面板拿到的是脱敏后文本。
    """
    def _g(name: str, default: Any = None) -> Any:
        return getattr(entry, name, default)

    mtype = _g("type")
    layer = str(getattr(mtype, "value", mtype) or "")
    ttl_expires = _g("ttl_expires_at")
    ttl_seconds = None
    if isinstance(ttl_expires, (int, float)):
        created = _g("created_at")
        if isinstance(created, (int, float)):
            ttl_seconds = round(float(ttl_expires) - float(created), 3)
    return {
        "memory_id": str(_g("id", "") or ""),
        "layer": layer,
        "tenant_id": str(_g("tenant_id", "") or ""),
        "subject_id": str(_g("subject_id", "") or ""),
        "scope": str(_g("scope", "") or ""),
        "scope_kind": ("project" if str(_g("scope", "") or "").startswith("project:")
                       else "global"),
        "content_redacted": str(_g("content_redacted", "") or "")[:200],
        "content_hash": str(_g("content_hash", "") or ""),
        "confidence": _g("confidence"),
        "created_at": _g("created_at"),
        "ttl_expires_at": ttl_expires,
        "ttl_seconds": ttl_seconds,
        "forget_candidate": bool(_g("forget_candidate", False)),
        "degraded": bool(_g("degraded", False)),
        "degradation_reason": str(_g("degradation_reason", "") or ""),
        "org_level": bool(_g("org_level", False)),
        "source_capability_id": str(_g("source_capability_id", "") or ""),
        "schema_version": _g("schema_version"),
    }


# ════════════════════════════════════════════════════════════
#  §G 审计导出（含验签摘要）
# ════════════════════════════════════════════════════════════


def audit_export(*, limit: int = 200, start_seq: Optional[int] = None,
                 end_seq: Optional[int] = None, action: str = "",
                 actor: str = "", day: str = "", verify: bool = True,
                 verify_scope: str = "exported", chain: Any = None) -> Dict[str, Any]:
    """审计导出（S2-02）：链式台账 + **验签摘要**

    验签摘要 = ``verify_chain()`` 的 ``ChainVerification.to_dict()``
    （``ok / checked / first_bad_seq / bad_seqs / reason / detail``）+ 链头
    （``last_seq`` / ``head_self_hash``）。**导出内容与验签结果同出一源**，
    故导出文件自证其完整性。

    Args:
        verify_scope: 验签范围口径（**性能与语义都要求显式标注**）：
            ``"exported"``（默认）只验**本次导出区间**——与导出内容同口径，
            且避免全链重算（实测全链 19615 条 ≈0.55–0.94s）破坏聚合预算；
            ``"full"`` 全链重算（**慢**，供审计员显式要求）；
            ``"head"`` 只验最近 ``limit`` 条尾部窗口。
    """
    limit = _clamp(limit, high=2000, default=200)
    err = ""
    entries: List[Dict[str, Any]] = []
    verification: Dict[str, Any] = {}
    head: Dict[str, Any] = {}
    import time as _t
    t0 = _t.perf_counter()
    try:
        c = chain
        if c is None:
            from agent.audit.chain import get_audit_chain
            c = get_audit_chain()
        rows = c.entries(start_seq=start_seq, end_seq=end_seq,
                         action=action or None, actor=actor or None,
                         day=day or None, limit=limit)
        entries = [e.to_dict() for e in rows]
        head = c.chain_head()
        if verify:
            if verify_scope == "full":
                v = c.verify_chain()
            elif verify_scope == "head":
                seqs = [int(e.get("seq") or 0) for e in entries]
                lo = min(seqs) if seqs else None
                v = c.verify_chain(start_seq=lo) if lo else c.verify_chain()
            else:
                seqs = [int(e.get("seq") or 0) for e in entries]
                lo = start_seq if start_seq is not None else (min(seqs) if seqs else None)
                hi = end_seq if end_seq is not None else (max(seqs) if seqs else None)
                v = c.verify_chain(start_seq=lo, end_seq=hi) if lo else c.verify_chain()
            verification = v.to_dict()
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    elapsed_ms = round((_t.perf_counter() - t0) * 1000, 3)
    n = len(entries)
    return {
        "ok": not err,
        "panel": panel_map("audit_export"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "load_error": err,
        "filters": {"start_seq": start_seq, "end_seq": end_seq,
                    "action": action, "actor": actor, "day": day,
                    "limit": limit},
        "count": n,
        "exported": metric(
            n, source="agent/audit/chain.py::AuditChain.entries()",
            formula="按筛选条件导出的链式审计记录条数", unit="count",
            sample_size=n),
        "verify": verification,
        "verify_scope": verify_scope,
        "elapsed_ms": metric(
            elapsed_ms, source="本端点实测（time.perf_counter）",
            formula="读记录 + 验签的端到端耗时（含验签 ⇒ 口径见 verify_scope）",
            unit="ms",
            note=("verify_scope=exported 时与导出区间同口径；full 会全链重算，"
                  "耗时随链长线性增长（口径不得混用）")),
        "chain_head": head,
        "entries": entries,
        "disclosure": ("导出含验签摘要：verify.ok 为真表示从锚点重算的自哈希链一致；"
                       "bad_seqs 列出首个篡改点及其后全部异常记录（S2-02 §11.10）"),
    }


def audit_export_csv(payload: Mapping[str, Any]) -> str:
    """把 ``audit_export`` 的 entries 渲染成 CSV（首行附验签摘要注释）"""
    rows = list(payload.get("entries") or [])
    cols = ["seq", "ts", "actor", "action", "subject", "source", "status",
            "payload_hash", "prev_hash", "self_hash", "trace_id", "task_id",
            "workspace_id", "subject_id"]
    buf = io.StringIO()
    v = payload.get("verify") or {}
    buf.write(f"# verify_ok={v.get('ok')} checked={v.get('checked')} "
              f"first_bad_seq={v.get('first_bad_seq')} reason={v.get('reason')}\n")
    buf.write(f"# chain_head={json.dumps(payload.get('chain_head') or {}, ensure_ascii=False)}\n")
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in cols})
    return buf.getvalue()


# ════════════════════════════════════════════════════════════
#  §H 安全渲染 / 边界词（U1：常量单一来源）
# ════════════════════════════════════════════════════════════


def security_render_state() -> Dict[str, Any]:
    """机制 6 状态快照（U1 前端常量来源）

    ★ **前端不得自定义**：五类边界词、60s 上限、
    ``accepts_text_approval=False``、TaintBadge 三个 class 名、
    审批区 ``z-index`` 固定值，一律取本端点返回值。
    """
    out: Dict[str, Any] = {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        from agent.guardrails.safe_render import safe_render_state
        out["safe_render"] = safe_render_state()
    except Exception as e:  # noqa: BLE001
        out["safe_render"] = absent(f"safe_render 不可用: {type(e).__name__}")
    try:
        from agent.guardrails.boundary_words import boundary_state
        out["boundary_words"] = boundary_state()
    except Exception as e:  # noqa: BLE001
        out["boundary_words"] = absent(f"boundary_words 不可用: {type(e).__name__}")
    try:
        from agent.guardrails.injection_defense import defense_status
        # defense_status() 已聚合 6 机制（含 5 边界词 / 6 安全渲染），全量透出
        out["injection_defense"] = defense_status()
    except Exception as e:  # noqa: BLE001
        out["injection_defense"] = absent(f"injection_defense 不可用: {type(e).__name__}")
    out["frontend_contract"] = {
        "must_not_customize": [
            "边界词五类（NEVER_AUTOMATED）", "确认时效硬上限 60s",
            "accepts_text_approval（恒 False）", "single_action_bound（恒 True）",
            "TaintBadge 的 class / base_class / attr",
            "审批区 z-index 固定值 + isolation + contain",
        ],
        "rule": "U1：前端按既有常量渲染，勿自定义（本端点即常量单一来源）",
    }
    return out
