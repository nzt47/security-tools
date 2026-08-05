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


def scan_other_workflows() -> list[tuple[str, str]]:
    """扫描其他 workflow：github-script 调 issues.* API 但无 issues 权限 → WARN。"""
    warns: list[tuple[str, str]] = []
    wf_dir = PROJECT_ROOT / ".github" / "workflows"
    for wf in sorted(wf_dir.glob("*.yml")):
        if wf.name == Path(TARGET_REL).name:
            continue
        text = wf.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"issues\.(create|listForRepo|update|addLabels)", text):
            continue
        if "issues:" not in text:
            warns.append((wf.name, "github-script 调用 issues.* API 但文件内无 issues 权限"))
    return warns


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

    for fname, reason in scan_other_workflows():
        print(f"[WARN] {fname}: {reason}")

    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
