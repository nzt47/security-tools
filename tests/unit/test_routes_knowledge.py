"""任务6 · 知识库 API 路由测试

覆盖：卡片 CRUD（列表/详情/创建/更新/删除）、index、lint、graph、query，
及错误契约（404/422/409/400/503 均返回 JSON，不抛 HTML）。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from flask import Flask

from agent.knowledge.card import CardStore
from agent.knowledge.schema import slugify
from agent.server_routes.routes_knowledge import register_routes


def _card_dict(title: str = "Test Card", **overrides) -> dict:
    """构造合法卡片 body（必填字段齐全）。"""
    card = {
        "title": title,
        "slug": slugify(title),
        "status": "current",
        "type": "concepts",
        "source": "manual",
        "date": "2026-08-01",
        "tags": [],
        "links": [],
        "contradictions": [],
        "insight": "一句话核心洞见",
        "scope": "knowledge",
        "content": "",
        "metadata": {},
    }
    card.update(overrides)
    return card


@pytest.fixture
def kb_env(tmp_path):
    """临时知识库布局 + 最小 Flask app + test_client。

    require_token 打桩为恒等函数（鉴权不在本测试范围）。
    """
    kb = tmp_path / "kb"
    store = CardStore(
        kb / "wiki",
        archives_dir=kb / "archives",
        index_path=kb / "index.md",
        log_path=kb / "log.md",
        links_index_path=kb / "index_links.md",
    )
    return _make_client(store)


def _make_client(store):
    Yunshu = type("_Yunshu", (), {"_card_store": store})()
    state = type("_State", (), {"Yunshu": Yunshu})()
    app = Flask(__name__)
    app.config.update(TESTING=True)
    with patch("agent.server_routes.routes_knowledge.require_token", lambda f: f):
        register_routes(app, state)
    return app.test_client(), store


def _json(resp):
    return resp.get_json()


# ═══════════════ 列表 ═══════════════

def test_list_cards_empty(kb_env):
    client, _ = kb_env
    resp = client.get("/api/knowledge/cards")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["ok"] is True
    assert data["cards"] == []
    assert data["count"] == 0


def test_list_cards_uses_cache(kb_env, monkeypatch):
    """列表路由经 use_cache=True 读取（读路径走内存缓存）。"""
    client, store = kb_env
    captured: dict = {}
    original = store.list

    def spy(*args, **kwargs):
        captured["kwargs"] = kwargs
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "list", spy)
    resp = client.get("/api/knowledge/cards")
    assert resp.status_code == 200
    assert captured["kwargs"].get("use_cache") is True


def test_list_cards_filter_by_status_and_type(kb_env):
    client, _ = kb_env
    client.post("/api/knowledge/cards", json=_card_dict("Card A", type="concepts"))
    client.post("/api/knowledge/cards", json=_card_dict("Card B", type="entities", status="draft"))

    resp = client.get("/api/knowledge/cards?status=draft")
    assert resp.status_code == 200
    cards = _json(resp)["cards"]
    assert [c["slug"] for c in cards] == [slugify("Card B")]

    resp = client.get("/api/knowledge/cards?type=concepts")
    cards = _json(resp)["cards"]
    assert [c["slug"] for c in cards] == [slugify("Card A")]


def test_list_cards_unknown_type_filter_returns_empty(kb_env):
    """CardStore.list 的 type 过滤不校验合法性：未知 type 返回空列表（200）。"""
    client, _ = kb_env
    client.post("/api/knowledge/cards", json=_card_dict("Card A"))
    resp = client.get("/api/knowledge/cards?type=not-a-type")
    assert resp.status_code == 200
    assert _json(resp)["cards"] == []


# ═══════════════ 详情 ═══════════════

def test_get_card_detail_with_links_contradictions_incoming(kb_env):
    client, _ = kb_env
    client.post("/api/knowledge/cards", json=_card_dict(
        "Alpha", slug="alpha", links=["beta"],
        contradictions=[{"target_slug": "beta", "status": "conflict", "summary": "待裁决"}],
    ))
    client.post("/api/knowledge/cards", json=_card_dict("Beta", slug="beta"))

    resp = client.get("/api/knowledge/cards/alpha")
    assert resp.status_code == 200
    card = _json(resp)["card"]
    assert card["slug"] == "alpha"
    assert card["links"] == ["beta"]
    assert card["contradictions"][0]["target_slug"] == "beta"
    # 入链：beta 无入链，alpha 被 beta 引用？alpha.links=[beta] 是出链；
    # 再验证 beta 的入链包含 alpha
    resp = client.get("/api/knowledge/cards/beta")
    assert _json(resp)["card"]["incoming_links"] == ["alpha"]


def test_get_card_detail_not_found_404(kb_env):
    client, _ = kb_env
    resp = client.get("/api/knowledge/cards/nope")
    assert resp.status_code == 404
    assert "error" in _json(resp)


def test_get_card_detail_explicit_slug_not_leaked(kb_env):
    """explicit_slug 仅内存标记，不进入 API 响应。"""
    client, _ = kb_env
    client.post("/api/knowledge/cards", json=_card_dict("Alpha", slug="alpha"))
    resp = client.get("/api/knowledge/cards/alpha")
    assert "explicit_slug" not in _json(resp)["card"]


# ═══════════════ 创建 ═══════════════

def test_create_card_201(kb_env):
    client, store = kb_env
    resp = client.post("/api/knowledge/cards", json=_card_dict("Alpha", slug="alpha"))
    assert resp.status_code == 201
    assert _json(resp)["card"]["slug"] == "alpha"
    assert store.get("alpha") is not None


def test_create_card_conflict_409(kb_env):
    client, _ = kb_env
    client.post("/api/knowledge/cards", json=_card_dict("Alpha", slug="alpha"))
    resp = client.post("/api/knowledge/cards", json=_card_dict("Alpha", slug="alpha"))
    assert resp.status_code == 409
    assert "error" in _json(resp)


def test_create_card_missing_required_field_422(kb_env):
    client, _ = kb_env
    body = _card_dict("No Date")
    body.pop("date")
    resp = client.post("/api/knowledge/cards", json=body)
    assert resp.status_code == 422
    data = _json(resp)
    assert "violations" in data
    assert any("date" in v for v in data["violations"])


def test_create_card_invalid_status_422(kb_env):
    client, _ = kb_env
    resp = client.post("/api/knowledge/cards", json=_card_dict("Bad", status="banana"))
    assert resp.status_code == 422
    assert "violations" in _json(resp)


def test_create_card_non_dict_400(kb_env):
    client, _ = kb_env
    resp = client.post("/api/knowledge/cards", json="not-an-object")
    assert resp.status_code == 400
    assert "error" in _json(resp)


# ═══════════════ 更新 ═══════════════

def test_update_card_fields(kb_env):
    client, _ = kb_env
    client.post("/api/knowledge/cards", json=_card_dict("Alpha", slug="alpha"))
    resp = client.patch("/api/knowledge/cards/alpha", json={"insight": "新洞见"})
    assert resp.status_code == 200
    assert _json(resp)["card"]["insight"] == "新洞见"


def test_update_card_transition(kb_env):
    client, _ = kb_env
    client.post("/api/knowledge/cards", json=_card_dict("Alpha", slug="alpha", status="draft"))
    resp = client.patch("/api/knowledge/cards/alpha", json={"transition": "current"})
    assert resp.status_code == 200
    assert _json(resp)["card"]["status"] == "current"


def test_update_card_invalid_transition_409(kb_env):
    client, _ = kb_env
    client.post("/api/knowledge/cards", json=_card_dict("Alpha", slug="alpha", status="archive"))
    resp = client.patch("/api/knowledge/cards/alpha", json={"transition": "current"})
    assert resp.status_code == 409
    assert "error" in _json(resp)


def test_update_card_invalid_field_422(kb_env):
    client, _ = kb_env
    client.post("/api/knowledge/cards", json=_card_dict("Alpha", slug="alpha"))
    resp = client.patch("/api/knowledge/cards/alpha", json={"status": "banana"})
    assert resp.status_code == 422
    assert "violations" in _json(resp)


def test_update_card_not_found_404(kb_env):
    client, _ = kb_env
    resp = client.patch("/api/knowledge/cards/nope", json={"insight": "x"})
    assert resp.status_code == 404


# ═══════════════ 删除 ═══════════════

def test_delete_card_success(kb_env):
    client, store = kb_env
    client.post("/api/knowledge/cards", json=_card_dict("Alpha", slug="alpha"))
    resp = client.delete("/api/knowledge/cards/alpha")
    assert resp.status_code == 200
    assert _json(resp)["deleted"] == "alpha"
    assert store.get("alpha") is None


def test_delete_card_with_incoming_links_409(kb_env):
    """用户场景：A 通过正文双链指向 B，删除 B 应 409 并返回入链列表。"""
    client, _ = kb_env
    # 通过正文双链建卡（create 会解析正文 → links 登记入链索引）
    client.post("/api/knowledge/cards", json=_card_dict("Alpha", slug="alpha", content="参见 [[beta]]"))
    client.post("/api/knowledge/cards", json=_card_dict("Beta", slug="beta"))

    resp = client.delete("/api/knowledge/cards/beta")
    assert resp.status_code == 409
    data = _json(resp)
    assert data["incoming_links"] == ["alpha"]


def test_delete_card_not_found_404(kb_env):
    client, _ = kb_env
    resp = client.delete("/api/knowledge/cards/nope")
    assert resp.status_code == 404


# ═══════════════ index / lint / graph ═══════════════

def test_index_content(kb_env):
    client, store = kb_env
    store._index_path.parent.mkdir(parents=True, exist_ok=True)
    store._index_path.write_text("# 知识库索引\n- [[alpha]]\n", encoding="utf-8")
    resp = client.get("/api/knowledge/index")
    assert resp.status_code == 200
    assert "[[alpha]]" in _json(resp)["content"]


def test_index_missing_404(kb_env):
    client, _ = kb_env
    resp = client.get("/api/knowledge/index")
    assert resp.status_code == 404


def test_lint_report_interlinked_cards_score_100(kb_env):
    """两张互链卡片：无孤儿/断链 → 健康分 100。"""
    client, _ = kb_env
    client.post("/api/knowledge/cards", json=_card_dict("Alpha", slug="alpha", content="指向 [[beta]]"))
    client.post("/api/knowledge/cards", json=_card_dict("Beta", slug="beta", content="指向 [[alpha]]"))
    resp = client.get("/api/knowledge/lint")
    assert resp.status_code == 200
    report = _json(resp)["report"]
    assert report["total_cards"] == 2
    assert report["health_score"] == 100.0
    assert report["orphans"] == []


def test_lint_report_orphan_deduction(kb_env):
    """单张无链接卡片是孤儿 → 扣 2 分 → 98.0。"""
    client, _ = kb_env
    client.post("/api/knowledge/cards", json=_card_dict("Alpha", slug="alpha"))
    resp = client.get("/api/knowledge/lint")
    report = _json(resp)["report"]
    assert report["health_score"] == 98.0
    assert "alpha" in report["orphans"]


def test_graph_nodes_and_edges(kb_env):
    client, _ = kb_env
    client.post("/api/knowledge/cards", json=_card_dict("Alpha", slug="alpha", links=["beta"]))
    client.post("/api/knowledge/cards", json=_card_dict("Beta", slug="beta"))
    resp = client.get("/api/knowledge/graph")
    assert resp.status_code == 200
    data = _json(resp)
    assert {n["id"] for n in data["nodes"]} == {"alpha", "beta"}
    assert {"source": "alpha", "target": "beta"} in data["edges"]
    assert all(n["status"] in ("current", "draft", "archive", "unknown") for n in data["nodes"])


def test_graph_filters_non_wiki_edges(kb_env):
    """指向 archives/ 或不存在卡片的链接不产生边。"""
    client, _ = kb_env
    client.post("/api/knowledge/cards", json=_card_dict("Alpha", slug="alpha", links=["archives/old", "ghost"]))
    resp = client.get("/api/knowledge/graph")
    assert _json(resp)["edges"] == []


# ═══════════════ query ═══════════════

def test_query_hits(kb_env):
    client, _ = kb_env
    client.post("/api/knowledge/cards", json=_card_dict(
        "RRF Fusion", slug="rrf-fusion", content="Reciprocal Rank Fusion 融合多路召回。",
    ))
    resp = client.post("/api/knowledge/query", json={"question": "RRF 融合"})
    assert resp.status_code == 200
    data = _json(resp)
    assert data["ok"] is True
    assert len(data["hits"]) >= 1
    assert data["hits"][0]["slug"] == "rrf-fusion"


def test_query_empty_question_400(kb_env):
    client, _ = kb_env
    resp = client.post("/api/knowledge/query", json={"question": "   "})
    assert resp.status_code == 400


# ═══════════════ 503 守卫 ═══════════════

def test_store_required_returns_503(tmp_path):
    """CardStore 未初始化 → 全部路由返回 503 JSON。"""
    client, _ = _make_client(None)
    assert client.get("/api/knowledge/cards").status_code == 503
    assert client.get("/api/knowledge/cards/alpha").status_code == 503
    assert client.post("/api/knowledge/cards", json=_card_dict()).status_code == 503
    assert client.patch("/api/knowledge/cards/alpha", json={}).status_code == 503
    assert client.delete("/api/knowledge/cards/alpha").status_code == 503
    assert client.get("/api/knowledge/index").status_code == 503
    assert client.get("/api/knowledge/lint").status_code == 503
    assert client.get("/api/knowledge/graph").status_code == 503
    resp = client.get("/api/knowledge/cards")
    assert "error" in resp.get_json()
