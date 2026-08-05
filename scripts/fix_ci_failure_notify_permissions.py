#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_ci_failure_notify_permissions.py — 确保 CI 失败通知 workflow 拥有 issues: write 权限

背景（2026-08-05）: ci-failure-notify.yml "创建 GitHub Issue" 步骤调 github-script 的
issues.create / issues.listForRepo，顶层 permissions 仅 actions/contents read → 403
"Resource not accessible by integration"，通知链路 failure（run 31022043462/31022373915）。

本脚本幂等修复：
  1. 目标文件 .github/workflows/ci-failure-notify.yml 顶层 permissions 缺 issues: write 时补上
  2. --check 模式供 CI 门禁使用（缺权限 exit 1，防回归）
  3. 顺带扫描其他 workflow 中 github-script 调用 issues.* API 却缺 issues 权限（WARN 不阻断）

【简易】行级解析 permissions 块（保留注释与格式），不做整文件 YAML 重写。
【不易】只增不改：已存在的权限项绝不删除/重排；重复执行结果一致（幂等）。

用法:
    python scripts/fix_ci_failure_notify_permissions.py            # 修复（幂等）
    python scripts/fix_ci_failure_notify_permissions.py --check    # 仅检查, 缺权限 exit 1
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGET_REL = ".github/workflows/ci-failure-notify.yml"
ISSUES_LINE = "  issues: write"

# 【不易】issues.* API 需要的权限映射（2026-08-05 精确判定增强）：
#   - issues.create/update/listForRepo/addLabels → 独立 issue 读写，需 issues: write
#   - issues.createComment → PR 评论场景（GitHub 的 PR 评论复用 issues API），需 pull-requests: write
# 旧扫描器用 "issues:" 子串判定，把已配 pull-requests: write 的 workflow（如
# observability-ci.yml 各评论 job）误标 WARN。按 API 区分后消除误报。
ISSUES_API_RE = re.compile(r"github\.rest\.issues\.(\w+)\(")
PERM_BY_API = {
    "create": "issues",
    "update": "issues",
    "listForRepo": "issues",
    "addLabels": "issues",
    "createComment": "pull-requests",
}


def _find_permissions_block(lines: list[str]) -> tuple[int, int] | None:
    """定位顶层 permissions 块 [start, end)；未找到返回 None。

    【不易】permissions: 必须出现在顶层（前导非空白，且非 job 内缩进）。
    """
    start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "permissions:" and not line.startswith((" ", "\t")):
            start = i
            break
    if start is None:
        return None
    end = start + 1
    while end < len(lines):
        line = lines[end]
        # 子项以 2 空格缩进且非空；遇到非缩进行或空行结束
        if line.startswith("  ") and line.strip():
            end += 1
        else:
            break
    return start, end


def ensure_issues_write(path: Path, check_only: bool) -> tuple[bool, str]:
    """确保 workflow 顶层 permissions 含 issues: write。返回 (changed, detail)。"""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    block = _find_permissions_block(lines)
    if block is None:
        return False, "未找到顶层 permissions 块"
    start, end = block
    if any("issues:" in ln for ln in lines[start:end]):
        return False, "issues 权限已存在（幂等跳过）"
    if check_only:
        return True, "缺 issues: write（需修复）"
    lines.insert(end, ISSUES_LINE)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True, "已补 issues: write"


def _top_permissions(lines: list[str]) -> set[str]:
    """顶层 permissions 块（无缩进的 permissions:）→ 完整权限项集合（如 {"issues: write"}）。"""
    perms: set[str] = set()
    for i, line in enumerate(lines):
        if line.strip() == "permissions:" and not line.startswith((" ", "\t")):
            j = i + 1
            while j < len(lines) and lines[j].startswith("  ") and lines[j].strip():
                item = lines[j].strip()
                if not item.startswith("#"):  # 跳过注释行
                    perms.add(item)
                j += 1
            break
    return perms


