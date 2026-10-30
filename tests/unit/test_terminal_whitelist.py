# -*- coding: utf-8 -*-
"""命令执行（白名单）—— 2026-10-31。

锁四件事：
  ① 只能选白名单里的 command_id（未知 id 一律 400，且回 allowed 清单）；
  ② 参数只做**数值收敛**（越界夹紧、非法回默认），用户输入永不进入命令名；
  ③ argv 全部来自服务端常量表（`shell=False`，不拼字符串）；
  ④ 真命令有真实输出（git status / python --version）。
"""
from __future__ import annotations

import shutil

import pytest

from plugins import system_tools as st


def test_unknown_command_is_rejected():
    r = st._terminal_run("rm -rf /", {})
    assert r["ok"] is False and r["status"] == 400
    assert "allowed" in r and "git-status" in r["allowed"]


def test_clamp_int_bounds():
    assert st._clamp_int(9999, 1, 50, 10) == 50
    assert st._clamp_int(0, 1, 50, 10) == 1
    assert st._clamp_int("abc", 1, 50, 10) == 10
    assert st._clamp_int(None, 1, 50, 10) == 10
    assert st._clamp_int("7", 1, 50, 10) == 7


def test_git_log_param_is_clamped_into_argv():
    r = st._terminal_run("git-log", {"n": 9999})
    assert r["argv"][:4] == ["git", "log", "--oneline", "-n"]
    assert r["argv"][4] == "50", "越界参数必须夹紧到上界"


@pytest.mark.skipif(shutil.which("git") is None, reason="环境无 git")
def test_git_status_executes_for_real():
    r = st._terminal_run("git-status", {})
    assert r["ok"] is True
    assert r["argv"] == ["git", "status", "--short", "--branch"]
    assert r["exit_code"] == 0, (r.get("stdout"), r.get("stderr"))
    assert isinstance(r["duration_ms"], float)


def test_python_version_executes_for_real():
    r = st._terminal_run("python-version", {})
    assert r["ok"] is True and r["exit_code"] == 0
    assert "Python" in (r["stdout"] + r["stderr"])


def test_every_spec_builds_constant_argv():
    specs = st._terminal_specs()
    assert len(specs) >= 4
    for cid, (label, desc, params, builder) in specs.items():
        assert isinstance(label, str) and label
        assert isinstance(desc, str) and desc
        argv = builder({"n": 3})
        assert isinstance(argv, list) and argv
        assert all(isinstance(a, str) for a in argv), cid
        # 参数只允许出现在数值位（夹紧后仍是纯数字）
        for p in params:
            assert p["name"] == "n" and p["type"] == "int"
