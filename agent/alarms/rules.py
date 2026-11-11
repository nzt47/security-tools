# -*- coding: utf-8 -*-
"""告警规则求值与接入（2026-11-11）：把 monitoring/alerts.yml 的 16 条规则真正跑起来。

【三种可选方案，以及为什么选这一种】
A. 部署 Prometheus，让报警器去查它 —— 规则零改动、最标准；但**本机没有 Prometheus**，
   等于为了 16 条规则再引入一个服务（安装/持久化/端口/权限），且报警器就依赖外部进程。
B. 手工把规则翻成"指标 + 阈值 + 比较符" —— 改动最小，但会**丢掉 rate / clamp_min /
   histogram_quantile**：那正是"5xx 比例"和"P99 延迟"的全部内容，翻完只剩恒真规则。
C. **在本进程解析并求值原 PromQL 子集**（本模块 + `promql.py` + `samples.py`）——
   规则文件一个字不改；窗口函数靠 60s 采样环算；算不出来的一律如实标
   `no_data` / `warming` / `unsupported`，绝不降级成"看着像在监控"。
   ⇒ 选 C。它是 A 的"零外部依赖"版本，也是 B 的"不丢表达力"版本。

【为什么大部分规则现在会是 no_data —— 这是事实，不是 bug】
本进程的注册表里**没有** `up`（那是 Prometheus 自己 scrape 出来的）、也没有
`yunshu_*_total` 里的 llm/tool/security/hallucination/eval 系列（它们要么从未自增过、
要么本部署根本没有对应埋点）。规则写着不等于指标存在 —— 求值器如实回报，
并把原因带到 `/api/observability/alerts` 的运行态里，让"规则表"不再假装在监控。
真要覆盖这些面，要么补埋点（本仓代码），要么上 Prometheus（方案 A）。这一条**已登记**
`alarms-evaluator-wiring`，等 Owner 决定是否补埋点。

【`for` 的语义】与 Prometheus 一致：条件**连续**成立够时长才 firing；
中途不再成立就清零。状态在进程内（重启后重新计时）。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from agent.alarms.promql import (
    STATUS_ERROR,
    STATUS_FIRING,
    STATUS_NO_DATA,
    STATUS_OK,
    STATUS_UNSUPPORTED,
    STATUS_WARMING,
    Evaluator,
)
from agent.alarms.samples import SampleStore, get_sample_store, parse_range

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

#: 规则文件（与 /api/observability/alerts 读的是同一份；不复制、不改写）
RULES_FILE = Path(__file__).resolve().parents[2] / "monitoring" / "alerts.yml"

#: 没有 `for` 时的默认持续时长（秒）—— Prometheus 缺省是 0（立即 firing）
DEFAULT_FOR_SEC = 0.0

#: 规则节点在台账里的 key 前缀（与 health/unit/audit 平级，便于按源过滤）
RULE_KEY_PREFIX = "rule."

STATE_PENDING = "pending"

#: 求值状态 → 中文标签（给 /api/observability/alerts 的运行态用）
STATUS_LABELS = {
    STATUS_FIRING: "触发中",
    STATE_PENDING: "待确认（未满 for 时长）",
    STATUS_OK: "正常",
    STATUS_NO_DATA: "无数据（指标不存在或未自增）",
    STATUS_WARMING: "窗口样本不足（刚启动）",
    STATUS_UNSUPPORTED: "表达式不可求值（需 Prometheus）",
    STATUS_ERROR: "求值异常",
}


def load_rules(path: Optional[Path] = None) -> List[dict]:
    """读规则文件，展平成 [{alert, expr, for, severity, labels, annotations, group}]"""
    target = Path(path) if path else RULES_FILE
    try:
        import yaml  # noqa: PLC0415 延迟 import：只在读规则时需要
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 规则文件坏了不能让报警器崩
        logger.warning("报警规则读取失败（%s）: %s", target.name, type(exc).__name__)
        return []
    out: List[dict] = []
    for group in raw.get("groups") or []:
        for rule in group.get("rules") or []:
            name = rule.get("alert") or rule.get("name")
            if not name:
                continue
            out.append({
                "alert": str(name),
                "expr": " ".join(str(rule.get("expr") or "").split()),
                "for": str(rule.get("for") or "0m"),
                "severity": str((rule.get("labels") or {}).get("severity") or "warning"),
                "labels": dict(rule.get("labels") or {}),
                "annotations": dict(rule.get("annotations") or {}),
                "group": str(group.get("name") or ""),
            })
    return out


@dataclass
class RuleState:
    alert: str
    expr: str
    severity: str
    state: str = STATUS_NO_DATA
    value: Optional[float] = None
    reason: str = ""
    since: float = 0.0
    for_sec: float = 0.0
    evaluated_at: float = 0.0
    annotations: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "alert": self.alert,
            "expr": self.expr,
            "severity": self.severity,
            "state": self.state,
            "state_label": STATUS_LABELS.get(self.state, self.state),
            "value": self.value,
            "reason": self.reason,
            "since": self.since,
            "for_sec": self.for_sec,
            "evaluated_at": self.evaluated_at,
            "labels": self.annotations,
        }


_lock = threading.Lock()
_states: Dict[str, RuleState] = {}


def reset_states() -> None:
    with _lock:
        _states.clear()


def evaluate_all(rules: Optional[List[dict]] = None,
                 store: Optional[SampleStore] = None,
                 now: Optional[float] = None) -> List[RuleState]:
    """求值全部规则（`for` 状态机在这里推进）；返回本轮状态快照。"""
    samples = store or get_sample_store()
    ts = time.time() if now is None else float(now)
    evaluator = Evaluator(samples)
    out: List[RuleState] = []
    for rule in rules if rules is not None else load_rules():
        for_sec = parse_range(rule.get("for")) or DEFAULT_FOR_SEC
        raw_value, status = evaluator.evaluate(rule["expr"])
        if ":" in status:
            status, reason = status.split(":", 1)
        else:
            reason = ""
        with _lock:
            state = _states.get(rule["alert"]) or RuleState(
                alert=rule["alert"], expr=rule["expr"], severity=rule["severity"],
                for_sec=for_sec, annotations=rule.get("annotations") or {})
            state.expr = rule["expr"]
            state.severity = rule["severity"]
            state.for_sec = for_sec
            state.evaluated_at = ts
            state.reason = reason
            if status == STATUS_FIRING:
                if not state.since:
                    state.since = ts
                elapsed = ts - state.since
                # 【首个采样点】刚起来时 elapsed=0；for=0 的规则允许立即 firing
                state.state = STATUS_FIRING if elapsed >= for_sec else STATE_PENDING
                state.value = raw_value
            else:
                state.since = 0.0
                state.state = status
                state.value = raw_value
            _states[rule["alert"]] = state
            out.append(state)
    return out


def snapshot() -> List[dict]:
    """当前状态（不推进求值）"""
    with _lock:
        return [s.to_dict() for s in _states.values()]


def runtime_states() -> Dict[str, dict]:
    """{alert: state} 供 /api/observability/alerts 合并运行态"""
    with _lock:
        return {name: {"state": s.state, "since": s.since, "value": s.value,
                       "reason": s.reason, "evaluated_at": s.evaluated_at}
                for name, s in _states.items()}


def candidates(states: Optional[List[RuleState]] = None) -> List[dict]:
    """firing 的规则 → 报警候选（key=rule.<alert>）"""
    rows: List[dict] = []
    for state in states if states is not None else [RuleState(**s) for s in []]:
        if state.state != STATUS_FIRING:
            continue
        summary = (state.annotations or {}).get("summary") or state.alert
        value_text = "" if state.value is None else ("实测 %.4g" % state.value)
        rows.append({
            "key": RULE_KEY_PREFIX + state.alert,
            "kind": "rule",
            "severity": state.severity if state.severity in ("info", "warning", "critical") else "warning",
            "title": "告警规则触发：" + summary,
            "detail": (value_text + "；" + state.expr).strip("；"),
            "subject": state.alert,
            "source": "/api/observability/alerts",
        })
    return rows


def coverage() -> Dict[str, object]:
    """规则覆盖率：多少条**真的能求值**、多少条因为指标不存在/窗口不足算不了。

    【为什么要有这个读数】"规则表里有 16 条"不等于"16 个面在被监控"。
    本仓实测：16 条里只有 6 条的指标在本进程注册表里存在；其余 10 条引用的
    up / llm_calls / tool_call / security_blocks / hallucination / eval_score
    要么由外部 Prometheus 采集，要么本部署根本没有埋点。把这件事做成**接口读数**，
    才不会重演"图上一片规则、实际一条也不会响"。
    """
    states = snapshot()
    by_state: Dict[str, int] = {}
    blocked = []
    for row in states:
        state = str(row.get("state") or "")
        by_state[state] = by_state.get(state, 0) + 1
        if state not in (STATUS_FIRING, STATE_PENDING, STATUS_OK):
            blocked.append({"alert": row.get("alert"),
                            "state": state,
                            "state_label": STATUS_LABELS.get(state, state),
                            "reason": row.get("reason") or ""})
    evaluable = by_state.get(STATUS_FIRING, 0) + by_state.get(STATE_PENDING, 0) + by_state.get(STATUS_OK, 0)
    return {
        "rules_total": len(states),
        "rules_evaluable": evaluable,
        "rules_blocked": len(blocked),
        "by_state": by_state,
        "blocked": blocked,
    }


def firing_states(states: Optional[List[RuleState]] = None) -> List[RuleState]:
    rows = states if states is not None else []
    return [s for s in rows if s.state == STATUS_FIRING]
