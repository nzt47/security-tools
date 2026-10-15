"""会话 ↔ 项目绑定 与「工作空间按项目解析」单元测试。

覆盖：
- create_session(project_id=) → 索引（list_sessions）与 meta（get_session_metadata）都能看到归属；
- set_session_project：归入/移出（索引 + meta 同步），不存在的会话返回 False；
- get_session_workspace_dir 优先级：会话自绑 > 所属项目 > 默认；
- list_session_workspace 的 source 字段（session / project / default）；
- 未接线项目解析器、项目不存在、解析器抛错 ⇒ 一律按"未归属项目"回落默认（向后兼容）。
"""
from pathlib import Path

import pytest

from agent.session_manager import ProjectStore, SessionManager


@pytest.fixture
def session_dir(tmp_path):
    return str(tmp_path / "sessions")


@pytest.fixture
def manager(session_dir):
    return SessionManager(sessions_dir=session_dir)


@pytest.fixture
def projects(session_dir):
    return ProjectStore(sessions_dir=session_dir)


@pytest.fixture
def wired(manager, projects):
    """按 app_server 的接线方式注入项目→工作空间解析器"""
    manager.set_project_root_resolver(projects.workspace_root_for)
    return manager


class TestSessionProjectBinding:
    def test_create_with_project(self, manager):
        info = manager.create_session(title="A", project_id="proj_1")
        assert info["project_id"] == "proj_1"
        listed = {s["id"]: s for s in manager.list_sessions()}[info["id"]]
        assert listed["project_id"] == "proj_1"
        assert manager.get_session_metadata(info["id"])["project_id"] == "proj_1"

    def test_create_without_project(self, manager):
        info = manager.create_session(title="A")
        assert info["project_id"] is None
        assert manager.get_session_metadata(info["id"])["project_id"] is None

    def test_set_and_clear_project(self, manager):
        info = manager.create_session()
        assert manager.set_session_project(info["id"], "proj_9") is True
        assert manager.get_session(info["id"])["project_id"] == "proj_9"
        assert manager.get_session_metadata(info["id"])["project_id"] == "proj_9"
        assert manager.set_session_project(info["id"], None) is True
        assert manager.get_session(info["id"])["project_id"] is None

    def test_set_project_unknown_session(self, manager):
        assert manager.set_session_project("sess_nope", "proj_1") is False


class TestProjectWorkspaceResolution:
    def test_project_root_used_when_no_session_override(self, wired, projects, tmp_path):
        proj_root = tmp_path / "proj_ws"
        proj_root.mkdir()
        p = projects.create_project("P", str(proj_root))
        info = wired.create_session(project_id=p["id"])

        result = wired.list_session_workspace(info["id"])
        assert result["root"] == str(proj_root)
        assert result["source"] == "project"
        assert result["custom"] is True
        assert result["project_id"] == p["id"]

    def test_session_override_wins_over_project(self, wired, projects, tmp_path):
        proj_root = tmp_path / "proj_ws"
        proj_root.mkdir()
        override = tmp_path / "override"
        override.mkdir()
        p = projects.create_project("P", str(proj_root))
        info = wired.create_session(project_id=p["id"])

        ok, _payload = wired.bind_workspace_root(info["id"], str(override))
        assert ok
        result = wired.list_session_workspace(info["id"])
        assert result["root"] == str(override)
        assert result["source"] == "session"

    def test_default_when_no_project(self, wired, tmp_path):
        info = wired.create_session()
        result = wired.list_session_workspace(info["id"])
        assert result["source"] == "default"
        assert result["custom"] is False
        assert result["root"] == str(tmp_path / "sessions" / info["id"] / "workspace")

    def test_unknown_project_falls_back_to_default(self, wired):
        info = wired.create_session(project_id="proj_nope")
        assert wired.list_session_workspace(info["id"])["source"] == "default"

    def test_without_resolver_falls_back_to_default(self, manager, tmp_path):
        info = manager.create_session(project_id="proj_1")
        assert manager.get_session_workspace_dir(info["id"]) == Path(
            str(tmp_path / "sessions" / info["id"] / "workspace")
        )

    def test_resolver_failure_treated_as_unbound(self, manager):
        def _boom(_project_id):
            raise RuntimeError("resolver down")

        manager.set_project_root_resolver(_boom)
        info = manager.create_session(project_id="proj_1")
        assert manager.list_session_workspace(info["id"])["source"] == "default"
