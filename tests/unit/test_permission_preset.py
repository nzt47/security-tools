"""E0-1 权限预设与文件围栏的契约测试。

覆盖三层：
1. 预设解析（默认 full-access / 非法值回落 / 三档枚举 / 可写根 / 审批姿态）；
2. 文件围栏判定（full-access 放行、read-only 全拒、workspace-write 只放工作区与临时根、
   `..` 越界、大小写、不同盘符、拒绝载荷形状与审计）；
3. 工具接线（write_file / write_workspace / workspace_delete 在 read-only 档必须 blocked）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent.sandbox import fs_fence
from agent.sandbox import preset as preset_mod


@pytest.fixture(autouse=True)
def _clean_sandbox_env(monkeypatch):
    monkeypatch.delenv(preset_mod.PRESET_ENV, raising=False)
    monkeypatch.delenv(preset_mod.ROOT_ENV, raising=False)


# ── 1. 预设解析 ────────────────────────────────────────────────


def test_默认预设是_full_access():
    assert preset_mod.permission_preset() == preset_mod.FULL_ACCESS
    assert preset_mod.is_confined() is False


@pytest.mark.parametrize("value", ["read-only", "workspace-write", "full-access"])
def test_三档枚举原样解析(monkeypatch, value):
    monkeypatch.setenv(preset_mod.PRESET_ENV, value)
    assert preset_mod.permission_preset() == value


@pytest.mark.parametrize("value", ["readonly", "limited", "", "enforce"])
def test_非法值回落_full_access(monkeypatch, value):
    monkeypatch.setenv(preset_mod.PRESET_ENV, value)
    assert preset_mod.permission_preset() == preset_mod.FULL_ACCESS


def test_可写根_按档位(monkeypatch, tmp_path):
    monkeypatch.setenv(preset_mod.ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.READ_ONLY)
    assert preset_mod.writable_roots() == ()
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.WORKSPACE_WRITE)
    roots = preset_mod.writable_roots()
    assert str(tmp_path.resolve()) == str(roots[0])
    assert len(roots) == 2  # 工作区根 + 私有临时根
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.FULL_ACCESS)
    assert preset_mod.writable_roots() == ()


def test_审批姿态映射(monkeypatch):
    for preset, policy in ((preset_mod.READ_ONLY, "strict"),
                           (preset_mod.WORKSPACE_WRITE, "standard"),
                           (preset_mod.FULL_ACCESS, "minimal")):
        assert preset_mod.approval_policy(preset) == policy


def test_沙箱根_空白值回落仓库根(monkeypatch):
    # 空/全空白 = 未配置（NUL 不能进 os.environ，跨平台用例不碰它）
    monkeypatch.setenv(preset_mod.ROOT_ENV, "   ")
    assert preset_mod.sandbox_root() == preset_mod.repo_root()


def test_status_形状(monkeypatch, tmp_path):
    monkeypatch.setenv(preset_mod.ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.WORKSPACE_WRITE)
    payload = preset_mod.status()
    assert payload["preset"] == preset_mod.WORKSPACE_WRITE
    assert payload["confined"] is True
    assert payload["approval_policy_wired"] is False
    assert payload["approval_policy"] == "standard"
    assert str(tmp_path.resolve()) in payload["writable_roots"]


# ── 2. 文件围栏 ────────────────────────────────────────────────


def test_围栏_full_access_全放行(tmp_path):
    assert fs_fence.check_write(tmp_path / "x.txt", "write_file") is None
    assert fs_fence.is_write_allowed(tmp_path / "x.txt") is True


def test_围栏_read_only_全拒(monkeypatch, tmp_path):
    monkeypatch.setenv(preset_mod.ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.READ_ONLY)
    denied = fs_fence.check_write(tmp_path / "x.txt", "write_file")
    assert denied is not None and denied["blocked"] is True
    assert denied["sandbox"] == preset_mod.READ_ONLY
    assert denied["error"] == "[sandbox: file access denied under read-only mode]"
    assert fs_fence.is_write_allowed(tmp_path / "x.txt") is False


def test_围栏_workspace_write_区内放行区外拒绝(monkeypatch, tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv(preset_mod.ROOT_ENV, str(root))
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.WORKSPACE_WRITE)
    assert fs_fence.check_write(root / "sub" / "a.txt", "write_file") is None
    outside = tmp_path / "outside.txt"
    denied = fs_fence.check_write(outside, "write_file")
    assert denied is not None and denied["blocked"] is True
    assert denied["sandbox"] == preset_mod.WORKSPACE_WRITE


def test_围栏_点点越界被拒(monkeypatch, tmp_path):
    root = tmp_path / "ws"
    (root / "sub").mkdir(parents=True)
    monkeypatch.setenv(preset_mod.ROOT_ENV, str(root))
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.WORKSPACE_WRITE)
    escape = root / "sub" / ".." / ".." / "escape.txt"
    assert fs_fence.check_write(escape, "write_file") is not None


def test_围栏_前缀相同但不是子目录被拒(monkeypatch, tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    sibling = tmp_path / "ws-evil"
    sibling.mkdir()
    monkeypatch.setenv(preset_mod.ROOT_ENV, str(root))
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.WORKSPACE_WRITE)
    # 字符串前缀检查会把 ws-evil 误判成 ws 的子目录
    assert fs_fence.check_write(sibling / "a.txt", "write_file") is not None


@pytest.mark.skipif(os.name != "nt", reason="大小写不敏感是 Windows 语义（Linux 区分大小写）")
def test_围栏_大小写不敏感(monkeypatch, tmp_path):
    root = tmp_path / "CaseWS"
    root.mkdir()
    monkeypatch.setenv(preset_mod.ROOT_ENV, str(root))
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.WORKSPACE_WRITE)
    lowered = Path(str(root).lower()) / "a.txt"
    assert fs_fence.check_write(lowered, "write_file") is None


def test_围栏_拒绝必审计(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(preset_mod, "audit", lambda event, **kw: events.append((event, kw)))
    monkeypatch.setenv(preset_mod.ROOT_ENV, str(tmp_path / "ws"))
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.READ_ONLY)
    fs_fence.check_write(tmp_path / "x.txt", "write_file")
    assert events and events[0][0] == "sandbox_fs_denied"
    payload = events[0][1]["payload"]
    assert payload["mode"] == preset_mod.READ_ONLY
    assert payload["path_recorded"] is False
    assert payload["path_digest"].startswith("sha256:")


def test_围栏_None与空路径按拒绝处理(monkeypatch, tmp_path):
    monkeypatch.setenv(preset_mod.ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.WORKSPACE_WRITE)
    assert fs_fence.is_write_allowed("") is False
    assert fs_fence.check_write(None, "write_file") is not None


# ── 3. 工具接线 ────────────────────────────────────────────────


def test_write_file_in_read_only_blocked(monkeypatch, tmp_path):
    monkeypatch.setenv(preset_mod.ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.READ_ONLY)
    from agent.tools.file_tools import write_file
    target = tmp_path / "ro.txt"
    result = write_file(str(target), "x")
    assert result["ok"] is False and result["blocked"] is True
    assert "file access denied" in result["error"]
    assert not target.exists()


def test_write_file_in_workspace_write_inside_ok(monkeypatch, tmp_path):
    monkeypatch.setenv(preset_mod.ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.WORKSPACE_WRITE)
    from agent.tools.file_tools import write_file
    target = tmp_path / "ok.txt"
    result = write_file(str(target), "x")
    assert result["ok"] is True and target.read_text(encoding="utf-8") == "x"


def test_workspace_write_delete_blocked_in_read_only(monkeypatch, tmp_path):
    from agent.tools import workspace_tools
    monkeypatch.setattr(workspace_tools, "WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv(preset_mod.PRESET_ENV, preset_mod.READ_ONLY)
    result = workspace_tools.write_workspace("a.txt", "x")
    assert result["ok"] is False and result["blocked"] is True
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    result = workspace_tools.delete_workspace("a.txt")
    assert result["ok"] is False and result["blocked"] is True
    assert (tmp_path / "a.txt").exists()


def test_env_block_双NUL结尾():
    from agent.sandbox.shell_run import build_env, env_block
    block = env_block(build_env(Path(os.environ.get("TEMP", "."))))
    assert block.endswith(b"\0\0")
    assert "PATH=" in block.decode("utf-16-le", errors="replace")

