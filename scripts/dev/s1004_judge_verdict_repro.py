"""S10-04 复现/对照脚本：judge `verdict` 口径（结论前置 + 0.85 置信度门槛）

用法：
    python scripts/dev/s1004_judge_verdict_repro.py

干什么：把"规格 §4.5 逐字 vs 实现"最容易读反的**同输入对比**跑一遍，分五段：
    [A] 字段层   `LLMJudge.score()` 的 `verdict/model_verdict/conflict/score`
    [B] 层③门槛  `sandbox.diff_judge` 用**真实** `LLMJudge` 对象时的放行/拦截
    [C] 阈值边界 结论为等价时 0.84 / 0.85 / 0.86（原口径必须逐字不变）
    [D] 全链路   `ShadowRunner` 灰度 → `ShadowReport.pass_rate/negative` + 判定存档
    [E] 一致率   judge 判定 vs 人工裁定的 `judge_consistency`

看什么：`different + confidence=1.0`（"我 100% 确定它们不同"）必须判 **fail**；
旧口径（confidence 当相似度）会判 pass ⇒ 层③放行、`pass_rate=1.0/negative=0`
（**负例整批消失**）。详见 `docs/zh/CloudPivot_v7.2重构计划/TASK-S10-04_验收报告.md`。

副作用：**只读** agent 代码；运行时数据（事件/台账/判定存档）全部落临时目录，
不写 `data/`。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

TMP = tempfile.mkdtemp(prefix="s1004_")
# 事件（成本）一律落临时目录：本脚本**不得**写仓库的 data/events
os.environ["CP_EVENTS_DIR"] = os.path.join(TMP, "events")

from agent.digestion import shadow as SH            # noqa: E402
from agent.digestion import judge_runtime as JR     # noqa: E402
from agent.digestion import cases as C              # noqa: E402
from agent.digestion import gate as G               # noqa: E402
from agent.digestion.sandbox import Observation, diff_judge   # noqa: E402
from agent.observability import events as _ev       # noqa: E402

CAP = "cp.filesystem.write"


def reply(verdict: str, confidence: float, reason: str = "r") -> str:
    return json.dumps({"verdict": verdict, "confidence": confidence, "reason": reason},
                      ensure_ascii=False)


CASES = [
    ("different + conf=1.00", reply("different", 1.0, "步骤和副作用完全不同")),
    ("不等价  + conf=0.95", reply("不等价", 0.95, "上游读文件/候选删库")),
    ("equivalent + conf=0.95", reply("equivalent", 0.95, "步骤与输出一致")),
    ("equivalent + conf=0.50", reply("equivalent", 0.50, "不太确定")),
    ("different + conf=0.20", reply("different", 0.20, "细微不同")),
    ("legacy {score:0.90}", '{"score": 0.90, "reason": "legacy"}'),
]


def section(title: str) -> None:
    print()
    print("=" * 84)
    print(title)
    print("=" * 84)


def part_a() -> None:
    section("[A] 字段层：`LLMJudge.score()`（`parse_judge_verdict` 的结果）")
    print(f"{'输入':<24}{'verdict':<9}{'model_verdict':<15}{'conflict':<10}{'score':<8}")
    for label, text in CASES:
        judge = SH.LLMJudge(invoke=lambda prompt, _t=text: _t)
        res = judge.score("upstream-obs", "candidate-obs")
        print(f"{label:<24}{res['verdict']:<9}{str(res.get('model_verdict')):<15}"
              f"{str(res.get('conflict')):<10}{res['score']:<8}")


def part_b() -> None:
    section("[B] 层③门槛：`diff_judge` 用真实 `LLMJudge` 对象（只看一个浮点）")
    up = Observation(status="success", steps=["read_file", "write_file"],
                     outputs=[{"label": "result", "value": "报告已写入"}])
    cand = Observation(status="success", steps=["read_file", "write_file"],
                       outputs=[{"label": "result", "value": "数据库已删除"}])
    for label, text in CASES:
        judge = SH.LLMJudge(invoke=lambda prompt, _t=text: _t)
        layer = diff_judge(up, cand, judge=judge, judge_kind=lambda: judge.kind)
        print(f"{label:<24} layer3.passed={str(layer.passed):<7} score={layer.score:<8} "
              f"reasons={layer.reasons}")


def part_c() -> None:
    section("[C] 阈值边界：equivalent 下的 0.84 / 0.85 / 0.86")
    for conf in (0.84, 0.85, 0.86, 0.99):
        judge = SH.LLMJudge(invoke=lambda prompt, _c=conf: reply("equivalent", _c))
        r = judge.score("a", "b")
        print(f"equivalent conf={conf:<6} -> verdict={r['verdict']}")


def make_case(index: int) -> "C.EquivalenceCase":
    path = f"C:/sandbox/out/a{index}.txt"
    steps = [C.ProgramStep(label="read_file", params={"path": path}, capability_id=CAP),
             C.ProgramStep(label="write_file",
                           params={"path": path, "content": f"c{index}"},
                           capability_id=CAP)]
    return C.EquivalenceCase(
        case_id=f"case-{index:03d}", capability_id=CAP, input={"path": path},
        upstream=steps, native=steps, fixtures={path: f"c{index}"},
        expected_side_effects={"files_written": [path]},
        expected_status="success", sandbox_root="C:/sandbox")


def part_d() -> None:
    section("[D] 全链路灰度：judge 说 different+1.0 时读到的报告与判定存档")
    case_set = C.build_case_set(CAP, [make_case(i) for i in range(24)])
    store = G.PassportStore(os.path.join(TMP, "cases"))
    G.acceptance_gate(CAP, case_set=case_set, passport_store=store, emit_events=False)
    for label, text in [("different + conf=1.00", reply("different", 1.0, "完全不同")),
                        ("equivalent + conf=0.95", reply("equivalent", 0.95, "一致"))]:
        verdicts = JR.JudgeVerdictStore(
            os.path.join(TMP, "store", label.split()[0] + ".jsonl"))
        _ev.reset_event_stores()
        runtime = JR.build_judge_runtime(
            JR.JudgeConfig(enabled=True, provider="probe", model="gpt-4o-mini",
                           daily_budget_cents=100.0),
            env={}, invoke=lambda prompt, _t=text: _t,
            dotenv_path=os.path.join(TMP, "absent.env"),
            events_dir=os.path.join(TMP, "events"), verdict_store=verdicts,
            capability_id=CAP)
        runner = SH.ShadowRunner(
            judge_runtime=runtime, passport_store=store,
            case_store=C.open_case_store(os.path.join(TMP, "cases")),
            ledger=SH.ShadowLedger(os.path.join(TMP, "shadow", "l.jsonl")),
            review_queue=SH.ManualReviewQueue(os.path.join(TMP, "shadow", "r.jsonl")),
            env={}, emit_events=False)
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=40)
        summary = verdicts.summary(CAP)
        row = verdicts.rows()[0]
        print(f"{label:<24} judge_kind={report.judge_kind:<28} "
              f"pass_rate={report.pass_rate} negative={report.negative} "
              f"total={report.total}")
        print(f"{'':<24} 样本数={len(report.samples)}（**不得因判负例而丢样本**）"
              f" 判定存档 stored={summary['stored']} by_verdict={summary['by_verdict']}")
        print(f"{'':<24} 存档首行 verdict={row['verdict']!r} "
              f"model_verdict={row.get('model_verdict')!r} "
              f"threshold_verdict={row.get('threshold_verdict')!r} "
              f"confidence={row['confidence']} judge_score={row['judge_score']}")


def part_e() -> None:
    section("[E] 一致率统计：judge 判定（真实解析管线产物）vs 人工裁定")
    queue = SH.ManualReviewQueue(os.path.join(TMP, "cons", "r.jsonl"))
    store = JR.JudgeVerdictStore(os.path.join(TMP, "cons", "j.jsonl"))
    import inspect
    record_params = set(inspect.signature(JR.JudgeVerdictStore.record).parameters)
    # 人工裁定取语义真值：判"不同"的样本人工给 fail，判"等价"的给 pass
    pairs = [(reply("different", 1.0, "完全不同"), "fail"),
             (reply("equivalent", 0.95, "一致"), "pass")]
    for index, (text, human) in enumerate(pairs):
        case_id = f"case-{index:03d}"
        res = SH.LLMJudge(invoke=lambda prompt, _t=text: _t).score("a", "b")
        extra = {k: res.get(k, "") for k in ("model_verdict", "threshold_verdict")
                 if k in record_params}
        store.record(capability_id=CAP, case_id=case_id, verdict=res["verdict"],
                     confidence=res["confidence"], judge_kind="llm:probe:stub",
                     judge_score=res["score"], **extra)
        queue.record_review(case_id, capability_id=CAP, verdict=human,
                            reviewer="owner", role="human")
        print(f"case-{index:03d}: judge_verdict={res['verdict']!r} "
              f"(model={res.get('model_verdict')!r}, "
              f"threshold={res.get('threshold_verdict')!r}) / 人工={human!r}")
    report = JR.judge_consistency(verdict_store=store, review_queue=queue,
                                  capability_id=CAP)
    print(json.dumps({"samples": report["samples"], "agree": report["agree"],
                      "disagree": report["disagree"],
                      # `conflicted` 为 S10-04 新增字段（修复前不存在 ⇒ None）
                      "conflicted": report.get("conflicted"),
                      "agreement_rate": report["agreement_rate"],
                      "disagreements": [d["judge_verdict"] + " vs " + d["human_verdict"]
                                        for d in report["disagreements"]]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    part_a()
    part_b()
    part_c()
    part_d()
    part_e()
