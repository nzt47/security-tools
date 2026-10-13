"""shell_execute 后台执行（fire-and-forget）的单元测试

覆盖：
1. _submit_background_shell 提交给 AsyncExecutor 并立即返回 task_id；
2. 提交参数不带 background（防无限套娃）；
3. 执行器不可用 / 提交失败时返回结构化错误；
4. _dispatch_shell 的前后台分流（后台不触发同步执行）；
5. YAML 声明层确实暴露 background 布尔参数。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.tools import system_tools as st


class _FakeExecutor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def submit(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


@pytest.fixture()
def fake_executor(monkeypatch):
    """把 agent.async_executor.get_async_executor 换成可控替身"""
    def _install(result):
        fake = _FakeExecutor(result)
        # _submit_background_shell 在调用期才 import，故打在模块属性上即可生效
        monkeypatch.setattr("agent.async_executor.get_async_executor",
                            lambda *a, **k: fake)
        return fake
    return _install


class TestSubmitBackgroundShell:

    def test_returns_task_id_and_passes_command(self, fake_executor):
        fake = fake_executor({"ok": True, "task_id": "task_abc123", "status": "pending"})
        res = st._submit_background_shell(
            "Get-Process | Select-Object -First 3", "powershell", None, 30)
        assert res["ok"] is True
        assert res["background"] is True
        assert res["task_id"] == "task_abc123"
        assert "get_task_status" in res["hint"]
        assert fake.calls, "应调用 AsyncExecutor.submit"
        kw = fake.calls[0]
        assert kw["tool_name"] == "shell_execute"
        assert kw["params"]["command"].startswith("Get-Process")
        assert kw["params"]["shell"] == "powershell"
        assert kw["params"]["timeout"] == 30

    def test_params_exclude_background_flag(self, fake_executor):
        fake = fake_executor({"ok": True, "task_id": "t", "status": "pending"})
        st._submit_background_shell("echo hi", "bash", None, 10)
        assert "background" not in fake.calls[0]["params"], (
            "background 不得透传，否则工作线程会再次转后台（无限套娃）")

    def test_submit_failure_returns_error(self, fake_executor):
        fake_executor({"ok": False, "error": "线程池已关闭"})
        res = st._submit_background_shell("echo hi", "bash", None, 10)
        assert res["ok"] is False
        assert "线程池已关闭" in res["error"]

    def test_executor_unavailable_returns_error(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("no executor")
        monkeypatch.setattr("agent.async_executor.get_async_executor", _boom)
        res = st._submit_background_shell("echo hi", "bash", None, 10)
        assert res["ok"] is False
        assert "后台" in res["error"]


class TestDispatchShell:

    def test_background_does_not_run_synchronously(self, monkeypatch):
        called = {}

        def _fake_sync(*a, **k):  # pragma: no cover - 本分支不该被调用
            called["sync"] = True
            return {"ok": True}

        def _fake_bg(*a, **k):
            called["bg"] = True
            return {"ok": True, "background": True, "task_id": "t"}

        monkeypatch.setattr(st, "_submit_background_shell", _fake_bg)
        monkeypatch.setattr("agent.tools.shell_tools.execute_shell", _fake_sync)
        res = st._dispatch_shell("echo hi", "bash", None, 10, background=True)
        assert res["background"] is True
        assert called == {"bg": True}

    def test_foreground_uses_execute_shell(self, monkeypatch):
        captured = {}

        def _fake_sync(command, shell=None, cwd=None, timeout=None):
            captured.update(command=command, shell=shell, cwd=cwd, timeout=timeout)
            return {"ok": True, "stdout": "hi"}

        monkeypatch.setattr("agent.tools.shell_tools.execute_shell", _fake_sync)
        res = st._dispatch_shell("echo hi", "bash", None, 10, background=False)
        assert res["ok"] is True
        assert captured["command"] == "echo hi"
        assert captured["timeout"] == 10


def test_yaml_declares_background_property():
    p = (Path(__file__).resolve().parents[2] / "data" / "tool_definitions"
         / "shell_execute.yaml")
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    prop = doc["schema"]["properties"]["background"]
    assert prop["type"] == "boolean"
