#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删除并重建 P0 安全验证 workflow 文件，绕过 GitHub Actions 平台缓存故障

使用场景：
    P0 回归测试 Job 持续因 "Set up job" 失败（48 小时内 16/17 次失败），
    且修改 Job 名称、修改 job_id、rerun-failed-jobs、workflow_dispatch 均无效。
    诊断为 GitHub Actions 平台对该 workflow 文件的持续性缓存故障。
    本脚本通过删除旧 workflow 文件并用新文件名创建，强制生成新 workflow ID。

操作流程：
    1. 备份当前 workflow 文件到 docs/security/archive/
    2. 通过 git rm 删除旧文件 .github/workflows/p0-security.yml
    3. 用新文件名 .github/workflows/p0-security-v2.yml 创建新 workflow（内容相同）
    4. 提交并推送
    5. 等待新 workflow 出现并触发首次运行
    6. 轮询首次运行结果，验证 P0 回归测试 Job 是否能成功分配运行器

使用方法：
    python scripts/rebuild_p0_workflow.py              # 交互模式（推荐）
    python scripts/rebuild_p0_workflow.py --yes         # 跳过确认提示
    python scripts/rebuild_p0_workflow.py --dry-run     # 仅模拟，不实际执行

前置条件：
    - 当前分支为 phase2-visibility-convergence
    - 工作目录干净（无未提交变更）
    - ~/.git-credentials 中有有效的 GitHub token

注意：
    - 此操作不可逆（但旧 workflow 的运行历史会保留在 GitHub Actions UI 中）
    - 新 workflow 会有全新的 workflow ID，所有 Job 缓存会被清除
    - 推送后会自动触发首次 CI 运行
