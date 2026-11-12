# -*- coding: utf-8 -*-
"""「子进程不许弹黑窗」的守卫（2026-11-13，Owner：启动与定时任务别再弹黑窗）。

【现场取证（为什么不是凭猜测换 pythonw）】
    本机后端由 deploy_workbench / watchdog 用 DETACHED_PROCESS 拉起 ⇒ **自身没有控制台**。
    在这种父进程里，`subprocess.run(["gh", ...])` 会让 Windows 给子进程**新分配一个控制台**：
    实测子进程 `GetConsoleWindow()` 非 0、`IsWindowVisible()=true`、窗口类 `ConsoleWindowClass`；
    同一子进程加 `CREATE_NO_WINDOW` 后 `GetConsoleWindow()==0`、枚举不到任何可见控制台窗口。
    ⇒ 真因是**子进程被分配控制台**，换 pythonw 只会把 stdout/stderr 一起吞掉。

【本文件钉住三件事】
1. `agent/utils/proc.py::hidden_kwargs()` 的契约：非 Windows 返回空字典（CI 是 Linux，
   POSIX 上 `creationflags != 0` 会 ValueError）；Windows 返回 `CREATE_NO_WINDOW` + 隐藏
   `STARTUPINFO`；每次返回新的 startupinfo（可变结构不能被多个子进程共用）。
2. **形态口径**：调用点必须是 `subprocess.<spawn>(..., **_proc.hidden_kwargs(), ...)`，
   **不要**封装成 `proc.run()` —— 本仓多处用例替换模块属性 `mod.subprocess` 打桩，
   换成包装函数会把桩绕过（2026-11-13 实测 5 个分片 30+ 条用例因此变红）。
3. **全仓哨兵**：`agent/` 下每个 `subprocess.run/Popen/check_output/call/check_call`
   要么显式给 `creationflags`/`startupinfo`，要么展开 `**_proc.hidden_kwargs()`，
   否则本文件失败。唯一不需要 flag 的例外是 stdlib-only 的隔离执行体（它就地取常量）。
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

from agent.utils import proc

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENT_ROOT = REPO_ROOT / "agent"

#: 运行期工具目录（data/tool_definitions 声明的本机运行时实现，不入库 ⇒ 不参与哨兵）
RUNTIME_ONLY_DIRS = {"agent/tools/custom"}
#: 就地取常量的模块（stdlib-only 契约：不能 import agent.*，故不展开 hidden_kwargs）
INLINE_FLAG_FILES = {"agent/digestion/isolation_worker.py"}

_SPAWNERS = {"run", "Popen", "check_output", "call", "check_call"}


def _iter_agent_py():
    for path in sorted(AGENT_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(rel.startswith(d) for d in RUNTIME_ONLY_DIRS):
            continue
        yield rel, path


class _FakeStartupInfo:
    """subprocess.STARTUPINFO 的替身（Linux 上不存在；用来验证注入内容）。"""

    dwFlags = 0
    wShowWindow = 1


@pytest.fixture
def fake_windows(monkeypatch):
    """把平台假装成 win32，并在缺失时补上 STARTUPINFO（CI 是 Linux）。"""
    monkeypatch.setattr(proc.sys, "platform", "win32")
    if not hasattr(subprocess, "STARTUPINFO"):
        monkeypatch.setattr(subprocess, "STARTUPINFO", _FakeStartupInfo, raising=False)
    return proc


class TestHiddenKwargs:
    def test_non_windows_injects_nothing(self, monkeypatch):
        for platform in ("linux", "darwin", "freebsd13", "emscripten"):
            monkeypatch.setattr(proc.sys, "platform", platform)
            assert proc.hidden_kwargs() == {}, platform

    def test_windows_injects_create_no_window_and_hidden_startupinfo(self, fake_windows):
        kwargs = fake_windows.hidden_kwargs()
        assert kwargs["creationflags"] == proc.CREATE_NO_WINDOW
        # 0x08000000 是 Windows 的 CREATE_NO_WINDOW；写死是为了让"标志被改成别的"也红
        assert proc.CREATE_NO_WINDOW == 0x08000000
        startupinfo = kwargs["startupinfo"]
        assert startupinfo.dwFlags & proc._STARTF_USESHOWWINDOW
        assert startupinfo.wShowWindow == proc._SW_HIDE

    def test_windows_returns_fresh_startupinfo_each_call(self, fake_windows):
        first = fake_windows.hidden_kwargs()["startupinfo"]
        second = fake_windows.hidden_kwargs()["startupinfo"]
        assert first is not second, "startupinfo 可变，多个子进程共用会互相踩"

    def test_kwargs_usable_as_call_arguments_off_windows(self, monkeypatch):
        """Linux/CI 上展开后与改造前逐字相同（不新增任何关键字）。"""
        calls = {}

        def fake_run(argv, **kwargs):
            calls["argv"], calls["kwargs"] = argv, kwargs
            return "ok"

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(proc.sys, "platform", "linux")
        assert subprocess.run(["git", "status"], **proc.hidden_kwargs(), capture_output=True) == "ok"
        assert calls["kwargs"] == {"capture_output": True}

    def test_kwargs_usable_as_call_arguments_on_windows(self, fake_windows, monkeypatch):
        calls = {}

        def fake_run(argv, **kwargs):
            calls["kwargs"] = kwargs
            return "ok"

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert subprocess.run(["gh", "run", "list"], **fake_windows.hidden_kwargs()) == "ok"
        assert calls["kwargs"]["creationflags"] == fake_windows.CREATE_NO_WINDOW
        assert "startupinfo" in calls["kwargs"]


class TestNoBareSpawners:
    """全仓哨兵：agent/ 里每个 spawn 都要带 flag（否则又会闪窗）。"""

    @staticmethod
    def _has_hidden_kwargs(node) -> bool:
        for kw in node.keywords:
            if kw.arg is not None:
                continue  # arg=None 才是 **kwargs 展开
            value = kw.value
            if isinstance(value, ast.Call):
                func = value.func
                if isinstance(func, ast.Attribute) and func.attr == "hidden_kwargs":
                    return True
            if isinstance(value, ast.Name) and value.id == "hidden_kwargs":
                return True
        return False

    def test_no_bare_subprocess_spawners(self):
        offenders = []
        scanned = 0
        for rel, path in _iter_agent_py():
            scanned += 1
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:  # pragma: no cover - 语法错误的模块另有门禁
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                        and func.value.id == "subprocess" and func.attr in _SPAWNERS):
                    continue
                keywords = {kw.arg for kw in node.keywords if kw.arg}
                if "creationflags" in keywords or "startupinfo" in keywords:
                    continue
                if rel in INLINE_FLAG_FILES:
                    continue
                if self._has_hidden_kwargs(node):
                    continue
                offenders.append("%s:%d subprocess.%s()" % (rel, node.lineno, func.attr))
        assert scanned > 50, "agent/ 扫描面异常（应远超 50 个 .py）"
        assert offenders == [], (
            "以下调用点会在 Windows 上弹黑窗，请加 **_proc.hidden_kwargs()：\n"
            + "\n".join(offenders)
        )

    def test_sentinel_sees_known_good_sites(self):
        """反证：迁移过的模块必须真的带 flag（否则哨兵可能"什么都没扫到"仍绿）。"""
        for rel in ("agent/monitoring/ci_status.py", "agent/maintenance/zero_token_jobs.py",
                    "agent/tools/software_tools.py"):
            src = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert "**_proc.hidden_kwargs()" in src, rel

    def test_isolation_worker_stays_stdlib_only(self):
        """隔离执行体不能 import agent.*（容器里只挂载它自己 + 标准库）。"""
        src = (REPO_ROOT / "agent" / "digestion" / "isolation_worker.py").read_text(encoding="utf-8")
        assert "_NO_WINDOW" in src, "隔离执行体必须就地取 CREATE_NO_WINDOW 常量"
        assert "from agent" not in src and "import agent" not in src
