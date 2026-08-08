"""任务5 · 冲突裁决协议 回归测试

覆盖（评估标准）：
- mark_conflict / resolve_conflict / list_unresolved 全链路。
- resolve_conflict 后两卡片状态正确、被否卡片归档、log.md 登记、
  list_unresolved 不再包含该对。
- AI 不自动裁决：mark_conflict 仅标记（status=conflict），不归档不裁决。
"""

from __future__ import annotations

import pytest

from agent.knowledge.card import CardStore
from agent.knowledge.conflict import list_unresolved, mark_conflict, resolve_conflict
from agent.knowledge.schema import Card, slugify


def make_card(
    title: str = "卡片",
    slug: str = "",
    links=None,
    contradictions=None,
) -> Card:
    card = Card(
        title=title,
        slug=slug or slugify(title),
        status="current",
        type="concepts",
        source="inbox/test.md",
        date="2026-08-02",
        tags=[],
        links=links if links is not None else [],
        contradictions=contradictions if contradictions is not None else [],
        insight="一句话核心洞见",
    )
    card.content = ""
    return card


@pytest.fixture
def store(tmp_path):
    """临时知识库：CardStore(<tmp>/kb/wiki)。"""
    return CardStore(tmp_path / "kb" / "wiki")


# ---------- mark_conflict ----------


def test_mark_conflict_adds_entry(store):
    store.create(make_card("A", slug="a"))
    assert mark_conflict("a", "b", "观点相悖", card_store=store) is True
    card = store.get("a")
    assert {"target_slug": "b", "status": "conflict", "summary": "观点相悖"} in card.contradictions


def test_mark_conflict_idempotent(store):
    store.create(make_card("A", slug="a"))
    assert mark_conflict("a", "b", "s1", card_store=store) is True
    assert mark_conflict("a", "b", "s2", card_store=store) is False  # 幂等跳过
    assert len(store.get("a").contradictions) == 1


def test_mark_conflict_missing_source_false(store):
    assert mark_conflict("ghost", "b", "s", card_store=store) is False


def test_mark_conflict_does_not_auto_resolve(store):
    """AI 不自动裁决：mark 仅标记 conflict，不归档、不置 resolved。"""
    store.create(make_card("A", slug="a"))
    mark_conflict("a", "b", "s", card_store=store)
    entry = store.get("a").contradictions[0]
    assert entry["status"] == "conflict"
    assert "decision_slug" not in entry
    assert store.get("a") is not None  # 未被归档
    assert list_unresolved(store) != []  # 矛盾仍待裁决


# ---------- list_unresolved ----------


def test_list_unresolved_only_unresolved(store):
    store.create(make_card("A", slug="a", contradictions=[
        {"target_slug": "b", "status": "conflict", "summary": "s1"},
    ]))
    store.create(make_card("C", slug="c", contradictions=[
        {"target_slug": "x", "status": "resolved", "summary": "s2"},
    ]))
    unresolved = list_unresolved(store)
    assert {"source_slug": "a", "target_slug": "b", "summary": "s1"} in unresolved
    assert all(u["source_slug"] != "c" for u in unresolved)


# ---------- resolve_conflict ----------


def test_resolve_conflict_decision_is_source(store):
    """裁决卡 = source：target 归档、双方矛盾置 resolved、log 登记。"""
    store.create(make_card("A", slug="a", links=["b"], contradictions=[
        {"target_slug": "b", "status": "conflict", "summary": "矛盾"},
    ]))
    store.create(make_card("B", slug="b", links=["a"]))

    assert resolve_conflict("a", "b", "a", card_store=store) is True

    # 被否卡片 target 归档
    assert store.get("b") is None
    archived = store.get("archives/b")
    assert archived is not None and archived.status == "archive"

    # 裁决卡 source 仍在线，矛盾 resolved + 关联裁决卡
    card = store.get("a")
    entry = next(it for it in card.contradictions if it["target_slug"] == "b")
    assert entry["status"] == "resolved"
    assert entry["decision_slug"] == "a"

    # list_unresolved 不再包含该对
    assert list_unresolved(store) == []

    # log.md 登记
    log_text = store._log_path.read_text(encoding="utf-8")
    assert "resolve_conflict" in log_text
    assert "current → archive" in log_text  # transition 归档登记


def test_resolve_conflict_decision_is_target(store):
    """裁决卡 = target：source 归档、target 保留且矛盾 resolved。"""
    store.create(make_card("A", slug="a", links=["b"], contradictions=[
        {"target_slug": "b", "status": "conflict", "summary": "矛盾"},
    ]))
    store.create(make_card("B", slug="b", links=["a"]))

    assert resolve_conflict("a", "b", "b", card_store=store) is True

    assert store.get("a") is None
    # source 已归档，矛盾条目随卡片归档（status=resolved + 关联裁决卡）
    archived_a = store.get("archives/a")
    assert archived_a is not None and archived_a.status == "archive"
    entry = next(it for it in archived_a.contradictions if it["target_slug"] == "b")
    assert entry["status"] == "resolved"
    assert entry["decision_slug"] == "b"
    assert store.get("b") is not None  # 裁决卡保留在线
    assert list_unresolved(store) == []


def test_resolve_conflict_third_party_archives_both(store):
    """第三方裁决卡：双方均归档（均非 decision 一方）。"""
    store.create(make_card("A", slug="a", contradictions=[
        {"target_slug": "b", "status": "conflict", "summary": "s"},
    ]))
    store.create(make_card("B", slug="b"))

    assert resolve_conflict("a", "b", "decision-card", card_store=store) is True
    assert store.get("a") is None
    assert store.get("b") is None
    assert store.get("archives/a") is not None
    assert store.get("archives/b") is not None
    assert list_unresolved(store) == []


def test_resolve_conflict_missing_pair_false(store):
    store.create(make_card("A", slug="a"))
    assert resolve_conflict("a", "b", "a", card_store=store) is False


def test_resolve_conflict_missing_source_false(store):
    assert resolve_conflict("ghost", "b", "a", card_store=store) is False


def test_resolve_conflict_archived_contradiction_cleared_from_wiki(store):
    """裁决后被否卡片归档，其矛盾不再出现在 wiki 的 list_unresolved 中。"""
    store.create(make_card("A", slug="a", contradictions=[
        {"target_slug": "b", "status": "conflict", "summary": "s"},
    ]))
    store.create(make_card("B", slug="b", contradictions=[
        {"target_slug": "a", "status": "conflict", "summary": "反向"},
    ]))
    assert len(list_unresolved(store)) == 2

    resolve_conflict("a", "b", "a", card_store=store)
    # a 保留（resolved）、b 归档 → wiki 内无未裁决矛盾
    assert list_unresolved(store) == []
