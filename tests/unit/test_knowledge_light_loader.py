"""P0 内存优化 · 轻量检测视图（light_loader 插件）回归测试。

覆盖（评估标准）：
- parse_light：单文件 frontmatter → CardLight 六字段提取（含 archives/ 链接）；
- scan_light_cards：排序与 CardStore.list 一致、损坏卡跳过不阻断；
- CardStore.list_light：与 list() 在检测六字段上语义一致；
- lint_all 默认 light_read=True（轻量视图）与 light_read=False（完整卡）
  产出的五类检测结果完全一致（语义不变，仅驻留字段不同）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.knowledge.card import CardStore
from agent.knowledge.light_loader import CardLight, parse_light, scan_light_cards
from agent.knowledge.lint import lint_all
from agent.knowledge.schema import Card, slugify


def make_card(title: str, *, status="current", links=None, date_str="") -> Card:
    slug = slugify(title)
    return Card(
        title=slug, slug=slug, status=status, type="concepts",
        source="inbox/light-test.md",
        date=date_str or "2026-08-01", tags=[],
        links=links if links is not None else [],
        contradictions=[], insight=f"{slug} 的洞见",
    )


# ── parse_light ──────────────────────────────────────────────

def test_parse_light_extracts_six_fields(tmp_path):
    """parse_light：frontmatter 六字段正确提取，insight/content 不驻留。"""
    path = tmp_path / "a.md"
    path.write_text(
        "---\nslug: alpha\nstatus: current\ntype: concepts\ndate: 2026-08-01\n"
        "links:\n  - beta\n  - archives/old\ntags: []\ncontradictions: []\n"
        "insight: 洞见\n---\n\n正文内容很长很长\n",
        encoding="utf-8",
    )
    light = parse_light(path.read_text(encoding="utf-8"))
    assert isinstance(light, CardLight)
    assert light.slug == "alpha"
    assert light.status == "current"
    assert light.type == "concepts"
    assert light.date == "2026-08-01"
    assert light.links == ["beta", "archives/old"]  # archives/ 前缀保留
    assert not hasattr(light, "insight")            # 大字段不驻留
    assert not hasattr(light, "content")


def test_parse_light_invalid_frontmatter_raises(tmp_path):
    """损坏卡（无 frontmatter）→ ValueError（调用方按损坏卡跳过）。"""
    path = tmp_path / "bad.md"
    path.write_text("没有 frontmatter 的正文\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_light(path.read_text(encoding="utf-8"))


# ── scan_light_cards ─────────────────────────────────────────

def test_scan_light_cards_order_and_skip_corrupt(tmp_path):
    """scan_light_cards：按 slug 字典序，损坏卡跳过不阻断。"""
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "entities").mkdir()
    (wiki / "concepts" / "beta.md").write_text(
        "---\nslug: beta\nstatus: current\ntype: concepts\ndate: 2026-08-01\nlinks: []\n---\n",
        encoding="utf-8",
    )
    (wiki / "concepts" / "alpha.md").write_text(
        "---\nslug: alpha\nstatus: current\ntype: concepts\ndate: 2026-08-01\nlinks: []\n---\n",
        encoding="utf-8",
    )
    (wiki / "concepts" / "corrupt.md").write_text("无 frontmatter\n", encoding="utf-8")
    (wiki / "entities" / "delta.md").write_text(
        "---\nslug: delta\nstatus: current\ntype: entities\ndate: 2026-08-01\nlinks: []\n---\n",
        encoding="utf-8",
    )
    cards = scan_light_cards(wiki)
    # 排序：类型目录序（concepts 在 entities 前）+ 组内 slug 字典序
    assert [c.slug for c in cards] == ["alpha", "beta", "delta"]
    assert "corrupt" not in [c.slug for c in cards]  # 损坏卡跳过


def test_scan_light_cards_parallel_same_result(tmp_path):
    """parallel=True 与串行结果完全一致（保序 + 损坏卡跳过）。"""
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    for i in range(20):
        (wiki / "concepts" / f"c{i}.md").write_text(
            f"---\nslug: c{i}\nstatus: current\ntype: concepts\ndate: 2026-08-01\n"
            f"links: []\n---\n",
            encoding="utf-8",
        )
    serial = scan_light_cards(wiki)
    parallel = scan_light_cards(wiki, parallel=True)
    assert [c.slug for c in serial] == [c.slug for c in parallel]


def test_scan_light_cards_max_workers_respected(tmp_path):
    """max_workers 显式指定：覆盖默认 min(8,·)，且保序/跳坏语义不变。"""
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    for i in range(12):
        text = ("---\nslug: c{i}\nstatus: current\ntype: concepts\ndate: 2026-08-01\n"
                f"links: []\n---\n").replace("{i}", str(i))
        # 固定宽度文件名：字典序 == 数值序（避免 c10 < c2 干扰排序断言）
        (wiki / "concepts" / f"c{i:02d}.md").write_text(text, encoding="utf-8")
    (wiki / "concepts" / "corrupt.md").write_text("无 frontmatter\n", encoding="utf-8")

    default = scan_light_cards(wiki, parallel=True)          # min(8, 13) = 8
    explicit = scan_light_cards(wiki, parallel=True, max_workers=3)
    capped = scan_light_cards(wiki, parallel=True, max_workers=999)  # 超过卡片数
    assert [c.slug for c in default] == [c.slug for c in explicit]
    assert [c.slug for c in default] == [c.slug for c in capped]
    assert len(default) == 12                      # 损坏卡跳过
    assert default[0].slug == "c0" and default[-1].slug == "c11"  # 保序


# ── CardStore.list_light 与 list 一致性 ──────────────────────

def test_list_light_matches_list_on_detection_fields(tmp_path):
    """list_light 与 list 在检测六字段上逐卡一致（含链接列表）。"""
    store = CardStore(tmp_path / "wiki")
    store.create(make_card("卡A", links=["卡B", "archives/old"]))
    store.create(make_card("卡B"))
    full = store.list()
    light = store.list_light()
    assert len(full) == len(light) == 2
    by_slug = {c.slug: c for c in light}
    for c in full:
        l = by_slug[c.slug]
        assert l.status == c.status
        assert l.type == c.type
        assert l.date == c.date
        assert l.links == c.links
        assert l.contradictions == c.contradictions


# ── lint_all 轻量视图语义不变 ────────────────────────────────

def test_lint_all_light_read_semantics_unchanged(tmp_path):
    """lint_all light_read=True（轻量视图）与 False（完整卡）结果完全一致。

    构造：自环 + 重复断链 + 过期 + 孤儿混合库，验证五类检测在两条路径
    下产出相同 report（语义不变，仅驻留字段不同）。
    """
    from datetime import date, timedelta

    store = CardStore(tmp_path / "wiki")
    index_path = tmp_path / "index.md"
    old = (date.today() - timedelta(days=91)).isoformat()
    store.create(make_card("枢纽", links=["引用A", "引用B", "幽灵1", "幽灵2"]))
    store.create(make_card("引用A", links=["幽灵1", "幽灵1", "archives/old"]))
    store.create(make_card("引用B", links=["幽灵2"]))
    store.create(make_card("过期卡", date_str=old))

    r_light = lint_all(store, index_path=index_path, light_read=True)
    r_full = lint_all(store, index_path=index_path, light_read=False)

    assert r_light.total_cards == r_full.total_cards == 4
    assert r_light.broken_links == r_full.broken_links  # 断链 5 条（幽灵1×3+幽灵2×2）
    assert r_light.orphans == r_full.orphans            # 孤儿 2 张（枢纽、过期卡无入链）
    assert [s["slug"] for s in r_light.stale_cards] == [s["slug"] for s in r_full.stale_cards]
    assert r_light.index_drift == r_full.index_drift
    assert r_light.health_score == r_full.health_score
