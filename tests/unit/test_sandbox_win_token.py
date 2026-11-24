"""E0-1 Windows 受限令牌层的测试。

分两档：
1. **纯逻辑**（任何平台都跑）：平台可用性、SID/环境块形状、失败即抛（fail-closed）；
2. **真机**（默认跳过）：只在 `CP_SANDBOX_TEST_REAL=1` 时跑——会创建本地组、授 ACL、
   起受限子进程。CI 不跑（机器级副作用），本地按 DoD 用 `.local/verify_e0_1_*` 验。

真机用例的断言与探针一致：写授权根成功、写根外被 OS 拒绝。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from agent.sandbox import preset as preset_mod
from agent.sandbox import shell_run, win_acl

_REAL = os.environ.get("CP_SANDBOX_TEST_REAL") == "1"
_ON_WINDOWS = sys.platform == "win32"
needs_real = pytest.mark.skipif(not (_REAL and _ON_WINDOWS),
                                reason="真机用例需 CP_SANDBOX_TEST_REAL=1 且 Windows")


@pytest.fixture(autouse=True)
def _clean_sandbox_env(monkeypatch):
    monkeypatch.delenv(preset_mod.PRESET_ENV, raising=False)
    monkeypatch.delenv(preset_mod.ROOT_ENV, raising=False)


def test_平台事实():
    from agent.sandbox import win_token
    assert win_token.is_windows() is _ON_WINDOWS
    assert win_token.is_available() is _ON_WINDOWS
    if not _ON_WINDOWS:
        assert win_token.unavailable_reason()


def test_非_windows_构造令牌必须抛():
    if _ON_WINDOWS:
        pytest.skip("仅非 Windows")
    from agent.sandbox import win_token
    with pytest.raises(win_token.SandboxUnavailable):
        win_token.RestrictedToken(None)


def test_full_access_不走受限执行器(tmp_path):
    with pytest.raises(win_acl.SandboxUnavailable):
        shell_run.run_captured(["cmd", "/c", "echo x"], str(tmp_path), 5,
                               preset=preset_mod.FULL_ACCESS)


def test_未授权根_fail_closed(monkeypatch, tmp_path):
    """marker 缺失时必须抛 SandboxNotReady，绝不降级执行。"""
    if not _ON_WINDOWS:
        pytest.skip("仅 Windows")
    monkeypatch.setenv(preset_mod.ROOT_ENV, str(tmp_path / "not-granted"))
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.WORKSPACE_WRITE)
    (tmp_path / "not-granted").mkdir()
    # 组 SID 存在但该根没有 marker/ACE ⇒ not ready
    monkeypatch.setattr(win_acl, "group_sid", lambda name=win_acl.GROUP_NAME: "S-1-5-21-0-0-0-1")
    monkeypatch.setattr(win_acl, "is_granted", lambda *a, **k: False)
    with pytest.raises(win_acl.SandboxNotReady):
        shell_run.run_captured(["cmd", "/c", "echo x"], str(tmp_path), 5)


@needs_real
def test_真机_子进程写授权根成功_根外被拒(monkeypatch, tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv(preset_mod.ROOT_ENV, str(root))
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.WORKSPACE_WRITE)
    sid = win_acl.ensure_grant(root)
    assert sid.startswith("S-1-5-")
    inside = root / "inside.txt"
    result = shell_run.run_captured(["cmd", "/c", f"echo ok > {inside}"], str(root), 30)
    assert result["ok"] is True and inside.exists()
    outside = tmp_path / "outside.txt"
    result = shell_run.run_captured(["cmd", "/c", f"echo no > {outside}"], str(root), 30)
    assert result["ok"] is False and not outside.exists()
    win_acl.revoke(root)


@needs_real
def test_真机_read_only_写不进工作区(monkeypatch, tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv(preset_mod.ROOT_ENV, str(root))
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.READ_ONLY)
    target = root / "nope.txt"
    result = shell_run.run_captured(["cmd", "/c", f"echo no > {target}"], str(root), 30)
    assert result["ok"] is False and not target.exists()
