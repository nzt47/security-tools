"""断链 + 过期数据边界场景 mock 库，跑完整 audit 演示（任务5/7 边界用例）。

覆盖边界（验证 run_audit 检测/健康分推导的正确性）：
    【断链边界】
      - 自环链接（卡链接自己）        → 不算断链（目标存在）
      - 归档链接（archives/xxx）      → 不算断链（归档目标可解析）
      - 重复链接（links 含 3 次幽灵a）→ 计 3 条断链（按引用次数计数）
      - 多引用方指向同一幽灵目标       → 每张引用卡各计 1 条
      - 断链洪泛（11 条）             → 11×2=22 → 封顶 20 分（独立 mini 库验证）
    【过期边界】（stale_days=90）
      - 恰逢阈值 90 天（current）     → 不算过期（days > 90 才计）
      - 超过 1 天 91 天（current）    → 算过期
      - draft 状态超期                → 不算（仅 status=current 检查）
      - 非法日期                      → 跳过不阻断巡检
      - 7 张 current 超期             → 7×3=21 → 封顶 20 分

预期（主库）：
    断链 7 条×2=14 分、过期 7 条→封顶 20 分、孤儿 1 张×2=2 分
    health_score = 100 - 36 = 64.0
    score_breakdown = {"broken_links": 14, "stale": 20, "orphans": 2}

用法（仓库根目录下）：
    $env:PYTHONIOENCODING="utf-8"
    python scripts/dev/demo_audit_edge.py
"""

from __future__ import annotations

import logging
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

# 使脚本可在任意 cwd 下运行：把仓库根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.knowledge.card import CardStore  # noqa: E402
from agent.knowledge.lint import lint_all, score_breakdown  # noqa: E402
from agent.knowledge.schema import Card  # noqa: E402
from agent.knowledge.workflow import WorkflowRunner  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
)

STALE_DAYS = 90


def _banner(title: str) -> None:
    print("\n" + "═" * 64)
    print(title)
    print("═" * 64)


def _make_card(slug: str, links, status: str = "current",
               card_date: str | None = None) -> Card:
    """构造卡片（title=slug 规避 slugify 消歧）。"""
    return Card(
        title=slug,
        slug=slug,
        status=status,
        type="concepts",
        source="mock/edge.md",
        date=card_date or date.today().isoformat(),
        links=links,
        contradictions=[],
        insight=f"{slug} 的一句话核心洞见",
    )


def _drop_index_entry(idx: Path, slug: str) -> None:
    """幂等删除 index.md 中某卡片条目（模拟引擎归档同步，保证漂移归零）。"""
    text = idx.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip() != f"- [[{slug}]]"]
    idx.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_edge_library(root: Path) -> CardStore:
    """构造断链/过期边界全覆盖 mock 库（wiki 17 张 + 归档 1 张）。"""
    store = CardStore(root / "wiki")
    today = date.today()

    # ── 断链边界组 ────────────────────────────────────────────
    # 根卡：只出链给枢纽卡（提供入链），自身无入链 → 孤儿 1 张（链路起点）
    store.create(_make_card("根卡", links=["枢纽卡"]))
    # 枢纽卡：为其余卡提供入链（非孤儿）；同时带 2 条断链（幽灵a/幽灵b）
    store.create(_make_card("枢纽卡", links=[
        "自环卡片", "重复断链卡", "双引用卡a", "双引用卡b", "引用归档卡",
        "过期卡1", "过期卡2", "过期卡3", "过期卡4", "过期卡5",
        "过期卡6", "过期卡7", "恰逢阈值卡", "draft超期卡", "非法日期卡",
        "幽灵a", "幽灵b",
    ]))
    store.create(_make_card("自环卡片", links=["自环卡片"]))          # 自环 → 非断链
    store.create(_make_card("重复断链卡", links=["幽灵a", "幽灵a", "幽灵a"]))  # 重复 → 3 条
    store.create(_make_card("双引用卡a", links=["幽灵b"]))            # 同幽灵目标
    store.create(_make_card("双引用卡b", links=["幽灵b"]))            # 同幽灵目标
    store.create(_make_card("引用归档卡", links=["archives/归档目标卡"]))  # 归档 → 非断链
    store.create(_make_card("归档目标卡", links=[]))
    store.transition("归档目标卡", "archive")                          # 移入 archives/
    _drop_index_entry(root / "index.md", "归档目标卡")                # 同步索引，漂移归零

    # ── 过期边界组（阈值 90 天） ───────────────────────────────
    for i in range(1, 8):                                            # 7 张超期 → 封顶 20
        store.create(_make_card(
            f"过期卡{i}", links=[],
            card_date=(today - timedelta(days=91)).isoformat(),
        ))
    store.create(_make_card(                                          # 恰逢阈值 → 不算过期
        "恰逢阈值卡", links=[], card_date=(today - timedelta(days=90)).isoformat()))
    store.create(_make_card(                                          # draft 超期 → 不算
        "draft超期卡", links=[], status="draft",
        card_date=(today - timedelta(days=120)).isoformat()))
    store.create(_make_card("非法日期卡", links=[], card_date="not-a-date"))  # 跳过

    return store


