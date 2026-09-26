"""优雅关闭「收到信号即显式落盘会话最后一条 LLM 通信」（W5/TASK-08 · R2）

事实前提（决定本测试为何必须存在）：
  * `atexit` 对 **SIGTERM/SIGKILL 不触发**；因此"关闭时必存最后一条"此前实际只由
    `agent/llm_monitor.py:218-223` 的「每条即写 + 5s 节流」(PERSIST_MIN_INTERVAL_S=5.0)
    保证 ⇒ **最坏暴露窗口 ≤5s**；这一窗口内的最后一条在信号退出时会丢。
  * 端到端受控探针（真服务 + 真 SIGTERM + 真落盘，含"不装钩子就会丢"的反事实）：
    `scripts/dev/graceful_shutdown_persist_probe.py`

本文件是**进程内**单元层守门：钩子必须真的注册、真的先落盘、且退出路径绝不抛异常。
⚠ 注意：本模块会 `import app_server`（实测 80–100s，见
tests/unit/test_server_routes_registration_inventory.py:36-40），故显式给足超时预算 ——
pytest.ini 的 `--timeout-method=thread` 超时会 `os._exit(1)` **杀掉整个 pytest 进程**。
"""
from __future__ import annotations

import os
import signal

import pytest

pytestmark = pytest.mark.timeout(900)


@pytest.fixture(scope="module")
def app_server_mod():
    """真实入口（与生产同一份注册代码，绝不用 Flask(__name__) 手搓）；用完**整表还原**注册表

    【不易·为什么必须还原】`import app_server` 会登记整套内建工具（实测 91 个）到
    **进程级** `agent/tools/__init__.py:_registry`；不还原就会污染同进程后续测试
    （实测：本文件排在 `tests/unit/test_tool_count_consistency.py` 之前时后者必红 2 条）。
    """
    from agent import tools as _tools

    saved = dict(_tools._registry)
    try:
        import app_server  # noqa: PLC0415
        yield app_server
    finally:
        _tools._registry.clear()
        _tools._registry.update(saved)
        _tools._registry_version += 1


@pytest.fixture
def guard(app_server_mod, monkeypatch):
    """把"退出动作"换成记录器 —— 否则测试进程会真的被信号/硬退出干掉"""
    monkeypatch.setattr(app_server_mod, "_GRACEFUL_SHUTDOWN_DONE", False)
    calls: list = []
    monkeypatch.setattr(os, "_exit", lambda code: calls.append(("exit", code)))
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append(("kill", sig)))
    return calls


class TestHooksInstalled:
    def test_三个信号都被真实注册(self, app_server_mod, monkeypatch):
        monkeypatch.setattr(app_server_mod, "_GRACEFUL_SHUTDOWN_DONE", False)
        installed = app_server_mod._install_graceful_shutdown_hooks()
        # POSIX 语义用 SIGTERM；Windows 能真正跨进程投递的是 SIGINT/SIGBREAK
        assert "SIGTERM" in installed
        assert "SIGINT" in installed
        assert "SIGBREAK" in installed or not hasattr(signal, "SIGBREAK")
        for name in installed:
            assert signal.getsignal(getattr(signal, name)) is app_server_mod._graceful_shutdown_persist


class TestPersistBeforeExit:
    def test_先落盘再退出(self, app_server_mod, guard, monkeypatch):
        import agent.llm_monitor as lm

        order: list = []
        monkeypatch.setattr(lm, "persist_session_last",
                            lambda: (order.append("persist"), True)[1])

        app_server_mod._graceful_shutdown_persist(signal.SIGTERM, None)

        assert order == ["persist"], "退出路径必须先显式落盘"
        assert guard, "落盘之后必须继续退出（不得吞掉信号）"
        assert guard[-1][0] == "exit"

    def test_落盘失败也绝不阻断退出(self, app_server_mod, guard, monkeypatch):
        import agent.llm_monitor as lm

        def boom():
            raise RuntimeError("磁盘满了")

        monkeypatch.setattr(lm, "persist_session_last", boom)
        app_server_mod._graceful_shutdown_persist(signal.SIGTERM, None)  # 不得抛出
        assert guard and guard[-1][0] == "exit"

    def test_幂等_重入只落盘一次(self, app_server_mod, guard, monkeypatch):
        import agent.llm_monitor as lm

        hits: list = []
        monkeypatch.setattr(lm, "persist_session_last",
                            lambda: (hits.append(1), True)[1])
        app_server_mod._graceful_shutdown_persist(signal.SIGTERM, None)
        app_server_mod._graceful_shutdown_persist(signal.SIGTERM, None)
        assert len(hits) == 1

    def test_退出码保持信号语义(self, app_server_mod, guard, monkeypatch):
        """SIGTERM ⇒ 128+15；不得悄悄变成 0（否则外部编排者以为"正常结束"）"""
        import agent.llm_monitor as lm

        monkeypatch.setattr(lm, "persist_session_last", lambda: True)
        app_server_mod._graceful_shutdown_persist(signal.SIGTERM, None)
        assert ("exit", 128 + int(signal.SIGTERM)) in guard
