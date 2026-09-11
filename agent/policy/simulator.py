"""策略模拟器（P7.2-19：``cloudpivot policy simulate --since 7d``）

【设计文档原文】
    〔P7.2-19〕策略模拟器：``cloudpivot policy simulate --since 7d``——对历史
    PolicyDecision 重放候选新策略，报告 deny→allow 变更数与高危命中清单；
    策略变更 PR 必附模拟报告，否则不可合入。

【怎么"重放"】
    历史决策的**输入**存在 ``DecisionLog`` 里（``decisions.py`` 说明为什么事件
    埋点不够）。重放＝把每条历史 ``input`` 分别喂给

        - **基线引擎**（当前生效策略库）→ ``old``
        - **候选引擎**（当前策略库 + 候选策略）→ ``new``

    两边都**关闭缓存**、关闭埋点与落盘，保证纯计算、可重放、无副作用。
    然后比对 ``old.effect`` 与 ``new.effect``：

    | 变化 | 含义 | 风险 |
    |---|---|---|
    | ``deny → allow`` | 原本被拦的操作将放行 | **高危**（P7.2-19 要的正是这一类的清单）|
    | ``allow → deny`` | 新增拦截 | 需确认不是误伤（新策略过宽）|
    | ``* → ask`` | 从自动变人工 | 影响面/吞吐，需确认收件箱容量 |
    | ``ask → allow`` | 人工环节被取消 | **高危**（与 deny→allow 同级）|
    | ``ask → deny`` | 人工环节变硬拦 | 中（可能阻断既有业务）|

【两项诚实性纪律】
    1. **重放漂移要说出来**：决策日志与事件层同口径脱敏（键名黑名单），若候选/基线
       策略匹配了被脱敏丢弃的键，重放结果会与历史不一致。基线引擎对历史输入重算
       时若得到 ≠ 历史 effect，计入 ``replay_drift`` 并在报告里单列——这是数据
       质量信号，不是错误。
    2. **不夸大样本**：报告永远带 ``total`` 与 ``window``。样本为 0 时明确写
       「无历史决策，模拟不可判定」，而不是输出一张空表让人误以为「零变更＝安全」。
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.policy.decisions import DecisionLog, DecisionRecord
from agent.policy.engine import PolicyEngine
from agent.policy.models import (
    EFFECT_ALLOW,
    EFFECT_ASK,
    EFFECT_DENY,
    Policy,
    PolicyValidationError,
    now_iso,
)
from agent.policy.store import PolicyStore

logger = logging.getLogger("agent.policy.simulator")

#: 变更类别
CHANGE_DENY_TO_ALLOW = "deny_to_allow"
CHANGE_ALLOW_TO_DENY = "allow_to_deny"
CHANGE_ASK_TO_ALLOW = "ask_to_allow"
CHANGE_ASK_TO_DENY = "ask_to_deny"
CHANGE_TO_ASK = "to_ask"
CHANGE_OTHER = "other"

#: 高危变更类别（必须人工在报告里逐条确认）
HIGH_RISK_CHANGES = (CHANGE_DENY_TO_ALLOW, CHANGE_ASK_TO_ALLOW)

#: 报告 schema
REPORT_SCHEMA = "policy.simulation.v1"


# ════════════════════════════════════════════════════════════
#  窗口解析
# ════════════════════════════════════════════════════════════


def parse_since(value: Any) -> Tuple[float, str]:
    """解析 ``7d`` / ``24h`` / ``90m`` / ``7`` → ``(天数, 原文)``

    缺省单位按**天**（对齐 ``--since 7d`` 的书写习惯）；非法输入回退 7 天。
    """
    raw = str(value if value is not None else "7d").strip().lower()
    if not raw:
        return 7.0, "7d"
    unit = raw[-1]
    number = raw[:-1] if unit.isalpha() else raw
    try:
        amount = float(number)
    except (TypeError, ValueError):
        return 7.0, raw
    if amount < 0:
        return 7.0, raw
    if unit == "h":
        return amount / 24.0, raw
    if unit == "m":
        return amount / 1440.0, raw
    return amount, raw  # d / w 之外的字母或纯数字按天


# ════════════════════════════════════════════════════════════
#  变更与报告
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SimulationChange:
    """一条「历史决策 → 候选决策」的变化

    Attributes:
        kind: 变更类别（见 ``CHANGE_*``）。
        high_risk: 是否属高危类别（``deny→allow`` / ``ask→allow``）。
        old_effect / new_effect: 变化前后的效果。
        old_policy_id / new_policy_id: 变化前后命中的策略。
        capability_id / action / actor / tenant_id: 定位叶子。
        ts: 原决策时刻。
        drift: 基线与历史记录本身不一致（重放漂移）。
    """

    kind: str
    old_effect: str
    new_effect: str
    old_policy_id: str = ""
    new_policy_id: str = ""
    policy_version: str = ""
    capability_id: str = ""
    action: str = ""
    actor: str = ""
    tenant_id: str = "default"
    ts: str = ""
    high_risk: bool = False
    drift: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind, "high_risk": self.high_risk, "drift": self.drift,
            "old_effect": self.old_effect, "new_effect": self.new_effect,
            "old_policy_id": self.old_policy_id, "new_policy_id": self.new_policy_id,
            "policy_version": self.policy_version,
            "capability_id": self.capability_id, "action": self.action,
            "actor": self.actor, "tenant_id": self.tenant_id, "ts": self.ts,
        }


def classify_change(old_effect: str, new_effect: str) -> str:
    """判定变更类别（纯函数；报告与门禁共用同一口径）"""
    old_e, new_e = str(old_effect or ""), str(new_effect or "")
    if old_e == new_e:
        return ""
    if old_e == EFFECT_DENY and new_e == EFFECT_ALLOW:
        return CHANGE_DENY_TO_ALLOW
    if old_e == EFFECT_ALLOW and new_e == EFFECT_DENY:
        return CHANGE_ALLOW_TO_DENY
    if old_e == EFFECT_ASK and new_e == EFFECT_ALLOW:
        return CHANGE_ASK_TO_ALLOW
    if old_e == EFFECT_ASK and new_e == EFFECT_DENY:
        return CHANGE_ASK_TO_DENY
    if new_e == EFFECT_ASK:
        return CHANGE_TO_ASK
    return CHANGE_OTHER


@dataclass
class SimulationReport:
    """模拟报告（P7.2-19 的输出物；同时是 PR 合入门禁的输入）

    Attributes:
        candidate: 候选策略摘要（id/version/effect/match 哈希）。
        window: 重放窗口（``--since`` 原文）与天数。
        total / unchanged / changes: 样本量、未变数、变更清单。
        deny_to_allow: **P7.2-19 指定的主指标**。
        allow_to_deny / to_ask / ask_to_allow / ask_to_deny: 其余分类计数。
        high_risk_hits: 高危命中清单（原 deny 现 allow 等）。
        replay_drift: 重放漂移条数（基线重算 ≠ 历史记录）。
        by_capability / by_policy: 影响面分布。
        shadow: 策略遮蔽诊断（来自 ``PolicyStore.shadow_report``）。
        baseline_fingerprint / candidate_fingerprint: 前后策略库指纹。
    """

    candidate: Dict[str, Any] = field(default_factory=dict)
    window: str = "7d"
    window_days: float = 7.0
    total: int = 0
    unchanged: int = 0
    changes: List[SimulationChange] = field(default_factory=list)
    deny_to_allow: int = 0
    allow_to_deny: int = 0
    to_ask: int = 0
    ask_to_allow: int = 0
    ask_to_deny: int = 0
    other: int = 0
    replay_drift: int = 0
    by_capability: Dict[str, int] = field(default_factory=dict)
    by_policy: Dict[str, int] = field(default_factory=dict)
    shadow: List[Dict[str, str]] = field(default_factory=list)
    baseline_fingerprint: str = ""
    candidate_fingerprint: str = ""
    generated_at: str = field(default_factory=now_iso)
    source: str = ""
    schema: str = REPORT_SCHEMA

    # ── 派生 ──

    @property
    def high_risk_hits(self) -> List[SimulationChange]:
        return [c for c in self.changes if c.high_risk]

    @property
    def has_sample(self) -> bool:
        """是否有可判定样本（无样本时**不可**声称"零变更＝安全"）"""
        return self.total > 0

    @property
    def verdict(self) -> str:
        """门禁用的结论（``clean`` / ``needs_ack`` / ``no_sample``）"""
        if not self.has_sample:
            return "no_sample"
        return "needs_ack" if self.high_risk_hits else "clean"

    # ── 序列化 ──

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "generated_at": self.generated_at,
            "source": self.source,
            "candidate": dict(self.candidate),
            "window": self.window,
            "window_days": self.window_days,
            "totals": {
                "total": self.total, "unchanged": self.unchanged,
                "changed": len(self.changes),
                "deny_to_allow": self.deny_to_allow,
                "allow_to_deny": self.allow_to_deny,
                "to_ask": self.to_ask,
                "ask_to_allow": self.ask_to_allow,
                "ask_to_deny": self.ask_to_deny,
                "other": self.other,
                "replay_drift": self.replay_drift,
            },
            "high_risk_hits": [c.to_dict() for c in self.high_risk_hits],
            "changes": [c.to_dict() for c in self.changes],
            "by_capability": dict(self.by_capability),
            "by_policy": dict(self.by_policy),
            "shadow": list(self.shadow),
            "baseline_fingerprint": self.baseline_fingerprint,
            "candidate_fingerprint": self.candidate_fingerprint,
            "verdict": self.verdict,
        }

    def to_markdown(self) -> str:
        return render_markdown(self)


# ════════════════════════════════════════════════════════════
#  模拟
# ════════════════════════════════════════════════════════════


def _candidate_summary(policy: Policy) -> Dict[str, Any]:
    return {
        "id": policy.id, "version": policy.version, "owner": policy.owner,
        "effect": policy.effect.value,
        "match_hash": policy.match_signature(),
        "match": policy.match,
        "break_glass_ttl_min": policy.break_glass_ttl_min,
        "effective_range": policy.effective_range.to_dict(),
        "source_ref": policy.source_ref,
    }


def build_candidate_engine(candidate: Any, *, base_store: Optional[PolicyStore] = None,
                           base_path: Optional[str] = None) -> Tuple[PolicyEngine, Policy]:
    """构造「当前策略库 + 候选策略」的隔离引擎（纯计算，无埋点无落盘）"""
    from agent.policy.engine import DecisionObserver

    base = base_store if base_store is not None else PolicyStore(path=base_path)
    candidate_dict = candidate.to_dict() if isinstance(candidate, Policy) else candidate
    if isinstance(candidate_dict, dict) and "policies" in candidate_dict:
        items = candidate_dict.get("policies") or []
        if len(items) != 1:
            raise PolicyValidationError(
                [f"候选文件应恰含 1 条策略，got {len(items)}"],
                code="INVALID_CANDIDATE")
        candidate_dict = items[0]
    parsed = Policy.parse(candidate_dict, source_ref="candidate")

    store = PolicyStore(path=base_path, autoload=False,
                        include_builtins=base.builtins_enabled)
    for policy in base.active():
        store.adopt(policy)  # 直接搬运已生效版本（含内置不变量）
    # 同 id 且**同版本**的候选要先移除旧条目，否则会被 version_key 排序挤掉
    existing = store.get(parsed.id, parsed.version)
    if existing is not None:
        store.remove(parsed.id, parsed.version)
    store.add(parsed, source_ref="candidate")

    engine = PolicyEngine(
        store,
        cache_size=0,               # 重放必须逐条重算，不能命中缓存
        decision_log=False,         # 模拟不落盘
        observer=DecisionObserver(enabled=False),
        inbox=False,
        latency_window=64,
    )
    return engine, parsed


def simulate(
    candidate: Any,
    *,
    engine: Optional[PolicyEngine] = None,
    since_days: float = 7.0,
    window_label: str = "",
    log_path: Optional[str] = None,
    limit: Optional[int] = None,
    records: Optional[Sequence[DecisionRecord]] = None,
) -> SimulationReport:
    """对历史决策重放候选新策略（P7.2-19）

    Args:
        candidate: 候选策略（dict / :class:`~agent.policy.models.Policy` /
            ``{"policies": [...]}`` 单条文档）。
        engine: 提供**基线策略库**的引擎（默认进程级）；它自己的决策日志被用作
            重放数据源。
        since_days: 回看窗口（天）。``0`` ⇒ 全部历史。
        window_label: 窗口原文（报告展示用，如 ``"7d"``）。
        log_path: 覆盖决策日志路径（不复用 ``engine`` 的日志时）。
        limit: 只用最近 limit 条（快速预演）。
        records: 直接给记录清单（测试/离线重放用；给定时忽略日志读取）。

    Returns:
        :class:`SimulationReport`。
    """
    base_engine = engine
    base_store = base_engine.store if base_engine is not None else None

    history: List[DecisionRecord] = list(records or [])
    if not history:
        source_log = None
        if log_path:
            source_log = DecisionLog(log_path, enabled=False)
        elif base_engine is not None and base_engine.decision_log is not None:
            source_log = base_engine.decision_log
        else:
            source_log = DecisionLog(enabled=False)
        history = source_log.read(since_days=since_days if since_days else None,
                                  limit=limit)

    baseline_engine = base_engine if base_engine is not None else PolicyEngine(
        cache_size=0, decision_log=False, inbox=False)
    candidate_engine, candidate_policy = build_candidate_engine(
        candidate, base_store=baseline_engine.store)

    report = SimulationReport(
        candidate=_candidate_summary(candidate_policy),
        window=window_label or (f"{since_days}d" if since_days else "all"),
        window_days=float(since_days or 0),
        total=len(history),
        baseline_fingerprint=baseline_engine.store.fingerprint(),
        candidate_fingerprint=candidate_engine.store.fingerprint(),
        source=str(log_path or (baseline_engine.decision_log.path
                                if baseline_engine.decision_log else "")),
        shadow=candidate_engine.store.shadow_report(),
    )

    cap_counter: Counter = Counter()
    policy_counter: Counter = Counter()

    for record in history:
        ctx = record.ctx()
        old = baseline_engine.check(ctx, use_cache=False)
        new = candidate_engine.check(ctx, use_cache=False)
        drift = bool(record.effect) and old.effect != record.effect
        if drift:
            report.replay_drift += 1
        kind = classify_change(old.effect, new.effect)
        if not kind:
            report.unchanged += 1
            continue
        change = SimulationChange(
            kind=kind, old_effect=old.effect, new_effect=new.effect,
            old_policy_id=old.policy_id, new_policy_id=new.policy_id,
            policy_version=new.policy_version or old.policy_version,
            capability_id=record.capability_id or ctx.capability_id,
            action=record.action or ctx.action,
            actor=record.actor or ctx.actor,
            tenant_id=record.tenant_id or ctx.tenant_id,
            ts=record.ts, high_risk=kind in HIGH_RISK_CHANGES, drift=drift)
        report.changes.append(change)
        cap_counter[change.capability_id or "<unspecified>"] += 1
        policy_counter[change.new_policy_id or change.old_policy_id] += 1

    report.deny_to_allow = sum(1 for c in report.changes
                               if c.kind == CHANGE_DENY_TO_ALLOW)
    report.allow_to_deny = sum(1 for c in report.changes
                               if c.kind == CHANGE_ALLOW_TO_DENY)
    report.to_ask = sum(1 for c in report.changes if c.kind == CHANGE_TO_ASK)
    report.ask_to_allow = sum(1 for c in report.changes
                              if c.kind == CHANGE_ASK_TO_ALLOW)
    report.ask_to_deny = sum(1 for c in report.changes
                             if c.kind == CHANGE_ASK_TO_DENY)
    report.other = sum(1 for c in report.changes if c.kind == CHANGE_OTHER)
    report.by_capability = dict(cap_counter.most_common())
    report.by_policy = dict(policy_counter.most_common())
    return report


# ════════════════════════════════════════════════════════════
#  报告渲染
# ════════════════════════════════════════════════════════════


def render_markdown(report: SimulationReport) -> str:
    """渲染人读报告（PR 附件形态；与 ``to_dict`` 同源，不另算一遍）"""
    data = report.to_dict()
    candidate = data["candidate"]
    totals = data["totals"]
    lines: List[str] = []
    lines.append("# 策略模拟报告（P7.2-19）")
    lines.append("")
    lines.append(f"- 生成时间：`{data['generated_at']}`")
    lines.append(f"- 数据源：`{data['source'] or '(未指定)'}`")
    lines.append(f"- 重放窗口：`{data['window']}`（{data['window_days']} 天）")
    lines.append(f"- 基线策略库指纹：`{data['baseline_fingerprint']}`")
    lines.append(f"- 候选策略库指纹：`{data['candidate_fingerprint']}`")
    lines.append("")
    lines.append("## 一、候选策略")
    lines.append("")
    lines.append(f"- id / version：`{candidate.get('id')}` / `{candidate.get('version')}`")
    lines.append(f"- owner：`{candidate.get('owner')}`")
    lines.append(f"- effect：`{candidate.get('effect')}`")
    lines.append(f"- match（哈希 `{candidate.get('match_hash')}`）：")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(candidate.get("match"), ensure_ascii=False, indent=2,
                            sort_keys=True))
    lines.append("```")
    lines.append("")
    lines.append("## 二、结论")
    lines.append("")
    if not report.has_sample:
        lines.append("> **无历史决策样本（total=0）——模拟不可判定。**")
        lines.append("> 「零变更」在此情形下**不构成**安全证据；请先在具备真实流量的环境")
        lines.append("> 积累决策日志（`data/policies/decisions.jsonl`）后重跑。")
    elif report.high_risk_hits:
        lines.append(f"> **需人工确认：{len(report.high_risk_hits)} 条高危变更**"
                     f"（原 deny/ask 现 allow）。合入门禁要求逐条确认。")
    else:
        lines.append("> 未发现高危变更（无 deny→allow / ask→allow）。")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 历史决策总数 | {totals['total']} |")
    lines.append(f"| 未变 | {totals['unchanged']} |")
    lines.append(f"| **deny → allow（主指标）** | **{totals['deny_to_allow']}** |")
    lines.append(f"| allow → deny | {totals['allow_to_deny']} |")
    lines.append(f"| 变更为 ask | {totals['to_ask']} |")
    lines.append(f"| ask → allow | {totals['ask_to_allow']} |")
    lines.append(f"| ask → deny | {totals['ask_to_deny']} |")
    lines.append(f"| 其它 | {totals['other']} |")
    lines.append(f"| 重放漂移（基线重算 ≠ 历史） | {totals['replay_drift']} |")
    lines.append("")

    lines.append("## 三、高危命中清单")
    lines.append("")
    if not report.high_risk_hits:
        lines.append("（无）")
    else:
        lines.append("| # | 变更 | 能力 | 动作 | 租户 | 角色 | 原策略 | 新策略 | 时刻 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for index, change in enumerate(report.high_risk_hits, 1):
            lines.append(
                f"| {index} | `{change.old_effect}→{change.new_effect}` | "
                f"`{change.capability_id}` | `{change.action}` | "
                f"`{change.tenant_id}` | `{change.actor}` | "
                f"`{change.old_policy_id}` | `{change.new_policy_id}` | "
                f"`{change.ts}` |")
    lines.append("")

    lines.append("## 四、影响面")
    lines.append("")
    lines.append(f"- 按能力：`{json.dumps(report.by_capability, ensure_ascii=False)}`")
    lines.append(f"- 按策略：`{json.dumps(report.by_policy, ensure_ascii=False)}`")
    if report.shadow:
        lines.append("")
        lines.append("### 策略遮蔽诊断（首个命中生效的已知代价）")
        lines.append("")
        for item in report.shadow:
            lines.append(f"- {item.get('detail')}")
    lines.append("")
    lines.append("## 五、合入检查项")
    lines.append("")
    lines.append("合入门禁（`scripts/check_policy_change_gate.py`）要求：")
    lines.append("")
    lines.append("1. 本报告**随 PR 提交**（JSON 版用于机读：`--json-out <path>.json`）；")
    lines.append("2. 高危命中清单**逐条确认**——PR 描述 `## 高危确认` 段的**已勾选项**"
                 "数量必须 ≥ 高危命中数，且每条写明策略 id"
                 "（模板见 `.github/pull_request_template.md`）；")
    lines.append("3. 若 `total=0`（无样本），PR 描述必须在 `## 无样本声明` 段**勾选**"
                 "「无历史决策样本」——门禁不认可未勾选的声明，也不认可把「零变更」"
                 "当作安全证据。")
    lines.append("")
    return "\n".join(lines)


def write_report(report: SimulationReport, *, md_path: Optional[str] = None,
                 json_path: Optional[str] = None) -> Dict[str, str]:
    """落盘报告（``.md`` 与人读、``.json`` 与机读）；返回写出的路径"""
    written: Dict[str, str] = {}
    if md_path:
        _write(md_path, render_markdown(report))
        written["markdown"] = md_path
    if json_path:
        _write(json_path, json.dumps(report.to_dict(), ensure_ascii=False,
                                     indent=2, sort_keys=True))
        written["json"] = json_path
    return written


def _write(path: str, text: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


# ════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python -m agent.policy.simulator --candidate <file> --since 7d``"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m agent.policy.simulator",
        description="策略模拟器（P7.2-19）：对历史 PolicyDecision 重放候选新策略")
    parser.add_argument("--candidate", required=True,
                        help="候选策略 JSON（单条策略 / {policies:[...]} / 裸数组）")
    parser.add_argument("--since", default="7d", help="回看窗口，如 7d / 24h / 90m")
    parser.add_argument("--policy-file", default=None,
                        help="基线策略库路径（默认 CP_POLICY_FILE 或 data/policies/policies.json）")
    parser.add_argument("--log", default=None, help="决策日志路径（默认取引擎配置）")
    parser.add_argument("--limit", type=int, default=None, help="最多重放最近 N 条")
    parser.add_argument("--out", default=None, help="报告输出路径（.md）")
    parser.add_argument("--json-out", default=None, help="报告输出路径（.json）")
    parser.add_argument("--fail-on-high-risk", action="store_true",
                        help="存在高危变更时以退出码 2 结束（CI 便捷用法）")
    args = parser.parse_args(list(argv) if argv is not None else None)

    days, label = parse_since(args.since)
    store = PolicyStore(path=args.policy_file)
    engine = PolicyEngine(store, decision_log=DecisionLog(args.log, enabled=False))
    report = simulate(_load_json(args.candidate), engine=engine,
                      since_days=days, window_label=label,
                      log_path=args.log, limit=args.limit)
    text = render_markdown(report)
    print(text)
    if args.out or args.json_out:
        written = write_report(report, md_path=args.out, json_path=args.json_out)
        print(f"\n[报告已写出] {json.dumps(written, ensure_ascii=False)}")
    if args.fail_on_high_risk and report.high_risk_hits:
        return 2
    return 0


__all__ = [
    "CHANGE_DENY_TO_ALLOW", "CHANGE_ALLOW_TO_DENY", "CHANGE_ASK_TO_ALLOW",
    "CHANGE_ASK_TO_DENY", "CHANGE_TO_ASK", "CHANGE_OTHER", "HIGH_RISK_CHANGES",
    "REPORT_SCHEMA", "parse_since", "classify_change", "SimulationChange",
    "SimulationReport", "build_candidate_engine", "simulate", "render_markdown",
    "write_report", "main",
]


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
