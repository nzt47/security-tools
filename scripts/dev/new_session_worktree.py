#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新会话隔离 worktree 创建脚本

背景（Why）:
    并行会话共享同一 <repo>/.git 目录时，refs / index / worktrees 注册均为全局
    单例文件，存在非原子竞争（现象：worktree add 报"分支已存在"但 reflog 显示
    是自己创建的、branch -D 报 not found、"no changes added to commit"）。
    本脚本为每个新会话分配专属 worktree 与分支前缀 sN/，把竞争面隔离到各自
    前缀下；worktree 目录位于 <repo>/.worktrees/（已入 .gitignore），互不干扰。

用法:
    python scripts/dev/new_session_worktree.py                    # 自动分配下个会话号并创建
    python scripts/dev/new_session_worktree.py --id s7            # 指定会话号
    python scripts/dev/new_session_worktree.py --base develop     # 基准分支（默认 develop）
    python scripts/dev/new_session_worktree.py list               # 列出所有会话
    python scripts/dev/new_session_worktree.py cleanup s7         # 清理会话（worktree + 前缀分支）
    python scripts/dev/new_session_worktree.py guard s7/fix-x     # 校验分支归属会话前缀

会话约定（不易）:
    - 会话号 sN 全局唯一；worktree 路径 <repo>/.worktrees/sN
    - 分支前缀 sN/：该会话内所有新分支必须命名为 sN/<name>，禁止跨前缀操作
    - 会话元数据写入 <repo>/.git/worktrees/sN/session-config.json
      （git 内部目录，不入版本库）
    - 创建成功后自动提供主工作区 .env（优先符号链接，失败回退复制）；
      已存在的 .env 绝不覆盖，未被 .gitignore 忽略时回滚删除（禁止入 git）

.env 供给（Why）:
    worktree 是独立工作目录，主工作区的 .env 不会自动出现。缺 .env 的 worktree
    内进程没有 LLM 配置，只能走离线响应，极易把"环境没配"误判成"功能不达标"
    （S9-01 实测登记遗留 #6：.worktrees/<id>/.env 为 0 字节，主工作区为 144741 字节）。
    因此 create 成功后默认提供 .env，让新会话开箱即用。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKTREES_DIR = REPO_ROOT / ".worktrees"
SESSION_RE = re.compile(r"^s(\d+)$")
GITIGNORE_MARKER = "# 并行会话隔离 worktree（new_session_worktree.py 生成）"
ENV_NAME = ".env"


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8"
    )


def _git_at(path: Path, *args: str) -> subprocess.CompletedProcess:
    """在指定目录（如 worktree 根）内执行 git，取该 worktree 自身的视图"""
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, encoding="utf-8"
    )


def _session_no(session_id: str) -> int:
    m = SESSION_RE.match(session_id)
    if not m:
        sys.exit(f"会话 ID 必须是 s<数字> 格式（如 s3），收到: {session_id!r}")
    return int(m.group(1))


def _existing_sessions() -> list[str]:
    """收集 .git/worktrees 与 .worktrees/ 下登记的会话号（去重排序）"""
    sessions: set[str] = set()
    gitdir_wt = REPO_ROOT / ".git" / "worktrees"
    if gitdir_wt.is_dir():
        for p in gitdir_wt.iterdir():
            if p.is_dir() and SESSION_RE.match(p.name):
                sessions.add(p.name)
    if WORKTREES_DIR.is_dir():
        for p in WORKTREES_DIR.iterdir():
            if p.is_dir() and SESSION_RE.match(p.name):
                sessions.add(p.name)
    return sorted(sessions, key=lambda s: _session_no(s))


def _ensure_gitignore() -> None:
    """幂等追加 /.worktrees/ 到 .gitignore（worktree 目录不入版本库）"""
    gi = REPO_ROOT / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if "/.worktrees/" in text:
        return
    gi.write_text(
        text.rstrip() + "\n\n" + GITIGNORE_MARKER + "\n/.worktrees/\n",
        encoding="utf-8",
    )
    print(f"[gitignore] 已追加 /.worktrees/ 到 .gitignore")


