"""TASK-S3-01 端到端演示：轨迹 → 清洗 → 模式挖掘 → SKILL.md 草稿 → stage 迁移

覆盖验收清单的"端到端样例（轨迹→SKILL.md 草稿）可复现"与四项遗留（L1–L4）的
可复现证据。**全部隔离**：统一台账写到临时目录，事件写到临时目录，审计链绑定
到临时库；仅**演示产物**（报告 JSON + 草稿 SKILL.md）归档到
``data/digestion/demo/`` 供验收报告引用。

用法::

    python scripts/demo_s3_01_digestion.py            # 完整演示（9 步）
    python scripts/demo_s3_01_digestion.py --tasks 24 # 调整同类轨迹条数
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEMO_CAP = "cp.builtin.read_file"
CHAIN_STEPS = ("cp.builtin.shell_execute", "cp.builtin.write_file")
BASE_TS = 1_760_000_000.0
ARTIFACT_DIR = os.path.join("data", "digestion", "demo")


def _hr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def _step(n: int, text: str) -> None:
    print(f"\n[步骤 {n}] {text}")


def build_demo_ledger(facade, tasks: int, fail_every: int):
    """合成统一台账：每个任务 = 任务级 Trace + 能力级子 Trace

    刻意注入噪声（探索前缀 ``list_dir``、相邻重试重复）与部分失败轨迹，
    使清洗规则与"失败轨迹→负样本"在演示中真实发生。
    """
    fail_ids = []
    for i in range(tasks):
        t0 = BASE_TS + i * 10.0
        task_id = f"demo-task{i:04d}"
        facade.start(task_id=task_id, workspace_id="ws_s3_01_demo")
        cursor = t0
        # 探索前缀（噪声 1）
        facade.record("list_dir", args={"path": f"C:/repo/proj{i}"},
                      output={"ok": True}, started_at=cursor, duration_ms=4.0)
        cursor += 1.0
        args = {"path": f"C:/repo/proj{i}/src/mod{i}.py", "encoding": "utf-8"}
        facade.record(DEMO_CAP, args=dict(args),
                      output={"ok": True, "size": 100 + i},
                      started_at=cursor, duration_ms=3.0)
        cursor += 1.0
        # 相邻重复（噪声 2：重试）
        facade.record(DEMO_CAP, args=dict(args),
                      output={"ok": True, "size": 100 + i},
                      started_at=cursor, duration_ms=3.0)
        cursor += 1.0
        failing = bool(fail_every) and i % fail_every == fail_every - 1
        for j, label in enumerate(CHAIN_STEPS):
            if failing and j == len(CHAIN_STEPS) - 1:
                continue      # 失败轨迹缺末步 ⇒ 成功/失败骨架有差异
            facade.record(label, args={"cmd": f"run{i}_{j}"},
                          output={"ok": True}, started_at=cursor,
                          duration_ms=2.0)
            cursor += 1.0
        facade.finish(status="error" if failing else "success")
        if failing:
            fail_ids.append(task_id)
    return {"tasks": tasks, "fail_task_ids": fail_ids,
            "success_tasks": tasks - len(fail_ids)}


def main() -> int:
    parser = argparse.ArgumentParser(description="TASK-S3-01 消化流水线演示")
    parser.add_argument("--tasks", type=int, default=28,
                        help="同类轨迹条数（默认 28 ≥ 门槛 20）")
    parser.add_argument("--fail-every", type=int, default=7,
                        help="每 N 条注入 1 条失败轨迹（0=全成功）")
    parser.add_argument("--archive", action="store_true", default=True,
                        help="归档产物到 data/digestion/demo/")
    parser.add_argument("--no-archive", dest="archive", action="store_false")
    args = parser.parse_args()

    tmp = tempfile.mkdtemp(prefix="s3_01_demo_")
    os.environ["CP_EVENTS_DIR"] = os.path.join(tmp, "events")
    os.environ["AUDIT_CHAIN_ENABLED"] = "0"     # 演示不改真实审计链

    from agent.audit import facade as facade_mod
    from agent.audit.chain import AuditChain
    from agent.descriptors.bridge import register_bridge_view
    from agent.descriptors.registry import DescriptorRegistry
    from agent.digestion import DigestionService
    from agent.observability.trace_v2 import TraceFacade
    from agent.tool_calling import resolve_tool_capability_id

    # ── 隔离：审计链绑定到临时库 ──
    chain = AuditChain(os.path.join(tmp, "audit.db"),
                       roots_path=os.path.join(tmp, "roots.jsonl"),
                       signing_key_path=os.path.join(tmp, "k.pem"),
                       auto_seal=False)
    facade_mod.audit.bind(chain)
    facade_mod.audit.enabled = True

    # ── 隔离：统一台账 → 临时库 ──
    trace_facade = TraceFacade(os.path.join(tmp, "trace.db"))
    TraceFacade._instance = trace_facade

    # ── 隔离：descriptor 台账 → 临时文件 ──
    reg = DescriptorRegistry(path=os.path.join(tmp, "descriptors.json"),
                            autosave=False)
    register_bridge_view(reg, "builtin", [
        {"name": "read_file", "description": "读取文件内容"},
        {"name": "shell_execute", "description": "执行命令"},
        {"name": "write_file", "description": "写入文件"}])

    _hr("TASK-S3-01 消化流水线端到端演示")
    print(f"隔离目录: {tmp}")

    # ── 步骤 1：合成轨迹台账 ──
    _step(1, "合成统一轨迹台账（含探索前缀/重试重复噪声 + 失败轨迹）")
    info = build_demo_ledger(trace_facade, args.tasks, args.fail_every)
    trace_facade.flush()
    print(f"  任务数={info['tasks']} 成功={info['success_tasks']} "
          f"失败={len(info['fail_task_ids'])}")
    print(f"  台账行数={len(trace_facade.query())}")

    # ── 步骤 2：【L1】工具名 → capability_id 改写与 join ──
    _step(2, "【L1】工具链落账键改写：工具名 → canonical capability_id")
    resolved = resolve_tool_capability_id("read_file")
    joined = reg.get(resolved) is not None
    print(f"  resolve_tool_capability_id('read_file') = {resolved}")
    print(f"  与 descriptor 台账 join: {joined}")
    assert resolved == DEMO_CAP and joined

    # ── 步骤 3：消化流水线（清洗 → 挖掘 → 草稿 → stage） ──
    _step(3, "运行消化流水线 DigestionService.pipeline()")
    svc = DigestionService(store=trace_facade._store, registry=reg,
                           draft_dir=os.path.join(tmp, "drafts"))
    report = svc.pipeline(capability_id=DEMO_CAP, migrate=True)
    data = report.as_dict()
    print(f"  轨迹总数={data['trajectories_total']} "
          f"清洗后={data['trajectories_cleaned']} 负样本={data['negative_total']}")
    print(f"  清洗统计={data['cleanup']}")
    print(f"  达标={data['eligible']} 说明={data['reason']}")

    # ── 步骤 4：候选模式 ──
    _step(4, "候选模式（LCS 骨架 + 参数槽 + 决策树分支）")
    assert data["candidates"], "未产出候选模式"
    pattern = data["candidates"][0]
    print(f"  pattern_id={pattern['pattern_id']}")
    print(f"  骨架({pattern['lcs_length']} 步): "
          + " → ".join(s["label"] for s in pattern["steps"]))
    print(f"  参数槽={pattern['slots']}")
    print(f"  分支条件={pattern['branches']}")
    print(f"  支撑={pattern['support']}/{pattern['sample_size']} "
          f"覆盖率={pattern['coverage']} 置信度={pattern['confidence']}")

    # ── 步骤 5：SKILL.md 草稿（draft 态） ──
    _step(5, "SKILL.md 草稿（draft 态；不发布、不越过审批）")
    draft_info = data["drafts"][0]
    print(f"  skill_id={draft_info['skill_id']} status={draft_info['status']}")
    print(f"  落盘={draft_info['path']}")
    with open(draft_info["path"], encoding="utf-8") as fh:
        markdown = fh.read()
    print("  ── 草稿前 40 行 ──")
    for line in markdown.splitlines()[:40]:
        print("   " + line)

    # ── 步骤 6：stage 迁移（三处联动） ──
    _step(6, "stage 迁移：descriptor + 审计 + digest.stage 事件")
    migration = data["stage_migration"]
    print(f"  {migration['from']} → {migration['to']} "
          f"applied={migration['applied']} verdict={migration['verdict']}")
    print(f"  trace_policy={migration['trace_policy']}")
    print(f"  event_id={migration['event_id']} audit_seq={migration['audit_seq']} "
          f"audit_action={migration['audit_action']}")
    print(f"  descriptor.evolution.stage="
          f"{reg.get(DEMO_CAP).evolution.stage.value}")  # type: ignore[union-attr]

    # ── 步骤 7：事件流核验 ──
    _step(7, "事件流（events.v1 信封）核验")
    import agent.observability.events as events_mod
    emitted = events_mod.iter_events(directory=os.environ["CP_EVENTS_DIR"])
    for etype in ("skill.generated", "digest.stage"):
        rows = [e for e in emitted if e.type == etype]
        print(f"  {etype}: {len(rows)} 条"
              + (f" | event_id={rows[0].event_id}" if rows else ""))
    assert any(e.type == "skill.generated" for e in emitted)
    assert any(e.type == "digest.stage" for e in emitted)

    # ── 步骤 8：审计链核验 ──
    _step(8, "链式审计核验（stage 写入由 registry 独占，无重复留痕）")
    chain.flush()
    entries = chain.entries()
    actions = [e.action for e in entries]
    print(f"  链上记录数={len(entries)} actions={sorted(set(actions))}")
    print(f"  descriptor.stage 条数={actions.count('descriptor.stage')}")
    assert actions.count("descriptor.stage") == 1
    verification = facade_mod.audit.verify()
    print(f"  链验签: ok={verification.ok} checked={verification.checked}")

    # ── 步骤 9：L4 首次入轨（对演示台账重放，证明入轨可复现） ──
    _step(9, "【L3+L4】首次入轨 + 占位 trace_policy 切换（演示台账副本）")
    import agent.descriptors.bridge as bridge_mod
    from agent.descriptors.models import EvolutionStage

    demo2 = DescriptorRegistry(path=os.path.join(tmp, "descriptors2.json"),
                              autosave=False)
    register_bridge_view(demo2, "builtin", [
        {"name": "read_file", "description": "读"},
        {"name": "write_file", "description": "写"}])
    # 构造两类存量：① 未入轨 + 占位策略；② 已 borrowed 但策略仍是 S2 占位串
    legacy = bridge_mod.descriptor_from_builtin_tool("legacy_tool", "老资产")
    legacy.evolution.trace_policy = "trace:builtin:call-side(S2-ledger-pending)"
    demo2.register(legacy)
    stale = bridge_mod.descriptor_from_builtin_tool("stale_policy_tool", "旧策略资产")
    stale.evolution.stage = EvolutionStage.BORROWED
    stale.evolution.trace_policy = (
        "trace:skill:import-ledger(S2-pending)")
    demo2.register(stale)

    ingest = svc.__class__(store=trace_facade._store,
                           registry=demo2).ingest_unstaged(execute=False)
    print(f"  干跑：资产={ingest['total_assets']} 未入轨={ingest['empty_stage']} "
          f"占位策略={ingest['policies']['placeholder']}")
    ingest = svc.__class__(store=trace_facade._store,
                           registry=demo2).ingest_unstaged(execute=True)
    print(f"  实跑：入轨={len(ingest['ingested'])} "
          f"占位切换={len(ingest['policies']['refreshed'])} "
          f"残留未入轨={ingest['warnings_after']}")
    for cid in ("cp.builtin.legacy_tool", "cp.builtin.stale_policy_tool"):
        desc = demo2.get(cid)
        assert desc is not None
        print(f"  {cid}: stage={desc.evolution.stage.value} "  # type: ignore[union-attr]
              f"policy={desc.evolution.trace_policy[:70]}…")
        assert "pending" not in desc.evolution.trace_policy.lower()
    assert ingest["warnings_after"] == 0
    assert len(ingest["policies"]["refreshed"]) == 1

    # ── 归档 ──
    if args.archive:
        os.makedirs(ARTIFACT_DIR, exist_ok=True)
        report_path = os.path.join(ARTIFACT_DIR, "digestion_report.json")
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        draft_target = os.path.join(ARTIFACT_DIR,
                                    f"{draft_info['skill_id']}.SKILL.md")
        shutil.copy2(draft_info["path"], draft_target)
        with open(os.path.join(ARTIFACT_DIR, "demo_summary.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "capability_id": DEMO_CAP,
                "tasks": info["tasks"],
                "fail_tasks": len(info["fail_task_ids"]),
                "eligible": data["eligible"],
                "pattern": pattern,
                "stage_migration": migration,
                "l1_resolved_capability_id": resolved,
                "l1_joined": joined,
                "draft_skill_id": draft_info["skill_id"],
                "draft_status": draft_info["status"],
            }, fh, ensure_ascii=False, indent=2)
        print(f"\n产物归档: {report_path}")
        print(f"           {draft_target}")
        print(f"           {os.path.join(ARTIFACT_DIR, 'demo_summary.json')}")

    print("\n" + "=" * 72)
    print("演示完成：轨迹 → 清洗 → 模式 → SKILL.md 草稿 → stage 迁移 全链路通过")
    print("=" * 72)
    chain.close(timeout=2.0)
    trace_facade._store.stop(timeout=2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
