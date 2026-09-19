#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""权威覆盖率采集（D1 交付物 · 2026-09-19）

Why 需要这个脚本（而不是一条 `python -m pytest --cov=...`）
==========================================================
本仓的覆盖率长期存在"三个数字互不可比"的问题。核实后（见
`docs/closeout/COVERAGE_SCOPE_20260919.md`）根因有两条，都必须由脚本固化：

1. **口径漂移**：`pyproject.toml` 声明 `source` = 9 个包，但实测跑的人常常只写
   `--cov=agent`（1 个包）。而 coverage 的"未执行文件"是**采集期**由 tracer 的
   文件定位器扫描 `--cov=` 指定的目录得到的（实测见下），**报告期不会再补扫
   pyproject 的 `source`**。⇒ `--cov` 给几个包，分母就是几个包。
   本脚本从 `pyproject.toml` 读 `[tool.coverage.run].source` 并**逐个展开成
   `--cov=<pkg>`**，让"声明的口径"与"采集的口径"不可能再分叉。

2. **会被整块杀掉**：全量 `tests/unit` 单进程跑很久（TASK-03 实测分块 10 块共
   ~84 分钟），一旦中途被杀，pytest 的短摘要在进程结束才输出 ⇒ 一条结果都拿不到。
   本脚本沿用 TASK-03 的分块 + `--cov-append` 方案，并额外**逐块校验该块是否真跑完**
   （检测 pytest 结束摘要行），把"块被强杀 ⇒ 文件从未执行"这一事故显性化。

实测证据（裸 coverage 最小复现，2026-09-19）
-------------------------------------------
    # pyproject: source = ["pkg_a", "pkg_b"]
    coverage run runner.py            # 只 import pkg_a
    coverage report  →  pkg_a 100% / pkg_b 0%        ← pkg_b 进了分母

    coverage run --source=pkg_a runner.py   # 采集期只扫 pkg_a
    coverage report  →  只有 pkg_a                    ← pkg_b 完全消失

⇒ 结论：**分母由采集期的 `--cov` 决定，不由报告期的 pyproject `source` 决定。**

用法
----
    # 权威口径全量（9 个包，与 pyproject 声明一致）
    python scripts/run_authoritative_coverage.py --out _ci_logs/coverage_auth

    # 只跑一个包并明确标注"子集口径"（快速冒烟，数字不可与全量比较）
    python scripts/run_authoritative_coverage.py --packages agent --out _ci_logs/cov_agent

    # 重跑被强杀的那一块（脚本会打印该跑哪块）
    python scripts/run_authoritative_coverage.py --out _ci_logs/coverage_auth --only-chunk 8

退出码
------
0 = 每块都跑完（rc 不论；pytest 自身失败不影响覆盖率采集的完整性）
      且成功写出 coverage.json/coverage.xml
1 = 至少有一块**没有跑完**（被强杀/崩溃）⇒ 覆盖率数据不完整，禁止当作基线
2 = 用法或环境错误
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "_ci_logs" / "coverage_authoritative"

#: pytest 结束摘要行的特征（有它 ⇒ 该块的会话正常收尾，不是被 os._exit 强杀）。
#: 例：`1234 passed, 5 failed, 2 errors in 300.12s`
SUMMARY_RE = re.compile(r"\d+\s+(passed|failed|error|skipped|xfailed|xpassed|deselected)")
#: `--continue-on-collection-errors` 下收集期错误也会进摘要，仍算正常收尾。
NO_TESTS_RE = re.compile(r"no tests ran")


