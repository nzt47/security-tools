"""L2 Core-50 基线（TASK-S5-02 步骤 2/3）——**UTC 基线唯一依据**

本模块把三路输入合成为一份可复核的 L2 基线快照：

1. **用例集与判定结果**：L2 Core-50 的清单、哈希、逐场景规模、本次运行结论；
2. **真实性能与样本量**（S3-03 已交付，直接消费，**不另建测量**）：
   * `agent.digestion.shadow.ShadowReport.p99_wall_*` —— **真实墙钟**口径的候选/上游 p99；
   * `agent.digestion.shadow.ShadowLedger.daily_average()` —— 能力级样本量趋势；
   * `ShadowLedger.rows()` —— 灰度期通过率（技能成功率）的分子分母；
3. **成本/UTC**：`agent.observability.utc.utc_window()`（与 S5-03 同源，**不重算**）。

## 两条"不许含糊"的口径

* **clock 口径必须标明**：L2 运行耗时用 ``wall_clock(perf_counter)``；
  灰度 p99 用 S3-03 的 ``CLOCK_WALL = "wall_clock(perf_counter; per-arm real elapsed)"``
  （S3-02 的 p99 是模型时钟量，已被 S3-03 的 M2 收口，本模块不使用模型时钟）。
* **样本充分性单独判定**：基线快照本身可以有值，但"是否够用来校准"必须单独回答，
  且不足时**如实列出缺口**（对齐口径纪律"每能力 ≥20 条同类轨迹"）。

## 与 S5-03 的接口（Owner 裁定 C）

裁定 C 的触发条件是"L2 就绪后启动成本系数校准评估"。本模块输出
``calibration_trigger``：

* ``l2_dataset_ready``：L2 Core-50 数据集与基线口径是否就绪（本任务交付 → True）；
* ``cost_samples_adequate``：**真实成本事件样本**是否达到每模型 ≥20 条；
* ``status``：``unlocked``（L2 就绪，可启动校准评估流程）/ ``blocked``（缺数据源）。

"触发条件解锁" ≠ "校准已完成"：后者需要真实成本样本，本任务不代 S5-03 裁定。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import agent.eval.anchor as A
import agent.eval.cases as C
import agent.eval.metrics as M
import agent.eval.runner as R
from agent.observability import utc as UTC

logger = logging.getLogger("agent.eval.baseline")

#: 基线快照 schema
BASELINE_SCHEMA = "eval.l2_baseline.v1"

#: 样本充分性门槛（口径纪律：每能力 ≥20 条同类轨迹）
MIN_SAMPLES_PER_CAPABILITY = 20
#: 成本系数校准的最小成本事件样本（每模型）
MIN_COST_SAMPLES_PER_MODEL = 20

#: 基线默认落点（运行期产物目录，与锚分离；已在 .gitignore 中排除漂移）
DEFAULT_BASELINE_PATH = os.path.join(A._REPO_ROOT, "data", "eval", "l2_baseline.json")

#: Owner 裁定 C 的原文引用（报告与结案材料直接引用，避免口径漂移）
OWNER_DECISION_C = ("沿用价格锚定系数表（本次不校准）；触发条件：L2 就绪后启动"
                    "校准评估（季度复校）——见 00_总览「待裁定项」C")


def default_baseline_path() -> str:
    return DEFAULT_BASELINE_PATH


# ════════════════════════════════════════════════════════════
#  三路数据源（全部复用既有接口）
# ════════════════════════════════════════════════════════════


def shadow_inputs(*, shadow_dir: str = "") -> Dict[str, Any]:
    """S3-03 灰度数据：真实墙钟 p99 + 能力级样本量趋势 + 通过率（复用其公开接口）"""
    out: Dict[str, Any] = {
        "source": "agent.digestion.shadow.ShadowLedger（S3-03 交付）",
        "available": False, "rows": 0, "by_capability": {},
        "p99_wall_candidate_ms": None, "p99_wall_upstream_ms": None,
        "pass_rate": None, "clock": "",
    }
    try:
        from agent.digestion.shadow import CLOCK_MODEL, CLOCK_WALL, ShadowLedger
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"shadow 模块不可导入: {type(e).__name__}: {e}"
        return out
    out["clock"] = CLOCK_WALL
    out["model_clock_excluded"] = CLOCK_MODEL
    directory = shadow_dir or os.getenv("CP_DIGESTION_SHADOW_DIR") or ""
    if not directory:
        out["reason"] = ("未提供 --shadow-dir 且未设 CP_DIGESTION_SHADOW_DIR："
                         "不触碰运行时目录，灰度输入置空")
        return out
    try:
        ledger = ShadowLedger(directory=directory)
        rows = ledger.rows()
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"灰度台账读取失败: {type(e).__name__}: {e}"
        return out
    out["rows"] = len(rows)
    out["available"] = bool(rows)
    if not rows:
        out["reason"] = f"灰度台账为空（{directory}）"
        return out
    passed = sum(int(r.get("passed") or 0) for r in rows)
    sampled = sum(int(r.get("sampled") or 0) for r in rows)
    candidates = [float(r["p99_wall_candidate_ms"]) for r in rows
                  if r.get("p99_wall_candidate_ms") is not None]
    upstreams = [float(r["p99_wall_upstream_ms"]) for r in rows
                 if r.get("p99_wall_upstream_ms") is not None]
    out["p99_wall_candidate_ms"] = max(candidates) if candidates else None
    out["p99_wall_upstream_ms"] = max(upstreams) if upstreams else None
    out["pass_rate"] = round(passed / sampled, 6) if sampled else None
    out["sampled"] = sampled
    out["passed"] = passed
    by_cap: Dict[str, Any] = {}
    for capability_id in sorted({str(r.get("capability_id") or "") for r in rows} - {""}):
        by_cap[capability_id] = {
            "runs": len([r for r in rows if str(r.get("capability_id") or "") == capability_id]),
            "daily_average_samples": ledger.daily_average(capability_id=capability_id),
            "adequate_for_calibration": ledger.daily_average(
                capability_id=capability_id) >= MIN_SAMPLES_PER_CAPABILITY,
        }
    out["by_capability"] = by_cap
    return out


def trace_inputs(*, trace_db: str = "") -> Dict[str, Any]:
    """轨迹台账：能力级同类轨迹数（"是否够校准"的直接依据）

    只在**显式给出** ``trace_db`` 或默认库**已存在**时打开（避免创建运行时文件）。
    """
    try:
        from agent.observability import trace_v2
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"trace_v2 不可导入: {e}"}
    path = str(trace_db or trace_v2._DEFAULT_DB_PATH)
    if not trace_db and not os.path.exists(path):
        return {"available": False, "path": path, "total": 0,
                "reason": "统一轨迹库不存在（不创建运行时文件）"}
    store = None
    try:
        store = trace_v2.UnifiedTraceStore(path)
        stats = store.snapshot_stats()
    except Exception as e:  # noqa: BLE001
        return {"available": False, "path": path, "reason": f"台账读取失败: {e}"}
    finally:
        if store is not None:
            try:
                store.stop(timeout=2.0)
            except Exception:  # noqa: BLE001 关闭失败不影响基线取值
                pass
    by_capability = {str(k): int(v) for k, v in (stats.get("by_capability") or {}).items()}
    adequate = {k: v >= MIN_SAMPLES_PER_CAPABILITY for k, v in by_capability.items()}
    return {
        "available": True, "path": path, "total": int(stats.get("total") or 0),
        "success_rate": stats.get("success_rate"),
        "by_capability": by_capability,
        "adequate_by_capability": adequate,
        "capabilities_with_adequate_samples": sorted(
            k for k, ok in adequate.items() if ok),
        "min_samples_per_capability": MIN_SAMPLES_PER_CAPABILITY,
    }


def cost_inputs(*, start: str, end: str, events_dir: Optional[str] = None) -> Dict[str, Any]:
    """UTC/成本窗口（复用 `utc.utc_window`）→ 校验成本样本充分性"""
    window = UTC.utc_window(start=start, end=end, directory=events_dir)
    by_model = dict(window.get("by_model") or {})
    total_cost_events = sum(int(v.get("calls") or 0) for v in by_model.values())
    adequate = total_cost_events >= MIN_COST_SAMPLES_PER_MODEL
    return {
        "source": "agent.observability.utc.utc_window(start, end)（S2-03 交付）",
        "window": {"start": start, "end": end},
        "utc_cents_per_task": window.get("utc_cents_per_task"),
        "utc_cents_per_task_acr_cohort": window.get("utc_cents_per_task_acr_cohort"),
        "cost_normalized_cents": window.get("cost_normalized_cents"),
        "cache_hit_rate": window.get("cache_hit_rate"),
        "anchor_model": window.get("anchor_model"),
        "tasks": dict(window.get("tasks") or {}),
        "llm_calls": window.get("llm_calls"),
        "by_model": by_model,
        "min_cost_samples_per_model": MIN_COST_SAMPLES_PER_MODEL,
        "cost_samples": total_cost_events,
        "cost_samples_adequate": adequate,
        "utc_formula": window.get("utc_formula"),
    }


# ════════════════════════════════════════════════════════════
#  组装基线
# ════════════════════════════════════════════════════════════


def build_l2_baseline(*, report: Optional[R.EvalReport] = None,
                      case_set: Optional[C.EvalCaseSet] = None,
                      days: int = 7, start: str = "", end: str = "",
                      events_dir: Optional[str] = None,
                      shadow_dir: str = "", trace_db: str = "",
                      registry: Any = None, registry_path: str = "",
                      annotations: Optional[Sequence[Mapping[str, Any]]] = None,
                      delegations: Optional[Sequence[Mapping[str, Any]]] = None,
                      upstream_rate: Optional[float] = None,
                      ) -> Dict[str, Any]:
    """构建 L2 Core-50 基线快照（用例集 + 真实性能/样本量 + UTC + 充分性 + 触发条件）"""
    begin, finish = M.resolve_window(days=days, start=start, end=end)
    span = (date.fromisoformat(finish) - date.fromisoformat(begin)).days + 1
    if case_set is None:
        # 缺省从 L2 标准落点加载数据集（不存在则如实留空，不伪造条数）
        candidate = ((report.caseset_path if report is not None else "")
                     or R.LAYER_CASESET_PATHS[C.LAYER_L2])
        if candidate and os.path.exists(candidate):
            case_set = C.load_case_set(candidate)
    caps = case_set.cases if case_set is not None else ()
    shadow = shadow_inputs(shadow_dir=shadow_dir)
    traces = trace_inputs(trace_db=trace_db)
    cost = cost_inputs(start=begin, end=finish, events_dir=events_dir)
    metric_report = M.compute_metrics(days=days, start=begin, end=finish,
                                      events_dir=events_dir, registry=registry,
                                      registry_path=registry_path,
                                      shadow_dir=shadow_dir,
                                      annotations=annotations,
                                      delegations=delegations,
                                      upstream_rate=upstream_rate)

    evidence = {
        "l2_dataset_cases": len(caps),
        "by_scenario": (C.EvalCaseSet(layer=C.LAYER_L2, cases=caps).scenario_counts()
                        if caps else {}),
        "caseset_sha256": (case_set.caseset_sha256 if case_set is not None else ""),
        "run": (report.to_dict() if report is not None else None),
    }
    shortfall: List[str] = []
    contract_size = C.LAYER_SIZES[C.LAYER_L2] or 0
    if len(caps) < contract_size:
        shortfall.append(f"L2 用例数 {len(caps)} < 契约 {contract_size}")
    if not shadow.get("available"):
        shortfall.append("灰度台账无样本（真实墙钟 p99 与样本量趋势不可用）："
                         f"{shadow.get('reason', 'n/a')}")
    if not traces.get("available") or not traces.get("capabilities_with_adequate_samples"):
        shortfall.append(
            f"轨迹台账未达「每能力 ≥{MIN_SAMPLES_PER_CAPABILITY} 条同类轨迹」"
            f"（源 {traces.get('path') or 'n/a'}）")
    if not cost.get("cost_samples_adequate"):
        shortfall.append(
            f"成本事件样本 {cost.get('cost_samples')} < {MIN_COST_SAMPLES_PER_MODEL}"
            "（不足以拟合模型成本系数）")

    l2_ready = bool(caps) and len(caps) >= (C.LAYER_SIZES[C.LAYER_L2] or 0)
    trigger = {
        "owner_decision": "C",
        "owner_decision_text": OWNER_DECISION_C,
        "l2_dataset_ready": l2_ready,
        "cost_samples_adequate": bool(cost.get("cost_samples_adequate")),
        "cost_samples": cost.get("cost_samples"),
        "min_cost_samples_required": MIN_COST_SAMPLES_PER_MODEL,
        "status": "unlocked" if l2_ready else "blocked",
        "what_is_unlocked": ("L2 Core-50 数据集与基线口径已就绪 → S5-03 可启动"
                             "「跨模型成本-效果校准」流程（口径、数据源、触发条件均已定义）"),
        "what_is_not_claimed": ("本任务**不声称**校准已完成：真实成本样本未达阈值前，"
                                "系数仍沿用价格锚定表（沿用而非拟合）"),
        "shortfall": shortfall,
    }

    baseline = {
        "schema": BASELINE_SCHEMA,
        "layer": C.LAYER_L2,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "window": {"start": begin, "end": finish, "days": span},
        "clock": {
            "case_run": R.CLOCK_WALL,
            "shadow_p99": shadow.get("clock") or "（未提供灰度台账）",
            "model_clock_excluded": shadow.get("model_clock_excluded"),
        },
        "caseset": evidence,
        "performance": {
            "source": "agent.eval.runner（用例耗时，单调墙钟）",
            "p99_wall_ms": (report.p99_wall_ms() if report is not None else None),
            "by_scenario": (report.by_scenario() if report is not None else {}),
            "pass_rate": (report.pass_rate if report is not None else None),
            "assessed": (report.assessed if report is not None else 0),
            "unassessed": (report.unassessed if report is not None else 0),
        },
        "shadow_inputs": shadow,
        "trace_inputs": traces,
        "cost_inputs": cost,
        "metrics": metric_report["metrics"],
        "metric_sources": metric_report["sources"],
        "sample_adequacy": {
            "min_samples_per_capability": MIN_SAMPLES_PER_CAPABILITY,
            "min_cost_samples_per_model": MIN_COST_SAMPLES_PER_MODEL,
            "capabilities_with_adequate_samples": traces.get(
                "capabilities_with_adequate_samples", []),
            "adequate": bool(l2_ready) and not shortfall,
            "shortfall": shortfall,
        },
        "calibration_trigger": trigger,
        "disclosures": [
            "L2 Core-50 是**UTC 基线的唯一依据**（§6.5）：容量、场景覆盖与哈希随本快照冻结",
            "性能数字均为**真实墙钟**口径（S3-03 的 p99_wall_* 已收口模型时钟量）",
            ("真实流量未达「每能力 ≥20 条同类轨迹」时，**不得**据此声称"
             "「已实现真实能力内化」（口径纪律）"),
            ("本基线不含模型能力结论：判定结论来自所声明的 solver（reference 自检 / "
             "mutant 区分度 / 真实解算器）"),
        ],
    }
    return baseline


def write_l2_baseline(baseline: Mapping[str, Any], path: str = "") -> str:
    """写出基线快照（先过 `anchor.guard_write`：绝不写进锚目录）"""
    target = path or DEFAULT_BASELINE_PATH
    A.guard_write(target)
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(baseline, ensure_ascii=False, indent=2, default=str) + "\n")
    return target


def load_l2_baseline(path: str = "") -> Dict[str, Any]:
    """读基线快照（缺失/非法 → 空字典）"""
    target = path or DEFAULT_BASELINE_PATH
    if not os.path.exists(target):
        return {}
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("L2 基线文件非法（%s）: %s", target, e)
        return {}
    return dict(data) if isinstance(data, Mapping) else {}


def baseline_markdown(baseline: Mapping[str, Any]) -> str:
    """基线摘要（验收报告/面板用；每个数字带来源）"""
    trigger = dict(baseline.get("calibration_trigger") or {})
    cost = dict(baseline.get("cost_inputs") or {})
    shadow = dict(baseline.get("shadow_inputs") or {})
    traces = dict(baseline.get("trace_inputs") or {})
    perf = dict(baseline.get("performance") or {})
    caseset = dict(baseline.get("caseset") or {})
    lines = [
        "# L2 Core-50 基线快照",
        "",
        f"- 用例集：{caseset.get('l2_dataset_cases')} 条"
        f"（sha256 `{str(caseset.get('caseset_sha256') or '')[:12]}`）"
        f"｜场景分布 {caseset.get('by_scenario')}",
        f"- 本次运行：pass_rate={perf.get('pass_rate')}"
        f"（已评测 {perf.get('assessed')} / 未评测 {perf.get('unassessed')}）",
        f"- 用例耗时 p99 = {perf.get('p99_wall_ms')} ms（clock: {R.CLOCK_WALL}）",
        f"- 灰度真实墙钟 p99：候选 {shadow.get('p99_wall_candidate_ms')} ms ≤ 上游 "
        f"{shadow.get('p99_wall_upstream_ms')} ms（clock: {shadow.get('clock') or '—'}）",
        f"- 成本/UTC：UTC = {cost.get('utc_cents_per_task')} cents/任务"
        f"（归一成本 {cost.get('cost_normalized_cents')}，锚模型 {cost.get('anchor_model')}）",
        f"- 轨迹台账：{traces.get('total')} 条；达标的（≥"
        f"{MIN_SAMPLES_PER_CAPABILITY} 条）能力 {traces.get('capabilities_with_adequate_samples')}",
        f"- 校准触发条件（Owner 裁定 C）：**{trigger.get('status')}**",
        f"  - 就绪项：L2 数据集 = {trigger.get('l2_dataset_ready')}",
        f"  - 未就绪项：成本样本 {trigger.get('cost_samples')} / "
        f"{trigger.get('min_cost_samples_required')}；{trigger.get('what_is_not_claimed')}",
    ]
    shortfall = list((baseline.get("sample_adequacy") or {}).get("shortfall") or [])
    if shortfall:
        lines += ["", "**样本充分性缺口**"] + [f"- {item}" for item in shortfall]
    return "\n".join(lines)


__all__ = [
    "BASELINE_SCHEMA", "MIN_SAMPLES_PER_CAPABILITY", "MIN_COST_SAMPLES_PER_MODEL",
    "DEFAULT_BASELINE_PATH", "OWNER_DECISION_C", "default_baseline_path",
    "shadow_inputs", "trace_inputs", "cost_inputs", "build_l2_baseline",
    "write_l2_baseline", "load_l2_baseline", "baseline_markdown",
]
