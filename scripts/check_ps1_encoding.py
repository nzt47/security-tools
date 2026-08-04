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
  --quiet             仅输出结果汇总
  --fix               自动修复：去叠加 BOM（保留 1 个）/ 关键契约文件补 BOM
  --require-bom PATH  追加关键契约文件（可多次，相对 --repo-root）

退出码：0 = PASS / WARN（可放行），1 = BLOCK（阻止提交）
"""
import argparse
import sys
from pathlib import Path

BOM = b"\xef\xbb\xbf"

# 关键契约文件：缺 BOM → BLOCK（相对仓库根目录）
REQUIRE_BOM_DEFAULT = ["scripts/dev/hook_fail_safe.psm1"]


def count_leading_bom(data: bytes) -> int:
    """统计前导 EF BB BF 连续出现次数。"""
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


def iter_ps_files(root: Path):
    """遍历仓库 scripts/ 与 packages/ 下的 .ps1/.psm1。"""
    for base in ("scripts", "packages"):
        d = root / base
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if p.suffix.lower() in (".ps1", ".psm1") and p.is_file():
                yield p


def main() -> int:
    ap = argparse.ArgumentParser(
        description="PS 脚本编码契约检查（BLOCK/WARN 分级，支持 --fix 修复）")
    ap.add_argument("--repo-root", default=".", help="仓库根目录（默认当前目录）")
    ap.add_argument("--strict", action="store_true", help="WARN 升级为 BLOCK")
    ap.add_argument("--quiet", action="store_true", help="仅输出结果汇总")
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

    if not args.quiet:
        for rel, reason in fixed:
            print(f"[FIXED] {rel}: {reason}")
        for rel, reason in blocked:
            print(f"[BLOCK] {rel}: {reason}")
        for rel, reason in warned:
            print(f"[WARN]  {rel}: {reason}")
        print(f"---")
        print(f"扫描 {root}  →  BLOCK {len(blocked)} / WARN {len(warned)} / FIXED {len(fixed)}")

    if blocked:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
