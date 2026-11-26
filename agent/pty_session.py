"""持久终端会话（E3-1）：**真的 TTY**，不是"再起一个 shell 命令"。

【与 shell_execute / 后台任务的分工】
  · `shell_execute`：一次性命令，拿退出码与合并输出（无 TTY）；
  · 后台任务（`submit_background_shell` + `/api/background/tasks`）：脱离请求长跑，但没有交互 stdin；
  · 本模块：**带伪终端**的常驻会话 —— REPL、交互式提示、需要 Ctrl-C 的程序。三者互补，不互相替代。

【为什么默认关 + 受限档一律拒绝】raw PTY 等于把一条完整的交互 shell 交给模型：它绕过
`shell_execute` 的命令沙箱模式匹配，也绕过权限预设对子进程的限制。所以：
  · `CP_TERMINAL_ENABLED` 默认 **0**（未显式开启就不存在这个能力）；
  · 权限预设不是 `full-access` 时**一律拒绝**（fail-closed：宁可没有终端，也不给一条越狱通道）；
  · 每次 open / send / signal 都记审计（只记命令摘要与长度，不记原文）。

【平台】Windows 走 pywinpty 的 ConPTY（`PtyProcess`）；POSIX 走标准库 `pty.openpty()`。
pywinpty 缺失时**不静默降级到管道**（管道不是 TTY，REPL 行为会变），而是回可执行提示。

【边界】会话存进程内存（重启即失）；输出缓冲是有上限的环形窗口（默认 200k 字符），
被裁掉的部分用 `trimmed_from` 如实告知，不假装读到了。空闲超过 `CP_TERMINAL_IDLE_SECONDS`
自动关闭（避免忘了关的会话一直占资源）。
"""
from __future__ import annotations

import hashlib
import logging
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ENV_TERMINAL_ENABLED = "CP_TERMINAL_ENABLED"
ENV_TERMINAL_MAX_SESSIONS = "CP_TERMINAL_MAX_SESSIONS"
ENV_TERMINAL_BUFFER_CHARS = "CP_TERMINAL_BUFFER_CHARS"
ENV_TERMINAL_IDLE_SECONDS = "CP_TERMINAL_IDLE_SECONDS"

_TRUE = ("1", "true", "yes", "on")
_DEFAULT_MAX_SESSIONS = 4
_DEFAULT_BUFFER_CHARS = 200_000
_DEFAULT_IDLE_SECONDS = 1800.0
_READ_CHUNK = 4096
_READ_LIMIT_MAX = 60_000
_READ_LIMIT_DEFAULT = 20_000


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        logger.warning("[terminal] %s=%r 不是整数，回落到 %s", name, raw, default)
        return default
    return max(lo, min(hi, value))


def enabled() -> bool:
    """终端能力开关（默认关；只有显式写 1/true/yes/on 才开）"""
    return str(os.environ.get(ENV_TERMINAL_ENABLED, "")).strip().lower() in _TRUE


def max_sessions() -> int:
    return _env_int(ENV_TERMINAL_MAX_SESSIONS, _DEFAULT_MAX_SESSIONS, 1, 16)


def buffer_chars() -> int:
    return _env_int(ENV_TERMINAL_BUFFER_CHARS, _DEFAULT_BUFFER_CHARS, 4096, 2_000_000)


def idle_seconds() -> float:
    return float(_env_int(ENV_TERMINAL_IDLE_SECONDS, int(_DEFAULT_IDLE_SECONDS), 30, 86400))


def _audit(event: str, *, session_id: str = "", command: str = "", payload: Optional[dict] = None) -> None:
    """审计（只记摘要 + 长度，不记命令原文：可能是密钥/令牌）"""
    try:
        from agent.audit.facade import audit
        body = dict(payload or {})
        if command:
            body["command_chars"] = len(command)
            body["command_digest"] = "sha256:" + hashlib.sha256(
                command.encode("utf-8", errors="replace")).hexdigest()[:32]
            body["command_recorded"] = False
        audit.record(event, actor="terminal", subject=str(session_id or ""),
                     payload=body, source="agent")
    except Exception as exc:  # noqa: BLE001 审计失败不影响终端本身
        logger.debug("[terminal] 审计写入失败: %s", exc)


