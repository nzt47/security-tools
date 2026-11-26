# -*- coding: utf-8 -*-
"""E0-4② 子进程硬超时：超时必须杀**整棵进程树**（2026-11-27）

缺口：沙箱/子进程工具超时走的是 proc.kill()，只终结直接子进程；
"cmd /c python -m pytest" 这类命令的孙进程会继续跑并占住文件锁（本机实测过）。

本文件钉住：
  1. kill_tree 能同时收掉子进程与孙进程（真起进程，不 mock）；
  2. kill_tree 幂等：对已退出的 pid 再调一次不抛、不误报；
  3. 非法 pid / 无法清理时**如实返回 False**（调用方据此退回 proc.kill()）；
  4. run_sandboxed 超时后孙进程也不在了（集成；用子进程写 pid 文件证明）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent import proc_tree


def _spawn_tree(pid_file: Path) -> subprocess.Popen:
    """起一个「父 → 孙」进程链，孙的 pid 写进 pid_file（跨平台）"""
    helper = pid_file.parent / "spawn_tree_helper.py"
    helper.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "open(sys.argv[1], 'w').write(str(child.pid))\n"
        "time.sleep(120)\n",
        encoding="utf-8")
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen([sys.executable, str(helper), str(pid_file)], **kwargs)


def _wait_pid_file(pid_file: Path, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pid_file.exists():
            try:
                text = pid_file.read_text(encoding="utf-8").strip()
            except OSError:
                text = ""
            if text.isdigit():
                return int(text)
        time.sleep(0.1)
    return None


def _wait_dead(pid: int, timeout=15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not proc_tree.alive(pid):
            return True
        time.sleep(0.1)
    return False


expect_alive = pytest.mark.skipif(
    os.name == "nt", reason="Windows 上 alive() 依赖 psutil，CI 的 windows 腿不保证装")


class TestKillTree:
    def test_杀整棵树(self, tmp_path):
        pid_file = tmp_path / "child.pid"
        parent = _spawn_tree(pid_file)
        try:
            grandchild = _wait_pid_file(pid_file)
            assert grandchild, "用例前提：孙进程必须先把 pid 写出来"
            assert proc_tree.alive(parent.pid)
            assert proc_tree.kill_tree(parent.pid) is True
            assert _wait_dead(parent.pid), "直接子进程必须退出"
            assert _wait_dead(grandchild), "孙进程也必须退出（这就是本片要修的那条）"
        finally:
            for pid in (parent.pid,):
                try:
                    proc_tree.kill_tree(pid)
                except Exception:
                    pass

    def test_幂等_对已退出的pid不抛(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait(timeout=30)
        assert proc_tree.kill_tree(proc.pid) in (True, False)   # 只要求不抛
        assert proc_tree.alive(proc.pid) is False

    def test_非法pid返回False(self):
        # 0/-1/非数字都不是合法 pid；注意**不存在的正 pid** 可能返回 True
        # （taskkill /T 对已退出进程回 128 = "已不存在"，按幂等算清理到位）
        for bad in (0, -1, None, "abc", ""):
            assert proc_tree.kill_tree(bad) is False

    def test_alive对非法pid为False(self):
        assert proc_tree.alive(0) is False
        assert proc_tree.alive("x") is False


class TestSandboxTimeoutKillsTree:
    def test_超时后孙进程也被收掉(self, tmp_path):
        """集成：run_sandboxed 超时 ⇒ 父与孙都不在了（否则会占文件锁）"""
        from agent.subagent.sandbox import SandboxResourceLimits, get_tool_sandbox

        pid_file = tmp_path / "child.pid"
        # 【为什么落成脚本文件而不是 -c】沙箱的命令校验会拦截命令行里带 subprocess 的
        #   "-c" 形态（"Python subprocess 执行任意命令"）；这里要测的是**超时清理**，
        #   不是危险命令拦截，所以把同样的逻辑放进脚本文件再按路径执行。
        helper = tmp_path / "spawn_tree.py"
        helper.write_text(
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "open(sys.argv[1], 'w').write(str(child.pid))\n"
            "time.sleep(120)\n",
            encoding="utf-8")
        limits = SandboxResourceLimits(timeout_s=6.0, max_output_bytes=20000)
        result = get_tool_sandbox().run_sandboxed(
            [sys.executable, str(helper), str(pid_file)],
            limits=limits,
            allowed_paths=[str(tmp_path)],   # 指定 cwd 时必须同时给允许根（沙箱默认拒绝）
            cwd=str(tmp_path),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        assert result.timed_out is True, "用例前提：必须真的触发超时分支"
        grandchild = _wait_pid_file(pid_file, timeout=5.0)
        assert grandchild, "用例前提：孙进程必须已启动"
        assert _wait_dead(grandchild), "超时后孙进程必须被清理（否则会占住文件锁）"

