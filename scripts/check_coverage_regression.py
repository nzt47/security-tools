#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""覆盖率不得下降断言（D1 交付物 · 2026-09-19）

Why 需要这个脚本
----------------
TASK-04 需要一个"重构不得降低覆盖率"的可执行断言。但本仓历史上有**三个互不可比
的覆盖率数字**（49.08% / 77.20% / CI 门禁 40%），根因是**口径不统一**：

* 有人跑 `--cov=agent`（1 个包），有人跑 pyproject 声明的 9 个包；
* coverage 的"未执行文件"是**采集期**由 `--cov=` 指定的目录扫描出来的，
  报告期**不会**再按 pyproject 的 `source` 补扫 ⇒ 分母完全由调用方式决定。

⇒ 只比较百分比是**危险**的：把口径从 1 个包换成 9 个包，数字自然下降，
   会被误判成"覆盖率回归"；反过来放宽口径会让真实回归被洗白。

所以本脚本把**口径**当成断言的一部分：

    口径不一致（包列表不同 / 分支开关不同） ⇒ 直接拒绝比较（退出码 3），
    而不是给出一个看起来合理的百分比差值。

用法
----
    # 1) 先采集权威口径（脚本会写出 coverage.json，含 packages 字段）
    python scripts/run_authoritative_coverage.py --out _ci_logs/coverage_auth

    # 2) 与基线比较（默认容忍 0 个百分点：只允许持平或上升）
    python scripts/check_coverage_regression.py \
        --baseline coverage_baseline.json \
        --current  _ci_logs/coverage_auth/coverage.json

    # 3) 额外叠加绝对下限（与 CI 的 --fail-under 同口径时才有意义）
    python scripts/check_coverage_regression.py --baseline B --current C --fail-under 40

