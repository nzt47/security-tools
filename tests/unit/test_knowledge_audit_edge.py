"""任务5/7 · 知识审计边界回归测试（断链/过期边界 + 扣分封顶）。

把 scripts/dev/demo_audit_edge.py 手工验证的边界行为固化为正式测试套件，
防止 lint_all / find_broken_links / find_stale_cards / 健康分推导后续回归：

【断链计数规则】
- 自环链接（卡链接自己）→ 目标存在，不算断链。
- 归档链接（archives/xxx）→ 归档目标可解析，不算断链。
- 重复链接（links 含 3 次同一幽灵目标）→ 按引用次数计 3 条。
- 多引用方指向同一幽灵目标 → 每张引用卡各计 1 条。
- 断链洪泛（11 条）→ 11×2=22 → 按类封顶 20 分。

【过期阈值规则】（stale_days=90）
- 恰逢 90 天（days==90）→ 不算（需 days > 90）。
- 91 天 → 算过期。
- draft 状态超期 → 不算（仅 status=current 检查）。
- 非法日期 → 跳过不阻断巡检。
- 过期洪泛（7 条）→ 7×3=21 → 按类封顶 20 分。

【综合场景】断链 7 + 过期 7 + 孤儿 1 → 健康分 64.0。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from agent.knowledge.card import CardStore
from agent.knowledge.lint import lint_all, score_breakdown
from agent.knowledge.schema import Card, slugify


def make_card(
    title: str = "卡片",
    slug: str = "",
    status: str = "current",
    links=None,
    date_str: str = "",
) -> Card:
    """构造卡片（title 与 slug 对齐，规避 slugify 消歧校验）。"""
    slug = slug or slugify(title)
    return Card(
        title=slug,
        slug=slug,
        status=status,
        type="concepts",
        source="inbox/edge-test.md",
        date=date_str or date.today().isoformat(),
        tags=[],
        links=links if links is not None else [],
        contradictions=[],
        insight=f"{slug} 的一句话核心洞见",
    )


@pytest.fixture
def kb(tmp_path):
    """临时知识库：返回 (store, index_path)。"""
    root = tmp_path / "kb"
    store = CardStore(root / "wiki")
    return store, root / "index.md"


def _drop_index_entry(index_path: Path, slug: str) -> None:
    """幂等删除 index.md 中某卡片条目（模拟归档后索引同步，避免漂移）。"""
    text = index_path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip() != f"- [[{slug}]]"]
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ════════════════════════════════════════════════════════════
#  断链计数规则（边界）
# ════════════════════════════════════════════════════════════

def test_broken_self_loop_not_counted(kb):
    """自环链接：目标=自身存在 → 不算断链；且自环算作自身入链（免孤儿）。"""
    store, index_path = kb
    store.create(make_card("自环卡", slug="self-loop", links=["self-loop"]))
    report = lint_all(store, index_path=index_path)
    assert report.broken_links == []
    assert report.orphans == []               # 自环链接计为自身入链
    assert report.health_score == 100.0


def test_broken_archives_link_not_counted(kb):
    """归档链接 archives/<slug>：归档目标可解析 → 不算断链。"""
    store, index_path = kb
    store.create(make_card("目标卡", slug="target"))
    store.create(make_card("引用卡", slug="ref", links=["archives/target"]))
    store.transition("target", "archive")           # 移入 archives/
    _drop_index_entry(index_path, "target")         # 同步索引，防漂移干扰
    report = lint_all(store, index_path=index_path)
    assert report.broken_links == []


def test_broken_duplicate_links_counted(kb):
    """重复链接：同一幽灵目标出现 3 次 → 计 3 条断链（按引用次数）。"""
    store, index_path = kb
    store.create(make_card("重复卡", slug="dup",
                           links=["ghost", "ghost", "ghost"]))
    report = lint_all(store, index_path=index_path)
    assert len(report.broken_links) == 3
    assert all(b == {"from_slug": "dup", "to_slug": "ghost"}
               for b in report.broken_links)


def test_broken_multi_referrer_same_target(kb):
    """多引用方指向同一幽灵目标 → 每张引用卡各计 1 条。"""
    store, index_path = kb
    store.create(make_card("卡A", slug="a", links=["ghost"]))
    store.create(make_card("卡B", slug="b", links=["ghost"]))
    report = lint_all(store, index_path=index_path)
    assert len(report.broken_links) == 2
    assert {b["from_slug"] for b in report.broken_links} == {"a", "b"}


def test_broken_flood_capped_at_20(kb):
    """断链洪泛：11 条×2=22 → 封顶 20 分（不超扣）。"""
    store, index_path = kb
    store.create(make_card("洪泛卡", slug="flood",
                           links=[f"ghost{i}" for i in range(1, 12)]))
    report = lint_all(store, index_path=index_path)
    assert len(report.broken_links) == 11
    assert report.health_score == 78.0            # 100 - 20(封顶) - 2(孤儿)
    assert score_breakdown(report)["broken_links"] == 20


# ════════════════════════════════════════════════════════════
#  过期阈值规则（边界）
# ════════════════════════════════════════════════════════════

def test_stale_exact_threshold_not_stale(kb):
    """恰逢 90 天（days==90）→ 不算过期（需 > 90）。"""
    store, index_path = kb
    d = (date.today() - timedelta(days=90)).isoformat()
    store.create(make_card("恰好卡", slug="at-threshold", date_str=d))
    report = lint_all(store, index_path=index_path)
    assert report.stale_cards == []


def test_stale_over_threshold_counted(kb):
    """91 天（days==91 > 90）→ 算过期。"""
    store, index_path = kb
    d = (date.today() - timedelta(days=91)).isoformat()
    store.create(make_card("过期卡", slug="stale-one", date_str=d))
    report = lint_all(store, index_path=index_path)
    assert [s["slug"] for s in report.stale_cards] == ["stale-one"]
    assert report.stale_cards[0]["days_unaccessed"] == 91


def test_stale_draft_status_skipped(kb):
    """draft 状态超期 → 不检查（仅 status=current）。"""
    store, index_path = kb
    d = (date.today() - timedelta(days=120)).isoformat()
    store.create(make_card("草稿卡", slug="draft-old", status="draft", date_str=d))
    report = lint_all(store, index_path=index_path)
    assert report.stale_cards == []


def test_stale_invalid_date_skipped(kb):
    """非法日期 → 跳过，不阻断巡检。"""
    store, index_path = kb
    store.create(make_card("坏日期卡", slug="bad-date", date_str="not-a-date"))
    report = lint_all(store, index_path=index_path)
    assert report.stale_cards == []


def test_stale_flood_capped_at_20(kb):
    """过期洪泛：7 条×3=21 → 封顶 20 分。"""
    store, index_path = kb
    d = (date.today() - timedelta(days=91)).isoformat()
    for i in range(1, 8):
        store.create(make_card(f"过期卡{i}", slug=f"stale{i}", date_str=d))
    report = lint_all(store, index_path=index_path)
    assert len(report.stale_cards) == 7
    assert score_breakdown(report)["stale"] == 20  # 封顶（7×3=21→20）


# ════════════════════════════════════════════════════════════
#  综合场景（对齐 demo_audit_edge.py 的 64.0）
# ════════════════════════════════════════════════════════════

def _build_edge_library(store, index_path) -> None:
    """构造边界全覆盖 mock 库（wiki 17 卡 + 归档 1 卡）。"""
    today = date.today()
    # 根卡：只出链给枢纽卡（提供入链），自身无入链 → 孤儿 1 张
    store.create(make_card("根卡", slug="root", links=["hub"]))
    # 枢纽卡：为其余卡提供入链；同时带 2 条断链（ghost-a/ghost-b）
    store.create(make_card("枢纽卡", slug="hub", links=[
        "self-loop", "dup", "ref-a", "ref-b", "ref-arch",
        *[f"stale{i}" for i in range(1, 8)],
        "at-threshold", "draft-old", "bad-date",
        "ghost-a", "ghost-b",
    ]))
    store.create(make_card("自环卡", slug="self-loop", links=["self-loop"]))
    store.create(make_card("重复卡", slug="dup",
                           links=["ghost-a", "ghost-a", "ghost-a"]))
    store.create(make_card("引用卡A", slug="ref-a", links=["ghost-b"]))
    store.create(make_card("引用卡B", slug="ref-b", links=["ghost-b"]))
    store.create(make_card("归档引用卡", slug="ref-arch", links=["archives/arch-target"]))
    store.create(make_card("归档目标卡", slug="arch-target"))
    store.transition("arch-target", "archive")
    _drop_index_entry(index_path, "arch-target")
    # 过期组（7 张 91 天）+ 阈值/草稿/坏日期边界卡
    stale_d = (today - timedelta(days=91)).isoformat()
    for i in range(1, 8):
        store.create(make_card(f"过期卡{i}", slug=f"stale{i}", date_str=stale_d))
    store.create(make_card("恰逢阈值卡", slug="at-threshold",
                           date_str=(today - timedelta(days=90)).isoformat()))
    store.create(make_card("draft超期卡", slug="draft-old", status="draft",
                           date_str=(today - timedelta(days=120)).isoformat()))
    store.create(make_card("非法日期卡", slug="bad-date", date_str="not-a-date"))


def test_edge_library_combined_health_score(kb):
    """综合边界库：断链 7 + 过期 7 + 孤儿 1 → 健康分 64.0。"""
    store, index_path = kb
    _build_edge_library(store, index_path)
    report = lint_all(store, index_path=index_path)

    # 断链计数：枢纽 2 + 重复 3 + 引用A/B 各 1 = 7 条
    assert len(report.broken_links) == 7
    from_slugs = {b["from_slug"] for b in report.broken_links}
    assert from_slugs == {"hub", "dup", "ref-a", "ref-b"}
    assert "self-loop" not in from_slugs      # 自环不计
    assert "ref-arch" not in from_slugs       # 归档不计

    # 过期：恰 7 张 current 超期；阈值/draft/非法日期不误判
    stale_slugs = {s["slug"] for s in report.stale_cards}
    assert stale_slugs == {f"stale{i}" for i in range(1, 8)}
    assert "at-threshold" not in stale_slugs
    assert "draft-old" not in stale_slugs
    assert "bad-date" not in stale_slugs

    # 健康分推导：断链 14 + 过期封顶 20 + 孤儿 2 = 36 → 64.0
    assert report.total_cards == 17
    assert report.health_score == 64.0
    assert score_breakdown(report) == {"broken_links": 14, "stale": 20, "orphans": 2}