"""
import os
import re
import sys
import json
import time
import shutil
import subprocess
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

REPO = "nzt47/security-tools"
BRANCH = "phase2-visibility-convergence"
OLD_WORKFLOW_PATH = ".github/workflows/p0-security.yml"
NEW_WORKFLOW_PATH = ".github/workflows/p0-security-v2.yml"
ARCHIVE_DIR = "docs/security/archive"
REPO_ROOT = Path(__file__).resolve().parent.parent


def get_token():
    cred_file = os.path.expanduser("~/.git-credentials")
    with open(cred_file, "r", encoding="utf-8") as f:
        return re.search(r"https://[^:]+:([^@]+)@github\.com", f.read()).group(1)


def make_request(url, token, method="GET", expected_status=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "workflow-rebuild",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        status = resp.status
        body = resp.read().decode("utf-8")
        if expected_status and status not in expected_status:
            return status, None, f"unexpected status: {status}, body: {body}"
        return status, json.loads(body) if body else None, None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, None, f"HTTPError {e.code}: {body}"
    except Exception as e:
        return None, None, f"Exception: {e}"


def run_git(args, check=True, capture=True):
    """执行 git 命令"""
    cmd = ["git"] + args
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        print(f"  ❌ git 命令失败 (exit {result.returncode})")
        if result.stderr:
            print(f"     stderr: {result.stderr}")
        sys.exit(1)
    return result


def check_prerequisites():
    """检查前置条件"""
    print("=== 检查前置条件 ===")

    # 检查当前分支
    result = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    current_branch = result.stdout.strip()
    if current_branch != BRANCH:
        print(f"  ❌ 当前分支是 '{current_branch}'，需要切换到 '{BRANCH}'")
        sys.exit(1)
    print(f"  ✅ 当前分支: {current_branch}")

    # 检查工作目录状态（只检查 workflow 文件）
    result = run_git(["status", "--porcelain", OLD_WORKFLOW_PATH])
    if result.stdout.strip():
        print(f"  ⚠️ {OLD_WORKFLOW_PATH} 有未提交的变更:")
        print(f"     {result.stdout.strip()}")
        print(f"     建议先提交或 stash 这些变更")
        response = input("  继续? (y/N): ").strip().lower()
        if response != "y":
            print("  已取消")
            sys.exit(0)

    # 检查旧 workflow 文件存在
    old_path = REPO_ROOT / OLD_WORKFLOW_PATH
    if not old_path.exists():
        print(f"  ❌ 旧 workflow 文件不存在: {OLD_WORKFLOW_PATH}")
        sys.exit(1)
    print(f"  ✅ 旧 workflow 文件存在: {OLD_WORKFLOW_PATH}")

    # 检查新 workflow 文件不存在（避免覆盖）
    new_path = REPO_ROOT / NEW_WORKFLOW_PATH
    if new_path.exists():
        print(f"  ⚠️ 新 workflow 文件已存在: {NEW_WORKFLOW_PATH}")
        print(f"     如需重新创建，请先手动删除该文件")
        response = input("  继续（将覆盖）? (y/N): ").strip().lower()
        if response != "y":
            print("  已取消")
            sys.exit(0)

    print()


def backup_old_workflow(dry_run=False):
    """备份旧 workflow 文件"""
    print("=== Step 1: 备份旧 workflow 文件 ===")
    old_path = REPO_ROOT / OLD_WORKFLOW_PATH
    archive_dir = REPO_ROOT / ARCHIVE_DIR

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"p0-security.yml.backup_{timestamp}"
    backup_path = archive_dir / backup_name

    if dry_run:
        print(f"  [dry-run] 将备份到: {backup_path}")
        print()
        return backup_path

    archive_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(old_path), str(backup_path))
    print(f"  ✅ 已备份到: {backup_path}")
    print()
    return backup_path


def create_new_workflow(dry_run=False):
    """创建新 workflow 文件（内容与旧文件相同，但文件名不同）"""
    print("=== Step 2: 创建新 workflow 文件 ===")
    old_path = REPO_ROOT / OLD_WORKFLOW_PATH
    new_path = REPO_ROOT / NEW_WORKFLOW_PATH

    if dry_run:
        print(f"  [dry-run] 将创建新文件: {NEW_WORKFLOW_PATH}（内容与旧文件相同）")
        print()
        return

    # 读取旧文件内容
    with open(old_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 写入新文件（内容完全相同，只是文件名不同）
    with open(new_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

    print(f"  ✅ 已创建新 workflow 文件: {NEW_WORKFLOW_PATH}")
    print(f"     内容与旧文件完全相同（确保功能一致）")
    print()


def remove_old_workflow(dry_run=False):
    """删除旧 workflow 文件"""
    print("=== Step 3: 删除旧 workflow 文件 ===")
    if dry_run:
        print(f"  [dry-run] 将执行: git rm {OLD_WORKFLOW_PATH}")
        print()
        return

    run_git(["rm", OLD_WORKFLOW_PATH])
    print(f"  ✅ 已删除旧 workflow 文件: {OLD_WORKFLOW_PATH}")
    print()


def commit_and_push(dry_run=False):
    """提交并推送变更"""
    print("=== Step 4: 提交并推送变更 ===")
    if dry_run:
        print(f"  [dry-run] 将执行: git add {NEW_WORKFLOW_PATH} && git commit && git push")
        print()
        return

    # 暂存新文件
    run_git(["add", NEW_WORKFLOW_PATH])

    # 提交
    commit_msg = (
        "ci(security): 删除并重建 P0 安全验证 workflow 绕过 GitHub Actions 平台故障\n\n"
        "背景：P0 回归测试 Job 在 48 小时内 16/17 次因 'Set up job' 失败，\n"
        "且修改 Job 名称、修改 job_id、rerun-failed-jobs、workflow_dispatch 均无效。\n"
        "诊断为 GitHub Actions 平台对 workflow 文件的持续性缓存故障。\n\n"
        "操作：\n"
        "1. 删除旧文件 .github/workflows/p0-security.yml\n"
        "2. 创建新文件 .github/workflows/p0-security-v2.yml（内容相同）\n"
        "3. 新文件名产生新 workflow ID，绕过平台缓存\n\n"
        "旧 workflow 的运行历史保留在 GitHub Actions UI 中。"
    )
    result = run_git(["commit", "-m", commit_msg], check=False)
    if result.returncode != 0:
        print(f"  ⚠️ commit 失败: {result.stderr}")
        sys.exit(1)
    print(f"  ✅ 已提交")

    # 推送
    result = run_git(["push", "origin", BRANCH], check=False)
    if result.returncode != 0:
        print(f"  ❌ push 失败: {result.stderr}")
        sys.exit(1)
    print(f"  ✅ 已推送到 {BRANCH}")

    # 获取 commit SHA
    result = run_git(["rev-parse", "--short", "HEAD"])
    commit_sha = result.stdout.strip()
    print(f"  Commit: {commit_sha}")
    print()
    return commit_sha


def wait_for_new_workflow(token, max_wait=180):
    """等待新 workflow 出现在 GitHub Actions 中"""
    print("=== Step 5: 等待新 workflow 出现 ===")
    print(f"  查询 workflows 列表（最多等待 {max_wait}s）...")

    start = time.time()
    while time.time() - start < max_wait:
        url = f"https://api.github.com/repos/{REPO}/actions/workflows?per_page=50"
        status, data, err = make_request(url, token)
        if err:
            print(f"  查询失败: {err}")
            time.sleep(10)
            continue

        for wf in data.get("workflows", []):
            if wf.get("path", "").endswith("p0-security-v2.yml"):
                print(f"  ✅ 发现新 workflow!")
                print(f"     workflow ID: {wf['id']}")
                print(f"     名称: {wf['name']}")
                print(f"     路径: {wf['path']}")
                print(f"     状态: {wf['state']}")
                return wf

        print(f"  未发现新 workflow，等待 10s 后重试...")
        time.sleep(10)

    print(f"  ⚠️ 超时未发现新 workflow")
    return None


def wait_for_first_run(token, commit_sha, max_wait=180):
    """等待首次 CI 运行出现"""
    print(f"\n=== Step 6: 等待首次 CI 运行（commit {commit_sha}）===")
    print(f"  查询运行列表（最多等待 {max_wait}s）...")

    start = time.time()
    while time.time() - start < max_wait:
        url = f"https://api.github.com/repos/{REPO}/actions/runs?branch={BRANCH}&per_page=5"
        status, data, err = make_request(url, token)
        if err:
            print(f"  查询失败: {err}")
            time.sleep(10)
            continue

        for run in data.get("workflow_runs", []):
            if run["head_sha"][:8] == commit_sha[:8] and "P0" in run.get("name", ""):
                print(f"  ✅ 发现首次运行!")
                print(f"     运行 ID: {run['id']}")
                print(f"     状态: {run['status']}")
                print(f"     URL: {run['html_url']}")
                return run

        print(f"  未发现首次运行，等待 10s 后重试...")
        time.sleep(10)

    print(f"  ⚠️ 超时未发现首次运行")
    return None


def poll_run(token, run_id, max_poll=20, interval=30):
    """轮询运行直到完成"""
    print(f"\n=== Step 7: 轮询运行 {run_id} 直到完成 ===")
    print(f"  最多轮询 {max_poll} 次，每次间隔 {interval}s")

    for i in range(max_poll):
        time.sleep(interval)
        url = f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/jobs?per_page=10"
        status, data, err = make_request(url, token)
        if err:
            print(f"  第 {i+1}/{max_poll} 次查询失败: {err}")
            continue

        jobs = data.get("jobs", [])
        all_completed = all(j.get("status") == "completed" for j in jobs)
        line_parts = []
        for j in jobs:
            c = j.get("conclusion", "?")
            s = j.get("status", "?")
            icon = "✅" if c == "success" else "❌" if c == "failure" else "⏳"
            line_parts.append(f"{icon}{j['name']}({c or s})")
        print(f"  第 {i+1}/{max_poll} 次: {' | '.join(line_parts)}")

        if all_completed:
            return jobs

    print(f"  ⚠️ 超时未完成")
    return None


def analyze_result(jobs):
    """分析运行结果"""
    print(f"\n=== Step 8: 结果分析 ===")
    if not jobs:
        print("  ⚠️ 无法获取 jobs 状态")
        return False

    all_success = True
    p0_regression_status = None
    for j in jobs:
        c = j.get("conclusion", "?")
        icon = "✅" if c == "success" else "❌" if c == "failure" else "⏳"
        failed_steps = [s["name"] for s in j.get("steps", []) if s.get("conclusion") == "failure"]
        failed_info = f" (失败步骤: {failed_steps})" if failed_steps else ""
        print(f"  {icon} {j['name']} | conclusion={c}{failed_info}")
        if c != "success":
            all_success = False
        # 识别 P0 回归测试 Job
        jname = j.get("name", "")
        if "P0" in jname and ("回归" in jname or "Regression" in jname):
            p0_regression_status = c

    print()
    if all_success:
        print("🎉🎉🎉 所有 Job 通过！P0 安全验证全绿！")
        print("🎉 删除并重建 workflow 成功绕过了 GitHub Actions 平台故障！")
        return True
    elif p0_regression_status == "success":
        print("🎉 P0 回归测试 Job 已通过！重建 workflow 解决了平台故障！")
        return True
    elif p0_regression_status == "failure":
        failed_steps = []
        for j in jobs:
            jname = j.get("name", "")
            if "P0" in jname and ("回归" in jname or "Regression" in jname):
                failed_steps = [s["name"] for s in j.get("steps", []) if s.get("conclusion") == "failure"]
        print(f"⚠️ P0 回归测试 Job 仍失败: {failed_steps}")
        if "Set up job" in failed_steps:
            print("   仍是 Set up job 失败，重建 workflow 未解决问题")
            print("   建议：联系 GitHub 支持，报告平台 bug")
        else:
            print("   非 Set up job 失败，可能是代码问题，请检查步骤详情")
        return False
    else:
        print("⚠️ 仍有 Job 失败，但 P0 回归测试状态未确认")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="删除并重建 P0 安全验证 workflow")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过确认提示")
    parser.add_argument("--dry-run", action="store_true", help="仅模拟，不实际执行")
    args = parser.parse_args()

    print("=" * 70)
    print("P0 安全验证 workflow 重建工具")
    print("=" * 70)
    print()
    print(f"仓库: {REPO}")
    print(f"分支: {BRANCH}")
    print(f"旧文件: {OLD_WORKFLOW_PATH}")
    print(f"新文件: {NEW_WORKFLOW_PATH}")
    print()

    if not args.dry_run:
        print("⚠️ 警告：此操作将：")
        print(f"  1. 备份 {OLD_WORKFLOW_PATH} 到 {ARCHIVE_DIR}/")
        print(f"  2. 删除 {OLD_WORKFLOW_PATH}")
        print(f"  3. 创建 {NEW_WORKFLOW_PATH}（内容相同）")
        print(f"  4. 提交并推送")
        print(f"  5. 等待新 workflow 出现并触发首次运行")
        print(f"  6. 轮询首次运行结果")
        print()
        if not args.yes:
            response = input("确认执行? (y/N): ").strip().lower()
            if response != "y":
                print("已取消")
                return
        print()

    # 前置检查
    check_prerequisites()

    # Step 1: 备份
    backup_old_workflow(dry_run=args.dry_run)

    # Step 2: 创建新文件
    create_new_workflow(dry_run=args.dry_run)

    # Step 3: 删除旧文件
    remove_old_workflow(dry_run=args.dry_run)

    # Step 4: 提交并推送
    commit_sha = commit_and_push(dry_run=args.dry_run)

    if args.dry_run:
        print("\n[dry-run] 模拟完成，未实际执行任何操作")
        return

    # Step 5: 等待新 workflow 出现
    token = get_token()
    new_wf = wait_for_new_workflow(token)
    if not new_wf:
        print("\n⚠️ 未发现新 workflow，请手动检查 GitHub Actions UI")
        return

    # Step 6: 等待首次运行
    first_run = wait_for_first_run(token, commit_sha)
    if not first_run:
        print("\n⚠️ 未发现首次运行，请手动触发 workflow_dispatch")
        print(f"   新 workflow ID: {new_wf['id']}")
        return

    # Step 7: 轮询运行
    final_jobs = poll_run(token, first_run["id"])

    # Step 8: 分析结果
    success = analyze_result(final_jobs)

    print("\n" + "=" * 70)
    if success:
        print("✅ 重建成功！P0 安全验证 workflow 已恢复正常")
    else:
        print("⚠️ 重建后仍有问题，请检查上方详情")
    print("=" * 70)


if __name__ == "__main__":
    main()
