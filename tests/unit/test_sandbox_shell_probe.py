# -*- coding: utf-8 -*-
"""E0-1① 受限档 shell 可用性探针（2026-11-27）

背景：受限令牌下 WSL bash 走 LxssManager、Git Bash 建 MSYS2 共享内存段，两者都被 pass-2
拒绝 ⇒ 切档（workspace-write）前必须先知道**哪几个 shell 还能用**，否则切完才发现
「带 unix 特征的命令全线失败」，那是能力回退而不是安全收益。

本文件钉住探针的输出契约：三个 shell 都有结论、失败只记类名与领域 message、
可用/不可用两份清单互斥且自洽。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def grant_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "sandbox_grant.py"
    spec = importlib.util.spec_from_file_location("sandbox_grant_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestProbeShell:
    def test_三个shell都有结论(self, grant_script, monkeypatch):
        from agent.sandbox import shell_run

        monkeypatch.setattr(shell_run, "run_captured",
                            lambda argv, cwd, timeout, preset=None: {"ok": True, "exit_code": 0})
        got = grant_script.probe_shell()
        assert set(got["shells"]) == {"cmd", "powershell", "bash"}
        assert got["available"] == ["bash", "cmd", "powershell"]
        assert got["unavailable"] == []
        assert got["preset_probed"] == "workspace-write"
        assert "切档前" in got["note"]

    def test_不可用的shell进unavailable(self, grant_script, monkeypatch):
        from agent.sandbox import shell_run

        def fake(argv, cwd, timeout, preset=None):
            if argv[0] == "bash":
                return {"ok": False, "exit_code": 1, "stderr": "couldn't create shared memory"}
            return {"ok": True, "exit_code": 0}

        monkeypatch.setattr(shell_run, "run_captured", fake)
        got = grant_script.probe_shell()
        assert got["available"] == ["cmd", "powershell"]
        assert got["unavailable"] == ["bash"]
        assert "shared memory" in got["shells"]["bash"]["stderr_head"]

    def test_抛异常只影响那一个shell(self, grant_script, monkeypatch):
        from agent.sandbox import shell_run

        class _SandboxNotReady(Exception):
            def __init__(self, message):
                super().__init__(message)
                self.message = message

        def fake(argv, cwd, timeout, preset=None):
            if argv[0] == "powershell":
                raise _SandboxNotReady("围栏不可用（未授权）")
            return {"ok": True, "exit_code": 0}

        monkeypatch.setattr(shell_run, "run_captured", fake)
        got = grant_script.probe_shell()
        assert got["shells"]["powershell"]["ok"] is False
        assert got["shells"]["powershell"]["error_class"] == "_SandboxNotReady"
        assert "未授权" in got["shells"]["powershell"]["message"]
        assert "cmd" in got["available"] and "bash" in got["available"]

    def test_可用与不可用清单互斥且覆盖全部(self, grant_script, monkeypatch):
        from agent.sandbox import shell_run

        monkeypatch.setattr(shell_run, "run_captured",
                            lambda *a, **k: {"ok": False, "exit_code": 1})
        got = grant_script.probe_shell()
        assert got["available"] == []
        assert set(got["unavailable"]) == set(got["shells"])

