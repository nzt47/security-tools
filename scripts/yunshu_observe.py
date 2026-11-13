#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""云枢 · 后台观察窗（2026-11-13，Owner：「启动器点击时一直静默也不行，需要一个反映后台的观察窗」）。

【为什么是"可见的观察窗"而不是又弹一个黑窗】
    一键启动把后端放在**隐藏**控制台里跑（见 start_yunshu.bat / scripts/yunshu_launch.vbs），
    后台任务也就不会再闪窗；但"完全静默"让人无法判断到底启没起来。本窗口是**唯一**一个
    故意可见的控制台：它只读，不持有后端，关掉它后端继续跑。

【口径】
    1. 只依赖标准库（后端起不来时它必须还能告诉人"起不来"，不 import agent.*）；
    2. 令牌只从 .env 读、只用于请求头，**任何分支都不打印**（原文/指纹/长度/布尔都不行）；
    3. 读数全部来自既有只读端点，不新增路由：/api/health、/api/version、/api/system/units、
       /api/alarms/coverage、/api/scheduler/overview；
    4. 取不到的字段显示「—」，不折算成 0 或「正常」（L5 口径）。

用法：
    python scripts/yunshu_observe.py            # 2s 刷新，q 退出、r 立即刷新
    python scripts/yunshu_observe.py --once     # 只打印一屏（脚本/排查用）
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:5678"
TOKEN_ENV = "FLASK_API_TOKEN"

#: 轮询间隔（秒）—— 2s 足够看清冷启动的推进，又不至于刷屏
REFRESH_SEC = 2.0
#: 版本/单位/覆盖这类变化慢的读数，每 N 次刷新拉一次（health 每次都拉）
SLOW_EVERY = 3
#: 请求超时（秒）
HTTP_TIMEOUT_SEC = 3.0
#: 日志里"最近的错误"最多显示几行
LOG_TAIL_LINES = 4

WINDOW_TITLE = "云枢 · 后台观察窗（关掉本窗口不影响后端）"

_DIM = "\033[2m"
_RESET = "\033[0m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"


