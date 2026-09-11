"""评测数据自检门（TASK-S5-02）——L0–L3 用例集 + 锚 + 判定器区分度一键校验

用法::

    python scripts/check_eval_datasets.py                # 全量自检
    python scripts/check_eval_datasets.py --layer L0     # 只查某一层
    python scripts/check_eval_datasets.py --json         # 机器可读输出

自检项（对齐任务书 §四 验收清单）：

1. **契约**：每层用例集通过 `cases.validate_case_set`（规模 / 场景覆盖 / 判定器登记）；
2. **锚**：L0 锚完整性（逐条哈希 + 用例集哈希 + 参考解哈希 + 位置独立性）；
3. **参考解**：该层 reference.json 必须让**全部已评测用例 pass**（判定器可用性）；
4. **区分度**：**逐条判定条目**单独破坏后该用例必须判 fail（"判定器有没有在干活"），
   外加 `mutant(all_checks)` 全量负样本对照（pass 必须为 0）；
5. **层框架**：L3 框架契约可读（`run_l3()` 返回 framework 声明）。

退出码：0 = 全绿；1 = 存在失败项。**任何一项失败都不得"跳过"**——宁可红，不可假绿。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.eval import anchor as A  # noqa: E402
from agent.eval import cases as C  # noqa: E402
from agent.eval import runner as R  # noqa: E402
from agent.eval import solvers as S  # noqa: E402

REFERENCE_PATHS = {
    C.LAYER_L0: os.path.join(A.DEFAULT_ANCHOR_DIR, A.REFERENCE_FILENAME),
    C.LAYER_L1: os.path.join(A._REPO_ROOT, "eval", "l1_min", "reference.json"),
    C.LAYER_L2: os.path.join(A._REPO_ROOT, "eval", "l2_core50", "reference.json"),
}


def _load_answers(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    answers = data.get("answers") if isinstance(data, dict) and isinstance(
        data.get("answers"), dict) else data
    return dict(answers or {})


def _check_layer(layer: str, *, anchor_dir: str = "") -> dict:
    result: dict = {"layer": layer, "ok": False, "problems": []}
    try:
        if layer == C.LAYER_L0:
            store = A.AnchorStore(anchor_dir)
            integrity = store.integrity()
            result["integrity_ok"] = integrity["ok"]
            result["caseset_sha256"] = integrity["caseset_sha256"]
            result["reference_ok"] = bool(integrity["reference"].get("ok"))
            for problem in integrity.get("problems", []):
                result["problems"].append(f"锚完整性: {problem}")
            case_set = store.load(verify=True)
            answers = store.load_reference()
        elif layer == C.LAYER_L3:
            report = R.run_l3()
            result["framework_status"] = (report.framework or {}).get("status")
            result["ok"] = result["framework_status"] == "framework_only"
            result["cases"] = report.total
            if not result["ok"]:
                result["problems"].append("L3 框架声明缺失或状态非 framework_only")
            return result
        else:
            path = R.LAYER_CASESET_PATHS[layer]
            if not os.path.exists(path):
                result["problems"].append(f"用例集不存在: {path}")
                return result
            case_set = C.load_case_set(path)
            answers = _load_answers(REFERENCE_PATHS.get(layer, ""))
    except Exception as e:  # noqa: BLE001 自检门的失败必须显式暴露
        result["problems"].append(f"{type(e).__name__}: {e}")
        return result

    result["cases"] = len(case_set)
    result["caseset_sha256"] = case_set.caseset_sha256
    result["scenario_counts"] = case_set.scenario_counts()
    result["verdict_counts"] = case_set.verdict_counts()
    errors = C.validate_case_set(case_set, require_layer_size=True)
    if errors:
        result["problems"].extend(f"契约: {e}" for e in errors)
    if not answers:
        result["problems"].append("参考解缺失（无法做判定器自检与区分度对照）")
        return result

    # ③ 参考解全过
    solve, _ = S.reference_solver(answers)
    report = R.run_layer(layer, case_set=case_set, solver=solve, solver_name="reference")
    result["reference_counts"] = report.counts()
    result["reference_pass_rate"] = report.pass_rate
    if report.counts().get(R.STATUS_FAIL) or report.counts().get(R.STATUS_ERROR):
        result["problems"].append(
            "参考解未全过: " + json.dumps(report.counts(), ensure_ascii=False))

    # ④ 逐条判定条目区分度
    survivors = []
    for case in case_set.cases:
        base = answers.get(case.id)
        if base is None:
            survivors.append(f"{case.id}: 无参考解")
            continue
        for index, check in enumerate(case.expect):
            broken = S.broken_answer_for_check(base, case, index)
            passed = R.run_case(case, lambda c, b=broken: b).passed
            if passed:
                survivors.append(f"{case.id}.expect[{index}].{check.get('checker')}")
    result["per_check_survivors"] = survivors
    if survivors:
        result["problems"].append(f"判定条目无区分度 {len(survivors)} 条: {survivors[:10]}")

    solve_m, _ = S.mutant_solver(answers)
    mutant_report = R.run_layer(layer, case_set=case_set, solver=solve_m,
                                solver_name="mutant")
    result["mutant_counts"] = mutant_report.counts()
    mutant_survivors = [r.case_id for r in mutant_report.results if r.passed]
    result["mutant_survivors"] = mutant_survivors
    if mutant_survivors:
        result["problems"].append(f"变异解仍有通过用例 {len(mutant_survivors)} 条: "
                                  f"{mutant_survivors[:10]}")

    result["ok"] = not result["problems"]
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="L0–L3 评测数据自检（TASK-S5-02）")
    parser.add_argument("--layer", default="", choices=["", *C.LAYERS])
    parser.add_argument("--anchor-dir", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    layers = [args.layer] if args.layer else list(C.LAYERS)
    reports = [_check_layer(layer, anchor_dir=args.anchor_dir) for layer in layers]
    payload = {"ok": all(r["ok"] for r in reports), "layers": reports}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        for row in reports:
            flag = "OK  " if row["ok"] else "FAIL"
            print(f"[{flag}] {row['layer']}: cases={row.get('cases')} "
                  f"sha={(row.get('caseset_sha256') or '')[:12]} "
                  f"ref={row.get('reference_counts')} mutant={row.get('mutant_counts')}")
            for problem in row.get("problems", []):
                print(f"        - {problem}")
        print(f"\n自检结论: {'全绿' if payload['ok'] else '存在失败项'}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
