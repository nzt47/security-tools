#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_ps1_encoding.py — PS 脚本(.ps1/.psm1) 编码契约检查（CI/Commit 门禁）。

分级策略（BLOCK 阻止提交，WARN 仅提示）：
  BLOCK → exit 1
    1. 非法 UTF-8（无法按 UTF-8 解码）
    2. 叠加 BOM（前导 EF BB BF 连续 ≥2 次，破坏 `<#` 块注释导致解析失败）
    3. 关键契约文件缺 BOM（PS 5.1 中文系统按 GBK 解码，无 BOM 中文乱码）
  WARN  → exit 0
    存量其他文件缺 BOM（历史遗留，仅提示，不自动修改）

关键契约文件（必须恰好 1 个 BOM）：
  scripts/dev/hook_fail_safe.psm1（hook 模板，Import-Module 依赖）
  可通过 --require-bom 追加

CLI:
  --repo-root PATH    仓库根目录（默认当前目录）
  --strict            WARN 升级为 BLOCK（存量缺 BOM 也阻止）
  --quiet             仅输出结果汇总（BLOCK 明细始终输出，供 hook 失败诊断）
  --fix               自动修复：去叠加 BOM（保留 1 个）/ 关键契约文件补 BOM
  --require-bom PATH  追加关键契约文件（可多次，相对 --repo-root）

退出码：0 = PASS / WARN（可放行），1 = BLOCK（阻止提交）
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
    # Windows runner 控制台默认 cp1252，print 中文 BLOCK 明细会 UnicodeEncodeError 崩溃，
    # 强制 stdout/stderr 使用 UTF-8，保证 BLOCK 诊断始终可输出（CI 门禁可见性）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        description="PS 脚本编码契约检查（BLOCK/WARN 分级，支持 --fix 修复）")
    ap.add_argument("--repo-root", default=".", help="仓库根目录（默认当前目录）")
    ap.add_argument("--strict", action="store_true", help="WARN 升级为 BLOCK")
    ap.add_argument("--quiet", action="store_true",
                    help="仅输出结果汇总（BLOCK 明细始终输出，供 hook 失败诊断）")
    ap.add_argument("--fix", action="store_true", help="自动修复（去叠加 BOM / 关键文件补 BOM）")
    ap.add_argument("--require-bom", action="append", default=[],
                    help="追加关键契约文件（相对 --repo-root，可多次）")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"[ERROR] 仓库根目录不存在: {root}", file=sys.stderr)
        return 1

    require_bom = [Path(p) for p in REQUIRE_BOM_DEFAULT + args.require_bom]
    blocked = []   # (path, reason)
    warned = []    # (path, reason)
    fixed = []

    for p in iter_ps_files(root):
        rel = p.relative_to(root)
        data = p.read_bytes()
        n_bom = count_leading_bom(data)
        is_utf8_ok = is_utf8(data)
        is_key = rel in require_bom

        # BLOCK 1：非法 UTF-8
        if not is_utf8_ok:
            blocked.append((rel, f"非法 UTF-8 (head: {hex_head(data)})"))
            continue
        # BLOCK 2：叠加 BOM
        if n_bom > 1:
            blocked.append((rel, f"叠加 BOM x{n_bom} (head: {hex_head(data)})"))
            if args.fix:
                # 去叠加：保留恰好 1 个 BOM
                keep = BOM + data[n_bom * 3:]
                p.write_bytes(keep)
                fixed.append((rel, f"去叠加 BOM x{n_bom} → x1"))
            continue
        # BLOCK 3 / WARN：缺 BOM
        if n_bom == 0:
            if is_key:
                blocked.append((rel, "关键契约文件缺 BOM"))
                if args.fix:
                    p.write_bytes(BOM + data)
                    fixed.append((rel, "补 BOM"))
            elif args.strict:
                blocked.append((rel, "缺 BOM（--strict 将 WARN 升级为 BLOCK）"))
            else:
                warned.append((rel, "缺 BOM（存量文件仅提示，不自动修改）"))
        # n_bom == 1：契约态，通过

    # BLOCK 明细始终输出(与 fix_ps_bom 同契约, 问题行不被 --quiet 吞掉):
    # hook 的 run_check 失败时会重跑本脚本过滤诊断行, 需据此定位具体文件。
    for rel, reason in blocked:
        print(f"[BLOCK] {rel}: {reason}")
    if not args.quiet:
        for rel, reason in fixed:
            print(f"[FIXED] {rel}: {reason}")
        for rel, reason in warned:
            print(f"[WARN]  {rel}: {reason}")
        print(f"---")
        print(f"扫描 {root}  →  BLOCK {len(blocked)} / WARN {len(warned)} / FIXED {len(fixed)}")

    if blocked:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
