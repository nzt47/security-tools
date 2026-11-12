#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""取证工具：验证"子进程不弹黑窗"（2026-11-13，Owner: 启动与定时任务别再弹黑窗）。

【为什么要有它】"没看到窗口"不能当证据（人不在屏幕前）。本工具在**没有控制台的父进程**里
跑真实代码路径，同时用采样线程枚举"可见控制台窗口"（ConsoleWindowClass / Cascadia），
把"有没有闪"变成可复核的数字。

三种模式（都在 console-less 父进程下跑才有意义）：

    # 对照臂：改造前的裸 subprocess.run（应 >0）
    python -c "import subprocess,sys; subprocess.Popen([sys.executable, 'scripts/verify_no_console_windows.py', '--control'], creationflags=0x00000008|0x00000200)"

    # 真实路径：ci_status.sync_once()（gh 两次调用；期望 0）
    ... '--sync'

    # 连续观察：每 200ms 采样 N 秒，落 logs/console_watch_YYYYMMDD.jsonl（0 sighting = 没闪过）
    ... '--watch', '3900'

判读：--control 必须 >0（否则说明采样器本身看不到窗口，结论无效），--sync/--watch 必须 =0。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from datetime import datetime

_USER32 = ctypes.windll.user32
_KERNEL32 = ctypes.windll.kernel32
_ENUM_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
_CONSOLE_CLASSES = ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS")
#: 采样间隔（秒）：50ms 足够抓住一次 0.2~3s 的闪现
_SAMPLE_INTERVAL_SEC = 0.05
#: --watch 的默认时长（秒）：比两个 30 分钟边界多 10 分钟
_DEFAULT_WATCH_SEC = 3900
#: 控制台窗口进程查询权限
_PROCESS_QUERY_LIMITED = 0x1000
_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _image_name(pid: int) -> str:
    try:
        handle = _KERNEL32.OpenProcess(_PROCESS_QUERY_LIMITED, False, int(pid))
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            if _KERNEL32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
        finally:
            _KERNEL32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 取证失败不影响采样
        return ""
    return ""


def visible_consoles() -> list:
    """当前所有**可见**控制台窗口（hwnd/pid/镜像名/窗口类）。"""
    found = []

    def _cb(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(256)
        _USER32.GetClassNameW(hwnd, buf, 256)
        if buf.value in _CONSOLE_CLASSES and _USER32.IsWindowVisible(hwnd):
            pid = ctypes.c_ulong(0)
            _USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append({"hwnd": int(hwnd), "pid": int(pid.value),
                          "image": _image_name(pid.value), "wndclass": buf.value})
        return True

    _USER32.EnumWindows(_ENUM_PROC(_cb), 0)
    return found


class _Sampler:
    """后台线程：每 _SAMPLE_INTERVAL_SEC 采样一次，记录新出现的 (hwnd,pid)。"""

    def __init__(self) -> None:
        import threading
        self._seen: set = set()
        self._sightings: list = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._interval = _SAMPLE_INTERVAL_SEC

    def __enter__(self) -> "_Sampler":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            for item in visible_consoles():
                key = (item["hwnd"], item["pid"])
                if key not in self._seen:
                    self._seen.add(key)
                    self._sightings.append(item)
            self._stop.wait(self._interval)

    @property
    def sightings(self) -> list:
        return list(self._sightings)


def _run_sync() -> dict:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from agent.monitoring import ci_status  # noqa: E402 延迟导入：--watch 模式不需要

    started = time.time()
    result = ci_status.sync_once()
    return {
        "sync_result": {k: v for k, v in (result or {}).items() if k != "runs"},
        "runs": int((result or {}).get("runs") or 0),
        "duration_ms": int((time.time() - started) * 1000),
    }


def _run_control() -> dict:
    import subprocess
    started = time.time()
    subprocess.run(["gh", "--version"], capture_output=True, text=True, encoding="utf-8")
    subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner"],
                   capture_output=True, text=True, encoding="utf-8")
    return {"duration_ms": int((time.time() - started) * 1000)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="验证子进程不弹黑窗（采样可见控制台窗口）")
    ap.add_argument("--control", action="store_true", help="对照臂：裸 subprocess.run（应 >0）")
    ap.add_argument("--sync", action="store_true", help="真实路径：ci_status.sync_once()（应 0）")
    ap.add_argument("--watch", type=float, default=0.0, metavar="SECONDS",
                    help="连续采样 N 秒并落 logs/console_watch_*.jsonl")
    args = ap.parse_args(argv)

    base = {"parent_console_hwnd": int(_KERNEL32.GetConsoleWindow() or 0),
            "parent_pid": os.getpid(), "ts": _now()}
    if args.control or args.sync:
        with _Sampler() as sampler:
            detail = _run_control() if args.control else _run_sync()
        payload = dict(base, mode="control" if args.control else "sync", **detail,
                       visible_consoles_seen=sampler.sightings,
                       visible_console_count=len(sampler.sightings))
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    if args.watch:
        os.makedirs(_LOG_DIR, exist_ok=True)
        path = os.path.join(_LOG_DIR, "console_watch_%s.jsonl" % datetime.now().strftime("%Y%m%d"))
        started = time.time()
        count = 0
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(base, event="start", seconds=args.watch),
                                 ensure_ascii=False) + "\n")
            fh.flush()
            with _Sampler() as sampler:
                while time.time() - started < args.watch:
                    time.sleep(1.0)
                sightings = sampler.sightings
            for item in sightings:
                fh.write(json.dumps(dict(item, ts=_now(), event="sighting"),
                                    ensure_ascii=False) + "\n")
            fh.write(json.dumps({"ts": _now(), "event": "stop", "sightings": len(sightings)},
                                ensure_ascii=False) + "\n")
            fh.flush()
        print(json.dumps({"log": path, "sightings": len(sightings)}, ensure_ascii=False))
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
