#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""失败基线回归检查（TASK-03 第 3 步交付物）。

Why 需要这个脚本
----------------
`failures_baseline.txt` 固化了 68 FAILED + 10 ERROR，但**没有任何消费方**
（`git grep failures_baseline` 只命中 pytest.ini 的注释与文档）。也就是说它
只是一个"记事本"：新引入一条失败时没有任何东西会红，基线只会一天天变旧。
本脚本把它变成**可执行契约**：

    只允许失败集合**变小**；出现基线之外的新失败 ⇒ 非零退出。

Why 用"集合比较"而不是"数量比较"
--------------------------------
数量比较会被掩盖：修好 2 条旧失败 + 新引入 2 条失败 ⇒ 总数不变 ⇒ 门禁绿灯，
但那是**两条真实回归**。所以按 nodeid 集合做差集，而不是比总数。

Why 归一化要去掉消息、只留 `状态 + nodeid`
----------------------------------------
同一条失败的消息里常含临时路径（`.pytest_tmp\\tmpj_ikd4vl\\...`）、耗时、
随机 seed，逐字比较会永远不相等。nodeid 才是稳定身份。

用法
----
    # 跑测试并比对（默认 tests/unit）
    python scripts/check_baseline_regression.py

    # 只从已有 pytest 日志解析（不重跑，用于 CI 复用同一份产物）
    python scripts/check_baseline_regression.py --pytest-log _ci_logs/baseline_pytest.txt

    # 用实测结果收缩基线（**只允许删除已修复项**；新增项会被拒绝）
    python scripts/check_baseline_regression.py --pytest-log X --update

退出码
------
0 = 无新增失败（可含"已修复"信息）；1 = 出现基线之外的新失败；2 = 用法/文件错误。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPO_ROOT / "failures_baseline.txt"

#: 匹配 pytest 短摘要里的失败/错误行。
#: 例：`FAILED tests/unit/test_x.py::TestY::test_z - AssertionError: assert 1 == 2`
#:     `ERROR tests/unit/test_x.py`（收集期错误，无 nodeid 后缀）
#: Why 允许行首有空白：pytest 在部分终端下会给摘要行加缩进。
RESULT_RE = re.compile(r"^\s*(FAILED|ERROR)\s+(\S+?)(?:\s+-\s+.*)?$")

#: 匹配 `-q` 进度输出里的 `[ 12%]` 之类噪声，避免误判。
NOISE_RE = re.compile(r"^\s*\d+\s+(failed|passed|error)")


def normalize(status: str, nodeid: str) -> str:
    """把一条结果归一化成稳定键：`STATUS <nodeid>`（丢掉易变的消息部分）。"""
    nodeid = nodeid.strip()
    # Windows 反斜杠路径统一成 `/`，避免同一用例因路径分隔符不同被判成两条
    nodeid = nodeid.replace("\\", "/")
    return f"{status} {nodeid}"


def parse_results(text: str) -> dict[str, str]:
    """从 pytest 输出中解析 {归一化键: 原始行}。"""
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if NOISE_RE.match(line):
            continue
        m = RESULT_RE.match(line)
        if not m:
            continue
        status, nodeid = m.group(1), m.group(2)
        # 排除 `ERROR`/`FAILED` 出现在说明性文本里的情况：nodeid 必须带路径特征
        if "/" not in nodeid and "::" not in nodeid:
            continue
        found[normalize(status, nodeid)] = line.strip()
    return found


