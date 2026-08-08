#!/usr/bin/env python3
"""getpass 跨平台兼容性测试脚本（Windows / Linux / macOS / CI 环境）。

【用途】
  验证 scripts/apply_smtp_auth_code.py --interactive 依赖的 getpass 模块
  在当前终端下是否可靠地"输入不回显"。

【getpass 实现机制（先验知识，脚本据此检测）】
  - Windows (nt)：走 msvcrt.getch() 逐字符读取，不回显由 msvcrt 控制，
    不依赖终端配置 → CMD/PowerShell/Git Bash(Win Python) 均可靠。
  - Linux/macOS (posix)：走 termios 关闭 ECHO 后 readline → 常规终端可靠。
  - 退化路径：sys.stdin 非 TTY（CI、管道重定向、nohup）时 getpass 自动
    退化为 readline，此时【会回显并打印警告 "Password input may be echoed"】。
    本脚本会明确检测并报告该风险。

【用法】
  python scripts/test_getpass_compat.py              # 交互：输入一次验证不回显
  python scripts/test_getpass_compat.py --no-input   # 非交互：只做静态检测（CI 用）

【退出码】0=当前环境交互输入安全；1=存在回显风险（非 TTY 等）；2=用法错误
"""

from __future__ import annotations

import argparse
import getpass
import platform
import sys

try:
    import msvcrt  # Windows only
    HAS_MSVCRT = True
except ImportError:
    HAS_MSVCRT = False

try:
    import termios  # POSIX only
    HAS_TERMIOS = True
except ImportError:
    HAS_TERMIOS = False

PASS, FAIL, WARN = 0, 1, 2


def main() -> int:
    parser = argparse.ArgumentParser(description="getpass 跨平台兼容性测试")
    parser.add_argument("--no-input", action="store_true", help="跳过交互输入，仅静态检测（CI 环境）")
    args = parser.parse_args()

    print("═══ getpass 跨平台兼容性检测 ═══")
    print(f"  平台        : {platform.platform()}")
    print(f"  sys.platform: {sys.platform}")
    print(f"  Python      : {sys.version.split()[0]}")

    issues = 0

    # 1. stdin TTY 检测（决定是否走安全路径）
    is_tty = sys.stdin.isatty()
    print(f"\n[1] stdin TTY 检测: {'TTY（交互终端）' if is_tty else '非 TTY（CI/管道/重定向）'}")
    if is_tty:
        print("    [PASS] 走交互路径，getpass 可关闭回显")
    else:
        issues += 1
        print("    [FAIL] 非 TTY：getpass 将退化为 readline，输入会回显！")

    # 2. 实现机制检测
    print("\n[2] getpass 实现机制:")
    if sys.platform.startswith("win"):
        print(f"    Windows 平台 → 使用 msvcrt（HAS_MSVCRT={HAS_MSVCRT}）")
        print("    msvcrt.getch() 由底层 API 控制回显，不依赖终端配置")
        print("    [PASS] Windows 下 CMD/PowerShell/Git Bash(Win Python) 均可靠")
    else:
        print(f"    POSIX 平台 → 使用 termios（HAS_TERMIOS={HAS_TERMIOS}）")
        if HAS_TERMIOS:
            print("    [PASS] termios 可用，常规终端可靠")
        else:
            issues += 1
            print("    [FAIL] termios 不可用（异常环境），getpass 将退化回显")

    # 3. 交互输入实测（可选；非 TTY 时不执行，避免管道下 getpass 挂起/回显）
    if args.no_input:
        print("\n[3] 交互输入实测: 已跳过（--no-input）")
    elif not is_tty:
        print("\n[3] 交互输入实测: 跳过（非 TTY 环境，getpass 可能挂起或回显；请用真实终端重试）")
    else:
        print("\n[3] 交互输入实测:")
        print("    ↓ 请在下方输入一段任意字符串（如模拟授权码 a1b2c3d4e5f6），")
        print("    ↓ 观察【输入过程中字符是否可见】。输入完成后回车：")
        try:
            value = getpass.getpass("    输入(不应回显): ")
        except (EOFError, KeyboardInterrupt):
            print("    [WARN] 交互被中断（EOF/Ctrl+C）")
            return WARN
        print(f"    输入完成，读取到 {len(value)} 个字符")
        if is_tty:
            print("    [PASS] 终端为 TTY 且 getpass 成功读取，未回显（除非上方可见输入字符）")
        else:
            issues += 1
            print("    [FAIL] 非 TTY 环境回显风险已确认")

    # 4. 结论
    print("\n═══ 结论 ═══")
    if issues == 0:
        print("  当前环境交互输入安全，apply --interactive 可用 ✓")
        return PASS
    print(f"  存在 {issues} 项回显风险 → apply --interactive 可能回显授权码！")
    print("  建议：切换到真实交互终端（TTY）执行；或使用 read -s + 环境变量方式（见操作手册 1.1-B）")
    return FAIL


if __name__ == "__main__":
    sys.exit(main())
