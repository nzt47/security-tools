# -*- coding: utf-8 -*-
"""E3-1 持久终端（PTY）：会话管理 + 安全闸门 + 工具分派（2026-11-27）

为什么默认关：raw PTY 等于把一条完整交互 shell 交给模型（绕过一次性命令的模式匹配，也绕过
权限预设对子进程的限制）。所以本文件同时钉两件事：**能力能用**（开/发/读/列/关 + 环形缓冲）,
  **安全闸门在**（默认关、受限档拒绝、会话数上限）。

平台：Windows 用 pywinpty（CI 未必装 ⇒ 相关用例 skip），POSIX 用标准库 pty。
"""
from __future__ import annotations

import sys
import time

import pytest

from agent import pty_session


def _backend() -> str:
    """当前平台可用的 PTY 后端；不可用返回空串（用例 skip）"""
    if sys.platform == "win32":
        try:
            import winpty  # noqa: F401
            return "winpty"
        except Exception:  # noqa: BLE001
            return ""
    try:
        import pty  # noqa: F401
        return "pty"
    except Exception:  # noqa: BLE001
        return ""


BACKEND = _backend()
needs_pty = pytest.mark.skipif(not BACKEND, reason="本机没有可用的 PTY 后端（Windows 需 pywinpty）")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("CP_PERMISSION_PRESET", raising=False)   # 默认 full-access
    monkeypatch.delenv("CP_TERMINAL_ENABLED", raising=False)
    yield
    pty_session.close_all()


def _echo_script(tmp_path):
    """一个"读一行回一行"的小程序：比 REPL 稳定（不依赖提示符格式）"""
    path = tmp_path / "echo_lines.py"
    path.write_text(
        "import sys\n"
        "print('READY', flush=True)\n"
        "for line in sys.stdin:\n"
        "    print('ECHO:' + line.strip(), flush=True)\n",
        encoding="utf-8")
    return path


def _wait_for(session_id, needle, timeout=15.0):
    """轮询 read 直到出现 needle（PTY 输出是异步的，不能睡固定时间）"""
    deadline = time.time() + timeout
    collected = ""
    offset = 0
    while time.time() < deadline:
        got = pty_session.read(session_id, offset=offset, limit=20000)
        if got.get("ok"):
            collected += got.get("chunk") or ""
            offset = got.get("next_offset") if got.get("next_offset") is not None else got.get("offset", 0) + len(got.get("chunk") or "")
            if needle in collected:
                return collected
        time.sleep(0.1)
    return collected


class TestGuards:
    def test_默认关_开不起来(self):
        got = pty_session.open_session()
        assert got["ok"] is False
        assert "CP_TERMINAL_ENABLED" in got["error"]

    def test_受限档一律拒绝(self, monkeypatch):
        monkeypatch.setenv("CP_TERMINAL_ENABLED", "1")
        import agent.sandbox as sandbox
        monkeypatch.setattr(sandbox, "is_confined", lambda *a, **k: True)
        got = pty_session.open_session()
        assert got["ok"] is False and got.get("blocked") is True
        assert "受限档" in got["error"]


