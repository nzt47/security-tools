"""任务2 · 知识卡片 CRUD/持久化/索引/日志 回归测试

覆盖（评估标准）：创建/读取/更新/删除、同 slug 冲突（CardConflictError）、
跨 type 全局查重、原子写、list 过滤、正文双链同步 links、index 全量/增量一致性、
log.md 写操作登记、slug 路径穿越防御、损坏卡片容错。
"""

from __future__ import annotations

import pytest

from agent.knowledge.card import (
    CardConflictError,
    CardNotFoundError,
    CardStore,
)
from agent.knowledge.index import rebuild_index, update_index_delta
from agent.knowledge.logbook import append_log
from agent.knowledge.schema import Card, slugify


def make_card(
    title: str = "驾驭工程",
    status: str = "current",
    type: str = "concepts",
    content: str = "",
    links=None,
    insight: str = "一句话核心洞见",
    **kw,
) -> Card:
    card = Card(
        title=title,
        slug=kw.pop("slug", slugify(title)),
        status=status,
        type=type,
        source=kw.pop("source", "inbox/test.md"),
        date=kw.pop("date", "2026-08-02"),
        tags=kw.pop("tags", []),
        links=links if links is not None else [],
        insight=insight,
        **kw,
    )
    card.content = content
    return card


@pytest.fixture
def store(tmp_path):
    """临时知识库布局：<tmp>/kb/{wiki,archives,index.md,log.md}"""
    return CardStore(tmp_path / "kb" / "wiki")


def _strip_time(text: str) -> str:
    """剔除时间戳行，用于 rebuild 与 delta 叠加结果的一致性比对。"""
    return "\n".join(
        line
        for line in text.splitlines()
        if not line.startswith("> 此文件由 AI 自动维护")
    )


# ---------- create / get ----------


def test_create_and_get_roundtrip(store):
    a = make_card("驾驭工程", content="正文内容", tags=["工程"])
    store.create(a)
    got = store.get("驾驭工程")
    assert got is not None
    assert got.title == "驾驭工程"
    assert got.slug == "驾驭工程"
    assert got.status == "current"
    assert got.type == "concepts"
    assert got.content == "正文内容"
    assert got.tags == ["工程"]
    assert got.insight == "一句话核心洞见"


def test_create_duplicate_slug_raises_conflict(store):
    store.create(make_card("驾驭工程"))
    with pytest.raises(CardConflictError):
        store.create(make_card("驾驭工程"))


def test_create_duplicate_slug_across_types_raises(store):
    """同 slug 跨 type 也冲突（slug 全局唯一，不静默覆盖）。"""
    store.create(make_card("驾驭工程", type="concepts"))
    with pytest.raises(CardConflictError):
        store.create(make_card("驾驭工程", type="entities"))


def test_create_invalid_card_raises_valueerror(store):
    card = make_card("驾驭工程")
    card.insight = ""  # validate_card 契约：核心洞见必填
    with pytest.raises(ValueError, match="核心洞见"):
        store.create(card)


def test_create_illegal_slug_raises_valueerror(store):
    """路径穿越防御：slug 不允许含路径分隔符。"""
    card = make_card("驾驭工程")
    card.slug = "../evil"
    with pytest.raises(ValueError, match="非法 slug"):
        store.create(card)


def test_create_empty_slug_raises_valueerror(store):
    card = make_card("驾驭工程")
    card.slug = ""
    with pytest.raises(ValueError, match="slug 不能为空"):
        store.create(card)


def test_get_missing_returns_none(store):
    assert store.get("不存在") is None


def test_get_corrupted_card_returns_none(store, tmp_path):
    p = tmp_path / "kb" / "wiki" / "concepts" / "坏卡.md"
    p.parent.mkdir(parents=True)
    p.write_text("没有 frontmatter", encoding="utf-8")
    assert store.get("坏卡") is None
    assert store.list() == []  # 损坏卡片被列举时跳过


def test_get_invalid_yaml_frontmatter_returns_none(store, tmp_path):
    """frontmatter 非法 YAML → 视为不存在（不抛异常）。"""
    p = tmp_path / "kb" / "wiki" / "concepts" / "坏卡.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\ntitle: [未闭合\n---\n正文", encoding="utf-8")
    assert store.get("坏卡") is None