退出码
------
0 = 无回归（且不破 --fail-under）
1 = 覆盖率下降超过容忍值，或低于 --fail-under
2 = 用法/文件/解析错误
3 = 口径不一致（拒绝比较，**不是**通过）
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _force_utf8_stdio() -> None:
    """Windows 控制台默认 GBK：输出含 ✔/✗/⇒ 时会 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_measurement(path: Path) -> dict:
    """读入一次覆盖率测量结果。

    支持两种来源：
      1. `run_authoritative_coverage.py` 写出的 `coverage.json`（含口径字段，**推荐**）；
      2. coverage 自己写的 `coverage.xml`（**没有包列表**，只能从 `<sources>` 推断，
         会被标记为 scope=inferred，只有显式 --allow-inferred-scope 才参与比较）。
    """
    if not path.exists():
        raise FileNotFoundError(str(path))
    if path.suffix.lower() == ".xml":
        root = ET.parse(path).getroot()
        sources = [Path(s.text).name for s in root.findall("./sources/source")]
        return {
            "line_rate": float(root.get("line-rate")),
            "lines_covered": int(root.get("lines-covered")),
            "lines_valid": int(root.get("lines-valid")),
            "branch_rate": float(root.get("branch-rate")),
            "branches_valid": int(root.get("branches-valid")),
            "packages": sorted(sources),
            "scope_source": "inferred-from-xml-sources",
            "source_path": str(path),
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    if "line_rate" not in data:
        raise ValueError(f"{path} 里没有 line_rate 字段，不是覆盖率测量结果")
    data.setdefault("packages", None)
    data.setdefault("scope_source", "explicit" if data.get("packages") else "unknown")
    data["source_path"] = str(path)
    return data


def compare_scope(base: dict, cur: dict) -> tuple[bool, str]:
    """口径一致性判定：这是本脚本的核心，比数值比较更重要。

    Why 把分支开关也算进口径：`branch=True` 会显著改变 lines-valid 与百分比，
    只比包列表不足以判定可比性。
    """
    bp, cp = base.get("packages"), cur.get("packages")
    if bp is None or cp is None:
        return False, "至少一侧缺少 packages 字段（口径未知）"
    if sorted(bp) != sorted(cp):
        only_b = sorted(set(bp) - set(cp))
        only_c = sorted(set(cp) - set(bp))
        return False, f"包列表不同（基线独有 {only_b}；本次独有 {only_c}）"

    bb = bool(base.get("branches_valid"))
    cb = bool(cur.get("branches_valid"))
    if bb != cb:
        return False, f"分支覆盖开关不同（基线 branches_valid={base.get('branches_valid')}，本次 {cur.get('branches_valid')}）"
    return True, f"口径一致：{len(bp)} 个包 {sorted(bp)}；分支覆盖={'开' if cb else '关'}"


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="覆盖率不得下降断言（含口径一致性硬校验）")
    ap.add_argument("--baseline", type=Path, required=True, help="基线测量（coverage.json 或 coverage.xml）")
    ap.add_argument("--current", type=Path, required=True, help="本次测量（同上）")
    ap.add_argument(
        "--tolerance",
        type=float,
        default=0.0,
        help="允许的下降幅度（百分点）。默认 0.0 = 只允许持平或上升",
    )
    ap.add_argument("--fail-under", type=float, default=None, help="绝对下限（百分点）；不给则只比基线")
    ap.add_argument(
        "--allow-inferred-scope",
        action="store_true",
        help="允许把 XMl <sources> 推断出来的口径当作口径（默认拒绝，因为 <sources> 是"
             "报告期配置，不是采集期 --cov，二者实测会分叉）",
    )
    ap.add_argument("--report", type=Path, default=None, help="把比对结果写成 JSON")
    args = ap.parse_args(argv)

    try:
        base = load_measurement(args.baseline)
        cur = load_measurement(args.current)
    except (OSError, ValueError, ET.ParseError, json.JSONDecodeError) as exc:
        print(f"[check_coverage_regression] 读取失败：{exc}", file=sys.stderr)
        return 2

    print("=" * 78)
    print("覆盖率不得下降断言")
    print("=" * 78)
    print(f"基线      : {args.baseline}")
    print(f"          line_rate={base['line_rate'] * 100:.2f}%  "
          f"({base['lines_covered']}/{base['lines_valid']})  口径来源={base.get('scope_source')}")
    print(f"本次      : {args.current}")
    print(f"          line_rate={cur['line_rate'] * 100:.2f}%  "
          f"({cur['lines_covered']}/{cur['lines_valid']})  口径来源={cur.get('scope_source')}")
    print(f"容忍下降  : {args.tolerance:.2f} pp")
    print("-" * 78)

    inferred = base.get("scope_source") == "inferred-from-xml-sources" or \
        cur.get("scope_source") == "inferred-from-xml-sources"
    if inferred and not args.allow_inferred_scope:
        print("✗ 拒绝比较：一侧的口径是从 coverage.xml 的 <sources> 推断的，")
        print("  而 <sources> 记录的是**报告期**配置（pyproject 的 source），实测与")
        print("  **采集期** `--cov=` 指定的包会分叉（见 docs/closeout/COVERAGE_SCOPE_20260919.md）。")
        print("  请用 run_authoritative_coverage.py 产出的 coverage.json 作为基线/本次值，")
        print("  或显式 --allow-inferred-scope 承担风险。")
        return 3

    same, why = compare_scope(base, cur)
    print(("✔ " if same else "✗ ") + why)
    if not same:
        print("✗ 拒绝比较：口径不同的两个数字相减没有任何含义。")
        print("  这正是本脚本要挡住的错误（历史 49.08% 与 77.20% 就是这样被误比的）。")
        return 3

    base_pct = base["line_rate"] * 100
    cur_pct = cur["line_rate"] * 100
    delta = cur_pct - base_pct
    print("-" * 78)
    print(f"基线      : {base_pct:8.2f}%")
    print(f"本次      : {cur_pct:8.2f}%")
    print(f"变化      : {delta:+8.2f} pp   （容忍下降 {args.tolerance:.2f} pp）")

    failed = False
    if delta < -args.tolerance - 1e-9:
        print(f"✗ **覆盖率下降**：{delta:+.2f} pp 超过容忍值 -{args.tolerance:.2f} pp")
        failed = True
    else:
        print("✔ 覆盖率未下降（在容忍范围内）")

    if args.fail_under is not None:
        floor = args.fail_under
        if cur_pct + 1e-9 < floor:
            print(f"✗ **低于绝对下限**：{cur_pct:.2f}% < --fail-under={floor:.2f}%")
            failed = True
        else:
            print(f"✔ 达到绝对下限：{cur_pct:.2f}% ≥ --fail-under={floor:.2f}%")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "baseline": {"path": str(args.baseline), "line_rate": base["line_rate"],
                                 "lines_covered": base["lines_covered"], "lines_valid": base["lines_valid"],
                                 "packages": base.get("packages")},
                    "current": {"path": str(args.current), "line_rate": cur["line_rate"],
                                "lines_covered": cur["lines_covered"], "lines_valid": cur["lines_valid"],
                                "packages": cur.get("packages")},
                    "tolerance_pp": args.tolerance,
                    "fail_under": args.fail_under,
                    "delta_pp": round(delta, 4),
                    "scope_consistent": same,
                    "verdict": "regression" if failed else "ok",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[check_coverage_regression] 比对结果已写入 {args.report}")

    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
