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
    python scripts/deploy_workbench.py --smoke         # 部署后对关键端点冒烟（/api/health + /api/version + /chat）
    python scripts/deploy_workbench.py --rollback      # 回滚到"上次部署前"的 commit（读 logs/deploy_last.json）
    python scripts/deploy_workbench.py --rollback --check
    python scripts/deploy_workbench.py --rollback --hard   # 回滚用 git reset --hard（默认 --keep 更安全）

## 安全边界

- 代码更新只用 **ff-only**；若本地脏路径与待更新文件相交，直接拒绝并列出（不 hard reset、
  不 stash），把处置权交回人。
- **回滚记录**：真正更新代码前，把"从哪个 commit 到哪个 commit"写 logs/deploy_last.json
  （原子写），所以即使更新/构建中途崩溃也能 --rollback。回滚默认用 **git reset --keep**：
  只在"两个 commit 间有差异、且本地又改过"的文件上中止，不像 --hard 那样丢弃运行期脏文件。
- **构建戳**：前端重建成功后把 {commit, built_at} 写 static/assets/.build-stamp.json；
  判断"产物是否陈旧"优先看它，mtime 比较退为兜底 —— 比 mtime 更准（不受 touch/时钟偏移影响）。
- 前端只在"index 缺失 / 本次更新动了 yunshu-ui/ 或 templates/yunshu.html / 构建戳与目标
  commit 不一致 / 源码 mtime 比产物新"时重建（除非 --force-build）。
- 重启时若检测到看护进程在跑，则**只杀监听者、交由看护进程拉起**，避免"两个进程抢 5678"；
  看护进程未及时拉起才回退为直接启动。
- 运行期状态（.env、data/、logs/）不在本脚本的更新范围内（只做 fast-forward，不碰未跟踪文件）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REF = "origin/master"
DEFAULT_PORT = 5678
DEFAULT_HEALTH_URL = "http://127.0.0.1:5678/api/health"
DEFAULT_READY_TIMEOUT = 180.0
#: 看护回退前复查到端口已被占用时，再等它真正就绪的秒数（不叠加整轮 180s）
FALLBACK_RECHECK_WAIT = 60.0
VERSION_PATH = "/api/version"
BACKEND_ARGV = ["app_server.py"]
WATCHDOG_LOCK = REPO_ROOT / "logs" / "watchdog_yunshu.lock"
FRONTEND_SRC_PREFIX = "yunshu-ui/"
INDEX_HTML = REPO_ROOT / "templates" / "yunshu.html"
#: 上次部署前/后的 commit（回滚依据；logs/ 是运行期目录，不入库）
DEPLOY_RECORD = REPO_ROOT / "logs" / "deploy_last.json"
#: 前端构建戳（static/assets/ 不入库，同目录放着被它描述的主 bundle）
BUILD_STAMP = REPO_ROOT / "static" / "assets" / ".build-stamp.json"
#: --smoke 默认探测的端点（/api/health 免令牌；其余带 X-API-Token）
DEFAULT_SMOKE_PATHS: Tuple[str, ...] = ("/api/health", "/api/version", "/chat")