def test_get_archives_prefixed_target(store):
    store.create(make_card("驾驭工程", status="current"))
    store.transition("驾驭工程", "archive")
    archived = store.get("archives/驾驭工程")
    assert archived is not None
    assert archived.slug == "驾驭工程"
    assert archived.status == "archive"


def test_get_archives_traversal_returns_none(store):
    assert store.get("archives/../evil") is None


def test_create_writes_frontmatter_format(store, tmp_path):
    a = make_card("驾驭工程", content="正文内容", links=["提示词工程"])
    store.create(a)
    text = (
        tmp_path / "kb" / "wiki" / "concepts" / "驾驭工程.md"
    ).read_text(encoding="utf-8")
    assert text.startswith("---\ntitle: 驾驭工程\n")
    assert "slug: 驾驭工程" in text
    assert "status: current" in text
    assert "links: [提示词工程]" in text or "links:\n- 提示词工程" in text
    assert "insight: 一句话核心洞见" in text
    assert text.strip().endswith("正文内容")


def test_create_atomic_no_tmp_leftover(store, tmp_path):
    store.create(make_card("驾驭工程"))
    leftovers = list((tmp_path / "kb" / "wiki" / "concepts").glob("*.tmp"))
    assert leftovers == []


# ---------- update ----------


def test_update_persists_changes(store):
    a = make_card("驾驭工程")
    store.create(a)
    a.insight = "更新后的摘要"
    a.content = "更新后的正文"
    store.update(a)
    got = store.get("驾驭工程")
    assert got.insight == "更新后的摘要"
    assert got.content == "更新后的正文"


def test_update_syncs_links_from_content(store):
    """【不易】update 以正文双链解析结果同步 links 字段。"""
    a = make_card("驾驭工程")
    store.create(a)
    assert store.get("驾驭工程").links == []  # create 保持传入值
    a.content = "参见 [[提示词工程]] 与 [[第一性原理|原理]]"
    store.update(a)
    assert store.get("驾驭工程").links == ["提示词工程", "第一性原理"]


def test_update_missing_raises_not_found(store):
    with pytest.raises(CardNotFoundError):
        store.update(make_card("不存在"))


def test_update_type_change_migrates_file(store, tmp_path):
    a = make_card("驾驭工程", type="concepts")
    store.create(a)
    old = tmp_path / "kb" / "wiki" / "concepts" / "驾驭工程.md"
    assert old.exists()
    a.type = "entities"
    store.update(a)
    assert not old.exists()
    assert (tmp_path / "kb" / "wiki" / "entities" / "驾驭工程.md").exists()


def test_update_type_change_moves_index_entry(store, tmp_path):
    """【评估标准】type 变更后 index 条目迁移到新 section（增量 == 全量重建）。"""
    index_path = tmp_path / "kb" / "index.md"
    a = make_card("驾驭工程", type="concepts")
    store.create(a)
    store.create(make_card("张三", type="entities"))
    a.type = "entities"
    store.update(a)
    delta_text = _strip_time(index_path.read_text(encoding="utf-8"))
    assert rebuild_index(tmp_path / "kb" / "wiki", index_path) == 2
    rebuilt = _strip_time(index_path.read_text(encoding="utf-8"))
    assert rebuilt == delta_text
    # 旧 section 无残留、新 section 含条目
    concepts_part = delta_text.split("## 实体 (Entities)")[0]
    assert "驾驭工程" not in concepts_part
    assert "- [[驾驭工程]]" in delta_text.split("## 实体 (Entities)")[1]


def test_update_refreshes_index_entry(store, tmp_path):
    a = make_card("驾驭工程")
    store.create(a)
    a.insight = "更新后的摘要"
    store.update(a)
    text = (tmp_path / "kb" / "index.md").read_text(encoding="utf-8")
    assert "更新后的摘要" in text


# ---------- delete ----------


def test_delete_removes_card(store):
    store.create(make_card("驾驭工程"))
    assert store.delete("驾驭工程") is True
    assert store.get("驾驭工程") is None


def test_delete_with_incoming_links_rejected(store):
    """【评估标准】有入链时拒绝删除并返回 False。"""
    store.create(make_card("提示词工程", links=["驾驭工程"]))
    store.create(make_card("驾驭工程"))
    assert store.delete("驾驭工程") is False
    assert store.get("驾驭工程") is not None


