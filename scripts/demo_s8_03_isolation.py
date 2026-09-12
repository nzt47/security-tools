"""TASK-S8-03 端到端演示（隔离执行 → 灰度记录 → 未双写 → 回退验证）

四幕，全程用**真东西**（真判定集、真验收门、真隔离执行器、真事故卡）：

| 幕 | 做什么 | 证明什么 |
|---|---|---|
| ① 环境与等级 | 探测 Docker 与子进程能力 → 解析生效等级 | 等级**如实标注**（不可用即降级并说明） |
| ② 隔离执行 + 灰度 | 真发通行证 → 灰度双跑 → 开启接管并在隔离边界内执行候选 | 接管**在隔离环境内**发生（不是进程内模型冒充） |
| ③ 未双写 | 对"真实环境"见证目录做前后指纹比对 | 副作用**只记录不双写**（机器可读差异） |
| ④ 回退验证 | 注入持续失败的候选 → 连续 N 次失败 | 自动回落 `sandbox_replay_only` + 事故卡 |

产物落在 `data/isolation/`（**运行时区，gitignore**）：JSON 证据 + Markdown 摘要。

用法::

    python scripts/demo_s8_03_isolation.py
    python scripts/demo_s8_03_isolation.py --level subprocess_hardened
    python scripts/demo_s8_03_isolation.py --keep-work    # 保留隔离工作目录便于排查
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent.digestion import cases as C  # noqa: E402
from agent.digestion import gate as G  # noqa: E402
from agent.digestion import isolation as ISO  # noqa: E402
from agent.digestion import shadow as SH  # noqa: E402
from agent.digestion import takeover as TK  # noqa: E402

CAP = "cp.builtin.read_file"
OUT_DIR = os.path.join(_ROOT, "data", "isolation")
WITNESS_DIRNAME = ".tmp_iso_witness"


# ════════════════════════════════════════════════════════════
#  构造（真判定集 / 真通行证）
# ════════════════════════════════════════════════════════════


def make_case(index: int, *, root: str = "C:/sandbox") -> C.EquivalenceCase:
    path = f"{root}/out/a{index}.txt"
    steps = [C.ProgramStep(label="read_file", params={"path": path}),
             C.ProgramStep(label="write_file",
                           params={"path": path, "content": f"c{index}"})]
    return C.EquivalenceCase(
        case_id=f"case-{index:03d}", capability_id=CAP, input={"path": path},
        upstream=steps, native=steps, fixtures={path: f"c{index}"},
        expected_side_effects={"files_written": [path]},
        expected_status="success", sandbox_root=root)


def build_signed_case_set(workdir: str, size: int) -> Any:
    """真发证：补齐到 `GATE_REPLAY_MIN` 后走 `acceptance_gate`（不伪造通行证）"""
    cases = [make_case(i) for i in range(size)]
    for i in range(len(cases), G.GATE_REPLAY_MIN):
        cases.append(make_case(i))
    case_set = C.build_case_set(CAP, cases)
    store = G.PassportStore(os.path.join(workdir, "cases"))
    result = G.acceptance_gate(CAP, case_set=case_set, passport_store=store,
                               emit_events=False)
    return case_set, store, result


def make_runner(workdir: str, *, executor: Any, plan: ISO.IsolationPlan,
                env: Dict[str, str], emit: bool) -> SH.ShadowRunner:
    return SH.ShadowRunner(
        judge=SH.resolve_judge("local").scorer, judge_kind=SH.JUDGE_KIND_LOCAL,
        passport_store=G.PassportStore(os.path.join(workdir, "cases")),
        case_store=C.open_case_store(os.path.join(workdir, "cases")),
        ledger=SH.ShadowLedger(os.path.join(workdir, "shadow", "ledger.jsonl")),
        review_queue=SH.ManualReviewQueue(os.path.join(workdir, "shadow",
                                                       "reviews.jsonl")),
        takeover_ledger=TK.TakeoverLedger(os.path.join(workdir, "takeover.jsonl")),
        incident_dir=os.path.join(workdir, "incidents"),
        trace_db=os.path.join(workdir, "tool_trace.db"),
        isolation_executor=executor, isolation_plan=plan,
        env=dict(env), emit_events=emit)


class AlwaysFailingExecutor(ISO.IsolationExecutor):
    """第四幕专用：持续失败的"候选执行"（模拟候选实现本身崩掉）

    **它不是假证据**：前两幕的真实边界由真实执行器给出；这里只是把"失败"
    这个输入稳定地喂给回退逻辑，好让"连续 N 次失败 ⇒ 回落"能被复现地演示。
    """

    level = ISO.ISOLATION_SUBPROCESS_HARDENED

    def run(self, job: Dict[str, Any]) -> ISO.IsolationResult:
        return ISO.IsolationResult(
            level=self.level, job_id=str(job.get("job_id") or ""), ran=True,
            status=ISO.STATUS_ERROR, error_code="E_DEMO_CANDIDATE_CRASH",
            error="演示夹具：候选实现在隔离边界内崩了",
            steps=[str(s.get("op")) for s in (job.get("steps") or [])],
            outputs=[{"ok": False, "error": "candidate crashed"}],
            side_effects={"files_written": [], "files_deleted": [],
                          "external_calls": []},
            duration_ms=1.0, wall_ms=2.0, exit_code=1,
            isolation={"level": self.level, "demo": True})


# ════════════════════════════════════════════════════════════
#  四幕
# ════════════════════════════════════════════════════════════


def act1_environment(args: argparse.Namespace) -> Dict[str, Any]:
    print("=" * 72)
    print("① 环境与隔离等级（**如实标注**：不可用即降级并写明理由）")
    print("=" * 72)
    ISO.reset_isolation_probe_cache()
    docker = ISO.probe_docker(refresh=True).to_dict()
    plan = ISO.resolve_isolation_level(requested=args.level or "",
                                       prober=None if args.level else None)
    print(json.dumps({"docker": docker, "plan": plan.to_dict()},
                     ensure_ascii=False, indent=2)[:2400])
    print(f"\n生效等级：{plan.level}（{plan.boundaries['display']}）")
    for reason in plan.reasons:
        print(f"  · {reason}")
    print("\n**不保证的边界**（诚实清单）：")
    for gap in plan.boundaries["not_guaranteed"]:
        print(f"  ✗ {gap}")
    #: **把决议对象本身带出去**（而不是只带 level）：后两幕要用同一份决议，
    #: 否则声明里的 docker/降级理由会丢，报告就少了一截证据
    return {"docker": docker, "plan": plan.to_dict(), "_plan": plan}


def act2_takeover(args: argparse.Namespace, workdir: str,
                  plan: ISO.IsolationPlan) -> Dict[str, Any]:
    print("\n" + "=" * 72)
    print("② 隔离执行 + 灰度记录 + 真实接管（在隔离边界内）")
    print("=" * 72)
    case_set, _store, gate_result = build_signed_case_set(workdir, args.cases)
    print(f"判定集：{case_set.size} 组｜验收门：passed={gate_result.passed}"
          f"（{gate_result.passport.get('passport_id') if hasattr(gate_result, 'passport') else '-'}）")
    if not gate_result.passed:
        print(f"⚠️ 未获通行证 ⇒ 灰度会被门挡住：{gate_result.reasons()[:3]}")

    executor = ISO.executor_for(plan, keep_work_dir=args.keep_work)
    env = dict({TK.ENV_REAL_TAKEOVER: "true",
                TK.ENV_TAKEOVER_RATIO: str(args.ratio)})
    runner = make_runner(workdir, executor=executor, plan=plan, env=env, emit=True)
    report = runner.run(CAP, case_set=case_set, force=True, daily_avg=args.daily_avg,
                        shadow_config={"enabled": True, "gray_ratio": args.ratio},
                        write_ledger=False, enqueue_manual=False)
    print(report.markdown())
    print("接管报告：")
    print(json.dumps({k: v for k, v in report.takeover.items() if k != "attempts"},
                     ensure_ascii=False, indent=2)[:1800])
    for attempt in (report.takeover.get("attempts") or [])[:3]:
        print(f"  · {attempt['case_id']}: status={attempt['status']} "
              f"matched={attempt['matched']} failed={attempt['failed']} "
              f"adopted={attempt['adopted']}")
    return {"gate_passed": bool(gate_result.passed),
            "shadow": report.to_dict(include_samples=True),
            "takeover": report.takeover}


def act3_no_double_write(witness_dir: str, before: Dict[str, Any]) -> Dict[str, Any]:
    print("\n" + "=" * 72)
    print("③ 未双写实测（真实环境前后指纹比对）")
    print("=" * 72)
    after = ISO.snapshot_paths([witness_dir])
    diff = ISO.diff_snapshot(before, after)
    print(f"见证目录：{witness_dir}")
    print(f"前后差异：{json.dumps(diff, ensure_ascii=False)}")
    print("判定：" + ("**未被改动** ✅（副作用只记录不双写）" if diff["unchanged"]
                    else f"**被改动了** ❌ {diff}"))
    return diff


def act4_fallback(args: argparse.Namespace, workdir: str,
                  plan: ISO.IsolationPlan) -> Dict[str, Any]:
    print("\n" + "=" * 72)
    print("④ 回退验证（连续失败 ⇒ 自动回落 sandbox_replay_only + 事故卡）")
    print("=" * 72)
    case_set, _store, gate_result = build_signed_case_set(workdir, args.cases)
    print(f"验收门：passed={gate_result.passed}")
    failing = ISO.IsolationPlan(level=(
        plan.level if plan.level != ISO.ISOLATION_IN_PROCESS
        else ISO.ISOLATION_SUBPROCESS_HARDENED))
    env = dict({TK.ENV_REAL_TAKEOVER: "true",
                TK.ENV_TAKEOVER_RATIO: str(args.ratio),
                TK.ENV_FAIL_THRESHOLD: str(args.fail_threshold)})
    runner = make_runner(workdir, executor=AlwaysFailingExecutor(), plan=failing,
                         env=env, emit=True)
    rounds: List[Dict[str, Any]] = []
    for index in range(args.fail_threshold):
        report = runner.run(CAP, case_set=case_set, force=True,
                            daily_avg=args.daily_avg,
                            shadow_config={"enabled": True,
                                           "gray_ratio": args.ratio},
                            write_ledger=False, enqueue_manual=False)
        rounds.append({"round": index + 1,
                       "executed": report.takeover["executed"],
                       "failed": report.takeover["failed"],
                       "consecutive_failures": report.takeover["consecutive_failures"],
                       "fallback": report.takeover["fallback"],
                       "fallback_transport": report.takeover["fallback_transport"],
                       "incident_id": report.takeover["incident_id"]})
        print(f"  第 {index + 1} 轮：执行 {rounds[-1]['executed']}／失败 "
              f"{rounds[-1]['failed']}｜连续失败 {rounds[-1]['consecutive_failures']}"
              f"｜回落 {rounds[-1]['fallback']}"
              + (f" → `{rounds[-1]['fallback_transport']}`"
                 f"（事故卡 {rounds[-1]['incident_id']}）"
                 if rounds[-1]["fallback"] else ""))
    last = rounds[-1]
    incident_path = os.path.join(workdir, "incidents", f"{last['incident_id']}.json")
    card: Dict[str, Any] = {}
    if last["incident_id"] and os.path.exists(incident_path):
        with open(incident_path, encoding="utf-8") as fh:
            card = json.load(fh)
        print(f"  事故卡：{incident_path}")
        print(f"    严重级别={card.get('severity')}｜回落目标="
              f"{(card.get('detail') or {}).get('fallback_transport')}")
    assert last["fallback"], "连续失败未触发回落 —— 演示失败"
    print("判定：**连续失败已触发自动回落 + 事故卡** ✅")
    return {"rounds": rounds, "incident": card,
            "ledger": runner.takeover_ledger.rows(CAP)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="TASK-S8-03 端到端演示")
    parser.add_argument("--level", default="",
                        help="强制隔离等级（留空=auto 探测）")
    parser.add_argument("--cases", type=int, default=8, help="灰度用例数")
    parser.add_argument("--ratio", type=float, default=0.99, help="灰度/接管抽样比例")
    parser.add_argument("--daily-avg", type=float, default=100.0, help="日均流量")
    parser.add_argument("--fail-threshold", type=int, default=3, help="连续失败阈值")
    parser.add_argument("--keep-work", action="store_true", help="保留隔离工作目录")
    parser.add_argument("--out-dir", default=OUT_DIR, help="产物目录（gitignore）")
    args = parser.parse_args(list(argv) if argv is not None else None)

    started = time.time()
    workdir = tempfile.mkdtemp(prefix="cp-s803-demo-")
    os.makedirs(args.out_dir, exist_ok=True)
    #: 见证目录模拟"真实环境"（宿主工作区）：**演示的就是它不该被动到**
    witness_dir = os.path.join(_ROOT, WITNESS_DIRNAME)
    shutil.rmtree(witness_dir, ignore_errors=True)
    os.makedirs(witness_dir, exist_ok=True)
    sentinel = os.path.join(witness_dir, "real-env-sentinel.txt")
    with open(sentinel, "w", encoding="utf-8") as fh:
        fh.write("REAL-ENV-PRISTINE（真实环境哨兵：演示期间不得被改动）")
    nested = os.path.join(witness_dir, "nested")
    os.makedirs(nested, exist_ok=True)
    with open(os.path.join(nested, "keep.txt"), "w", encoding="utf-8") as fh:
        fh.write("keep")
    witness_before = ISO.snapshot_paths([witness_dir])

    evidence: Dict[str, Any] = {
        "task": "TASK-S8-03", "act": "端到端演示",
        "generated_at_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "platform": {"sys_platform": sys.platform,
                     "python": sys.version.split()[0]},
    }
    try:
        evidence["act1_environment"] = act1_environment(args)
        plan = evidence["act1_environment"].pop("_plan")
        evidence["act2_takeover"] = act2_takeover(args, workdir, plan)
        evidence["act3_no_double_write"] = act3_no_double_write(witness_dir,
                                                               witness_before)
        evidence["act4_fallback"] = act4_fallback(args, workdir, plan)
    finally:
        shutil.rmtree(witness_dir, ignore_errors=True)
        shutil.rmtree(workdir, ignore_errors=True)

    json_path = os.path.join(args.out_dir, "demo_e2e_evidence.json")
    md_path = os.path.join(args.out_dir, "demo_e2e_summary.md")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_summary(evidence, elapsed=time.time() - started))
    print("\n" + "=" * 72)
    print(f"证据 JSON → {json_path}")
    print(f"摘要 MD   → {md_path}")
    print("=" * 72)
    return 0


def render_summary(evidence: Dict[str, Any], *, elapsed: float) -> str:
    act1 = evidence.get("act1_environment") or {}
    act2 = evidence.get("act2_takeover") or {}
    act3 = evidence.get("act3_no_double_write") or {}
    act4 = evidence.get("act4_fallback") or {}
    takeover = act2.get("takeover") or {}
    isolation = ((act2.get("shadow") or {}).get("isolation")) or {}
    lines = [
        "# TASK-S8-03 端到端演示摘要",
        "",
        f"- 生成时间：{evidence.get('generated_at_iso')}｜耗时 {elapsed:.1f}s",
        f"- 平台：{evidence.get('platform', {}).get('sys_platform')} / "
        f"Python {evidence.get('platform', {}).get('python')}",
        "",
        "## ① 环境与隔离等级（如实标注）",
        "",
        f"- 生效等级：`{act1.get('plan', {}).get('level')}`"
        f"（来源 `{act1.get('plan', {}).get('source')}`，"
        f"降级={act1.get('plan', {}).get('downgraded')}）",
        f"- Docker：available={act1.get('docker', {}).get('available')}"
        f"（server {act1.get('docker', {}).get('server_version') or '-'}）",
    ]
    for gap in (act1.get("plan", {}).get("not_guaranteed") or [])[:4]:
        lines.append(f"- ✗ 不保证：{gap}")
    lines += [
        "",
        "## ② 隔离执行 + 灰度记录 + 真实接管",
        "",
        f"- 验收门通过：{act2.get('gate_passed')}",
        f"- 执行模型：`{isolation.get('mode')}`｜真实接管："
        f"{isolation.get('real_takeover')}｜容器隔离："
        f"{isolation.get('container_isolated')}",
        f"- 接管：开启={takeover.get('enabled')}｜预算={takeover.get('budget')}"
        f"｜执行={takeover.get('executed')}｜一致={takeover.get('matched')}"
        f"｜失败={takeover.get('failed')}｜**adopted={takeover.get('adopted')}**",
        "",
        "## ③ 未双写实测",
        "",
        f"- 真实环境前后差异：`{json.dumps(act3, ensure_ascii=False)}`",
        f"- 判定：{'**未被改动** ✅' if act3.get('unchanged') else '**被改动** ❌'}",
        "",
        "## ④ 回退验证",
        "",
    ]
    for row in act4.get("rounds") or []:
        lines.append(f"- 第 {row['round']} 轮：执行 {row['executed']}／失败 "
                     f"{row['failed']}｜连续失败 {row['consecutive_failures']}"
                     f"｜回落 {row['fallback']}"
                     + (f" → `{row['fallback_transport']}`"
                        f"（事故卡 {row['incident_id']}）"
                        if row["fallback"] else ""))
    lines += ["", "> 本摘要由 `scripts/demo_s8_03_isolation.py` 生成；"
                  "四幕全部为**实测**，未运行的部分不补写。"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
