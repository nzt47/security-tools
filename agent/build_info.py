# -*- coding: utf-8 -*-
"""构建/版本信息采集（部署漂移可视化，2026-10-16）。

【它回答什么】"现在这个进程到底加载的是哪一版？线上前端又是哪一版？"

背景事故（2026-10-16 实测）：主仓工作树停在旧 commit 而远端 PR 已合并，看护进程
只探活、不换代码 ⇒ 线上长期跑旧代码却无人察觉；前端产物又是**另一个**独立步骤。
故这里把"后端 commit / 前端 bundle / 进程启动"三件事收成一份只读快照。

【口径】
- backend：**进程导入本模块那一刻**解析的 git commit（不是每次请求现查工作树）——
  这才代表"本进程加载的代码"；工作树之后被改动不应改变本进程的答案。用 refresh=True 可强制重查。
- frontend：读 templates/yunshu.html（build:flask 的产物）里引用的主 bundle 文件名；built_at
  优先取 static/assets/.build-stamp.json（deploy_workbench.py 构建成功后写入），无戳时退回该文件 mtime。
- 构建戳还记录构建时的源码 commit（build_commit），可用于核对前后端是否同版；无戳则为 None。
- 全部 best-effort：没有 git / 没有产物 / 命令失败都不抛异常，字段为 None 并带 error。

本模块**不 import Flask / app_server**，可被插件、脚本、测试自由导入。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

#: 仓库根 = 本文件（agent/build_info.py）的上一级
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 本进程的启动时间（导入即固定）
PROCESS_STARTED_AT = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

#: 主 bundle 形如 src="/static/assets/index-BjKvrIBE.js"
_MAIN_BUNDLE = re.compile(r'src="/static/assets/(index-[^"]+\.js)"')

#: 前端构建戳（deploy_workbench.py 构建成功后写入；static/assets/ 不入库）
_BUILD_STAMP_REL = ("static", "assets", ".build-stamp.json")

_BACKEND_CACHE: Optional[Dict[str, Any]] = None


def _run(cmd, cwd: Path, timeout: float = 10.0) -> Tuple[Optional[str], str]:
    """跑一条外部命令，返回 (stdout, error)；失败不抛异常。"""
    try:
        proc = subprocess.run(
            list(cmd), cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except Exception as e:  # noqa: BLE001 无 git / 超时 / 权限问题一律降级
        return None, "%s: %s" % (type(e).__name__, e)
    if proc.returncode != 0:
        return None, (proc.stderr or "").strip()[:300]
    return (proc.stdout or "").strip(), ""


def _porcelain_path(line: str) -> str:
    """从 `git status --porcelain` 的一行里取路径。

    porcelain 形态是 `XY<space>PATH`（路径从下标 3 开始），但 `_run()` 会对整体
    stdout 做 `.strip()`，于是**首行**的前导空格被削掉（` M data/x` → `M data/x`）；
    此时再按固定偏移 `line[3:]` 切就会吃掉路径首字符（线上实测 `data/logs/...`
    被显示成 `ata/logs/...`）。先剥掉 1 位状态码再 strip，两种形态都能取到完整路径。
    """
    return line[2:].strip() if len(line) > 2 else ""


def backend_info(repo_root: Path = REPO_ROOT, *, refresh: bool = False) -> Dict[str, Any]:
    """后端 git 信息（默认缓存：代表本进程加载的代码）。"""
    global _BACKEND_CACHE
    if _BACKEND_CACHE is not None and not refresh:
        return dict(_BACKEND_CACHE)

    info: Dict[str, Any] = {
        "commit": None, "commit_subject": None, "committed_at": None,
        "dirty": None, "dirty_files": None, "error": None,
    }
    sha, err = _run(["git", "rev-parse", "HEAD"], repo_root)
    if not sha:
        info["error"] = err or "git rev-parse 无输出"
        _BACKEND_CACHE = info
        return dict(info)
    info["commit"] = sha
    subject, _ = _run(["git", "log", "-1", "--format=%s"], repo_root)
    info["commit_subject"] = subject
    committed_at, _ = _run(["git", "log", "-1", "--format=%cI"], repo_root)
    info["committed_at"] = committed_at
    porcelain, _ = _run(["git", "status", "--porcelain"], repo_root)
    if porcelain is not None:
        files = [_porcelain_path(ln) for ln in porcelain.splitlines() if ln.strip()]
        info["dirty_files"] = files
        info["dirty"] = bool(files)
    _BACKEND_CACHE = info
    return dict(info)


def _read_build_stamp(repo_root: Path) -> Optional[Dict[str, Any]]:
    """读前端构建戳（deploy_workbench.py 构建成功后写入）；没有/损坏返回 None。"""
    path = Path(repo_root).joinpath(*_BUILD_STAMP_REL)
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def frontend_info(repo_root: Path = REPO_ROOT) -> Dict[str, Any]:
    """线上前端产物信息（templates/yunshu.html 引用的主 bundle）。

    built_at 优先取构建戳（构建成功那一刻），无戳时退回产物文件 mtime（built_at_source
    标明来源）；build_commit 是构建戳记录的源码 commit，用于核对前后端是否同版。
    """
    html = Path(repo_root) / "templates" / "yunshu.html"
    out: Dict[str, Any] = {
        "index_html": str(html), "index_html_present": html.exists(),
        "bundle": None, "built_at": None, "built_at_source": None,
        "build_commit": None, "error": None,
    }
    if not html.exists():
        return out
    try:
        text = html.read_text(encoding="utf-8", errors="replace")
        m = _MAIN_BUNDLE.search(text)
        if m:
            out["bundle"] = m.group(1)
    except OSError as e:  # noqa: PERF203 读不到就如实报错
        out["error"] = str(e)
    stamp = _read_build_stamp(repo_root)
    if stamp:
        if isinstance(stamp.get("commit"), str) and stamp.get("commit"):
            out["build_commit"] = stamp["commit"]
        if isinstance(stamp.get("built_at"), str) and stamp["built_at"]:
            out["built_at"] = stamp["built_at"]
            out["built_at_source"] = "build-stamp"
    if out["built_at"] is None:
        try:
            out["built_at"] = datetime.fromtimestamp(
                html.stat().st_mtime, tz=timezone.utc
            ).astimezone().isoformat(timespec="seconds")
            out["built_at_source"] = "mtime"
        except OSError as e:
            if not out["error"]:
                out["error"] = str(e)
    return out


def process_info() -> Dict[str, Any]:
    """本进程信息（用于区分"同名但不同进程/不同启动时间"）。"""
    return {
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "started_at": PROCESS_STARTED_AT,
        "cwd": os.getcwd(),
    }


def collect(repo_root: Path = REPO_ROOT, *, refresh: bool = False) -> Dict[str, Any]:
    """后端 + 前端 + 进程三部分快照。"""
    return {
        "backend": backend_info(repo_root, refresh=refresh),
        "frontend": frontend_info(repo_root),
        "process": process_info(),
    }
