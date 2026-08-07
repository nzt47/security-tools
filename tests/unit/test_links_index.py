"""任务7 · 入链索引 links_index.py 一致性测试（TDD：先锁定不变量，再实现）

覆盖（评估标准）：
1. 全量一致性：rebuild_links_index 结果 == 全库扫描逐卡 links 聚合结果
2. 增量一致性：update_links_delta 任意叠加结果 == rebuild_links_index 结果
3. 幂等：重复 add 不产生重复引用；add=False 移除不存在引用无变更
4. 语义边界：archives/ 前缀链接不列入链表（对齐 find_orphans 语义）
5. 收敛：update 场景（先 remove 旧引用、再 add 新引用）叠加 == 重建
"""

from __future__ import annotations

import pytest

from agent.knowledge.card import CardStore
from agent.knowledge.schema import Card, slugify

# 被测模块（TDD 红阶段：实现前该导入会 ImportError）
from agent.knowledge.links_index import (
    read_links_index,
    rebuild_links_index,
    update_links_delta,
)


def make_card(title: str, links=None, type: str = "concepts", content=None) -> Card:
    card = Card(
        title=title,
        slug=slugify(title),
        status="current",
        type=type,
        source="inbox/test.md",
        date="2026-08-02",
        tags=[],
        links=links if links is not None else [],
        insight="一句话核心洞见",
    )
    card.content = content or f"# {title}\n"
    return card


@pytest.fixture
def store(tmp_path):
    return CardStore(tmp_path / "kb" / "wiki")


def _links_index_path(tmp_path):
    return tmp_path / "kb" / "index_links.md"


# ---------- 1. 全量一致性 ----------


def test_rebuild_matches_full_scan(tmp_path, store):
    """rebuild_links_index == 全库扫描聚合：b→[a]、c→[a,b]，archives 不入表。"""
    store.create(make_card("A", links=["b", "c"]))
    store.create(make_card("B", links=["c"]))
    store.create(make_card("C", links=["archives/d"]))
    index_path = _links_index_path(tmp_path)
    assert rebuild_links_index(tmp_path / "kb" / "wiki", index_path) == 2
    assert read_links_index(index_path) == {"b": ["a"], "c": ["a", "b"]}


# ---------- 2. 增量一致性 ----------


def test_delta_accumulation_matches_rebuild(tmp_path, store):
    """update_links_delta 逐条叠加结果 == rebuild_links_index 结果。"""
    store.create(make_card("A", links=["b"]))
    store.create(make_card("B", links=["c"]))
    store.create(make_card("C"))
    index_path = _links_index_path(tmp_path)
    for card in store.list():
        for link in card.links:
            if not link.startswith("archives/"):
                update_links_delta(link, card.slug, index_path, add=True)
    delta = read_links_index(index_path)
    rebuild_links_index(tmp_path / "kb" / "wiki", index_path)
    assert delta == read_links_index(index_path)


def test_delta_update_sequence_matches_rebuild(tmp_path, store):
    """update 场景：真实更新卡片（正文双链变更为 [c, d]），表先删旧引用、
    再加新引用，叠加结果 == 重建。"""
    store.create(make_card("A", links=["b", "c"]))
    store.create(make_card("B", links=["c"]))
    index_path = _links_index_path(tmp_path)

    # 初始状态建表（模拟 create 挂接：每张卡入表）
    for card in store.list():
        for link in card.links:
            if not link.startswith("archives/"):
                update_links_delta(link, card.slug, index_path, add=True)

    # update 流程（与 card.py 挂接后一致）：
    #   1) 读旧卡 links，remove 旧引用
    old_links = store.get("a").links
    for old in old_links:
        if not old.startswith("archives/"):
            update_links_delta(old, "a", index_path, add=False)
    #   2) 真实更新磁盘（正文双链 [[c]] [[d]]，update 同步 links=[c, d]）
    new_a = make_card(
        "A", content="# A\n\n[[c]]\n\n[[d]]\n",
    )
    store.update(new_a)
    #   3) add 新引用
    for new in store.get("a").links:
        if not new.startswith("archives/"):
            update_links_delta(new, "a", index_path, add=True)

    expected = read_links_index(index_path)
    rebuild_links_index(tmp_path / "kb" / "wiki", index_path)
    assert expected == read_links_index(index_path)


