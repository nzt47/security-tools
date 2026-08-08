"""知识库 API 路由测试（任务6 Step 4）。

覆盖全部路由的正常 / 404 / 422 / 409 / 503 分支：
    GET    /api/knowledge/cards         列表 + 过滤
    GET    /api/knowledge/cards/<slug>  详情（links / contradictions / incoming_links）
    POST   /api/knowledge/cards         创建（冲突 409 / schema 422）
    PATCH  /api/knowledge/cards/<slug>  更新 + 状态迁移 transition
    DELETE /api/knowledge/cards/<slug>  删除（有入链 409）
    GET    /api/knowledge/index         index.md 内容
    GET    /api/knowledge/lint          健康报告
    GET    /api/knowledge/graph         节点-边
    POST   /api/knowledge/query         融合检索（任务4 回归）
"""

from __future__ import annotations

import pytest
from flask import Flask

from agent.knowledge import Card, CardStore, slugify
from agent.server_routes.routes_knowledge import register_routes


def _card_dict(title: str, **overrides) -> dict:
    """构造合法卡片 body（通过 schema 校验）。"""
    data = {
        "title": title,
        "slug": slugify(title),
        "status": "current",
        "type": "concepts",
        "source": "tests",
        "date": "2026-08-08",
        "insight": f"{title} 的一句话核心洞见",
        "content": f"{title} 正文内容",
    }
    data.update(overrides)
    return data


@pytest.fixture()
def kb_env(tmp_path):
    """构造 Flask app + 临时 CardStore + 测试客户端。

    每个测试独立 app（register_routes 闭包内的 _searcher 懒加载单例
    与 store 绑定，独立构造避免跨测试复用污染）。
    """
    store = CardStore(
        tmp_path / "wiki",
        archives_dir=tmp_path / "archives",
        index_path=tmp_path / "index.md",
        log_path=tmp_path / "log.md",
        links_index_path=tmp_path / "index_links.md",
    )

    class _Yunshu:
        _card_store = store
        _vector_memory = None

    class _State:
        Yunshu = _Yunshu

    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_routes(app, _State())
    client = app.test_client()

    env = {"store": store, "client": client, "tmp_path": tmp_path}
    yield env


def _no_store_env():
    """无 CardStore 的 state（验证 503 分支）。"""

    class _Yunshu:
        pass

    class _State:
        Yunshu = _Yunshu

    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_routes(app, _State())
    return app.test_client()


# ═══════════════════════════════════════════════════════════
#  列表
# ═══════════════════════════════════════════════════════════


def test_list_cards_empty(kb_env):
    resp = kb_env["client"].get("/api/knowledge/cards")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["cards"] == []
    assert data["count"] == 0


def test_list_cards_with_filter(kb_env):
    store, client = kb_env["store"], kb_env["client"]
    store.create(Card(**_card_dict("概念一", type="concepts")))
    store.create(Card(**_card_dict("实体一", type="entities")))
    store.create(Card(**_card_dict("草稿卡", type="insights", status="draft")))

    resp = client.get("/api/knowledge/cards")
    assert resp.status_code == 200
    assert resp.get_json()["count"] == 3

    resp = client.get("/api/knowledge/cards?type=entities")
    data = resp.get_json()
    assert data["count"] == 1
    assert data["cards"][0]["slug"] == slugify("实体一")

    resp = client.get("/api/knowledge/cards?status=draft")
    data = resp.get_json()
    assert data["count"] == 1
    assert data["cards"][0]["slug"] == slugify("草稿卡")


def test_list_cards_unknown_type_filter_returns_empty(kb_env):
    # 未知 type 过滤 → 列表为空（CardStore.list 不校验、仅过滤）
    resp = kb_env["client"].get("/api/knowledge/cards?type=不存在类型")
    assert resp.status_code == 200
    assert resp.get_json()["cards"] == []


# ═══════════════════════════════════════════════════════════
#  详情
# ═══════════════════════════════════════════════════════════


