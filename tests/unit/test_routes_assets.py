"""资产管理 API 路由单元测试

覆盖 agent/server_routes/routes_assets.py 的全部路由与辅助函数:
  - overview / list / add / delete（8 类资产，文件型 + 非文件型）
  - backup / backup list / backup delete / restore / export
  - _read_json_file / _write_json_file / _get_non_file_items 各分支

设计原则: 用 Flask test_client 真实触发路由, tmp_path + monkeypatch 隔离
ASSETS_DIR / BACKUPS_DIR（不触碰真实 data/ 目录）, state.skills_mgr 用
SimpleNamespace fake 对象, 无需真实子系统。
"""
# pylint: disable=redefined-outer-name,missing-function-docstring,protected-access

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask

from agent.server_routes import routes_assets
from agent.server_routes.routes_assets import (
    FILE_BASED_CATEGORIES,
    register_routes,
    _read_json_file,
    _write_json_file,
    _get_non_file_items,
)


def _write_asset(tmp_path, cat, items):
    """向测试用 ASSETS_DIR 写入指定类别的资产 JSON 文件"""
    path = tmp_path / "assets" / f"{cat}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """构建注册了资产路由的 Flask test_client, 数据目录隔离到 tmp_path"""
    monkeypatch.setattr(routes_assets, "ASSETS_DIR", tmp_path / "assets")
    monkeypatch.setattr(routes_assets, "BACKUPS_DIR", tmp_path / "backups")
    skills_mgr = SimpleNamespace(get_all=lambda: [
        {"id": "s1", "name": "技能1", "description": "d1", "enabled": True},
        {"id": "s2", "name": "技能2", "description": "d2", "enabled": False},
    ])
    state = SimpleNamespace(skills_mgr=skills_mgr)
    app = Flask(__name__)
    app.config["TESTING"] = True
    register_routes(app, state)
    return app.test_client()


# ═══════════════════════════════════════════════════════════
#  文件读写辅助函数
# ═══════════════════════════════════════════════════════════

class TestReadJsonFile:
    """_read_json_file 各分支"""

    def test_missing_file_returns_empty(self, tmp_path):
        """文件不存在时应返回空列表"""
        assert _read_json_file(tmp_path / "nope.json") == []

    def test_valid_json(self, tmp_path):
        """合法 JSON 应原样返回"""
        p = tmp_path / "a.json"
        p.write_text('[{"id": 1}]', encoding="utf-8")
        assert _read_json_file(p) == [{"id": 1}]

    def test_invalid_json_returns_empty(self, tmp_path):
        """JSON 解析失败应返回空列表"""
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert _read_json_file(p) == []

    def test_oserror_returns_empty(self, tmp_path):
        """读取抛 OSError 应返回空列表"""
        p = MagicMock()
        p.exists.return_value = True
        p.read_text.side_effect = OSError("io error")
        assert _read_json_file(p) == []


class TestWriteJsonFile:
    """_write_json_file"""

    def test_writes_file_with_parents(self, tmp_path):
        """应递归创建父目录并以 UTF-8 写入 JSON"""
        target = tmp_path / "nested" / "dir" / "x.json"
        _write_json_file(target, [{"a": 1}])
        assert json.loads(target.read_text(encoding="utf-8")) == [{"a": 1}]


class TestGetNonFileItems:
    """_get_non_file_items 非文件型资产获取"""

    def test_skills_with_manager(self):
        """skills 且 skills_mgr 存在时返回映射后的技能列表"""
        state = SimpleNamespace(skills_mgr=SimpleNamespace(get_all=lambda: [
            {"id": "s1", "name": "n", "description": "d", "enabled": True},
        ]))
        items = _get_non_file_items("skills", state)
        assert items == [{
            "id": "s1", "title": "n", "name": "n", "description": "d",
            "enabled": True, "category": "skills",
        }]

    def test_skills_without_manager(self):
        """skills 但 skills_mgr 为 None 时应返回空列表"""
        state = SimpleNamespace(skills_mgr=None)
        assert _get_non_file_items("skills", state) == []

    def test_non_skills_category(self):
        """非 skills 类别（未实现子系统对接）应返回空列表"""
        state = SimpleNamespace(skills_mgr=SimpleNamespace(get_all=lambda: []))
        assert _get_non_file_items("memory", state) == []

    def test_manager_raises_returns_empty(self):
        """skills_mgr 抛异常应被捕获并返回空列表"""
        def boom():
            raise RuntimeError("boom")
        state = SimpleNamespace(skills_mgr=SimpleNamespace(get_all=boom))
        assert _get_non_file_items("skills", state) == []


# ═══════════════════════════════════════════════════════════
#  overview / list
# ═══════════════════════════════════════════════════════════

class TestOverview:
    """GET /api/assets/overview"""

    def test_overview_counts(self, client, tmp_path):
        """应统计 8 类资产数量（文件型读 JSON, 非文件型走子系统）"""
        _write_asset(tmp_path, "habits", [{"id": "h1"}, {"id": "h2"}])
        _write_asset(tmp_path, "inspires", [])
        resp = client.get("/api/assets/overview")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        ov = data["overview"]
        assert ov["habits"] == 2
        assert ov["inspires"] == 0
        assert ov["skills"] == 2  # 来自 fake skills_mgr
        # total 是各分类数量之和（不含 total 键自身）
        assert ov["total"] == sum(v for k, v in ov.items() if k != "total")

    def test_overview_error(self, client, monkeypatch):
        """统计过程抛异常应返回 500"""
        monkeypatch.setattr(routes_assets, "_read_json_file",
                            MagicMock(side_effect=RuntimeError("boom")))
        resp = client.get("/api/assets/overview")
        assert resp.status_code == 500
        assert resp.get_json()["ok"] is False


class TestList:
    """GET /api/assets/<category>"""

    def test_file_based_list_fills_defaults(self, client, tmp_path):
        """文件类资产应补齐 id/title/type/category 默认值"""
        _write_asset(tmp_path, "habits", [
            {"name": "a"},                # 无 id/title → 补 id, title=name
            {"id": "h2", "target": "tg"}, # 无 title → title=target
            {"id": "h3"},                 # 无 title → title=id
            {"id": "h4", "title": "T4"},  # 已具备 → 不覆盖
        ])
        resp = client.get("/api/assets/habits")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 4
        items = data["items"]
        assert items[0]["id"] == "habits_0"
        assert items[0]["title"] == "a"
        assert items[0]["type"] == "habits"
        assert items[0]["category"] == "habits"
        assert items[1]["title"] == "tg"
        assert items[2]["title"] == "h3"
        assert items[3]["title"] == "T4"

    def test_non_file_list(self, client):
        """非文件类（skills）应走子系统获取并补默认字段"""
        resp = client.get("/api/assets/skills")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 2
        assert data["items"][0]["title"] == "技能1"
        assert data["items"][0]["type"] == "skills"
        assert data["items"][0]["category"] == "skills"

    def test_list_error(self, client, monkeypatch):
        """获取过程抛异常应返回 500"""
        monkeypatch.setattr(routes_assets, "_get_non_file_items",
                            MagicMock(side_effect=RuntimeError("boom")))
        resp = client.get("/api/assets/memory")
        assert resp.status_code == 500
        assert resp.get_json()["ok"] is False


# ═══════════════════════════════════════════════════════════
#  add / delete
# ═══════════════════════════════════════════════════════════

class TestAdd:
    """POST /api/assets/<category>"""

    def test_add_non_file_category_rejected(self, client):
        """非文件类不支持直接添加, 应返回 400"""
        resp = client.post("/api/assets/skills", json={"name": "x"})
        assert resp.status_code == 400
        assert "不支持" in resp.get_json()["error"]

    def test_add_success(self, client, tmp_path):
        """文件类添加成功应返回新项并落盘"""
        resp = client.post("/api/assets/habits", json={"name": "晨跑", "score": 3})
        assert resp.status_code == 200
        data = resp.get_json()
        item = data["item"]
        assert item["id"].startswith("habits_")
        assert item["created_at"]
        assert item["name"] == "晨跑"
        saved = json.loads((tmp_path / "assets" / "habits.json").read_text(encoding="utf-8"))
        assert len(saved) == 1
        assert saved[0]["id"] == item["id"]

    def test_add_error(self, client, monkeypatch):
        """写入失败应返回 500"""
        monkeypatch.setattr(routes_assets, "_write_json_file",
                            MagicMock(side_effect=OSError("disk full")))
        resp = client.post("/api/assets/habits", json={"name": "x"})
        assert resp.status_code == 500
        assert resp.get_json()["ok"] is False


class TestDelete:
    """DELETE /api/assets/<category>/<item_id>"""

    def test_delete_non_file_category_rejected(self, client):
        """非文件类不支持删除, 应返回 400"""
        resp = client.delete("/api/assets/skills/s1")
        assert resp.status_code == 400

    def test_delete_not_found(self, client, tmp_path):
        """删除不存在的项应返回 404"""
        _write_asset(tmp_path, "habits", [{"id": "h1"}])
        resp = client.delete("/api/assets/habits/nope")
        assert resp.status_code == 404
        assert resp.get_json()["ok"] is False

    def test_delete_by_id(self, client, tmp_path):
        """按 id 删除成功应返回 200 并更新文件"""
        _write_asset(tmp_path, "habits", [{"id": "h1"}, {"id": "h2"}])
        resp = client.delete("/api/assets/habits/h1")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        saved = json.loads((tmp_path / "assets" / "habits.json").read_text(encoding="utf-8"))
        assert [i["id"] for i in saved] == ["h2"]

    def test_delete_by_name_or_title(self, client, tmp_path):
        """无 id 的项可按 name / title 匹配删除"""
        _write_asset(tmp_path, "habits", [{"name": "n1"}, {"title": "t1"}])
        resp = client.delete("/api/assets/habits/n1")
        assert resp.status_code == 200
        resp = client.delete("/api/assets/habits/t1")
        assert resp.status_code == 200
        saved = json.loads((tmp_path / "assets" / "habits.json").read_text(encoding="utf-8"))
        assert saved == []

    def test_delete_error(self, client, monkeypatch):
        """读取失败应返回 500"""
        monkeypatch.setattr(routes_assets, "_read_json_file",
                            MagicMock(side_effect=RuntimeError("boom")))
        resp = client.delete("/api/assets/habits/h1")
        assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════
#  backup / restore / export
# ═══════════════════════════════════════════════════════════

class TestBackup:
    """POST /api/assets/backup"""

    def test_backup_all_file_categories(self, client, tmp_path):
        """未指定类别时应备份全部文件类资产"""
        _write_asset(tmp_path, "habits", [{"id": "h1"}])
        resp = client.post("/api/assets/backup", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        backup_id = data["backup_id"]
        assert backup_id.startswith("assets_backup_")
        backup_file = tmp_path / "backups" / f"{backup_id}.json"
        assert backup_file.exists()
        content = json.loads(backup_file.read_text(encoding="utf-8"))
        assert set(content.keys()) == FILE_BASED_CATEGORIES
        assert content["habits"] == [{"id": "h1"}]

    def test_backup_selected_categories_skips_non_file(self, client, tmp_path):
        """指定类别备份时应跳过非文件类（skills）"""
        _write_asset(tmp_path, "habits", [{"id": "h1"}])
        resp = client.post("/api/assets/backup", json={"categories": ["habits", "skills"]})
        assert resp.status_code == 200
        backup_file = tmp_path / "backups" / f"{resp.get_json()['backup_id']}.json"
        content = json.loads(backup_file.read_text(encoding="utf-8"))
        assert set(content.keys()) == {"habits"}

    def test_backup_error(self, client, monkeypatch):
        """读取资产失败应返回 500"""
        monkeypatch.setattr(routes_assets, "_read_json_file",
                            MagicMock(side_effect=RuntimeError("boom")))
        resp = client.post("/api/assets/backup", json={})
        assert resp.status_code == 500


class TestBackupList:
    """GET /api/assets/backup/list"""

    def test_empty_backups(self, client):
        """无备份时应返回空列表"""
        resp = client.get("/api/assets/backup/list")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["backups"] == []

    def test_list_with_backups(self, client, tmp_path):
        """有备份时应列出类别、文件数与大小（含静态路由优先匹配验证）"""
        _write_asset(tmp_path, "habits", [{"id": "h1"}, {"id": "h2"}])
        client.post("/api/assets/backup", json={})
        resp = client.get("/api/assets/backup/list")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1
        b = data["backups"][0]
        assert b["backup_id"].startswith("assets_backup_")
        assert set(b["categories"]) == FILE_BASED_CATEGORIES
        assert b["file_count"] == 2  # habits 2 条, 其余空
        assert b["size_bytes"] > 0

    def test_non_dict_backup_file(self, client, tmp_path):
        """备份文件内容非 dict（损坏/旧格式）时 categories/file_count 兜底为空"""
        backups = tmp_path / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        (backups / "assets_backup_x.json").write_text("[]", encoding="utf-8")
        resp = client.get("/api/assets/backup/list")
        assert resp.status_code == 200
        b = resp.get_json()["backups"][0]
        assert b["categories"] == []
        assert b["file_count"] == 0

    def test_list_error(self, client, tmp_path, monkeypatch):
        """备份目录不可用（指向已存在文件）时应返回 500"""
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(routes_assets, "BACKUPS_DIR", blocker)
        resp = client.get("/api/assets/backup/list")
        assert resp.status_code == 500
        assert resp.get_json()["ok"] is False


class TestBackupDelete:
    """DELETE /api/assets/backup/<backup_id>"""

    def test_delete_missing(self, client):
        """删除不存在的备份应返回 404"""
        resp = client.delete("/api/assets/backup/nope")
        assert resp.status_code == 404

    def test_delete_success(self, client, tmp_path):
        """删除成功应返回 200 并移除文件"""
        client.post("/api/assets/backup", json={})
        backup_file = next((tmp_path / "backups").glob("assets_backup_*.json"))
        resp = client.delete(f"/api/assets/backup/{backup_file.stem}")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        assert not backup_file.exists()

    def test_delete_error(self, client, tmp_path, monkeypatch):
        """文件删除抛异常应返回 500"""
        backups = tmp_path / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        (backups / "assets_backup_x.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(Path, "unlink", MagicMock(side_effect=OSError("denied")))
        resp = client.delete("/api/assets/backup/assets_backup_x")
        assert resp.status_code == 500


class TestRestore:
    """POST /api/assets/restore"""

    def test_missing_backup_id(self, client):
        """缺少 backup_id 应返回 400"""
        resp = client.post("/api/assets/restore", json={})
        assert resp.status_code == 400

    def test_backup_not_found(self, client):
        """备份不存在应返回 404"""
        resp = client.post("/api/assets/restore", json={"backup_id": "nope"})
        assert resp.status_code == 404

    def test_restore_success_skips_non_file(self, client, tmp_path):
        """恢复成功应写回文件类资产并跳过非文件类条目"""
        backups = tmp_path / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        (backups / "assets_backup_b1.json").write_text(json.dumps({
            "habits": [{"id": "h1"}],
            "memory": [{"id": "m1"}],  # 非文件类别 → 跳过
        }, ensure_ascii=False), encoding="utf-8")
        _write_asset(tmp_path, "habits", [{"id": "old"}])  # 制造被改坏的资产
        resp = client.post("/api/assets/restore", json={"backup_id": "assets_backup_b1"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["result"]["restored_files"] == ["habits"]
        assert data["result"]["restored_count"] == 1
        saved = json.loads((tmp_path / "assets" / "habits.json").read_text(encoding="utf-8"))
        assert saved == [{"id": "h1"}]
        assert not (tmp_path / "assets" / "memory.json").exists()

    def test_restore_corrupt_backup(self, client, tmp_path):
        """备份文件损坏（非法 JSON）应返回 500"""
        backups = tmp_path / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        (backups / "assets_backup_bad.json").write_text("{corrupt", encoding="utf-8")
        resp = client.post("/api/assets/restore", json={"backup_id": "assets_backup_bad"})
        assert resp.status_code == 500


class TestExport:
    """GET /api/assets/export"""

    def test_export_success(self, client, tmp_path):
        """导出应返回全部文件类资产并生成导出文件"""
        _write_asset(tmp_path, "habits", [{"id": "h1"}])
        resp = client.get("/api/assets/export")
        assert resp.status_code == 200
        exported = json.loads(resp.data.decode("utf-8"))
        assert set(exported.keys()) == FILE_BASED_CATEGORIES
        assert exported["habits"] == [{"id": "h1"}]
        export_files = list((tmp_path / "backups").glob("assets_export_*.json"))
        assert len(export_files) == 1

    def test_export_error(self, client, tmp_path, monkeypatch):
        """备份目录不可写（指向已存在文件）应返回 500"""
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(routes_assets, "BACKUPS_DIR", blocker)
        resp = client.get("/api/assets/export")
        assert resp.status_code == 500
        assert resp.get_json()["ok"] is False
