"""SessionGroupStore 单元测试（会话分组存储）

覆盖：创建/查询/重命名/删除分组、会话归属（assign/remove_member）、
删除分组自动清空成员归属、实例间持久化、并发下的一致性。
"""
import json
import threading

import pytest

from agent.session_manager import SessionGroupStore


@pytest.fixture
def store(tmp_path):
    """使用临时目录的 SessionGroupStore"""
    return SessionGroupStore(sessions_dir=str(tmp_path / "sessions"))


class TestGroupCrud:
    def test_list_empty(self, store):
        result = store.list_groups()
        assert result["groups"] == []
        assert result["membership"] == {}

    def test_create_group(self, store):
        g = store.create_group("项目A")
        assert g["id"].startswith("grp_")
        assert g["name"] == "项目A"
        assert "created_at" in g

    def test_create_group_default_name(self, store):
        g = store.create_group("")
        assert g["name"] == "新分组"

    def test_create_group_trims_and_caps(self, store):
        raw = "  很长的名字" + "字" * 80
        g = store.create_group(raw)
        assert g["name"] == raw.strip()[:40]
        assert len(g["name"]) <= 40

    def test_list_groups_returns_all(self, store):
        store.create_group("A")
        store.create_group("B")
        assert len(store.list_groups()["groups"]) == 2

    def test_rename_group(self, store):
        g = store.create_group("旧名")
        assert store.rename_group(g["id"], "新名") is True
        assert store.get_group(g["id"])["name"] == "新名"

    def test_rename_group_missing(self, store):
        assert store.rename_group("grp_nope", "x") is False

    def test_rename_group_blank(self, store):
        g = store.create_group("A")
        assert store.rename_group(g["id"], "  ") is False

    def test_get_group_missing(self, store):
        assert store.get_group("grp_nope") is None


class TestMembership:
    def test_assign_and_list_membership(self, store):
        g = store.create_group("项目A")
        assert store.assign("sess-1", g["id"]) is True
        result = store.list_groups()
        assert result["membership"] == {"sess-1": g["id"]}
        # count 汇总
        assert result["groups"][0]["count"] == 1

    def test_assign_unknown_group_fails(self, store):
        assert store.assign("sess-1", "grp_nope") is False

    def test_unassign_with_none(self, store):
        g = store.create_group("项目A")
        store.assign("sess-1", g["id"])
        assert store.assign("sess-1", None) is True
        assert store.list_groups()["membership"] == {}

    def test_unassign_with_empty_string(self, store):
        g = store.create_group("项目A")
        store.assign("sess-1", g["id"])
        assert store.assign("sess-1", "") is True
        assert store.list_groups()["membership"] == {}

    def test_reassign(self, store):
        g1 = store.create_group("G1")
        g2 = store.create_group("G2")
        store.assign("sess-1", g1["id"])
        store.assign("sess-1", g2["id"])
        assert store.list_groups()["membership"]["sess-1"] == g2["id"]

    def test_remove_member_idempotent(self, store):
        g = store.create_group("项目A")
        store.assign("sess-1", g["id"])
        store.remove_member("sess-1")
        assert store.list_groups()["membership"] == {}
        store.remove_member("sess-1")  # 幂等
        assert store.list_groups()["membership"] == {}


class TestGroupDelete:
    def test_delete_group(self, store):
        g = store.create_group("项目A")
        store.create_group("项目B")
        assert store.delete_group(g["id"]) is True
        assert store.get_group(g["id"]) is None

    def test_delete_group_missing(self, store):
        assert store.delete_group("grp_nope") is False

    def test_delete_group_clears_membership(self, store):
        g = store.create_group("项目A")
        store.create_group("项目B")
        store.assign("sess-1", g["id"])
        store.assign("sess-2", g["id"])
        store.delete_group(g["id"])
        result = store.list_groups()
        assert result["membership"] == {}  # 成员自动变为未分组
        assert len(result["groups"]) == 1  # 只剩 项目B


class TestPersistence:
    def test_persistence_across_instances(self, tmp_path):
        dir_path = str(tmp_path / "sessions")
        s1 = SessionGroupStore(sessions_dir=dir_path)
        g = s1.create_group("项目A")
        s1.assign("sess-1", g["id"])

        s2 = SessionGroupStore(sessions_dir=dir_path)
        result = s2.list_groups()
        assert len(result["groups"]) == 1
        assert result["groups"][0]["name"] == "项目A"
        assert result["membership"] == {"sess-1": g["id"]}

    def test_corrupted_file_recovers(self, tmp_path):
        dir_path = tmp_path / "sessions"
        dir_path.mkdir(parents=True)
        (dir_path / "groups.json").write_text("not json", encoding="utf-8")
        store = SessionGroupStore(sessions_dir=str(dir_path))
        assert store.list_groups()["groups"] == []


class TestConcurrency:
    def test_concurrent_create_groups(self, store):
        """并发建组全部成功且 ID 唯一"""
        barrier = threading.Barrier(6)
        results = []

        def create():
            barrier.wait()
            results.append(store.create_group("并发组"))

        threads = [threading.Thread(target=create) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ids = {r["id"] for r in results}
        assert len(ids) == 6
        assert len(store.list_groups()["groups"]) == 6

    def test_concurrent_assign_same_session(self, store):
        """并发把同一会话放入不同组：最后写入胜出，文件不损坏"""
        g1 = store.create_group("G1")
        g2 = store.create_group("G2")
        barrier = threading.Barrier(4)

        def assign(gid):
            barrier.wait()
            store.assign("sess-1", gid)

        threads = [threading.Thread(target=assign, args=(g1["id"],)) for _ in range(2)]
        threads += [threading.Thread(target=assign, args=(g2["id"],)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        membership = store.list_groups()["membership"]
        assert membership["sess-1"] in (g1["id"], g2["id"])
        # 文件仍为合法 JSON
        assert json.loads(store._groups_path.read_text(encoding="utf-8"))["membership"] == membership
