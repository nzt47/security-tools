#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_ps_bom.py — 批量检测并修复 PS 文件被意外添加的 BOM（一键运维工具）。

覆盖场景（2026-08-04 事故复盘）：其他进程/工具批量写入 .ps1/.psm1 时，
在文件头叠加了多个 BOM（EF BB BF EF BB BF …），导致 PS 5.1 块注释 `<#`
不再位于行首、注释内容被当成代码 → `Missing expression after unary operator`。

修复规则：
  1. 叠加 BOM（前导 EF BB BF ≥2 次）→ 去叠加，保留恰好 1 个
  2. 关键契约文件缺 BOM（scripts/dev/hook_fail_safe.psm1 等）→ 补 BOM
  3. 非法 UTF-8 → 报告，不自动修复（需人工介入）
  4. --fill-missing：补全所有缺 BOM 的 .ps1/.psm1（含历史遗留非关键文件）

用法：
  python scripts/fix_ps_bom.py              # dry-run 预览（不写盘）
  python scripts/fix_ps_bom.py --apply      # 实际写入修复
  python scripts/fix_ps_bom.py --fill-missing --apply   # 补全所有缺 BOM 文件
  python scripts/fix_ps_bom.py --check      # 仅检测：存在异常 exit 1（适合接入 CI/hook）
  python scripts/fix_ps_bom.py --quiet      # 仅输出报告
  python scripts/fix_ps_bom.py --roots src  # 追加扫描目录（可多次）
  python scripts/fix_ps_bom.py --repo-root <repo>  # 指定仓库根（hook 调用用）
"""
import argparse
import sys
from pathlib import Path

# BOM 契约单一事实源: 常量与纯函数见 ps_bom_contract.py(check_ps1_encoding 与
# fix_ps_bom 共用, 消除重复实现——2026-08-05 P0 合并)。此处重导出保持旧接口兼容。
import ps_bom_contract as _contract

BOM = _contract.BOM
REQUIRE_BOM_DEFAULT = _contract.REQUIRE_BOM_DEFAULT
count_leading_bom = _contract.count_leading_bom
is_utf8 = _contract.is_utf8
hex_head = _contract.hex_head
iter_ps_files = _contract.iter_ps_files


def main() -> int:
    ap = argparse.ArgumentParser(description="批量检测并修复 PS 文件 BOM 异常")
    ap.add_argument("--apply", action="store_true", help="实际写入修复（默认 dry-run）")
    ap.add_argument("--check", action="store_true", help="仅检测模式：存在异常 exit 1")
    ap.add_argument("--quiet", action="store_true", help="仅输出报告")
    ap.add_argument("--fill-missing", action="store_true",
                    help="补全所有缺 BOM 的 .ps1/.psm1（含非关键文件）")
    ap.add_argument("--repo-root", default=".",
                    help="仓库根目录（默认当前目录；hook 调用传 TLM_HOOK_SOURCE_REPO）")
    ap.add_argument("--roots", action="append", default=["scripts", "packages"],
                    help="扫描目录（相对 --repo-root，可多次）")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"[ERROR] 仓库根目录不存在: {root}", file=sys.stderr)
        return 1
    require_bom = [Path(p) for p in REQUIRE_BOM_DEFAULT]

    to_fix = []   # (path, action)
    problems = []  # (path, reason) 非法 UTF-8 等不可自动修复

    for p in iter_ps_files(root, bases=args.roots):
        data = p.read_bytes()
        n_bom = count_leading_bom(data)
        rel = p.relative_to(root)

        if not is_utf8(data):
            problems.append((rel, f"非法 UTF-8 (head: {hex_head(data)})"))
            continue
        if n_bom > 1:
            to_fix.append((rel, f"去叠加 BOM x{n_bom} → x1 (head: {hex_head(data)})"))
        elif n_bom == 0 and (rel in require_bom or args.fill_missing):
            note = "" if rel in require_bom else "（非关键文件）"
            to_fix.append((rel, f"补 BOM{note}"))

    # 报告：问题行始终输出（--quiet 仅隐藏汇总，hook 失败时可诊断）
    for rel, action in to_fix:
        print(f"[待修复] {rel}: {action}")
    for rel, reason in problems:
        print(f"[异常]   {rel}: {reason}")
    if not args.quiet:
        print("---")
        print(f"待修复 {len(to_fix)} / 不可自动修复 {len(problems)}"
              f"（模式: {'APPLY' if args.apply else 'dry-run'}"
              f"{' + fill-missing' if args.fill_missing else ''}）")

    # 执行修复
    if args.apply:
        for rel, action in to_fix:
            p = root / rel
            data = p.read_bytes()
            if action.startswith("去叠加"):
                p.write_bytes(BOM + data[count_leading_bom(data) * 3:])
            elif action.startswith("补 BOM"):
                p.write_bytes(BOM + data)
        if not args.quiet:
            print(f"已修复 {len(to_fix)} 个文件")

    # 退出码：--check 且存在异常 → 1
    if args.check and (to_fix or problems):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