def _force_utf8_stdio() -> None:
    """Windows 控制台默认 GBK：输出里的 ⇒/✔/✗ 会 UnicodeEncodeError。

    与 scripts/audit_dependency_drift.py:608 的 `_force_utf8_stdio()` 同一处置。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def read_declared_packages() -> list[str]:
    """从 pyproject.toml 读 `[tool.coverage.run].source`——口径的唯一真相源。

    Why 不硬编码：硬编码就会再次出现"脚本写的包"与"pyproject 声明的包"分叉，
    这正是本任务要消灭的那种第二真相源。
    """
    import tomllib  # Python >= 3.11（pyproject requires-python >=3.11）

    with (ROOT / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    src = data.get("tool", {}).get("coverage", {}).get("run", {}).get("source")
    if isinstance(src, str):
        return [s.strip() for s in src.split(",") if s.strip()]
    if isinstance(src, list):
        return [str(s) for s in src]
    raise ValueError("pyproject.toml 里没有 [tool.coverage.run].source，无法确定口径")


def collect_test_files() -> list[str]:
    """枚举 tests/unit 下的测试文件（与 pytest 收集口径一致：test_*.py / *_test.py）。"""
    base = ROOT / "tests" / "unit"
    return sorted(
        p.relative_to(ROOT).as_posix()
        for p in base.rglob("*.py")
        if p.name.startswith("test_") or p.name.endswith("_test.py")
    )


def chunk_of(files: list[str], n: int, idx: int) -> list[str]:
    """轮转分块：让每块混合不同目录/字母段，避免"某块全是重载模块"。"""
    return [files[i::n] for i in range(n)][idx]


def build_cmd(files: list[str], packages: list[str], out: Path, timeout: int, timeout_method: str) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *files,
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:randomly",
        f"--timeout={timeout}",
        f"--timeout-method={timeout_method}",
        "--cov-report=",  # 逐块不出报告，最后统一出一次
        "--cov-append",
    ]
    cmd += [f"--cov={pkg}" for pkg in packages]
    return cmd


def chunk_completed(log: Path) -> tuple[bool, str]:
    """判断某块的 pytest 会话是否**正常收尾**。

    Why 必须单独判断：`--timeout-method=thread` 下超时是 `os._exit` 掉整个进程，
    pytest 来不及写摘要 ⇒ 该块里**排在挂起测试之后的文件一个都没跑**，
    而 rc 看起来只是"失败"。只信 rc 会把"丢了一批文件"误判成"有一批失败"。
    """
    if not log.exists():
        return False, "日志文件不存在"
    text = log.read_text(encoding="utf-8", errors="replace")
    if NO_TESTS_RE.search(text):
        return True, "no tests ran（收集为空，会话正常收尾）"
    for line in reversed(text.splitlines()):
        if SUMMARY_RE.search(line):
            return True, line.strip()
    if "+++ Timeout +++" in text or "Timeout" in text:
        return False, "会话未见摘要行，且日志含 Timeout 标记 ⇒ 疑似被强杀"
    return False, "会话未见 pytest 结束摘要行 ⇒ 疑似被强杀/崩溃"


def read_xml_total(xml_path: Path) -> dict:
    root = ET.parse(xml_path).getroot()
    return {
        "line_rate": float(root.get("line-rate")),
        "lines_covered": int(root.get("lines-covered")),
        "lines_valid": int(root.get("lines-valid")),
        "branch_rate": float(root.get("branch-rate")),
        "branches_valid": int(root.get("branches-valid")),
        "sources": [s.text for s in root.findall("./sources/source")],
    }


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="权威覆盖率采集：口径来自 pyproject，分块 + 逐块完整性校验")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="产物目录")
    ap.add_argument("--chunks", type=int, default=10, help="分块数（默认 10，与 TASK-03 一致）")
    ap.add_argument("--only-chunk", type=int, default=None, help="只跑第 N 块（1 起数，用于补跑被强杀的块）")
    ap.add_argument("--packages", default=None, help="覆盖口径；默认读 pyproject 的 9 个包。传单包即为子集口径")
    ap.add_argument("--timeout", type=int, default=300, help="单测试超时秒数")
    ap.add_argument(
        "--timeout-method",
        default="thread",
        choices=["thread", "signal"],
        help="pytest-timeout 超时法；与 pytest.ini 的 addopts 一致时才可复现线上现象",
    )
    ap.add_argument("--dry-run", action="store_true", help="只打印将要执行的命令，不真跑")
    args = ap.parse_args(argv)

    packages = (
        [p.strip() for p in args.packages.split(",") if p.strip()]
        if args.packages
        else read_declared_packages()
    )
    subset = len(packages) == 1

    files = collect_test_files()
    n = max(1, args.chunks)
    chunks = [c for c in (chunk_of(files, n, i) for i in range(n)) if c]
    if args.only_chunk is not None:
        chunks = [chunks[args.only_chunk - 1]]

    args.out.mkdir(parents=True, exist_ok=True)
    print("=" * 78)
    print("权威覆盖率采集")
    print("=" * 78)
    print(f"仓库根        : {ROOT}")
    print(f"口径（包）    : {len(packages)} 个 → {packages}")
    print(f"口径标注      : {'**子集口径（单包，数字不可与全量比较）**' if subset else '全量口径（与 pyproject 声明一致）'}")
    print(f"测试文件      : {len(files)} 个（tests/unit）")
    print(f"分块          : {len(chunks)} 块 × 约 {len(chunks[0]) if chunks else 0} 文件")
    print(f"超时          : --timeout={args.timeout} --timeout-method={args.timeout_method}")
    print(f"产物目录      : {args.out}")

    if args.dry_run:
        for i, chunk in enumerate(chunks, 1):
            print(f"\n[chunk {i}] " + " ".join(build_cmd(chunk, packages, args.out, args.timeout, args.timeout_method)[2:]))
        return 0

    # coverage 数据统一放产物目录，避免污染仓库根的 .coverage
    data_file = args.out / ".coverage"
    if args.only_chunk is None and data_file.exists():
        data_file.unlink()

    env = dict(os.environ)
    env["COVERAGE_FILE"] = str(data_file)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    results = []
    for idx, chunk in enumerate(chunks, 1):
        log = args.out / f"chunk_{idx:02d}.log"
        cmd = build_cmd(chunk, packages, args.out, args.timeout, args.timeout_method)
        print(f"\n[chunk {idx}/{len(chunks)}] {len(chunk)} 文件 → {log.name}", flush=True)
        t0 = time.time()
        # Why 用文件句柄而不是管道：本沙箱受限模式下程序无法打开命名管道，
        # subprocess.run(capture_output=True) 会卡在 communicate() 的 join 上（假挂死）。
        with log.open("w", encoding="utf-8", errors="replace") as fh:
            proc = subprocess.run(cmd, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT, env=env)
        dt = time.time() - t0
        ok, detail = chunk_completed(log)
        results.append(
            {
                "chunk": idx,
                "files": len(chunk),
                "rc": proc.returncode,
                "seconds": round(dt, 1),
                "completed": ok,
                "evidence": detail,
                "file_list": chunk,
            }
        )
        flag = "✔ 已跑完" if ok else "✗ **未跑完（该块文件可能整体缺失）**"
        print(f"[chunk {idx}/{len(chunks)}] rc={proc.returncode} {dt:.0f}s {flag} :: {detail}", flush=True)

    manifest = args.out / "chunks.json"
    manifest.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    incomplete = [r for r in results if not r["completed"]]
    lost_files = [f for r in incomplete for f in r["file_list"]]

    print("\n" + "=" * 78)
    print("分块完整性")
    print("=" * 78)
    print(f"总块数        : {len(results)}")
    print(f"已完成        : {len(results) - len(incomplete)}")
    print(f"未跑完        : {len(incomplete)} → {[r['chunk'] for r in incomplete]}")
    print(f"受影响文件    : {len(lost_files)} 个（从未执行）")
    if lost_files:
        (args.out / "incomplete_files.txt").write_text("\n".join(lost_files) + "\n", encoding="utf-8")
        print(f"清单          : {args.out / 'incomplete_files.txt'}")
        print(f"补跑命令      : python scripts/run_authoritative_coverage.py --out {args.out} --only-chunk <N>")

    # 出报告（coverage 自己读 pyproject 的 report 配置；source 由 CLI 覆盖，见下）
    print("\n[coverage] 生成报告 ...", flush=True)
    xml_path = args.out / "coverage.xml"
    rc_xml = subprocess.run(
        [sys.executable, "-m", "coverage", "xml", "-o", str(xml_path), "--ignore-errors"],
        cwd=str(ROOT),
        env=env,
    ).returncode
    rc_total = subprocess.run(
        [sys.executable, "-m", "coverage", "report", "--format=total", "--ignore-errors"],
        cwd=str(ROOT),
        env=env,
    ).returncode

    summary: dict = {
        "packages": packages,
        "scope_label": "subset-single-package" if subset else "full-9-packages",
        "chunks": len(results),
        "incomplete_chunks": [r["chunk"] for r in incomplete],
        "lost_file_count": len(lost_files),
        "seconds_total": round(sum(r["seconds"] for r in results), 1),
        "xml_rc": rc_xml,
    }
    if xml_path.exists():
        summary.update(read_xml_total(xml_path))
        print(f"line-rate     : {summary['line_rate'] * 100:.2f}%  "
              f"({summary['lines_covered']}/{summary['lines_valid']} 行)")
        print(f"branch-rate   : {summary['branch_rate'] * 100:.2f}%  "
              f"(branches-valid={summary['branches_valid']})")
    summary_path = args.out / "coverage.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"摘要          : {summary_path}")

    if incomplete:
        print("\n✗ 存在未跑完的块 ⇒ 本次覆盖率数据不完整，**禁止作为基线**。")
        return 1
    print("\n✔ 所有块均已跑完，数据可用于基线。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
