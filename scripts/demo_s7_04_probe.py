#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TASK-S7-04 子项 B 探活设施**演示**（可复跑，产出验收报告所需的演示记录）

两段演示，分别回答两个问题：

| 段 | 问题 | 期望输出 |
|---|---|---|
| **一** | 当前真实台账有没有可探活的能力？ | 台账 32 个能力**全在 borrowed**、0 个 internalized/native ⇒ `run_all()` 返回 `no_capability`（**不报错、不编造**） |
| **二** | 能力真的到了 native 且退化了，设施会做什么？ | 四项条件逐条判定 → `degraded` → **回退建议（不执行）** → **事故卡（六要素）**；**全程台账 stage 未变** |

【安全边界】
- 第二段用**临时目录 + 显式传入的 store/台账**，不触碰 `data/descriptors.json`、
  `data/digestion/*`、`data/audit/*`（`emit_audit=False`，事件目录指向 tmp）；
- 第一段只**读**真实台账（`DescriptorRegistry().list()`），不做任何写操作；
- **不做任何 stage 变更**：演示脚本与 `probe.py` 都没有调用 `stage_migrate` 的路径。

用法：

    python scripts/demo_s7_04_probe.py
    python scripts/demo_s7_04_probe.py --json > reports/s7_04/probe_demo.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Any, Dict, List

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

CAP = "cp.builtin.read_file"


# ════════════════════════════════════════════════════════════
#  第一段：真实台账的可探活范围
# ════════════════════════════════════════════════════════════


def scan_real_ledger(ledger_path: str = "") -> Dict[str, Any]:
    """真实台账的 stage 分布 + `run_all()` 的如实返回（**只读**）

    Args:
        ledger_path: 显式指定台账文件（**只读**）。缺省用 `DescriptorRegistry()`
            的默认路径；worktree 内可能没有该文件（未被 git 跟踪），
            此时可显式指向主工作区的运行时台账以取证。
    """
    from agent.digestion.probe import PROBE_STAGES, LivenessProbe
    from agent.descriptors.registry import DescriptorRegistry

    registry = (DescriptorRegistry(path=ledger_path) if str(ledger_path or "").strip()
                else DescriptorRegistry())
    try:
        descriptions = registry.list()
    except Exception as exc:  # noqa: BLE001
        return {"ledger_available": False,
                "error": f"{type(exc).__name__}: {exc}",
                "note": "台账不可读 ⇒ 不猜、不编造目标"}

    counts: Dict[str, int] = {}
    for desc in descriptions:
        stage = getattr(getattr(desc, "evolution", None), "stage", None)
        key = str(getattr(stage, "value", stage) or "None")
        counts[key] = counts.get(key, 0) + 1

    box = LivenessProbe(registry=registry, emit_audit=False)
    targets = box.scan_targets()
    outcome = box.run_all()
    return {
        "ledger_available": True,
        "ledger_path": str(getattr(registry, "path", "") or ""),
        "capabilities_total": len(descriptions),
        "stage_counts": counts,
        "probe_stage_scope": list(PROBE_STAGES),
        "probe_targets": list(targets),
        "internalized_or_native_count": len(targets),
        "run_all_status": outcome.get("status"),
        "run_all_note": outcome.get("note"),
    }


# ════════════════════════════════════════════════════════════
#  第二段：受控演示（临时目录）
# ════════════════════════════════════════════════════════════


def _demo_case(index: int):
    from agent.digestion import cases as C

    base = "C:/sandbox"
    read_path = f"{base}/tests/s7_04_mod{index:03d}.py"
    report = f"{base}/out/s7_04_report{index:03d}.md"
    upstream = [
        C.ProgramStep(label="read_file",
                      params={"path": read_path, "encoding": "utf-8"}),
        C.ProgramStep(label="shell_execute",
                      params={"cmd": f"pytest tests/s7_04_mod{index:03d}.py -q"}),
        C.ProgramStep(label="write_file",
                      params={"path": report, "content": f"report {index:03d}"}),
    ]
    return C.EquivalenceCase(
        case_id=f"s704-case-{index:03d}", capability_id=CAP,
        input={"path": read_path, "encoding": "utf-8"},
        bindings={"cmd": f"pytest tests/s7_04_mod{index:03d}.py -q",
                  "content": f"report {index:03d}"},
        upstream=upstream, fixtures={read_path: f"# fixture {index}\n"},
        expected_output_schema={"ok": "bool", "path": "str", "bytes": "number"},
        expected_side_effects={"files_written": [report],
                               "external_calls": ["shell_execute"]},
        expected_status="success",
        side_effects_source=C.SIDE_EFFECT_SOURCE_AUTHORED,
        kind=C.CASE_KIND_TRACE, origin_trace_id=f"tr-s704-{index:03d}")


