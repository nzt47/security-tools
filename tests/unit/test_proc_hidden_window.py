# -*- coding: utf-8 -*-
"""「子进程不许弹黑窗」的守卫（2026-11-13，Owner：启动与定时任务别再弹黑窗）。

【现场取证（为什么不是凭猜测换 pythonw）】
    本机后端由 deploy_workbench / watchdog 用 DETACHED_PROCESS 拉起 ⇒ **自身没有控制台**。
    在这种父进程里，`subprocess.run(["gh", ...])` 会让 Windows 给子进程**新分配一个控制台**：
    实测（AppData\Local\Temp\ys_console_probe）子进程 `GetConsoleWindow()` 非 0、
    `IsWindowVisible()=true`、窗口类 `ConsoleWindowClass`；同一子进程加
    `CREATE_NO_WINDOW` 后 `GetConsoleWindow()==0`、枚举不到任何可见控制台窗口。
    ⇒ 真因是**子进程被分配控制台**，不是"启动器写了 .bat"这一个点；换 pythonw 只会把
    stdout/stderr 一起吞掉，且对"后端自己 spawn 的 gh/python 维护作业"毫无作用。

【本文件钉住三件事】
1. 叶子模块 `agent/utils/proc.py` 的契约：非 Windows **不注入**任何参数（CI 是 Linux，
   POSIX 上 creationflags != 0 会 ValueError）；Windows 注入 CREATE_NO_WINDOW + 隐藏 STARTUPINFO；
   调用方显式传的 creationflags/startupinfo 优先（DETACHED_PROCESS 等场景不被覆盖）。
2. run/popen/check_output 都**透传**到 subprocess 的同名函数（不是复制一份实现）。
3. **全仓哨兵**：`agent/` 下任何新增的裸 `subprocess.run/Popen/check_output/call/check_call`
   都会让本文件失败 —— 唯一允许的例外是 `agent/utils/proc.py` 本身。
"""
from __future__ import annotations

import ast
import pathlib
import subprocess

import pytest

from agent.utils import proc

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENT_ROOT = REPO_ROOT / "agent"

#: 唯一允许直接调用 subprocess 启动函数的文件（叶子模块，别处一律走它）
ALLOWED_FILES = {"agent/utils/proc.py"}

#: 运行期工具目录（data/tool_definitions 声明的本机运行时实现，不入库 ⇒ 不参与哨兵）
RUNTIME_ONLY_DIRS = {"agent/tools/custom"}

_SPAWNERS = {"run", "Popen", "check_output", "call", "check_call"}


