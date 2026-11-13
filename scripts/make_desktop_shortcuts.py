#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""重建两个桌面快捷方式（2026-11-13 无窗口改造）—— 幂等，可重复跑。

【为什么要有这个脚本】桌面 .lnk 不在版本控制里，改了没记录 = 下一个人只能靠猜。
本脚本把"快捷方式应该长什么样"写成代码：

    旧：.lnk -> start_yunshu.bat            双击必开一个 cmd 黑窗（.bat 是控制台程序）
    新：.lnk -> wscript.exe scripts\\yunshu_launch.vbs start_yunshu
        wscript 是 GUI 子系统（自身没有控制台），用 Run(..., 0, False) 隐藏拉起 .bat

只写**当前用户桌面**的两个图标：不碰开始菜单、不注册计划任务（口径不变：看护只做手动快捷方式）。

用法：
    python scripts/make_desktop_shortcuts.py            # 创建/刷新两个快捷方式
    python scripts/make_desktop_shortcuts.py --print    # 只打印将执行的 PowerShell，不落盘
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "scripts" / "yunshu_launch.vbs"
#: 系统目录前缀（不在调用点写路径字面量）
SYSTEM_ROOT = Path(os.environ.get("SystemRoot", r"C:\Windows"))
WSCRIPT = SYSTEM_ROOT / "System32" / "wscript.exe"
POWERSHELL = SYSTEM_ROOT / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
PYTHON_EXE = Path(sys.executable)

#: 观察窗入口（可见窗口，只读）—— 与隐藏启动器分开，因为它是**故意**可见的那一个
OBSERVER_BAT = REPO_ROOT / "observe_yunshu.bat"

#: (桌面图标名, 启动器参数|None, 说明, 目标)
#: 参数为 None ⇒ 直接指向观察窗的 .bat（可见控制台）；否则 wscript + yunshu_launch.vbs
SHORTCUTS = [
    ("云枢一键启动.lnk", "start_yunshu",
     "启动云枢后端（生产构建）+ 打开观察窗与工作台 http://127.0.0.1:5678/chat", None),
    ("云枢状态.lnk", "observe",
     "打开云枢后台观察窗（只读：健康/版本/单位/覆盖/最近错误；关掉不影响后端）", OBSERVER_BAT),
    ("云枢看护.lnk", "watchdog",
     "手动启动云枢看护进程（每 30s 探活，连续 3 次失败自动重启后端；无控制台窗口）", None),
]

#: 快捷方式窗口样式：1 = 常规窗口（wscript 自身没有控制台 ⇒ 不会出现黑窗）
WINDOW_STYLE_NORMAL = 1
#: 创建快捷方式的超时（秒）
CREATE_TIMEOUT_SEC = 120


def _ps_string(text: object) -> str:
    """PowerShell 单引号字符串字面量（内部单引号翻倍）。"""
    return "'" + str(text).replace("'", "''") + "'"


def build_command(desktop: Path) -> str:
    """拼出创建两个快捷方式的 PowerShell 脚本。"""
    parts = ["$ws = New-Object -ComObject WScript.Shell"]
    for name, mode, desc, target in SHORTCUTS:
        link = desktop / name
        if target is None:
            exe, args = WSCRIPT, '"%s" %s' % (LAUNCHER, mode)
        else:
            exe, args = target, ""
        parts.append(
            "$s = $ws.CreateShortcut(%s); "
            "$s.TargetPath = %s; $s.Arguments = %s; $s.WorkingDirectory = %s; "
            "$s.WindowStyle = %d; $s.IconLocation = %s; $s.Description = %s; $s.Save()"
            % (_ps_string(link), _ps_string(exe), _ps_string(args),
               _ps_string(REPO_ROOT), WINDOW_STYLE_NORMAL,
               _ps_string("%s,0" % PYTHON_EXE), _ps_string(desc))
        )
    return "; ".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="创建/刷新云枢桌面快捷方式（隐藏启动器）")
    ap.add_argument("--print", dest="dry_run", action="store_true", help="只打印，不写快捷方式")
    args = ap.parse_args(argv)

    if not LAUNCHER.is_file():
        print("[ERROR] 找不到启动器：%s" % LAUNCHER)
        return 2
    if os.name != "nt":
        print("[SKIP] 只有 Windows 有 .lnk 快捷方式")
        return 0

    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    script = build_command(desktop)
    if args.dry_run:
        print(script)
        return 0

    proc = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=CREATE_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        print("[ERROR] 创建快捷方式失败：%s" % ((proc.stderr or "").strip()[:500] or proc.returncode))
        return 1
    for name, mode, _desc, target in SHORTCUTS:
        what = (target or LAUNCHER).name
        print("[OK] %s -> %s %s" % (desktop / name, what, mode if target is None else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
