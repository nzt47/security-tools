#!/usr/bin/env python3
"""从 CI 的 junit 产物机械重生成 scripts/shard_time_weights.json。

【为什么有这个脚本】权重表原先由未入库的 _t06_logs/gen_weights.py 生成（见表内
generator 字段），重标定无法复核、无法复现。本脚本把那条一次性路径固化成
「多个 run 的 junit 目录 → 一张权重表」，拿到同样的 artifact 都能复算出同一张表
（排序、四舍五入、中位数取法全部显式固定，不依赖文件系统枚举顺序）。

【数据从哪来】observability-ci.yml 的 full-project-tests job 每个 shard 上传
full-project-tests-results-shard<N>（内含 junit.xml / junit-serial.xml）：
    gh run download <run_id> -n full-project-tests-results-shard1 -D <dir>/<run>/shard1
然后用 --junit-dir <dir>/<run> 逐个传入（可重复）。

【口径·不易】权重 = 单文件 junit time 属性累加的 **跨 run 中位数**（秒）。
  · 用中位数而非均值/单次：单次 run 的耗时受同批 job 争抢、runner 冷热影响，
    2026-10-28 三次 master run 的 Shard 4 分别是 1620/1792/1851s，单次取样会把
    噪声写进分片成员；中位数对单次离群（一次超时/一次冷缓存）不敏感。
  · 不做 -n 2 折算：并行度只影响墙钟，不影响文件之间的相对权重。

用法：
    python scripts/regen_shard_time_weights.py \
        --junit-dir <a> --junit-dir <b> --junit-dir <c> --shards 6 \
        --run 40636453312 --run 40663968008 --run 40653670782 \
        --steps "40636453312=1:519,2:343,3:526,4:1851,5:502,6:720" [...] \
        --date 2026-10-28 [--check]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import split_unit_tests as SUT  # noqa: E402

OUT_PATH = ROOT / "scripts" / "shard_time_weights.json"
# 权重下限：全部用例被 skip 的文件实测和是 0，给 0 会让贪心把它当「无成本」
# 反复塞进同一个分片；给 1ms 既保留「几乎不耗时」的语义又保持确定性。
MIN_WEIGHT = 0.001

# 【不易】权重必须在「同一个 pytest 过滤条件」下测：各 job 的 marker 过滤、
# 超时与 --root 都不同，混用会把在本 job 里被整体过滤掉的慢文件算成真实成本。
# 故按 mode 固定 workflow / 收集根 / 命令行，生成器不接受"随便传一串参数"。
MODE_CONTEXT = {
    "full-project": {
        "workflow": "observability-ci.yml / full-project-tests",
        "test_root": "tests",
        "command": ("pytest <split_unit_tests.py --root tests --shards {shards} --by=time> "
                    "-n 2 --dist=loadscope -m \"not slow and not skip_ci and not serial\" --timeout=60"),
    },
    "unit": {
        "workflow": "ci.yml / unit-tests",
        "test_root": "tests/unit",
        "command": ("pytest <split_unit_tests.py --shards {shards} --by=time> -n 2 "
                    "--dist=loadscope -m \"not slow and not skip_ci and not serial\" --timeout=60"),
    },
    "integration": {
        "workflow": "ci.yml / integration-tests",
        "test_root": "tests/integration",
        "command": ("pytest <split_unit_tests.py --root tests/integration --shards {shards} --by=time> "
                    "-m \"not slow and not e2e and not requires_llm and not skip_ci\" --timeout=300"),
    },
}


def known_test_files(test_root: str = "tests") -> set[str]:
    """已收集的测试文件口径必须与分片脚本完全一致（EXCLUDED/目录排除同源）。"""
    return set(SUT.collect_test_files(ROOT, test_root))


def resolve(classname: str, known: set[str]) -> str | None:
    """junit classname（点分模块名 + 可选类名）→ 收集到的测试文件路径。

    【不易】按「从长到短」试探前缀：classname 可能只有模块名
    （tests.unit.test_x，无类）也可能带类名（tests.unit.test_x.TestY），
    模块名自身还可能含子目录成分，固定层数切分会漏。
    """
    parts = classname.split(".")
    for i in range(len(parts), 0, -1):
        cand = "/".join(parts[:i]) + ".py"
        if cand in known:
            return cand
    return None


def read_junit(path: Path, known: set[str], totals: dict[str, float],
               counts: dict[str, int]) -> tuple[float, int]:
    seconds = 0.0
    cases = 0
    for _, el in ET.iterparse(str(path), events=("end",)):
        if el.tag != "testcase":
            continue
        target = resolve(el.get("classname") or "", known)
        t = float(el.get("time") or 0.0)
        if target is not None:
            totals[target] += t
            counts[target] += 1
            seconds += t
            cases += 1
        el.clear()
    return seconds, cases


def parse_steps(spec: str) -> dict[str, dict[str, float]]:
    """'run=1:519,2:343 run2=...' → {run: {"1": 519.0, ...}}"""
    out: dict[str, dict[str, float]] = {}
    for chunk in spec.split():
        run, _, rest = chunk.partition("=")
        per: dict[str, float] = {}
        for item in rest.split(","):
            k, _, v = item.partition(":")
            if k.strip() and v.strip():
                per[k.strip()] = float(v)
        if run.strip() and per:
            out[run.strip()] = per
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="从 junit 产物重生成分片耗时权重表（跨 run 中位数）")
    ap.add_argument("--junit-dir", required=True, action="append", type=Path,
                    help="一次 run 的根目录（内含 shard1..shardN）；可重复传入多次 run")
    ap.add_argument("--run", action="append", default=[],
                    help="与 --junit-dir 一一对应的 CI run id（写进 measurement_context）")
    ap.add_argument("--steps", default="",
                    help='各 run、各 shard 测试步骤墙钟秒：\'40636453312=1:519,2:343 ...\'')
    ap.add_argument("--mode", choices=sorted(MODE_CONTEXT), default="full-project",
                    help="按哪个 job 的口径标定（决定 workflow / 收集根 / 命令行元数据）")
    ap.add_argument("--shards", type=int, default=6)
    ap.add_argument("--test-root", default="", help="覆盖 mode 默认的收集根")
    ap.add_argument("--date", default="")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--check", action="store_true",
                    help="只比对：现有表与重算结果是否逐字节一致（不一致退出码 1）")
    args = ap.parse_args()

    mode = MODE_CONTEXT[args.mode]
    test_root = args.test_root or mode["test_root"]
    known = known_test_files(test_root)
    step_map = parse_steps(args.steps)
    run_ids = args.run or [""] * len(args.junit_dir)

    per_run: list[dict[str, dict]] = []
    for idx, root in enumerate(args.junit_dir):
        totals: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        per_shard: dict[str, dict[str, float]] = {}
        for shard in range(1, args.shards + 1):
            shard_dir = root / ("shard%d" % shard)
            shard_seconds = 0.0
            shard_cases = 0
            # 【不易】不写死文件名：observability-ci 的 artifact 是 junit.xml /
            #   junit-serial.xml，ci.yml 集成测试的是 junit-integration-shardN.xml。
            #   按 shard 目录下 *.xml 全收，避免「换了 artifact 命名就漏测」。
            for p in sorted(shard_dir.glob("*.xml")):
                s, c = read_junit(p, known, totals, counts)
                shard_seconds += s
                shard_cases += c
            per_shard[str(shard)] = {"tests": shard_cases, "junit_s": round(shard_seconds, 3)}
        run_id = run_ids[idx] if idx < len(run_ids) else ""
        steps = step_map.get(run_id, {})
        for k, v in per_shard.items():
            if k in steps:
                v["step_s"] = steps[k]
        if not totals:
            print("error: %s 下没有解析出任何用例" % root, file=sys.stderr)
            return 2
        per_run.append({"run": run_id, "dir": str(root), "totals": dict(totals),
                        "counts": dict(counts), "shards": per_shard,
                        "seconds": sum(totals.values()), "cases": sum(counts.values())})

    files = sorted({f for r in per_run for f in r["totals"]})
    median: dict[str, float] = {}
    for f in files:
        vals = [r["totals"][f] for r in per_run if f in r["totals"]]
        median[f] = round(statistics.median(vals), 3)
    counts_max = {f: max(r["counts"].get(f, 0) for r in per_run) for f in files}

    total_seconds = sum(median.values())
    total_cases = sum(counts_max.values())
    rate = round(total_seconds / total_cases, 6) if total_cases else SUT.FALLBACK_SECONDS_PER_TEST

    shard_summary: dict[str, dict[str, float]] = {}
    for shard in range(1, args.shards + 1):
        key = str(shard)
        jt = [r["shards"][key]["junit_s"] for r in per_run]
        ct = [r["shards"][key]["tests"] for r in per_run]
        st = [r["shards"][key]["step_s"] for r in per_run if "step_s" in r["shards"][key]]
        entry: dict[str, float] = {
            "tests": int(statistics.median(ct)),
            "junit_s": round(statistics.median(jt), 3),
        }
        if st:
            entry["step_s"] = round(statistics.median(st), 3)
        shard_summary[key] = entry

    throughputs = []
    for r in per_run:
        step_total = sum(v.get("step_s", 0.0) for v in r["shards"].values())
        if step_total:
            throughputs.append(r["seconds"] / step_total)
    throughput = round(statistics.mean(throughputs), 5) if throughputs else None

    missing = sorted(known - set(files))
    doc = {
        "schema": 1,
        "title": "按实测耗时均衡分片用的每文件耗时权重（秒）",
        "generated_at": args.date or date.today().isoformat(),
        "generator": ("python scripts/regen_shard_time_weights.py --mode %s --shards %d "
                      "--junit-dir <运行时目录> --run %s --date %s"
                      % (args.mode, args.shards, " ".join(r["run"] for r in per_run),
                         args.date or date.today().isoformat())),
        "unit": "seconds (pytest junit time 属性累加；跨 %d 次 CI run 取中位数)" % len(per_run),
        "measurement_context": {
            "mode": args.mode,
            "workflow": mode["workflow"],
            "runs": [r["run"] for r in per_run],
            "test_root": test_root,
            "command": mode["command"].format(shards=args.shards),
            "env": "SKILLS_OFFLINE=1 AGENT_HYBRID_EMBEDDING=0",
            "runner": "ubuntu-22.04",
        },
        "calibration": {
            "measured_seconds_total": round(total_seconds, 3),
            "measured_tests_total": total_cases,
            "measured_files": len(files),
            "default_seconds_per_test": rate,
            "default_derivation": ("本表未覆盖的文件按「条数 x 该单位耗时」定标；"
                                   "单位耗时 = 各文件中位数合计 / 用例合计"),
            "parallel_throughput": throughput,
            "parallel_throughput_derivation": ("各 run 的「实测秒数合计 / 各 shard 测试步骤墙钟合计」"
                                               "取均值；含每 shard 固定开销，故是并行度下界"),
            "observed_shards": shard_summary,
        },
        "sources": {
            "ci_junit": "Artifact full-project-tests-results-shard*/junit.xml（manifest 见 measurement_context.runs）",
            "unmeasured_files": missing[:50],
            "unmeasured_count": len(missing),
        },
        "files": {f: max(MIN_WEIGHT, median[f]) for f in files},
    }

    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    if args.check:
        try:
            current = args.out.read_text(encoding="utf-8")
        except OSError as exc:
            print("error: 读不到 %s（%s）" % (args.out, exc), file=sys.stderr)
            return 1
        if current != text:
            print("DIFF: %s 与 junit 重算结果不一致" % args.out, file=sys.stderr)
            return 1
        print("OK: %s 与 junit 重算逐字节一致" % args.out)
        return 0

    args.out.write_text(text, encoding="utf-8")
    print("wrote %s: %d files / %.1fs / %d cases (rate=%.6f, runs=%d)"
          % (args.out.name, len(files), total_seconds, total_cases, rate, len(per_run)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
