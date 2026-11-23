"""第三方插件包进程宿主（P2 进程隔离，2026-11-22）。

【它解决什么】
- 插件包的 Python 代码不再 import 进宿主进程：独立子进程加载、调用、退出；
  插件段错误/OOM/无限循环不会直接带走宿主（超时会被硬杀）。
- 子进程只拿到最小环境变量白名单；FLASK_API_TOKEN / DEEPSEEK_API_KEY 等不进环境。

【它不解决什么（如实声明）】
- 默认（CP_PLUGIN_SANDBOX=off）仍只是进程边界：子进程以当前用户身份运行，
  能读当前用户能读的文件；不隔离文件/网络。
- CP_PLUGIN_SANDBOX=appcontainer 时走 Windows AppContainer：默认无网络、
  读不到用户目录，只能访问显式 ACL 授权的插件目录/数据目录（见 appcontainer.py）。
- 插件工具调用是**串行**的（一把锁；协议是单行请求/响应）。并发由上层
  CP_PLUGIN_MAX_CONCURRENT 限制；插件内部并发需自己处理。

【协议】见 agent/extensions/plugin_worker.py 的模块 docstring。
"""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.utils import proc as _proc
from . import appcontainer, plugin_policy

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STDERR_TAIL_LINES = 40

logger = logging.getLogger(__name__)


class PluginProcessError(RuntimeError):
    error_code = "PLUGIN_PROCESS_ERROR"

    def __init__(self, message: str, error_code: Optional[str] = None):
        super().__init__(message)
        self.message = str(message)
        if error_code:
            self.error_code = str(error_code)


class PluginProcessTimeout(PluginProcessError):
    error_code = "PLUGIN_PROCESS_TIMEOUT"


def _worker_env(deps_dir: Optional[Path]) -> Dict[str, str]:
    """最小环境变量白名单；**绝不**传递宿主 .env 里的密钥。"""
    # 逐条字面量读取：不要用 for k in keys + os.environ[k]（扫描器会判成
    # <unresolved> 动态家族，CI 的开关零缺口门禁会红）。
    candidates = (
        ("PATH", os.environ.get("PATH")),
        ("SYSTEMROOT", os.environ.get("SYSTEMROOT")),
        ("WINDIR", os.environ.get("WINDIR")),
        ("TEMP", os.environ.get("TEMP")),
        ("TMP", os.environ.get("TMP")),
        ("COMSPEC", os.environ.get("COMSPEC")),
        ("HOME", os.environ.get("HOME")),
        ("USERPROFILE", os.environ.get("USERPROFILE")),
        ("APPDATA", os.environ.get("APPDATA")),
        ("LOCALAPPDATA", os.environ.get("LOCALAPPDATA")),
    )
    env = {k: v for k, v in candidates if v is not None}
    paths = [str(_REPO_ROOT)]
    if deps_dir is not None and deps_dir.is_dir():
        paths.insert(0, str(deps_dir))
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["CP_PLUGIN_PROCESS_ISOLATED"] = "1"
    return env