def _token() -> str:
    """令牌只从 .env 读（与服务端同源），读不到就空着 —— 不打印、不落任何文件。"""
    raw = (os.environ.get(TOKEN_ENV) or "").strip()
    if raw:
        return raw
    try:
        for line in (REPO_ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(TOKEN_ENV + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


class _Client:
    def __init__(self) -> None:
        self.token = _token()

    def get(self, path: str) -> tuple:
        """返回 (data|None, error|None)；错误只回类名/状态码，不回异常原文。"""
        req = urllib.request.Request(BASE_URL + path)
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
                body = resp.read()
            ms = int((time.time() - started) * 1000)
            return (json.loads(body.decode("utf-8")), ms, None) if body else (None, ms, "空响应")
        except urllib.error.HTTPError as exc:
            return None, int((time.time() - started) * 1000), "HTTP %s" % exc.code
        except Exception as exc:  # noqa: BLE001 连不上/超时一律如实显示
            return None, int((time.time() - started) * 1000), type(exc).__name__


def _num(value, unit: str = "") -> str:
    """None → 「—」（无数据不折算成 0/正常，L5）。"""
    if value is None:
        return "—"
    if isinstance(value, float):
        return ("%.1f" % value) + unit
    return str(value) + unit


def _newest_log_error() -> str:
    """最新一份后端错误日志里的最后几条 ERROR（没有就返回空串）。"""
    logs = REPO_ROOT / "logs"
    try:
        candidates = sorted(
            [p for p in logs.glob("*.err.log") if p.is_file()],
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
    except OSError:
        return ""
    for path in candidates[:1]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        errors = [ln.strip() for ln in lines if "ERROR" in ln or "Traceback" in ln]
        if errors:
            return "\n".join(_DIM + ln[:150] + _RESET for ln in errors[-LOG_TAIL_LINES:])
    return ""


def _dashboard(client: _Client, slow: dict) -> str:
    out = []
    out.append(_CYAN + "=" * 78 + _RESET)
    out.append(_CYAN + "  云枢 · 后台观察窗        " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + _RESET)
    out.append(_CYAN + "=" * 78 + _RESET)

    health, ms, err = client.get("/api/health")
    if health is None:
        out.append(_RED + "  后端   : 没起来（%s，%dms）" % (err or "无响应", ms) + _RESET)
        out.append(_DIM + "           冷启动通常 50-90s；日志：logs\\backend_boot_*.err.log / logs\\deploy_spawn_*.err.log" + _RESET)
        out.append("")
        out.append(_DIM + "  关闭本窗口不会影响后端；后端起来后本窗口会自动变成就绪状态。" + _RESET)
        return "\n".join(out)

    out.append(_GREEN + "  后端   : 就绪 (HTTP 200, %dms)  %s" % (ms, BASE_URL) + _RESET)

    ver = slow.get("version")
    if ver:
        be = ver.get("backend") or {}
        fe = ver.get("frontend") or {}
        proc = ver.get("process") or {}
        dirty = be.get("dirty_files") or []
        out.append("  版本   : %s  %s" % (str(be.get("commit") or "—")[:9],
                                          _DIM + str(be.get("committed_at") or "") + _RESET))
        out.append(_DIM + "           前端 %s · bundle %s" % (str(fe.get("build_commit") or "—")[:9],
                                                             fe.get("bundle") or "—") + _RESET)
        out.append("  进程   : pid %s  启动于 %s" % (_num(proc.get("pid")), proc.get("started_at") or "—"))
        out.append("  工作区 : %s" % ("有未提交改动 %d 个（正常，含运行期状态）" % len(dirty) if dirty else "干净"))
    else:
        out.append(_DIM + "  版本   : —（/api/version 拿不到）" + _RESET)

    units = slow.get("units")
    if units:
        out.append("  单位   : %s ok / %s warn / %s never · 健康分 %s" % (
            _num(units.get("ok")), _num(units.get("warn")), _num(units.get("never")),
            _num(units.get("health_score"))))
    else:
        out.append(_DIM + "  单位   : —" + _RESET)

    cov = slow.get("coverage")
    if cov:
        gaps = cov.get("gaps") or {}
        out.append("  覆盖   : 未覆盖 %s 条%s" % (_num(gaps.get("count")),
                                             "  " + _YELLOW + "(有缺口，见告警页)" + _RESET if (gaps.get("count") or 0) else ""))
        ci = cov.get("ci") or {}
        out.append("  CI 同步: %s（%s 分钟前）" % (ci.get("last_sync_at") or "—",
                                              _num(round((ci.get("age_seconds") or 0) / 60.0)) if ci.get("age_seconds") is not None else "—"))
    else:
        out.append(_DIM + "  覆盖   : —" + _RESET)

    sched = slow.get("scheduler")
    if sched:
        tasks = sched.get("tasks") or []
        enabled = [t for t in tasks if t.get("enabled")]
        out.append("  调度   : %d 个 / 启用 %d" % (len(tasks), len(enabled)))
    else:
        out.append(_DIM + "  调度   : —" + _RESET)

    errs = _newest_log_error()
    if errs:
        out.append("")
        out.append(_YELLOW + "  最近的后端错误（只显示最后几行）:" + _RESET)
        out.append(errs)

    out.append("")
    out.append(_DIM + "  q 退出观察窗（后端继续运行） · r 立即刷新 · 关闭本窗口不会停后端" + _RESET)
    out.append(_DIM + "  要停后端：taskkill /PID <上面的 pid> /F（或重新跑一键启动）" + _RESET)
    return "\n".join(out)


def _read_key() -> str:
    """非阻塞读一个键（Windows 用 msvcrt，其它平台返回空串）。"""
    if os.name != "nt":
        return ""
    import msvcrt
    if not msvcrt.kbhit():
        return ""
    try:
        return msvcrt.getwch().lower()
    except Exception:  # noqa: BLE001
        return ""


def _set_title() -> None:
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(WINDOW_TITLE)
    except Exception:  # noqa: BLE001 非 Windows / 无控制台时忽略
        pass


def main(argv=None) -> int:
    global BASE_URL
    ap = argparse.ArgumentParser(description="云枢后台观察窗（只读）")
    ap.add_argument("--once", action="store_true", help="只打印一屏后退出")
    ap.add_argument("--url", default=BASE_URL, help="后端基址")
    args = ap.parse_args(argv)
    BASE_URL = args.url.rstrip("/")

    client = _Client()
    if args.once:
        print(_dashboard(client, {"version": (client.get("/api/version") or (None,))[0]}))
        return 0

    _set_title()
    os.system("")  # 让 Windows 控制台启用 ANSI 转义
    slow: dict = {}
    tick = 0
    try:
        while True:
            if tick % SLOW_EVERY == 0:
                for key, path in (("version", "/api/version"),
                                  ("units", "/api/system/units"),
                                  ("coverage", "/api/alarms/coverage"),
                                  ("scheduler", "/api/scheduler/overview")):
                    data, _ms, _err = client.get(path)
                    if data is not None:
                        slow[key] = data.get("data", data) if isinstance(data, dict) else data
            if os.name == "nt":
                os.system("cls")
            else:
                sys.stdout.write("\033[H\033[2J")
            print(_dashboard(client, slow))
            sys.stdout.flush()
            tick += 1
            waited = 0.0
            while waited < REFRESH_SEC:
                key = _read_key()
                if key == "q":
                    return 0
                if key == "r":
                    break
                time.sleep(0.1)
                waited += 0.1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