def test_get_card_detail(kb_env):
    store, client = kb_env["store"], kb_env["client"]
    store.create(Card(**_card_dict(
        "被引卡片",
        links=["引用卡"],  # create 不解析正文双链，需显式传入
        contradictions=[{"target_slug": "其他卡", "status": "conflict", "summary": "有矛盾"}],
    )))
    store.create(Card(**_card_dict("引用卡", links=["被引卡片"])))

    resp = client.get(f"/api/knowledge/cards/{slugify('被引卡片')}")
    assert resp.status_code == 200
    card = resp.get_json()["card"]
    assert card["slug"] == slugify("被引卡片")
    assert "引用卡" in card["links"]
    assert card["contradictions"][0]["status"] == "conflict"
    # incoming_links：引用卡指向被引卡片
    assert slugify("引用卡") in card["incoming_links"]
    # explicit_slug 不应暴露到 API
    assert "explicit_slug" not in card


def test_get_card_not_found_404(kb_env):
    resp = kb_env["client"].get("/api/knowledge/cards/不存在")
    assert resp.status_code == 404
    assert "error" in resp.get_json()


# ═══════════════════════════════════════════════════════════
#  创建
# ═══════════════════════════════════════════════════════════


def test_create_card_201(kb_env):
    client = kb_env["client"]
    resp = client.post("/api/knowledge/cards", json=_card_dict("新建卡"))
    assert resp.status_code == 201
    card = resp.get_json()["card"]
    assert card["slug"] == slugify("新建卡")
    assert card["status"] == "current"


def test_create_card_conflict_409(kb_env):
    store, client = kb_env["store"], kb_env["client"]
    store.create(Card(**_card_dict("重复卡")))
    resp = client.post("/api/knowledge/cards", json=_card_dict("重复卡"))
    assert resp.status_code == 409
    assert "已存在" in resp.get_json()["error"]


def test_create_card_schema_invalid_422(kb_env):
    client = kb_env["client"]
    resp = client.post("/api/knowledge/cards", json={"title": "缺字段卡"})
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["violations"]  # 违规项列表非空
    assert any("缺少必填字段" in v for v in body["violations"])


def test_create_card_invalid_status_422(kb_env):
    client = kb_env["client"]
    resp = client.post("/api/knowledge/cards", json=_card_dict("坏状态卡", status="bogus"))
    assert resp.status_code == 422
    assert any("非法 status" in v for v in resp.get_json()["violations"])


def test_create_card_non_dict_body_400(kb_env):
    resp = kb_env["client"].post("/api/knowledge/cards", json=["not", "a", "dict"])
    assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════
#  更新
# ═══════════════════════════════════════════════════════════


def test_patch_card_fields(kb_env):
    store, client = kb_env["store"], kb_env["client"]
    store.create(Card(**_card_dict("更新卡", content="旧正文")))
    resp = client.patch(
        f"/api/knowledge/cards/{slugify('更新卡')}",
        json={"content": "新正文", "tags": ["tag-a"]},
    )
    assert resp.status_code == 200
    card = resp.get_json()["card"]
    assert card["content"] == "新正文"
    assert card["tags"] == ["tag-a"]