class PluginProcess:
    """一个插件包对应一个子进程；start() 后通过 call() 调工具。"""

    def __init__(self, plugin_id: str, install_path: str,
                 config: Optional[Dict[str, Any]] = None,
                 timeout: float = 30.0,
                 handshake_timeout: float = 30.0):
        self.plugin_id = str(plugin_id)
        self.install_path = str(install_path)
        self._config = dict(config or {})
        self._timeout = max(1.0, float(timeout))
        self._handshake_timeout = max(1.0, float(handshake_timeout))
        self._proc: Optional[subprocess.Popen] = None
        self._stdout_q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._stderr_tail: deque = deque(maxlen=_STDERR_TAIL_LINES)
        self._io_lock = threading.RLock()
        self._reader: Optional[threading.Thread] = None
        self._err_reader: Optional[threading.Thread] = None
        self._tools: List[Dict[str, Any]] = []
        self._sandbox: Optional[Any] = None

    # ── 生命周期 ──────────────────────────────────────────────

    def start(self) -> List[Dict[str, Any]]:
        p = Path(self.install_path)
        if not p.exists():
            raise PluginProcessError("插件路径不存在")
        plugin_dir = p.parent if p.is_file() else p
        deps = (p / ".deps") if p.is_dir() else None
        if plugin_policy.sandbox_mode() == "appcontainer":
            self._proc = self._spawn_sandboxed(p, plugin_dir, deps)
        else:
            cmd = [sys.executable, "-m", "agent.extensions.plugin_worker",
                   str(p), self.plugin_id]
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=str(plugin_dir),
                    env=_worker_env(deps),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    **_proc.hidden_kwargs(),
                )
            except Exception as exc:  # noqa: BLE001
                raise PluginProcessError("插件子进程启动失败（%s）" % type(exc).__name__) from None
        self._reader = threading.Thread(
            target=self._read_stdout, name="plugin-worker-out-" + self.plugin_id,
            daemon=True)
        self._err_reader = threading.Thread(
            target=self._read_stderr, name="plugin-worker-err-" + self.plugin_id,
            daemon=True)
        self._reader.start()
        self._err_reader.start()
        try:
            self._send({"op": "init", "config": self._config})
            msg = self._recv(self._handshake_timeout, kill_on_timeout=True)
        except Exception:
            self.close()
            raise
        if str(msg.get("op") or "") != "ready":
            self.close()
            raise PluginProcessError("插件子进程初始化失败: " + str(msg.get("error") or "unknown"))
        tools = msg.get("tools")
        self._tools = [t for t in (tools or []) if isinstance(t, dict)]
        return list(self._tools)

    def _spawn_sandboxed(self, p: Path, plugin_dir: Path, deps: Optional[Path]):
        """在 AppContainer 里启动 worker；runner 复制到插件数据目录，避免授予仓库读权限。"""
        if not appcontainer.is_available():
            raise PluginProcessError(
                "AppContainer 不可用（%s）" % (appcontainer.unavailable_reason() or "unknown"),
                "PLUGIN_SANDBOX_UNAVAILABLE")
        data_dir = appcontainer.sandbox_data_dir(self.plugin_id)
        data_dir.mkdir(parents=True, exist_ok=True)
        runner = data_dir / "plugin_worker.py"
        try:
            runner.write_bytes(Path(__file__).with_name("plugin_worker.py").read_bytes())
        except OSError:
            raise PluginProcessError("沙箱 runner 写入失败", "PLUGIN_SANDBOX_LAUNCH_FAILED") from None
        grants = [
            (p.parent, "RX", False),
            (p, "RX", p.is_dir()),
        ]
        sandbox = None
        try:
            appcontainer.ensure_runtime_access()
            sandbox = appcontainer.AppContainerSandbox(self.plugin_id, data_dir, grants)
            env = appcontainer.build_environment(data_dir, deps)
            cmd = [sys.executable, str(runner), str(p), self.plugin_id]
            proc = sandbox.spawn(cmd, data_dir, env)
        except Exception as exc:  # noqa: BLE001 不可用/启动失败都 fail-closed
            if sandbox is not None:
                sandbox.close()
            raise PluginProcessError(
                "AppContainer 启动失败（%s）" % type(exc).__name__,
                "PLUGIN_SANDBOX_LAUNCH_FAILED") from None
        self._sandbox = sandbox
        return proc

    def _close_sandbox(self) -> None:
        sandbox = self._sandbox
        self._sandbox = None
        if sandbox is not None:
            try:
                sandbox.close()
            except Exception:
                pass

    def call(self, tool: str, args: Optional[Dict[str, Any]] = None) -> Any:
        if not self.alive:
            raise PluginProcessError("插件子进程已退出")
        with self._io_lock:
            self._send({"op": "call", "tool": str(tool), "args": dict(args or {})})
            msg = self._recv(self._timeout, kill_on_timeout=True)
        if msg.get("ok") is False:
            raise PluginProcessError(str(msg.get("error") or "插件工具执行失败"),
                                     str(msg.get("error_code") or "PLUGIN_TOOL_ERROR"))
        return msg.get("result")

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            self._close_sandbox()
            return
        try:
            if proc.poll() is None:
                with self._io_lock:
                    self._send_raw({"op": "shutdown"})
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self._kill()
        except Exception:
            self._kill()
        finally:
            self._proc = None
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
            self._close_sandbox()

    def _kill(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── 协议 I/O ──────────────────────────────────────────────

    def _send_raw(self, obj: Dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise PluginProcessError("插件子进程不可写")
        try:
            proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except Exception as exc:  # noqa: BLE001
            raise PluginProcessError("插件子进程写入失败（%s）" % type(exc).__name__) from None

    def _send(self, obj: Dict[str, Any]) -> None:
        with self._io_lock:
            self._send_raw(obj)

    def _recv(self, timeout: float, *, kill_on_timeout: bool = False) -> Dict[str, Any]:
        try:
            line = self._stdout_q.get(timeout=max(0.1, timeout))
        except queue.Empty:
            if kill_on_timeout:
                self._kill()
            raise PluginProcessTimeout("插件调用超过 %.1fs，已中止" % timeout) from None
        if line is None:
            if self._stderr_tail:
                # stderr 可能含插件打印的敏感内容：只进日志，不进响应/异常文案。
                logger.warning("[plugin-process] %s 子进程退出，stderr 尾部: %s",
                               self.plugin_id, self._stderr_tail[-1])
            raise PluginProcessError("插件子进程已退出（详见服务日志）")
        try:
            msg = json.loads(line)
        except Exception:
            raise PluginProcessError("插件子进程返回了非 JSON 协议行") from None
        if not isinstance(msg, dict):
            raise PluginProcessError("插件子进程返回了非对象协议行")
        return msg

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            self._stdout_q.put(None)
            return
        try:
            for line in proc.stdout:
                self._stdout_q.put(line)
        except Exception:
            pass
        finally:
            self._stdout_q.put(None)

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                text = str(line).strip()
                if text:
                    self._stderr_tail.append(text[:300])
        except Exception:
            pass