def _file_size(path: Path) -> int:
    """文件大小（跟随符号链接）；不可读/断链返回 0"""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _env_status_entry(wt_path: Path) -> str | None:
    """`git -C <worktree> status --short` 中与 .env 相关的行；None 表示未出现。

    两条守卫语义：
      - 返回 None  ≈ .env 已被 .gitignore 忽略（期望值，不入 git）；
      - 返回非 None ⇒ .env 会以未跟踪文件出现在 status 里，有被 add/commit 的风险。
    注：worktree 内的忽略判定用的是 worktree 自身的树（`/.worktrees/` 锚定的是
    worktree 根，管不到 worktree 根下的 .env），真正兜底的是 .gitignore 里的通用
    `.env` 规则 —— 故此处必须实测 status，而不是假定"在 .worktrees/ 下必然被忽略"。
    """
    r = _git_at(wt_path, "status", "--short", "--untracked-files=all", "--", ENV_NAME)
    if r.returncode != 0:
        return None  # 非 git 目录等无法判定场景：不阻断（不臆造结论）
    return r.stdout.strip() or None


def _try_symlink(source: Path, target: Path) -> bool:
    """优先建符号链接（随主工作区同步更新）；无权限/不支持时返回 False 交回退处理"""
    try:
        target.symlink_to(source)
        return True
    except (OSError, NotImplementedError):
        return False


def _provide_env(wt_path: Path, source: Path | None = None) -> dict:
    """在 worktree 根提供主工作区 .env：符号链接优先，失败回退复制。

    不覆盖已存在的 .env；写完实测 `git status --short`，一旦 .env 会出现在 status
    里（即未被忽略）立即回滚删除，绝不把 .env 带进 git。

    返回 {"mode", "path", "bytes"[,"status_entry"]}，mode ∈
      linked / copied / skipped-exists / missing-source / blocked-untracked
    """
    source = source if source is not None else REPO_ROOT / ENV_NAME
    target = wt_path / ENV_NAME

    if target.exists() or target.is_symlink():
        return {"mode": "skipped-exists", "path": target, "bytes": _file_size(target)}
    if not source.exists():
        return {"mode": "missing-source", "path": source, "bytes": 0}

    linked = _try_symlink(source, target)
    if not linked:
        # 符号链接失败的半成品残留（极少见）先清掉，保证复制路径干净
        if target.exists() or target.is_symlink():
            target.unlink()
        shutil.copy2(source, target)

    size = _file_size(target)
    entry = _env_status_entry(wt_path)
    if entry is not None:
        target.unlink()
        return {
            "mode": "blocked-untracked",
            "path": target,
            "bytes": size,
            "status_entry": entry,
        }
    return {"mode": "linked" if linked else "copied", "path": target, "bytes": size}


def _report_env(result: dict) -> None:
    """打印 .env 供给结论：明确是链接还是复制，并点出复制不跟随主工作区更新"""
    mode = result["mode"]
    target = result["path"]
    size = result["bytes"]
    if mode == "linked":
        print(f"[env] .env 已提供（符号链接）: {target}")
        print(f"      ← {REPO_ROOT / ENV_NAME}（{size} 字节；自动跟随主工作区更新）")
        print("      自检: git status --short 未出现 .env（已被 .gitignore 忽略）")
    elif mode == "copied":
        print(f"[env] .env 已提供（复制）: {target}（{size} 字节）")
        print("      ⚠ 复制不会自动跟随主工作区更新：主工作区的 .env 变更后需重新创建或手工同步")
        print("      自检: git status --short 未出现 .env（已被 .gitignore 忽略）")
    elif mode == "skipped-exists":
        print(f"[env] .env 已存在，未覆盖: {target}（{size} 字节）")
    elif mode == "missing-source":
        print(f"[env] ⚠ 主工作区无 {result['path']}：本 worktree 内进程没有 LLM 配置，")
        print("      只能走离线响应（勿把“环境没配”误判成“功能不达标”）")
    else:  # blocked-untracked
        print(f"[env] ✗ .env 未被 .gitignore 忽略（git status 出现: {result['status_entry']}）")
        print("      已回滚删除，拒绝把 .env 带进 git；请先在 .gitignore 加入 .env 再重新创建")


