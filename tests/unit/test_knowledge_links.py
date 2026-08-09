"""任务2 · 双向链接解析/孤儿/断链/重链 回归测试

覆盖（评估标准）：`[[目标]]` 与 `[[目标|别名]]` 两种语法、去重保序、
孤儿检测（archives 前缀不算入链）、断链解析返回 None 而非抛异常、
归档重链后无死链、正文双链重写（保留别名）。
"""

from __future__ import annotations

import pytest

from agent.knowledge.card import CardStore
from agent.knowledge.links import (
    find_broken_links,
    find_orphans,
    parse_links,
    resolve_link,
    rewrite_link_targets,
)
from agent.knowledge.schema import Card, slugify


def make_card(
    title: str = "驾驭工程",
    slug: str = "",
    type: str = "concepts",
    content: str = "",
    links=None,
) -> Card:
    card = Card(
        title=title,
        slug=slug or slugify(title),
        status="current",
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


# ---------- parse_links ----------


def test_parse_links_basic_and_dedup():
    text = "看 [[驾驭工程]] 与 [[提示词工程|别名]]，再看 [[驾驭工程]]"
    assert parse_links(text) == ["驾驭工程", "提示词工程"]


def test_parse_links_archives_target():
    assert parse_links("归档见 [[archives/旧主题|旧主题]]") == ["archives/旧主题"]


def test_parse_links_archives_and_pure_are_distinct():
    """【边界】archives/X 与 X 是两个不同目标，互不去重。"""
    text = "[[驾驭工程]] 与 [[archives/驾驭工程|归档]]"
    assert parse_links(text) == ["驾驭工程", "archives/驾驭工程"]


def test_parse_links_no_links_returns_empty():
    assert parse_links("没有任何双链的普通文本") == []
    assert parse_links("") == []
    assert parse_links(None) == []


def test_parse_links_trims_whitespace():
    assert parse_links("[[ 空格目标 ]]") == ["空格目标"]


def test_parse_links_multiline():
    text = "第一行 [[A]]\n第二行 [[B|别名]]"
    assert parse_links(text) == ["A", "B"]


# ---------- find_orphans ----------


def test_find_orphans_identifies():
    a = make_card("驾驭工程")
    b = make_card("提示词工程", links=["驾驭工程"])
    c = make_card("第一性原理", links=["提示词工程"])
    d = make_card("复杂系统", links=["第一性原理"])
    # 链状引用 d→c→b→a：只有 d 无入链
    assert find_orphans([a, b, c, d]) == ["复杂系统"]


def test_find_orphans_archives_link_not_incoming():
    """【评估标准】archives/ 前缀指向归档，不算 wiki 卡片入链。"""
    a = make_card("驾驭工程")
    b = make_card("提示词工程", links=["archives/驾驭工程"])
    assert find_orphans([a, b]) == ["驾驭工程", "提示词工程"]


def test_find_orphans_archives_link_does_not_count():
    """【边界】只有 archives/ 前缀引用（无纯 slug 引用）的卡片仍判孤儿。"""
    a = make_card("驾驭工程")
    b = make_card("提示词工程", links=["驾驭工程", "archives/复杂系统"])
    c = make_card("复杂系统", links=["archives/驾驭工程"])
    # 入链 = {驾驭工程}（b 的纯 slug 链接）；archives 引用全部忽略
    assert find_orphans([a, b, c]) == ["提示词工程", "复杂系统"]


def test_find_orphans_empty_when_all_linked():
    a = make_card("A", slug="a")
    b = make_card("B", slug="b", links=["a"])
    c = make_card("C", slug="c", links=["b"])
    a.links = ["c"]
    assert find_orphans([a, b, c]) == []


def test_find_orphans_empty_list():
    assert find_orphans([]) == []


# ---------- resolve_link ----------


def test_resolve_link_existing(store):
    store.create(make_card("驾驭工程"))
    card = resolve_link("驾驭工程", store)
    assert card is not None
    assert card.slug == "驾驭工程"


def test_resolve_link_missing_returns_none(store):
    assert resolve_link("不存在", store) is None


def test_resolve_link_archives_target(store):
    store.create(make_card("驾驭工程", type="concepts"))
    store.transition("驾驭工程", "archive")
    assert resolve_link("archives/驾驭工程", store) is not None


def test_resolve_link_archives_missing_returns_none(store):
    """【边界】归档后：archives/ 目标存在可解析；旧的纯 slug 链接变断链。"""
    store.create(make_card("驾驭工程"))
    store.transition("驾驭工程", "archive")
    assert resolve_link("archives/不存在", store) is None
    assert resolve_link("驾驭工程", store) is None


def test_resolve_link_archives_requires_archived(store):
    """【边界】archives/X 只解析归档目录；X 仍在 wiki 时该链接为断链。"""
    store.create(make_card("驾驭工程"))
    assert resolve_link("archives/驾驭工程", store) is None
    assert resolve_link("驾驭工程", store) is not None


def test_resolve_link_never_raises(store):
    """断链/畸形目标不抛异常（含损坏卡片场景）。"""
    assert resolve_link("", store) is None
    assert resolve_link("a/../b", store) is None
    assert resolve_link("archives/", store) is None


def test_resolve_link_swallows_store_exception():
    """store.get 抛异常（如磁盘 IO）时 resolve_link 返回 None 而非上抛。"""

    class _BrokenStore:
        def get(self, slug):
            raise RuntimeError("boom")

    assert resolve_link("x", _BrokenStore()) is None


# ---------- find_broken_links ----------


def test_find_broken_links_empty(store):
    store.create(make_card("提示词工程", links=["驾驭工程"]))
    store.create(make_card("驾驭工程"))
    b = store.get("提示词工程")
    assert find_broken_links([b], store) == []


def test_find_broken_links_detects(store):
    store.create(make_card("驾驭工程"))
    store.create(make_card("提示词工程", links=["驾驭工程", "幽灵"]))
    b = store.get("提示词工程")
    assert find_broken_links([b], store) == [
        {"from_slug": "提示词工程", "to_slug": "幽灵"}
    ]


def test_find_broken_links_archives_prefix_detected(store):
    """【边界】指向不存在归档的 archives/ 链接同样被检出为断链。"""
    store.create(make_card("提示词工程", links=["archives/幽灵归档"]))
    b = store.get("提示词工程")
    assert find_broken_links([b], store) == [
        {"from_slug": "提示词工程", "to_slug": "archives/幽灵归档"}
    ]


def test_find_broken_links_empty_cards(store):
    assert find_broken_links([], store) == []


def test_find_broken_links_matches_reference_extreme(store, tmp_path):
    """优化后（内存集合 + 惰性缓存）与无缓存基准在极端数据下结果一致。

    覆盖关键语义路径：同目标多卡引用（缓存命中）、archives/ 前缀目标
    （走 store 探测）、跨 type 引用（known 命中）、frontmatter 损坏卡
    （回退探测仍判断链）、别名链接（目标即 slug）。
    """
    for i in range(50):
        store.create(make_card(f"o{i}", slug=f"o{i}", links=["ghost"]))
    store.create(make_card("甲", slug="甲", links=["乙", "archives/旧卡", "实体卡"]))
    store.create(make_card("乙", slug="乙", links=["甲"]))
    store.create(make_card("实体卡", slug="实体卡", type="entities"))
    # frontmatter 损坏卡：文件存在但不可解析 → 仍判断链（回退 store 探测）
    bad_dir = tmp_path / "kb" / "wiki" / "concepts"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "损坏卡.md").write_text("无 frontmatter 的坏文件", encoding="utf-8")
    store.create(make_card("引坏卡", slug="引坏卡", links=["损坏卡"]))
    # 别名链接：目标文本含 |，视为独立目标（不存在 → 断链）
    store.create(make_card("带别名", slug="带别名", links=["ghost|幽灵别名"]))

    def reference(cards_, s):
        """未优化基准：每个链接独立 resolve_link。"""
        return sorted(
            (
                {"from_slug": c.slug, "to_slug": t}
                for c in cards_
                for t in c.links
                if resolve_link(t, s) is None
            ),
            key=lambda b: (b["from_slug"], b["to_slug"]),
        )

    cards = store.list()
    opt = sorted(
        find_broken_links(cards, store),
        key=lambda b: (b["from_slug"], b["to_slug"]),
    )
    assert opt == reference(cards, store)
    assert len(opt) == 53  # 50×ghost + archives/旧卡 + 损坏卡 + 别名目标


