"""E4-5 第三批：git worktree / forge 的**只读**投影

【不易·只读】本模块只跑四个只读 git 子命令（worktree list / status / diff），
不改仓库、不建分支、不提交、不推远程。argv 全部是**服务端常量**（路径来自 git 自己的
输出，不来自 HTTP 参数）—— 与「HTTP 侧 argv 必须来自常量表」同一口径。

【变易·路径只做等值匹配】worktree 路径来自 `git worktree list --porcelain` 的输出；
路由不接受用户传路径。后续若要「指定某个 worktree」，也只能拿列表里的名字做等值匹配。

【简易·上限】最多 20 个 worktree；status 只回计数（不搬全量）；diff --stat 截到
4000 字符并标 truncated。git 缺失/不是仓库都回稳定 code，不 500。
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 开关（默认关；判据只在这一处与 settings/registry 登记项）
ENABLED_ENV = "CP_WORKTREE_PANEL_ENABLED"
#: 最多列多少个 worktree
MAX_WORKTREES = 20
#: diff --stat 最多回多少字符
MAX_DIFF_CHARS = 4000
#: 单个 git 子命令超时（秒）
GIT_TIMEOUT_SEC = 10

_ROOT = Path(__file__).resolve().parents[1]

#: 失败码 → 人读说明（中文唯一一份）
CODE_MESSAGES: Dict[str, str] = {
    "disabled": "worktree 面板未启用（CP_WORKTREE_PANEL_ENABLED=0）。",
    "no_git": "本机没有可用的 git 可执行文件。",
    "not_a_repo": "当前工作目录不是 git 仓库，或 git 读取 worktree 失败。",
}


def enabled() -> bool:
    import os
    return str(os.environ.get(ENABLED_ENV, "") or "").strip().lower() in (
        "1", "true", "yes", "on")


def _git() -> Optional[str]:
    try:
        return shutil.which("git")
    except Exception:  # noqa: BLE001
        return None


def _run(exe: str, args: List[str]) -> Tuple[int, str]:
    """跑一个只读 git 命令（隐藏窗口；超时/异常回 (1, "")）"""
    from agent.utils import proc as _proc

    try:
        done = subprocess.run(
            [exe] + list(args), **_proc.hidden_kwargs(),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=GIT_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
        logger.debug("[worktree] git 子命令失败: %s", type(exc).__name__)
        return 1, ""
    return int(done.returncode), str(done.stdout or "")


def _unquote(raw: str) -> str:
    """porcelain 里的路径可能被 C 风格引号包住（空格/特殊字符）；做最小反解"""
    text = str(raw or "")
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
        text = text.replace('\\"', '"').replace("\\\\", "\\")
    return text


def parse_porcelain(output: str) -> List[Dict[str, Any]]:
    """解析 `git worktree list --porcelain`（纯函数，便于契约测试）"""
    items: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for line in str(output or "").splitlines():
        if not line.strip():
            continue
        if line.startswith("worktree "):
            if current is not None:
                items.append(current)
            current = {"path": _unquote(line[len("worktree "):]),
                       "head": "", "branch": "", "detached": False,
                       "bare": False, "locked": False}
            continue
        if current is None:
            continue
        if line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            current["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            current["detached"] = True
        elif line == "bare":
            current["bare"] = True
        elif line.startswith("locked"):
            current["locked"] = True
    if current is not None:
        items.append(current)
    for item in items:
        item["name"] = Path(str(item.get("path") or "")).name
    return items


def _status_counts(exe: str, path: str) -> Dict[str, int]:
    rc, out = _run(exe, ["-C", path, "status", "--porcelain=v1",
                         "--untracked-files=no"])
    counts: Dict[str, int] = {"M": 0, "A": 0, "D": 0, "R": 0, "C": 0, "U": 0}
    if rc != 0:
        return counts
    for line in out.splitlines():
        code = line[:2]
        for ch in code:
            if ch in counts:
                counts[ch] += 1
    return counts


def _diff_stat(exe: str, path: str) -> Tuple[str, bool]:
    rc, out = _run(exe, ["-C", path, "diff", "--stat", "HEAD", "--no-color"])
    if rc != 0:
        rc, out = _run(exe, ["-C", path, "diff", "--stat", "--no-color"])
    text = str(out or "").strip()
    truncated = len(text) > MAX_DIFF_CHARS
    return text[:MAX_DIFF_CHARS], truncated


def _details(exe: str, path: str) -> Dict[str, Any]:
    counts = _status_counts(exe, path)
    diff_stat, truncated = _diff_stat(exe, path)
    dirty = any(int(v) > 0 for v in counts.values())
    return {"dirty": dirty, "counts": counts,
            "diff_stat": diff_stat, "diff_truncated": truncated}


def collect(root: Optional[Path] = None) -> Dict[str, Any]:
    """只读投影：worktree 列表 + 每个的 status 计数与 diff --stat

    Returns:
        {"ok": True, "root": str, "worktrees": [...], "truncated": bool}
        或 {"ok": False, "code": ..., "error": ...}
    """
    exe = _git()
    if not exe:
        return {"ok": False, "code": "no_git", "error": CODE_MESSAGES["no_git"]}
    base = Path(root) if root is not None else _ROOT
    rc, out = _run(exe, ["-C", str(base), "worktree", "list", "--porcelain"])
    if rc != 0:
        return {"ok": False, "code": "not_a_repo", "error": CODE_MESSAGES["not_a_repo"]}
    items = parse_porcelain(out)
    truncated = len(items) > MAX_WORKTREES
    listed = items[:MAX_WORKTREES]
    for item in listed:
        try:
            item.update(_details(exe, str(item.get("path") or "")))
        except Exception as exc:  # noqa: BLE001 单个 worktree 坏了只影响它自己
            logger.debug("[worktree] 读取 %s 失败: %s", item.get("name"), type(exc).__name__)
            item.update({"dirty": False, "counts": {}, "diff_stat": "",
                         "diff_truncated": False})
    return {"ok": True, "root": str(base), "worktrees": listed,
            "truncated": truncated}


__all__ = [
    "CODE_MESSAGES", "ENABLED_ENV", "GIT_TIMEOUT_SEC", "MAX_DIFF_CHARS",
    "MAX_WORKTREES", "collect", "enabled", "parse_porcelain",
]
