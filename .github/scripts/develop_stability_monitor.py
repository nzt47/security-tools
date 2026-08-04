#!/usr/bin/env python3
"""Develop 分支 CI 稳定性监控脚本

由 .github/workflows/develop-ci-stability-monitor.yml 调用。
职责:
  1. 读取计数器文件,判断是否应跳过
  2. 用 gh CLI 查询本次 develop push 触发的关键 workflow 状态
  3. 汇总稳定性结论
  4. 递减计数器,记录历史
  5. 输出报告供 workflow 创建 Issue

退出码:
  0  正常(无论是否跳过)
  1  监控逻辑出错(计数器文件损坏、gh 查询失败等)

输出: 写入 /tmp/stability_report.md 供 workflow 读取;
       标准输出 key=value 供 workflow $GITHUB_OUTPUT 读取。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════

REPO_ROOT = Path(__file__).resolve().parents[2]
COUNTER_FILE = REPO_ROOT / ".github" / "monitoring" / "develop_stability_counter.json"
# 用 tempfile 跨平台;CI(Linux) 下解析为 /tmp/stability_report.md
REPORT_FILE = Path(tempfile.gettempdir()) / "stability_report.md"
COMMIT_SHA = os.environ.get("GITHUB_SHA", "")
BRANCH = "develop"


# ════════════════════════════════════════════════════════════
#  工具函数
# ════════════════════════════════════════════════════════════

def log(msg: str) -> None:
    """结构化日志输出到 stderr"""
    print(f"[monitor] {msg}", file=sys.stderr)


def set_output(key: str, value: str) -> None:
    """写入 $GITHUB_OUTPUT"""
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
    else:
        # 本地调试: 输出到 stdout
        print(f"{key}={value}")


def gh_run_list(workflow_filename: str, branch: str, limit: int = 5) -> list[dict[str, Any]]:
    """调用 gh CLI 查询 workflow run 列表

    返回: [{databaseId, status, conclusion, headSha, event}, ...]
    """
    try:
        result = subprocess.run(
            [
                "gh", "run", "list",
                f"--workflow={workflow_filename}",
                f"--branch={branch}",
                f"--limit={limit}",
                "--json", "databaseId,status,conclusion,headSha,event",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            log(f"gh run list 失败 ({workflow_filename}): {result.stderr.strip()}")
            return []
        return json.loads(result.stdout) if result.stdout.strip() else []
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        log(f"gh run list 异常 ({workflow_filename}): {e}")
        return []


def find_commit_run(runs: list[dict[str, Any]], commit_sha: str) -> dict[str, Any] | None:
    """从 run 列表中找出当前 commit 的 push 触发的 run"""
    for r in runs:
        if r.get("headSha") == commit_sha and r.get("event") == "push":
            return r
    # 回退: 当前 commit 的任意 run
    for r in runs:
        if r.get("headSha") == commit_sha:
            return r
    return None


# ════════════════════════════════════════════════════════════
#  核心逻辑
# ════════════════════════════════════════════════════════════

def load_counter() -> dict[str, Any] | None:
    """读取计数器文件"""
    if not COUNTER_FILE.exists():
        log(f"计数器文件不存在: {COUNTER_FILE}")
        return None
    try:
        return json.loads(COUNTER_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log(f"计数器文件 JSON 解析失败: {e}")
        return None


def query_workflows(workflows: list[str], commit_sha: str) -> list[dict[str, Any]]:
    """查询所有监控 workflow 在当前 commit 的状态

    返回: [{workflow, status, conclusion, run_id}, ...]
    """
    results: list[dict[str, Any]] = []
    for wf in workflows:
        runs = gh_run_list(wf, BRANCH, limit=5)
        run = find_commit_run(runs, commit_sha)
        if run is None:
            results.append({
                "workflow": wf,
                "status": "not_triggered",
                "conclusion": "-",
                "run_id": "-",
            })
        else:
            results.append({
                "workflow": wf,
                "status": run.get("status", "unknown"),
                "conclusion": run.get("conclusion") or "-",
                "run_id": str(run.get("databaseId", "-")),
            })
    return results


def build_report(rows: list[dict[str, Any]], commit_sha: str) -> tuple[str, dict[str, Any]]:
    """生成 Markdown 报告 + 稳定性结论

    返回: (markdown_report, summary_dict)
    """
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"# CI 稳定性快照 (commit {commit_sha[:7]})",
        f"# 检查时间: {checked_at}",
        "",
        "| Workflow | Status | Conclusion | Run ID |",
        "|----------|--------|------------|--------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['workflow']} | {r['status']} | {r['conclusion']} | {r['run_id']} |"
        )
    markdown = "\n".join(lines) + "\n"

    total = len(rows)
    success = sum(1 for r in rows if r["conclusion"] == "success")
    failure = sum(1 for r in rows if r["conclusion"] == "failure")
    cancelled = sum(1 for r in rows if r["conclusion"] == "cancelled")
    other = total - success - failure - cancelled
    failures = [r["workflow"] for r in rows if r["conclusion"] == "failure"]
    # 稳定定义: 无 failure 且无 cancelled（未触发的 workflow 视为 not_triggered,不阻塞）
    stable = failure == 0 and cancelled == 0
    summary = {
        "total": total,
        "success": success,
        "failure": failure,
        "cancelled": cancelled,
        "other": other,
        "failures": failures,
        "stable": stable,
    }
    return markdown, summary


def update_counter(counter: dict[str, Any], commit_sha: str, report: str, summary: dict[str, Any]) -> dict[str, Any]:
    """递减计数器并记录历史,返回更新后的计数器"""
    remaining = counter.get("remaining_checks", 0)
    new_remaining = max(0, remaining - 1)
    counter["remaining_checks"] = new_remaining
    counter["last_check_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    counter["last_check_commit"] = commit_sha
    history = counter.setdefault("history", [])
    history.append({
        "check_no": remaining,
        "commit": commit_sha,
        "checked_at": counter["last_check_at"],
        "stable": summary["stable"],
        "failure_count": summary["failure"],
        "report": report.strip(),
    })
    # 保留最近 10 条历史,避免文件膨胀
    counter["history"] = history[-10:]
    return counter


def save_counter(counter: dict[str, Any]) -> None:
    """保存计数器文件"""
    COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)
    COUNTER_FILE.write_text(
        json.dumps(counter, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════

def main() -> int:
    force_check = os.environ.get("FORCE_CHECK", "false") == "true"

    # 1. 读取计数器
    counter = load_counter()
    if counter is None:
        set_output("should_run", "false")
        log("计数器不可用,跳过")
        return 0

    remaining = counter.get("remaining_checks", 0)
    log(f"remaining_checks={remaining}, force_check={force_check}")

    # 2. 判断是否应跳过
    if remaining <= 0 and not force_check:
        set_output("should_run", "false")
        log(f"监控已完成(remaining={remaining}),自动跳过")
        return 0

    set_output("should_run", "true")
    set_output("remaining_before", str(remaining))

    if not COMMIT_SHA:
        log("GITHUB_SHA 未设置,无法监控")
        set_output("should_run", "false")
        return 0

    # 3. 查询 workflow 状态
    workflows = counter.get("monitored_workflows", [])
    if not workflows:
        log("计数器中未配置 monitored_workflows")
        return 1

    log(f"监控 {len(workflows)} 个 workflow,commit={COMMIT_SHA[:7]}")
    rows = query_workflows(workflows, COMMIT_SHA)

    # 4. 生成报告
    report, summary = build_report(rows, COMMIT_SHA)
    REPORT_FILE.write_text(report, encoding="utf-8")
    # 同时输出到 stdout 供 workflow 日志查看
    print(report)

    log(f"stable={summary['stable']}, failure={summary['failure']}, "
        f"cancelled={summary['cancelled']}")

    # 5. 递减计数器（force_check 模式不消耗计数器,纯查看）
    if force_check:
        new_remaining = remaining
        is_final = False
        log(f"force_check=true,不递减计数器(remaining 保持 {new_remaining})")
    else:
        updated = update_counter(counter, COMMIT_SHA, report, summary)
        new_remaining = updated["remaining_checks"]
        save_counter(updated)
        # is_final 仅在「正常递减到 0」时为 true,force_check 不触发
        is_final = new_remaining == 0
        log(f"计数器已更新: remaining_checks={new_remaining}, is_final={is_final}")

    # 6. 输出供 workflow 使用
    set_output("stable", "true" if summary["stable"] else "false")
    set_output("failure_count", str(summary["failure"]))
    set_output("new_remaining", str(new_remaining))
    set_output("is_final", "true" if is_final else "false")
    set_output("report_file", str(REPORT_FILE))

    return 0


if __name__ == "__main__":
    sys.exit(main())