def test_find_broken_links_same_target_cached(store):
    """性能路径：同一目标被多卡引用时只解析一次，结果仍逐卡完整。"""
    store.create(make_card("a", slug="a", links=["ghost"]))
    store.create(make_card("b", slug="b", links=["ghost"]))
    store.create(make_card("c", slug="c", links=["ghost"]))
    broken = find_broken_links(store.list(), store)
    assert sorted(broken, key=lambda b: b["from_slug"]) == [
        {"from_slug": "a", "to_slug": "ghost"},
        {"from_slug": "b", "to_slug": "ghost"},
        {"from_slug": "c", "to_slug": "ghost"},
    ]


# ---------- rewrite_link_targets（归档重链） ----------


def test_rewrite_link_targets_basic():
    text = "看 [[old]] 和 [[other|别名]]"
    out = rewrite_link_targets(text, "old", "archives/old", default_alias="旧名")
    assert "[[archives/old|旧名]]" in out
    assert "[[other|别名]]" in out  # 其他链接原样保留


def test_rewrite_link_targets_keeps_alias():
    out = rewrite_link_targets(
        "[[old|自定义别名]]", "old", "archives/old", default_alias="旧名"
    )
    assert out == "[[archives/old|自定义别名]]"


def test_rewrite_link_targets_default_alias_falls_back_to_old():
    out = rewrite_link_targets("[[old]]", "old", "archives/old")
    assert out == "[[archives/old|old]]"


def test_rewrite_link_targets_untouched():
    out = rewrite_link_targets(
        "[[目标A]] [[目标B|别名]]", "old", "archives/old"
    )
    assert out == "[[目标A]] [[目标B|别名]]"


def test_rewrite_link_targets_empty():
    assert rewrite_link_targets("", "old", "archives/old") == ""
    assert rewrite_link_targets(None, "old", "archives/old") == ""
