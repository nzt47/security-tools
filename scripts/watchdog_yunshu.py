#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""云枢后端常驻看门狗 —— 探 /api/health，连续失败即重启并留痕（A1-③）。

【为什么需要它（实测背景）】
    logs/backend_20260922_224531.err.log：服务 2026-09-22 22:46:29 起、约 23:27 停，
    日志**无 traceback、无优雅关闭记录**，之后**再没有人发现**，直到 2026-09-25 审计
    才查出"5678 已无监听"。也就是说：进程没了 ≠ 有人知道。本脚本就是那个"会发现的人"。

【为什么用 Python 而不是 PowerShell（已选并说明理由）】
    ① 执行策略：.ps1 受 ExecutionPolicy 约束，默认 Restricted 机器上直接拒绝运行
       （需要 -ExecutionPolicy Bypass 才跑得起来），而 .py 没有这道门；
    ② 解释器保证：本仓库唯一被实测确认存在的解释器就是 python 3.12.0（venv/ 是空壳，
       全目录 0 个 python.exe），PowerShell 版本未做任何约束；
    ③ 与服务的同构性：服务本身就是 python 进程，用的是同一个 sys.executable，
       不存在"看门狗能跑但服务跑不起来"的解释器分歧。

【与 agent/server_port_guard.py 的关系：有意重复，不做复用】
    本脚本只依赖 **Python 标准库**，不 import 仓库内任何模块。理由：看门狗必须在
    被看护对象"坏掉"时仍然可用；一旦 import agent.*，任何一处导入期错误（依赖缺失、
    语法错误、.env 解析失败）都会让看门狗与被看护对象一起躺下 —— 那就失去了兜底
    的意义。代价是 netstat 解析被重复实现了约 15 行（见 _listening_pids），有意为之。

【留痕】
    结构化 JSON Lines 追加写到 logs/watchdog_yunshu.jsonl，每条至少带
    ts / event / reason / old_pid / new_pid，可直接 grep / jq 检索。

用法:
    python scripts/watchdog_yunshu.py                     # 常驻，默认 30s 一轮
    python scripts/watchdog_yunshu.py --once              # 只做一轮检查（不健康即处理）
    python scripts/watchdog_yunshu.py --once --dry-run    # 干跑：只留痕，不真的拉起/杀进程
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

#: 仓库根 = 本脚本所在目录的上一级（可移植，不写死机器路径）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_HEALTH_URL = "http://127.0.0.1:5678/api/health"
DEFAULT_PORT = 5678
DEFAULT_INTERVAL = 30.0           # 探活间隔（秒）—— 需求指定 30s
DEFAULT_FAIL_THRESHOLD = 3        # 连续失败几次才判定"服务没了"（抗瞬时抖动）
DEFAULT_RESTART_COOLDOWN = 120.0  # 两次重启之间的最小间隔（抗重启风暴）
DEFAULT_READY_TIMEOUT = 180.0     # 拉起后等就绪的上限（冷启动实测 55-85s）
DEFAULT_HTTP_TIMEOUT = 3.0
DEFAULT_LOG_PATH = os.path.join(REPO_ROOT, "logs", "watchdog_yunshu.jsonl")
DEFAULT_LOCK_PATH = os.path.join(REPO_ROOT, "logs", "watchdog_yunshu.lock")

#: 后端启动命令（与 start_yunshu.bat 的 `python app_server.py` 一致）
BACKEND_ARGV = ["app_server.py"]


def now_iso() -> str:
    """本地时区 ISO 时间戳（秒精度）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_record(path: str, record: Dict[str, Any]) -> None:
    """追加一条 JSON Lines 留痕。**留痕失败不得让看门狗本身崩掉**。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        print("[watchdog] 留痕写入失败（忽略）: %s" % e, file=sys.stderr)