class _DemoDescriptor:
    def __init__(self, capability_id: str, stage: str, version: str = "") -> None:
        self.capability_id = capability_id
        self.evolution = type("Evo", (), {"stage": stage})()
        self.meta = type("Meta", (), {"version": version})()


class _DemoRegistry:
    """受控台账：**记录** `set_stage` 调用 ⇒ 演示"未自动执行 stage 变更"的证据"""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.set_stage_calls: List[tuple] = []

    def get(self, capability_id: str):
        if capability_id != CAP:
            return None
        return _DemoDescriptor(CAP, self.stage, "demo-impl-v1")

    def list_by_stage(self, stage: str):
        return [self.get(CAP)] if str(stage) == self.stage else []

    def set_stage(self, capability_id: str, stage: str, **_kw) -> None:
        self.set_stage_calls.append((capability_id, stage))
        self.stage = stage


def run_controlled_demo() -> Dict[str, Any]:
    """受控演示：native 能力 → 健康探活 → 退化探活（回退建议 + 事故卡）"""
    from agent.digestion import cases as C
    from agent.digestion import probe as P
    from agent.digestion.sandbox import ProgramImplementation

    workdir = tempfile.mkdtemp(prefix="cp_s704_demo_")
    env_patch = {
        "CP_DIGESTION_CASE_DIR": os.path.join(workdir, "cases"),
        "CP_DIGESTION_LIVENESS_DIR": os.path.join(workdir, "liveness"),
        "CP_HEALING_INCIDENTS_DIR": os.path.join(workdir, "incidents"),
        "CP_EVENTS_DIR": os.path.join(workdir, "events"),
    }
    previous = {k: os.environ.get(k) for k in env_patch}
    os.environ.update(env_patch)

    try:
        registry = _DemoRegistry("native")
        store = C.JsonCaseStore(env_patch["CP_DIGESTION_CASE_DIR"])
        baselines = P.LivenessBaselineStore(os.path.join(workdir, "liveness",
                                                         "baseline.json"))
        case_set = C.build_case_set(CAP, [_demo_case(i) for i in range(12)])
        store.save(case_set)

        probe = P.LivenessProbe(case_store=store, registry=registry,
                               baseline_store=baselines, probe_size=5,
                               emit_audit=False, env={})

        def healthy(case):
            return ProgramImplementation(list(case.upstream), name="native_ok")

        def degraded(case):
            return ProgramImplementation(list(case.upstream)[:2], name="native_bad")

        # ① 首次探活：建立基线（四项条件均"不判定"，**不误报退化**）
        first = probe.run(CAP, candidate=healthy, now=1_000_000.0)

        # ② 健康复探（同一轮基线可比）：应保持 ok
        second = probe.run(CAP, candidate=healthy, now=1_000_060.0)

        # ③ 退化探活：候选实现丢步 ⇒ 结构层硬性比对失败 ⇒ ① 命中
        third = probe.run(CAP, candidate=degraded, fatal_change="demo-commit-abc123",
                          now=1_000_120.0)

        from agent.self_healing.levels import load_incident
        card = load_incident(third.incident_id) if third.incident_id else None

        return {
            "workdir": workdir,
            "capability_id": CAP,
            "stage_before": "native",
            "stage_after": registry.stage,
            "set_stage_calls": [list(x) for x in registry.set_stage_calls],
            "stage_unchanged": registry.stage == "native"
                               and not registry.set_stage_calls,
            "baseline_established_on_first": first.baseline_established,
            "first_probe": {
                "verdict": first.verdict,
                "pass_rate": first.pass_rate,
                "p99_wall_candidate_ms": first.p99_wall_candidate_ms,
                "clock": first.clock,
                "unmeasured_conditions": first.unmeasured_conditions,
                "triggered_conditions": first.triggered_conditions,
            },
            "healthy_probe": {
                "verdict": second.verdict,
                "pass_rate": second.pass_rate,
                "p99_wall_candidate_ms": second.p99_wall_candidate_ms,
                "triggered_conditions": second.triggered_conditions,
                "rollback_recommended": bool(second.rollback
                                             and second.rollback.recommended),
            },
            "degraded_probe": {
                "verdict": third.verdict,
                "degraded": third.degraded,
                "pass_rate": third.pass_rate,
                "triggered_conditions": third.triggered_conditions,
                "unmeasured_conditions": third.unmeasured_conditions,
                "failure_list": third.failure_list,
                "conditions": third.conditions,
                "rollback": third.rollback.to_dict() if third.rollback else None,
                "incident_id": third.incident_id,
                "incident_missing_elements": third.incident_missing_elements,
                "incident_card": card.to_dict() if card else None,
                "incident_resolvable": bool(card and card.is_resolvable()),
            },
        }
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TASK-S7-04 子项 B 探活设施演示")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    parser.add_argument("--ledger", default="",
                        help="显式指定**只读**的 descriptor 台账文件（取证用）")
    args = parser.parse_args(argv)

    ledger = scan_real_ledger(args.ledger)
    demo = run_controlled_demo()
    payload = {"task": "TASK-S7-04", "subtask": "B native 期探活设施演示",
               "part1_real_ledger": ledger, "part2_controlled_demo": demo}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("=" * 78)
    print("第一段：真实台账的可探活范围（只读）")
    print("=" * 78)
    print(f"  台账能力总数：{ledger.get('capabilities_total')}"
          f"（台账={ledger.get('ledger_path') or '默认路径'}）")
    print(f"  stage 分布  ：{ledger.get('stage_counts')}")
    print(f"  可探活目标  ：{ledger.get('probe_targets')}"
          f"（{ledger.get('probe_stage_scope')} 共 "
          f"{ledger.get('internalized_or_native_count')} 个）")
    print(f"  run_all()   ：status={ledger.get('run_all_status')}")
    print(f"  说明        ：{ledger.get('run_all_note')}")
    print()
    print("=" * 78)
    print("第二段：受控演示（native 能力 → 健康探活 → 退化探活）")
    print("=" * 78)
    print(f"  ① 首次探活：verdict={demo['first_probe']['verdict']}"
          f"  基线已建立={demo['baseline_established_on_first']}"
          f"  通过率={demo['first_probe']['pass_rate']}"
          f"  真实墙钟 p99={demo['first_probe']['p99_wall_candidate_ms']}ms")
    print(f"     未判定项（首次无基线，**不误报退化**）："
          f"{demo['first_probe']['unmeasured_conditions']}")
    print(f"  ② 健康复探：verdict={demo['healthy_probe']['verdict']}"
          f"  触发条件={demo['healthy_probe']['triggered_conditions']}"
          f"  建议回退={demo['healthy_probe']['rollback_recommended']}")
    deg = demo["degraded_probe"]
    print(f"  ③ 退化探活：verdict={deg['verdict']}  degraded={deg['degraded']}"
          f"  命中条件={deg['triggered_conditions']}")
    for condition in deg["conditions"]:
        if condition["triggered"]:
            print(f"       [{condition['label']}] {condition['reasons'][-1]}")
    rollback = deg["rollback"] or {}
    print(f"     回退建议：{rollback.get('from_stage')} → {rollback.get('to_stage')}"
          f"  recommended={rollback.get('recommended')}"
          f"  **executed={rollback.get('executed')}**"
          f"  requires_approval={rollback.get('requires_approval')}")
    print(f"     建议规则 id：{rollback.get('rule_id')}")
    print(f"     若需执行的调用形状（本脚本**不执行**）：{rollback.get('apply_hint')}")
    print(f"     事故卡：{deg['incident_id']}"
          f"  缺失要素={deg['incident_missing_elements']}"
          f"  可 resolved={deg['incident_resolvable']}")
    print(f"  台账 stage：{demo['stage_before']} → {demo['stage_after']}"
          f"  set_stage 调用={demo['set_stage_calls']}"
          f"  **未自动执行={demo['stage_unchanged']}**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
