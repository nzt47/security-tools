#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lint: 扫描 GitHub Actions workflow_run 工作流中的"守卫反模式"。

反模式定义(详见 docs/ci_guidelines/workflow_run_guard.md):
  守卫 step 的 run 块同时出现以下三者,把"跳过"误标为"失败":
    1. workflow_run.conclusion 引用(直接或经变量赋值)
    2. exit 1
    3. != success 类判断
  后果: 上游被 cancelled/failure 时,守卫 job 自身 failed -> 整条流水线
  conclusion=failure -> 发噪音失败邮件,但下游测试并未运行。

正解: 用 job outputs.should_run 串联下游,守卫永不 exit 1。

用法:
  python scripts/lint_workflow_guard.py [.github/workflows]

退出码:
  0 = 未发现反模式
  1 = 发现反模式(CI 应阻塞)
  2 = 脚本自身错误(YAML 解析失败等)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - 环境问题,显式报错
    print("ERROR: 需要 PyYAML,请运行 `pip install pyyaml`", file=sys.stderr)
    sys.exit(2)


# ── 反模式信号 ────────────────────────────────────────────────────────
# Why 直接匹配字符串: GitHub Actions 中 conclusion 必须从 ${{ github.event.workflow_run.conclusion }}
# 取值,故 run 块内一定出现该子串(即便赋给变量 CONCLUSION)。
CONCLUSION_REF = re.compile(r"workflow_run\.conclusion")
EXIT_1 = re.compile(r"\bexit\s+1\b")
# 匹配 != "success" / != 'success' / != success
NOT_SUCCESS = re.compile(r"!=\s*[\"']?success[\"']?")


def _get_on(workflow: dict) -> Any:
    """取 workflow 的 on 触发器。

    Why 兼容 PyYAML: YAML 1.1 会把裸 key `on:` 解析成 Python True,
    故需同时查 "on" 与 True 两个键。
    """
    return workflow.get("on") if "on" in workflow else workflow.get(True)


def scan_file(path: Path) -> list[tuple[str, ...]]:
    """扫描单个 workflow 文件,返回 issue 列表。

    issue 形如:
      ("anti_pattern", job_name, step_name)
      ("parse_error", error_message)
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 任意解析错误都收集,不中断扫描
        return [("parse_error", str(exc))]

    if not isinstance(data, dict):
        return []

    on = _get_on(data)
    # 仅 workflow_run 触发的工作流才可能命中本反模式;非 dict 形式(字符串/list)跳过
    if not (isinstance(on, dict) and "workflow_run" in on):
        return []

    issues: list[tuple[str, ...]] = []
    jobs = data.get("jobs", {}) or {}
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps", []) or []:
            if not isinstance(step, dict):
                continue
            run = step.get("run")
            if not isinstance(run, str):
                continue
            # 三信号同时命中 -> 反模式
            if (
                CONCLUSION_REF.search(run)
                and EXIT_1.search(run)
                and NOT_SUCCESS.search(run)
            ):
                step_name = step.get("name", "<unnamed>")
                issues.append(("anti_pattern", str(job_name), str(step_name)))
    return issues


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="扫描 workflow_run 工作流中 conclusion+exit 1 的守卫反模式"
    )
    ap.add_argument(
        "path",
        nargs="?",
        default=".github/workflows",
        help="workflow 目录(默认 .github/workflows)",
    )
    args = ap.parse_args(argv)

    root = Path(args.path)
    if not root.exists():
        print(f"ERROR: 路径不存在: {root}", file=sys.stderr)
        return 2

    files = sorted(root.glob("*.yml")) + sorted(root.glob("*.yaml"))
    anti_count = 0
    parse_errors = 0

    for f in files:
        for issue in scan_file(f):
            if issue[0] == "parse_error":
                print(f"PARSE_ERROR  {f}: {issue[1]}")
                parse_errors += 1
            else:
                _, job, step = issue
                print(
                    f"ANTI_PATTERN {f} :: job='{job}' step='{step}'\n"
                    f"  -> 守卫 step 同时含 workflow_run.conclusion + exit 1 + != success,"
                    f"疑似把'跳过'标记为'失败'。改用 outputs.should_run 串联下游"
                    f"(见 docs/ci_guidelines/workflow_run_guard.md)"
                )
                anti_count += 1

    print(
        f"\n扫描 {len(files)} 个文件 | 反模式 {anti_count} 处 | 解析错误 {parse_errors} 处"
    )

    # 解析错误优先返回 2(脚本自身问题),否则反模式返回 1
    if parse_errors:
        return 2
    return 1 if anti_count else 0


if __name__ == "__main__":
    sys.exit(main())
