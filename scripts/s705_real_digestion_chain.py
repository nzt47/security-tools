#!/usr/bin/env python
"""TASK-S7-05 真实数据打通：真实任务采集 + 六阶段消化链（一条命令可复现）

【这个脚本做什么】

把 S3 全链（清洗 → 挖掘 → 判定集 → 回放/验收门 → 灰度 → 内化决策 → promote PR）
**第一次跑在真实数据上**，并把证据全部落到部署运行时根（面板读的就是这里）：

| 阶段 | 真实数据来源 | 落点（面板读的目录） |
|---|---|---|
| ① 采集 | 受控工作区里的**真实任务**（真读文件 / 真跑 pytest / 真写报告） | `<root>/agent/data/tool_trace.db`（S2-01 统一台账） |
| ② 消化 | 同上台账的同类轨迹 | `digest.stage` / `skill.generated` 事件 + draft SKILL.md |
| ③ 判定集 | Seed Pack + **真实轨迹派生用例**（带 `origin_trace_id`） | `<root>/data/digestion/cases/` |
| ④ 验收门 | 四条件回放 | `<root>/data/digestion/cases/passports/` |
| ⑤ 灰度 | 显式开开关 + 每日预算上限 | `<root>/data/digestion/shadow/shadow_ledger.jsonl` |
| ⑥ 内化 | 六条件（⑤⑥ 一票否决） | `<root>/data/digestion/promote_pr/<slug>/<pr_id>/decision.json` |

【为什么默认写到"部署根"而不是 worktree】

治理面板按**代码根**（`agent/...` 上一级）解析运行时目录。本任务在 worktree 里做，
但面板服务的是**部署根**；若把真实数据写进 worktree，面板仍然是 0，"打通最后一公里"
就落空了。故本脚本用 `git rev-parse --git-common-dir` 推导**部署根**并显式写入其中；
`--runtime-root` 可覆盖（用于隔离复跑），所有目录都会在开始时打印。

【纪律】

- **显式传路径**：所有运行时目录都由本脚本显式指定（env / 构造参数），不依赖隐式默认；
- **不得合成轨迹**：轨迹全部由 `agent.digestion.real_capture` 的真实工具调用产生；
- **不得调参刷过**：验收门四条件、内化六条件的门槛全部取模块常量（不因演示下调）；
  未通过项如实记录；
- **开关显式开 + 结束复位**：演示前/后各打一次开关快照并对比（`--emit-switch-snapshots`）；
- promote PR 是**本地产物**：脚本不 push、不 merge（`create_promote_pr` 内部固定
  `pushed=False / merged=False`）。

用法::

    # 全流程（采集 + 14 个到达批次 × 每批一次真实消化周期）
    python scripts/s705_real_digestion_chain.py --tasks 210 --batches 14

    # 只采集（不跑消化链）
    python scripts/s705_real_digestion_chain.py --collect-only

    # 隔离复跑（全部写临时目录，不碰部署运行时）
    python scripts/s705_real_digestion_chain.py --runtime-root data/s705_isolated

    # 面板三列读数（部署根默认路径口径）
    python scripts/s705_panel_probe.py --runtime-root <部署根>
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CODE_ROOT = os.path.dirname(SCRIPT_DIR)
if CODE_ROOT not in sys.path:
    sys.path.insert(0, CODE_ROOT)

#: 目标能力（只读、可回放、副作用可控 —— 任务书 §五 风险预案的首选）
DEFAULT_CAPABILITY = "cp.builtin.read_file"
#: 正式门槛（v7.2 §3.3）：每能力 ≥20 条同类轨迹 —— **不因演示下调**
FORMAL_THRESHOLD = 20
#: 演示门槛（Owner 默认 ≥5）：仅用于"演示声明"，与正式门槛**分开标注**
DEMO_THRESHOLD = 5
#: 判定集规模收敛上限（S3-02：30–100 组；超出即确定性采样收敛）
DEFAULT_CASE_LIMIT = 60

#: 演示期**显式开启**的开关（默认全部关闭；演示后必须复位，见 main 的快照对比）
#: 只开 shadow 主开关：预算仍按引擎默认 ratio=0.15 / cap=50（"小预算"由引擎算，
#: 不由本脚本塞一个更大的数）—— 灰度策略的 5% 走能力级 shadow_config，不改全局。
DEMO_SWITCHES: Dict[str, str] = {
    "CP_DIGESTION_SHADOW_ENABLED": "true",
}


def enable_demo_switches(values: Optional[Dict[str, str]] = None
                         ) -> Dict[str, Optional[str]]:
    """显式开启演示开关，返回**被覆盖前的原值**（复位用；``None`` = 原本未设置）"""
    saved: Dict[str, Optional[str]] = {}
    for key, value in (values or DEMO_SWITCHES).items():
        saved[key] = os.environ.get(key)
        os.environ[key] = str(value)
    return saved


def restore_switches(saved: Dict[str, Optional[str]]) -> None:
    """复位到演示前的原值（原本未设置 ⇒ 删除该变量，而不是置空）"""
    for key, value in (saved or {}).items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)


# ════════════════════════════════════════════════════════════
#  运行时根与目录（显式，无隐式默认）
# ════════════════════════════════════════════════════════════


def _git_common_root() -> str:
    """部署根：worktree → 主仓库根（`git rev-parse --git-common-dir` 的父目录）"""
    try:
        out = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                             cwd=CODE_ROOT, capture_output=True, text=True,
                             timeout=15)
        if out.returncode == 0:
            common = str(out.stdout or "").strip()
            if common:
                if not os.path.isabs(common):
                    common = os.path.join(CODE_ROOT, common)
                root = os.path.dirname(os.path.abspath(common))
                if os.path.isdir(root):
                    return root
    except Exception:  # noqa: BLE001  git 不可用 → 回退代码根（不猜别的）
        pass
    return CODE_ROOT


def runtime_dirs(runtime_root: str) -> Dict[str, str]:
    """运行时目录清单（全部由 `runtime_root` 显式推导）"""
    root = os.path.abspath(str(runtime_root))
    demo = os.path.join(root, "data", "digestion", "s705_demo")
    return {
        "runtime_root": root,
        "demo_dir": demo,
        "workspace": os.path.join(demo, "workspace"),
        "drafts": os.path.join(demo, "drafts"),
        "evidence": demo,
        "trace_db": os.path.join(root, "agent", "data", "tool_trace.db"),
        "descriptors": os.path.join(root, "data", "descriptors.json"),
        "events_dir": os.path.join(root, "data", "events"),
        "case_dir": os.path.join(root, "data", "digestion", "cases"),
        "shadow_dir": os.path.join(root, "data", "digestion", "shadow"),
        "promote_dir": os.path.join(root, "data", "digestion", "promote_pr"),
        "audit_db": os.path.join(root, "data", "audit", "audit_chain.db"),
        "audit_roots": os.path.join(root, "data", "audit", "daily_roots.jsonl"),
        "audit_key": os.path.join(root, "data", "audit", "audit_signing_key.pem"),
    }


def apply_runtime_env(paths: Dict[str, str]) -> None:
    """把运行时目录写进 env（**必须在 import agent 之前**，见模块文档）"""
    for key in ("demo_dir", "workspace", "drafts"):
        os.makedirs(paths[key], exist_ok=True)
    for key in ("events_dir", "case_dir", "shadow_dir", "promote_dir"):
        os.makedirs(paths[key], exist_ok=True)
    os.environ["CP_EVENTS_DIR"] = paths["events_dir"]
    os.environ["CP_DIGESTION_CASE_DIR"] = paths["case_dir"]
    os.environ["CP_DIGESTION_SHADOW_DIR"] = paths["shadow_dir"]
    os.environ["CP_DIGESTION_PROMOTE_DIR"] = paths["promote_dir"]
    os.environ["AUDIT_DB_PATH"] = paths["audit_db"]
    # 回放沙箱允许"模拟外部调用"（`shell_execute` 在沙箱内只记账不真实执行）；
    # 真实执行发生在**采集期**，与回放严格分离（§4.5 record-and-replay）
    os.environ.pop("CP_DIGESTION_SANDBOX_DENY_EXTERNAL", None)


def _write_json(path: str, payload: Any) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, default=str)
    return path


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _step(n: Any, text: str) -> None:
    print(f"\n[阶段 {n}] {text}")


# ════════════════════════════════════════════════════════════
#  单次真实消化周期（六阶段）
# ════════════════════════════════════════════════════════════


def _migration_view(migration: Any) -> Optional[Dict[str, Any]]:
    """`StageMigration` → 可序列化视图（dataclass 无 `to_dict`，此处显式取字段）"""
    if migration is None:
        return None
    import dataclasses

    payload = dataclasses.asdict(migration)
    payload["ok"] = bool(getattr(migration, "ok", False))
    return payload


def run_cycle(ctx: Dict[str, Any], *, cycle: int,
              shadow_daily_avg: Optional[float]) -> Dict[str, Any]:
    """跑一次真实的六阶段消化周期（全部真实路径，无桩、无注入证据）"""
    from agent.digestion import cases as C
    from agent.digestion import gate as G
    from agent.digestion.sandbox import PatternImplementation

    cap = ctx["capability"]
    svc = ctx["svc"]
    out: Dict[str, Any] = {"cycle": int(cycle), "capability_id": cap}

    # ── ① 消化流水线（清洗 → 挖掘 → 候选模式 → draft SKILL.md → stage 建议/迁移） ──
    report = svc.pipeline(capability_id=cap, limit=0)
    out["pipeline"] = {
        "digest_run_id": report.digest_run_id,
        "eligible": bool(report.eligible),
        "reason": str(report.reason or ""),
        "threshold": int(report.threshold),
        "rows_total": int(report.rows_total),
        "trajectories_total": int(report.trajectories_total),
        "negative_total": int(report.negative_total),
        "trace_sets": list(report.trace_sets)[:5],
        "cleanup": dict(report.cleanup),
        "capability_resolution": dict(report.capability_resolution),
        "stage_recommendation": dict(report.stage_recommendation or {}),
        "stage_migration": _migration_view(report.stage_migration),
        "events": list(report.events),
        "drafts": [{"skill_id": d.skill_id, "status": d.status,
                    "source_track": d.source_track} for d in report.drafts],
    }
    pattern = report.candidates[0] if report.candidates else None
    if pattern is None:
        out["aborted"] = "未挖出候选模式（未达门槛或无骨架）"
        return out
    out["pattern"] = {
        "pattern_id": pattern.pattern_id,
        "steps": [s.label for s in pattern.steps],
        "support": int(pattern.support),
        "sample_size": int(pattern.sample_size),
        "coverage": float(pattern.coverage),
        "confidence": float(pattern.confidence),
        "lcs_length": int(pattern.lcs_length),
        "is_shallow": bool(pattern.is_shallow),
        "slots": [s.name for s in pattern.slots],
        "branches": [{"at_step": b.at_step, "condition": b.condition,
                      "support": b.support, "total": b.total,
                      "outcome": b.outcome} for b in pattern.branches],
        "side_effect_profile": dict(pattern.side_effect_profile or {}),
    }

    # ── ② 判定集（Seed Pack + 真实轨迹派生用例，带 origin_trace_id） ──
    seed_cases = C.seed_cases_for(cap)
    concrete = C.concrete_args_provider(svc.store, cap, registry=ctx["reg"])
    trace_set = C.trace_set_for(cap, service=svc, limit=0)
    if trace_set is None:
        out["aborted"] = "同类成功轨迹集为空"
        return out
    trace_cases_all = C.cases_from_trace_set(trace_set, capability_id=cap,
                                             concrete_args=concrete)
    trace_cases = trace_cases_all[:int(ctx["case_limit"])]
    for case in seed_cases:
        # M4 显式适用性：Seed 用例描述 read_file 的**单次读取契约**，与被评
        # 「读→测→写」三段任务链候选形状不同 ⇒ 显式声明排除（取代 active+notes）
        case.applicability = C.CaseApplicability(
            exclude_kinds=[C.CANDIDATE_KIND_PATTERN],
            reason=("Seed 用例描述 read_file 单次读取契约，与被评三段任务链候选"
                    "形状不同（M4 显式字段）"),
            declared_by="s705_real_digestion_chain")
    case_set = C.build_case_set(cap, C.merge_cases(seed_cases, trace_cases),
                               version=int(cycle), upstream_version="1.0.0")
    ctx["case_store"].save(case_set)
    applicable, excluded = C.applicable_cases(case_set.active_cases(),
                                              C.CANDIDATE_KIND_PATTERN)
    out["case_set"] = {
        "version": int(case_set.version),
        "total": int(case_set.size),
        "seed": len(seed_cases), "trace": len(trace_cases),
        "trace_available": len(trace_cases_all),
        "trace_capped_at": int(ctx["case_limit"]),
        "size_verdict": case_set.size_verdict(),
        "applicable": len(applicable),
        "excluded": len(excluded),
        "origin_trace_ids_sample": [c.origin_trace_id for c in trace_cases[:3]],
        "kinds": case_set.kind_counts(),
    }

    # ── ③ 验收门（四条件 → 通行证 → 凭通行证 mirrored→shadow） ──
    candidate = PatternImplementation(pattern)
    gate = G.acceptance_gate(
        cap, case_set=case_set, store=ctx["case_store"], candidate=candidate,
        sandbox=ctx["box"], pattern=pattern, passport_store=ctx["passport_store"],
        baseline_store=svc.store, advance=True, registry=ctx["reg"],
        candidate_kind=C.CANDIDATE_KIND_PATTERN)
    out["gate"] = {k: v for k, v in gate.to_dict().items() if k != "replay"}
    out["gate"]["replay"] = {
        "total": gate.replay_report.total if gate.replay_report else 0,
        "passed": gate.replay_report.passed if gate.replay_report else 0,
        "failed_ids": (gate.replay_report.failed_ids
                       if gate.replay_report else []),
        "layer_failures": (dict(gate.replay_report.layer_failures)
                           if gate.replay_report else {}),
        "manual_sample": (list(gate.replay_report.manual_sample)
                          if gate.replay_report else []),
        "clock": "确定性模型时钟（回放沙箱；真实墙钟见灰度阶段）",
    }

    # ── ④ shadow 灰度（显式开开关 + 每日预算上限 + 真实墙钟 p99） ──
    shadow = ctx["shadow_runner"].run(
        cap, case_set=case_set, candidate=candidate,
        daily_avg=shadow_daily_avg,
        shadow_config={"enabled": True, "gray_ratio": 0.05},
        force=True, candidate_kind=C.CANDIDATE_KIND_PATTERN)
    out["shadow"] = {
        "allowed": bool(shadow.allowed),
        "blocked_reasons": list(shadow.blocked_reasons or []),
        "plan": shadow.plan.to_dict(),
        "total": int(shadow.total), "passed": int(shadow.passed),
        "negative": int(shadow.negative), "pass_rate": float(shadow.pass_rate),
        "judge_kind": str(shadow.judge_kind), "judge_is_llm": bool(shadow.judge_is_llm),
        "judge": dict(shadow.judge or {}),
        "p99_wall_candidate_ms": shadow.p99_wall_candidate_ms(),
        "p99_wall_upstream_ms": shadow.p99_wall_upstream_ms(),
        "p99_model_candidate_ms": shadow.p99_model_candidate_ms(),
        "p99_model_upstream_ms": shadow.p99_model_upstream_ms(),
        "manual_sample": list(shadow.manual_sample or []),
        "manual_review": dict(shadow.manual_review or {}),
        "degradation": dict(shadow.degradation or {}),
        "isolation": dict(shadow.isolation or {}),
        "event_id": str(shadow.event_id or ""),
        "audit_seq": int(shadow.audit_seq or 0),
    }

    # ── ⑤ 内化六条件（⑤⑥ 一票否决；真实证据，无注入） ──
    decision = ctx["engine"].evaluate(cap, shadow_report=shadow,
                                      registry=ctx["reg"],
                                      ledger_store=svc.store)
    out["decision"] = decision.to_dict()
    out["decision"]["verdict"] = str(decision.verdict)
    out["decision"]["promotable"] = bool(decision.promotable)

    # ── ⑥ stage.promote PR（本地产物；不 push、不 merge） ──
    pr = None
    if decision.promotable:
        pr = ctx["engine"].create_promote_pr(decision, registry=ctx["reg"],
                                             out_dir=ctx["promote_dir"])
    out["promote_pr"] = pr.to_dict() if pr is not None else None
    return out


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TASK-S7-05 真实数据打通：真实任务采集 + 六阶段消化链")
    parser.add_argument("--runtime-root", default="",
                        help="运行时根（默认：由 git common dir 推导的部署根）")
    parser.add_argument("--tasks", type=int, default=315, help="真实任务条数")
    parser.add_argument("--batches", type=int, default=21,
                        help="到达批次数（每批 = 一次真实消化周期）")
    parser.add_argument("--bug-every", type=int, default=10,
                        help="每 N 个被测模块注入一个真实缺陷（0=不注入）")
    parser.add_argument("--capability", default=DEFAULT_CAPABILITY)
    parser.add_argument("--threshold", type=int, default=FORMAL_THRESHOLD,
                        help="同类轨迹条数门槛（默认正式门槛 20，不因演示下调）")
    parser.add_argument("--demo-threshold", type=int, default=DEMO_THRESHOLD)
    parser.add_argument("--case-limit", type=int, default=DEFAULT_CASE_LIMIT,
                        help="判定集轨迹用例上限（S3-02 规模区间 30–100）")
    parser.add_argument("--shell", default="cmd", help="真实 shell（cmd/powershell）")
    parser.add_argument("--timeout", type=int, default=120, help="单条真实命令超时秒")
    parser.add_argument("--collect-only", action="store_true",
                        help="只采集真实轨迹，不跑消化链")
    parser.add_argument("--chain-only", action="store_true",
                        help="跳过采集（复用已有台账），直接跑消化链")
    parser.add_argument("--no-archive", dest="archive", action="store_false",
                        default=True)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    root = os.path.abspath(args.runtime_root or _git_common_root())
    paths = runtime_dirs(root)
    apply_runtime_env(paths)

    # ── import agent 侧（必须在 env 就绪之后：AUDIT_DB_PATH 在 import 期读取） ──
    from agent.digestion import cases as C
    from agent.digestion import shadow as SH
    from agent.digestion.gate import PassportStore
    from agent.digestion.internalize import InternalizeEngine
    from agent.digestion.real_capture import (EVIDENCE_FILENAME, RealTaskRunner,
                                              build_workspace, registry_coverage,
                                              same_kind_summary,
                                              summarize_results, write_evidence)
    from agent.digestion.sandbox import ReplaySandbox
    from agent.digestion.service import DigestionService
    from agent.digestion.switch_snapshot import (diff_snapshots,
                                                 switch_snapshot)
    from agent.descriptors.registry import DescriptorRegistry
    from agent.observability.trace_v2 import TraceFacade

    _hr("TASK-S7-05 真实数据打通（真实内化演示 + 治理面板出真数）")
    print(f"代码根（本脚本所在）: {CODE_ROOT}")
    print(f"运行时根（真实数据落点）: {paths['runtime_root']}")
    print(f"  统一台账  : {paths['trace_db']}")
    print(f"  descriptor: {paths['descriptors']}")
    print(f"  事件目录  : {paths['events_dir']}")
    print(f"  判定集    : {paths['case_dir']}")
    print(f"  灰度台账  : {paths['shadow_dir']}")
    print(f"  promote PR: {paths['promote_dir']}")
    print(f"  审计链    : {paths['audit_db']}")
    print("口径声明：本次为**真实任务**（真实工具调用 + 真实结果状态）在真实运行时目录上"
          "的全链跑通；门槛一律取模块常量，未通过项如实记录。")

    # ── 开关快照（演示前 → 演示期 → 演示后复位） ──
    snap_before = switch_snapshot(label="演示前（环境原状）")
    before_path = _write_json(os.path.join(paths["evidence"], "s705_开关快照_演示前.json"),
                              snap_before)
    saved_switches = enable_demo_switches()
    snap_during = switch_snapshot(label="演示期（显式开启）")
    during_path = _write_json(os.path.join(paths["evidence"], "s705_开关快照_演示期.json"),
                              snap_during)
    print(f"\n开关快照（演示前）: {before_path}")
    print(f"开关快照（演示期）: {during_path}"
          f"｜显式开启: {sorted(DEMO_SWITCHES)}")
    print(f"  shadow 开关生效值：演示前="
          f"{(snap_before['effective'].get('shadow_enabled') or {}).get('value')}"
          f" → 演示期="
          f"{(snap_during['effective'].get('shadow_enabled') or {}).get('value')}"
          f"｜judge_kind = "
          f"{(snap_before['effective'].get('judge_kind') or {}).get('value')}")

    # ── 运行时对象（全部显式构造，不依赖隐式默认） ──
    #
    # 审计链：`AuditFacade` 的 db 路径虽可由 `AUDIT_DB_PATH` 给出，但**签名密钥与
    # 每日根路径**没有 env 通道、默认按"代码根"解析 —— 在 worktree 里跑就会用到
    # worktree 的密钥，与部署链的历史记录签名不一致。故这里**显式构造并 bind**
    # 部署根的链（db + 每日根 + 密钥三件套全部显式），使链式审计连续可验。
    from agent.audit import facade as audit_facade_mod
    from agent.audit.chain import AuditChain
    os.makedirs(os.path.dirname(paths["audit_db"]), exist_ok=True)
    audit_chain = AuditChain(paths["audit_db"], roots_path=paths["audit_roots"],
                             signing_key_path=paths["audit_key"])
    audit_facade_mod.audit.bind(audit_chain)
    audit_facade_mod.audit.enabled = True
    print(f"\n审计链已绑定：db={paths['audit_db']}"
          f"｜key={paths['audit_key']}")

    facade = TraceFacade(paths["trace_db"])
    TraceFacade._instance = facade
    reg = DescriptorRegistry(path=paths["descriptors"])   # autosave=True：stage 真实落库
    # ── S1-02 trust 回填（**前置配置动作，不是调参**） ──
    # 内化条件⑥（隐私闸门）对"未分级"从严：`data_class=None` ⇒ `unknown` ⇒ 一票否决
    # （这正是引擎的设计）。`cp.builtin.read_file` 在真实台账里的 `data_class` 为
    # null —— 本步按 S1-02 口径**如实分级**为 `internal`（只读本地仓库文件，不外发），
    # 前后值、actor 与理由全部进证据；不做任何"为了让条件通过"的数值调整。
    trust_backfill: Dict[str, Any] = {"applied": False,
                                      "capability_id": str(args.capability)}
    desc = reg.get(str(args.capability))
    if desc is not None:
        trust = getattr(desc, "trust", None)
        origin = getattr(desc, "origin", None)
        before = {"data_class": getattr(trust, "data_class", None),
                  "external_endpoint": getattr(origin, "external_endpoint", None)}
        trust_backfill["before"] = before
        if not before["data_class"]:
            reg.update_fields(
                str(args.capability), {"trust": {"data_class": "internal"}},
                actor="s705_demo",
                reason=("TASK-S7-05：S1-02 trust 回填 —— read_file 只读本地仓库文件、"
                        "不外发 ⇒ data_class=internal"))
            after_desc = reg.get(str(args.capability))
            after_trust = getattr(after_desc, "trust", None)
            after_origin = getattr(after_desc, "origin", None)
            trust_backfill.update({
                "applied": True,
                "after": {"data_class": getattr(after_trust, "data_class", None),
                          "external_endpoint":
                              getattr(after_origin, "external_endpoint", None)},
                "reason": "S1-02 trust 回填（本地文件读取 ⇒ internal）"})
            print(f"\ntrust 回填：{args.capability} data_class "
                  f"{before['data_class']!r} → "
                  f"{trust_backfill['after']['data_class']!r}"
                  f"（S1-02 口径；未回填则条件⑥ 一票否决 —— 引擎从严，如设计）")
    trust_backfill["after"] = trust_backfill.get("after") or trust_backfill.get("before")
    # `TraceFacade._store` 是 S3-01/02/03 演示同款的取库方式（门面本身不暴露 store）
    store = facade._store
    case_store = C.open_case_store(root=paths["case_dir"])
    passport_store = PassportStore(root=paths["case_dir"])
    shadow_ledger = SH.ShadowLedger(directory=paths["shadow_dir"])
    review_queue = SH.ManualReviewQueue(directory=paths["shadow_dir"])
    box = ReplaySandbox()
    svc = DigestionService(store=store, registry=reg, threshold=int(args.threshold),
                           draft_dir=paths["drafts"], persist_drafts=True,
                           emit_events=True)
    judge = SH.resolve_judge("auto")
    shadow_runner = SH.ShadowRunner(
        judge=judge.scorer, judge_kind=judge.kind, passport_store=passport_store,
        case_store=case_store, ledger=shadow_ledger, review_queue=review_queue,
        env=dict(os.environ), emit_events=True)
    engine = InternalizeEngine(passport_store=passport_store, registry=reg,
                               case_store=case_store, ledger=shadow_ledger,
                               shadow_runner=shadow_runner)
    ctx: Dict[str, Any] = {
        "capability": str(args.capability), "svc": svc, "reg": reg, "store": store,
        "case_store": case_store, "passport_store": passport_store,
        "box": box, "shadow_runner": shadow_runner, "engine": engine,
        "promote_dir": paths["promote_dir"], "threshold": int(args.threshold),
        "case_limit": int(args.case_limit),
    }
    print(f"\njudge 解析：kind=`{judge.kind}`（LLM={judge.is_llm}）"
          f"｜{judge.detail.get('note') or judge.detail.get('unavailable_reason', '')}")

    # ── 阶段 1：真实任务采集（分批）+ 每批一次真实消化周期 ──
    collection: Dict[str, Any] = {}
    cycles: List[Dict[str, Any]] = []
    results: List[Any] = []
    specs: List[Any] = []
    if not args.chain_only:
        _step(1, f"真实任务采集：受控工作区 + 真实工具（{args.tasks} 条，"
                 f"分 {args.batches} 批到达）")
        specs = build_workspace(paths["workspace"], modules=int(args.tasks),
                                bug_every=int(args.bug_every))
        runner = RealTaskRunner(facade=facade, workspace=paths["workspace"],
                                workspace_id="ws_s705_real", shell=str(args.shell),
                                timeout=int(args.timeout))
        batches = max(1, int(args.batches))
        per = max(1, (len(specs) + batches - 1) // batches)
        started = time.perf_counter()
        for batch in range(batches):
            slice_specs = specs[batch * per:(batch + 1) * per]
            if not slice_specs:
                break
            for spec in slice_specs:
                results.append(runner.run_task(spec))
            facade.flush()
            done = len(results)
            print(f"  批次 {batch + 1}/{batches}：已完成真实任务 {done} 条"
                  f"（成功 {sum(1 for r in results if r.status == 'success')}）"
                  f"｜{time.perf_counter() - started:.0f}s")
            if not args.collect_only:
                cycle = run_cycle(ctx, cycle=batch + 1,
                                  shadow_daily_avg=float(done))
                cycles.append(cycle)
                print(f"    消化周期 {batch + 1}：模式="
                      f"{(cycle.get('pattern') or {}).get('pattern_id', '-')}"
                      f"｜门={(cycle.get('gate') or {}).get('passed')}"
                      f"｜灰度样本={(cycle.get('shadow') or {}).get('total')}"
                      f"｜内化判决={(cycle.get('decision') or {}).get('verdict')}"
                      f"｜PR={'有' if cycle.get('promote_pr') else '无'}")
        facade.flush()
        collection = {
            "workspace": paths["workspace"],
            "specs": [s.to_dict() for s in specs],
            "results": [r.to_dict() for r in results],
            "summary": summarize_results(results),
            "same_kind_formal_threshold": same_kind_summary(
                store, ctx["capability"], threshold=int(args.threshold),
                registry=reg),
            "same_kind_demo_threshold": same_kind_summary(
                store, ctx["capability"], threshold=int(args.demo_threshold),
                registry=reg),
            "registry_coverage": registry_coverage(store, reg,
                                                   threshold=int(args.threshold)),
        }
        ev = write_evidence(collection,
                            os.path.join(paths["evidence"], EVIDENCE_FILENAME))
        print(f"\n  真实轨迹采集证据: {ev}")
        print(f"  同类轨迹（正式门槛 ≥{args.threshold}）："
              f"max={collection['same_kind_formal_threshold']['max_success_size']}"
              f"｜达标={collection['same_kind_formal_threshold']['meets_threshold']}")
        print(f"  同类轨迹（演示门槛 ≥{args.demo_threshold}）："
              f"达标={collection['same_kind_demo_threshold']['meets_threshold']}")
        print(f"  未达门槛的同类组数（正式门槛口径）："
              f"{len(collection['same_kind_formal_threshold']['below_threshold'])}")
        coverage = collection["registry_coverage"]
        print(f"  全台账能力覆盖：达标 {coverage.get('meets')}/"
              f"{len(coverage.get('capabilities') or [])}"
              f"｜未达正式门槛 {coverage.get('below_threshold_count')} 个"
              f"（清单见采集证据 registry_coverage.below_threshold）")

    # ── 阶段 2：最终一次完整消化周期（详细证据） ──
    _step(2, "最终真实消化周期（六阶段详细证据）")
    final_cycle = run_cycle(ctx, cycle=max(1, len(cycles) + 1),
                            shadow_daily_avg=float(len(results) or 0) or None)
    cycles.append(final_cycle)
    print(f"  ① 消化：eligible={final_cycle.get('pipeline', {}).get('eligible')}"
          f"｜{final_cycle.get('pipeline', {}).get('reason')}")
    print(f"  ② 判定集：{json.dumps(final_cycle.get('case_set', {}).get('size_verdict', {}), ensure_ascii=False)}")
    gate = final_cycle.get("gate") or {}
    print(f"  ③ 验收门：passed={gate.get('passed')}｜executed={gate.get('executed')}"
          f"｜失败条件={gate.get('failed_conditions')}")
    shadow_out = final_cycle.get("shadow") or {}
    print(f"  ④ 灰度：allowed={shadow_out.get('allowed')}"
          f"｜样本={shadow_out.get('total')}｜通过率={shadow_out.get('pass_rate')}"
          f"｜judge_kind={shadow_out.get('judge_kind')}")
    print(f"     墙钟 p99：候选 {shadow_out.get('p99_wall_candidate_ms')}ms / "
          f"上游 {shadow_out.get('p99_wall_upstream_ms')}ms")
    decision = final_cycle.get("decision") or {}
    print(f"  ⑤ 内化判决：{decision.get('verdict')}｜排序分={decision.get('rank_score')}"
          f"｜阻断项={decision.get('blocker') or '（无）'}")
    for cond in (decision.get("conditions") or []):
        print(f"     - {cond.get('name')}[{cond.get('dimension')}] "
              f"passed={cond.get('passed')} actual={cond.get('actual')} "
              f"threshold={cond.get('threshold')}")
    pr = final_cycle.get("promote_pr")
    print(f"  ⑥ promote PR：{('本地已产出 ' + str(pr.get('pr_id'))) if pr else '未产出（判决不可 promote）'}")

    # ── 阶段 3：面板三列读数（真实运行时目录） ──
    _step(3, "治理面板三列读数（数据源 = 上面的真实落点）")
    from agent.ui_panels.data import pipeline_view
    panel = pipeline_view(days=30, events_dir=paths["events_dir"],
                          shadow_dir=paths["shadow_dir"],
                          promote_dir=paths["promote_dir"], registry=reg)
    _print_panel(panel)
    panel_path = _write_json(os.path.join(paths["evidence"], "s705_面板读数.json"),
                             panel)

    # ── 阶段 4：开关复位 + 快照对比 ──
    _step(4, "开关复位与快照对比（演示前 ↔ 演示期 ↔ 演示后）")
    restore_switches(saved_switches)
    snap_after = switch_snapshot(label="演示后（已复位）")
    after_path = _write_json(os.path.join(paths["evidence"], "s705_开关快照_演示后.json"),
                             snap_after)
    diff_before_after = diff_snapshots(snap_before, snap_after)
    diff_before_during = diff_snapshots(snap_before, snap_during)
    print(f"  开关快照（演示后）: {after_path}")
    print(f"  演示期开启项（前→期）：{diff_before_during['added']}"
          f"｜生效值变化={diff_before_during['effective_changed']}")
    print(f"  复位对比（前↔后）：identical={diff_before_after['identical']}"
          f"｜added={diff_before_after['added']}"
          f"｜removed={diff_before_after['removed']}"
          f"｜changed={diff_before_after['changed']}"
          f"｜effective_changed={diff_before_after['effective_changed']}")
    if not diff_before_after["identical"]:
        print("  ✗ 开关未完全复位 —— 如实记为未通过（不得静默）")

    # ── 归档 ──
    chain_payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "runtime_root": paths["runtime_root"],
        "code_root": CODE_ROOT,
        "capability_id": ctx["capability"],
        "thresholds": {"formal": int(args.threshold),
                       "demo": int(args.demo_threshold),
                       "note": "两者不通用；本演示所有输出旁均标注『演示门槛』或『正式门槛』"},
        "judge": {"kind": judge.kind, "is_llm": judge.is_llm, "detail": judge.detail},
        "cycles": cycles,
        "final": final_cycle,
        "panel": panel,
        "panel_path": panel_path,
        "switch_snapshot_before": snap_before,
        "switch_snapshot_during": snap_during,
        "switch_snapshot_after": snap_after,
        "switch_diff_before_during": diff_before_during,
        "switch_diff_after_reset": diff_before_after,
        "trust_backfill": trust_backfill,
        "collection": collection,
    }
    if args.archive:
        chain_path = _write_json(
            os.path.join(paths["evidence"], "s705_链证据.json"), chain_payload)
        print(f"\n  全链证据: {chain_path}")

    _hr("结束")
    print(f"真实任务={len(results)}｜同类轨迹 max="
          f"{collection.get('same_kind_formal_threshold', {}).get('max_success_size', '-')}"
          f"｜门={gate.get('passed')}｜内化判决={decision.get('verdict')}"
          f"｜面板三列="
          f"{[lane['event_count']['value'] for lane in panel.get('lanes', [])]}"
          f"｜shadow_runs={panel['summary']['shadow_runs']['value']}"
          f"｜internalize_decisions={panel['summary']['internalize_decisions']['value']}")
    return 0


def _print_panel(panel: Dict[str, Any]) -> None:
    """打印面板三列/关键指标（每一格都带它自己的数据源）"""
    for lane in panel.get("lanes", []):
        metric = lane.get("event_count") or {}
        print(f"  泳道「{lane.get('title')}」事件数 = {metric.get('value')}"
              f"（源：{metric.get('source')}）")
    summary = panel.get("summary") or {}
    for key in ("digest_stage_events", "applied_migrations", "shadow_runs",
                "internalize_decisions", "internalize_rate", "capabilities_touched"):
        item = summary.get(key) or {}
        print(f"  {key} = {item.get('value')}（源：{item.get('source')}）")
    print(f"  stage_distribution = {summary.get('stage_distribution')}")
    print(f"  灰度卡片数 = {len(panel.get('shadow') or [])}"
          f"｜内化决策卡片数 = {len(panel.get('internalize') or [])}")


if __name__ == "__main__":
    raise SystemExit(main())