def _verify_edge_library(root: Path, report: dict) -> None:
    """断言边界行为（断链计数规则 + 过期阈值规则 + 健康分推导）。"""
    # ── 断链边界 ──
    broken = report["broken_links"]
    assert len(broken) == 7, f"预期 7 条断链，实际 {len(broken)}: {broken}"
    from_slugs = {b["from_slug"] for b in broken}
    assert from_slugs == {"枢纽卡", "重复断链卡", "双引用卡a", "双引用卡b"}, from_slugs
    assert broken.count({"from_slug": "重复断链卡", "to_slug": "幽灵a"}) == 3, \
        "重复链接应按引用次数各计 1 条断链"
    assert "自环卡片" not in from_slugs, "自环链接（目标存在）不应算断链"
    assert "引用归档卡" not in from_slugs, "归档链接（archives/ 可解析）不应算断链"
    print("  ✓ 断链边界：7 条（重复计 3、双引用方计 2、枢纽 2）；自环/归档不计")

    # ── 过期边界 ──
    stale_slugs = {s["slug"] for s in report["stale_cards"]}
    assert stale_slugs == {f"过期卡{i}" for i in range(1, 8)}, stale_slugs
    assert "恰逢阈值卡" not in stale_slugs, "恰逢 90 天阈值不应算过期（需 > 90）"
    assert "draft超期卡" not in stale_slugs, "draft 状态超期不应检查"
    assert "非法日期卡" not in stale_slugs, "非法日期应跳过"
    print("  ✓ 过期边界：恰 7 张 current 超期；阈值 90/draft/非法日期均不误判")

    # ── 健康分推导（断链 14 + 过期封顶 20 + 孤儿 2 = 36） ──
    assert report["total_cards"] == 17, report["total_cards"]
    assert report["health_score"] == 64.0, report["health_score"]
    assert report["score_breakdown"] == {"broken_links": 14, "stale": 20, "orphans": 2}, \
        report["score_breakdown"]
    assert report["ok"] is False
    print("  ✓ 健康分 64.0 = 100 - 断链14 - 过期20(封顶) - 孤儿2；"
          "score_breakdown 与日志一致")


def _verify_caps() -> None:
    """独立 mini 库：断链洪泛触发封顶（11×2=22 → 封顶 20）。"""
    with tempfile.TemporaryDirectory(prefix="kb-cap-") as tmp:
        root = Path(tmp)
        store = CardStore(root / "wiki")
        store.create(_make_card("洪泛卡", links=[f"幽灵{i}" for i in range(1, 12)]))
        hr = lint_all(store, index_path=str(root / "index.md"))
        assert len(hr.broken_links) == 11, hr.broken_links
        assert hr.health_score == 78.0, hr.health_score          # 100-20(封顶)-2(孤儿)
        assert score_breakdown(hr) == {"broken_links": 20, "orphans": 2}
        print("  ✓ 断链封顶：11 条×2=22 → 封顶 20 分（未超扣）")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="kb-audit-edge-") as tmp:
        root = Path(tmp)
        print(f"临时知识根: {root}（演示结束自动清理）")

        _banner("[Step1] 构造断链+过期边界 mock 库（wiki 17 卡 + 归档 1 卡）")
        store = _build_edge_library(root)

        _banner("[Step2] WorkflowRunner.run_audit（检测/计算/报告三阶段耗时日志）")
        report = WorkflowRunner(knowledge_root=root).run_audit()
        print(f"  total_cards={report['total_cards']} 健康分={report['health_score']} "
              f"断链={len(report['broken_links'])} 过期={len(report['stale_cards'])} "
              f"孤儿={len(report['orphans'])}")

        _banner("[Step3] 边界行为断言")
        _verify_edge_library(root, report)

        _banner("[Step4] 封顶行为独立验证（断链洪泛）")
        _verify_caps()

    print("\n边界 mock audit 演示完成 ✓（断链/过期边界 + 封顶 + 三阶段耗时日志）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