def _iter_agent_py():
    for path in sorted(AGENT_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(rel.startswith(d) for d in RUNTIME_ONLY_DIRS):
            continue
        yield rel, path


class TestHiddenKwargs:
    def test_non_windows_injects_nothing(self, monkeypatch):
        monkeypatch.setattr(proc.sys, "platform", "linux")
        assert proc.hidden_kwargs() == {}

    def test_windows_injects_create_no_window_and_hidden_startupinfo(self, monkeypatch):
        monkeypatch.setattr(proc.sys, "platform", "win32")
        kwargs = proc.hidden_kwargs()
        assert kwargs["creationflags"] == proc.CREATE_NO_WINDOW
        # 0x08000000 是 CREATE_NO_WINDOW 的 Windows 常量；写死是为了让"标志被改成别的"也红
        assert proc.CREATE_NO_WINDOW == 0x08000000
        startupinfo = kwargs["startupinfo"]
        assert startupinfo.dwFlags & proc._STARTF_USESHOWWINDOW
        assert startupinfo.wShowWindow == proc._SW_HIDE

    def test_windows_returns_fresh_startupinfo_each_call(self, monkeypatch):
        monkeypatch.setattr(proc.sys, "platform", "win32")
        first = proc.hidden_kwargs()["startupinfo"]
        second = proc.hidden_kwargs()["startupinfo"]
        assert first is not second, "startupinfo 可变，多个子进程共用会互相踩"

    def test_windows_only_on_win32_not_on_cygwin_style_names(self, monkeypatch):
        for platform in ("linux", "darwin", "freebsd13", "emscripten"):
            monkeypatch.setattr(proc.sys, "platform", platform)
            assert proc.hidden_kwargs() == {}, platform


class TestCallerWins:
    def test_caller_creationflags_not_overridden(self, monkeypatch):
        monkeypatch.setattr(proc.sys, "platform", "win32")
        merged = proc._with_hidden({"creationflags": 0x00000008})
        assert merged["creationflags"] == 0x00000008, "DETACHED_PROCESS 等调用方语义必须保留"

    def test_caller_startupinfo_not_overridden(self, monkeypatch):
        monkeypatch.setattr(proc.sys, "platform", "win32")
        sentinel = object()
        merged = proc._with_hidden({"startupinfo": sentinel})
        assert merged["startupinfo"] is sentinel


class TestPassThrough:
    """run/popen/check_output 必须真的调用同名 subprocess 函数（不另写一份）。"""

    def test_run_forwards(self, monkeypatch):
        calls = {}

        def fake_run(argv, **kwargs):
            calls["argv"] = argv
            calls["kwargs"] = kwargs
            return "sentinel-run"

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(proc.sys, "platform", "win32")
        assert proc.run(["gh", "run", "list"], capture_output=True) == "sentinel-run"
        assert calls["argv"] == ["gh", "run", "list"]
        assert calls["kwargs"]["capture_output"] is True
        assert calls["kwargs"]["creationflags"] == proc.CREATE_NO_WINDOW

    def test_run_forwards_nothing_extra_off_windows(self, monkeypatch):
        calls = {}

        def fake_run(argv, **kwargs):
            calls["kwargs"] = kwargs
            return 0

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(proc.sys, "platform", "linux")
        assert proc.run(["git", "status"]) == 0
        assert calls["kwargs"] == {}, "Linux/CI 上必须与改造前逐字相同（不传 creationflags）"

    def test_popen_forwards(self, monkeypatch):
        calls = {}

        def fake_popen(argv, **kwargs):
            calls["argv"] = argv
            calls["kwargs"] = kwargs
            return "sentinel-popen"

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(proc.sys, "platform", "win32")
        assert proc.popen(["npm", "install"]) == "sentinel-popen"
        assert calls["kwargs"]["creationflags"] == proc.CREATE_NO_WINDOW

    def test_check_output_forwards(self, monkeypatch):
        calls = {}

        def fake_check_output(argv, **kwargs):
            calls["kwargs"] = kwargs
            return b"ok"

        monkeypatch.setattr(subprocess, "check_output", fake_check_output)
        monkeypatch.setattr(proc.sys, "platform", "linux")
        assert proc.check_output(["git", "rev-parse", "HEAD"]) == b"ok"
        assert calls["kwargs"] == {}


class TestNoBareSpawners:
    """全仓哨兵：agent/ 里不许再出现裸 subprocess.run/Popen/...（否则又会闪窗）。"""

    def test_no_bare_subprocess_spawners(self):
        offenders = []
        scanned = 0
        for rel, path in _iter_agent_py():
            scanned += 1
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:  # pragma: no cover - 有语法错误的模块另有门禁负责
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                        and func.value.id == "subprocess" and func.attr in _SPAWNERS):
                    continue
                keywords = {kw.arg for kw in node.keywords if kw.arg}
                # 显式自己带 creationflags 的调用（如 env_config_manager 的 icacls）不算裸调用
                if "creationflags" in keywords:
                    continue
                if rel in ALLOWED_FILES:
                    continue
                offenders.append("%s:%d subprocess.%s()" % (rel, node.lineno, func.attr))
        assert scanned > 50, "agent/ 扫描面异常（应远超 50 个 .py）"
        assert offenders == [], "以下调用点会弹黑窗，请改用 agent.utils.proc：\n" + "\n".join(offenders)

    def test_sentinel_sees_a_known_good_migrated_module(self):
        """反证：被迁移过的模块必须真的走 proc（否则哨兵可能"什么都没扫到"仍绿）。"""
        src = (REPO_ROOT / "agent" / "monitoring" / "ci_status.py").read_text(encoding="utf-8")
        assert "_proc.run(" in src
        assert "subprocess.run(" not in src
