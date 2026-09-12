"""TASK-S3-03 端到端演示：shadow 灰度 → 六条件 → promote PR → 低流量手动通道

覆盖验收清单里"**完整内化决策样例可复现**"与"低流量手动通道、⑤⑥ 一票否决"。
**全部隔离**：统一台账 / descriptor 台账 / 审计链 / 事件 / 判定集 / 通行证 / 灰度台账
/ 审批记录全部落在临时目录；仅演示产物归档到 ``data/digestion/demo_s3_03/``
（人工抽检清单另存 ``data/digestion/shadow/manual_reviews.jsonl`` 供 Owner 裁定）。

演示路径（**真实代码路径，非桩**）：

1. 合成同类轨迹台账 → `DigestionService.pipeline()` → 候选模式 + stage 入轨
2. 判定集（Seed Pack + Trace 自动生成）+ **M4 显式适用性字段**（取代 S3-02 的
   `active` + `notes` 临时表达）
3. 验收门 → 通行证 → 凭通行证 ``mirrored → shadow``（S3-02 通道）
4. **shadow 灰度**：日预算 ``min(日均×15%, 50)`` + trace_id 哈希确定性抽样 +
   灰度策略（默认关闭/显式阈值）+ judge_kind 如实标注 + **真实墙钟 p99** +
   10% 人工抽检 + 劣化（R4）信号
5. **内化六条件**（§4.5.1）：逐项打分 → ①-④ 排序 / ⑤⑥ 一票否决 → ROI 报告
6. **stage.promote PR 产物**（本地可审阅：补丁 + 描述 + ROI + 合入说明；
   **不推送远端、不自动合并**）
7. **低流量手动 promote 通道**（T2）：approval 留痕 → 人工批准 → `stage_migrate`
   ``shadow → internalized``
8. 负样本开关：``--veto-p99``（性能倒退 ⇒ ⑤ 一票否决）、``--no-privacy``
   （secret + 出域端点 ⇒ ⑥ 一票否决）、``--low-traffic``（月样本 30 ⇒ 手动通道）

用法::

    python scripts/demo_s3_03_internalize.py                    # 完整演示
    python scripts/demo_s3_03_internalize.py --veto-p99          # 负样本：⑤ 否决
    python scripts/demo_s3_03_internalize.py --no-privacy        # 负样本：⑥ 否决
    python scripts/demo_s3_03_internalize.py --low-traffic       # 低流量手动通道
    python scripts/demo_s3_03_internalize.py --review-sheet      # 只打印人工抽检清单
    python scripts/demo_s3_03_internalize.py --record-review \
        --case-id <case_id> --verdict pass --reviewer <name>     # 记录人工裁定（M5）

**口径声明**：演示的样本量来自**离线设施**（合成轨迹 + 判定集回放），真实流量尚未
达到"每能力 ≥20 条同类轨迹"；**不得**据此声称"已实现真实能力内化"。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEMO_CAP = "cp.builtin.read_file"
CHAIN_STEPS = ("cp.builtin.shell_execute", "cp.builtin.write_file")
BASE_TS = 1_760_000_000.0
ARTIFACT_DIR = os.path.join("data", "digestion", "demo_s3_03")
SHADOW_RUNTIME_DIR = os.path.join("data", "digestion", "shadow")
BRANCH_COVERED = "path contains test"


def _hr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def _step(n: int, text: str) -> None:
    print(f"\n[步骤 {n}] {text}")


def build_demo_ledger(facade, tasks: int, fail_every: int) -> dict:
    """合成统一台账（与 S3-02 演示同款：噪声 + 失败轨迹 + **真实副作用记录**）"""
    from agent.observability.trace_v2 import SideEffects

    fail_ids = []
    for i in range(tasks):
        cursor = BASE_TS + i * 10.0
        task_id = f"s303-task{i:04d}"
        facade.start(task_id=task_id, workspace_id="ws_s3_03_demo")
        facade.record("list_dir", args={"path": f"C:/repo/proj{i:03d}"},
                      output={"ok": True}, started_at=cursor, duration_ms=4.0)
        cursor += 1.0
        args = {"path": f"C:/repo/proj{i:03d}/tests/test_mod{i:03d}.py",
                "encoding": "utf-8"}
        facade.record(DEMO_CAP, args=dict(args),
                      output={"ok": True, "size": 100 + i, "lines": 3},
                      started_at=cursor, duration_ms=3.0)
        cursor += 1.0
        facade.record(DEMO_CAP, args=dict(args),
                      output={"ok": True, "size": 100 + i, "lines": 3},
                      started_at=cursor, duration_ms=3.0)
        cursor += 1.0
        failing = bool(fail_every) and i % fail_every == fail_every - 1
        report_path = f"C:/repo/proj{i:03d}/out/report.md"
        for j, label in enumerate(CHAIN_STEPS):
            if failing and j == len(CHAIN_STEPS) - 1:
                continue
            effects = (SideEffects(external_calls=["shell_execute"])
                       if label == "cp.builtin.shell_execute"
                       else SideEffects(files_written=[report_path]))
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


from agent.digestion.sandbox import Implementation  # noqa: E402  （演示用慢候选基类）


class SlowCandidate(Implementation):
    """负样本用的"行为正确但更慢"的候选（**真实墙钟**回归，非伪造数字）

    直接子类化 `sandbox.Implementation`，包住真实候选实现并在其后 sleep ——
    故条件⑤ 拿到的是**真实测量**出来的性能倒退，不是编造的证据。
    """

    name = "candidate_slow"

    def __init__(self, inner, delay_ms: float = 6.0) -> None:
        self.inner = inner
        self.delay_ms = float(delay_ms)

    def steps_for(self, case):
        return self.inner.steps_for(case)

    def missing_inputs(self, case):
        return self.inner.missing_inputs(case)

    def run(self, case, *, quota=None, tools=None):
        obs = self.inner.run(case, quota=quota, tools=tools)
        time.sleep(self.delay_ms / 1000.0)
        return obs


def main() -> int:
    parser = argparse.ArgumentParser(description="TASK-S3-03 shadow 灰度与内化引擎演示")
    parser.add_argument("--tasks", type=int, default=40, help="同类轨迹条数")
    parser.add_argument("--fail-every", type=int, default=10, help="每 N 条注入 1 条失败轨迹")
    parser.add_argument("--digest-runs", type=int, default=3,
                        help="额外跑几次消化流水线以累积 digest_count（审计通道实测）")
    parser.add_argument("--veto-p99", action="store_true",
                        help="负样本：候选更慢 ⇒ 条件⑤ 一票否决")
    parser.add_argument("--no-privacy", action="store_true",
                        help="负样本：secret + 出域端点 ⇒ 条件⑥ 一票否决")
    parser.add_argument("--low-traffic", action="store_true",
                        help="低流量：月样本 30（T2 手动通道）")
    parser.add_argument("--archive", action="store_true", default=True)
    parser.add_argument("--no-archive", dest="archive", action="store_false")
    parser.add_argument("--review-sheet", action="store_true",
                        help="只打印人工抽检清单（不跑演示）")
    parser.add_argument("--record-review", action="store_true",
                        help="记录一条人工裁定（M5 留痕）")
    parser.add_argument("--case-id", default="", help="人工裁定：用例 id")
    # TASK-S8-05 修正【上游已知坑 #4】：`--verdict` 曾有 default="pass" —— 复核人漏传
    # 参数时会**静默签 pass**（虚假验收，违反 M5 与"不谎报"纪律）。现改为**必须显式传值**：
    # 缺省即报错退出，且取值校验复用 `shadow.REVIEW_VERDICTS`（单一词表，不复制）。
    parser.add_argument("--verdict", default=None,
                        help="人工裁定：**必填**，取值 pass/fail/uncertain")
    parser.add_argument("--reviewer", default="", help="人工裁定：复核人")
    parser.add_argument("--review-note", default="", help="人工裁定：备注")
    args = parser.parse_args()

    # ── 人工裁定子命令（Owner 用；写到运行时台账，不依赖本次演示的临时目录） ──
    if args.review_sheet or args.record_review:
        from agent.digestion.shadow import (
            REVIEW_ROLE_HUMAN,
            REVIEW_VERDICTS,
            ManualReviewQueue,
        )
        queue = ManualReviewQueue(directory=SHADOW_RUNTIME_DIR)
        if args.review_sheet:
            print(queue.review_sheet(DEMO_CAP))
            print(json.dumps(queue.summary(DEMO_CAP), ensure_ascii=False, indent=1))
            return 0
        if not (args.case_id and args.reviewer):
            print("--record-review 需要 --case-id 与 --reviewer")
            return 2
        if not args.verdict:
            print("--record-review 需要显式 --verdict（" + "/".join(REVIEW_VERDICTS)
                  + "）—— 不提供默认值，避免误签 pass")
            return 2
        if str(args.verdict).strip().lower() not in REVIEW_VERDICTS:
            print(f"非法 --verdict {args.verdict!r}（允许 {REVIEW_VERDICTS}）")
            return 2
        item = queue.record_review(args.case_id, capability_id=DEMO_CAP,
                                   verdict=args.verdict, reviewer=args.reviewer,
                                   role=REVIEW_ROLE_HUMAN, note=args.review_note)
        print(json.dumps(item.to_dict(), ensure_ascii=False, indent=1))
        print(json.dumps(queue.summary(DEMO_CAP), ensure_ascii=False, indent=1))
        return 0

    tmp = tempfile.mkdtemp(prefix="s3_03_demo_")
    os.environ["CP_EVENTS_DIR"] = os.path.join(tmp, "events")
    os.environ["CP_DIGESTION_CASE_DIR"] = os.path.join(tmp, "cases")
    os.environ.pop("CP_DIGESTION_SANDBOX_DENY_EXTERNAL", None)

    from agent.audit import facade as facade_mod
    from agent.audit.chain import AuditChain
    from agent.descriptors.bridge import register_bridge_view
    from agent.descriptors.registry import DescriptorRegistry
    from agent.digestion import cases as C
    from agent.digestion import gate as G
    from agent.digestion import internalize as I
    from agent.digestion import shadow as SH
    from agent.digestion.models import BranchCondition
    from agent.digestion.sandbox import Implementation, PatternImplementation, ReplaySandbox
    from agent.digestion.service import DigestionService
    from agent.observability.trace_v2 import TraceFacade

    # ── 隔离 ──
    chain = AuditChain(os.path.join(tmp, "audit.db"),
                       roots_path=os.path.join(tmp, "roots.jsonl"),
                       signing_key_path=os.path.join(tmp, "k.pem"), auto_seal=False)
    facade_mod.audit.bind(chain)
    facade_mod.audit.enabled = True
    trace_facade = TraceFacade(os.path.join(tmp, "trace.db"))
    TraceFacade._instance = trace_facade
    reg = DescriptorRegistry(path=os.path.join(tmp, "descriptors.json"), autosave=False)
    register_bridge_view(reg, "builtin", [
        {"name": "read_file", "description": "读取文件内容"},
        {"name": "shell_execute", "description": "执行命令"},
        {"name": "write_file", "description": "写入文件"}])
    case_store = C.open_case_store()
    passport_store = G.PassportStore()
    review_queue = SH.ManualReviewQueue(os.path.join(tmp, "reviews.jsonl"))
    shadow_ledger = SH.ShadowLedger(os.path.join(tmp, "shadow_ledger.jsonl"))
    artifacts: dict = {"isolated_dir": tmp, "capability_id": DEMO_CAP}

    _hr("TASK-S3-03 shadow 灰度 + 内化六条件引擎 端到端演示")
    print(f"隔离目录: {tmp}")
    print(f"判定集存储: {case_store.root}")
    print("口径声明：样本量来自**离线设施**（合成轨迹 + 判定集回放）；真实流量尚未达标，"
          "本演示**不**声称已实现真实能力内化")

    # ── 步骤 1：轨迹台账 + 消化流水线 ──
    _step(1, "合成同类轨迹台账 → 消化流水线（清洗/挖掘/草稿/入轨）")
    info = build_demo_ledger(trace_facade, args.tasks, args.fail_every)
    trace_facade.flush()
    print(f"  任务数={info['tasks']} 成功={info['success_tasks']} "
          f"失败={len(info['fail_task_ids'])}")
    # S2-03 成本归一数据：为上游任务如实记账（内化条件③ ROI 的数据源）
    from agent.observability.acr import record_task_closed
    from agent.observability.utc import record_cost, utc_window
    for i in range(info["tasks"]):
        record_cost(model="gpt-4o-mini", source="demo_upstream", task_id=f"s303-task{i:04d}",
                    tokens_in=1200, tokens_out=400, interaction_id=f"s303-llm-{i:04d}")
        record_task_closed(task_id=f"s303-task{i:04d}",
                           status=("failed" if f"s303-task{i:04d}" in info["fail_task_ids"]
                                   else "closed"), intent="fix")
    _end = time.strftime("%Y-%m-%d", time.localtime())
    _start = time.strftime("%Y-%m-%d", time.localtime(time.time() - 30 * 86400))
    _utc = utc_window(start=_start, end=_end)
    print(f"  S2-03 成本归一（UTC）：{_utc.get('utc_cents_per_task')} 分/任务；"
          f"归一成本合计 {_utc.get('cost_normalized_cents')} 分"
          f"（{_utc.get('tasks', {}).get('closed_and_failed')} 任务）"
          "—— 条件③ ROI 的数据源")
    svc = DigestionService(store=trace_facade._store, registry=reg,
                           draft_dir=os.path.join(tmp, "drafts"), persist_drafts=True)
    report = svc.pipeline(capability_id=DEMO_CAP)
    for _ in range(max(0, int(args.digest_runs))):
        report = svc.pipeline(capability_id=DEMO_CAP)
    pattern = report.candidates[0] if report.candidates else None
    if pattern is None:
        print("  ✗ 未挖出候选模式，演示终止")
        return 2
    print(f"  候选模式 {pattern.pattern_id}：{ [s.label for s in pattern.steps] }")
    print(f"  stage: {report.stage_recommendation.get('from')} → "
          f"{report.stage_recommendation.get('to')}"
          f"（{report.stage_migration.verdict if report.stage_migration else '-'}）")
    artifacts["pattern"] = {"pattern_id": pattern.pattern_id,
                            "steps": [s.label for s in pattern.steps]}

    # ── 步骤 2：判定集 + M4 显式适用性 ──
    _step(2, "判定集（Seed Pack + Trace）+ **M4 显式适用性字段**")
    seed_cases = C.seed_cases_for(DEMO_CAP)
    concrete = C.concrete_args_provider(svc.store, DEMO_CAP, registry=reg)
    trace_set = C.trace_set_for(DEMO_CAP, service=svc)
    assert trace_set is not None, "演示前置：同类轨迹集为空"
    trace_cases = C.cases_from_trace_set(trace_set, capability_id=DEMO_CAP,
                                         concrete_args=concrete)
    all_cases = C.merge_cases(seed_cases, trace_cases)
    # M4：把"Seed 用例描述的是 read_file 单次读取契约、本候选是三段任务链骨架"
    # 这件事**显式声明**为适用性约束（S3-02 只能用 active=False + notes 表达）
    for case in seed_cases:
        case.applicability = C.CaseApplicability(
            exclude_kinds=[C.CANDIDATE_KIND_PATTERN],
            reason=("该用例描述 read_file 单次读取契约，与被评三段任务链候选形状不同"
                    "（M4 显式字段，取代 S3-02 的 active+notes）"),
            declared_by="demo_s3_03")
    case_set = C.build_case_set(DEMO_CAP, all_cases, version=1, upstream_version="1.0.0")
    case_store.save(case_set)
    applicable, excluded = C.applicable_cases(case_set.active_cases(),
                                              C.CANDIDATE_KIND_PATTERN)
    print(f"  判定集 {len(case_set.cases)} 组（Seed {len(seed_cases)} + "
          f"Trace {len(trace_cases)}）；候选身份=candidate_pattern")
    print(f"  **适用性过滤**：适用 {len(applicable)} 组，排除 {len(excluded)} 组")
    for row in excluded[:3]:
        print(f"    - {row['case_id']}：{row['reason']}")
    # 判定集重生成后可**机器重新施加**（S3-02 遗留 M4 的另一半诉求）
    reapply = C.apply_applicability(case_set, rules=[{
        "match": {"case_ids": [c.case_id for c in seed_cases]},
        "exclude_kinds": [C.CANDIDATE_KIND_PATTERN],
        "reason": "重生成后重新施加：Seed 用例属 read_file 自身契约，非本候选任务链",
    }], declared_by="demo_s3_03_reapply", reset_first=True)
    case_store.save(case_set)
    print(f"  适用性**重新施加**（判定集重生成后的机器通道）：命中 "
          f"{sum(len(a['matched']) for a in reapply['applied'])} 组")
    artifacts["applicability"] = {"applicable": len(applicable),
                                  "excluded": len(excluded),
                                  "excluded_cases": excluded,
                                  "reapply": reapply}

    # ── 步骤 3：验收门 → 通行证 → mirrored→shadow ──
    _step(3, "验收门四条件 → 通行证 → 凭通行证 mirrored → shadow（S3-02 通道）")
    box = ReplaySandbox()
    candidate = PatternImplementation(pattern)
    slow = (SlowCandidate(candidate, delay_ms=6.0) if args.veto_p99 else None)
    graded = slow or candidate
    extra = [BranchCondition(at_step=1, condition=BRANCH_COVERED, support=4,
                             total=info["tasks"], outcome="failure",
                             advice="读取测试文件的历史失败率更高")]
    gate = G.acceptance_gate(DEMO_CAP, case_set=case_set, store=case_store,
                             candidate=graded, sandbox=box, pattern=pattern,
                             extra_conditions=extra, passport_store=passport_store,
                             baseline_store=svc.store, advance=True, registry=reg,
                             candidate_kind=C.CANDIDATE_KIND_PATTERN)
    print(f"  门判决={gate.passed} 生效用例={gate.executed}（适用性过滤后）"
          f" 失败条件={gate.failed_conditions}")
    if gate.replay_report is not None:
        print(f"  回放 {gate.replay_report.passed}/{gate.replay_report.total}；"
              f"人工抽检 {gate.replay_report.manual_sample}")
    if gate.passport:
        print(f"  通行证 {gate.passport['passport_id']}"
              f"（executed={gate.passport['executed']}/required="
              f"{gate.passport['required_replays']}）")
    if not gate.passed:
        print("  ✗ 门未通过 ⇒ 灰度无凭据（fail-closed），演示终止")
        for why in gate.reasons():
            print(f"    - {why}")
        return 1
    migration = gate.migration
    assert migration is not None
    desc = reg.get(DEMO_CAP)
    assert desc is not None
    print(f"  mirrored → shadow applied={migration.applied}"
          f"（stage={desc.evolution.stage}）")
    # S1-02 的 trust 回填口径：read_file 读本地仓库文件 ⇒ data_class=internal
    # （不显式回填则隐私闸门 ⑥ 判"未分级"⇒ 一票否决 —— 这正是本引擎从严的设计）
    reg.update_fields(DEMO_CAP, {"trust": {"data_class": "internal"}}, actor="demo",
                      reason="TASK-S3-03 演示：S1-02 trust 回填（本地文件读取）")
    desc = reg.get(DEMO_CAP)
    assert desc is not None
    print(f"  trust 回填：data_class={desc.trust.data_class}"
          f"｜external_endpoint={desc.origin.external_endpoint}")
    artifacts["gate"] = {k: v for k, v in gate.to_dict().items() if k != "replay"}

    # ── 步骤 4：shadow 灰度 ──
    _step(4, "shadow 灰度：日预算 min(日均×15%,50) + trace_id 哈希确定性抽样 + 灰度策略")
    judge = SH.resolve_judge("auto")
    print(f"  judge 解析：kind=`{judge.kind}`（LLM={judge.is_llm}）"
          f"｜说明={judge.detail.get('note') or judge.detail.get('unavailable_reason', '')}")
    runner = SH.ShadowRunner(judge=judge.scorer, judge_kind=judge.kind,
                             passport_store=passport_store, case_store=case_store,
                             ledger=shadow_ledger, review_queue=review_queue,
                             env={}, emit_events=True)
    # 抽样确定性证据：同批两次抽样逐字一致
    plan_probe = runner.plan(DEMO_CAP, sample_ids=[f"t{i:03d}" for i in range(200)],
                             daily_avg=120.0)
    plan_probe2 = runner.plan(DEMO_CAP, sample_ids=[f"t{i:03d}" for i in range(200)],
                              daily_avg=120.0)
    print(f"  日预算（日均 120）：{plan_probe.budget} 次"
          f"（= min(120×0.15, 50)）；抽样确定性="
          f"{plan_probe.sampled == plan_probe2.sampled}")
    print(f"  灰度策略（默认）：enabled={plan_probe.gray['enabled']}"
          f" source={plan_probe.gray['source']}"
          f"｜显式阈值试算："
          f"{SH.resolve_gray_policy(shadow_config={'enabled': True, 'gray_ratio': 0.05})['enabled']}"
          f"（real_takeover=False ⇒ 只记录选中，不接管真实执行）")
    shadow = runner.run(DEMO_CAP, case_set=case_set, candidate=graded,
                        daily_avg=args.tasks, shadow_config={"enabled": True,
                                                             "gray_ratio": 0.05},
                        force=True, candidate_kind=C.CANDIDATE_KIND_PATTERN)
    print(f"  抽样：宇宙 {len(shadow.plan.universe)} → 预算 {shadow.plan.budget}"
          f" → 执行 {shadow.total}；灰度选中 {len(shadow.plan.gray_routed)} 条")
    print(f"  三层比对：通过 {shadow.passed}/{shadow.total}（通过率 {shadow.pass_rate}）"
          f"；负例 {shadow.negative}")
    print(f"  judge_kind=`{shadow.judge_kind}`（LLM={shadow.judge_is_llm}）")
    print(f"  墙钟 p99：候选 {shadow.p99_wall_candidate_ms()}ms ≤ 上游 "
          f"{shadow.p99_wall_upstream_ms()}ms（口径 {SH.CLOCK_WALL}）")
    print(f"  模型时钟 p99（S3-02 口径，仅披露）：候选 "
          f"{shadow.p99_model_candidate_ms()}ms / 上游 {shadow.p99_model_upstream_ms()}ms")
    print(f"  人工抽检（10%）：{shadow.manual_sample} → 队列待裁定 "
          f"{shadow.manual_review.get('pending')} 条（closed={shadow.manual_review_closed()}）")
    print(f"  劣化判定：{shadow.degradation['verdict']}"
          f"（action={shadow.degradation['action']}）")
    print(f"  隔离边界：{shadow.isolation['mode']}"
          f"｜容器隔离={shadow.isolation['container_isolated']}"
          f"｜真实接管={shadow.isolation['real_takeover']}")
    print(f"  shadow_overhead：{shadow.overhead.get('shadow_overhead_ms')}ms"
          f"（{shadow.overhead.get('sample_count')} 样本）")
    artifacts["shadow"] = shadow.to_dict(include_samples=False)
    # M5：把 10% 抽检清单**另存运行时台账**（供 Owner 实际裁定）——
    # 演示目录是临时目录，抽检项必须落到可长期复核的位置，否则"清单"无人可复核
    runtime_queue = SH.ManualReviewQueue(directory=SHADOW_RUNTIME_DIR)
    runtime_queue.enqueue(DEMO_CAP, shadow.manual_sample,
                          candidate_kind=shadow.candidate_kind,
                          reasons={c: ["灰度为正确候选但需人工确认行为等价"]
                                   for c in shadow.manual_sample},
                          queued_by="demo_s3_03")
    sheet = runtime_queue.review_sheet(DEMO_CAP)
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    sheet_path = os.path.join(ARTIFACT_DIR, "人工抽检清单.md")
    with open(sheet_path, "w", encoding="utf-8") as fh:
        fh.write(sheet)
    print(f"  抽检清单（供 Owner 裁定）：{sheet_path}"
          f"｜运行时台账 {runtime_queue.path}")
    artifacts["manual_review_queue"] = runtime_queue.summary(DEMO_CAP)

    # ── 步骤 5：内化六条件 ──
    _step(5, "内化六条件（§4.5.1 / P7.2-01）：逐项打分 → ⑤⑥ 一票否决 / ①-④ 排序")
    if args.no_privacy:
        reg.update_fields(DEMO_CAP, {"trust": {"data_class": "confidential"}}, actor="demo",
                          reason="负样本：置为 confidential 以触发⑥")
        reg.update_fields(DEMO_CAP, {"origin": {"external_endpoint": True}}, actor="demo",
                          reason="负样本：登记出域端点")
        print("  [--no-privacy] descriptor 置为 data_class=confidential + "
              "external_endpoint=True（受限等级 + 出域 ⇒ ⑥ 一票否决）")
    evidence_extra: dict = {}
    if args.low_traffic:
        evidence_extra["monthly_samples"] = 30
        print("  [--low-traffic] 月样本按 30 计（T2 手动通道场景）")
    engine = I.InternalizeEngine(passport_store=passport_store, registry=reg,
                                 case_store=case_store, ledger=shadow_ledger,
                                 shadow_runner=runner)
    decision = engine.evaluate(DEMO_CAP, shadow_report=shadow,
                               ledger_store=svc.store, evidence=evidence_extra)
    print(f"  digest_count（审计通道实测）="
          f"{decision.evidence['digest_count']['value']}"
          f"（来源 {decision.evidence['digest_count']['source']}）")
    print(decision.markdown())
    print("  ── ROI 报告 ──")
    print(decision.roi_report.markdown())
    artifacts["decision"] = decision.to_dict()

    # ── 步骤 6：promote PR（本地产物） / 步骤 7：低流量手动通道 ──
    pr = None
    _step(6, "低流量手动 promote 通道（T2）：approval 留痕 → 人工批准 → stage_migrate")
    if decision.verdict == I.VERDICT_LOW_TRAFFIC_MANUAL:
        print(f"  判决={decision.verdict}（{decision.manual_label}）："
              f"①②③ 样本不足**不阻塞**，但需 ④⑤⑥ 复核 + 人工裁定")
    else:
        print(f"  判决={decision.verdict}：{'可走自动路径（见步骤 7）' if decision.promotable else '⑤⑥ 或 ④ 未过 ⇒ 手动通道同样拒绝'}")
    manual = engine.manual_promote(
        DEMO_CAP, "demo_requester", decision=decision,
        flow=_demo_flow(tmp), note="演示：低样本人工裁定通道")
    print(f"  提交结果：permitted={manual.permitted} label={manual.label or '-'}"
          f" record={manual.record_id or '-'} state={manual.state or '-'}"
          f" level={manual.level or '-'}")
    for why in manual.blocked_reasons:
        print(f"    - {why}")
    artifacts["manual_promote"] = manual.to_dict()
    if manual.permitted:
        # 人工批准（演示中由 "demo_reviewer" 扮演；真实场景为 Owner）
        ok = engine.confirm_manual_promote(
            DEMO_CAP, record_id=manual.record_id, actor="demo_reviewer",
            approve=True, note="演示：人工复核通过（真实为 Owner 裁定）",
            flow=_demo_flow(tmp))
        print(f"  人工批准：{ok}")
        applied = engine.apply_manual_promote(
            DEMO_CAP, record_id=manual.record_id, actor="demo_reviewer",
            registry=reg, decision=decision, flow=_demo_flow(tmp))
        print(f"  人工通道生效：applied={applied['applied']}｜{applied['reasons'][:1]}")
        final_desc = reg.get(DEMO_CAP)
        assert final_desc is not None
        print(f"  台账 stage={final_desc.evolution.stage}")
        artifacts["manual_apply"] = {"applied": applied["applied"],
                                     "reasons": applied["reasons"],
                                     "audit_seq": applied["audit_seq"]}
    else:
        print("  ✗ ④⑤⑥ 未通过 ⇒ 手动通道同样不放行（一票否决/质量项不可由人工绕过）")
        artifacts["manual_apply"] = {"applied": False,
                                     "reasons": manual.blocked_reasons}

    # ── 步骤 7：自动路径（六条件齐备）→ stage.promote PR 产物 ──
    _step(7, "自动路径：六条件齐备 → stage.promote PR（**本地可审阅**，不推送/不合并）")
    if decision.promotable:
        auto = decision
        print("  本轮实测证据已齐备 ⇒ 直接用实测决策产出 PR")
    else:
        auto = engine.evaluate(
            DEMO_CAP, shadow_report=shadow, ledger_store=svc.store,
            evidence={"digest_count": I.DIGEST_COUNT_MIN + 2,
                      "monthly_samples": I.MONTHLY_SAMPLES_MIN + 40,
                      "roi": {"upstream_unit_cents": 12.0,
                              "native_unit_cents": 1.5,
                              "one_time_investment_cents": 6000.0}})
        print(f"  本轮实测 ①②③ 样本不足（离线设施所致）⇒ 以**合成样本量**演示自动路径："
              f"digest_count={I.DIGEST_COUNT_MIN + 2}、"
              f"monthly_samples={I.MONTHLY_SAMPLES_MIN + 40}、"
              f"上游 12 分/次 vs 自研 1.5 分/次（**离线设施合成，不代表真实收益**）")
        print(f"  合成证据下判决={auto.verdict}｜排序分={auto.rank_score}"
              f"｜阻断项={auto.blocker or '（无）'}")
    if auto.promotable:
        pr = engine.create_promote_pr(auto, registry=reg,
                                      out_dir=os.path.join(tmp, "promote_pr"))
        assert pr is not None
        print(f"  PR {pr.pr_id}｜建议分支 `{pr.branch}`"
              f"｜pushed={pr.pushed} merged={pr.merged}")
        print(f"  产物：{sorted(pr.files)}")
        print("  ── 变更补丁 ──")
        print(pr.patch)
        print("  ── ROI 报告（节选） ──")
        print("\n".join(auto.roi_report.markdown().splitlines()[:14]))
        artifacts["promote_pr"] = pr.to_dict()
        artifacts["promote_pr_artifacts"] = {"description": pr.description,
                                             "roi_markdown": pr.roi_markdown}
        artifacts["auto_decision"] = auto.to_dict()
    else:
        print(f"  判决={auto.verdict}｜阻断项：{auto.blocker}")
        print("  ⇒ 一票否决/条件未满足时**不产出 PR**（不静默、不绕门）")
        artifacts["promote_pr"] = None

    # ── 步骤 8：归档 ──
    _archive(args, artifacts, tmp, decision, pr)
    _hr("演示结束")
    end_desc = reg.get(DEMO_CAP)
    assert end_desc is not None
    print(f"门判决={gate.passed}｜内化判决={decision.verdict}｜"
          f"stage={end_desc.evolution.stage}｜"
          f"judge_kind={shadow.judge_kind}｜灰度高流量={decision.promotable}")
    if args.veto_p99 or args.no_privacy:
        return 0 if decision.verdict == I.VERDICT_VETO_BLOCKED else 1
    if args.low_traffic:
        return 0 if decision.verdict == I.VERDICT_LOW_TRAFFIC_MANUAL else 1
    return 0


def _demo_flow(tmp: str):
    """演示用审批流（隔离到临时目录；L2 = 人工执行）"""
    from agent.digestion.internalize import (APPROVAL_ACTION, APPROVAL_LEVEL_MANUAL,
                                             APPROVAL_OBJECT_TYPE)
    from agent.skills_mgmt.approval import ApprovalFlow
    return ApprovalFlow(records_path=os.path.join(tmp, "approvals.jsonl"),
                        enabled=True,
                        level_map={(APPROVAL_OBJECT_TYPE, APPROVAL_ACTION):
                                   APPROVAL_LEVEL_MANUAL},
                        default_level=APPROVAL_LEVEL_MANUAL)


def _archive(args, artifacts: dict, tmp: str, decision, pr) -> None:
    if not args.archive:
        print("\n  （--no-archive：跳过归档）")
        return
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    out = os.path.join(ARTIFACT_DIR, "s3_03_demo_report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(artifacts, fh, ensure_ascii=False, indent=1, default=str)
    print(f"\n  已归档: {out}")
    md = os.path.join(ARTIFACT_DIR, "S3-03_内化决策样例.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write(decision.markdown())
        fh.write("\n")
        fh.write(decision.roi_report.markdown())
        if pr is not None:
            fh.write("\n")
            fh.write(pr.markdown())
    print(f"  内化决策样例: {md}")


if __name__ == "__main__":
    raise SystemExit(main())
