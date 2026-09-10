"""TASK-S2-03 端到端演示：events.v1 信封 + ACR/UTC 埋点（真实任务链快照）

【演示目标】
    用**一条真实任务链**串起本任务的全部埋点，并输出可直接粘贴进验收报告的
    ACR + UTC 样例快照：

    1. 审批流：submit / approve（1）/ 条件通过（0.5）/ 驳回（扩展项）/ 超时 Deny（2）
    2. 逃逸：用户手工编辑受管任务文件 → `escape` + `intervention(weight=1)`
    3. 查看：人工查看待审批项 → `intervention(weight=0)`
    4. 会话任务：TraceContext 起点 → 工具级 Trace → LLM 记账（缓存命中不计 token）
       → 重跑介入（task 关联）→ 任务收口 `task.closed`
    5. 模型降级：LLM 调用失败 → `model.degraded {from,to,reason}`
    6. 幂等：把同一批事件**内容级重放** → 计数不变（§四验收「重放不重复计数」）
    7. 对账：归一化成本 vs 既有成本监控口径（逐分一致）

【运行方式】
    python scripts/demo_s2_03_events_acr.py            # 全流程
    python scripts/demo_s2_03_events_acr.py --json      # 追加打印 JSON 快照

【产物】
    data/events_demo/events.jsonl        （events.v1 事件流，演示用，不入库）
    data/acr_snapshot.json               （ACR 日/周快照）
    data/utc_snapshot.json               （UTC 日/周快照）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# 演示隔离：事件目录与台账都用 demo 位置，避免污染生产数据目录
_DEMO_EVENTS_DIR = _ROOT / "data" / "events_demo"
os.environ["CP_EVENTS_DIR"] = str(_DEMO_EVENTS_DIR)
os.environ.setdefault("CP_UTC_ANCHOR_MODEL", "gpt-4")

from agent.observability import acr, escape, events as ev, model_degrade, utc  # noqa: E402
from agent.observability.trace_v2 import TraceFacade  # noqa: E402


def hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="追加打印 JSON 快照")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 终端不支持则保持默认
            pass

    # ── 环境准备（可复现：清空上次演示产物）────────────────
    if _DEMO_EVENTS_DIR.exists():
        shutil.rmtree(_DEMO_EVENTS_DIR, ignore_errors=True)
    _DEMO_EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    ev.reset_event_stores()
    TraceFacade.reset()
    demo_trace_db = _ROOT / "data" / "s2_03_demo_trace.db"
    TraceFacade.instance(db_path=str(demo_trace_db))
    tmpdir = tempfile.mkdtemp(prefix="s2_03_demo_")
    events_dir = str(_DEMO_EVENTS_DIR)
    store = ev.get_event_store()

    hr("TASK-S2-03 演示：events.v1 信封 + ACR/UTC 埋点（真实任务链）")
    print(f"事件流：{_DEMO_EVENTS_DIR / ev.ACTIVE_FILENAME}")
    print(f"Trace 台账：{demo_trace_db}")
    print(f"锚模型：{utc.resolve_anchor_model()[0]}（来源 {utc.resolve_anchor_model()[1]}）")

    # ════════════════════════════════════════════════════════
    # [1] 审批流（治理侧，无任务上下文 → correlation=unlinked）
    # ════════════════════════════════════════════════════════
    hr("[1] 审批流 → approval.required / approval / intervention（§6.1 口径）")
    from agent.skills_mgmt.approval import ApprovalFlow

    flow = ApprovalFlow(records_path=str(Path(tmpdir) / "approvals.jsonl"))
    r_approve = flow.submit("skill", "demo-skill-a", action="params_submit",
                            actor="agent")
    flow.approve(r_approve.record_id, actor="reviewer")
    r_cond = flow.submit("skill", "demo-skill-b", action="params_submit",
                         actor="agent")
    flow.approve(r_cond.record_id, actor="reviewer", note="仅限本周，观察后再放开")
    r_reject = flow.submit("prompt", "demo-prompt-c", action="prompt_apply",
                           actor="agent")
    flow.reject(r_reject.record_id, actor="reviewer", reason="证据不足")
    r_timeout = flow.submit("skill", "demo-skill-d", action="params_submit",
                            actor="agent")
    r_timeout.created_at = (datetime.now() - timedelta(days=2)).isoformat(
        timespec="seconds")
    flow._persist()
    expired = flow.expire_pending(older_than_seconds=3600 * 24)
    print(f"  提交 4 条 → approve / 条件通过 / 驳回 / 超时拒绝 {len(expired)} 条")

    # ════════════════════════════════════════════════════════
    # [2] 逃逸：用户手工编辑受管任务文件
    # ════════════════════════════════════════════════════════
    hr("[2] 逃逸检测：用户手工编辑受管任务文件（未经受控写入路径）")
    watched = Path(tmpdir) / "scheduled_tasks.json"
    watched.write_text(json.dumps({"tasks": [{"id": "demo-1"}]}, ensure_ascii=False),
                       encoding="utf-8")
    escape.detect_escapes([str(watched)])                      # 首次见到 → 基线
    escape.record_governed_write(str(watched), writer="create_scheduled_task",
                                 extra={"task_ids": ["demo-1"]})
    escape.detect_escapes([str(watched)])                      # 受控写后 → clean
    watched.write_text(json.dumps(
        {"tasks": [{"id": "demo-1"}, {"id": "hand-edited"}]}, ensure_ascii=False),
        encoding="utf-8")
    for row in escape.detect_escapes([str(watched)]):
        print(f"  {row['status']:<8} path={Path(row['path']).name} "
              f"reason={row.get('reason', '')} "
              f"capability={row.get('capability_id', '')} "
              f"changed={row.get('changed_task_ids', [])}")
    again = escape.detect_escapes([str(watched)])               # 重放 → 不再计数
    print(f"  重复检测：emitted={again[0]['emitted']}"
          f"（同一 (path, 新摘要) 只计一次）")

    # ════════════════════════════════════════════════════════
    # [3] 会话任务链：TraceContext → 工具 → LLM → 重跑 → 收口
    # ════════════════════════════════════════════════════════
    hr("[3] 会话任务链：TraceContext → 工具调用 → LLM 记账 → task.closed")
    user_input = "实现一个新的解析器并跑通测试"
    intent = acr.classify_intent(user_input)
    difficulty = acr.classify_difficulty(user_input, intent)
    task_trace = TraceFacade.instance().start(task_id="demo-task-1",
                                             workspace_id="ws_demo_s203")
    facade = TraceFacade.instance()
    facade.record("cp.builtin.read_file", args={"path": "agent/parser.py"},
                  output={"ok": True}, actor="auto")
    facade.record("cp.builtin.shell_execute", args={"cmd": "pytest -q"},
                  output={"ok": False, "error_code": "TestFailed"},
                  actor="auto")
    print(f"  任务意图（启发式）：intent={intent} difficulty={difficulty}")
    print(f"  任务级 trace_id：{task_trace}（workspace_id=ws_demo_s203）")

    from agent.llm_monitor import LLMInteraction, LLMMonitor

    monitor = LLMMonitor(max_records=20)
    monitor.record(LLMInteraction(
        source="chat", model="gpt-4", provider="openai",
        request_tokens=2000, response_tokens=1000, duration_ms=120.0))
    monitor.record(LLMInteraction(
        source="tool_calling", model="gpt-4o-mini", provider="openai",
        request_tokens=5000, response_tokens=5000, duration_ms=30.0,
        cache_hit=True, shadow_overhead_ms=15.0))
    monitor.record(LLMInteraction(
        source="chat", model="gpt-4", provider="openai",
        error="APITimeoutError: request timed out"))
    print("\n  cost 埋点（§6.6：task_id/workspace_id/model/tokens/retries/"
          "shadow_overhead/cents）：")
    for env in ev.read_events(types=["cost"], directory=events_dir):
        p = env.payload
        print(f"    model={p['model']:<13} tok={p['tokens_in']}/{p['tokens_out']} "
              f"计费tok={p['billable_tokens_in']}/{p['billable_tokens_out']} "
              f"cache_hit={str(p['cache_hit']):<5} "
              f"归一={p['cost_normalized_cents']}¢ "
              f"task_id={p['task_id'] or '(unlinked)'} ws={p['workspace_id'] or '-'}")

    # 人工重跑（在任务上下文内 → 归属该任务）
    acr.record_intervention(acr.KIND_RERUN, actor="human",
                            correlation_id=task_trace, task_id="demo-task-1",
                            source_ref="scheduler:demo-task-1")
    acr.record_intervention(acr.KIND_VIEW, actor="human",
                            source_ref="approval_view:demo")

    from agent.orchestrator.orchestrator import _end_unified_task_trace

    _end_unified_task_trace(task_trace, "success", "", user_input)
    closed = ev.read_events(types=["task.closed"], directory=events_dir)
    if closed:
        payload = closed[-1].payload
        print(f"\n  task.closed: intent={payload['intent']} "
              f"difficulty={payload['difficulty']} status={payload['status']} "
              f"intervened={payload['intervened']} "
              f"intervention_kind={payload['intervention_kind']}")

    # ════════════════════════════════════════════════════════
    # [4] 模型降级
    # ════════════════════════════════════════════════════════
    hr("[4] model.degraded（P7.1-18 第 9 事件 / §11.6.0 E_MODEL_DEGRADED）")
    model_degrade.reset_chain_cache()
    chain_models, chain_source = model_degrade.resolve_fallback_chain()
    print(f"  降级链：{chain_models}（来源 {chain_source}）"
          f" 实际切换开关 enabled={model_degrade.fallback_enabled()}"
          f"（CP_MODEL_FALLBACK_ENABLED，默认 0 = 只观测不改行为）")
    summary = model_degrade.degrade_summary(directory=events_dir)
    for edge, count in summary["edges"].items():
        print(f"  {edge}  ×{count}")
    for env in ev.read_events(types=["model.degraded"], directory=events_dir):
        print(f"  payload: from={env.payload['from']} to={env.payload['to']} "
              f"reason={env.payload['reason'][:48]}… "
              f"error_code={env.payload['error_code']} "
              f"attempted={env.payload['fallback_attempted']}")

    # ════════════════════════════════════════════════════════
    # [5] 事件流总览
    # ════════════════════════════════════════════════════════
    hr("[5] events.v1 事件流总览（信封：v/event_id/ts/correlation_id/actor/type/payload）")
    all_events = ev.read_events(directory=events_dir)
    by_type: dict = {}
    for env in all_events:
        by_type[env.type] = by_type.get(env.type, 0) + 1
    print(f"  事件总数={len(all_events)}  "
          f"类型分布={json.dumps(by_type, ensure_ascii=False)}")
    print("  样例信封（§3.6 七字段，逐字）：")
    for line in json.dumps(all_events[-1].to_dict(), ensure_ascii=False,
                           indent=2).splitlines():
        print("    " + line)

    # ════════════════════════════════════════════════════════
    # [6] 幂等：内容级重放不重复计数
    # ════════════════════════════════════════════════════════
    hr("[6] 幂等验证：同一批事件内容级重放 → 计数不变")
    before = len(all_events)
    folded = 0
    for env in all_events:
        if store.append(ev.EventEnvelope.from_dict(env.to_dict())) is False:
            folded += 1
    after = len(ev.read_events(directory=events_dir))
    print(f"  重放前 {before} 条 → 重放后 {after} 条"
          f"（内容级重放折叠 {folded}/{before} 条，新增 {after - before} 条）")

    # ════════════════════════════════════════════════════════
    # [7] ACR 快照
    # ════════════════════════════════════════════════════════
    hr("[7] ACR 汇总（§6.1 口径 / 分母排除 explore·consult / 探索满意度单列）")
    acr_view = acr.acr_snapshot(days=7, directory=events_dir)
    today = acr_view["today"]
    print(f"  分母：closed={today['denominator']['closed']} "
          f"failed={today['denominator']['failed']} "
          f"total={today['denominator']['total']}"
          f"（排除 explore/consult {today['excluded']['tasks']} 个任务；"
          f"abandoned={today['abandoned']} 单列）")
    print(f"  介入：events={today['interventions']['events']} "
          f"总权重={today['interventions']['total_weight']} "
          f"§6.1 分子={today['acr_numerator']} "
          f"扩展项权重={today['interventions']['extension_weight']} "
          f"无法归属={today['interventions']['unattributed_weight']}")
    print(f"  分项计数：{json.dumps(today['interventions']['counts'], ensure_ascii=False)}")
    print(f"  分项权重：{json.dumps(today['interventions']['by_kind'], ensure_ascii=False)}")
    print(f"  权重表：{json.dumps(today['weights'], ensure_ascii=False)}")
    print(f"  ACR = {today['acr']}   （{today['acr_formula']}）")
    print(f"  探索满意度（P7.1-16 单列）：tasks="
          f"{today['exploration']['tasks']} "
          f"satisfaction={today['exploration']['satisfaction']} "
          f"介入权重={today['exploration']['intervention_weight']}")
    print(f"  周视图：{acr_view['week']['window']} ACR={acr_view['week']['acr']}")

    # ════════════════════════════════════════════════════════
    # [8] UTC 快照
    # ════════════════════════════════════════════════════════
    hr("[8] UTC 汇总（单位任务成本；锚价 + 系数表；S5-03 断食/刹车数据源）")
    # 为演示 §7 的「近 7 日滚动基线 / ratio」补两天历史成本（**显式标注 demo_seed**，
    # 不冒充真实调用：见 payload.demo_seed 与下方说明）
    for offset in (1, 2):
        past = (date.today() - timedelta(days=offset)).isoformat()
        utc.record_cost(model="gpt-4", tokens_in=1000, tokens_out=500,
                        source="demo_seed", interaction_id=f"seed-{offset}",
                        ts=f"{past}T09:00:00+08:00", store=store,
                        error="")
    utc_view = utc.utc_snapshot(days=7, directory=events_dir)
    row = utc_view["today"]
    print(f"  锚价：{json.dumps(utc.anchor_prices_cents())} cents/1k tokens")
    print(f"  当日：llm_calls={row['llm_calls']} cache_hits={row['cache_hits']} "
          f"计费token={row['billable_tokens_total']}/{row['tokens_total']}"
          f"（缓存命中不计 token，P7.1-18）")
    print(f"  成本：raw={row['cost_raw_cents']}¢  "
          f"归一={row['cost_normalized_cents']}¢  "
          f"shadow_overhead={row['shadow_overhead_cents']}¢")
    print(f"  UTC = 归一成本 / 任务数 = {row['utc_cents_per_task']} ¢/任务"
          f"（ACR 同口径分母：{row['utc_cents_per_task_acr_cohort']} ¢/任务）")
    print(f"  滚动基线：{utc_view['baseline_cents_per_day']}¢/日"
          f"（{utc_view['baseline_days']} 日，含 2 条 demo_seed 历史事件）"
          f" ratio={utc_view['ratio']}")
    print(f"  断食阈值（S5-03 消费，本任务只供度量）："
          f"{json.dumps(utc_view['thresholds'], ensure_ascii=False)}")
    print("  系数表（模型 × 计价，T6「以主力模型为锚 + 换算系数」）：")
    for name, item in utc.coefficient_table()["models"].items():
        print(f"    {name:<14} k_in={item['in']:.6f} k_out={item['out']:.6f} "
              f"来源={item['source']}")
    reconcile = utc.reconcile_pricing()
    print(f"  与既有成本监控（CostTracker/MODEL_COSTS）对账："
          f"一致={reconcile['consistent']}（{len(reconcile['rows'])} 个模型逐分相等）")

    # ════════════════════════════════════════════════════════
    # [9] 快照落盘
    # ════════════════════════════════════════════════════════
    hr("[9] 快照落盘")
    acr_path = _ROOT / "data" / "acr_snapshot.json"
    utc_path = _ROOT / "data" / "utc_snapshot.json"
    acr.write_acr_snapshot(str(acr_path), days=7, directory=events_dir)
    utc.write_utc_snapshot(str(utc_path), days=7, directory=events_dir)
    print(f"  {acr_path}")
    print(f"  {utc_path}")

    if args.json:
        hr("[JSON] ACR + UTC 快照")
        print(json.dumps({"acr": acr_view["today"], "utc": row},
                         ensure_ascii=False, indent=2))

    hr("演示完成")
    print("  ① events.v1 信封七字段对齐 §3.6（九事件 + §6.6 埋点事件）")
    print("  ② ACR 口径：Approve=1 / 条件=0.5 / 自动放行=0 / 超时 Deny=2 /"
          " 重跑=1 / 逃逸=1 / 查看=0")
    print("  ③ 分母排除 explore/consult，探索满意度单列；重放不重复计数")
    print("  ④ UTC：缓存命中不计 token；锚价 + 系数表归一；与既有成本监控逐分一致")
    print("  ⑤ model.degraded 在错误路径触发且有 from/to/reason")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