class _Session:
    """一个 PTY 会话：进程 + 环形输出缓冲 + 读线程。"""

    __slots__ = ("id", "proc", "master_fd", "thread", "lock", "buf", "buf_start",
                 "produced", "created_at", "last_active", "command", "cwd", "closed")

    def __init__(self, session_id: str, proc, master_fd, command: str, cwd: str):
        self.id = session_id
        self.proc = proc
        self.master_fd = master_fd
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.buf = ""
        self.buf_start = 0
        self.produced = 0
        self.created_at = time.time()
        self.last_active = self.created_at
        self.command = command
        self.cwd = cwd
        self.closed = False

    # ── 输出缓冲 ──
    def append(self, text: str) -> None:
        if not text:
            return
        cap = buffer_chars()
        with self.lock:
            self.buf += text
            self.produced += len(text)
            overflow = len(self.buf) - cap
            if overflow > 0:
                self.buf = self.buf[overflow:]
                self.buf_start += overflow

    def read(self, offset: Any = None, limit: Any = None) -> Dict[str, Any]:
        try:
            take = int(limit) if limit is not None else _READ_LIMIT_DEFAULT
        except (TypeError, ValueError):
            take = _READ_LIMIT_DEFAULT
        take = max(1, min(_READ_LIMIT_MAX, take))
        with self.lock:
            start = self.buf_start if offset is None else max(0, int(offset))
            trimmed = start < self.buf_start
            if trimmed:
                start = self.buf_start
            rel = max(0, start - self.buf_start)
            chunk = self.buf[rel: rel + take]
            end = start + len(chunk)
            total = self.produced
        self.last_active = time.time()
        return {
            "ok": True,
            "id": self.id,
            "chunk": chunk,
            "offset": start,
            "next_offset": end if end < total else None,
            "total_chars": total,
            "trimmed_from": self.buf_start,
            "trimmed": trimmed,
            "alive": self.alive(),
        }

    def alive(self) -> bool:
        proc = self.proc
        if proc is None:
            return False
        try:
            if hasattr(proc, "isalive"):
                return bool(proc.isalive())
            return proc.poll() is None
        except Exception:  # noqa: BLE001
            return False

    def write(self, text: str) -> None:
        if self.master_fd is not None:
            os.write(self.master_fd, text.encode("utf-8", "replace"))
        else:
            self.proc.write(text)
        self.last_active = time.time()

    def interrupt(self) -> None:
        if hasattr(self.proc, "sendintr"):
            self.proc.sendintr()
        elif self.master_fd is not None:
            os.write(self.master_fd, b"\x03")
        self.last_active = time.time()

    def terminate(self) -> None:
        """先礼后兵：terminate → 整棵树 kill（PTY 会话常带孙进程）"""
        try:
            if hasattr(self.proc, "terminate"):
                self.proc.terminate(force=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            from agent.proc_tree import kill_tree
            pid = getattr(self.proc, "pid", None)
            if pid:
                kill_tree(pid)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.terminate()
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        try:
            if hasattr(self.proc, "close"):
                self.proc.close(force=True)
        except Exception:  # noqa: BLE001
            pass


_sessions: Dict[str, _Session] = {}
_lock = threading.Lock()


def _spawn_windows(argv: List[str], cwd: str, rows: int, cols: int):
    try:
        import winpty
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("缺少 pywinpty：pip install pywinpty 后重试") from exc
    proc = winpty.PtyProcess.spawn(argv, cwd=cwd or None, dimensions=(rows, cols))
    return proc, None


def _spawn_posix(argv: List[str], cwd: str, rows: int, cols: int):
    import pty

    from agent.utils import proc as _proc

    master_fd, slave_fd = pty.openpty()
    try:
        # **_proc.hidden_kwargs()：形态口径（test_proc_hidden_window 会静态扫描裸 Popen）。
        # 非 Windows 返回 {}，所以对 POSIX 行为零影响；加上它是为了「不留一个裸 spawn 形态」。
        proc = subprocess.Popen(
            argv, cwd=cwd or None,
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            preexec_fn=os.setsid, close_fds=True, **_proc.hidden_kwargs())
    finally:
        os.close(slave_fd)
    return proc, master_fd


def _reader_loop(session: _Session) -> None:
    """后台读线程：PTY 输出不会自己进缓冲，必须有人一直读（否则子进程写阻塞）。"""
    while not session.closed:
        try:
            if session.master_fd is not None:
                data = os.read(session.master_fd, _READ_CHUNK)
                if not data:
                    break
                session.append(data.decode("utf-8", "replace"))
            else:
                if not session.alive() and not getattr(session.proc, "eof", lambda: True)():
                    break
                try:
                    data = session.proc.read(_READ_CHUNK)
                except EOFError:
                    break
                if not data:
                    break
                session.append(str(data))
        except (OSError, ValueError):
            break
        except Exception as exc:  # noqa: BLE001 读线程异常只结束本会话的采集
            logger.debug("[terminal] 读线程结束 %s: %s", session.id, type(exc).__name__)
            break


def _reap_idle() -> None:
    limit = idle_seconds()
    now = time.time()
    with _lock:
        stale = [sid for sid, s in _sessions.items() if now - s.last_active > limit]
    for sid in stale:
        logger.info("[terminal] 空闲回收会话 %s（> %.0fs）", sid, limit)
        close(sid)


def open_session(command: str = "", cwd: str = "", rows: int = 30, cols: int = 120) -> Dict[str, Any]:
    """开一个 PTY 会话（默认起当前平台默认 shell）。"""
    if not enabled():
        return {"ok": False, "disabled": True,
                "error": "终端未启用：需要 CP_TERMINAL_ENABLED=1（设置中心 A 级开关）"}
    try:
        from agent.sandbox import is_confined
        if is_confined():
            return {"ok": False, "blocked": True,
                    "error": "权限预设为受限档：raw PTY 会绕过命令沙箱，已拒绝（fail-closed）"}
    except Exception:  # noqa: BLE001 判不了就按受限处理（fail-closed）
        return {"ok": False, "blocked": True, "error": "无法判定权限预设，按受限档拒绝"}

    _reap_idle()
    with _lock:
        if len(_sessions) >= max_sessions():
            return {"ok": False,
                    "error": "终端会话已达上限（%d）；先 terminal(action=close) 或 list 看看" % max_sessions()}

    workdir = str(cwd or os.getcwd())
    try:
        if not os.path.isdir(workdir):
            return {"ok": False, "error": "工作目录不存在（或不可访问）"}
    except OSError:
        return {"ok": False, "error": "工作目录不可访问"}

    argv = shlex.split(command) if str(command or "").strip() else []
    if not argv:
        argv = ["cmd", "/Q"] if sys.platform == "win32" else ["bash", "-i"]

    try:
        if sys.platform == "win32":
            proc, master_fd = _spawn_windows(argv, workdir, rows, cols)
        else:
            proc, master_fd = _spawn_posix(argv, workdir, rows, cols)
    except RuntimeError as exc:
        return {"ok": False, "dependency": "pywinpty", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 启动失败只回固定文案（异常原文不进响应体）
        logger.warning("[terminal] 启动失败: %s", type(exc).__name__)
        return {"ok": False, "error": "终端启动失败（详见服务日志）"}

    session_id = "t-" + uuid.uuid4().hex[:8]
    session = _Session(session_id, proc, master_fd, " ".join(argv), workdir)
    thread = threading.Thread(target=_reader_loop, args=(session,),
                              name="pty-%s" % session_id, daemon=True)
    session.thread = thread
    thread.start()
    with _lock:
        _sessions[session_id] = session

    _audit("terminal_open", session_id=session_id, command=" ".join(argv),
           payload={"cwd_digest": "sha256:" + hashlib.sha256(
               workdir.encode("utf-8", "replace")).hexdigest()[:16], "rows": rows, "cols": cols})
    return {"ok": True, "id": session_id, "command": " ".join(argv), "cwd": workdir,
            "rows": rows, "cols": cols,
            "hint": "用 terminal(action=read, id=...) 取输出；send 写 stdin；signal=interrupt 发 Ctrl-C"}


def _get(session_id: Any) -> Optional[_Session]:
    with _lock:
        return _sessions.get(str(session_id or ""))


def send(session_id: str, text: str) -> Dict[str, Any]:
    sess = _get(session_id)
    if sess is None:
        return {"ok": False, "error": "会话不存在（可能已关闭/回收）"}
    try:
        sess.write(str(text or ""))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[terminal] 写入失败 %s: %s", session_id, type(exc).__name__)
        return {"ok": False, "id": session_id, "error": "写入失败（会话可能已退出）"}
    _audit("terminal_input", session_id=session_id, command=str(text or ""))
    return {"ok": True, "id": session_id, "chars": len(str(text or ""))}


def read(session_id: str, offset: Any = None, limit: Any = None) -> Dict[str, Any]:
    sess = _get(session_id)
    if sess is None:
        return {"ok": False, "error": "会话不存在（可能已关闭/回收）"}
    return sess.read(offset=offset, limit=limit)


def signal(session_id: str, action: str = "interrupt") -> Dict[str, Any]:
    sess = _get(session_id)
    if sess is None:
        return {"ok": False, "error": "会话不存在（可能已关闭/回收）"}
    act = str(action or "interrupt").strip().lower()
    if act in ("interrupt", "ctrl-c", "sigint"):
        sess.interrupt()
        _audit("terminal_signal", session_id=session_id, payload={"action": "interrupt"})
        return {"ok": True, "id": session_id, "action": "interrupt"}
    if act in ("terminate", "kill"):
        sess.terminate()
        _audit("terminal_signal", session_id=session_id, payload={"action": "terminate"})
        return {"ok": True, "id": session_id, "action": "terminate"}
    return {"ok": False, "error": "action 只能是 interrupt 或 terminate"}


def close(session_id: str) -> Dict[str, Any]:
    sid = str(session_id or "")
    with _lock:
        sess = _sessions.pop(sid, None)
    if sess is None:
        return {"ok": False, "error": "会话不存在（可能已关闭）"}
    sess.close()
    _audit("terminal_close", session_id=sid)
    return {"ok": True, "id": sid}


def list_sessions() -> Dict[str, Any]:
    _reap_idle()
    with _lock:
        items = []
        for sess in _sessions.values():
            items.append({
                "id": sess.id,
                "command": sess.command,
                "cwd": sess.cwd,
                "alive": sess.alive(),
                "age_s": round(time.time() - sess.created_at, 1),
                "idle_s": round(time.time() - sess.last_active, 1),
                "buffered_chars": len(sess.buf),
                "total_chars": sess.produced,
            })
    return {"ok": True, "sessions": items, "count": len(items), "max": max_sessions()}


def close_all() -> int:
    """关闭全部会话（测试/进程退出用）"""
    with _lock:
        ids = list(_sessions)
    for sid in ids:
        close(sid)
    return len(ids)


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "max_sessions": max_sessions(),
        "buffer_chars": buffer_chars(),
        "idle_seconds": idle_seconds(),
        "sessions": len(_sessions),
        "backend": "winpty" if sys.platform == "win32" else "pty",
    }


__all__ = ["ENV_TERMINAL_ENABLED", "ENV_TERMINAL_MAX_SESSIONS", "ENV_TERMINAL_BUFFER_CHARS",
           "ENV_TERMINAL_IDLE_SECONDS", "enabled", "max_sessions", "buffer_chars",
           "idle_seconds", "open_session", "send", "read", "signal", "close",
           "list_sessions", "close_all", "status"]
