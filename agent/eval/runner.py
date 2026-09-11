"""评测执行器：`run_l0()` 等分层运行器 + 基线对照（TASK-S5-02 / v7.2 §6.5）

## 三层状态口径（**不许把"无法评测"混进"通过"**）

===========  ==========================================================
status       语义
===========  ==========================================================
``pass``     全部判定条目通过（且用例 `verdict_kind` 为 mechanical/proxy）
``fail``     至少一条判定条目失败（含"答案缺少字段"）
``error``    解算器或判定器抛异常（异常即失败，但单独计数以便定位）
``unassessed`` 解算器返回 ``None``（无凭证/未接线/标记 unsupported）——**不计入通过率分母**
===========  ==========================================================

`pass_rate = pass / (pass + fail + error)`，并在报告里同时给出
``assessed``（分母）与 ``unassessed``（未评测）——"未评测"必须看得见。

## 基线对照

`compare_to_baseline()` 把本次运行与冻结的层基线逐条比对，输出
**回归（baseline pass → 现在 fail/error/unassessed）/ 改进 / 新增 / 消失**，
并在用例集哈希变化时标记 ``anchor_changed``（此时对照**不可信**，只做披露）。

## clock 口径

耗时一律用 ``time.perf_counter``（单调墙钟，`CLOCK_WALL`）并在报告中标注；
**不做硬编码墙钟断言**（任务书 §八 #2：CI 覆盖率插桩抖动下墙钟不可比）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from agent.eval import anchor as A
from agent.eval import cases as C
from agent.eval import checkers as K
from agent.eval import solvers as S

logger = logging.getLogger("agent.eval.runner")

#: 耗时 clock 口径（报告逐处标注）
CLOCK_WALL = "wall_clock(perf_counter)"

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_ERROR = "error"
STATUS_UNASSESSED = "unassessed"
STATUSES: Tuple[str, ...] = (STATUS_PASS, STATUS_FAIL, STATUS_ERROR, STATUS_UNASSESSED)

#: 层默认用例集路径（L0 走锚存储，见 `run_l0`）
LAYER_CASESET_PATHS: Dict[str, str] = {
    C.LAYER_L1: os.path.join(A._REPO_ROOT, "eval", "l1_min", "cases.json"),
    C.LAYER_L2: os.path.join(A._REPO_ROOT, "eval", "l2_core50", "cases.json"),
    C.LAYER_L3: os.path.join(A._REPO_ROOT, "eval", "l3_golden80", "cases.json"),
}
#: 层基线默认落点（`eval/baselines/`，随代码版本走）
LAYER_BASELINE_DIR = os.path.join(A._REPO_ROOT, "eval", "baselines")

#: L3 框架契约（本任务只定义框架；实际扩充入 M7+）
L3_FRAMEWORK: Dict[str, Any] = {
    "layer": C.LAYER_L3,
    "name": "Golden-80",
    "status": "framework_only",
    "directory": "eval/l3_golden80/",
    "caseset": "eval/l3_golden80/cases.json",
    "runner": "agent.eval.runner.run_l3()（接口与 L0/L1/L2 同构：run_layer(layer)）",
    "cli": "python scripts/run_eval.py --layer L3",
    "target_size": 80,
    "cadence": {
        "phase": "W15+ / M7+（设计文档 §6.5：L3 Golden-80 延后）",
        "trigger": "发布前 + 每月一次（与 §6.6 baseline_remeasure 的 weekly 口径同源扩展）",
        "baseline_remeasure_event": "baseline_remeasure {weekly, core50_cost, golden_set_pass_rate}",
    },
    "promotion_rule": ("L2 Core-50 连续 2 个窗口全绿且样本量达标 → 由 L2 抽取代表用例升入 L3，"
                       "并保留 L2 原始证据链（case_id 不变，层前缀升级）"),
    "isolation": ("L3 用例同样独立于系统数据目录（eval/ 根下），并复用 L0 的哈希锚定机制"
                  "（freeze_anchor 可直接对 l3_golden80 目录执行）"),
}


class RunnerError(ValueError):
    """执行器错误（层非法 / 用例集缺失 / 基线非法）"""


# ════════════════════════════════════════════════════════════
#  结果对象
# ════════════════════════════════════════════════════════════


@dataclass
class CaseResult:
    """单条用例结果"""

    case_id: str
    layer: str
    scenario: str
    title: str = ""
    verdict_kind: str = C.VERDICT_MECHANICAL
    status: str = STATUS_UNASSESSED
    checks: List[Dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0
    note: str = ""
    answer_present: bool = False

    @property
    def passed(self) -> bool:
        return self.status == STATUS_PASS

    @property
    def assessed(self) -> bool:
        return self.status != STATUS_UNASSESSED

    @property
    def mechanical(self) -> bool:
        return bool(self.checks) and all(bool(c.get("mechanical")) for c in self.checks)

    @property
    def failed_checks(self) -> List[Dict[str, Any]]:
        return [c for c in self.checks if not c.get("passed")]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id, "layer": self.layer, "scenario": self.scenario,
            "title": self.title, "verdict_kind": self.verdict_kind,
            "status": self.status, "passed": self.passed, "assessed": self.assessed,
            "mechanical": self.mechanical, "duration_ms": round(self.duration_ms, 3),
            "checks": [dict(c) for c in self.checks], "note": self.note,
            "answer_present": self.answer_present,
        }


@dataclass
class EvalReport:
    """一次分层评测的完整结果（可 JSON 序列化；供验收报告/周报/面板消费）"""

    layer: str
    solver: str = ""
    caseset_path: str = ""
    caseset_sha256: str = ""
    clock: str = CLOCK_WALL
    started_at: float = 0.0
    duration_ms: float = 0.0
    results: List[CaseResult] = field(default_factory=list)
    disclosures: List[str] = field(default_factory=list)
    baseline: Dict[str, Any] = field(default_factory=dict)
    integrity: Dict[str, Any] = field(default_factory=dict)
    framework: Dict[str, Any] = field(default_factory=dict)

    # ── 汇总 ────────────────────────────────────────────────

    @property
    def total(self) -> int:
        return len(self.results)

    def counts(self) -> Dict[str, int]:
        out = {status: 0 for status in STATUSES}
        for result in self.results:
            out[result.status] = out.get(result.status, 0) + 1
        return out

    @property
    def assessed(self) -> int:
        return sum(1 for r in self.results if r.assessed)

    @property
    def unassessed(self) -> int:
        return sum(1 for r in self.results if not r.assessed)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def pass_rate(self) -> Optional[float]:
        """通过率（分母 = 已评测条数）；无已评测用例 → ``None``（不虚报 0%）"""
        return round(self.passed / self.assessed, 6) if self.assessed else None

    def by_scenario(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for result in self.results:
            row = out.setdefault(result.scenario, {"total": 0, "assessed": 0,
                                                   "passed": 0, "statuses": {}})
            row["total"] += 1
            row["assessed"] += 1 if result.assessed else 0
            row["passed"] += 1 if result.passed else 0
            row["statuses"][result.status] = row["statuses"].get(result.status, 0) + 1
        for row in out.values():
            row["pass_rate"] = (round(row["passed"] / row["assessed"], 6)
                                if row["assessed"] else None)
        return dict(sorted(out.items()))

    def p99_wall_ms(self) -> float:
        """本次运行的用例耗时 p99（**单调墙钟**口径；样本 <100 取最大值）"""
        values = sorted(r.duration_ms for r in self.results)
        if not values:
            return 0.0
        if len(values) < 100:
            return round(values[-1], 3)
        return round(values[min(len(values) - 1, int(0.99 * len(values)))], 3)

    def failures(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self.results if r.status in (STATUS_FAIL, STATUS_ERROR)]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer": self.layer, "solver": self.solver,
            "caseset_path": self.caseset_path, "caseset_sha256": self.caseset_sha256,
            "clock": self.clock, "started_at": self.started_at,
            "duration_ms": round(self.duration_ms, 3),
            "total": self.total, "counts": self.counts(),
            "assessed": self.assessed, "unassessed": self.unassessed,
            "passed": self.passed, "pass_rate": self.pass_rate,
            "p99_wall_ms": self.p99_wall_ms(),
            "by_scenario": self.by_scenario(),
            "results": [r.to_dict() for r in self.results],
            "disclosures": list(self.disclosures),
            "baseline": dict(self.baseline),
            "integrity": dict(self.integrity),
            "framework": dict(self.framework),
        }

    def markdown(self) -> str:
        """人类可读摘要（验收报告/CI 日志直接用；口径逐项标注）"""
        counts = self.counts()
        rate = "n/a" if self.pass_rate is None else f"{self.pass_rate:.4f}"
        lines = [
            f"# {self.layer} 评测结果（solver=`{self.solver}`）",
            "",
            f"- 用例集：`{self.caseset_path}`（sha256 `{self.caseset_sha256[:12]}`）",
            f"- solver：`{self.solver}`｜clock 口径：{self.clock}",
            f"- 总数 {self.total}｜pass {counts[STATUS_PASS]}｜fail {counts[STATUS_FAIL]}"
            f"｜error {counts[STATUS_ERROR]}｜unassessed {counts[STATUS_UNASSESSED]}",
            f"- 通过率 {rate}（分母 = 已评测 {self.assessed} 条；未评测 {self.unassessed} 条不计入）",
            f"- 用例耗时 p99 = {self.p99_wall_ms()} ms（{self.clock}）",
        ]
        if self.baseline:
            lines.append(f"- 基线对照：{self.baseline.get('status')}"
                         f"（回归 {len(self.baseline.get('regressions', []))} 条）")
        if self.framework:
            lines.append(f"- 层框架：{self.framework.get('name')}"
                         f"（status={self.framework.get('status')}，"
                         f"目标规模 {self.framework.get('target_size')}，"
                         f"周期 {dict(self.framework.get('cadence') or {}).get('phase')}）")
        if self.disclosures:
            lines += ["", "**披露**"] + [f"- {d}" for d in self.disclosures]
        lines += ["", "| 场景 | 总数 | 已评测 | 通过 | 通过率 |", "|---|---|---|---|---|"]
        for scenario, row in self.by_scenario().items():
            rate_cell = "n/a" if row["pass_rate"] is None else f"{row['pass_rate']:.4f}"
            lines.append(f"| {scenario} | {row['total']} | {row['assessed']} | "
                         f"{row['passed']} | {rate_cell} |")
        failures = self.failures()
        if failures:
            lines += ["", "**失败明细（前 10）**", ""]
            for item in failures[:10]:
                reasons = "; ".join(f"{c['checker']}({c['path']}): {c['detail']}"
                                    for c in item["checks"] if not c["passed"]) or item["note"]
                lines.append(f"- `{item['case_id']}` [{item['status']}] {reasons[:400]}")
        return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  执行
# ════════════════════════════════════════════════════════════


def run_case(case: C.EvalCase, solver: S.Solver, *, repo_root: str = K.REPO_ROOT,
             solver_name: str = "") -> CaseResult:
    """执行单条用例（解算 → 判定）"""
    result = CaseResult(case_id=case.id, layer=case.layer, scenario=case.scenario,
                        title=case.title, verdict_kind=case.verdict_kind)
    started = time.perf_counter()
    if case.verdict_kind == C.VERDICT_UNSUPPORTED:
        result.status = STATUS_UNASSESSED
        result.note = case.notes or "用例标记为本环境不可判定（unsupported）"
        result.duration_ms = (time.perf_counter() - started) * 1000.0
        return result
    try:
        answer = solver(case)
    except Exception as e:  # noqa: BLE001 解算器异常 → error（异常即失败，单独计数）
        result.status = STATUS_ERROR
        result.note = f"解算器异常: {type(e).__name__}: {e}"
        result.duration_ms = (time.perf_counter() - started) * 1000.0
        return result
    if answer is None:
        result.status = STATUS_UNASSESSED
        result.note = (case.notes or
                       f"解算器 `{solver_name or 'n/a'}` 未产出答案（如实标注为未评测）")
        result.duration_ms = (time.perf_counter() - started) * 1000.0
        return result
    if not isinstance(answer, Mapping):
        result.status = STATUS_ERROR
        result.note = f"答案工件必须是对象，得到 {type(answer).__name__}"
        result.duration_ms = (time.perf_counter() - started) * 1000.0
        return result

    ctx = K.CheckContext(repo_root=repo_root, case_id=case.id, layer=case.layer,
                         case=case, extra={"solver": solver_name})
    checks = [K.run_check(answer, check, ctx) for check in case.expect]
    result.checks = checks
    result.answer_present = True
    result.status = STATUS_PASS if all(c["passed"] for c in checks) else STATUS_FAIL
    result.duration_ms = (time.perf_counter() - started) * 1000.0
    return result


def run_layer(layer: str, *, case_set: Optional[C.EvalCaseSet] = None,
              caseset_path: str = "", solver: Optional[S.Solver] = None,
              solver_name: str = "", repo_root: str = K.REPO_ROOT,
              compare_baseline: bool = False, baseline_path: str = "",
              store: Optional[A.AnchorStore] = None,
              disclosures: Optional[Sequence[str]] = None) -> EvalReport:
    """按层执行评测（L1/L2/L3 走用例集文件；L0 请用 `run_l0`：它会额外做锚校验）"""
    layer = str(layer or "").upper()
    if layer not in C.LAYERS:
        raise RunnerError(f"未知层: {layer!r}（允许 {C.LAYERS}）")
    if case_set is None:
        path = caseset_path or LAYER_CASESET_PATHS.get(layer, "")
        if not path or not os.path.exists(path):
            raise RunnerError(f"{layer} 用例集不存在: {path!r}")
        if layer == C.LAYER_L0 and store is None:
            store = A.AnchorStore(os.path.dirname(path))
        case_set = store.load() if store is not None else C.load_case_set(path)
    if solver is None:
        solve, solver_name = S.null_solver()
    else:
        solve = solver

    report = EvalReport(layer=layer, solver=solver_name or "custom",
                        caseset_path=case_set.path or caseset_path,
                        caseset_sha256=case_set.caseset_sha256,
                        started_at=time.time())
    started = time.perf_counter()
    for case in case_set.cases:
        report.results.append(run_case(case, solve, repo_root=repo_root,
                                       solver_name=report.solver))
    report.duration_ms = (time.perf_counter() - started) * 1000.0
    report.disclosures.extend(disclosures or [])
    if compare_baseline:
        target = baseline_path or default_baseline_path(layer)
        baseline = load_baseline(target)
        report.baseline = compare_to_baseline(report, baseline)
    return report


def run_l0(*, store: Optional[A.AnchorStore] = None, root: str = "",
           reference: bool = False, solver: Optional[S.Solver] = None,
           solver_name: str = "", repo_root: str = K.REPO_ROOT,
           compare_baseline: bool = True, baseline_path: str = "") -> EvalReport:
    """执行 L0 锚（**fail-closed**：锚完整性校验失败直接拒绝运行）

    默认解算器为 `null_solver` —— 即"未评测"，因为 L0 的意义是给出**客观标尺**，
    标尺本身不需要假装被测系统已经能跑；要验证标尺（判定器）可用，
    用 ``reference=True``（参考解自检）或 ``solver=mutant_solver(...)``（区分度对照）。
    """
    anchor_store = store or A.AnchorStore(root)
    case_set = anchor_store.load(verify=True)  # 完整性失败 → AnchorIntegrityError
    disclosures: List[str] = [
        "L0 锚为人工冻结用例，存储独立于系统数据目录；哈希锚定，自动化流程无权修改",
        f"clock 口径：{CLOCK_WALL}（报告内所有耗时均为此口径）",
    ]
    if reference:
        solve, solver_name = S.reference_solver(anchor_store.load_reference())
        disclosures.append(
            "本次运行使用**参考解**（锚内冻结答案）：验证的是判定器与管道本身，"
            "**不代表任何模型能力**；真实能力评测须由被测解算器产出答案工件")
    elif solver is None:
        solve, solver_name = S.null_solver()
        disclosures.append(
            "未提供被测解算器（本环境可能无真实 LLM 凭证）→ 全部用例如实标注为"
            "「未评测」，不计入通过率分母；可用 --reference 验证判定器，"
            "或用 --solver file:<answers.json> 传入被测答案")
    else:
        solve = solver
    report = run_layer(C.LAYER_L0, case_set=case_set, solver=solve,
                       solver_name=solver_name, repo_root=repo_root,
                       compare_baseline=compare_baseline, baseline_path=baseline_path,
                       disclosures=disclosures)
    report.integrity = anchor_store.integrity()
    return report


def run_l1(**kwargs: Any) -> EvalReport:
    """执行 L1 最小集（10 条快路径回归）"""
    kwargs.setdefault("layer", C.LAYER_L1)
    return run_layer(**kwargs)


def run_l2(**kwargs: Any) -> EvalReport:
    """执行 L2 Core-50（**UTC 基线唯一依据**）"""
    kwargs.setdefault("layer", C.LAYER_L2)
    return run_layer(**kwargs)


def run_l3(**kwargs: Any) -> EvalReport:
    """执行 L3 Golden-80（本任务只交付框架；用例集为空时返回框架声明）"""
    kwargs.setdefault("layer", C.LAYER_L3)
    framework_disclosure = (
        "L3 Golden-80 为**框架占位**（§6.5：W15+/M7+ 扩充）；本任务只定义"
        "目录/运行器/周期与从 L2 的晋升规则，不产出 80 条用例")
    path = kwargs.get("caseset_path") or LAYER_CASESET_PATHS[C.LAYER_L3]
    if not os.path.exists(path):
        report = EvalReport(layer=C.LAYER_L3, solver="n/a", caseset_path=path,
                            caseset_sha256="", framework=dict(L3_FRAMEWORK))
        report.disclosures.append(framework_disclosure)
        return report
    report = run_layer(**kwargs)
    report.framework = dict(L3_FRAMEWORK)
    if framework_disclosure not in report.disclosures:
        report.disclosures.append(framework_disclosure)
    return report


def l3_framework() -> Dict[str, Any]:
    """L3 框架契约（目录 / 运行器 / 周期 / 晋升规则）"""
    return dict(L3_FRAMEWORK)


# ════════════════════════════════════════════════════════════
#  基线
# ════════════════════════════════════════════════════════════


def default_baseline_path(layer: str) -> str:
    """层基线默认路径：``eval/baselines/<layer>_baseline.json``"""
    return os.path.join(LAYER_BASELINE_DIR, f"{str(layer).lower()}_baseline.json")


#: 各层参考解默认落点（仅用于**判定器自检**，不代表模型能力）
LAYER_REFERENCE_PATHS: Dict[str, str] = {
    C.LAYER_L0: os.path.join(A.DEFAULT_ANCHOR_DIR, A.REFERENCE_FILENAME),
    C.LAYER_L1: os.path.join(A._REPO_ROOT, "eval", "l1_min", "reference.json"),
    C.LAYER_L2: os.path.join(A._REPO_ROOT, "eval", "l2_core50", "reference.json"),
}


def load_layer_reference(layer: str, path: str = "") -> Dict[str, Dict[str, Any]]:
    """读取某层同目录的 ``reference.json``（缺失 → 空字典）

    参考解用于**判定器自检**与**变异解对照**；真实能力评测必须由被测解算器
    产出答案工件（本函数的返回值不得当作模型成绩）。
    """
    target = path or LAYER_REFERENCE_PATHS.get(str(layer).upper(), "")
    if not target or not os.path.exists(target):
        return {}
    with open(target, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    answers = data.get("answers") if isinstance(data, Mapping) and isinstance(
        data.get("answers"), Mapping) else data
    if not isinstance(answers, Mapping):
        return {}
    return {str(k): dict(v) if isinstance(v, Mapping) else {"value": v}
            for k, v in answers.items()}


def reference_self_check(layer: str, *, path: str = "", **kwargs: Any) -> EvalReport:
    """用该层参考解做一次**判定器自检**（报告里带"不代表模型能力"披露）"""
    kwargs.setdefault("layer", str(layer).upper())
    solve, name = S.reference_solver(load_layer_reference(layer, path))
    report = run_layer(solver=solve, solver_name=name, **kwargs)
    report.disclosures.append(
        "本次运行使用该层**参考解**：验证的是用例与判定器本身"
        "（参考解全过 + 变异解全负 = 判定器有区分度），**不代表任何模型能力**")
    return report


def baseline_payload(report: EvalReport, *, note: str = "") -> Dict[str, Any]:
    """把一次运行固化为基线负载（逐条状态 + 用例集哈希 + 口径）"""
    return {
        "schema": "eval.baseline.v1",
        "layer": report.layer, "solver": report.solver,
        "caseset_path": report.caseset_path,
        "caseset_sha256": report.caseset_sha256,
        "clock": report.clock,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total": report.total, "assessed": report.assessed,
        "unassessed": report.unassessed, "passed": report.passed,
        "pass_rate": report.pass_rate, "counts": report.counts(),
        "by_scenario": report.by_scenario(), "p99_wall_ms": report.p99_wall_ms(),
        "results": {r.case_id: r.status for r in report.results},
        "mechanical_cases": sum(1 for r in report.results if r.mechanical),
        "disclosures": list(report.disclosures),
        "note": note,
    }


def write_baseline(report: EvalReport, path: str = "", *, note: str = "") -> str:
    """写基线文件（**默认不写到锚目录**：先过 `anchor.guard_write`）"""
    target = path or default_baseline_path(report.layer)
    A.guard_write(target)
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = baseline_payload(report, note=note)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return target


def load_baseline(path: str) -> Dict[str, Any]:
    """读基线（缺失/非法 → 空字典 = "无基线"，对照函数会如实标注）"""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("基线文件非法（%s）: %s", path, e)
        return {}
    return dict(data) if isinstance(data, Mapping) else {}


def compare_to_baseline(report: EvalReport, baseline: Mapping[str, Any]) -> Dict[str, Any]:
    """本次运行 vs 基线 → 回归/改进/新增/消失（用例集哈希变化时只披露不判定）

    * ``regressions``：基线上 **pass**、本次 **fail/error**（真回归）；
    * ``unassessed_drop``：基线上 pass、本次 **unassessed**（换了"没有解算器"的运行，
      属于**未评测**而不是回归，单独列出以免把"没跑"混进"跑坏了"）；
    * ``anchor_changed``：用例集哈希变化时逐条对照**不可信**（用例改了），只披露。
    """
    if not baseline:
        return {"status": "baseline_missing", "regressions": [], "improvements": [],
                "new_cases": [], "removed_cases": [], "anchor_changed": False,
                "unassessed_drop": [],
                "note": "无基线可对照（首次运行请用 --record-baseline 固化）"}
    base_results = dict(baseline.get("results") or {})
    now_results = {r.case_id: r.status for r in report.results}
    anchor_changed = bool(baseline.get("caseset_sha256")) and \
        str(baseline.get("caseset_sha256")) != report.caseset_sha256
    regressions = [{"case_id": cid, "was": STATUS_PASS, "now": now_results[cid]}
                   for cid, status in sorted(base_results.items())
                   if status == STATUS_PASS and cid in now_results
                   and now_results[cid] in (STATUS_FAIL, STATUS_ERROR)]
    unassessed_drop = [{"case_id": cid, "was": STATUS_PASS, "now": STATUS_UNASSESSED}
                       for cid, status in sorted(base_results.items())
                       if status == STATUS_PASS and now_results.get(cid) == STATUS_UNASSESSED]
    improvements = [{"case_id": cid, "was": status, "now": now_results[cid]}
                    for cid, status in sorted(base_results.items())
                    if status != STATUS_PASS and now_results.get(cid) == STATUS_PASS]
    new_cases = sorted(set(now_results) - set(base_results))
    removed_cases = sorted(set(base_results) - set(now_results))
    status = "anchor_changed" if anchor_changed else ("ok" if not regressions else "regressed")
    return {
        "status": status, "anchor_changed": anchor_changed,
        "baseline_caseset_sha256": str(baseline.get("caseset_sha256") or ""),
        "caseset_sha256": report.caseset_sha256,
        "baseline_pass_rate": baseline.get("pass_rate"),
        "baseline_solver": baseline.get("solver"),
        "pass_rate": report.pass_rate,
        "regressions": regressions, "unassessed_drop": unassessed_drop,
        "improvements": improvements,
        "new_cases": new_cases, "removed_cases": removed_cases,
        "note": ("用例集哈希变化 → 逐条对照不可信（只披露）："
                 f"baseline={str(baseline.get('caseset_sha256') or '')[:12]} "
                 f"now={report.caseset_sha256[:12]}" if anchor_changed else
                 (f"基线 solver=`{baseline.get('solver')}`；本次 solver=`{report.solver}`"
                  "（不同解算器之间的对照只作参考）"
                  if baseline.get("solver") and baseline.get("solver") != report.solver
                  else "")),
    }


__all__ = [
    "CLOCK_WALL", "STATUSES", "STATUS_PASS", "STATUS_FAIL", "STATUS_ERROR",
    "STATUS_UNASSESSED", "LAYER_CASESET_PATHS", "LAYER_BASELINE_DIR", "L3_FRAMEWORK",
    "RunnerError", "CaseResult", "EvalReport", "run_case", "run_layer", "run_l0",
    "run_l1", "run_l2", "run_l3", "l3_framework", "default_baseline_path",
    "LAYER_REFERENCE_PATHS", "load_layer_reference", "reference_self_check",
    "baseline_payload", "write_baseline", "load_baseline", "compare_to_baseline",
]
