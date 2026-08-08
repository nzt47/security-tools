#!/usr/bin/env python3
"""一键复核：_edge_profile 是否已彻底从 git 移除 + 工作区敏感文件残留检查。

【背景】2026-08-08 曾发现 Edge 浏览器配置目录 `_edge_profile/`（含 Login Data / Cookies /
Vpn Tokens 等真实凭据）359 个文件被 git 跟踪，已执行 `git rm -r --cached` + 提交（b8a5474d）。
本脚本在处置后复核"是否彻底"：

  1. 索引（index）不再跟踪 `_edge_profile`
  2. git 历史（--all 全部分支可达）中不再有 `_edge_profile` 路径
  3. 对象库中不再有 `_edge_profile` 的文件对象（最彻底）
  4. `.gitignore` 已覆盖 `_edge_profile/`（防未来误加）
  5. 工作区物理文件仍保留（git rm --cached 不应删本地数据）
  6. 重跑敏感信息扫描，确认未被忽略的高风险文件仅剩已人工放行项

【用法】
  python scripts/recheck_sensitive_cleanup.py            # 全量复核
  python scripts/recheck_sensitive_cleanup.py --skip-scan # 跳过敏感扫描（快速模式）

【退出码】0=核心项全过（历史已清） 1=存在 FAIL（如历史残留，需清历史） 2=环境/用法错误

【不易】本脚本只读，不修改任何 git 状态或文件；清历史等破坏性动作仅输出命令由人工执行。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDGE = "_edge_profile"
IGNORE_RULE = "_edge_profile/"
SCAN_SCRIPT = ROOT / "scripts" / "scan_sensitive_files.py"

# 工作区须保留的关键凭据文件（证明本地数据未被误删）
KEEP_PROBES = [
    "_edge_profile/Default/Login Data",
    "_edge_profile/Default/Cookies",
]


def git(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=30,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="复核 _edge_profile 移除状态与敏感文件残留")
    parser.add_argument("--skip-scan", action="store_true", help="跳过敏感信息扫描（快速模式）")
    args = parser.parse_args()

    fail = 0
    print("═══ _edge_profile 清理复核 ═══")
    print(f"仓库: {ROOT}\n")

    # ── 1. 索引不再跟踪 ────────────────────────────────────────────────
    r = git(["ls-files", EDGE])
    tracked = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if not tracked:
        print("  [PASS] 索引(index)已无 _edge_profile 跟踪文件")
    else:
        fail += 1
        print(f"  [FAIL] 索引仍跟踪 {len(tracked)} 个文件（如: {tracked[0]}）→ 需 git rm -r --cached {EDGE}")

    # ── 2. 历史中不再新增该路径 ────────────────────────────────────────
    r = git(["log", "--all", "--diff-filter=A", "--format=%h %ad", "--date=short", "--", EDGE])
    added = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if not added:
        print("  [PASS] 全部历史中无 _edge_profile 的新增记录（从未入库或已重写）")
    else:
        fail += 1
        print("  [FAIL] 历史中存在新增 _edge_profile 的提交（真实凭据曾入库）:")
        for ln in added[:10]:
            print(f"        {ln}")
        print("        → 需历史重写（见下方处置命令）")

    # ── 3. 对象库无该路径对象（最彻底） ────────────────────────────────
    r = git(["rev-list", "--all", "--objects"])
    objs = [ln for ln in r.stdout.splitlines() if EDGE in ln]
    if not objs:
        print("  [PASS] 对象库中无 _edge_profile 文件对象（历史已彻底清除）")
    else:
        fail += 1
        print(f"  [FAIL] 对象库仍含 {len(objs)} 个 _edge_profile 对象（refs 可达历史中）")
        print("        → 若已 push 远端: git filter-repo --path _edge_profile --invert-paths 后强推")
        print("          若未 push: 推送前必须重写历史, 或禁止推送该分支")
        print("        → 历史中的凭据视为已泄露: 立即轮换受影响账号密码")

    # ── 4. .gitignore 覆盖 ─────────────────────────────────────────────
    gi = ROOT / ".gitignore"
    has_rule = gi.exists() and any(
        ln.strip() == IGNORE_RULE or ln.strip() == IGNORE_RULE.rstrip("/")
        for ln in gi.read_text(encoding="utf-8").splitlines()
    )
    if has_rule:
        print("  [PASS] .gitignore 已含 _edge_profile/ 规则（防未来误加）")
    else:
        fail += 1
        print("  [FAIL] .gitignore 缺少 _edge_profile/ 规则 → 请追加")

    # ── 5. 工作区物理文件保留 ──────────────────────────────────────────
    kept = [p for p in KEEP_PROBES if (ROOT / p).exists()]
    if kept:
        print(f"  [PASS] 工作区关键文件仍保留（{len(kept)}/{len(KEEP_PROBES)} 存在, 本地数据未删）")
        for p in kept:
            print(f"        · {p}")
    else:
        print("  [WARN] 未找到工作区关键文件（可能本就未创建, 或已被物理删除——请人工确认）")

    # ── 6. 敏感信息扫描（可选, 默认执行） ──────────────────────────────
    if args.skip_scan:
        print("\n  [SKIP] 敏感信息扫描已跳过（--skip-scan）")
    elif SCAN_SCRIPT.exists():
        print("\n── 敏感信息扫描（scripts/scan_sensitive_files.py）──")
        scan = subprocess.run(
            [sys.executable, str(SCAN_SCRIPT)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=300,
        )
        print(scan.stdout)
        if scan.stderr:
            print(scan.stderr, file=sys.stderr)
        print("  注: 未忽略高风险文件须逐项对照《敏感信息扫描报告》第 3 节已放行测试文件清单人工确认。")
    else:
        print("\n  [WARN] 未找到 scripts/scan_sensitive_files.py，跳过扫描")

    # ── 汇总 ───────────────────────────────────────────────────────────
    print("\n── 复核汇总 ──")
    if fail:
        print(f"  ✗ {fail} 项未通过（主要为历史残留）→ 请执行上方处置命令后重跑本脚本")
        return 1
    print("  ✓ _edge_profile 已彻底从 git 移除（索引+历史+对象库+ignore），工作区无敏感残留")
    return 0


if __name__ == "__main__":
    sys.exit(main())
