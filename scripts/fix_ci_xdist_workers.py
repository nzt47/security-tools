#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_ci_xdist_workers.py — 将 ci.yml 单元测试 xdist worker 数从 -n 2 调整为 -n 1（幂等）

背景（2026-08-05）: 云枢测试 Shard 3 (py3.12) INTERNALERROR: can't start new thread。
根因: xdist worker 下 --timeout-method=signal 自动降级为 thread 方法，每测试 1 个
Timer 线程，叠加测试自身线程逼近容器 pids.max。
决策（docs/observability/shard3_cannot_start_new_thread_analysis_20260805.md §4 方案 A）:
   -n 2 → -n 1 消除 thread 降级路径（signal 方法零线程创建），不损失超时保护，
   代价仅是运行时间 ~2x（单 shard 预计 10-16min，仍在 timeout-minutes 90 内）。
   适用条件: 监控报告峰值线程数 / pids.max > 80%（逼近限制）。

本脚本幂等修改 ci.yml 中"运行单元测试"步骤的 `-n 2` → `-n 1`：
  1. 仅匹配独立行 `-n 2`（strip 后），不误伤 -n 20/-n2 等；
  2. 出现次数必须为 1（ci.yml 当前仅 unit-tests 步骤一处），否则拒绝修改并 WARN；
  3. --check 模式供门禁使用（仍为 -n 2 时 exit 1）；
  4. 重复执行结果一致（幂等），支持 --value 自定义目标值。

【简易】单文件零第三方依赖，仅标准库。
【不易】只改 pytest 的 -n 行，不动其它超时/分片配置；行级替换保留缩进与注释。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGET_REL = ".github/workflows/ci.yml"
# 【不易】匹配独立行 "-n 2"（允许行尾反斜杠续行符），要求 12 空格缩进（pytest 命令内）
# 与 ci.yml 运行单元测试步骤的 pytest 块缩进一致，避免误伤顶层 env 等
OLD_LINE_RE = re.compile(r"^( {12}-n )2( \\?)$")
NEW_WORKERS = "1"


def ensure_workers(path: Path, check_only: bool, value: str) -> tuple[bool, str, int]:
    """确保 ci.yml 中 pytest 的 -n 参数为 value。返回 (changed, detail, matches)。"""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    matches: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = OLD_LINE_RE.match(line)
        if m:
            matches.append((i, line))
    if len(matches) == 0:
        # 可能已为目标值（-n 1）→ 幂等跳过
        target_re = re.compile(rf"^( {{12}}-n ){re.escape(value)}( \\?)$")
        done = sum(1 for ln in lines if target_re.match(ln))
        if done > 0:
            return False, f"-n 已是 {value}（幂等跳过）", done
        return False, "未找到 -n 独立行（格式变化，需人工确认）", 0
    if len(matches) > 1:
        return False, f"-n 行出现 {len(matches)} 处，拒绝自动修改（防误伤）", len(matches)
    if check_only:
        return True, f"-n 仍为 2（需调整为 {value}）", 1
    idx, old_line = matches[0]
    # 【不易】行级替换：仅替换数字，保留缩进与行尾续行符
    indent = old_line[: old_line.index("-n")]
    tail = old_line[old_line.rfind("2"):]
    new_line = f"{indent}-n {value}{tail[1:]}"
    lines[idx] = new_line
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True, f"已调整: -n 2 → -n {value}", 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="将 ci.yml 单元测试 xdist worker 数调整为 -n 1（幂等，方案 A）")
    ap.add_argument("--check", action="store_true",
                    help="仅检查，-n 仍为 2 时 exit 1（CI 门禁用）")
    ap.add_argument("--value", default=NEW_WORKERS,
                    help=f"目标 worker 数（默认 {NEW_WORKERS}）")
    ap.add_argument("--repo-root", default=str(PROJECT_ROOT), help="仓库根目录")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve()
    target = root / TARGET_REL
    if not target.exists():
        print(f"[ERROR] 目标 workflow 不存在: {target}", file=sys.stderr)
        return 1

    changed, detail, n = ensure_workers(target, args.check, args.value)
    if args.check:
        if changed:
            print(f"::error::[xdist-workers] {target.name}: {detail} → BLOCK")
        else:
            print(f"::notice::[xdist-workers] {target.name}: {detail} → PASS")
    else:
        print(f"[{'FIXED' if changed else 'OK'}] {target.name}: {detail}")
    # 【变易】出现 0/多处的格式变化必须告警，防止静默漏改
    if n == 0 or n > 1:
        print(f"[WARN] {target.name}: -n 独立行命中 {n} 处，自动修改被拒绝，请人工确认")
    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
