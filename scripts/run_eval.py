"""分层评测执行器 CLI（TASK-S5-02）——`run_l0()` 等的命令行入口

用法::

    # L0 锚：默认"未评测"（无真实解算器时如实标注）；--solver reference 验证判定器
    python scripts/run_eval.py --layer L0 --solver reference --print-md
    python scripts/run_eval.py --layer L0 --solver mutant --print-md

    # L1 / L2：默认用同目录 reference.json 作参考解自检
    python scripts/run_eval.py --layer L1 --print-md
    python scripts/run_eval.py --layer L2 --record-baseline --print-md

    # 被测系统的答案工件（纯 JSON：{"answers": {"<case_id>": {...}}}）
    python scripts/run_eval.py --layer L2 --solver file:answers.json --print-md

    # 记录 L2 Core-50 基线快照（UTC + shadow 真实墙钟 p99 + 样本充分性 + S5-03 触发）
    python scripts/run_eval.py --layer L2 --write-l2-baseline data/eval/l2_baseline.json

**口径**：报告里区分 pass / fail / error / **unassessed**（未评测不计入通过率分母）；
耗时一律 ``wall_clock(perf_counter)`` 并在报告中标注。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.eval import anchor as A  # noqa: E402
from agent.eval import baseline as B  # noqa: E402
from agent.eval import cases as C  # noqa: E402
from agent.eval import runner as R  # noqa: E402
from agent.eval import solvers as S  # noqa: E402

#: 各层参考解默认落点
REFERENCE_PATHS = {
    C.LAYER_L0: os.path.join(A.DEFAULT_ANCHOR_DIR, A.REFERENCE_FILENAME),
    C.LAYER_L1: os.path.join(A._REPO_ROOT, "eval", "l1_min", "reference.json"),
    C.LAYER_L2: os.path.join(A._REPO_ROOT, "eval", "l2_core50", "reference.json"),
}


def _load_answers(layer: str, path: str = "") -> dict:
    target = path or REFERENCE_PATHS.get(layer, "")
    if not target or not os.path.exists(target):
        return {}
    with open(target, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    answers = data.get("answers") if isinstance(data, dict) and isinstance(
        data.get("answers"), dict) else data
    return dict(answers or {})


def _resolve_solver(layer: str, name: str, answers_path: str):
    answers = _load_answers(layer, answers_path) if name != "null" else {}
    if name.startswith("file:"):
        return S.solver_from_spec(name)
    return S.solver_from_spec(name, answers=answers)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="v7.2 §6.5 分层评测执行器（TASK-S5-02）")
    parser.add_argument("--layer", default="L0", choices=list(C.LAYERS))
    parser.add_argument("--solver", default="auto",
                        help="null|reference|static|mutant[:mode]|file:<answers.json>|auto")
    parser.add_argument("--answers", default="", help="答案工件（缺省取该层 reference.json）")
    parser.add_argument("--anchor-dir", default="", help="L0 锚目录")
    parser.add_argument("--caseset", default="", help="显式指定用例集文件")
    parser.add_argument("--baseline", default="", help="基线文件（缺省 eval/baselines/<layer>_baseline.json）")
    parser.add_argument("--no-baseline-compare", action="store_true", help="跳过基线对照")
    parser.add_argument("--record-baseline", action="store_true", help="把本次运行固化为基线")
    parser.add_argument("--repo-root", default="", help="符号/路径判定所用仓库根")
    parser.add_argument("--json-out", default="", help="把完整报告写出为 JSON")
    parser.add_argument("--print-md", action="store_true", help="打印 Markdown 摘要")
    parser.add_argument("--write-l2-baseline", default="", nargs="?", const="__default__",
                        help="写出 L2 Core-50 基线快照（可给路径）")
    parser.add_argument("--events-dir", default="", help="事件流目录（L2 基线/UTC 用）")
    parser.add_argument("--shadow-dir", default="", help="灰度台账目录（真实墙钟 p99 用）")
    parser.add_argument("--trace-db", default="", help="统一轨迹库（能力级样本量用）")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    layer = args.layer.upper()
    repo_root = args.repo_root or None

    if layer == C.LAYER_L0:
        store = A.AnchorStore(args.anchor_dir)
        if args.solver in ("auto", "reference"):
            report = R.run_l0(store=store, reference=True, compare_baseline=False)
        else:
            solve, name = _resolve_solver(layer, args.solver, args.answers)
            report = R.run_l0(store=store, solver=solve, solver_name=name,
                              compare_baseline=False)
    elif layer == C.LAYER_L3:
        report = R.run_l3(caseset_path=args.caseset)
    else:
        path = args.caseset or R.LAYER_CASESET_PATHS.get(layer, "")
        case_set = C.load_case_set(path)
        if args.solver == "auto":
            solve, name = S.reference_solver(_load_answers(layer, args.answers),
                                             name="reference")
        else:
            solve, name = _resolve_solver(layer, args.solver, args.answers)
        kwargs = {"case_set": case_set, "solver": solve, "solver_name": name}
        if repo_root:
            kwargs["repo_root"] = repo_root
        report = R.run_layer(layer, **kwargs)

    if not args.no_baseline_compare:
        target = args.baseline or R.default_baseline_path(layer)
        report.baseline = R.compare_to_baseline(report, R.load_baseline(target))

    if args.record_baseline:
        written = R.write_baseline(report, args.baseline,
                                   note=f"solver={report.solver}")
        print(f"[OK] 基线已固化: {written}")

    if args.write_l2_baseline:
        snapshot = B.build_l2_baseline(days=7, events_dir=args.events_dir or None,
                                       shadow_dir=args.shadow_dir,
                                       trace_db=args.trace_db)
        target = ("" if args.write_l2_baseline == "__default__"
                  else args.write_l2_baseline)
        path = B.write_l2_baseline(snapshot, target)
        print(f"[OK] L2 基线快照已写出: {path}")
        print(B.baseline_markdown(snapshot))

    if args.json_out:
        parent = os.path.dirname(os.path.abspath(args.json_out))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(report.to_dict(), ensure_ascii=False, indent=2,
                                default=str) + "\n")
        print(f"[OK] 报告已写出: {args.json_out}")

    if args.print_md or not args.json_out:
        print(report.markdown())

    counts = report.counts()
    # 退出码：有用例 fail/error → 1；全未评测 → 0（如实标注，不假装失败/成功）
    return 1 if (counts.get(R.STATUS_FAIL) or counts.get(R.STATUS_ERROR)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