def cmd_create(args: argparse.Namespace) -> None:
    session_id = args.id or _next_id()
    base = args.base
    wt_path = WORKTREES_DIR / session_id
    branch = f"{session_id}/main"

    r = git("rev-parse", "--verify", "--quiet", f"refs/heads/{base}")
    if r.returncode != 0:
        sys.exit(f"基准分支 {base!r} 不存在（可用 git branch 确认）")
    if wt_path.exists():
        sys.exit(f"worktree 已存在: {wt_path}（先清理: cleanup {session_id}）")

    r = git("worktree", "add", str(wt_path), base, "-b", branch)
    if r.returncode != 0:
        sys.exit(f"worktree 创建失败:\n{r.stderr.strip()}")

    _ensure_gitignore()

    # 会话元数据写 git 内部目录（不入版本库）
    gitdir_session = REPO_ROOT / ".git" / "worktrees" / session_id
    gitdir_session.mkdir(parents=True, exist_ok=True)
    (gitdir_session / "session-config.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "branch_prefix": f"{session_id}/",
                "base": base,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    env_result = _provide_env(wt_path)
    print(f"[OK] 会话 {session_id} 就绪:")
    print(f"      worktree: {wt_path}")
    print(f"      初始分支: {branch}（基准 {base}）")
    print(f"      分支前缀: {session_id}/（本会话新分支必须用此前缀）")
    print(f"      操作入口: 在 worktree 目录内执行 git 命令，互不干扰")
    _report_env(env_result)
    if env_result["mode"] in ("missing-source", "blocked-untracked"):
        # worktree 本身已建好，但 .env 供给未达成 —— 用非零码（3）让自动化流程无法忽略，
        # 避免重演 S9-01 遗留 #6（缺 .env 走离线响应被误判成功能不达标）。
        print(
            f"[env] ✗ worktree 已创建，但 .env 未就绪（{env_result['mode']}）："
            f"本 worktree 内进程没有 LLM 配置（退出码 3）"
        )
        sys.exit(3)
    print(f"      开箱即用: .env 已由本脚本自动提供，新会话不必手工复制")


def cmd_list(args: argparse.Namespace) -> None:
    sessions = _existing_sessions()
    if not sessions:
        print("（无现有会话）")
        return
    for s in sessions:
        print(s)


def cmd_cleanup(args: argparse.Namespace) -> None:
    session_id = args.session
    _session_no(session_id)
    wt_path = WORKTREES_DIR / session_id

    # 仅删除本会话前缀下的分支，防误删其他会话
    r = git("for-each-ref", "refs/heads", "--format=%(refname:short)")
    branches = [b for b in r.stdout.splitlines() if b.startswith(f"{session_id}/")]

    if wt_path.exists():
        rr = git("worktree", "remove", str(wt_path), "--force")
        if rr.returncode != 0:
            sys.exit(f"worktree 移除失败:\n{rr.stderr.strip()}")
        print(f"[OK] 已移除 worktree {wt_path}")
    for b in branches:
        git("branch", "-D", b)
        print(f"[OK] 已删除分支 {b}")
    if not branches and not wt_path.exists():
        print(f"（会话 {session_id} 无残留）")


def cmd_guard(args: argparse.Namespace) -> None:
    """校验分支归属会话前缀（供会话内 commit/push 前调用）"""
    branch = args.branch
    prefix = branch.split("/", 1)[0]
    if "/" in branch and SESSION_RE.match(prefix):
        print(f"[OK] {branch} 属于会话 {prefix} 前缀")
        return
    sys.exit(f"分支 {branch!r} 不满足会话前缀约定（sN/<name>），拒绝操作")


def _next_id() -> str:
    nums = [_session_no(s) for s in _existing_sessions()]
    return f"s{max(nums) + 1 if nums else 1}"


def main() -> None:
    p = argparse.ArgumentParser(description="新会话隔离 worktree 创建/管理")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="创建新会话 worktree（默认子命令）")
    c.add_argument("--id", help="会话号（默认自动分配）")
    c.add_argument("--base", default="develop", help="基准分支（默认 develop）")
    c.set_defaults(func=cmd_create)

    l = sub.add_parser("list", help="列出所有会话")
    l.set_defaults(func=cmd_list)

    cl = sub.add_parser("cleanup", help="清理会话 worktree 与分支")
    cl.add_argument("session", help="会话号，如 s7")
    cl.set_defaults(func=cmd_cleanup)

    g = sub.add_parser("guard", help="校验分支归属会话前缀")
    g.add_argument("branch", help="分支名，如 s7/fix-x")
    g.set_defaults(func=cmd_guard)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
