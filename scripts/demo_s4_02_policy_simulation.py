#!/usr/bin/env python
"""TASK-S4-02 演示：策略模拟器（P7.2-19）样例报告生成

【为什么需要它】
    验收要求「模拟器样例报告（deny→allow 变更数 + 高危清单）」。但模拟器要跑，
    必须先有**历史决策日志**，而决策日志是运行时产物（不进仓库）。本脚本因此：

      1. 构造一段**合成的 7 天历史**（含正常外发、机密出域、黑盒破坏性操作、
         secret 出域被拒等混合场景），写入临时决策日志；
      2. 用**当前策略库**做基线，对一条**放宽 egress 的候选策略**重放；
      3. 输出样例报告（``.md`` 人读 + ``.json`` 机读），供验收报告引用。

    **边界声明（口径纪律）**：合成历史**不是真实流量**，因此本报告只能证明
    「模拟器会算、会分类、会列高危」，**不能**用来声称「策略变更已评估过真实影响」。
    真实评估要在具备真实决策日志的环境里重跑同一条命令。

【用法】

    python scripts/demo_s4_02_policy_simulation.py
    python scripts/demo_s4_02_policy_simulation.py --out reports/policy_simulation_sample.md \\
        --json-out reports/policy_simulation_sample.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.policy.decisions import DecisionLog  # noqa: E402
from agent.policy.engine import DecisionObserver, PolicyEngine  # noqa: E402
from agent.policy.models import PolicyContext  # noqa: E402
from agent.policy.simulator import simulate, write_report  # noqa: E402
from agent.policy.store import PolicyStore  # noqa: E402

DEFAULT_POLICY_FILE = "data/policies/policies.json"
DEFAULT_OUT = "reports/policy_simulation_sample.md"
DEFAULT_JSON_OUT = "reports/policy_simulation_sample.json"

#: 合成历史场景：(数据分级, 目标是否外部, 风险等级, provenance, 动作, 冻结窗口, 次数)
#: 覆盖 §5.6/§2.5 的判定分支，让报告里的分类计数不是真空跑出来的。
SCENARIOS: List[Dict[str, Any]] = [
    {"data_class": "internal", "external": True, "risk": "low",
     "provenance": "verified", "action": "http.get", "count": 24},
    {"data_class": None, "external": True, "risk": None,
     "provenance": "unknown", "action": "http.get", "count": 18},
    {"data_class": "confidential", "external": True, "risk": "high",
     "provenance": "verified", "action": "http.post", "count": 9},
    {"data_class": "secret", "external": True, "risk": "high",
     "provenance": "verified", "action": "http.post", "count": 6},
    {"data_class": "internal", "external": False, "risk": "low",
     "provenance": "verified", "action": "http.get", "count": 12},
    {"data_class": None, "external": True, "risk": "destructive",
     "provenance": "unknown", "action": "tool.run", "count": 4},
    # 冻结窗口内的外部出域（被 ops.freeze-external-egress 拒绝）—— 用于演示
    # 「deny → allow」这一 P7.2-19 主指标的非零样例
    {"data_class": "internal", "external": True, "risk": "medium",
     "provenance": "verified", "action": "http.post", "count": 7,
     "freeze_window": True},
]

#: 候选 A：把「机密数据出域」从需要人工确认放宽为**直接允许**（ask→allow，高危）。
CANDIDATE_RELAX_ASK: Dict[str, Any] = {
    "id": "gov.confidential-external-ask",
    "version": "2.0.0",
    "owner": "governance",
    "effect": "allow",
    "match": {
        "all": [
            {"field": "capability.trust.data_class", "op": "eq", "value": "confidential"},
            {"field": "target.external", "op": "eq", "value": True},
        ],
    },
    "message_template": "（候选）机密数据出域直接放行：{capability_id}",
    "effective_range": None,
    "break_glass_ttl_min": None,
    "signature": "",
}

#: 候选 B：把冻结窗口的绝对禁令降级为「允许」——**deny→allow，P7.2-19 的主指标**。
CANDIDATE_RELAX_DENY: Dict[str, Any] = {
    "id": "ops.freeze-external-egress",
    "version": "2.0.0",
    "owner": "ops",
    "effect": "allow",
    "match": {
        "all": [
            {"field": "attributes.freeze_window", "op": "eq", "value": True},
            {"field": "target.external", "op": "eq", "value": True},
        ],
    },
    "message_template": "（候选）冻结窗口内外部出域直接放行：{action} → {target_host}",
    "effective_range": None,
    "break_glass_ttl_min": None,
    "signature": "",
}

CANDIDATES = (("候选 A：放宽机密出域（ask→allow）", CANDIDATE_RELAX_ASK, "ask"),
              ("候选 B：放宽冻结窗口禁令（deny→allow）", CANDIDATE_RELAX_DENY, "deny"))


def _build_context(scenario: Dict[str, Any], index: int) -> PolicyContext:
    return PolicyContext.build(
        capability_id=f"cp.demo.src.act{index % 5}",
        capability={
            "trust": {"data_class": scenario["data_class"],
                      "risk_level": scenario["risk"]},
            "origin": {"provenance": scenario["provenance"],
                       "external_endpoint": scenario["external"]},
        },
        tenant_id=f"t-{index % 3}",
        actor=f"u-{index % 7}",
        action=scenario["action"], action_kind="egress",
        target={"external": scenario["external"], "host": "api.demo.example",
                "scheme": "https"},
        attributes={"payload_bytes": 256 + index, "scenario": "synthetic",
                    "freeze_window": bool(scenario.get("freeze_window"))},
    )


def seed_history(path: str, *, policy_file: str) -> Dict[str, Any]:
    """把合成历史写进决策日志；返回基线引擎（供模拟器复用）"""
    store = PolicyStore(path=policy_file)
    log = DecisionLog(path)
    engine = PolicyEngine(store, cache_size=0, decision_log=log,
                          observer=DecisionObserver(enabled=False), inbox=False)
    total = 0
    effects: Dict[str, int] = {}
    for scenario in SCENARIOS:
        for index in range(int(scenario["count"])):
            ctx = _build_context(scenario, total)
            decision = engine.check(ctx)
            effects[decision.effect] = effects.get(decision.effect, 0) + 1
            total += 1
    log.close()
    return {"engine": engine, "total": total, "effects": effects,
            "log_path": path}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="demo_s4_02_policy_simulation",
        description="生成策略模拟器样例报告（P7.2-19）")
    parser.add_argument("--policy-file", default=DEFAULT_POLICY_FILE,
                        help="基线策略库（默认仓库策略库）")
    parser.add_argument("--out", default=DEFAULT_OUT, help="报告输出（.md）")
    parser.add_argument("--json-out", default=DEFAULT_JSON_OUT,
                        help="报告输出（.json）")
    parser.add_argument("--keep-log", default=None,
                        help="决策日志落盘路径（默认写临时目录后删除）")
    args = parser.parse_args(list(argv) if argv is not None else None)

    tmp_dir = tempfile.mkdtemp(prefix="policy_demo_")
    log_path = args.keep_log or os.path.join(tmp_dir, "decisions.jsonl")

    seeded = seed_history(log_path, policy_file=args.policy_file)
    print("=" * 74)
    print("TASK-S4-02 策略模拟器演示（P7.2-19）")
    print("=" * 74)
    print(f"基线策略库   : {args.policy_file}")
    print(f"合成历史     : {seeded['total']} 条 → {seeded['effects']}")
    print(f"决策日志     : {log_path}")
    print("⚠ 合成历史不是真实流量：本报告只证明模拟器能算/能分类/能列高危，"
          "不能作为「已评估真实影响」的证据。")

    written_all: Dict[str, str] = {}
    for title, candidate, _expect in CANDIDATES:
        print()
        print("-" * 74)
        print(title)
        print("-" * 74)
        report = simulate(candidate, engine=seeded["engine"], since_days=7,
                          window_label="7d", log_path=log_path, limit=None)
        totals = report.to_dict()["totals"]
        print(f"重放条数     : {report.total}")
        print(f"deny → allow : {report.deny_to_allow}   ← P7.2-19 主指标")
        print(f"allow → deny : {report.allow_to_deny}")
        print(f"变更为 ask   : {report.to_ask}")
        print(f"ask → allow  : {report.ask_to_allow}")
        print(f"重放漂移     : {report.replay_drift}")
        print(f"高危命中     : {len(report.high_risk_hits)}")
        print(f"结论         : {report.verdict}")
        for index, hit in enumerate(report.high_risk_hits[:5], 1):
            print(f"  [{index}] {hit.old_effect}→{hit.new_effect} "
                  f"{hit.capability_id} {hit.action} "
                  f"(policy {hit.new_policy_id or hit.old_policy_id})")
        if len(report.high_risk_hits) > 5:
            print(f"  ...（其余 {len(report.high_risk_hits) - 5} 条见报告）")

        suffix = "" if candidate is CANDIDATES[0][1] else "_deny2allow"
        md_path = _with_suffix(args.out, suffix)
        json_path = _with_suffix(args.json_out, suffix)
        written = write_report(report, md_path=md_path, json_path=json_path)
        written_all.update({f"{suffix or '_default'}{key}": value
                            for key, value in written.items()})
        print(f"主指标核对   : totals={json.dumps(totals, ensure_ascii=False)}")

    print()
    print(f"报告已写出   : {json.dumps(written_all, ensure_ascii=False)}")
    return 0


def _with_suffix(path: str, suffix: str) -> str:
    if not suffix:
        return path
    base, dot, ext = str(path).rpartition(".")
    return f"{base}{suffix}.{ext}" if dot else f"{path}{suffix}"


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
