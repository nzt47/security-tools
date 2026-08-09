"""任务5 · 增量索引 回归测试

覆盖（评估标准）：
- 增量执行只触碰受影响文件（mock 断言未触发 rebuild_index / store.list 全量扫描）。
- 事件驱动正确：created / modified / deleted / archives / moved 五类事件。
- start_incremental_index_watcher：注册监听、依赖缺失降级不抛异常。
"""

from __future__ import annotations

import os
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

import agent.knowledge.index as index_mod
from agent.knowledge.card import CardStore
from agent.knowledge.index import (
    handle_wiki_file_event,
    read_index_slugs,
    start_incremental_index_watcher,
)
from agent.knowledge.schema import Card, slugify


def make_card(
    title: str = "卡片",
    slug: str = "",
    content: str = "",
    links=None,
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
        insight="一句话核心洞见",
    )
    card.content = content
    return card


def _write_card_file(path, card: Card) -> None:
    """绕过 CardStore 直接写卡片文件（模拟外部编辑/外部新增）。"""
    data = asdict(card)
    content = data.pop("content", "")
    data.pop("explicit_slug", None)
    if not data.get("metadata"):
        data.pop("metadata", None)
    fm = yaml.safe_dump(
        data, allow_unicode=True, sort_keys=False, default_flow_style=None
    ).strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{fm}\n---\n\n{content.rstrip()}\n", encoding="utf-8")


def _evt(event_type: str, src, dest=None) -> SimpleNamespace:
    meta = {"event_type": event_type, "src_path": str(src)}
    if dest is not None:
        meta["dest_path"] = str(dest)
    return SimpleNamespace(metadata=meta)


@pytest.fixture
def kb(tmp_path):
    """临时知识库：返回 (store, wiki_root, index_path, links_index_path)。"""
    root = tmp_path / "kb"
    store = CardStore(root / "wiki")
    return store, root / "wiki", root / "index.md", root / "index_links.md"


# ---------- handle_wiki_file_event：事件驱动 ----------


def test_handle_created_no_full_scan_incremental(kb):
    """增量不变量：created 事件只重建受影响 slug，不触发全量扫描。"""
    store, wiki, index_path, links_index_path = kb
    store.create(make_card("Alpha", slug="alpha"))
    store.create(make_card("Beta", slug="beta", content="见 [[alpha]]", links=["alpha"]))
    os.unlink(index_path)      # 模拟外部状态：索引文件缺失
    os.unlink(links_index_path)

    with patch("agent.knowledge.index.rebuild_index") as mock_rebuild, \
            patch("agent.knowledge.card.CardStore.list") as mock_list:
        slug = handle_wiki_file_event(
            "created", wiki / "concepts" / "beta.md", wiki,
            index_path=index_path, links_index_path=links_index_path,
        )
        mock_rebuild.assert_not_called()  # 未全量重建索引
        mock_list.assert_not_called()     # 未全量扫描卡片库

    assert slug == "beta"
    assert "beta" in read_index_slugs(index_path)
    assert "alpha" not in read_index_slugs(index_path)  # 只触碰受影响文件
    links_text = links_index_path.read_text(encoding="utf-8")
    assert "## alpha" in links_text and "- [[beta]]" in links_text  # 反向链接登记


def test_handle_modified_refreshes_reverse_links(kb):
    """modified：外部改链后，旧反向链接清除、新反向链接登记。"""
    store, wiki, index_path, links_index_path = kb
    store.create(make_card("Alpha", slug="alpha"))
    store.create(make_card("Gamma", slug="gamma"))
    store.create(make_card("Beta", slug="beta", content="见 [[alpha]]", links=["alpha"]))

    # 外部编辑 beta：由链 alpha 改为链 gamma（直接写文件，绕过 store）
    beta = store.get("beta")
    beta.content = "见 [[gamma]]"
    beta.links = ["gamma"]
    _write_card_file(wiki / "concepts" / "beta.md", beta)

    handle_wiki_file_event(
        "modified", wiki / "concepts" / "beta.md", wiki,
        index_path=index_path, links_index_path=links_index_path,
    )
    links_text = links_index_path.read_text(encoding="utf-8")
    assert "## alpha" not in links_text          # 旧引用已清除（段移除）
    assert "## gamma" in links_text              # 新引用已登记
    assert "- [[beta]]" in links_text


def test_handle_deleted_removes_index_and_reverse(kb):
    store, wiki, index_path, links_index_path = kb
    store.create(make_card("Alpha", slug="alpha"))
    store.create(make_card("Beta", slug="beta", content="见 [[alpha]]", links=["alpha"]))
    os.unlink(wiki / "concepts" / "beta.md")  # 模拟外部删除（绕过 store）

    handle_wiki_file_event(
        "deleted", wiki / "concepts" / "beta.md", wiki,
        index_path=index_path, links_index_path=links_index_path,
    )
    assert "beta" not in read_index_slugs(index_path)
    links_text = links_index_path.read_text(encoding="utf-8")
    assert "## alpha" not in links_text   # beta 清空后 alpha 段整体移除
    assert "- [[beta]]" not in links_text


