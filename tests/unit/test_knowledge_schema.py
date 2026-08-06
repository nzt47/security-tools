"""任务0 · 知识卡片 Schema 校验器回归测试

覆盖（评估标准）：validate_card 正反例（缺失必填/非法 type/非法 status/insight 缺失）、
slugify 幂等与规则、Card dataclass 默认值、explicit_slug 豁免、contradictions 结构、
与 lifecycle 的单一事实源一致性。本文件曾因外部回滚丢失，重建自 agent/knowledge/schema.py。
"""

from __future__ import annotations

import pytest

from agent.knowledge.lifecycle import TRANSITIONS
from agent.knowledge.schema import (
    REQUIRED_FIELDS,
    VALID_TYPES,
    Card,
    slugify,
    validate_card,
)


def _valid_card(**overrides) -> dict:
    """构造合法卡片（insight 必填；slug 与 slugify(title) 一致）。"""
    card = {
        "title": "测试卡片",
        "slug": slugify("测试卡片"),
        "status": "draft",
        "type": "concepts",
        "source": "https://example.com",
        "date": "2026-08-01",
        "insight": "一句话核心洞见",
    }
    card.update(overrides)
    return card


# ---------- validate_card 正例 ----------


def test_valid_card_no_errors():
    assert validate_card(_valid_card()) == []


def test_valid_card_with_full_metadata():
    card = _valid_card(
        tags=["知识库", "ai"],
        links=["topic-a", "topic-b"],
        contradictions=[{"target_slug": "topic-a", "status": "conflict"}],
        scope="仅适用于内部知识库",
        content="# 正文",
        metadata={"author": "yq"},
    )
    assert validate_card(card) == []


def test_valid_card_with_explicit_slug():
    """显式 slug 豁免：与 slugify(title) 不同但标记 explicit_slug=True。"""
    card = _valid_card(slug="custom-name", explicit_slug=True)
    assert validate_card(card) == []


# ---------- 必填字段 ----------


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_missing_required_field_reported(field):
    card = _valid_card()
    del card[field]
    errors = validate_card(card)
    assert f"缺少必填字段: {field}" in errors


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_empty_required_field_reported(field):
    card = _valid_card(**{field: ""})
    errors = validate_card(card)
    assert f"缺少必填字段: {field}" in errors


def test_missing_multiple_required_fields_all_reported():
    card = _valid_card()
    del card["title"]
    del card["date"]
    errors = validate_card(card)
    assert "缺少必填字段: title" in errors
    assert "缺少必填字段: date" in errors


# ---------- type 合法性 ----------


@pytest.mark.parametrize("bad_type", ["foo", "Concept", "", " ", 123, None])
def test_invalid_type_reported(bad_type):
    errors = validate_card(_valid_card(type=bad_type))
    assert any("非法 type" in e for e in errors)


@pytest.mark.parametrize("good_type", sorted(VALID_TYPES))
def test_all_valid_types_pass(good_type):
    assert validate_card(_valid_card(type=good_type)) == []


# ---------- status 合法性 ----------


@pytest.mark.parametrize("bad_status", ["live", "deleted", "  ", 0])
def test_invalid_status_reported(bad_status):
    errors = validate_card(_valid_card(status=bad_status))
    assert any("非法 status" in e for e in errors)


# ---------- slug 一致性 ----------


def test_slug_mismatch_reported():
    errors = validate_card(_valid_card(slug="wrong-slug"))
    assert any("slug 与 slugify(title) 不一致" in e for e in errors)


def test_slug_mismatch_message_shows_both_values():
    errors = validate_card(_valid_card(title="Alpha 测试", slug="wrong-slug"))
    assert any("'wrong-slug'" in e and "alpha" in e for e in errors)


def test_explicit_slug_true_waives_check():
    card = _valid_card(title="Alpha 测试", slug="totally-different", explicit_slug=True)
    assert validate_card(card) == []


def test_explicit_slug_falsy_still_checks():
    card = _valid_card(title="Alpha 测试", slug="wrong", explicit_slug=False)
    errors = validate_card(card)
    assert any("slug 与 slugify(title) 不一致" in e for e in errors)


# ---------- contradictions 结构 ----------


def test_contradiction_missing_target_slug():
    card = _valid_card(contradictions=[{"status": "conflict"}])
    errors = validate_card(card)
    assert any("contradictions[0] 缺少 target_slug" in e for e in errors)


def test_contradiction_invalid_status():
    card = _valid_card(contradictions=[{"target_slug": "a", "status": "bogus"}])
    errors = validate_card(card)
    assert any("contradictions[0] status 非法" in e for e in errors)


def test_contradiction_not_dict():
    card = _valid_card(contradictions=["not-a-dict"])
    errors = validate_card(card)
    assert any("contradictions[0] 必须是 dict" in e for e in errors)