def test_delete_not_blocked_by_archives_link(store):
    """【边界】archives/ 前缀链接指向归档，不算 wiki 入链，不阻止删除。"""
    store.create(make_card("驾驭工程"))
    store.create(make_card("提示词工程", links=["archives/驾驭工程"]))
    assert store.delete("驾驭工程") is True
    assert store.get("驾驭工程") is None


def test_delete_missing_returns_false(store):
    assert store.delete("不存在") is False


def test_delete_removes_index_entry(store, tmp_path):
    store.create(make_card("驾驭工程"))
    store.create(make_card("提示词工程"))
    assert store.delete("提示词工程") is True
    text = (tmp_path / "kb" / "index.md").read_text(encoding="utf-8")
    assert "- [[提示词工程]]" not in text
    assert "- [[驾驭工程]]" in text


# ---------- 批量删除（P1-1） ----------


def test_delete_many_no_external_refs(store, tmp_path):
    """无外部引用的整批删除：全部成功，文件与 index 条目清除。"""
    for title in ("驾驭工程", "提示词工程", "知识蒸馏"):
        store.create(make_card(title))
    result = store.delete_many(["驾驭工程", "提示词工程", "知识蒸馏"])
    assert result == {"驾驭工程": True, "提示词工程": True, "知识蒸馏": True}
    assert store.list() == []
    text = (tmp_path / "kb" / "index.md").read_text(encoding="utf-8")
    for title in ("驾驭工程", "提示词工程", "知识蒸馏"):
        assert f"- [[{title}]]" not in text


def test_delete_many_internal_refs_allowed(store):
    """待删集合内部的互相引用不阻止删除（整批同时消失，无残留断链）。"""
    store.create(make_card("驾驭工程", links=["提示词工程"]))
    store.create(make_card("提示词工程", links=["驾驭工程"]))
    result = store.delete_many(["驾驭工程", "提示词工程"])
    assert result == {"驾驭工程": True, "提示词工程": True}
    assert store.list() == []


def test_delete_many_external_ref_rejected(store):
    """待删集合外的引用方仍指向 slug → 该张拒绝且文件保留，其余正常删除。"""
    store.create(make_card("提示词工程"))
    store.create(make_card("驾驭工程", links=["提示词工程"]))
    store.create(make_card("知识蒸馏", links=["提示词工程"]))
    result = store.delete_many(["驾驭工程", "提示词工程"])
    assert result == {"驾驭工程": True, "提示词工程": False}
    assert store.get("驾驭工程") is None       # 无入链，正常删除
    assert store.get("提示词工程") is not None  # 外部引用（知识蒸馏）仍在 → 保留


def test_delete_many_matches_sequential_delete(tmp_path):
    """无内部互相引用时，批量判定 == 逐次 delete 判定（一致性不变量）。"""

    def build(i: int):
        s = CardStore(tmp_path / f"kb{i}" / "wiki")
        s.create(make_card("A卡"))
        s.create(make_card("B卡"))
        s.create(make_card("C卡", links=["A卡"]))  # C → A：A 有外部入链
        return s

    slugs = ["A卡", "B卡"]
    batch = build(0).delete_many(list(slugs))
    store_seq = build(1)
    seq = {s: store_seq.delete(s) for s in slugs}
    assert batch == seq  # {A卡: False, B卡: True}：判定一致
    assert batch == {"A卡": False, "B卡": True}


def test_delete_many_index_missing_fallback(store, tmp_path):
    """入链索引缺失时 delete_many 回退全库扫描，判定语义与索引存在时一致。"""
    links_index = tmp_path / "kb" / "index_links.md"
    store.create(make_card("提示词工程", links=["驾驭工程"]))
    store.create(make_card("驾驭工程", links=["提示词工程"]))
    store.create(make_card("知识蒸馏", links=["提示词工程"]))
    assert links_index.exists()          # create 挂接已建索引
    links_index.unlink()                 # 制造索引缺失 → 回退全扫
    result = store.delete_many(["驾驭工程", "提示词工程"])
    assert result == {"驾驭工程": True, "提示词工程": False}
    assert store.get("驾驭工程") is None
    assert store.get("提示词工程") is not None   # 外部引用（知识蒸馏）仍在 → 保留
    assert store.get("知识蒸馏") is not None


# ---------- list ----------