@needs_pty
class TestSessions:
    def test_开会话_发一行_读回回显(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CP_TERMINAL_ENABLED", "1")
        script = _echo_script(tmp_path)
        got = pty_session.open_session(
            command = '"%s" "%s"' % (sys.executable, script), cwd=str(tmp_path))
        assert got["ok"] is True, got
        sid = got["id"]
        assert "READY" in _wait_for(sid, "READY")
        # 直接调 pty_session.send 不经过工具的 newline 逻辑 ⇒ 这里自己给回车（PTY 的 Enter）
        assert pty_session.send(sid, "hello\r")["ok"] is True
        assert "ECHO:hello" in _wait_for(sid, "ECHO:hello")

    def test_list_能看到会话(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CP_TERMINAL_ENABLED", "1")
        script = _echo_script(tmp_path)
        sid = pty_session.open_session(command='"%s" "%s"' % (sys.executable, script),
                                       cwd=str(tmp_path))["id"]
        listed = pty_session.list_sessions()
        assert listed["ok"] is True
        assert [s["id"] for s in listed["sessions"]] == [sid]
        assert listed["sessions"][0]["alive"] is True

    def test_关掉后读不到(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CP_TERMINAL_ENABLED", "1")
        script = _echo_script(tmp_path)
        sid = pty_session.open_session(command='"%s" "%s"' % (sys.executable, script),
                                       cwd=str(tmp_path))["id"]
        assert pty_session.close(sid)["ok"] is True
        assert pty_session.read(sid)["ok"] is False
        assert pty_session.close(sid)["ok"] is False        # 幂等：再关一次只回错误

    def test_会话数上限(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CP_TERMINAL_ENABLED", "1")
        monkeypatch.setattr(pty_session, "max_sessions", lambda: 1)
        script = _echo_script(tmp_path)
        cmd = '"%s" "%s"' % (sys.executable, script)
        assert pty_session.open_session(command=cmd, cwd=str(tmp_path))["ok"] is True
        second = pty_session.open_session(command=cmd, cwd=str(tmp_path))
        assert second["ok"] is False
        assert "上限" in second["error"]

    def test_缓冲上限_裁掉的部分如实告知(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CP_TERMINAL_ENABLED", "1")
        monkeypatch.setattr(pty_session, "buffer_chars", lambda: 4096)
        script = _echo_script(tmp_path)
        sid = pty_session.open_session(command='"%s" "%s"' % (sys.executable, script),
                                       cwd=str(tmp_path))["id"]
        _wait_for(sid, "READY")
        for i in range(200):
            pty_session.send(sid, "x" * 50)
        _wait_for(sid, "ECHO:xxxx", timeout=10.0)
        got = pty_session.read(sid, offset=0, limit=100)
        assert got["ok"] is True
        assert got["total_chars"] > 4096
        assert got["trimmed"] is True and got["trimmed_from"] > 0
        assert got["offset"] == got["trimmed_from"]

    def test_signal_interrupt_不抛(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CP_TERMINAL_ENABLED", "1")
        script = _echo_script(tmp_path)
        sid = pty_session.open_session(command='"%s" "%s"' % (sys.executable, script),
                                       cwd=str(tmp_path))["id"]
        assert pty_session.signal(sid, "interrupt")["ok"] is True
        assert pty_session.signal(sid, "nonsense")["ok"] is False


def _handler():
    """直接取注册表里的 handler：绕过 T.call 的闸门/限流（这里测的是分派与安全文案）"""
    from agent.tools.terminal_tools import register_all
    register_all()
    from agent import tools as T
    return T._registry["terminal"]["handler"]


class TestToolDispatch:
    def test_缺id报可执行提示(self):
        got = _handler()(action="read")
        assert got["ok"] is False and "id" in got["error"]

    def test_未知action(self):
        got = _handler()(action="explode")
        assert got["ok"] is False and "action" in got["error"]

    def test_默认关时工具也开不起来(self):
        got = _handler()(action="open")
        assert got["ok"] is False and "CP_TERMINAL_ENABLED" in got["error"]

    def test_send默认补回车(self, monkeypatch):
        """newline 默认 true 必须补 \r（PTY 的 Enter），而不是 \n"""
        from agent import pty_session as ps
        seen = {}

        def fake_send(sid, text):
            seen["text"] = text
            return {"ok": True, "id": sid}

        monkeypatch.setattr(ps, "send", fake_send)
        assert _handler()(action="send", id="t-1", text="hello")["ok"] is True
        assert seen["text"] == "hello\r"
        assert _handler()(action="send", id="t-1", text="hi", newline=False)["ok"] is True
        assert seen["text"] == "hi"

