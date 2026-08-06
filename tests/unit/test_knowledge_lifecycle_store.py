"""任务2 · 生命周期状态机持久化 回归测试

覆盖（评估标准）：合法/非法迁移、Draft→Archive 直跳拒绝、Archive 物理移入
archives/、入链重写（links + 正文双链）且 parse_links 无死链、终态不可逆、
index/log.md 联动、不存在/损坏卡片防御。
"""

from __future__ import annotations

import pytest

from agent.knowledge.card import (
    CardNotFoundError,
    CardStore,
    InvalidTransitionError,
)
from agent.knowledge.links import find_broken_links, parse_links
from agent.knowledge.schema import Card, slugify


def make_card(
    title: str = "驾驭工程",
    status: str = "current",
    type: str = "concepts",
    content: str = "",
    links=None,
) -> Card:
    card = Card(
        title=title,
        slug=slugify(title),
        status=status,
        type=type,
        source="inbox/test.md",
        date="2026-08-02",
        tags=[],
        links=links if links is not None else [],
        insight="一句话核心洞见",
    )
    card.content = content
    return card


@pytest.fixture
def store(tmp_path):
    return CardStore(tmp_path / "kb" / "wiki")


# ---------- 非归档迁移 ----------


def test_transition_draft_to_current_updates_status(store, tmp_path):
    store.create(make_card("驾驭工程", status="draft"))
    path = tmp_path / "kb" / "wiki" / "concepts" / "驾驭工程.md"
    store.transition("驾驭工程", "current")
    assert store.get("驾驭工程").status == "current"
    assert path.exists()  # 【评估标准】非 Archive 迁移不改变文件路径


def test_transition_unknown_to_current(store):
    store.create(make_card("驾驭工程", status="unknown"))
    store.transition("驾驭工程", "current")
    assert store.get("驾驭工程").status == "current"


def test_transition_current_to_draft(store):
    store.create(make_card("驾驭工程", status="current"))
    store.transition("驾驭工程", "draft")
    assert store.get("驾驭工程").status == "draft"


# ---------- 非法迁移 ----------


def test_transition_draft_to_archive_rejected(store):
    """【评估标准】Draft→Archive 直跳被拒（须先经 Current）。"""
    store.create(make_card("驾驭工程", status="draft"))
    with pytest.raises(InvalidTransitionError):
        store.transition("驾驭工程", "archive")


def test_transition_illegal_target_value_raises(store):
    store.create(make_card("驾驭工程", status="current"))
    with pytest.raises(InvalidTransitionError):
        store.transition("驾驭工程", "bogus")


def test_transition_missing_slug_raises(store):
    with pytest.raises(CardNotFoundError):
        store.transition("不存在", "current")


def test_transition_corrupted_card_raises(store, tmp_path):
    p = tmp_path / "kb" / "wiki" / "concepts" / "坏卡.md"
    p.parent.mkdir(parents=True)
    p.write_text("无 frontmatter", encoding="utf-8")
    with pytest.raises(CardNotFoundError):
        store.transition("坏卡", "current")


def test_transition_invalid_current_status_raises(store, tmp_path):
    """卡片自身 status 非法 → 抛 InvalidTransitionError。"""
    p = tmp_path / "kb" / "wiki" / "concepts" / "怪卡.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        "---\ntitle: 怪卡\nslug: 怪卡\nstatus: bogus\ntype: concepts\n"
        "source: x\ndate: 2026-08-02\ninsight: 洞见\n---\n",
        encoding="utf-8",
    )
    with pytest.raises(InvalidTransitionError):
        store.transition("怪卡", "current")


# ---------- Archive 归档 ----------


def test_transition_current_to_archive_moves_file(store, tmp_path):
    """【评估标准】Archive 物理移入 archives/（wiki 文件消失）。"""
    store.create(make_card("驾驭工程", status="current"))
    wiki_path = tmp_path / "kb" / "wiki" / "concepts" / "驾驭工程.md"
    arch_path = tmp_path / "kb" / "archives" / "驾驭工程.md"
    store.transition("驾驭工程", "archive")
    assert not wiki_path.exists()
    assert arch_path.exists()
    archived = store.get("archives/驾驭工程")
    assert archived is not None
    assert archived.status == "archive"


def test_transition_archive_rewrites_incoming_links(store):
    """【评估标准】归档后全部入链改写为 archives 路径，parse_links 无死链。"""
    b = make_card(
        "提示词工程",
        content="参考 [[驾驭工程|驾驭]] 与 [[第一性原理]]",
        links=["驾驭工程", "第一性原理"],
    )
    store.create(b)
    store.create(make_card("第一性原理", type="insights"))
    store.create(make_card("驾驭工程", status="current"))
    store.transition("驾驭工程", "archive")

    b_after = store.get("提示词工程")
    assert "archives/驾驭工程" in b_after.links
    assert "第一性原理" in b_after.links
    # 正文双链：无别名 → 补卡片 title 作别名；有别名 → 保留别名
    assert "[[archives/驾驭工程|驾驭]]" in b_after.content
    assert "[[第一性原理]]" in b_after.content
    # parse_links 验证无死链
    assert set(parse_links(b_after.content)) == {
        "archives/驾驭工程",
        "第一性原理",
    }
    assert find_broken_links([b_after], store) == []


def test_transition_archive_rewrites_only_referrers(store):
    """未引用被归档卡的卡片保持原样。"""
    store.create(make_card("提示词工程", content="引用 [[第一性原理]]", links=["第一性原理"]))
    store.create(make_card("第一性原理", type="insights"))
    store.create(make_card("驾驭工程", status="current"))
    store.transition("驾驭工程", "archive")
    b = store.get("提示词工程")
    assert b.links == ["第一性原理"]
    assert "[[第一性原理]]" in b.content


def test_transition_archived_card_not_in_wiki(store):
    """Archive 终态：归档卡不再出现在 wiki 命名空间（无法再迁移）。"""
    store.create(make_card("驾驭工程", status="current"))
    store.transition("驾驭工程", "archive")
    with pytest.raises(CardNotFoundError):
        store.transition("驾驭工程", "draft")


def test_transition_archive_removes_from_index(store, tmp_path):
    store.create(make_card("驾驭工程", status="current"))
    index = tmp_path / "kb" / "index.md"
    assert "- [[驾驭工程]]" in index.read_text(encoding="utf-8")
    store.transition("驾驭工程", "archive")
    assert "- [[驾驭工程]]" not in index.read_text(encoding="utf-8")


def test_transition_archive_logs_logbook(store, tmp_path):
    store.create(make_card("驾驭工程", status="current"))
    store.transition("驾驭工程", "archive")
    log = (tmp_path / "kb" / "log.md").read_text(encoding="utf-8")
    assert "transition | 驾驭工程 | current → archive" in log


def test_transition_logs_logbook(store, tmp_path):
    store.create(make_card("驾驭工程", status="draft"))
    store.transition("驾驭工程", "current")
    log = (tmp_path / "kb" / "log.md").read_text(encoding="utf-8")
    assert "transition | 驾驭工程 | draft → current" in log


def test_transition_returns_updated_card(store):
    store.create(make_card("驾驭工程", status="draft"))
    card = store.transition("驾驭工程", "current")
    assert card.status == "current"