def test_list_all_and_filters(store):
    store.create(make_card("驾驭工程", type="concepts"))
    store.create(make_card("提示词工程", status="draft", type="concepts"))
    store.create(make_card("张三", type="entities"))
    assert len(store.list()) == 3
    assert {c.slug for c in store.list(status="draft")} == {"提示词工程"}
    assert {c.slug for c in store.list(type="entities")} == {"张三"}
    assert {c.slug for c in store.list(status="current", type="concepts")} == {
        "驾驭工程"
    }
    assert store.list(status="archive") == []


def test_list_empty_wiki(store):
    assert store.list() == []


# ---------- index.md 维护 ----------


def test_rebuild_index_format(store, tmp_path):
    store.create(make_card("驾驭工程", content=""))
    store.create(make_card("提示词工程", status="draft", type="concepts"))
    index_path = tmp_path / "kb" / "index.md"
    assert rebuild_index(tmp_path / "kb" / "wiki", index_path) == 2
    text = index_path.read_text(encoding="utf-8")
    assert text.startswith("# 知识库全局索引")
    assert "## 概念 (Concepts)" in text
    assert "## 实体 (Entities)" in text
    assert "## 洞察 (Insights)" in text
    assert "- [[驾驭工程]] `current` — 一句话核心洞见" in text
    assert "- [[提示词工程]] `draft` — 一句话核心洞见" in text


def test_rebuild_index_matches_delta_accumulation(store, tmp_path):
    """【评估标准】rebuild_index 与逐卡 update_index_delta 叠加结果一致。"""
    index_path = tmp_path / "kb" / "index.md"
    cards = [
        make_card("驾驭工程", type="concepts"),
        make_card("提示词工程", status="draft", type="concepts"),
        make_card("张三", type="entities"),
        make_card("关于复杂系统", type="insights"),
    ]
    for c in cards:
        store.create(c)
    delta_text = _strip_time(index_path.read_text(encoding="utf-8"))
    assert rebuild_index(tmp_path / "kb" / "wiki", index_path) == len(cards)
    rebuilt = _strip_time(index_path.read_text(encoding="utf-8"))
    assert rebuilt == delta_text


def _section_slugs(text: str) -> dict[str, list[str]]:
    """按 section 解析条目 slug 列表（用于 append 模式集合对比）。"""
    secs: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line
            secs[cur] = []
        elif line.startswith("- [["):
            secs.setdefault(cur, []).append(line.split("[[")[1].split("]]")[0])
    return secs


def test_index_append_mode_set_equivalence(store, tmp_path):
    """【P1-2】append 叠加的每 section slug 集合 == 全量重建集合（内容集合不变式）。"""
    index_path = tmp_path / "kb" / "index.md"
    cards = [
        make_card("驾驭工程", type="concepts"),
        make_card("提示词工程", status="draft", type="concepts"),
        make_card("张三", type="entities"),
        make_card("关于复杂系统", type="insights"),
    ]
    for c in cards:
        store.create(c)     # 卡片落库（rebuild 依赖）
    index_path.unlink()     # 重置骨架：以下模拟高频写路径只走 append 叠加
    for c in cards:
        update_index_delta(c.slug, c, index_path, append=True)
    delta_text = _strip_time(index_path.read_text(encoding="utf-8"))
    rebuild_index(tmp_path / "kb" / "wiki", index_path)
    rebuilt = _strip_time(index_path.read_text(encoding="utf-8"))
    assert {k: set(v) for k, v in _section_slugs(delta_text).items()} == {
        k: set(v) for k, v in _section_slugs(rebuilt).items()
    }


def test_index_append_mode_converges_after_rebuild(store, tmp_path):
    """【P1-2】append 状态执行一次 rebuild_index 即收敛为字典序（重整收敛不变式）。"""
    index_path = tmp_path / "kb" / "index.md"
    cards = [
        make_card("驾驭工程", type="concepts"),
        make_card("提示词工程", status="draft", type="concepts"),
        make_card("张三", type="entities"),
        make_card("关于复杂系统", type="insights"),
    ]
    for c in cards:
        store.create(c)
    index_path.unlink()
    for c in cards:
        update_index_delta(c.slug, c, index_path, append=True)
    assert rebuild_index(tmp_path / "kb" / "wiki", index_path) == len(cards)
    converged = _strip_time(index_path.read_text(encoding="utf-8"))
    index_path.unlink()
    rebuild_index(tmp_path / "kb" / "wiki", index_path)
    fresh = _strip_time(index_path.read_text(encoding="utf-8"))
    assert converged == fresh  # 重整结果 == 全新重建，逐字节一致