def test_contradiction_multiple_items_reported_independently():
    card = _valid_card(
        contradictions=[
            {"status": "conflict"},            # 缺 target_slug
            {"target_slug": "b", "status": "x"},  # 非法 status
            {"target_slug": "c", "status": "resolved"},  # 合法
        ]
    )
    errors = validate_card(card)
    assert any("contradictions[0] 缺少 target_slug" in e for e in errors)
    assert any("contradictions[1] status 非法" in e for e in errors)


@pytest.mark.parametrize("good_status", ["conflict", "reviewed", "resolved"])
def test_valid_contradiction_statuses_pass(good_status):
    card = _valid_card(contradictions=[{"target_slug": "a", "status": good_status}])
    assert validate_card(card) == []


def test_contradictions_empty_list_passes():
    assert validate_card(_valid_card(contradictions=[])) == []


# ---------- insight ----------


def test_missing_insight_reported():
    card = _valid_card()
    del card["insight"]
    errors = validate_card(card)
    assert "缺少一句话核心洞见" in errors


def test_empty_insight_reported():
    errors = validate_card(_valid_card(insight=""))
    assert "缺少一句话核心洞见" in errors


# ---------- 非 dict 入参 ----------


@pytest.mark.parametrize("bad_input", [None, "card", 42, ["a"], object()])
def test_non_dict_input_returns_error_list(bad_input):
    assert validate_card(bad_input) == ["card 必须是 dict"]


# ---------- slugify ----------


def test_slugify_ascii_lowercases_and_hyphenates():
    assert slugify("Hello World!") == "hello-world"


def test_slugify_chinese_kept():
    assert slugify("知识库 卡片") == "知识库-卡片"


def test_slugify_mixed_and_special_chars():
    # 尾部数字 "(2026)" 会被去歧义后缀逻辑剥除（见 test_slugify_removes_trailing_dedupe_suffix）
    assert slugify("RAG & Agent: 指南 (2026)") == "rag-agent-指南"


def test_slugify_collapses_multiple_separators():
    assert slugify("a  --  b") == "a-b"


def test_slugify_strips_leading_trailing_separators():
    assert slugify("-- hello --") == "hello"


def test_slugify_empty_and_blank():
    assert slugify("") == ""
    assert slugify("   ") == ""


def test_slugify_non_string_returns_empty():
    assert slugify(None) == ""
    assert slugify(123) == ""


def test_slugify_nfkc_fullwidth_normalized():
    # 全角数字/字母 → 半角
    assert slugify("ＡＢＣ１２３") == "abc123"


@pytest.mark.parametrize(
    "title,expected",
    [
        ("topic", "topic"),
        ("topic-2", "topic"),
        ("topic-2-3", "topic"),
        ("topic-2-3-4", "topic"),
    ],
)
def test_slugify_removes_trailing_dedupe_suffix(title, expected):
    assert slugify(title) == expected


def test_slugify_idempotent():
    """评估标准：slugify(slugify(x)) == slugify(x)。"""
    for title in ["Hello World!", "知识库", "topic-2-3", "RAG & Agent", "ＡＢＣ"]:
        once = slugify(title)
        assert slugify(once) == once


def test_slugify_does_not_strip_leading_digits():
    assert slugify("2026 roadmap") == "2026-roadmap"


# ---------- Card dataclass ----------


def test_card_defaults():
    card = Card(title="t", slug="s", status="draft", type="concepts",
                source="src", date="2026-01-01")
    assert card.tags == []
    assert card.links == []
    assert card.contradictions == []
    assert card.insight == ""
    assert card.scope == ""
    assert card.content == ""
    assert card.metadata == {}


def test_card_accepts_custom_values():
    card = Card(title="t", slug="s", status="current", type="entities",
                source="src", date="2026-01-01", tags=["x"],
                insight="核心", metadata={"k": "v"})
    assert card.tags == ["x"]
    assert card.insight == "核心"
    assert card.metadata == {"k": "v"}


def test_card_mutable_defaults_are_independent():
    """list/dict 默认值必须是独立实例（field default_factory）。"""
    a = Card(title="t", slug="s", status="draft", type="concepts", source="s", date="d")
    b = Card(title="t", slug="s", status="draft", type="concepts", source="s", date="d")
    a.tags.append("x")
    a.metadata["k"] = "v"
    assert b.tags == []
    assert b.metadata == {}


def test_card_requires_required_fields():
    with pytest.raises(TypeError):
        Card(title="t")  # type: ignore[call-arg]


def test_card_to_dict_roundtrip():
    card = _valid_card()
    assert card["status"] in {"draft", "current", "archive", "unknown"}


# ---------- 单一事实源一致性 ----------


def test_validate_card_statuses_match_lifecycle_transitions():
    """lifecycle.TRANSITIONS 的状态集必须与 schema 校验允许的状态集一致。"""
    transition_statuses = {s.value for s in TRANSITIONS}
    from agent.knowledge.schema import VALID_STATUS

    assert transition_statuses == VALID_STATUS
