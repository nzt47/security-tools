#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端演示的沙箱支持（TASK-S7-02；**不在 agent/repair 包内**）

【为什么这些函数放在脚本层，而不是 ``agent/repair/``】
    任务书 §一 边界 ① 要求「代码级断言无 push / 无 merge 调用」，且
    ``tests/unit/test_repair_no_push.py`` 会对 ``agent/repair/`` **全包**做源码扫描。
    演示沙箱为了造出"可被弄坏的仓库"，需要 ``git init/add/commit``——这是**造数**，
    不是修复流程的一部分。把它放在生产包里会让那条扫描要么误报、要么被迫放宽。

    因此边界很清晰：
      - ``agent/repair/`` = 生产代码，**只读 git + 唯一本地分支写操作**；
      - ``scripts/`` = 演示与运维脚本，可以造数，但**绝不触碰交付工作区**。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Sequence


def purge_pycache(root: str) -> int:
    """删除目录树内所有 ``__pycache__``（返回删除个数）

    【为什么必须做】复制仓库时 ``__pycache__`` 会被排除，但**目标根里已有的**旧字节码
    不在排除范围内。旧 ``.pyc`` 会让新写入的模块导入到"上一版"——实测表现为 pytest
    ``collection failure``（看似"演示的 bug 变了"，实则是缓存串味）。
    """
    removed = 0
    for dirpath, dirnames, _files in os.walk(root):
        for name in list(dirnames):
            if name == "__pycache__":
                shutil.rmtree(os.path.join(dirpath, name), ignore_errors=True)
                dirnames.remove(name)
                removed += 1
    return removed


def git_commit_all(root: str, message: str) -> bool:
    """在给定目录内暂存并提交（**只操作该目录**；失败返回 False）"""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    for args in (["add", "-A"], ["commit", "-q", "-m", message]):
        proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env)
        if proc.returncode != 0:
            return False
    return True


def git_init_commit(root: str, *, message: str = "demo sandbox base") -> bool:
    """初始化 git 并提交一次（供沙箱使用）"""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    steps = (
        ["init", "-q"],
        ["config", "user.email", "repair-demo@local"],
        ["config", "user.name", "repair-demo"],
    )
    for args in steps:
        proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env)
        if proc.returncode != 0:
            return False
    return git_commit_all(root, message)


def build_sandbox_repo(source_root: str, target_root: str, *,
                       gitignore_extra: Sequence[str] = (".git/", "/data/repair/"),
                       empty_targets: Sequence[str] = (),
                       overwrite: bool = False) -> str:
    """把一个仓库复制成**可安全注入故障的演示沙箱**（并初始化为独立 git 仓库）

    【为什么演示要另建沙箱】任务书 §八 已知坑 2 明确禁止在本会话 worktree 里造数：
    演示需要一个"可以被弄坏"的仓库，而交付物所在的工作区不能被弄坏。做法是：

      1. 复用 ``agent.repair.verify.prepare_isolated_copy`` 的排除规则
         （不带 ``.git`` / venv / 缓存 / 产物）；
      2. 清理沙箱内残留 ``__pycache__``（见 :func:`purge_pycache`）；
      3. 在沙箱里 ``git init`` 并提交一次（**只影响沙箱**）——让定位器的只读 git
         证据链与产物分支（``repair/<date>-<slug>``）都有真实落点；
      4. ``empty_targets`` 指定的测试文件被清空，使注入的故障只影响要演示的路径。

    【为什么默认拒绝覆盖已存在的目标根】Windows 上被占用的目录 ``rmtree`` 会**静默
    失败**（``ignore_errors=True``），此时 ``shutil.move`` 会把新副本塞进已存在目录里
    （变成 ``<target>/<repo_name>/...``），后续一切路径都对不上——实测踩过。
    故对已存在的目标根**显式报错**；需要重跑请先删除或传 ``overwrite=True``。

    Raises:
        FileExistsError: 目标根已存在且未授权覆盖，或覆盖失败（被占用）。
        FileNotFoundError: 移动完成后目标根结构异常（疑似嵌套）。
    """
    from agent.repair.verify import prepare_isolated_copy

    target = os.path.abspath(target_root)
    if os.path.exists(target):
        if not overwrite:
            raise FileExistsError(
                f"沙箱根已存在：{target}（请先删除，或传 overwrite=True）")
        shutil.rmtree(target, ignore_errors=True)
        if os.path.exists(target):
            raise FileExistsError(f"沙箱根无法删除（可能被其他进程占用）：{target}")

    copy_root = prepare_isolated_copy(source_root)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    # 用 shutil.move 而非 os.replace：后者在 Windows 上对目录跨卷/已存在目标会抛
    # PermissionError（实测 WinError 5），而 shutil.move 会退化为 copytree + rmtree。
    shutil.move(copy_root, target)
    if not os.path.isdir(os.path.join(target, "agent")):
        raise FileNotFoundError(
            f"沙箱结构异常（{target} 下未见 agent/）——"
            f"可能是目标根被占用导致 move 嵌套")

    if gitignore_extra:
        path = os.path.join(target, ".gitignore")
        existing = ""
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                existing = fh.read()
        addition = [line for line in gitignore_extra if line not in existing]
        if addition:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(existing.rstrip() + "\n\n" + "\n".join(addition) + "\n")

    for rel in empty_targets:
        full = os.path.join(target, str(rel).replace("/", os.sep))
        if os.path.isfile(full):
            with open(full, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("# 演示沙箱：本文件在沙箱内被清空（用于隔离演示路径）\n")

    purge_pycache(target)
    git_init_commit(target)
    return target


__all__ = ["purge_pycache", "git_commit_all", "git_init_commit", "build_sandbox_repo"]
