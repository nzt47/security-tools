"""W2 门禁新增指标的**基线校准**（TASK-04）

    python scripts/bench_perf_gate_metrics.py                 # 3 轮
    python scripts/bench_perf_gate_metrics.py --repeats 5 --json out.json

## 这是什么、不是什么

* 这是 scripts/check_perf_regression.py **新增指标**的基线标定工具：
  用**与门禁完全同一套**采集与统计口径（直接 import 门禁里的
  _paired / _entry），因此"基线"与"判决"不可能各说各话。
* **不是压测**：单进程、单线程、无并发、不发网络、不起服务、不写任何生产数据。
  本波次明令禁跑压测（D10：有并行子代理，压测相互污染），故这里刻意不做并发。

## 为什么需要"多轮"

门禁量是"被测 / 同刻参照负载"的交错比值，已经对机器负载不敏感；
但**基线本身**仍需要一个可复算的取值。本脚本跑 N 轮，报出：
  · value          = 交错比值 best-of-N(min) —— 门禁判决量
  · stability      = N 轮里 max(value)/min(value) —— 这台机器上该指标的可复现性
  · p95_stability  = N 轮里 max/min(p95_ratio) —— p95 那条线是否够稳
基线取 **N 轮的 min**：负载只会给测量值"加"时间，min 是最紧的真实成本上界
（与门禁自身的 best-of-N 理由一致）。

⚠️ stability 显著 > 1 时**不要**收紧阈值：那说明这台机器当前不适合定紧线，
   应在安静机器上重采（门禁里的 QUIET_MACHINE_THRESHOLD 正为此准备）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if os.path.join(_REPO_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

# ── GBK 控制台加固（与 scripts/check_perf_regression.py 同一修法，见 06-基线台账 §7.4）──
# 只放宽 errors、**不改 encoding**：改 encoding 会让 GBK 控制台上的中文变乱码，
# 而 errors="replace" 只把无法编码的装饰字符降级为 ?，中文仍正常；UTF-8 环境等价于无操作。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

import check_perf_regression as cpr  # noqa: E402


def _measure_envelope(reps: int, rows: int) -> Dict[str, Any]:
    """全量信封：**与门禁同口径**的单次采集（单线程，无并发）"""
    from agent.capregistry.view import CapabilityRegistry
    from bench_capregistry import _synthesize

    synth = _synthesize(rows)
    reg = CapabilityRegistry(synth)
    ratios, raws = cpr._paired(lambda: reg.list_envelope(), "cpu", reps)
    return cpr._entry(ratios, raws, "cpu",
                      p95_gated=("capregistry_list_envelope_10k" in cpr.P95_GATED),
                      n_synthetic=len(synth))


def _spread(vals: List[float]) -> float:
    lo = min(vals)
    return round((max(vals) / lo), 4) if lo > 0 else float("inf")


def main() -> int:
    ap = argparse.ArgumentParser(formatter_class=cpr._RawHelpFormatter)
    ap.add_argument("--repeats", type=int, default=3, help="重复轮数（默认 3）")
    ap.add_argument("--reps", type=int, default=30,
                    help="每轮样本数（默认 30；n<30 时最近秩法的 p95 会退化为 max）")
    ap.add_argument("--rows", type=int, default=10000, help="合成条数（默认 10000）")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    print("=" * 74)
    print("W2 门禁新增指标 · 基线校准（单线程、无并发、无网络 ⇒ 不是压测）")
    print("=" * 74)
    print("采集时间：%s" % time.strftime("%Y-%m-%dT%H:%M:%S"))
    print("口径：与 check_perf_regression 完全同一套（交错采样 + best-of-N(min)）")
    print("合成条数=%d，每轮样本=%d，轮数=%d" % (args.rows, args.reps, args.repeats))

    rows_out: List[Dict[str, Any]] = []
    rss_vals: List[float] = []
    for i in range(args.repeats):
        row = _measure_envelope(args.reps, args.rows)
        rows_out.append(row)
        rss_vals.append(cpr._rss_mb())
        print("  第 %d 轮：value=%.6f p50=%.6f p95=%.6f max=%.6f | "
              "raw_min=%.2fms raw_p50=%.2fms raw_p95=%.2fms"
              % (i + 1, row["value"], row["p50_ratio"], row["p95_ratio"],
                 row["max_ratio"], row["raw_min_ms"], row["raw_p50_ms"],
                 row["raw_p95_ms"]))

    values = [r["value"] for r in rows_out]
    p95s = [r["p95_ratio"] for r in rows_out]
    best = min(rows_out, key=lambda r: r["value"])
    rss_best = min(rss_vals)

    block = {
        "capregistry_list_envelope_10k": {
            "value": round(min(values), 6),
            "ref": "cpu",
            "p50_ratio": best["p50_ratio"],
            "max_ratio": best["max_ratio"],
            "raw_min_ms": best["raw_min_ms"],
            "raw_p50_ms": best["raw_p50_ms"],
            "p95_ratio": round(min(p95s), 6),
            "raw_p95_ms": best["raw_p95_ms"],
            "p95_gated": True,
            "n": best["n"],
            "unit": "x(cpu-ref)",
            "note": "合成 10k 条全量信封；只测数据结构在 10k 量级的行为",
        },
        "process_rss_mb": {
            "value": round(rss_best, 3),
            "ref": "mem",
            "p95_gated": False,
            "n": 1,
            "unit": "MB",
            "note": "门禁进程 RSS(MB)；判决量是【当前/基线】的比值",
        },
    }

    report = {
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "repeats": args.repeats, "reps": args.reps, "rows": args.rows,
        "envelope_value_per_round": [round(v, 6) for v in values],
        "envelope_p95_ratio_per_round": [round(v, 6) for v in p95s],
        "value_stability_max_over_min": _spread(values),
        "p95_stability_max_over_min": _spread(p95s),
        "rss_mb_per_round": [round(v, 3) for v in rss_vals],
        "rss_stability_max_over_min": _spread(rss_vals),
        "NEW_METRIC_BASELINE": block,
    }

    print("")
    print("─" * 74)
    print("稳定性（N 轮 max/min；越接近 1 越可复现）")
    print("─" * 74)
    print("  value     : %s  ⇒ 波动 %.3fx"
          % (report["envelope_value_per_round"], report["value_stability_max_over_min"]))
    print("  p95_ratio : %s  ⇒ 波动 %.3fx"
          % (report["envelope_p95_ratio_per_round"], report["p95_stability_max_over_min"]))
    print("  rss_mb    : %s  ⇒ 波动 %.3fx"
          % (report["rss_mb_per_round"], report["rss_stability_max_over_min"]))
    print("")
    print("可粘贴的基线（基线取 N 轮的 min；负载只会给测量值加时间）：")
    print("NEW_METRIC_BASELINE: dict = " + json.dumps(block, ensure_ascii=False, indent=4))

    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("")
        print("[bench] 结构化结果已写入 %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())