def test_handle_archives_event_removes_index_entry(kb):
    """外部移动到 archives：wiki index 条目移除（归档卡不进索引）。"""
    store, wiki, index_path, links_index_path = kb
    store.create(make_card("Alpha", slug="alpha"))
    archives = wiki.parent / "archives"
    archives.mkdir(parents=True, exist_ok=True)
    os.rename(wiki / "concepts" / "alpha.md", archives / "alpha.md")

    handle_wiki_file_event(
        "created", archives / "alpha.md", wiki,
        index_path=index_path, links_index_path=links_index_path,
    )
    assert "alpha" not in read_index_slugs(index_path)


def test_handle_non_card_events_ignored(kb):
    store, wiki, index_path, links_index_path = kb
    txt = wiki / "concepts" / "notes.txt"
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text("x", encoding="utf-8")
    assert handle_wiki_file_event(
        "created", txt, wiki,
        index_path=index_path, links_index_path=links_index_path,
    ) is None
    assert not index_path.exists()  # 未触碰索引
    # 受管目录之外的文件同样忽略
    assert handle_wiki_file_event(
        "created", wiki.parent.parent / "outside.md", wiki,
        index_path=index_path, links_index_path=links_index_path,
    ) is None


# ---------- start_incremental_index_watcher ----------


class _FakeWatcher:
    """文件监听器替身：记录构造参数，暴露 callback 供事件驱动测试。"""

    instances: list["_FakeWatcher"] = []

    def __init__(self, watch_dirs, callback, include=None, debounce_sec=2.0):
        self.watch_dirs = watch_dirs
        self.callback = callback
        self.include = include
        self.debounce_sec = debounce_sec
        self.started = False
        _FakeWatcher.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        pass


def test_start_watcher_registers_and_starts(kb):
    store, wiki, index_path, links_index_path = kb
    with patch("agent.knowledge.index._load_watcher_cls", return_value=_FakeWatcher):
        watcher = start_incremental_index_watcher(
            wiki, index_path=index_path, links_index_path=links_index_path,
        )
    assert watcher is _FakeWatcher.instances[-1]
    assert watcher.started
    # 监听三个类型子目录 + archives（比监听整个 wiki 更精准）
    for t in ("concepts", "entities", "insights"):
        assert str(wiki / t) in watcher.watch_dirs
    assert str(wiki.parent / "archives") in watcher.watch_dirs
    assert watcher.include == ["*.md"]


def test_start_watcher_event_driven_updates_index(kb):
    store, wiki, index_path, links_index_path = kb
    store.create(make_card("Alpha", slug="alpha"))
    with patch("agent.knowledge.index._load_watcher_cls", return_value=_FakeWatcher):
        watcher = start_incremental_index_watcher(
            wiki, index_path=index_path, links_index_path=links_index_path,
        )
    store.create(make_card("Beta", slug="beta"))
    os.unlink(index_path)  # 触发前索引缺失
    watcher.callback(_evt("created", wiki / "concepts" / "beta.md"))
    assert "beta" in read_index_slugs(index_path)


def test_start_watcher_moved_event_splits(kb):
    """moved 事件拆为 deleted + created：移动后索引条目仍在线。"""
    store, wiki, index_path, links_index_path = kb
    store.create(make_card("Alpha", slug="alpha"))
    with patch("agent.knowledge.index._load_watcher_cls", return_value=_FakeWatcher):
        watcher = start_incremental_index_watcher(
            wiki, index_path=index_path, links_index_path=links_index_path,
        )
    (wiki / "entities").mkdir(parents=True, exist_ok=True)  # 目标目录须先存在
    os.rename(wiki / "concepts" / "alpha.md", wiki / "entities" / "alpha.md")
    watcher.callback(_evt(
        "moved", wiki / "concepts" / "alpha.md", dest=wiki / "entities" / "alpha.md",
    ))
    assert "alpha" in read_index_slugs(index_path)


def test_start_watcher_missing_dependency_returns_none(kb):
    """依赖缺失（watchdog 不可用）时静默降级，不抛异常。"""
    _, wiki, _, _ = kb
    with patch("agent.knowledge.index._load_watcher_cls", return_value=None):
        assert start_incremental_index_watcher(wiki) is None


def test_load_watcher_cls_dependency_missing():
    # _load_watcher_cls 内部 `from importlib import import_module`（局部绑定），
    # 须 patch 模块命名空间下的绑定名才能生效
    with patch("agent.knowledge.index.import_module", side_effect=ImportError("no watchdog")):
        assert index_mod._load_watcher_cls() is None