def _job_context(lines: list[str], idx: int) -> tuple[str, set[str]]:
    """定位调用行所属 job，返回 (job 名, 该 job permissions 的 scope 集合)。"""
    job = "(top-level)"
    job_start = 0
    for i in range(idx - 1, -1, -1):
        line = lines[i]
        if line.strip() == "jobs:" and not line.startswith((" ", "\t")):
            break
        # job 定义: 2 空格缩进 + 名称 + 冒号（jobs: 下的直接子项）
        if re.match(r"^  [a-zA-Z0-9_-]+:$", line):
            job = line.strip()[:-1]
            job_start = i
            break
    perms: set[str] = set()
    if job != "(top-level)":
        # 该 job 内的 permissions 块（4 空格缩进），从 job 定义到调用行之间
        for i in range(job_start + 1, idx):
            line = lines[i]
            if re.match(r"^  [a-zA-Z0-9_-]+:$", line):
                break  # 已越过本 job（防御，正常不会发生）
            if line.startswith("    permissions:"):
                j = i + 1
                while j < len(lines) and lines[j].startswith("      ") and lines[j].strip():
                    item = lines[j].strip()
                    if not item.startswith("#"):  # 跳过注释行
                        perms.add(item)
                    j += 1
                break
    return job, perms


def scan_other_workflows() -> tuple[list[tuple[str, str, str, str]], list[tuple[str, str, str, str]]]:
    """扫描其他 workflow 的 issues.* 调用权限缺口。

    返回 (warns, infos)：warn 为缺权限调用点 (文件, job, API, 所需权限)；
    info 为已满足权限的调用点（消除误报）。【不易】只读扫描，不自动修复。
    """
    warns: list[tuple[str, str, str, str]] = []
    infos: list[tuple[str, str, str, str]] = []
    wf_dir = PROJECT_ROOT / ".github" / "workflows"
    for wf in sorted(wf_dir.glob("*.yml")):
        if wf.name == Path(TARGET_REL).name:
            continue
        lines = wf.read_text(encoding="utf-8", errors="replace").splitlines()
        top = _top_permissions(lines)
        for i, line in enumerate(lines):
            m = ISSUES_API_RE.search(line)
            if not m:
                continue
            perm = PERM_BY_API.get(m.group(1))
            if perm is None:
                continue
            job, job_perms = _job_context(lines, i)
            scope = f"{perm}: write"
            if scope in job_perms or scope in top:
                infos.append((wf.name, job, m.group(1), perm))
            else:
                warns.append((wf.name, job, m.group(1), perm))
    return warns, infos


def main() -> int:
    ap = argparse.ArgumentParser(
        description="CI 失败通知 workflow issues 权限修复/门禁（幂等）")
    ap.add_argument("--check", action="store_true",
                    help="仅检查，缺权限 exit 1（CI 门禁用）")
    ap.add_argument("--repo-root", default=str(PROJECT_ROOT), help="仓库根目录")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve()
    target = root / TARGET_REL
    if not target.exists():
        print(f"[ERROR] 目标 workflow 不存在: {target}", file=sys.stderr)
        return 1

    changed, detail = ensure_issues_write(target, args.check)
    if args.check:
        if changed:
            print(f"::error::[issues-perm] {target.name}: {detail} → BLOCK")
        else:
            print(f"::notice::[issues-perm] {target.name}: {detail} → PASS")
    else:
        print(f"[{'FIXED' if changed else 'OK'}] {target.name}: {detail}")
        if changed:
            print(f"[FIXED] 已写入: {ISSUES_LINE}")

    warns, infos = scan_other_workflows()
    for fname, job, api, perm in warns:
        print(f"[WARN] {fname} ({job}): {api} 需 {perm}: write 但缺失")
    for fname, job, api, perm in infos:
        print(f"[INFO] {fname} ({job}): {api} 已有 {perm}: write")

    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
