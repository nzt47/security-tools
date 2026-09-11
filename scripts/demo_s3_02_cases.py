"""TASK-S3-02 端到端演示：判定集 → 回放沙箱 → 验收门 → 通行证 → 漂移重探

覆盖验收清单的"真实能力样例（判定集 → 回放 → 验收门）可复现"。**全部隔离**：
统一台账 / descriptor 台账 / 审计链 / 事件 / 判定集存储 / 通行证全部落在临时目录，
仅演示产物归档到 ``data/digestion/demo_s3_02/`` 供验收报告引用。

演示路径（真实代码路径，非桩）：

1. 合成同类轨迹台账（含探索前缀/重试噪声 + 失败轨迹 + **真实副作用记录**）
2. `DigestionService.pipeline()` → 候选模式 + draft SKILL.md + stage 入轨（borrowed→mirrored）
3. 判定集三通道：Seed Pack（**独立回放验证**）+ Trace 自动生成（**复制台账原始参数**，
   数据脱离 Trace 生命周期，带 origin_trace_id）
4. 回放沙箱：双跑 + 三层比对 + 确定性自检 + record-and-replay 台账
5. 验收门四条件 → 通行证（`digest.stage` 事件 + 链式审计）
6. 凭通行证 `stage_migrate(mirrored → shadow)` 三处联动
7. 漂移重探（上游版本变化）→ drifted → 判定集失效 → 重生成新版本
8. 负样本（`--mutate` 候选缺末步 / `--uncovered-branch` 分支未覆盖）→ 门应拒绝

用法::

    python scripts/demo_s3_02_cases.py                    # 完整演示（应通过）
    python scripts/demo_s3_02_cases.py --tasks 40         # 调整同类轨迹条数
    python scripts/demo_s3_02_cases.py --mutate           # 负样本：候选提取错误
    python scripts/demo_s3_02_cases.py --uncovered-branch # 负样本：破坏性分支未覆盖
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEMO_CAP = "cp.builtin.read_file"
CHAIN_STEPS = ("cp.builtin.shell_execute", "cp.builtin.write_file")
BASE_TS = 1_760_000_000.0
ARTIFACT_DIR = os.path.join("data", "digestion", "demo_s3_02")
#: 演示用的参数级破坏性/失败倾向分支（S3-01 遗留 #4 的扩展点）：
#: 台账里被读取的路径位于 ``tests/`` 下 ⇒ 该条件被轨迹派生用例真实覆盖。
BRANCH_COVERED = "path contains test"
#: 负样本用：无人覆盖的参数级分支（条件 4 应因此拒绝发证）
BRANCH_UNCOVERED = "cmd contains force-recreate"


def _hr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def _step(n: int, text: str) -> None:
    print(f"\n[步骤 {n}] {text}")


def build_demo_ledger(facade, tasks: int, fail_every: int):
    """合成统一台账：任务级 Trace + 能力级子 Trace（含噪声、失败轨迹与真实副作用）

    与 S3-01 演示的差异（本任务需要）：**记录真实 side_effects**
    （``files_written`` / ``external_calls``）—— 判定集的"副作用集合"契约必须有
    真实证据来源，否则用例只能主张空集合（`side_effects_source="none"`）。
    """
    from agent.observability.trace_v2 import SideEffects

    fail_ids = []
    for i in range(tasks):
        cursor = BASE_TS + i * 10.0
        task_id = f"s302-task{i:04d}"
        facade.start(task_id=task_id, workspace_id="ws_s3_02_demo")
        # 噪声 1：探索前缀
        facade.record("list_dir", args={"path": f"C:/repo/proj{i:03d}"},
                      output={"ok": True}, started_at=cursor, duration_ms=4.0)
        cursor += 1.0
        args = {"path": f"C:/repo/proj{i:03d}/tests/test_mod{i:03d}.py",
                "encoding": "utf-8"}
        facade.record(DEMO_CAP, args=dict(args),
                      output={"ok": True, "size": 100 + i, "lines": 3},
                      started_at=cursor, duration_ms=3.0)
        cursor += 1.0
        # 噪声 2：相邻重复（重试）
        facade.record(DEMO_CAP, args=dict(args),
                      output={"ok": True, "size": 100 + i, "lines": 3},
                      started_at=cursor, duration_ms=3.0)
        cursor += 1.0
        failing = bool(fail_every) and i % fail_every == fail_every - 1
        report_path = f"C:/repo/proj{i:03d}/out/report.md"
        for j, label in enumerate(CHAIN_STEPS):
            if failing and j == len(CHAIN_STEPS) - 1:
                continue      # 失败轨迹缺末步 ⇒ 成功/失败骨架有差异（决策树可提取分支）
            if label == "cp.builtin.shell_execute":
                effects = SideEffects(external_calls=["shell_execute"])
            else:
                effects = SideEffects(files_written=[report_path])
            facade.record(label, args={
                "cmd": f"python -m pytest tests/test_mod{i:03d}.py -q",
                "path": report_path, "content": f"report {i:03d}"},
                output={"ok": True}, started_at=cursor, duration_ms=2.0,
                side_effects=effects)
            cursor += 1.0
        facade.finish(status="error" if failing else "success")
        if failing:
            fail_ids.append(task_id)
    return {"tasks": tasks, "fail_task_ids": fail_ids,
            "success_tasks": tasks - len(fail_ids)}


def main() -> int:
    parser = argparse.ArgumentParser(description="TASK-S3-02 判定集与回放沙箱演示")
    parser.add_argument("--tasks", type=int, default=40,
                        help="同类轨迹条数（默认 40 ⇒ 36 条成功轨迹 ⇒ 判定集 ≥30 组）")
    parser.add_argument("--fail-every", type=int, default=10,
                        help="每 N 条注入 1 条失败轨迹（0=全成功）")
    parser.add_argument("--mutate", action="store_true",
                        help="负样本：候选骨架缺末步（门应拒绝）")
    parser.add_argument("--uncovered-branch", action="store_true",
                        help="负样本：注入未被任何用例覆盖的参数级分支（条件 4 应拒绝）")
    parser.add_argument("--archive", action="store_true", default=True,
                        help="归档产物到 data/digestion/demo_s3_02/")
    parser.add_argument("--no-archive", dest="archive", action="store_false")
    args = parser.parse_args()

    tmp = tempfile.mkdtemp(prefix="s3_02_demo_")
    os.environ["CP_EVENTS_DIR"] = os.path.join(tmp, "events")
    os.environ["CP_DIGESTION_CASE_DIR"] = os.path.join(tmp, "cases")
    os.environ.pop("CP_DIGESTION_SANDBOX_DENY_EXTERNAL", None)   # 外部调用默认桩化执行

    from agent.audit import facade as facade_mod
    from agent.audit.chain import AuditChain
    from agent.descriptors.bridge import register_bridge_view
    from agent.descriptors.registry import DescriptorRegistry
    from agent.digestion import DigestionService
    from agent.digestion.cases import (
        build_case_set, cases_from_trace_set, concrete_args_provider,
        merge_cases, open_case_store, seed_candidate_for, seed_cases_for,
        seed_pack_case_sets, seed_pack_summary, trace_set_for,
    )
    from agent.digestion.gate import (
        PassportStore, acceptance_gate, advance_to_shadow, branch_coverage,
        reprobe, DRIFT_TRIGGER_UPSTREAM,
    )
    from agent.digestion.models import BranchCondition
    from agent.digestion.sandbox import (
        PatternImplementation, RecordReplayJournal, ReplaySandbox,
    )
    from agent.observability.trace_v2 import TraceFacade

    # ── 隔离 ──
    chain = AuditChain(os.path.join(tmp, "audit.db"),
                       roots_path=os.path.join(tmp, "roots.jsonl"),
                       signing_key_path=os.path.join(tmp, "k.pem"),
                       auto_seal=False)
    facade_mod.audit.bind(chain)
    facade_mod.audit.enabled = True

    trace_facade = TraceFacade(os.path.join(tmp, "trace.db"))
    TraceFacade._instance = trace_facade

    reg = DescriptorRegistry(path=os.path.join(tmp, "descriptors.json"),
                            autosave=False)
    register_bridge_view(reg, "builtin", [
        {"name": "read_file", "description": "读取文件内容"},
        {"name": "shell_execute", "description": "执行命令"},
        {"name": "write_file", "description": "写入文件"}])

    case_store = open_case_store()
    passport_store = PassportStore()
    journal = RecordReplayJournal(os.path.join(tmp, "journal"))
    box = ReplaySandbox()
    artifacts = {"isolated_dir": tmp, "capability_id": DEMO_CAP,
                 "mutate": bool(args.mutate),
                 "uncovered_branch": bool(args.uncovered_branch)}

    _hr("TASK-S3-02 判定集资产 + 回放沙箱 + 验收门 端到端演示")
    print(f"隔离目录: {tmp}")
    print(f"判定集存储: {case_store.root}（后端 {case_store.backend}）")
    print(f"统一台账存储: {os.path.join(tmp, 'trace.db')}"
          f" —— 与判定集**不同文件**（Trace 90 天过期不影响判定集）")

    # ── 步骤 1：合成轨迹台账 ──
    _step(1, "合成统一轨迹台账（含噪声 + 失败轨迹 + 真实副作用记录）")
    info = build_demo_ledger(trace_facade, args.tasks, args.fail_every)
    trace_facade.flush()
    print(f"  任务数={info['tasks']} 成功={info['success_tasks']} "
          f"失败={len(info['fail_task_ids'])}")
    print(f"  台账行数={len(trace_facade.query())}")

    # ── 步骤 2：消化流水线（S3-01）→ 候选模式 + 草稿 + 入轨 ──
    _step(2, "消化流水线：清洗 → 模式挖掘 → draft SKILL.md → stage 入轨")
    svc = DigestionService(store=trace_facade._store, registry=reg,
                           draft_dir=os.path.join(tmp, "drafts"),
                           persist_drafts=True)
    first = svc.pipeline(capability_id=DEMO_CAP)
    first_migration = first.stage_migration
    assert first_migration is not None
    print(f"  首次入轨: {first_migration.from_stage} → "
          f"{first_migration.to_stage} applied={first_migration.applied}")
    report = svc.pipeline(capability_id=DEMO_CAP)
    pattern = report.candidates[0] if report.candidates else None
    if pattern is None:
        print("  ✗ 未挖出候选模式，演示终止")
        return 2
    print(f"  候选模式: {pattern.pattern_id} 步骤={[s.label for s in pattern.steps]}")
    print(f"  支撑={pattern.support}/{pattern.sample_size} 覆盖率={pattern.coverage:.2f} "
          f"置信度={pattern.confidence:.2f}")
    print(f"  参数槽: {[s.placeholder for s in pattern.slots]}")
    print(f"  分支条件: {[b.condition for b in pattern.branches]}")
    print(f"  副作用画像: {pattern.side_effect_profile.get('files_written_shape')} / "
          f"{pattern.side_effect_profile.get('external_calls_shape')}")
    print(f"  draft: {[(d.skill_id, d.status) for d in report.drafts]}")
    print(f"  stage: {report.stage_recommendation.get('from')} → "
          f"{report.stage_recommendation.get('to')} "
          f"（{report.stage_migration.verdict if report.stage_migration else '-'}）")
    artifacts["pattern"] = {
        "pattern_id": pattern.pattern_id,
        "steps": [s.label for s in pattern.steps],
        "support": pattern.support, "sample_size": pattern.sample_size,
        "coverage": pattern.coverage, "confidence": pattern.confidence,
        "slots": [s.placeholder for s in pattern.slots],
        "branches": [b.condition for b in pattern.branches],
        "drafts": [d.skill_id for d in report.drafts],
    }

    # ── 步骤 3：判定集（Seed Pack + Trace 自动生成）──
    _step(3, "判定集：Seed Pack（P7.2-23，独立回放验证）+ 从同类轨迹自动生成")
    sp = seed_pack_summary()
    print(f"  Seed Pack: {sp['skills']} 技能 × ≥{sp['min_cases_per_skill']} 组 "
          f"= {sp['cases']} 组；达标 P7.2-23={sp['meets_p7_2_23']}")
    seed_fail = []
    for seed_set in seed_pack_case_sets():
        for case in seed_set.cases:
            if not box.replay_case(case, seed_candidate_for(case)).passed:
                seed_fail.append(case.case_id)
    print(f"  Seed Pack 可回放性：{sp['cases'] - len(seed_fail)}/{sp['cases']} 全部通过"
          f"（失败 {seed_fail}）")
    artifacts["seed_pack"] = {"summary": sp, "replay_failures": seed_fail}

    # 判定集与候选必须描述**同一条任务流**：Seed Pack 的用例是各能力**自身契约**
    # 的起步集（如 read_file 的单次读取），而本演示评估的候选是从同类轨迹挖出的
    # **三段任务链**骨架 —— 两者形状不同 ⇒ 保留在判定集内但**本次不纳入评估**
    # （显式标注，不做静默过滤）。
    seed_cases = seed_cases_for(DEMO_CAP)
    for case in seed_cases:
        case.active = False
        case.notes = ("本次被评候选为轨迹挖出的三段任务链骨架；本用例描述的是"
                      "read_file 单次读取契约 ⇒ 形状不匹配，本次不纳入评估"
                      "（用例保留在判定集内供其自身候选模板回放）")
    print(f"  本能力 Seed 用例 {len(seed_cases)} 组：形状与本候选任务链不同 ⇒ "
          f"标记为不参与本次评估（用例仍保留在资产内）")

    concrete = concrete_args_provider(svc.store, DEMO_CAP, registry=reg)
    trace_set = trace_set_for(DEMO_CAP, service=svc)
    assert trace_set is not None, "演示前置：同类轨迹集为空"
    trace_cases = cases_from_trace_set(trace_set, capability_id=DEMO_CAP,
                                       concrete_args=concrete)
    all_cases = merge_cases(seed_cases, trace_cases)
    case_set = build_case_set(DEMO_CAP, all_cases, version=1,
                              upstream_version="1.0.0")
    case_store.save(case_set)
    verdict = case_set.size_verdict()
    print(f"  判定集共 {verdict['total']} 组（生效 {verdict['active']} 组，"
          f"§3.1 区间 {verdict['min']}–{verdict['max']}，complete={verdict['complete']}）")
    print(f"  来源分布: {verdict['kinds']}")
    print(f"  判定集文件: {case_store.root}\\{DEMO_CAP}.json")
    print(f"  origin_trace_id 示例: {[c.origin_trace_id for c in trace_cases[:2]]}")
    print(f"  副作用证据来源分布: "
          f"{sorted({c.side_effects_source for c in trace_cases})}")
    print(f"  复制台账原始参数（§3.1 复制数据）："
          f"{trace_cases[0].provenance.get('concrete_args')}；"
          f"示例输入={trace_cases[0].input}")
    artifacts["case_set"] = {"version": case_set.version,
                             "size_verdict": verdict,
                             "case_set_file": os.path.join(
                                 case_store.root, f"{DEMO_CAP}.json"),
                             "sample_input": trace_cases[0].input,
                             "sample_origin_trace_id": trace_cases[0].origin_trace_id}

    # 独立存储验证：判定集与统一台账是**不同文件**，台账过期/删除不影响判定集
    print("\n  [独立存储验证] 判定集存储与统一台账分离：")
    reloaded = case_store.load(DEMO_CAP)
    assert reloaded is not None, "演示前置：判定集写入后应立即可读"
    trace_db = os.path.join(tmp, "trace.db")
    print(f"    台账文件={trace_db}｜判定集文件="
          f"{os.path.join(case_store.root, DEMO_CAP + '.json')}")
    print(f"    同一目录？{os.path.dirname(trace_db) == case_store.root}")
    print(f"    重载判定集={reloaded is not None} 用例数={len(reloaded.cases)} "
          f"（生效 {len(reloaded.active_cases())}）")

    # ── 步骤 4：回放沙箱 ──
    _step(4, "回放沙箱：上游 vs 候选双跑 + 三层比对 + 确定性自检 + journal")
    head = reloaded.cases[0]
    probe = box.determinism_probe(head, head.upstream)
    print(f"  确定性自检（两次回放逐字段一致）: {probe['deterministic']} "
          f"fingerprint={probe['fingerprint_first']}")
    replay_head = box.replay_case(head, head.upstream)
    for layer in replay_head.diff.layers:
        print(f"    层[{layer.layer}] kind={layer.kind} passed={layer.passed} "
              f"score={layer.score}")
    print(f"  副作用只记录不双写：env.commit() → "
          f"{_commit_probe(head)}")
    journal.record(replay_head, implementation="candidate")
    print(f"  record-and-replay 校验: {journal.verify(replay_head)}")
    artifacts["sandbox"] = {"deterministic": probe["deterministic"],
                            "layers": [l.to_dict() for l in replay_head.diff.layers]}

    # ── 步骤 5：验收门四条件 ──
    _step(5, "验收门四条件（§4.5）：≥20 条回放全过 / 成功率≥基线×0.98 / p99≤上游 / 覆盖破坏性分支")
    candidate = (PatternImplementation(pattern, drop_steps=(len(pattern.steps) - 1,))
                 if args.mutate else PatternImplementation(pattern))
    if args.mutate:
        print("  [--mutate] 候选骨架被刻意去掉末步（模拟提取错误）")
    extra = [BranchCondition(at_step=1,
                             condition=(BRANCH_UNCOVERED if args.uncovered_branch
                                        else BRANCH_COVERED),
                             support=4, total=info["tasks"], outcome="failure",
                             advice="读取测试文件的历史失败率更高（参数级条件，"
                                    "S3-01 遗留 #4 扩展点）")]
    if args.uncovered_branch:
        print(f"  [--uncovered-branch] 注入无人覆盖的参数级分支 "
              f"`{BRANCH_UNCOVERED}`（条件 4 应拒绝）")
    coverage = branch_coverage(pattern, reloaded.active_cases(),
                              extra_conditions=extra)
    print(f"  分支要求: 必须覆盖={coverage['required']}（已覆盖 {coverage['covered']}）"
          f"｜观察项={coverage['observed_count']}"
          f"（{ [o['condition'] for o in coverage['observed']] }）")
    gate = acceptance_gate(DEMO_CAP, case_set=reloaded, store=case_store,
                           candidate=candidate, sandbox=box, pattern=pattern,
                           extra_conditions=extra, passport_store=passport_store,
                           baseline_store=svc.store, advance=True, registry=reg)
    for cond in gate.conditions:
        print(f"    {cond.name:<28} passed={cond.passed} "
              f"actual={cond.actual if not isinstance(cond.actual, dict) else '…'} "
              f"threshold={cond.threshold if not isinstance(cond.threshold, dict) else '…'}")
        for why in cond.reasons:
            print(f"        ↳ {why}")
    print(f"  门判决: passed={gate.passed} 失败条件={gate.failed_conditions}")
    replay_report = gate.replay_report
    assert replay_report is not None
    print(f"  回放: {replay_report.passed}/{replay_report.total} 通过；"
          f"p99 候选={replay_report.p99_candidate_ms()}ms "
          f"上游={replay_report.p99_upstream_ms()}ms")
    print(f"  人工抽检清单（10%）：{replay_report.manual_sample}")
    if gate.failure_list():
        print("  失败清单：")
        for item in gate.failure_list()[:3]:
            print(f"    - {item['case_id']} 失败层={item['failed_layers']} "
                  f"{item['reasons'][:1]}")
    if gate.passport:
        print(f"  通行证: {gate.passport['passport_id']} "
              f"（executed={gate.passport['executed']}/"
              f"required={gate.passport['required_replays']}）")
        print(f"  事件 id={gate.event_id}｜审计 seq={gate.audit_seq} "
              f"hash={gate.audit_hash[:16]}…")
    artifacts["gate"] = gate.to_dict()
    gate_payload = artifacts["gate"]
    if isinstance(gate_payload, dict):
        gate_payload.pop("replay", None)

    # ── 步骤 6：凭通行证推进 mirrored → shadow ──
    _step(6, "凭通行证推进 stage（经既有 stage_migrate，三处联动）")
    if gate.passed:
        mig = gate.migration or advance_to_shadow(DEMO_CAP, gate, registry=reg)
        print(f"  迁移: {mig.from_stage} → {mig.to_stage} applied={mig.applied} "
              f"verdict={mig.verdict}")
        print(f"  reasons={mig.reasons}")
        print(f"  审计: action={mig.audit_action} seq={mig.audit_seq} "
              f"reason={mig.audit_reason[:70]}…")
        descriptor = reg.get(DEMO_CAP)
        assert descriptor is not None
        current = descriptor.evolution.stage
        print(f"  台账 stage={getattr(current, 'value', current)}")
    else:
        print("  门未通过 ⇒ 不推进 stage（保持 mirrored；不静默）")
        from agent.digestion.stage import evaluate_migration
        verdict_, reasons_ = evaluate_migration(
            capability_id=DEMO_CAP, from_stage="mirrored", to_stage="shadow",
            evidence={"acceptance_passport": gate.passport})
        print(f"  无有效通行证时 stage 门裁决: {verdict_}｜{reasons_[:1]}")

    # ── 步骤 7：漂移重探 ──
    _step(7, "漂移重探（§4.5）：上游版本变化 → 简化探针 → drifted → 失效 → 重生成")
    drift = reprobe(DEMO_CAP, trigger=DRIFT_TRIGGER_UPSTREAM, store=case_store,
                    candidate=candidate, sandbox=box, trace_set=trace_set,
                    concrete_args=concrete)
    print(f"  trigger={drift.trigger} verdict={drift.verdict} "
          f"drifted={drift.drifted} schema_changed={drift.schema_changed}")
    print(f"  探针用例: {drift.probes}")
    print(f"  理由: {drift.reasons}")
    print(f"  失效版本={drift.invalidated_version} → 重生成版本="
          f"{drift.regenerated_version}（{drift.regenerated_cases} 组）")
    print(f"  版本历史: {case_store.history(DEMO_CAP)}")
    regenerated = case_store.load(DEMO_CAP)
    assert regenerated is not None
    print(f"  重生成后生效用例={len(regenerated.active_cases())} 组")
    print(f"  事件 id={drift.event_id}｜审计 seq={drift.audit_seq}")
    # 重生成会以"全新用例"重建 ⇒ 适用性标注（哪些用例属本候选的流）需重新施加；
    # 当前用 case.active 表达，S3-03 应引入显式的 case↔candidate 适用性字段。
    marked = 0
    for case in regenerated.cases:
        if case.kind == "seed" and case.active:
            case.active = False
            case.notes = ("本次被评候选为轨迹挖出的三段任务链骨架；本用例描述的是"
                          "read_file 单次读取契约 ⇒ 形状不匹配，本次不纳入评估")
            marked += 1
    if marked:
        case_store.save(regenerated)
        print(f"  适用性标注重施加：{marked} 组 Seed 用例不参与本候选评估"
              f"（生效 {len(regenerated.active_cases())} 组）")
    artifacts["drift"] = drift.to_dict()

    # ── 步骤 8：归档 ──
    _step(8, "归档演示产物")
    if args.archive:
        os.makedirs(ARTIFACT_DIR, exist_ok=True)
        out = os.path.join(ARTIFACT_DIR, "s3_02_demo_report.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(artifacts, fh, ensure_ascii=False, indent=1, default=str)
        print(f"  已归档: {out}")
    else:
        print("  （--no-archive：跳过归档）")

    _hr("演示结束")
    final_descriptor = reg.get(DEMO_CAP)
    final_case_set = case_store.load(DEMO_CAP)
    assert final_descriptor is not None and final_case_set is not None
    print(f"门判决={gate.passed}｜stage={final_descriptor.evolution.stage}｜"
          f"判定集版本={final_case_set.version}")
    if args.mutate or args.uncovered_branch:
        return 0 if not gate.passed else 1     # 负样本：门**必须**拒绝
    return 0 if gate.passed else 1


def _commit_probe(case) -> str:
    """副作用只记录不双写：`ReplayEnv.commit()` 恒抛（此处只回显结论）"""
    from agent.digestion.sandbox import ReplayEnv, SandboxError
    env = ReplayEnv(root=case.sandbox_root)
    try:
        env.commit()
    except SandboxError as e:
        return f"被拒绝（{str(e)[:24]}…）"
    return "未拒绝（异常！）"


if __name__ == "__main__":
    raise SystemExit(main())
