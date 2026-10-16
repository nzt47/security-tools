#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作台一键部署/重启：fetch -> ff-only 更新 -> 按需重建前端 -> 重启 -> 验证。

## 解决什么（2026-10-16 事故）

本仓的"部署"长期是三件手工事：在工作树里 git、重建前端、重启后端。三者谁忘了都
不报错：

  · 主仓工作树停在旧 commit（落后 14 个提交）而远端 PR 已合并 —— 线上跑旧代码；
  · 看护进程 scripts/watchdog_yunshu.py 只探 /api/health、不换代码 —— 自愈也自愈在旧代码；
  · 前端产物（templates/yunshu.html + static/assets）是 npm run build:flask 的独立步骤。

本脚本把这条链路收成一个命令，并在结束时读 /api/version 报告"现在跑的到底是哪版"。

## 用法

    python scripts/deploy_workbench.py                 # 默认 origin/master，自动判断前端是否需要重建
    python scripts/deploy_workbench.py --check         # 干跑：只报告将要做什么（不动代码/不重启）
    python scripts/deploy_workbench.py --no-build      # 跳过前端重建
    python scripts/deploy_workbench.py --no-restart    # 只更新代码/前端
    python scripts/deploy_workbench.py --ref <ref>     # 指定目标 ref
    python scripts/deploy_workbench.py --force-build   # 无条件重建前端

## 安全边界

- 代码更新只用 **ff-only**；若本地脏路径与待更新文件相交，直接拒绝并列出（不 hard reset、
  不 stash），把处置权交回人。
- 前端只在"index 缺失 或 本次更新动了 yunshu-ui/ 或 templates/yunshu.html"时重建（除非 --force-build）。
- 重启时若检测到看护进程在跑，则**只杀监听者、交由看护进程拉起**，避免"两个进程抢 5678"；
  看护进程未及时拉起才回退为直接启动。
- 运行期状态（.env、data/、logs/）不在本脚本的更新范围内（只做 fast-forward，不碰未跟踪文件）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REF = "origin/master"
DEFAULT_PORT = 5678
DEFAULT_HEALTH_URL = "http://127.0.0.1:5678/api/health"
DEFAULT_READY_TIMEOUT = 180.0
VERSION_PATH = "/api/version"
BACKEND_ARGV = ["app_server.py"]
WATCHDOG_LOCK = REPO_ROOT / "logs" / "watchdog_yunshu.lock"
FRONTEND_SRC_PREFIX = "yunshu-ui/"
INDEX_HTML = REPO_ROOT / "templates" / "yunshu.html"


def log(msg: str) -> None:
    print("[deploy] " + str(msg), flush=True)


# ── 外部命令（全部 best-effort，返回 (rc, stdout, stderr)）──────────────

