"""会话「添加工作区」（绑定自定义目录）与 WorkspaceRegistry 单元测试

覆盖：
- 默认工作空间 vs 绑定自定义目录（list_session_workspace.custom / root）
- bind_workspace_root：绝对路径校验 / 自动创建 / 绑定文件报错 / 幂等
- clear_workspace_root：恢复默认
- WorkspaceRegistry：登记 / 幂等 / 移除 / 实例间持久化
"""
import json
import os
from pathlib import Path

import pytest

from agent.session_manager import SessionManager, WorkspaceRegistry


@pytest.fixture
def manager(tmp_path):
    return SessionManager(sessions_dir=str(tmp_path / "sessions"))


@pytest.fixture
def registry(tmp_path):
    return WorkspaceRegistry(sessions_dir=str(tmp_path / "sessions"))


class TestCustomWorkspaceBinding:
    def test_default_workspace_custom_false(self, manager, tmp_path):
        info = manager.create_session(title="A")
        result = manager.list_session_workspace(info["id"])
        assert result["custom"] is False
        assert result["root"] == str(tmp_path / "sessions" / info["id"] / "workspace")
        assert result["exists"] is True

    def test_bind_existing_dir(self, manager, tmp_path):
        info = manager.create_session()
        proj = tmp_path / "projects" / "myproject"
        proj.mkdir(parents=True)
        (proj / "plan.md").write_text("# plan", encoding="utf-8")

        ok, payload = manager.bind_workspace_root(info["id"], str(proj))
        assert ok is True
        assert payload["root"] == str(proj)

        result = manager.list_session_workspace(info["id"])
        assert result["custom"] is True
        assert result["root"] == str(proj)
        rels = {f["rel"] for f in result["files"]}
        assert "plan.md" in rels
        # 绑定目录不会被写入 .gitkeep 等文件
        assert not (proj / ".gitkeep").exists()

    def test_bind_creates_missing_dir(self, manager, tmp_path):
        info = manager.create_session()
        target = tmp_path / "new" / "created-later"
        ok, payload = manager.bind_workspace_root(info["id"], str(target), create=True)
        assert ok is True
        assert payload["created"] is True
        assert target.is_dir()

    def test_bind_missing_dir_without_create_fails(self, manager, tmp_path):
        info = manager.create_session()
        ok, payload = manager.bind_workspace_root(
            info["id"], str(tmp_path / "nope"), create=False
        )
        assert ok is False
        assert "不存在" in payload

    def test_bind_relative_path_fails(self, manager, tmp_path):
        info = manager.create_session()
        ok, payload = manager.bind_workspace_root(info["id"], "relative/path")
        assert ok is False
        assert "绝对路径" in payload

    def test_bind_file_path_fails(self, manager, tmp_path):
        info = manager.create_session()
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        ok, payload = manager.bind_workspace_root(info["id"], str(f))
        assert ok is False
        assert "不是文件夹" in payload

    def test_bind_nonexistent_session(self, manager):
        ok, payload = manager.bind_workspace_root("nope", "C:/x")
        assert ok is False

    def test_bind_empty_path_resets_to_default(self, manager, tmp_path):
        """空路径 = 恢复默认"""
        info = manager.create_session()
        proj = tmp_path / "p"
        proj.mkdir()
        manager.bind_workspace_root(info["id"], str(proj))
        ok, payload = manager.bind_workspace_root(info["id"], "")
        assert ok is True
        assert payload["root"] == str(tmp_path / "sessions" / info["id"] / "workspace")
        assert manager.list_session_workspace(info["id"])["custom"] is False

    def test_clear_workspace_root(self, manager, tmp_path):
        info = manager.create_session()
        proj = tmp_path / "p"
        proj.mkdir()
        manager.bind_workspace_root(info["id"], str(proj))
        assert manager.list_session_workspace(info["id"])["custom"] is True

        ok, payload = manager.clear_workspace_root(info["id"])
        assert ok is True
        assert payload["changed"] is True
        meta_path = tmp_path / "sessions" / info["id"] / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert "workspace_root" not in meta
        result = manager.list_session_workspace(info["id"])
        assert result["custom"] is False
        assert result["exists"] is True  # 默认目录仍在

    def test_missing_custom_dir_reports_exists_false(self, manager, tmp_path):
        """绑定的目录被删除后，list 如实返回 exists=False"""
        info = manager.create_session()
        proj = tmp_path / "gone"
        proj.mkdir()
        manager.bind_workspace_root(info["id"], str(proj))
        import shutil
        shutil.rmtree(proj)

        result = manager.list_session_workspace(info["id"])
        assert result["custom"] is True
        assert result["exists"] is False
        assert result["files"] == []

    def test_reveal_path_uses_bound_root(self, manager, tmp_path):
        info = manager.create_session()
        proj = tmp_path / "bound"
        proj.mkdir()
        manager.bind_workspace_root(info["id"], str(proj))
        assert manager.workspace_path(info["id"]) == proj

    def test_binding_persists_across_instances(self, tmp_path):
        dir_path = str(tmp_path / "sessions")
        proj = tmp_path / "proj"
        proj.mkdir()
        mgr1 = SessionManager(sessions_dir=dir_path)
        info = mgr1.create_session()
        mgr1.bind_workspace_root(info["id"], str(proj))

        mgr2 = SessionManager(sessions_dir=dir_path)
        result = mgr2.list_session_workspace(info["id"])
        assert result["custom"] is True
        assert result["root"] == str(proj)


class TestWorkspaceRegistry:
    def test_empty(self, registry):
        assert registry.list_workspaces() == []

    def test_add_and_list(self, registry, tmp_path):
        proj = tmp_path / "proj-a"
        proj.mkdir()
        entry = registry.add_workspace(str(proj))
        assert entry is not None
        assert entry["name"] == "proj-a"
        ws = registry.list_workspaces()
        assert len(ws) == 1
        assert ws[0]["path"] == str(proj)

    def test_add_idempotent(self, registry, tmp_path):
        proj = tmp_path / "proj-a"
        proj.mkdir()
        registry.add_workspace(str(proj))
        again = registry.add_workspace(str(proj))
        assert len(registry.list_workspaces()) == 1
        assert again["path"] == str(proj)

    def test_remove(self, registry, tmp_path):
        proj = tmp_path / "proj-a"
        proj.mkdir()
        registry.add_workspace(str(proj))
        assert registry.remove_workspace(str(proj)) is True
        assert registry.list_workspaces() == []
        assert registry.remove_workspace(str(proj)) is False

    def test_persistence_across_instances(self, tmp_path):
        dir_path = str(tmp_path / "sessions")
        proj = tmp_path / "proj-a"
        proj.mkdir()
        WorkspaceRegistry(sessions_dir=dir_path).add_workspace(str(proj))
        r2 = WorkspaceRegistry(sessions_dir=dir_path)
        assert len(r2.list_workspaces()) == 1

    def test_add_rejects_empty(self, registry):
        assert registry.add_workspace("   ") is None
