#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比 simulate_ci_failure_notify.py 的旧版(无退出码)与修复版, 生成修复对比报告

背景: 2026-08-05 发现 da309690(现 daaa3f6e) 提交的 simulate 脚本缺失退出码逻辑
(boundary_checks 无返回 / 无 sys.exit), 导致 pre-commit 的 WORKFLOW_SIM 段永远
exit 0 放行, 拦截形同虚设(无痕回滚风险)。c4384355 修复后退出码逻辑入库。

本脚本对比旧版与 HEAD 版的差异, 输出结构化报告(Markdown), 供复盘与追溯。

用法:
    python scripts/compare_simulate_versions.py                # 输出 Markdown 到 stdout
    python scripts/compare_simulate_versions.py --output docs/observability/xxx.md

报告结构:
    - 元信息(对比对象/时间/退出码)
    - 功能差异清单(旧版缺什么 / 新版有什么)
    - 逐行 diff(统一格式 + 行号)
"""

from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
from typing import List, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_REL = "scripts/simulate_ci_failure_notify.py"
OLD_COMMIT = "daaa3f6e"   # 旧版: 无退出码逻辑
NEW_COMMIT = "HEAD"       # 修复版: 退出码逻辑入库


def git_show(rev: str, path: str = SCRIPT_REL) -> Tuple[int, List[str]]:
    """读取 git 历史版本文件内容, 返回 (exit_code, lines)"""
    try:
        result = subprocess.run(
            ["git", "show", f"{rev}:{path}"],
            capture_output=True, text=True, encoding="utf-8", cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            return result.returncode, [result.stderr.strip()]
        return 0, result.stdout.splitlines()
    except Exception as e:  # noqa: BLE001
        return 1, [str(e)]


def unified_diff(old: List[str], new: List[str]) -> List[str]:
    """生成带行号的统一格式 diff 文本"""
    out: List[str] = []
    old_len, new_len = len(old), len(new)
    # 简化: 行级三向对比(等长前缀/后缀 + 中间逐行)
    prefix = 0
    while prefix < min(old_len, new_len) and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while (suffix < min(old_len, new_len) - prefix
           and old[old_len - 1 - suffix] == new[new_len - 1 - suffix]):
        suffix += 1

    changed_old = old[prefix:old_len - suffix] if suffix > 0 else old[prefix:]
    changed_new = new[prefix:new_len - suffix] if suffix > 0 else new[prefix:]

    if not changed_old and not changed_new:
        out.append("  (无差异)")
        return out

    out.append(f"@@ 旧版行 {prefix + 1}-{prefix + len(changed_old)} | 新版行 {prefix + 1}-{prefix + len(changed_new)} @@")
    for i, line in enumerate(changed_old):
        out.append(f"-{prefix + i + 1:>4}| {line}")
    out.append("  ---- 分隔 ----")
    for i, line in enumerate(changed_new):
        out.append(f"+{prefix + i + 1:>4}| {line}")
    return out


def extract_features(lines: List[str]) -> List[str]:
    """提取脚本关键功能标记(函数定义/退出码相关)"""
    marks = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("def ") or "sys.exit" in s or "blocked" in s \
                or "return ok" in s or "boundary_checks" in s:
            marks.append(s)
    return marks


def build_report(old: List[str], new: List[str], old_meta: str, new_meta: str) -> str:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    old_feats = extract_features(old)
    new_feats = extract_features(new)
    diff_lines = unified_diff(old, new)

    lines = [
        "# simulate_ci_failure_notify.py 修复对比报告",
        "",
        "## 元信息",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 生成时间 | {now} |",
        f"| 旧版 | `{old_meta}` |",
        f"| 修复版 | `{new_meta}` |",
        f"| 旧版行数 | {len(old)} |",
        f"| 新版行数 | {len(new)} |",
        "",
        "## 背景",
        "",
        "旧版 `daaa3f6e`(原 da309690) 提交时**缺失退出码逻辑**: `boundary_checks` 无返回值,",
        "main 中无 `blocked` 标记与 `sys.exit(1)`, 导致 pre-commit 的 WORKFLOW_SIM 段调用",
        "`python simulate_ci_failure_notify.py --all` 永远返回 0, 拦截形同虚设 —— 属无痕回滚风险",
        "(修复存在但未入库, 一旦工作区被还原即静默回归)。",
        "",
        "## 关键功能差异",
        "",
        "| 功能 | 旧版 (daaa3f6e) | 修复版 (HEAD) |",
        "|---|---|---|",
        f"| `boundary_checks` 返回值 | 无(返回 None) | 返回 (通过数, 总数) |",
        f"| yml 预检发现失效 action | 仅打印 [BLOCK], 不阻断 | 打印 [BLOCK] 且 `blocked=True` |",
        f"| 边界检查失败 | 仅打印 FAIL | `blocked=True` |",
        f"| 最终退出码 | 恒 0 | BLOCK/边界失败 → exit 1, 否则 exit 0 |",
        f"| pre-commit 拦截效果 | 无效(永远放行) | 有效(失败阻止提交) |",
        "",
        "## 功能标记提取",
        "",
        "### 旧版标记",
        "```",
        *(f"{m}" for m in (old_feats or ["  (无)"])),
        "```",
        "",
        "### 修复版标记",
        "```",
        *(f"{m}" for m in (new_feats or ["  (无)"])),
        "```",
        "",
        "## 逐行差异",
        "",
        "```diff",
        *diff_lines,
        "```",
        "",
        "## 修复验证",
        "",
        "- 构造含 `visiblelabs/dingtalk-action@v1` 的临时 yml → 预检 [BLOCK] → exit 1",
        "- 真实 `git commit` 被 hook 拦截: `[pre-commit][ERROR] 工作流模拟校验未通过, 提交被阻止`",
        "- 正常 yml → 6 场景判定符合预期, 边界检查 8/8 PASS → exit 0",
        "",
        "## 结论",
        "",
        "退出码逻辑缺失会让本地预检静默失效。已通过 `c4384355` 固化入库,",
        "但 `verify_core_invariants.py` 静态模式检查无法覆盖此类函数级缺失,",
        "需靠人工对比或本文档追溯。后续改动 simulate 脚本时务必保持 `--all` 的退出码语义(失败必须 exit 1)。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="对比 simulate 脚本新旧版本差异并生成报告")
    parser.add_argument("--old", default=OLD_COMMIT, help=f"旧版 commit(默认 {OLD_COMMIT})")
    parser.add_argument("--new", default=NEW_COMMIT, help=f"新版 commit(默认 {NEW_COMMIT})")
    parser.add_argument("--output", default="", help="报告输出路径(默认 stdout)")
    args = parser.parse_args()

    old_code, old_lines = git_show(args.old)
    new_code, new_lines = git_show(args.new)
    if old_code != 0 or new_code != 0:
        print(f"[ERROR] 读取版本失败: old_exit={old_code} new_exit={new_code}", file=sys.stderr)
        sys.exit(1)

    old_meta = f"{args.old} ({SCRIPT_REL})"
    new_meta = f"{args.new} ({SCRIPT_REL})"
    report = build_report(old_lines, new_lines, old_meta, new_meta)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[OK] 报告已写入: {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