def test_index_append_mode_idempotent(store, tmp_path):
    """【P1-2】append 重复写同 slug → 更新已有条目，不产生重复行（幂等）。"""
    index_path = tmp_path / "kb" / "index.md"
    card = make_card("驾驭工程")
    assert update_index_delta(card.slug, card, index_path, append=True) is True
    assert update_index_delta(card.slug, card, index_path, append=True) is True
    text = index_path.read_text(encoding="utf-8")
    assert text.count("- [[驾驭工程]]") == 1


def test_update_index_delta_repairs_missing_section(store, tmp_path):
    """防御：index 骨架被手工破坏（缺 section 头）时增量更新能自愈。"""
    index_path = tmp_path / "kb" / "index.md"
    index_path.parent.mkdir(parents=True)
    index_path.write_text("# 知识库全局索引\n", encoding="utf-8")
    assert update_index_delta("驾驭工程", make_card("驾驭工程"), index_path) is True
    text = index_path.read_text(encoding="utf-8")
    assert "## 概念 (Concepts)" in text
    assert "- [[驾驭工程]]" in text


def test_update_index_delta_remove_missing_noop(store, tmp_path):
    """移除不存在的条目 → 无变更返回 False。"""
    index_path = tmp_path / "kb" / "index.md"
    store.create(make_card("驾驭工程"))
    assert update_index_delta("不存在", None, index_path) is False
    assert "- [[驾驭工程]]" in index_path.read_text(encoding="utf-8")


def test_update_index_delta_insert_without_section_gap(store, tmp_path):
    """防御：index 缺 section 间空行时，条目仍插到下一个 section 头之前。"""
    index_path = tmp_path / "kb" / "index.md"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(
        "# 知识库全局索引\n> 时间\n\n## 概念 (Concepts)\n- [[a]]\n## 实体 (Entities)\n",
        encoding="utf-8",
    )
    card_b = make_card("B", slug="b")
    assert update_index_delta("b", card_b, index_path) is True
    text = index_path.read_text(encoding="utf-8")
    assert "- [[a]]\n- [[b]]" in text
    assert "## 实体 (Entities)" in text


def test_update_index_delta_unknown_type_noop(store, tmp_path):
    index_path = tmp_path / "kb" / "index.md"
    card = make_card("驾驭工程")
    card.type = "bogus"
    assert update_index_delta("驾驭工程", card, index_path) is False
    assert not index_path.exists()  # 未知类型不建骨架、不写盘


# ---------- log.md 登记 ----------


def test_create_logs_logbook(store, tmp_path):
    store.create(make_card("驾驭工程"))
    log = (tmp_path / "kb" / "log.md").read_text(encoding="utf-8")
    assert "create | 驾驭工程 | type=concepts" in log


def test_append_log_marker_insert_and_top(store, tmp_path):
    """append_log 顶部追加：有 marker 插其后；无 marker 插最顶。"""
    log = tmp_path / "log.md"
    log.write_text("# 头\n\n<!-- 新记录追加到此行下方（顶部） -->\n", encoding="utf-8")
    append_log("create", "A", "d1", log_path=log)
    text = log.read_text(encoding="utf-8")
    assert "create | A | d1" in text
    assert text.index("<!-- ") < text.index("create | A | d1")

    log2 = tmp_path / "log2.md"
    append_log("update", "B", "d2", log_path=log2)
    assert log2.read_text(encoding="utf-8").startswith("## [")
    append_log("delete", "C", log_path=log2)
    text3 = log2.read_text(encoding="utf-8")
    assert text3.index("delete | C") < text3.index("update | B | d2")


# ---------- 批量导入（import_from_dir） ----------


def _write_card_md(path, title, *, slug=None, type="concepts", content="正文"):
    """写一张合法 frontmatter 卡片文件（slug 契约: slug == slugify(title)）。"""
    slug = slug if slug is not None else slugify(title)
    path.write_text(
        f"---\n"
        f"title: {title}\n"
        f"slug: {slug}\n"
        f"status: current\n"
        f"type: {type}\n"
        f"source: import/{path.name}\n"
        f"date: 2026-08-07\n"
        f"tags: []\n"
        f"links: []\n"
        f"insight: 一句话核心洞见\n"
        f"---\n\n"
        f"{content}\n",
        encoding="utf-8",
    )