def run(cmd: Sequence[str], cwd: Path = REPO_ROOT, timeout: float = 600.0) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(list(cmd), cwd=str(cwd), capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001 无该命令/超时都如实返回
        return 1, "", "%s: %s" % (type(e).__name__, e)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def git(*args: str, cwd: Path = REPO_ROOT, timeout: float = 120.0) -> Tuple[int, str, str]:
    return run(["git", *args], cwd=cwd, timeout=timeout)


def parse_porcelain(porcelain: str) -> List[str]:
    """git status --porcelain 输出 -> 仓库相对路径列表（含未跟踪；rename 取新名）。"""
    out: List[str] = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        rest = line[3:]
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        out.append(rest.strip().strip(chr(34)))
    return out


def overwrites(dirty: Sequence[str], incoming: Sequence[str]) -> List[str]:
    """本地脏路径与待更新文件的交集（ff-only 会因它们被拒）。"""
    return sorted(set(dirty) & set(incoming))


def frontend_needs_rebuild(incoming: Sequence[str], index_html: Path = INDEX_HTML) -> bool:
    """index 缺失，或本次更新动了前端源码/产物时，需要重建。"""
    if not Path(index_html).exists():
        return True
    for p in incoming:
        if p.startswith(FRONTEND_SRC_PREFIX) or p == "templates/yunshu.html":
            return True
    return False


def read_env_token(repo_root: Path = REPO_ROOT) -> Optional[str]:
    """从 .env 读 FLASK_API_TOKEN（**只用于请求头，不打印、不落日志**）。"""
    env = Path(repo_root) / ".env"
    if not env.exists():
        return None
    try:
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("FLASK_API_TOKEN="):
                return line.split("=", 1)[1].strip().strip(chr(34)).strip(chr(39))
    except OSError:
        return None
    return None


# ── 进程/端口（Windows 主机；非 Windows 返回空/False，不假装）────────────

def listening_pids(port: int = DEFAULT_PORT) -> List[int]:
    if os.name != "nt":
        return []
    rc, out, _ = run(["netstat", "-ano"], timeout=15.0)
    pids: List[int] = []
    for line in out.splitlines():
        if "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 2 or not parts[1].endswith(":" + str(int(port))):
            continue
        if parts[-1].isdigit() and int(parts[-1]) not in pids:
            pids.append(int(parts[-1]))
    return pids


def pid_alive(pid: int) -> bool:
    if os.name != "nt":
        return False
    rc, out, _ = run(["tasklist", "/FI", "PID eq %d" % int(pid), "/NH"], timeout=15.0)
    return str(int(pid)) in out


def kill_pid(pid: int) -> None:
    if os.name != "nt":
        return
    run(["taskkill", "/F", "/T", "/PID", str(int(pid))], timeout=15.0)


def watchdog_pid(lock: Path = WATCHDOG_LOCK) -> Optional[int]:
    """看护进程 PID（读锁文件 + 活体校验）；没有则 None。"""
    try:
        if not Path(lock).exists():
            return None
        txt = Path(lock).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if txt.isdigit() and pid_alive(int(txt)):
        return int(txt)
    return None


def spawn_backend(repo_root: Path, python_exe: str) -> int:
    """分离进程拉起后端（与看护进程同构：不继承控制台、日志落 logs/）。"""
    logs = Path(repo_root) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = logs / ("deploy_spawn_%s.out.log" % stamp)
    err_path = logs / ("deploy_spawn_%s.err.log" % stamp)
    creat = 0x00000008 | 0x00000200 if os.name == "nt" else 0
    with open(out_path, "ab") as fo, open(err_path, "ab") as fe:
        proc = subprocess.Popen([python_exe, *BACKEND_ARGV], cwd=str(repo_root),
                                stdout=fo, stderr=fe, stdin=subprocess.DEVNULL,
                                creationflags=creat, close_fds=True)
    return int(proc.pid)


def probe_health(url: str = DEFAULT_HEALTH_URL, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(getattr(resp, "status", 0) or 0) == 200
    except Exception:  # noqa: BLE001 连不上/超时一律视为未就绪
        return False


def wait_health(url: str = DEFAULT_HEALTH_URL, timeout: float = DEFAULT_READY_TIMEOUT,
                interval: float = 5.0) -> Tuple[bool, float]:
    start = time.time()
    while True:
        if probe_health(url):
            return True, round(time.time() - start, 1)
        if time.time() - start >= timeout:
            return False, round(time.time() - start, 1)
        time.sleep(interval)


def fetch_version(port: int = DEFAULT_PORT, token: Optional[str] = None,
                  timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    url = "http://127.0.0.1:%d%s" % (int(port), VERSION_PATH)
    req = urllib.request.Request(url)
    if token:
        req.add_header("X-API-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 端点未就绪/无令牌都返回 None
        return None


def restart_backend(repo_root: Path, python_exe: str, port: int,
                    ready_timeout: float) -> Tuple[str, float]:
    """重启后端。看护进程在跑则交由它拉起，避免双主竞争；否则直接启动。"""
    pids = listening_pids(port)
    wd = watchdog_pid()
    for pid in pids:
        log("停止旧监听者 pid=%s" % pid)
        kill_pid(pid)
    if wd is not None:
        log("检测到看护进程 pid=%s，交由它拉起后端" % wd)
        ok, waited = wait_health(timeout=ready_timeout)
        if ok:
            return "watchdog", waited
        log("看护进程未在 %ss 内拉起，回退为直接启动" % int(ready_timeout))
    pid = spawn_backend(repo_root, python_exe)
    log("已直接拉起后端 pid=%s，等待就绪..." % pid)
    ok, waited = wait_health(timeout=ready_timeout)
    return ("direct" if ok else "failed"), waited


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="工作台一键部署/重启（fetch->ff-only->前端按需重建->重启->验证）")
    ap.add_argument("--ref", default=DEFAULT_REF, help="目标 ref（默认 origin/master）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="后端端口（默认 5678）")
    ap.add_argument("--check", action="store_true", help="干跑：只报告将做什么，不改动/不重启")
    ap.add_argument("--no-fetch", action="store_true", help="跳过 git fetch")
    ap.add_argument("--no-build", action="store_true", help="跳过前端重建")
    ap.add_argument("--no-restart", action="store_true", help="不重启后端")
    ap.add_argument("--force-build", action="store_true", help="无条件重建前端")
    ap.add_argument("--ready-timeout", type=float, default=DEFAULT_READY_TIMEOUT,
                    help="重启后等 /api/health 就绪的上限秒（默认 180）")
    ap.add_argument("--python", default=sys.executable, help="拉起后端用的解释器（默认当前解释器）")
    ap.add_argument("--npm", default="npm", help="npm 可执行文件（默认 npm）")
    args = ap.parse_args(argv)

    repo = REPO_ROOT
    rc, head, err = git("rev-parse", "HEAD")
    if rc != 0:
        log("无法读取当前 HEAD（不在 git 仓库？）: " + err)
        return 2

    if not args.no_fetch and not args.check:
        git("fetch", "origin")

    rc, target, err = git("rev-parse", args.ref)
    if rc != 0:
        log("无法解析 ref %s: %s" % (args.ref, err))
        return 2

    log("当前 HEAD : %s" % head[:12])
    log("目标      : %s -> %s" % (args.ref, target[:12]))

    incoming: List[str] = []
    if head != target:
        rc, out, _ = git("diff", "--name-only", head, target)
        incoming = [x for x in out.splitlines() if x.strip()]
        rc2, dirty_out, _ = git("status", "--porcelain")
        dirty = parse_porcelain(dirty_out)
        conflicts = overwrites(dirty, incoming)
        log("待更新 %d 个文件；本地脏路径 %d 个；相交 %d 个"
            % (len(incoming), len(dirty), len(conflicts)))
        if conflicts:
            log("拒绝 ff-only：以下本地改动会被覆盖，请先处置 -> " + ", ".join(conflicts[:20]))
            return 3
    else:
        log("代码已是最新，无需更新")

    need_build = args.force_build or frontend_needs_rebuild(incoming)
    log("前端需要重建: %s" % need_build)

    if args.check:
        log("--check 干跑结束：未改代码、未重建、未重启")
        return 0

    if head != target:
        rc, out, err = git("merge", "--ff-only", args.ref)
        if rc != 0:
            log("ff-only 更新失败: " + err)
            return 3
        log("代码已更新到 %s" % target[:12])

    if need_build and not args.no_build:
        ui = repo / "yunshu-ui"
        if not (ui / "node_modules").exists():
            log("yunshu-ui/node_modules 不存在，先 npm ci ...")
            rc, out, err = run([args.npm, "ci", "--prefer-offline"], cwd=ui, timeout=1800.0)
            if rc != 0:
                log("npm ci 失败: " + err[-500:])
                return 4
        log("重建前端：npm run build:flask ...")
        rc, out, err = run([args.npm, "run", "build:flask"], cwd=ui, timeout=1800.0)
        if rc != 0:
            log("前端重建失败: " + err[-500:])
            return 4
        log("前端重建完成")

    if not args.no_restart:
        how, waited = restart_backend(repo, args.python, args.port, args.ready_timeout)
        if how == "failed":
            log("重启后 %ss 内 /api/health 未就绪" % int(args.ready_timeout))
            return 5
        log("后端已就绪（%s，约 %ss）" % (how, waited))

    token = read_env_token(repo)
    payload = fetch_version(args.port, token)
    if payload:
        data = payload.get("data", payload)
        backend = (data or {}).get("backend", {}) or {}
        frontend = (data or {}).get("frontend", {}) or {}
        log("运行中后端 commit: %s  %s" % (str(backend.get("commit"))[:12], backend.get("commit_subject")))
        log("线上前端 bundle : %s  built_at=%s" % (frontend.get("bundle"), frontend.get("built_at")))
        if backend.get("commit") and backend.get("commit") != target:
            log("警告：运行进程的 commit 与目标不一致（%s != %s）"
                % (str(backend.get("commit"))[:12], target[:12]))
    else:
        log("未能读取 /api/version（端点未就绪，或需要 .env 里的 FLASK_API_TOKEN）")
    log("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
