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

用法：
  python scripts/fix_ps_bom.py              # dry-run 预览（不写盘）
  python scripts/fix_ps_bom.py --apply      # 实际写入修复
  python scripts/fix_ps_bom.py --check      # 仅检测：存在异常 exit 1
  python scripts/fix_ps_bom.py --quiet      # 仅输出报告
  python scripts/fix_ps_bom.py --roots src  # 追加扫描目录（可多次）
"""
import argparse
import sys
from pathlib import Path

BOM = b"\xef\xbb\xbf"

# 关键契约文件：缺 BOM 需要补上（相对仓库根目录）
REQUIRE_BOM_DEFAULT = ["scripts/dev/hook_fail_safe.psm1"]


def count_leading_bom(data: bytes) -> int:
    i = 0
    while data.startswith(BOM, i):
        i += 3
    return i // 3


def is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def hex_head(data: bytes, n: int = 8) -> str:
    return " ".join(f"{b:02X}" for b in data[:n])


def main() -> int:
    ap = argparse.ArgumentParser(description="批量检测并修复 PS 文件 BOM 异常")
    ap.add_argument("--apply", action="store_true", help="实际写入修复（默认 dry-run）")
    ap.add_argument("--check", action="store_true", help="仅检测模式：存在异常 exit 1")
    ap.add_argument("--quiet", action="store_true", help="仅输出报告")
    ap.add_argument("--roots", action="append", default=["scripts", "packages"],
                    help="扫描目录（相对当前目录，可多次）")
    args = ap.parse_args()

    cwd = Path.cwd()
    require_bom = [Path(p) for p in REQUIRE_BOM_DEFAULT]

    to_fix = []   # (path, action)
    problems = []  # (path, reason) 非法 UTF-8 等不可自动修复

    for base in args.roots:
        d = cwd / base
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if p.suffix.lower() not in (".ps1", ".psm1") or not p.is_file():
                continue
            data = p.read_bytes()
            n_bom = count_leading_bom(data)
            rel = p.relative_to(cwd)

            if not is_utf8(data):
                problems.append((rel, f"非法 UTF-8 (head: {hex_head(data)})"))
                continue
            if n_bom > 1:
                to_fix.append((rel, f"去叠加 BOM x{n_bom} → x1 (head: {hex_head(data)})"))
            elif n_bom == 0 and rel in require_bom:
                to_fix.append((rel, "补 BOM（关键契约文件）"))

    # 报告
    if not args.quiet:
        for rel, action in to_fix:
            print(f"[待修复] {rel}: {action}")
        for rel, reason in problems:
            print(f"[异常]   {rel}: {reason}")
        print("---")
        print(f"待修复 {len(to_fix)} / 不可自动修复 {len(problems)}"
              f"（模式: {'APPLY' if args.apply else 'dry-run'}）")

    # 执行修复
    if args.apply:
        for rel, action in to_fix:
            p = cwd / rel
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