def test_import_from_dir_basic(store, tmp_path):
    """2 张合法卡：导入计数、落盘、index 增量、log 登记。"""
    src = tmp_path / "src"
    src.mkdir()
    _write_card_md(src / "a.md", "驾驭工程")
    _write_card_md(src / "b.md", "提示词工程")
    result = store.import_from_dir(src)
    assert result.imported == 2 and result.skipped == 0 and result.failed == 0
    assert result.failures == []
    assert {c.slug for c in store.list()} == {"驾驭工程", "提示词工程"}
    assert (tmp_path / "kb" / "wiki" / "concepts" / "驾驭工程.md").exists()
    idx = (tmp_path / "kb" / "index.md").read_text(encoding="utf-8")
    assert "- [[驾驭工程]]" in idx and "- [[提示词工程]]" in idx
    log = (tmp_path / "kb" / "log.md").read_text(encoding="utf-8")
    assert log.count("create |") == 2


def test_import_from_dir_skips_conflict(store, tmp_path):
    """同 slug 冲突默认跳过不覆盖（create 契约），其余照常导入。"""
    store.create(make_card("驾驭工程"))
    src = tmp_path / "src"
    src.mkdir()
    _write_card_md(src / "a.md", "驾驭工程")
    _write_card_md(src / "b.md", "提示词工程")
    result = store.import_from_dir(src)
    assert result.imported == 1 and result.skipped == 1 and result.failed == 0
    assert result.failures == []
    # 原卡未被覆盖
    assert store.get("驾驭工程").content.strip() == ""


def test_import_from_dir_force_updates(store, tmp_path):
    """force=True 时冲突改走 update（内容覆盖 + log update 登记）。"""
    store.create(make_card("驾驭工程"))
    src = tmp_path / "src"
    src.mkdir()
    _write_card_md(src / "a.md", "驾驭工程", content="新正文")
    result = store.import_from_dir(src, force=True)
    assert result.imported == 1 and result.skipped == 0 and result.failed == 0
    got = store.get("驾驭工程")
    assert got is not None and got.content.strip() == "新正文"
    log = (tmp_path / "kb" / "log.md").read_text(encoding="utf-8")
    assert "update |" in log and "create |" in log


def test_import_from_dir_skips_corrupt(store, tmp_path):
    """无 frontmatter 的损坏文件计入失败并附明细，不中断批次。"""
    src = tmp_path / "src"
    src.mkdir()
    (src / "broken.md").write_text("# 无 frontmatter\n", encoding="utf-8")
    _write_card_md(src / "ok.md", "驾驭工程")
    result = store.import_from_dir(src)
    assert result.imported == 1 and result.failed == 1
    assert result.failures[0][0] == "broken.md"
    assert "frontmatter" in result.failures[0][1]


def test_import_from_dir_missing_dir(store, tmp_path):
    """目录不存在 → ValueError（调用方错误）。"""
    with pytest.raises(ValueError, match="目录不存在"):
        store.import_from_dir(tmp_path / "nope")


def test_import_from_dir_invalid_schema(store, tmp_path):
    """frontmatter 缺必填字段（title/source/date）→ Card 构造 TypeError 计入 failed。"""
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.md").write_text(
        "---\nslug: bad\nstatus: current\ntype: concepts\n---\n",
        encoding="utf-8",
    )
    result = store.import_from_dir(src)
    assert result.imported == 0 and result.failed == 1
    assert result.failures[0][0] == "bad.md"
    assert "missing" in result.failures[0][1]


def test_import_from_dir_validate_failure(store, tmp_path):
    """字段齐全但值非法（status=bogus）→ validate_card 失败计入 failed。"""
    src = tmp_path / "src"
    src.mkdir()
    _write_card_md(src / "ok.md", "驾驭工程")
    (src / "bad.md").write_text(
        "---\n"
        "title: 坏卡\n"
        "slug: bad\n"
        "status: bogus\n"
        "type: concepts\n"
        "source: import/bad.md\n"
        "date: 2026-08-07\n"
        "---\n",
        encoding="utf-8",
    )
    result = store.import_from_dir(src)
    assert result.imported == 1 and result.failed == 1
    assert result.failures[0][0] == "bad.md"
    assert "卡片校验失败" in result.failures[0][1]
