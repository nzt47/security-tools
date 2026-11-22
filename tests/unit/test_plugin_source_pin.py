"""GitHub 来源 ref 固定（P2）测试。"""
from __future__ import annotations

from types import SimpleNamespace

from agent.extensions import installer


def test_clone_repo_passes_ref(monkeypatch, tmp_path):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        clone_dir = tmp_path / "repo"
        clone_dir.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    clone_dir = tmp_path / "repo"
    assert installer.InstallEngine._clone_repo("owner/repo", clone_dir, "v1.2.3") is True
    assert "--branch" in calls["cmd"]
    assert "v1.2.3" in calls["cmd"]


def test_clone_repo_failure_cleans_partial_dir(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        clone_dir = tmp_path / "repo"
        clone_dir.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(returncode=128, stdout="", stderr="boom")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    clone_dir = tmp_path / "repo"
    assert installer.InstallEngine._clone_repo("owner/repo", clone_dir, "main") is False
    assert clone_dir.exists() is False