def probe_health(url: str = DEFAULT_HEALTH_URL,
                 timeout: float = DEFAULT_HTTP_TIMEOUT) -> Tuple[bool, str, Optional[int]]:
    """探一次 /api/health。

    Returns:
        (healthy, detail, status_code)；healthy 仅在 HTTP 200 时为真。
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            code = int(getattr(resp, "status", 0) or 0)
            resp.read(256)  # 读一点点，确保连接被正常消费
        if code == 200:
            return True, "http_200", code
        return False, "http_%s" % code, code
    except urllib.error.HTTPError as e:
        return False, "http_error_%s" % e.code, int(getattr(e, "code", 0) or 0)
    except Exception as e:  # noqa: BLE001 连不上 / 超时 / DNS 一律视为不健康
        return False, "%s: %s" % (type(e).__name__, e), None


def _listening_pids(port: int = DEFAULT_PORT) -> List[int]:
    """netstat -ano 里"本地地址端口精确等于 port"的 LISTENING PID（去重）。

    只认端口精确匹配：子串匹配会把 56789 之类的无关进程也算进来（误杀）。
    """
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             timeout=10).stdout or ""
    except Exception:  # noqa: BLE001
        return []
    pids: List[int] = []
    for line in out.splitlines():
        if "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        local = parts[1]
        if not local.endswith(":" + str(int(port))):
            continue
        pid = parts[-1]
        if pid.isdigit() and int(pid) not in pids:
            pids.append(int(pid))
    return pids


def _pid_alive(pid: int) -> bool:
    """PID 是否还活着（只用 tasklist，不引第三方库）。"""
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % int(pid), "/NH"],
                             capture_output=True, text=True, timeout=10).stdout or ""
        return str(int(pid)) in out
    except Exception:  # noqa: BLE001
        return False


def kill_stale(pid: int, *, dry_run: bool = False) -> bool:
    """强杀一个残留/卡死的后端实例**及其子进程树**。

    这里用 `/T`（清理子进程树）是**有意**的：本文件是新增文件，不受
    tests/unit/test_server_port_guard.py 对 argv 的逐字断言约束；而看门狗的职责
    就是"保证干净重来" —— 对**没有**加入 Job Object 的历史实例，`/T` 是唯一
    能顺带收走其 embedding/reranker 子进程的手段。
    """
    if dry_run:
        return False
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))],
                       capture_output=True, timeout=10)
        return True
    except Exception as e:  # noqa: BLE001
        print("[watchdog] taskkill 失败（忽略）: %s" % e, file=sys.stderr)
        return False


def spawn_backend(*, dry_run: bool = False) -> Optional[int]:
    """拉起后端（分离进程，日志落 logs/watchdog_spawn_*.out|err.log），返回新 PID。

    dry_run=True 时不真的拉起，返回 None —— 干跑的唯一作用是产出留痕。
    """
    if dry_run:
        return None
    os.makedirs(os.path.join(REPO_ROOT, "logs"), exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(REPO_ROOT, "logs", "watchdog_spawn_%s.out.log" % stamp)
    err_path = os.path.join(REPO_ROOT, "logs", "watchdog_spawn_%s.err.log" % stamp)
    argv = [sys.executable] + BACKEND_ARGV
    # DETACHED_PROCESS: 不继承本控制台 ⇒ 关掉看门狗窗口不会连带杀掉后端
    creat = 0
    if sys.platform == "win32":
        creat = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    with open(out_path, "ab") as fo, open(err_path, "ab") as fe:
        proc = subprocess.Popen(argv, cwd=REPO_ROOT, stdout=fo, stderr=fe,
                                stdin=subprocess.DEVNULL, creationflags=creat,
                                close_fds=True)
    return int(proc.pid)


def wait_until_ready(*, timeout: float = DEFAULT_READY_TIMEOUT,
                     url: str = DEFAULT_HEALTH_URL,
                     interval: float = 5.0) -> Tuple[bool, float]:
    """轮询等就绪，返回 (healthy, 已等待秒数)。冷启动实测 55-85s。"""
    start = time.time()
    while True:
        ok, _detail, _code = probe_health(url)
        if ok:
            return True, round(time.time() - start, 1)
        if time.time() - start >= timeout:
            return False, round(time.time() - start, 1)
        time.sleep(interval)


class SingleInstanceLock:
    """单实例锁：防止两个看门狗互相打架（会变成重启风暴的放大器）。

    实现是"锁文件 + 活体校验"：文件里写看门狗自己的 PID，启动时若发现该 PID 还活着
    就直接退出。没有用文件锁的原语，是因为 Windows 上的 `msvcrt.locking` 在进程被
    强杀时不会自动释放，反而会留下永久死锁。
    """

    def __init__(self, path: str = DEFAULT_LOCK_PATH) -> None:
        self.path = path
        self.acquired = False

    def __enter__(self) -> "SingleInstanceLock":
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    old = (f.read() or "").strip()
                if old.isdigit() and int(old) != os.getpid() and _pid_alive(int(old)):
                    print("[watchdog] 已有看门狗在运行（pid=%s），本次退出" % old)
                    self.acquired = False
                    return self
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            self.acquired = True
        except Exception as e:  # noqa: BLE001 拿不到锁也要能跑（只是失去防重）
            print("[watchdog] 单实例锁不可用（忽略）: %s" % e, file=sys.stderr)
            self.acquired = True
        return self

    def __exit__(self, *exc: Any) -> None:
        if not self.acquired:
            return
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    if (f.read() or "").strip() == str(os.getpid()):
                        os.remove(self.path)
        except Exception:  # noqa: BLE001
            pass


def check_once(
    *,
    log_path: str = DEFAULT_LOG_PATH,
    port: int = DEFAULT_PORT,
    url: str = DEFAULT_HEALTH_URL,
    consecutive_failures: int = 1,
    fail_threshold: int = DEFAULT_FAIL_THRESHOLD,
    force_act: bool = False,
    dry_run: bool = False,
    ready_timeout: float = DEFAULT_READY_TIMEOUT,
) -> Dict[str, Any]:
    """做一轮探活，必要时重启，并把全过程写成 JSON Lines。返回本轮记录。

    Args:
        consecutive_failures: 含本轮在内的连续失败次数（由调用方累计）
        force_act: True 时（--once）忽略 fail_threshold，不健康即处理
    """
    healthy, detail, code = probe_health(url)
    old_pids = _listening_pids(port)
    old_pid = old_pids[0] if old_pids else None

    record: Dict[str, Any] = {
        "ts": now_iso(),
        "event": "health_check",
        "healthy": healthy,
        "detail": detail,
        "status_code": code,
        "consecutive_failures": int(consecutive_failures),
        "fail_threshold": int(fail_threshold),
        "listening_pids": old_pids,
        "old_pid": old_pid,
        "new_pid": None,
        "reason": "healthy" if healthy else "health_unreachable",
        "action": "none",
        "dry_run": bool(dry_run),
        "watchdog_pid": os.getpid(),
    }

    if healthy:
        write_record(log_path, record)
        return record

    if not force_act and consecutive_failures < fail_threshold:
        record["action"] = "wait_threshold"
        record["reason"] = "health_unreachable_below_threshold"
        write_record(log_path, record)
        return record

    # ── 判定"服务没了"：先清掉残留监听（可能有卡死的僵实例占着端口），再拉起 ──
    record["reason"] = "health_unreachable_consecutive_%d" % int(consecutive_failures)
    record["action"] = "restart"
    if old_pid is not None:
        kill_stale(int(old_pid), dry_run=dry_run)
        record["stale_listener_killed"] = not dry_run
    new_pid = spawn_backend(dry_run=dry_run)
    record["new_pid"] = new_pid
    write_record(log_path, record)

    if dry_run:
        return record

    ready, waited = wait_until_ready(timeout=ready_timeout, url=url)
    write_record(log_path, {
        "ts": now_iso(),
        "event": "restart_result",
        "reason": "cold_start_probe",
        "old_pid": old_pid,
        "new_pid": new_pid,
        "ready": bool(ready),
        "waited_s": waited,
        "dry_run": False,
        "watchdog_pid": os.getpid(),
    })
    return record


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="云枢后端看门狗（探活 + 重启 + JSONL 留痕）")
    ap.add_argument("--url", default=DEFAULT_HEALTH_URL, help="健康检查 URL")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="后端端口（默认 5678）")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                    help="探活间隔秒（默认 30）")
    ap.add_argument("--fail-threshold", type=int, default=DEFAULT_FAIL_THRESHOLD,
                    help="连续失败多少次才重启（默认 3）")
    ap.add_argument("--cooldown", type=float, default=DEFAULT_RESTART_COOLDOWN,
                    help="两次重启之间的最小间隔秒（默认 120）")
    ap.add_argument("--ready-timeout", type=float, default=DEFAULT_READY_TIMEOUT,
                    help="拉起后等就绪的上限秒（默认 180）")
    ap.add_argument("--log", default=DEFAULT_LOG_PATH, help="JSONL 留痕文件")
    ap.add_argument("--lock", default=DEFAULT_LOCK_PATH, help="单实例锁文件")
    ap.add_argument("--once", action="store_true", help="只做一轮检查后退出")
    ap.add_argument("--dry-run", action="store_true",
                    help="干跑：只探活与留痕，不真的杀进程/拉起进程")
    args = ap.parse_args(argv)

    with SingleInstanceLock(args.lock) as lock:
        if not lock.acquired:
            return 0

        write_record(args.log, {
            "ts": now_iso(), "event": "watchdog_start", "reason": "startup",
            "old_pid": None, "new_pid": None, "dry_run": bool(args.dry_run),
            "interval_s": args.interval, "fail_threshold": args.fail_threshold,
            "url": args.url, "log": args.log, "watchdog_pid": os.getpid(),
        })

        if args.once:
            rec = check_once(log_path=args.log, port=args.port, url=args.url,
                             consecutive_failures=1,
                             fail_threshold=args.fail_threshold,
                             force_act=True, dry_run=args.dry_run,
                             ready_timeout=args.ready_timeout)
            print(json.dumps(rec, ensure_ascii=False))
            write_record(args.log, {"ts": now_iso(), "event": "watchdog_stop",
                                    "reason": "once_mode", "old_pid": rec.get("old_pid"),
                                    "new_pid": rec.get("new_pid"),
                                    "dry_run": bool(args.dry_run),
                                    "watchdog_pid": os.getpid()})
            return 0

        failures = 0
        last_restart = 0.0
        try:
            while True:
                healthy, detail, _code = probe_health(args.url)
                if healthy:
                    failures = 0
                    check_once(log_path=args.log, port=args.port, url=args.url,
                               consecutive_failures=0, fail_threshold=args.fail_threshold,
                               dry_run=False)
                else:
                    failures += 1
                    due = (time.time() - last_restart) >= args.cooldown
                    if failures >= args.fail_threshold and due:
                        check_once(log_path=args.log, port=args.port, url=args.url,
                                   consecutive_failures=failures,
                                   fail_threshold=args.fail_threshold,
                                   force_act=True, dry_run=args.dry_run,
                                   ready_timeout=args.ready_timeout)
                        last_restart = time.time()
                        failures = 0
                    elif failures >= args.fail_threshold and not due:
                        write_record(args.log, {
                            "ts": now_iso(), "event": "health_check",
                            "healthy": False, "detail": detail, "reason": "cooldown_active",
                            "old_pid": (_listening_pids(args.port) or [None])[0],
                            "new_pid": None, "action": "skip_restart_cooldown",
                            "consecutive_failures": failures,
                            "dry_run": bool(args.dry_run), "watchdog_pid": os.getpid(),
                        })
                    else:
                        write_record(args.log, {
                            "ts": now_iso(), "event": "health_check",
                            "healthy": False, "detail": detail,
                            "reason": "health_unreachable_below_threshold",
                            "old_pid": (_listening_pids(args.port) or [None])[0],
                            "new_pid": None, "action": "wait_threshold",
                            "consecutive_failures": failures,
                            "dry_run": bool(args.dry_run), "watchdog_pid": os.getpid(),
                        })
                time.sleep(args.interval)
        except KeyboardInterrupt:
            write_record(args.log, {"ts": now_iso(), "event": "watchdog_stop",
                                    "reason": "keyboard_interrupt", "old_pid": None,
                                    "new_pid": None, "dry_run": bool(args.dry_run),
                                    "watchdog_pid": os.getpid()})
            return 0


if __name__ == "__main__":
    sys.exit(main())
