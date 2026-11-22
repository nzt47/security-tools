"""插件依赖安装：失败必须可见、默认不污染宿主环境。"""
from __future__ import annotations

from types import SimpleNamespace

from agent.extensions import installer


def test_pip_failure_returns_false(monkeypatch):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    assert installer.InstallEngine.install_pip_package("demo") is False
    assert calls["cmd"][-1] == "demo"


def test_pip_target_dir_is_passed(monkeypatch, tmp_path):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    assert installer.InstallEngine.install_pip_package("demo", target_dir=tmp_path) is True
    assert "--target" in calls["cmd"]
    assert str(tmp_path) in calls["cmd"]


def test_dependencies_return_all_failures(monkeypatch):
    seen = []

    def fake_install(package, target_dir=None):
        seen.append((package, target_dir))
        return package == "ok"

    monkeypatch.setattr(installer.InstallEngine, "install_pip_package", staticmethod(fake_install))
    failed = installer.InstallEngine.install_dependencies(["bad", "ok"], target_dir="X")
    assert failed == ["bad"]
    assert seen == [("bad", "X"), ("ok", "X")]