def log(msg: str) -> None:
    print("[deploy] " + str(msg), flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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


def commit_subject(sha: str, cwd: Path = REPO_ROOT) -> Optional[str]:
    """commit 标题（best-effort；解析不了返回 None）。"""
    if not sha:
        return None
    rc, out, _ = git("log", "-1", "--format=%s", sha, cwd=cwd)
    return out or None


def commit_exists(sha: str, cwd: Path = REPO_ROOT) -> bool:
    """本地仓库能否解析出该 commit（回滚目标必须可达）。"""
    if not sha:
        return False
    rc, out, _ = git("rev-parse", "--verify", "--quiet", sha + "^{commit}", cwd=cwd)
    return rc == 0 and bool(out)


# ── JSON 原子读写（部署记录 / 构建戳）──────────────────────────────

def write_json_atomic(path, payload: Dict[str, Any]) -> bool:
    """先写 .tmp 再 os.replace，避免半截 JSON 被当成有效记录。"""
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        os.replace(str(tmp), str(target))
        return True
    except OSError as e:
        log("无法写入 %s: %s" % (target, e))
        return False


def read_json(path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


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


FRONTEND_SRC_DIR = REPO_ROOT / "yunshu-ui" / "src"
_FRONTEND_SUFFIXES = (".ts", ".tsx", ".css")


def npm_command(npm: str, npm_args: Sequence[str]) -> List[str]:
    """把 npm 调用包成 Windows 也能直接跑的形式。

    【为什么必须包】Windows 上 npm 实际是 npm.cmd；subprocess 以列表方式直调 "npm"
    会 FileNotFoundError（2026-10-16 真机实测：deploy 走到重建前端就失败）。
    走 cmd /c 才能执行 .cmd。
    """
    exe = shutil.which(npm) or npm
    if os.name == "nt":
        # Windows 上 shutil.which("npm") 可能先命中**无扩展名**的 sh shim
        # （本机实测返回 C:\Program Files\nodejs\npm），直接 Popen 会
        # OSError WinError 193「不是有效的 Win32 应用程序」。必须显式优先 .cmd/.exe/.bat。
        if not exe.lower().endswith((".exe", ".cmd", ".bat")):
            for ext in (".cmd", ".exe", ".bat"):
                cand = shutil.which(exe + ext)
                if cand:
                    exe = cand
                    break
        if exe.lower().endswith((".cmd", ".bat")):
            return ["cmd", "/c", exe, *npm_args]
    return [exe, *npm_args]


def _newest_mtime(root: Path) -> Optional[float]:
    """目录下前端源码（.ts/.tsx/.css）的最新 mtime；读不到返回 None。"""
    newest: Optional[float] = None
    try:
        for p in Path(root).rglob("*"):
            if p.suffix.lower() not in _FRONTEND_SUFFIXES:
                continue
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if newest is None or m > newest:
                newest = m
    except OSError:
        return None
    return newest


def write_build_stamp(path, commit: str, built_at: Optional[str] = None) -> bool:
    """把"这次前端产物是从哪个 commit 构建的"写进产物目录。"""
    return write_json_atomic(path, {
        "commit": commit,
        "built_at": built_at or now_iso(),
        "source": "deploy_workbench",
    })


def stamp_matches(stamp: Optional[Dict[str, Any]], commit: Optional[str]) -> bool:
    """构建戳的 commit 是否就是目标 commit（允许短/长 sha 前缀互认，最短 7 位）。"""
    if not isinstance(stamp, dict) or not commit:
        return False
    recorded = stamp.get("commit")
    if not isinstance(recorded, str) or not recorded:
        return False
    a, b = recorded.strip(), str(commit).strip()
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 7 and longer.startswith(shorter)


def frontend_needs_rebuild(incoming: Sequence[str], index_html: Path = INDEX_HTML,
                           src_dir: Path = FRONTEND_SRC_DIR, *,
                           stamp_path: Path = BUILD_STAMP,
                           expected_commit: Optional[str] = None) -> bool:
    """四种情形需要重建：index 缺失；本次更新动了前端源码/产物；构建戳与目标 commit
    不一致；产物比前端源码旧。

    第三种的来由是构建戳（比 mtime 准）；第四种是**上次构建失败/被中断后的残留**
    （2026-10-16 真机实测：ff 成功、构建失败 ⇒ 之后没有 yunshu-ui 变更就永远不会再重建，
    线上一直停在旧 bundle）。mtime 比较保留为兜底，覆盖"改了源码但没走本脚本构建"。
    """
    html = Path(index_html)
    if not html.exists():
        return True
    for p in incoming:
        if p.startswith(FRONTEND_SRC_PREFIX) or p == "templates/yunshu.html":
            return True
    if expected_commit:
        if not stamp_matches(read_json(stamp_path), expected_commit):
            return True
    try:
        html_mtime = html.stat().st_mtime
    except OSError:
        return True
    newest = _newest_mtime(src_dir)
    return newest is not None and newest > html_mtime


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


def http_status(url: str, token: Optional[str] = None, timeout: float = 5.0) -> Optional[int]:
    """GET 一个 URL 的状态码（HTTP 错误也返回其码；连不上返回 None）。"""
    req = urllib.request.Request(url)
    if token:
        req.add_header("X-API-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 0) or 0)
    except urllib.error.HTTPError as e:
        return int(e.code)
    except Exception:  # noqa: BLE001 连不上/超时 -> 无响应
        return None


def run_smoke(port: int = DEFAULT_PORT, token: Optional[str] = None,
              paths: Sequence[str] = DEFAULT_SMOKE_PATHS,
              timeout: float = 5.0) -> List[Dict[str, Any]]:
    """对关键端点逐个 GET，返回 [{path, status, ok}]；只判 200。"""
    results: List[Dict[str, Any]] = []
    for path in paths:
        url = "http://127.0.0.1:%d%s" % (int(port), path)
        code = http_status(url, token, timeout=timeout)
        results.append({"path": path, "status": code, "ok": code == 200})
    return results


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
        # 回退前必须再确认一次端口：看护的检测周期（30s × 3 次失败）叠上实测 87s 的
        # 冷启动，总耗时可能**刚好越过** ready_timeout（2026-10-31 实测差 1 秒）。
        # 这时若盲目 spawn，就会变成双主：两个 app_server 同时加载、后起的那个抢不到
        # 端口却仍在后台跑（实测需要人工 kill）。见 §11.30。
        late = listening_pids(port)
        if late:
            log("回退前发现端口已被 pid=%s 占用（看护已拉起），不再直接启动"
                % ",".join(str(p) for p in late))
            ok, waited2 = wait_health(timeout=min(FALLBACK_RECHECK_WAIT, ready_timeout))
            return ("watchdog" if ok else "failed"), waited + waited2
        log("看护进程未在 %ss 内拉起，回退为直接启动" % int(ready_timeout))
    pid = spawn_backend(repo_root, python_exe)
    log("已直接拉起后端 pid=%s，等待就绪..." % pid)
    ok, waited = wait_health(timeout=ready_timeout)
    return ("direct" if ok else "failed"), waited


def rebuild_frontend(repo: Path, npm: str) -> bool:
    """按需 npm ci + build:flask；成功返回 True。"""
    ui = Path(repo) / "yunshu-ui"
    if not (ui / "node_modules").exists():
        log("yunshu-ui/node_modules 不存在，先 npm ci ...")
        rc, out, err = run(npm_command(npm, ["ci", "--prefer-offline"]), cwd=ui, timeout=1800.0)
        if rc != 0:
            log("npm ci 失败: " + err[-500:])
            return False
    log("重建前端：npm run build:flask ...")
    rc, out, err = run(npm_command(npm, ["run", "build:flask"]), cwd=ui, timeout=1800.0)
    if rc != 0:
        log("前端重建失败: " + err[-500:])
        return False
    log("前端重建完成")
    return True


def restart_verify_smoke(repo: Path, args: argparse.Namespace, target: str) -> int:
    """重启（可跳过）+ 读 /api/version（可 --smoke 冒烟）；返回退出码（0 正常）。"""
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
        build_commit = frontend.get("build_commit")
        if build_commit and target and not stamp_matches({"commit": build_commit}, target):
            log("警告：前端构建戳 commit 与目标不一致（%s != %s）"
                % (str(build_commit)[:12], target[:12]))
    else:
        log("未能读取 /api/version（端点未就绪，或需要 .env 里的 FLASK_API_TOKEN）")

    if args.smoke:
        results = run_smoke(args.port, token, paths=tuple(args.smoke_paths or DEFAULT_SMOKE_PATHS))
        for r in results:
            shown = r["status"] if r["status"] is not None else "无响应"
            log("冒烟 %-14s -> %s" % (r["path"], shown))
        if not all(r["ok"] for r in results):
            log("冒烟失败：存在非 200 端点")
            return 6
    return 0


def _write_record(**fields: Any) -> None:
    write_json_atomic(DEPLOY_RECORD, fields)


def _write_record_safe(record: Dict[str, Any]) -> bool:
    """写部署记录；写不进去要显式失败（回滚依据不能静默丢）。"""
    if write_json_atomic(DEPLOY_RECORD, record):
        return True
    log("无法写部署记录 %s，拒绝继续（否则回滚无依据）" % DEPLOY_RECORD)
    return False


def deploy_update_guard(head: str, target: str, cwd: Path = REPO_ROOT) -> Tuple[int, List[str]]:
    """ff-only 前的脏路径相交检查；返回 (退出码, incoming文件)。0 表示可继续。"""
    incoming: List[str] = []
    if head == target:
        log("代码已是最新，无需更新")
        return 0, incoming
    rc, out, _ = git("diff", "--name-only", head, target, cwd=cwd)
    incoming = [x for x in out.splitlines() if x.strip()]
    rc2, dirty_out, _ = git("status", "--porcelain", cwd=cwd)
    dirty = parse_porcelain(dirty_out)
    conflicts = overwrites(dirty, incoming)
    log("待更新 %d 个文件；本地脏路径 %d 个；相交 %d 个"
        % (len(incoming), len(dirty), len(conflicts)))
    if conflicts:
        log("拒绝 ff-only：以下本地改动会被覆盖，请先处置 -> " + ", ".join(conflicts[:20]))
        return 3, incoming
    return 0, incoming


def run_rollback(args: argparse.Namespace, head: str, repo: Path) -> int:
    """--rollback：回滚到 logs/deploy_last.json 记录的 from_commit。"""
    record = read_json(DEPLOY_RECORD)
    if not isinstance(record, dict):
        log("没有回滚记录 %s（尚未由本脚本部署过，或记录被删）" % DEPLOY_RECORD)
        return 7
    if record.get("rolled_back_at"):
        log("上次回滚已在 %s 执行过（目标 %s），拒绝重复回滚；如需再操作请手工 git reset"
            % (record.get("rolled_back_at"), str(record.get("rolled_back_to"))[:12]))
        return 8
    target = record.get("from_commit")
    if not isinstance(target, str) or not target:
        log("回滚记录缺少 from_commit，无法回滚（记录：%s）" % DEPLOY_RECORD)
        return 7
    if not commit_exists(target, repo):
        log("回滚目标 %s 不在本地仓库（可能需先 git fetch）" % target[:12])
        return 7

    log("当前 HEAD : %s" % head[:12])
    log("回滚目标  : %s  %s" % (target[:12], commit_subject(target, repo) or ""))

    incoming: List[str] = []
    if head != target:
        rc, out, _ = git("diff", "--name-only", head, target, cwd=repo)
        incoming = [x for x in out.splitlines() if x.strip()]
        rc2, dirty_out, _ = git("status", "--porcelain", cwd=repo)
        dirty = parse_porcelain(dirty_out)
        conflicts = overwrites(dirty, incoming)
        log("待回退 %d 个文件；本地脏路径 %d 个；相交 %d 个"
            % (len(incoming), len(dirty), len(conflicts)))
        if conflicts:
            log("拒绝回滚：以下本地改动会被覆盖，请先处置 -> " + ", ".join(conflicts[:20]))
            return 3
    else:
        log("已在回滚目标上，无需移动 HEAD")

    need_build = args.force_build or frontend_needs_rebuild(incoming, expected_commit=target)
    log("前端需要重建: %s" % need_build)

    if args.check:
        log("--check 干跑结束：未回滚、未重建、未重启")
        return 0

    if head != target:
        mode = "--hard" if args.hard else "--keep"
        rc, out, err = git("reset", mode, target, cwd=repo)
        if rc != 0:
            log("git reset %s 失败: %s" % (mode, err))
            return 3
        log("代码已回退到 %s（git reset %s）" % (target[:12], mode))

    if need_build and not args.no_build:
        if not rebuild_frontend(repo, args.npm):
            return 4
        write_build_stamp(BUILD_STAMP, target)
        log("构建戳已写入 %s" % BUILD_STAMP)

    code = restart_verify_smoke(repo, args, target)

    record["rolled_back_at"] = now_iso()
    record["rolled_back_to"] = target
    record["rolled_back_from"] = head
    record["rollback_result"] = "ok" if code == 0 else "exit:%d" % code
    _write_record(**record)
    return code


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="工作台一键部署/重启（fetch->ff-only->前端按需重建->重启->验证）")
    ap.add_argument("--ref", default=DEFAULT_REF, help="目标 ref（默认 origin/master）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="后端端口（默认 5678）")
    ap.add_argument("--check", action="store_true", help="干跑：只报告将做什么，不改动/不重启")
    ap.add_argument("--no-fetch", action="store_true", help="跳过 git fetch")
    ap.add_argument("--no-build", action="store_true", help="跳过前端重建")
    ap.add_argument("--no-restart", action="store_true", help="不重启后端")
    ap.add_argument("--force-build", action="store_true", help="无条件重建前端")
    ap.add_argument("--smoke", action="store_true", help="重启后对关键端点冒烟（默认 /api/health + /api/version + /chat）")
    ap.add_argument("--smoke-paths", nargs="*", default=None, help="自定义冒烟路径（配合 --smoke）")
    ap.add_argument("--rollback", action="store_true", help="回滚到 logs/deploy_last.json 记录的 from_commit")
    ap.add_argument("--hard", action="store_true", help="回滚时用 git reset --hard（默认 --keep，更安全）")
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

    if args.rollback:
        return run_rollback(args, head, repo)

    if not args.no_fetch and not args.check:
        git("fetch", "origin")

    rc, target, err = git("rev-parse", args.ref)
    if rc != 0:
        log("无法解析 ref %s: %s" % (args.ref, err))
        return 2

    log("当前 HEAD : %s" % head[:12])
    log("目标      : %s -> %s" % (args.ref, target[:12]))

    rc, incoming = deploy_update_guard(head, target, repo)
    if rc != 0:
        return rc

    need_build = args.force_build or frontend_needs_rebuild(incoming, expected_commit=target)
    log("前端需要重建: %s" % need_build)
    if need_build and args.no_build:
        log("警告：构建戳/源码提示前端需要重建，但指定了 --no-build，跳过")

    if args.check:
        log("--check 干跑结束：未改代码、未重建、未重启")
        return 0

    record: Dict[str, Any] = {
        "started_at": now_iso(),
        "from_commit": head,
        "from_subject": commit_subject(head, repo),
        "to_commit": target,
        "to_subject": commit_subject(target, repo),
        "ref": args.ref,
        "frontend_rebuild_planned": bool(need_build),
        "result": "started",
    }
    if head != target:
        # 先落盘再动代码：即使 ff/构建中途崩溃，--rollback 仍有依据。
        if not _write_record_safe(record):
            return 9
        rc, out, err = git("merge", "--ff-only", args.ref)
        if rc != 0:
            record["result"] = "ff-failed"
            _write_record(**record)
            log("ff-only 更新失败: " + err)
            return 3
        log("代码已更新到 %s" % target[:12])

    frontend_rebuilt = False
    if need_build and not args.no_build:
        if not rebuild_frontend(repo, args.npm):
            record["result"] = "build-failed"
            _write_record(**record)
            return 4
        frontend_rebuilt = True
        if write_build_stamp(BUILD_STAMP, target):
            log("构建戳已写入 %s" % BUILD_STAMP)

    code = restart_verify_smoke(repo, args, target)

    record["finished_at"] = now_iso()
    record["frontend_rebuilt"] = frontend_rebuilt
    record["result"] = "ok" if code == 0 else "exit:%d" % code
    _write_record(**record)
    log("完成")
    return code


if __name__ == "__main__":
    sys.exit(main())
