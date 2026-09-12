"""既有指标的**复算**（TASK-S8-01 步骤 4「防篡改历史」）。

【本模块只做一件事：把既有指标读出来，前后各读一次，断言一致】
    它**不定义任何新指标口径**，只调用既有事实源：

        utc.weekly             `agent.observability.utc.utc_weekly()`（读事件分片）
        digestion.throughput   `ShadowLedger.rows()` + `ManualReviewQueue.summary()`
        audit.chain            `AuditChain.reader().verify_chain()` + `count()`

    为什么这是硬要求：归档若把读端看不见的数据搬走，指标就会"变小"，
    等于**篡改历史**（批次总表 §二③）。所以"归档前后指标一致"不是锦上添花，
    而是归档方式是否合格的判据 —— 不一致就说明该类必须改成
    「保留聚合摘要 + 明细归档」双轨。

【只读纪律】
    所有采集都在**存在性守卫**下进行：库/目录不存在时返回 `available=False`，
    **绝不构造会创建文件的 store**（否则 dry-run 就不再是"不落盘"）。
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from agent.retention.policy import PROJECT_ROOT

logger = logging.getLogger("agent.retention.metrics")

#: 受守护的既有指标清单（键 = 指标名，值 = 采集函数名）
METRIC_NAMES = ("utc.weekly", "digestion.throughput", "audit.chain")

#: 默认落点（与各模块既有常量同源）
DEFAULT_EVENTS_DIR = os.path.join(PROJECT_ROOT, "data", "events")
DEFAULT_SHADOW_DIR = os.path.join(PROJECT_ROOT, "data", "digestion", "shadow")
DEFAULT_AUDIT_DB = os.path.join(PROJECT_ROOT, "data", "audit", "audit_chain.db")


class MetricUnavailable(RuntimeError):
    """指标不可采集（事实源不存在）。**不抛给主流程**，由采集层转为 available=False。"""


# ════════════════════════════════════════════════════════════
#  采集器（全部只读；事实源缺失 → available=False）
# ════════════════════════════════════════════════════════════


def utc_weekly_metric(*, events_dir: str = "", anchor_day: str = "") -> Dict[str, Any]:
    """UTC 周成本（`utc.utc_weekly`）。

    事件目录不存在时返回 `available=False`：UTC 由事件流聚合而来，
    "没有事件目录"与"成本为 0"是两件事，**不得混同**（§0.3 口径纪律）。
    """
    directory = os.path.abspath(events_dir or DEFAULT_EVENTS_DIR)
    if not os.path.isdir(directory):
        return {"metric": "utc.weekly", "available": False,
                "reason": f"事件目录不存在：{directory}"}
    try:
        from agent.observability.utc import utc_weekly

        data = utc_weekly(anchor_day or None, directory=directory)
    except Exception as e:  # noqa: BLE001 指标不可用如实标注，不伪造 0
        logger.warning("[metrics] utc.weekly 采集失败：%s", e)
        return {"metric": "utc.weekly", "available": False, "reason": str(e)}
    # **逐字段**快照整份结果（`utc_weekly` 的输出不含时间戳，故可直接全量比对）；
    # 只挑几个字段比对会漏掉"任务数变了但成本没变"这类口径漂移。
    snap: Dict[str, Any] = dict(data)
    snap["available"] = True
    snap["metric"] = "utc.weekly"
    return snap


def digestion_throughput_metric(*, shadow_dir: str = "") -> Dict[str, Any]:
    """消化吞吐（灰度台账 runs / 抽样样本数）+ 人工复核队列状态。

    只读既有接口（`ShadowLedger.rows()` / `ManualReviewQueue.summary()`），
    **不自建聚合口径**；不调 `daily_counts()`（它按"最近 N 天"滚动，跨午夜会漂）。
    """
    directory = os.path.abspath(shadow_dir or DEFAULT_SHADOW_DIR)
    ledger_path = os.path.join(directory, "shadow_ledger.jsonl")
    reviews_path = os.path.join(directory, "manual_reviews.jsonl")
    if not os.path.exists(ledger_path) and not os.path.exists(reviews_path):
        return {"metric": "digestion.throughput", "available": False,
                "reason": f"灰度台账不存在：{directory}"}
    try:
        from agent.digestion.shadow import ManualReviewQueue, ShadowLedger

        rows = ShadowLedger(path=ledger_path).rows()
        queue = ManualReviewQueue(path=reviews_path)
        summary = queue.summary()
    except Exception as e:  # noqa: BLE001
        logger.warning("[metrics] digestion.throughput 采集失败：%s", e)
        return {"metric": "digestion.throughput", "available": False, "reason": str(e)}
    return {
        "metric": "digestion.throughput",
        "available": True,
        "ledger_runs": len(rows),
        "sampled_total": sum(int(r.get("sampled") or 0) for r in rows),
        "capabilities": len({str(r.get("capability_id") or "") for r in rows}),
        "reviews_sampled": int(summary.get("sampled") or 0),
        "reviews_pending": int(summary.get("pending") or 0),
        "reviews_decided": int(summary.get("decided") or 0),
        "reviews_closed": bool(summary.get("closed")),
    }


def audit_chain_metric(*, db_path: str = "") -> Dict[str, Any]:
    """审计链证据完整性（`verify_chain` + 条目数 + 每日根条数）。

    **绝不构造会建库的 reader**：库文件不存在 → `available=False`。
    这条正是"归档不得动链"的机器判据：归档前后 `ok/checked/head_seq` 必须一致。
    """
    path = os.path.abspath(db_path or DEFAULT_AUDIT_DB)
    if not os.path.exists(path):
        return {"metric": "audit.chain", "available": False,
                "reason": f"审计链库不存在：{path}"}
    try:
        from agent.audit.chain import AuditChain

        chain = AuditChain.reader(db_path=path)
    except Exception as e:  # noqa: BLE001
        logger.warning("[metrics] audit.chain 打开失败：%s", e)
        return {"metric": "audit.chain", "available": False, "reason": str(e)}
    try:
        verification = chain.verify_chain()
        head = chain.chain_head()      # 键名取自 `AuditChain.chain_head()` 实际返回
        return {
            "metric": "audit.chain",
            "available": True,
            "ok": bool(getattr(verification, "ok", False)),
            "checked": int(getattr(verification, "checked", 0) or 0),
            "verify_reason": str(getattr(verification, "reason", "") or ""),
            "first_seq": int(head.get("first_seq") or 0),
            "head_seq": int(head.get("last_seq") or 0),
            "head_self_hash": str(head.get("head_self_hash") or ""),
            "count": int(head.get("count") or 0),
            "entries": int(chain.count()),
            "daily_roots": len(chain.read_daily_roots()),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("[metrics] audit.chain 采集失败：%s", e)
        return {"metric": "audit.chain", "available": False, "reason": str(e)}
    finally:
        try:
            chain.close(timeout=1.0)
        except Exception:  # noqa: BLE001 只读链关闭失败无影响
            pass


COLLECTORS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "utc.weekly": utc_weekly_metric,
    "digestion.throughput": digestion_throughput_metric,
    "audit.chain": audit_chain_metric,
}


# ════════════════════════════════════════════════════════════
#  快照与比对
# ════════════════════════════════════════════════════════════


@dataclass
class MetricComparison:
    """单个指标的归档前后对照。"""

    metric: str = ""
    available_before: bool = False
    available_after: bool = False
    equal: bool = False
    changed_fields: List[str] = field(default_factory=list)
    before: Dict[str, Any] = field(default_factory=dict)
    after: Dict[str, Any] = field(default_factory=dict)
    verdict: str = ""      # consistent / changed / unavailable / newly_available

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        if not self.available_before and not self.available_after:
            return f"{self.metric}：两侧均不可采集（{self.verdict}）"
        if self.equal:
            return f"{self.metric}：口径一致 ✅"
        return (f"{self.metric}：**不一致** ❌ 变化字段 "
                f"{self.changed_fields}（{self.verdict}）")


@dataclass
class ConsistencyReport:
    """归档前后指标复算一致性报告（步骤 4 的交付物）。"""

    period: str = ""
    archive_dir: str = ""
    metrics: List[MetricComparison] = field(default_factory=list)
    consistent: bool = False
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["metrics"] = [m if isinstance(m, dict) else m.to_dict()
                        for m in self.metrics]
        return d

    def markdown(self) -> str:
        lines = ["| 指标 | 归档前可采 | 归档后可采 | 结论 | 变化字段 |",
                 "|---|---|---|---|---|"]
        for m in self.metrics:
            lines.append(
                f"| `{m.metric}` | {m.available_before} | {m.available_after} | "
                f"{'一致 ✅' if m.equal else ('不可比' if not m.available_before and not m.available_after else '**不一致** ❌')} | "
                f"{'、'.join(m.changed_fields) or '—'} |")
        return "\n".join(lines)


def snapshot(*, names: Sequence[str] = METRIC_NAMES, events_dir: str = "",
             shadow_dir: str = "", audit_db: str = "",
             anchor_day: str = "") -> Dict[str, Dict[str, Any]]:
    """采集一组指标快照（只读）。"""
    kw: Dict[str, Any] = {
        "utc.weekly": {"events_dir": events_dir, "anchor_day": anchor_day},
        "digestion.throughput": {"shadow_dir": shadow_dir},
        "audit.chain": {"db_path": audit_db},
    }
    out: Dict[str, Dict[str, Any]] = {}
    for name in names:
        collector = COLLECTORS.get(name)
        if collector is None:
            out[name] = {"metric": name, "available": False,
                         "reason": "未知指标（不在受守护清单内）"}
            continue
        try:
            out[name] = collector(**kw.get(name, {}))
        except Exception as e:  # noqa: BLE001 单个指标失败不影响其它
            out[name] = {"metric": name, "available": False, "reason": str(e)}
    return out


def compare_metrics(before: Dict[str, Dict[str, Any]],
                    after: Dict[str, Dict[str, Any]]) -> ConsistencyReport:
    """逐指标比对（**排除 `metric` 标签本身**；只比数值与标签字段）。"""
    report = ConsistencyReport()
    names = list(dict.fromkeys(list(before.keys()) + list(after.keys())))
    for name in names:
        b = dict(before.get(name) or {})
        a = dict(after.get(name) or {})
        b.pop("metric", None)
        a.pop("metric", None)
        comp = MetricComparison(metric=name, before=b, after=a,
                                available_before=bool(b.get("available")),
                                available_after=bool(a.get("available")))
        if not comp.available_before and not comp.available_after:
            comp.verdict = "unavailable"
            comp.equal = True          # 两侧都不可采 ⇒ 无口径变化（但如实标注）
        elif comp.available_before != comp.available_after:
            comp.verdict = "newly_available" if comp.available_after else "unavailable"
            comp.equal = False
        else:
            changed = sorted(k for k in set(b) | set(a) if b.get(k) != a.get(k))
            comp.changed_fields = changed
            comp.equal = not changed
            comp.verdict = "consistent" if comp.equal else "changed"
        report.metrics.append(comp)
    report.consistent = all(m.equal for m in report.metrics)
    if not report.consistent:
        report.notes.append(
            "存在指标不一致：该类归档方式不合格，须改为"
            "「保留聚合摘要 + 明细归档」双轨（TASK-S8-01 §二.4）")
    return report


def check_roundtrip_metrics(*, run,
                            metrics: Sequence[str] = METRIC_NAMES,
                            events_dir: str = "", shadow_dir: str = "",
                            audit_db: str = "", anchor_day: str = ""
                            ) -> ConsistencyReport:
    """**归档前后各采一次**并比对。

    Args:
        run: 无参可调用，执行一次归档（返回 `Archiver.run(confirm=True)` 的报告）。
             本函数负责"前采 → 执行 → 后采"的顺序，调用方不必自己安排。

    注：函数名刻意不以 `verify_` 开头 —— 本仓库 `pytest.ini` 的
    `python_functions = test_* verify_*` 会把测试模块里**导入的** `verify_*`
    当成用例收集（S8-01 实测踩过一次）。
    """
    before = snapshot(names=metrics, events_dir=events_dir, shadow_dir=shadow_dir,
                      audit_db=audit_db, anchor_day=anchor_day)
    run()
    after = snapshot(names=metrics, events_dir=events_dir, shadow_dir=shadow_dir,
                     audit_db=audit_db, anchor_day=anchor_day)
    report = compare_metrics(before, after)
    report.archive_dir = ""
    return report


__all__ = [
    "METRIC_NAMES", "DEFAULT_EVENTS_DIR", "DEFAULT_SHADOW_DIR", "DEFAULT_AUDIT_DB",
    "MetricUnavailable", "COLLECTORS",
    "utc_weekly_metric", "digestion_throughput_metric", "audit_chain_metric",
    "MetricComparison", "ConsistencyReport",
    "snapshot", "compare_metrics", "check_roundtrip_metrics",
]