# ---------- 3. 幂等 ----------


def test_delta_add_idempotent(tmp_path):
    """重复 add 同一引用 → 无变更（返回 0）。"""
    index_path = _links_index_path(tmp_path)
    assert update_links_delta("B", "A", index_path, add=True) == 1
    assert update_links_delta("B", "A", index_path, add=True) == 0
    assert read_links_index(index_path) == {"B": ["A"]}


def test_delta_remove_missing_noop(tmp_path):
    """add=False 且引用不存在 → 无变更（返回 0），文件不存在时不建文件。"""
    index_path = _links_index_path(tmp_path)
    assert update_links_delta("B", "A", index_path, add=False) == 0
    assert not index_path.exists()


def test_delta_remove_clears_empty_section(tmp_path):
    """引用清空后对应段应被移除（不残留空段）。"""
    index_path = _links_index_path(tmp_path)
    update_links_delta("B", "A", index_path, add=True)
    update_links_delta("B", "C", index_path, add=True)
    assert read_links_index(index_path) == {"B": ["A", "C"]}
    assert update_links_delta("B", "A", index_path, add=False) == 1
    assert read_links_index(index_path) == {"B": ["C"]}
    assert update_links_delta("B", "C", index_path, add=False) == 1
    assert read_links_index(index_path) == {}


# ---------- 4. 语义边界 ----------


def test_archives_links_excluded(tmp_path, store):
    """archives/ 前缀链接不列入链表（对齐 find_orphans 语义）。"""
    store.create(make_card("A", links=["archives/b"]))
    store.create(make_card("B"))
    index_path = _links_index_path(tmp_path)
    rebuild_links_index(tmp_path / "kb" / "wiki", index_path)
    assert read_links_index(index_path) == {}


# ---------- 5. CRUD 挂接一致性（s4 验收门核心） ----------


def test_crud_hooks_keep_delta_in_sync(tmp_path, store):
    """create/update/delete 挂接序列后的增量表 == 全量重建表。

    挂接点：create → add 引用；update → 先 remove 旧引用再 add 新引用；
    delete → remove 被删卡引用。任一步骤产生偏差都会在最终比对中暴露。
    """
    index_path = _links_index_path(tmp_path)

    # create（挂接自动入表）：a→[b]、b→[c]、c→[archives/d]（不入表）
    store.create(make_card("A", links=["b"]))
    store.create(make_card("B", links=["c"]))
    store.create(make_card("C", links=["archives/d"]))
    assert read_links_index(index_path) == {"b": ["a"], "c": ["b"]}

    # update：A 正文双链改为 [[c]] [[e]] → 先删 b:[a]，再登记 c:[a]、e:[a]
    store.update(make_card("A", content="# A\n\n[[c]]\n\n[[e]]\n"))
    assert read_links_index(index_path) == {"c": ["a", "b"], "e": ["a"]}

    # delete：删 A（无入链可删）→ 移除 c:[a]、e:[a]
    assert store.delete("a") is True
    assert read_links_index(index_path) == {"c": ["b"]}

    # 不变量：挂接叠加结果 == 全量重建结果
    delta = read_links_index(index_path)
    rebuild_links_index(tmp_path / "kb" / "wiki", index_path)
    assert delta == read_links_index(index_path)


def test_delete_many_hooks_keep_delta_in_sync(tmp_path, store):
    """delete_many 批量删除后增量表 == 全量重建表（内部互相引用整批消失）。"""
    index_path = _links_index_path(tmp_path)
    store.create(make_card("A", links=["b"]))
    store.create(make_card("B", links=["a"]))
    store.create(make_card("D"))  # 幸存卡：无引用，不产生入链
    assert read_links_index(index_path) == {"a": ["b"], "b": ["a"]}

    result = store.delete_many(["a", "b"])
    assert result == {"a": True, "b": True}
    # 表随删除清空，与全量重建一致
    delta = read_links_index(index_path)
    rebuild_links_index(tmp_path / "kb" / "wiki", index_path)
    assert delta == read_links_index(index_path) == {}