def test_patch_card_transition(kb_env):
    store, client = kb_env["store"], kb_env["client"]
    store.create(Card(**_card_dict("迁移卡", status="draft")))
    resp = client.patch(
        f"/api/knowledge/cards/{slugify('迁移卡')}", json={"transition": "current"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["card"]["status"] == "current"


def test_patch_card_invalid_transition_409(kb_env):
    store, client = kb_env["store"], kb_env["client"]
    store.create(Card(**_card_dict("终态卡", status="archive")))
    # archive 是终态，不可再迁移 → 409
    resp = client.patch(
        f"/api/knowledge/cards/{slugify('终态卡')}", json={"transition": "draft"}
    )
    assert resp.status_code == 409
    assert "非法" in resp.get_json()["error"]


def test_patch_card_schema_invalid_422(kb_env):
    store, client = kb_env["store"], kb_env["client"]
    store.create(Card(**_card_dict("校验卡")))
    resp = client.patch(
        f"/api/knowledge/cards/{slugify('校验卡')}", json={"status": "bogus"}
    )
    assert resp.status_code == 422
    assert any("非法 status" in v for v in resp.get_json()["violations"])


def test_patch_card_not_found_404(kb_env):
    resp = kb_env["client"].patch(
        "/api/knowledge/cards/不存在", json={"content": "x"}
    )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════
#  删除
# ═══════════════════════════════════════════════════════════


def test_delete_card_success(kb_env):
    store, client = kb_env["store"], kb_env["client"]
    store.create(Card(**_card_dict("待删卡")))
    resp = client.delete(f"/api/knowledge/cards/{slugify('待删卡')}")
    assert resp.status_code == 200
    assert resp.get_json()["deleted"] == slugify("待删卡")
    assert store.get(slugify("待删卡")) is None


def test_delete_card_with_incoming_links_409(kb_env):
    store, client = kb_env["store"], kb_env["client"]
    store.create(Card(**_card_dict("被引用卡")))
    # 引用方 links 显式指向被引用卡（create 不解析正文双链）
    store.create(Card(**_card_dict("引用方", links=["被引用卡"])))

    resp = client.delete(f"/api/knowledge/cards/{slugify('被引用卡')}")
    assert resp.status_code == 409
    body = resp.get_json()
    assert "incoming_links" in body
    assert slugify("引用方") in body["incoming_links"]
    # 卡片未被删除
    assert store.get(slugify("被引用卡")) is not None


def test_delete_card_not_found_404(kb_env):
    resp = kb_env["client"].delete("/api/knowledge/cards/不存在")
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════
#  index / lint / graph
# ═══════════════════════════════════════════════════════════


def test_get_index_content(kb_env):
    store, client = kb_env["store"], kb_env["client"]
    store.create(Card(**_card_dict("索引卡")))
    resp = client.get("/api/knowledge/index")
    assert resp.status_code == 200
    content = resp.get_json()["content"]
    assert slugify("索引卡") in content


def test_get_index_not_found_404(kb_env):
    # 未创建卡片 → index.md 尚未生成
    resp = kb_env["client"].get("/api/knowledge/index")
    assert resp.status_code == 404


def test_get_lint_report(kb_env):
    store, client = kb_env["store"], kb_env["client"]
    # 互链两张卡 → 无孤儿 / 无断链 / 无漂移 / 无矛盾 → 健康分 100
    store.create(Card(**_card_dict("健康卡甲", links=["健康卡乙"])))
    store.create(Card(**_card_dict("健康卡乙", links=["健康卡甲"])))
    resp = client.get("/api/knowledge/lint")
    assert resp.status_code == 200
    report = resp.get_json()["report"]
    assert report["total_cards"] == 2
    assert report["health_score"] == 100.0
    assert report["orphans"] == []
    assert "suggestions" in report


def test_get_lint_scores_deduction(kb_env):
    store, client = kb_env["store"], kb_env["client"]
    # 3 张孤儿卡（无入链）→ 每张扣 2，封顶 20
    for i in range(3):
        store.create(Card(**_card_dict(f"孤儿{i}")))
    resp = client.get("/api/knowledge/lint")
    report = resp.get_json()["report"]
    assert report["health_score"] == 100.0 - 3 * 2


def test_get_graph(kb_env):
    store, client = kb_env["store"], kb_env["client"]
    store.create(Card(**_card_dict("节点甲")))
    store.create(Card(**_card_dict("节点乙", links=["节点甲", "archives/旧卡"])))
    resp = client.get("/api/knowledge/graph")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["nodes"]) == 2
    node_ids = {n["id"] for n in data["nodes"]}
    assert slugify("节点甲") in node_ids
    assert slugify("节点乙") in node_ids
    # 只保留指向 wiki 节点的纯 slug 边（archives/ 目标过滤）
    assert {"source": slugify("节点乙"), "target": slugify("节点甲")} in data["edges"]
    assert all(e["target"] in node_ids for e in data["edges"])


# ═══════════════════════════════════════════════════════════
#  检索（任务4 回归）+ 503
# ═══════════════════════════════════════════════════════════


def test_query_returns_hits(kb_env):
    store, client = kb_env["store"], kb_env["client"]
    store.create(Card(**_card_dict(
        "搜索引擎", content="这是一个关于搜索引擎原理的卡片"
    )))
    resp = client.post("/api/knowledge/query", json={"question": "搜索引擎"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert any(h["slug"] == slugify("搜索引擎") for h in data["hits"])


def test_query_empty_question_400(kb_env):
    resp = kb_env["client"].post("/api/knowledge/query", json={"question": ""})
    assert resp.status_code == 400


def test_routes_503_when_store_missing():
    client = _no_store_env()
    for method, path, body in (
        ("GET", "/api/knowledge/cards", None),
        ("GET", "/api/knowledge/cards/x", None),
        ("POST", "/api/knowledge/cards", {}),
        ("GET", "/api/knowledge/lint", None),
        ("GET", "/api/knowledge/graph", None),
    ):
        resp = client.open(path, method=method, json=body)
        assert resp.status_code == 503, f"{method} {path} 应返回 503"
