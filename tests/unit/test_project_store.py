"""ProjectStore 单元测试（项目存储：项目 → 工作空间根目录）。

覆盖：新建 / 查询 / 改名 / 改工作空间 / 删除、跨实例持久化、坏文件降级。
【职责边界】本类不碰文件系统（除自建索引文件）—— 目录校验在路由层，故这里的
workspace_root 原样存取，不做 exists 判定。
"""
import json

import pytest

from agent.session_manager import ProjectStore


@pytest.fixture
def store(tmp_path):
    return ProjectStore(sessions_dir=str(tmp_path / "sessions"))


class TestProjectCrud:
    def test_list_empty(self, store):
        assert store.list_projects() == []

    def test_create_project(self, store, tmp_path):
        root = str(tmp_path / "ws")
        p = store.create_project("项目A", root)
        assert p["id"].startswith("proj_")
        assert p["name"] == "项目A"
        assert p["workspace_root"] == root
        assert "created_at" in p and "updated_at" in p
        assert store.get_project(p["id"])["name"] == "项目A"

    def test_create_default_name(self, store, tmp_path):
        assert store.create_project("   ", str(tmp_path / "ws"))["name"] == "新项目"

    def test_name_trim_and_cap(self, store, tmp_path):
        raw = "  很长的项目名" + "字" * 80
        assert store.create_project(raw, str(tmp_path / "ws"))["name"] == raw.strip()[:40]

    def test_update_name_and_root(self, store, tmp_path):
        p = store.create_project("A", str(tmp_path / "a"))
        updated = store.update_project(p["id"], name="B", workspace_root=str(tmp_path / "b"))
        assert updated["name"] == "B"
        assert updated["workspace_root"] == str(tmp_path / "b")

    def test_update_only_given_fields(self, store, tmp_path):
        p = store.create_project("A", str(tmp_path / "a"))
        # 只改名 → 工作空间不动
        updated = store.update_project(p["id"], name="B")
        assert updated["workspace_root"] == str(tmp_path / "a")

    def test_update_unknown_returns_none(self, store):
        assert store.update_project("proj_nope", name="x") is None

    def test_delete(self, store, tmp_path):
        p = store.create_project("A", str(tmp_path / "a"))
        assert store.delete_project(p["id"]) is True
        assert store.get_project(p["id"]) is None
        assert store.delete_project(p["id"]) is False

    def test_workspace_root_for(self, store, tmp_path):
        p = store.create_project("A", str(tmp_path / "a"))
        assert store.workspace_root_for(p["id"]) == str(tmp_path / "a")
        assert store.workspace_root_for("proj_nope") is None


class TestPersistence:
    def test_reload_from_disk(self, tmp_path):
        d = str(tmp_path / "sessions")
        p = ProjectStore(sessions_dir=d).create_project("A", str(tmp_path / "a"))
        s2 = ProjectStore(sessions_dir=d)
        assert s2.get_project(p["id"])["name"] == "A"
        raw = json.loads((tmp_path / "sessions" / "projects.json").read_text(encoding="utf-8"))
        assert len(raw["projects"]) == 1

    def test_corrupted_file_degrades(self, tmp_path):
        d = tmp_path / "sessions"
        d.mkdir()
        (d / "projects.json").write_text("{not json", encoding="utf-8")
        store = ProjectStore(sessions_dir=str(d))
        assert store.list_projects() == []
