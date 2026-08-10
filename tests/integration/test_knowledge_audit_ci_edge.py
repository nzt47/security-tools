"""知识审计边界用例 · 集成测试（模拟真实 CI 断链/过期场景）。

与 tests/unit/test_knowledge_audit_edge.py 互补：单元测试直调 lint_all 验证
边界语义；本文件用 subprocess 跑完整 CLI 链路（python -m agent.knowledge
audit --json，与 .github/workflows/ci.yml 的 knowledge-audit-smoke job 同口径），
验证「构造含边界问题的真实卡片库 → 子进程巡检 → 结构化 JSON 产物」全链路。

覆盖（评估标准）：
- 断链边界组：自环不算断链、归档链接可解析、重复链接按引用次数计、
  多引用方各计 1 条；
- 断链洪泛封顶：11 条 → 扣分封顶 20；
- 过期边界组：恰逢阈值不算、超阈值算、draft 跳过、非法日期跳过；
- 综合边界库：断链 7 + 过期 7 + 孤儿 1 → 健康分 64.0（score_breakdown 一致）。

所有用例在 tmp_path 隔离建库，绝不触碰仓库真实 knowledge/。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from agent.knowledge.card import CardStore
from agent.knowledge.schema import Card

_REPO_ROOT = Path(__file__).resolve().parents[2]


def make_card(
    title: str = "卡片",
    slug: str = "",
    status: str = "current",
    links=None,
    date_str: str = "",
) -> Card:
    """构造卡片（title 与 slug 对齐，规避 slugify 消歧校验）。"""
    slug = slug or title
    return Card(
        title=slug,
        slug=slug,
        status=status,
        type="concepts",
        source="inbox/ci-edge-test.md",
        date=date_str or date.today().isoformat(),
        tags=[],
        links=links if links is not None else [],
        contradictions=[],
        insight=f"{slug} 的一句话核心洞见",
    )


def _drop_index_entry(index_path: Path, slug: str) -> None:
    """幂等删除 index.md 中某卡片条目（模拟归档后索引同步，避免漂移）。"""
    text = index_path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip() != f"- [[{slug}]]"]
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _audit_cli(wiki: Path, index: Path, reports: Path) -> dict:
    """子进程跑完整 CLI 审计（--json），返回解析后的 JSON 产物。

    【不易】与 CI knowledge-audit-smoke 同口径：exit 0 断言 + JSON 落盘。
    cwd 固定仓库根（与 CI 一致），wiki/index 用绝对路径隔离。
    """
    out = index.parent / "audit.json"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "agent.knowledge", "audit",
         "--wiki", str(wiki), "--index", str(index),
         "--reports-dir", str(reports), "--no-email", "--json", str(out)],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(_REPO_ROOT), env=env, timeout=120,
    )
    assert proc.returncode == 0, f"CLI 审计失败: {proc.stderr}"
    return json.loads(out.read_text(encoding="utf-8"))


# ════════════════════════════════════════════════════════════
#  断链边界组（CI 子进程全链路）
# ════════════════════════════════════════════════════════════

def test_ci_audit_broken_link_edge_group(tmp_path):
    """自环/归档不判断链；重复链接按引用次数计；多引用方各计 1 条。

    库结构（tmp_path/kb/wiki）：
        self-loop → [self-loop]                自环（目标存在）
        target → []                             归档目标
        ref  → [archives/target]                归档链接（可解析）
        dup  → [ghost-a, ghost-a, ghost-a]      重复 3 次 → 3 条
        b/c  → [ghost-b]                        双引用方 → 各 1 条
    预期 JSON: broken_links 共 5 条。
    """
    root = tmp_path / "kb"
    store = CardStore(root / "wiki")
    store.create(make_card(slug="self-loop", links=["self-loop"]))
    store.create(make_card(slug="target"))
    store.create(make_card(slug="ref", links=["archives/target"]))
    store.create(make_card(slug="dup", links=["ghost-a", "ghost-a", "ghost-a"]))
    store.create(make_card(slug="b", links=["ghost-b"]))
    store.create(make_card(slug="c", links=["ghost-b"]))
    store.transition("target", "archive")       # 移入 archives/
    _drop_index_entry(root / "index.md", "target")

    payload = _audit_cli(root / "wiki", root / "index.md", root / "reports")

    assert len(payload["broken_links"]) == 5
    to_slugs = [b["to_slug"] for b in payload["broken_links"]]
    assert to_slugs.count("ghost-a") == 3       # 重复链接按引用次数计
    assert to_slugs.count("ghost-b") == 2       # 双引用方各计 1
    # 自环/归档链接不计入断链
    assert "self-loop" not in to_slugs and "archives/target" not in to_slugs
    assert payload["score_breakdown"]["broken_links"] == 10  # 5×2


def test_ci_audit_broken_link_flood_cap(tmp_path):
    """断链洪泛：11 条 ×2=22 → 扣分封顶 20（不超扣）。"""
    root = tmp_path / "kb"
    store = CardStore(root / "wiki")
    links = [f"ghost-{i}" for i in range(11)]
    store.create(make_card(slug="flood", links=links))

    payload = _audit_cli(root / "wiki", root / "index.md", root / "reports")

    assert len(payload["broken_links"]) == 11
    assert payload["score_breakdown"]["broken_links"] == 20  # 封顶
    assert payload["health_score"] == 100 - 20 - 2  # 洪泛卡自身孤儿扣 2


# ════════════════════════════════════════════════════════════
#  过期边界组（CI 子进程全链路）
# ════════════════════════════════════════════════════════════

def test_ci_audit_stale_edge_group(tmp_path):
    """恰逢阈值不算；91 天算；draft 跳过；非法日期跳过（stale_days=90）。"""
    root = tmp_path / "kb"
    store = CardStore(root / "wiki")
    today = date.today()
    store.create(make_card(slug="exact90",
                           date_str=(today - timedelta(days=90)).isoformat()))
    store.create(make_card(slug="over91",
                           date_str=(today - timedelta(days=91)).isoformat()))
    store.create(make_card(slug="draft-old", status="draft",
                           date_str=(today - timedelta(days=300)).isoformat()))
    store.create(make_card(slug="bad-date", date_str="not-a-date"))

    payload = _audit_cli(root / "wiki", root / "index.md", root / "reports")

    stale_slugs = {s["slug"] for s in payload["stale_cards"]}
    assert stale_slugs == {"over91"}                 # 恰逢 90 不算、91 算
    assert "draft-old" not in stale_slugs            # draft 状态跳过
    assert "bad-date" not in stale_slugs             # 非法日期跳过
    assert payload["stale_cards"][0]["days_unaccessed"] == 91
    assert payload["score_breakdown"]["stale"] == 3


# ════════════════════════════════════════════════════════════
#  综合边界库（CI 子进程全链路）
# ════════════════════════════════════════════════════════════

def _build_edge_library(store: CardStore, index_path: Path) -> None:
    """构造 17 wiki 卡 + 1 归档卡的综合边界库。

    结构（与单元测试 test_edge_library_combined_health_score 对齐）：
      - 根卡 hub：links=[hub-child-a, hub-child-b, over1..7, exact90,
        draft-old, bad-date, ghost-1, ghost-2]（枢纽给所有卡提供入链；
        自身无入链 → 唯一孤儿）
      - hub-child-a：自环 + 重复 ghost（ghost-a×3）+ 归档引用 archives/arch-t
      - hub-child-b：重复 ghost-b×2（保留重复引用计数语义）
      - 7 张 91 天过期卡（over1..over7）
      - 阈值/草稿/坏日期卡（由 hub 提供入链，避免误判孤儿）
      - 归档卡 arch-t 由 transition 移入 archives/
    预期：断链 7（hub 2 + ghost-a×3 + ghost-b×2）+ 过期 7 + 孤儿 1 →
    健康分 64.0，score_breakdown == {"broken_links": 14, "stale": 20, "orphans": 2}。
    """
    today = date.today()
    old = (today - timedelta(days=91)).isoformat()
    store.create(make_card(slug="hub", links=[
        "hub-child-a", "hub-child-b", "ghost-1", "ghost-2",
        *[f"over{i}" for i in range(1, 8)],
        "exact90", "draft-old", "bad-date",
    ]))
    store.create(make_card(slug="hub-child-a",
                           links=["hub-child-a", "ghost-a", "ghost-a", "ghost-a",
                                  "archives/arch-t"]))
    store.create(make_card(slug="hub-child-b",
                           links=["ghost-b", "ghost-b"]))
    for i in range(1, 8):
        store.create(make_card(slug=f"over{i}", date_str=old))
    store.create(make_card(slug="exact90",
                           date_str=(today - timedelta(days=90)).isoformat()))
    store.create(make_card(slug="draft-old", status="draft",
                           date_str=(today - timedelta(days=300)).isoformat()))
    store.create(make_card(slug="bad-date", date_str="not-a-date"))
    store.create(make_card(slug="arch-t"))
    store.transition("arch-t", "archive")
    _drop_index_entry(index_path, "arch-t")


def test_ci_audit_combined_edge_library(tmp_path):
    """综合边界库经 CLI 子进程审计：健康分 64.0 与扣分明细一致。"""
    root = tmp_path / "kb"
    store = CardStore(root / "wiki")
    index_path = root / "index.md"
    _build_edge_library(store, index_path)

    payload = _audit_cli(root / "wiki", index_path, root / "reports")

    assert payload["health_score"] == 64.0
    assert payload["score_breakdown"] == {
        "broken_links": 14,   # 断链 7 条 ×2（重复/双引用/洪泛计数语义）
        "stale": 20,          # 过期 7×3=21 → 封顶 20
        "orphans": 2,         # 孤儿 1 张 ×2
    }
    assert len(payload["stale_cards"]) == 7
    assert len(payload["broken_links"]) == 7
    assert payload["ok"] is False
