# -*- coding: utf-8 -*-
"""E4-5 第三批（只读）+ 第六批续：worktree 的 B 级写路径（Owner 2026-11-28 已点头）

【范围（Owner 拍板：只开"创建 + 提交到新分支"，不开 forge/推远程）】
  · create()：在 <repo>/.worktrees/<name> 建一个 worktree，分支固定前缀 yunshu/<name>；
  · commit()：在该 worktree 里 git add -A + git commit -m <message>；
  · 不涉及：推送、force push、改远程、建 tag、rebase、删除 worktree。

【不易·路径只做等值匹配】worktree 路径只能由 <repo>/.worktrees/<合法 name> 派生，
  或来自 git worktree list 的输出；HTTP 传来的 name 必须过 _NAME_RE（小写字母/数字/连字符，
  <=40），绝不接受路径。argv 全部是服务端常量，name 只作为一个受限参数拼入。

【变易·两把钥匙】CP_WORKTREE_PANEL_ENABLED（看）与 CP_WORKTREE_WRITE_ENABLED（写）分开；
  写路径默认关，关时回 write_disabled 且一个 git 命令都不跑。

【简易·提交信息是数据不是命令】message 只保留可打印字符（去控制符、折叠空白、<=200 字符），
  空 => 拒绝；提交失败（无事可提交/钩子失败）回稳定 code，不 500。
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 只读面板开关（默认关）
ENABLED_ENV = "CP_WORKTREE_PANEL_ENABLED"
#: 写路径开关（B 级，默认关；Owner 点头后才开）
WRITE_ENABLED_ENV = "CP_WORKTREE_WRITE_ENABLED"
#: 最多列多少个 worktree
MAX_WORKTREES = 20
#: diff --stat 最多回多少字符
MAX_DIFF_CHARS = 4000
#: 单个只读 git 子命令超时（秒）
GIT_TIMEOUT_SEC = 10
#: 写命令（add/commit）超时（秒）——钩子可能慢
WRITE_TIMEOUT_SEC = 60
#: worktree 名字与分支前缀
WORKTREES_DIRNAME = ".worktrees"
BRANCH_PREFIX = "yunshu/"
MAX_NAME_CHARS = 40
MAX_COMMIT_CHARS = 200

_ROOT = Path(__file__).resolve().parents[1]

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

#: 失败码 → 人读说明（中文唯一一份）
CODE_MESSAGES: Dict[str, str] = {
    "disabled": "worktree 面板未启用（CP_WORKTREE_PANEL_ENABLED=0）。",
    "write_disabled": "worktree 写路径未启用（CP_WORKTREE_WRITE_ENABLED=0）。",
    "no_git": "本机没有可用的 git 可执行文件。",
    "not_a_repo": "当前工作目录不是 git 仓库，或 git 读取 worktree 失败。",
    "bad_name": "名字只能用 1-40 位小写字母/数字/连字符，且以字母或数字开头。",
    "exists": "同名 worktree 目录已存在。",
    "branch_exists": "同名分支已存在（换个名字，或先删掉旧 worktree）。",
    "create_failed": "创建 worktree 失败（详见服务日志）。",
    "commit_failed": "提交失败（详见服务日志）。",
    "nothing_to_commit": "该 worktree 没有可提交的改动。",
    "bad_message": "提交信息不能为空，且不超过 200 个字符。",
    "not_found": "该名字不是本仓库的 worktree（只允许 .worktrees/ 下已存在的名字）。",
}


def enabled() -> bool:
    import os
    return str(os.environ.get(ENABLED_ENV, "") or "").strip().lower() in (
        "1", "true", "yes", "on")


def write_enabled() -> bool:
    import os
    return str(os.environ.get(WRITE_ENABLED_ENV, "") or "").strip().lower() in (
        "1", "true", "yes", "on")


def _git() -> Optional[str]:
    try:
        return shutil.which("git")
    except Exception:  # noqa: BLE001
        return None


def _run(exe: str, args: List[str], timeout: int = GIT_TIMEOUT_SEC) -> Tuple[int, str]:
    """跑一个 git 命令（隐藏窗口；超时/异常回 (1, "")）"""
    from agent.utils import proc as _proc

    try:
        done = subprocess.run(
            [exe] + list(args), **_proc.hidden_kwargs(),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
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
    """解析 git worktree list --porcelain（纯函数，便于契约测试）"""
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
        {"ok": True, "root": str, "worktrees": [...], "truncated": bool, "write_enabled": bool}
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
            "truncated": truncated, "write_enabled": write_enabled()}


# ── 写路径（B 级，Owner 已点头；只建 worktree 与本地提交）─────────────────


def valid_name(name: Any) -> bool:
    return bool(_NAME_RE.match(str(name or "")))


def sanitize_message(message: Any) -> str:
    """提交信息是数据：去控制符、折叠空白、截断到 200 字符"""
    text = _CTRL_RE.sub(" ", str(message or ""))
    text = " ".join(text.split())
    return text[:MAX_COMMIT_CHARS]


def _base(root: Optional[Path]) -> Path:
    return Path(root) if root is not None else _ROOT


def _listed_worktrees(exe: str, base: Path) -> List[Dict[str, Any]]:
    rc, out = _run(exe, ["-C", str(base), "worktree", "list", "--porcelain"])
    if rc != 0:
        return []
    return parse_porcelain(out)


def create(name: Any, root: Optional[Path] = None) -> Dict[str, Any]:
    """在 <repo>/.worktrees/<name> 建 worktree（分支 yunshu/<name>，基于当前 HEAD）"""
    if not write_enabled():
        return {"ok": False, "code": "write_disabled",
                "error": CODE_MESSAGES["write_disabled"]}
    if not valid_name(name):
        return {"ok": False, "code": "bad_name", "error": CODE_MESSAGES["bad_name"]}
    exe = _git()
    if not exe:
        return {"ok": False, "code": "no_git", "error": CODE_MESSAGES["no_git"]}
    base = _base(root)
    target = base / WORKTREES_DIRNAME / str(name)
    if target.exists():
        return {"ok": False, "code": "exists", "error": CODE_MESSAGES["exists"]}
    branch = BRANCH_PREFIX + str(name)
    rc, out = _run(exe, ["-C", str(base), "worktree", "add", "-b", branch,
                         str(target), "HEAD"], timeout=WRITE_TIMEOUT_SEC)
    if rc != 0:
        low = str(out or "").lower()
        if "already exists" in low:
            return {"ok": False, "code": "branch_exists",
                    "error": CODE_MESSAGES["branch_exists"]}
        logger.warning("[worktree] create 失败 rc=%s", rc)
        return {"ok": False, "code": "create_failed",
                "error": CODE_MESSAGES["create_failed"]}
    return {"ok": True, "name": str(name), "path": str(target), "branch": branch}


def commit(name: Any, message: Any, root: Optional[Path] = None) -> Dict[str, Any]:
    """在已存在的 worktree 里 git add -A + git commit -m <message>（不推送）"""
    if not write_enabled():
        return {"ok": False, "code": "write_disabled",
                "error": CODE_MESSAGES["write_disabled"]}
    if not valid_name(name):
        return {"ok": False, "code": "bad_name", "error": CODE_MESSAGES["bad_name"]}
    text = sanitize_message(message)
    if not text:
        return {"ok": False, "code": "bad_message", "error": CODE_MESSAGES["bad_message"]}
    exe = _git()
    if not exe:
        return {"ok": False, "code": "no_git", "error": CODE_MESSAGES["no_git"]}
    base = _base(root)
    expected = (base / WORKTREES_DIRNAME / str(name)).resolve()
    matched = None
    for item in _listed_worktrees(exe, base):
        try:
            if Path(str(item.get("path") or "")).resolve() == expected:
                matched = item
                break
        except OSError:
            continue
    if matched is None:
        return {"ok": False, "code": "not_found", "error": CODE_MESSAGES["not_found"]}
    path = str(matched.get("path") or "")
    rc, out = _run(exe, ["-C", path, "status", "--porcelain=v1"], timeout=WRITE_TIMEOUT_SEC)
    if rc != 0:
        return {"ok": False, "code": "commit_failed", "error": CODE_MESSAGES["commit_failed"]}
    changed = len([line for line in str(out or "").splitlines() if line.strip()])
    if changed == 0:
        return {"ok": False, "code": "nothing_to_commit",
                "error": CODE_MESSAGES["nothing_to_commit"]}
    rc, out = _run(exe, ["-C", path, "add", "-A"], timeout=WRITE_TIMEOUT_SEC)
    if rc != 0:
        logger.warning("[worktree] add 失败 rc=%s", rc)
        return {"ok": False, "code": "commit_failed", "error": CODE_MESSAGES["commit_failed"]}
    rc, out = _run(exe, ["-C", path, "commit", "-m", text], timeout=WRITE_TIMEOUT_SEC)
    if rc != 0:
        logger.warning("[worktree] commit 失败 rc=%s", rc)
        return {"ok": False, "code": "commit_failed", "error": CODE_MESSAGES["commit_failed"]}
    rc, sha = _run(exe, ["-C", path, "rev-parse", "--short", "HEAD"])
    return {"ok": True, "name": str(name), "branch": str(matched.get("branch") or ""),
            "commit": str(sha or "").strip(), "changed": changed}


__all__ = [
    "BRANCH_PREFIX", "CODE_MESSAGES", "ENABLED_ENV", "GIT_TIMEOUT_SEC", "MAX_COMMIT_CHARS",
    "MAX_DIFF_CHARS", "MAX_NAME_CHARS", "MAX_WORKTREES", "WORKTREES_DIRNAME",
    "WRITE_ENABLED_ENV", "WRITE_TIMEOUT_SEC", "collect", "commit", "create", "enabled",
    "parse_porcelain", "sanitize_message", "valid_name", "write_enabled",
]