def read_baseline(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    # baseline 文件里有些行是纯注释/说明，parse_results 会自然过滤掉
    return parse_results(text)


def write_baseline(path: Path, entries: dict[str, str], header_lines: list[str]) -> None:
    lines = list(header_lines)
    for key in sorted(entries):
        lines.append(entries[key])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def run_pytest(args: list[str], timeout: int) -> tuple[str, int]:
    """运行 pytest 并返回 (输出, 退出码)。

    Why 追加 `-rfE`：`-q` 模式下若不同时要求，短摘要里可能不含失败明细，
    解析器就会得出"0 失败"的**假绿**。显式要求列出 failed/error 是让
    本脚本可靠的前提，不是可选优化。
    """
    cmd = [sys.executable, "-m", "pytest", *args]
    if not any(a.startswith("-r") for a in args):
        cmd.append("-rfE")
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return (proc.stdout or "") + (proc.stderr or ""), proc.returncode


def extract_seed(text: str) -> str | None:
    m = re.search(r"Using --randomly-seed=(\d+)", text)
    return m.group(1) if m else None


def extract_summary(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        if re.search(r"\d+ (passed|failed|error)", line) and ("=" in line or "warning" in line):
            return line.strip("= ").strip()
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="失败基线回归检查：只允许失败集合变小")
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--pytest-log", type=Path, default=None, help="从已有 pytest 输出文件解析，不重跑")
    ap.add_argument("--pytest-args", default="tests/unit -q --no-header -p no:cacheprovider --timeout=300")
    ap.add_argument("--timeout", type=int, default=7200, help="pytest 子进程超时（秒）")
    ap.add_argument("--update", action="store_true", help="用实测结果收缩基线（拒绝新增项）")
    ap.add_argument("--force-update", action="store_true", help="连同新增失败一起写入基线（危险，仅供人工确认后使用）")
    ap.add_argument("--report", type=Path, default=None, help="把本次比对结果写入 JSON")
    args = ap.parse_args(argv)

    if args.pytest_log:
        text = args.pytest_log.read_text(encoding="utf-8", errors="replace")
        rc = 0
        source = f"日志文件 {args.pytest_log}"
    else:
        text, rc = run_pytest(args.pytest_args.split(), args.timeout)
        source = f"实跑 pytest {args.pytest_args}"

    actual = parse_results(text)
    baseline = read_baseline(args.baseline)

    new = {k: v for k, v in actual.items() if k not in baseline}
    fixed = {k: v for k, v in baseline.items() if k not in actual}
    kept = {k: v for k, v in actual.items() if k in baseline}

    print("=" * 78)
    print("失败基线回归检查")
    print("=" * 78)
    print(f"来源      : {source}")
    print(f"基线文件  : {args.baseline}（{len(baseline)} 条）")
    print(f"本次实测  : {len(actual)} 条")
    seed = extract_seed(text)
    if seed:
        print(f"随机 seed : {seed}（pytest-randomly 生效；比较基线时须记录 seed）")
    summary = extract_summary(text)
    if summary:
        print(f"pytest 摘要: {summary}")
    print(f"仍存在    : {len(kept)}")
    print(f"已修复    : {len(fixed)}")
    print(f"**新增**  : {len(new)}")
    print("-" * 78)

    if fixed:
        # Why 截断：只跑单个文件时（负例演示 / 局部调试），"已修复"会列出整份基线
        # 的全部条目，把真正要看的"新增失败"淹掉。完整清单始终在 --report 的 JSON 里。
        print(f"✔ 已修复（可从基线移出，显示前 {min(len(fixed), 20)} 条）：")
        for k in sorted(fixed)[:20]:
            print(f"    {k}")
        if len(fixed) > 20:
            print(f"    …（共 {len(fixed)} 条；完整清单见 --report 的 JSON）")
    if new:
        print("✗ 新增失败（基线之外 ⇒ 视为回归）：")
        for k in sorted(new):
            print(f"    {new[k]}")
    if not new:
        print("✔ 无新增失败。")

    if args.update or args.force_update:
        if new and not args.force_update:
            print(
                "\n[check_baseline_regression] 拒绝 --update：本次有新增失败，"
                "把它们写进基线等于把回归「洗掉」。请先修掉，或显式 --force-update 并人工确认。",
                file=sys.stderr,
            )
            return 1
        header = [
            "# 失败基线（failures_baseline.txt）",
            "# 由 scripts/check_baseline_regression.py --update 生成；格式：<STATUS> <nodeid> [- 消息]",
            "# 消费方：scripts/check_baseline_regression.py（出现基线之外的新失败即非零退出）",
            "# 纪律：**只允许这个文件收缩**。新增条目必须由人工确认为「已知债务」后显式 --force-update。",
        ]
        write_baseline(args.baseline, actual, header)
        print(f"\n[check_baseline_regression] 基线已更新 → {args.baseline}（{len(actual)} 条）")

    if args.report:
        import json

        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "source": source,
                    "baseline_count": len(baseline),
                    "actual_count": len(actual),
                    "kept": sorted(kept),
                    "fixed": sorted(fixed),
                    "new": sorted(new),
                    "randomly_seed": seed,
                    "summary": summary,
                    "pytest_rc": rc,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[check_baseline_regression] 比对结果已写入 {args.report}")

    return 1 if new else 0


if __name__ == "__main__":
    raise SystemExit(main())
