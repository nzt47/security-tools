"""模拟人工裁决未裁决矛盾流程（任务5 · resolve_conflict 演示）。

还原 demo 场景：卡片盒写作法 与 知识降噪设计 存在矛盾——AI 在深度讨论中
只标记 [冲突: 知识降噪设计]（contradictions status=conflict），**不自动裁决**
（AGENTS.md §6.2 人机边界：AI 只簿记，判断与裁决由人完成）。

人工裁决流程（全部调用由人触发）：
  1. list_unresolved(card_store)        查看全部未裁决矛盾
  2. 人工决策：判定「卡片盒写作法」获胜（decision_slug），「知识降噪设计」被否
  3. resolve_conflict(source, target, decision, card_store=store)   裁决
  4. 验证：双方矛盾置 resolved + 被否卡归档（自动重链）+ list_unresolved 清空
     + log.md 登记裁决记录

用法（仓库根目录下）：
    $env:PYTHONIOENCODING="utf-8"
    python scripts/dev/demo_conflict_resolve.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# 使脚本可在任意 cwd 下运行：把仓库根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.knowledge.card import CardStore  # noqa: E402
from agent.knowledge.conflict import list_unresolved, resolve_conflict  # noqa: E402
from agent.knowledge.schema import Card  # noqa: E402


def _banner(title: str) -> None:
    print("\n" + "═" * 64)
    print(title)
    print("═" * 64)


def _make_card(slug: str, links, contradictions=None) -> Card:
    """构造 current 态卡片（结构与 demo 产卡一致）。"""
    return Card(
        title=slug,
        slug=slug,
        status="current",
        type="concepts",
        source="discussion:卡片盒写作法.discussion.md",
        date="2026-08-10",
        links=links,
        contradictions=contradictions or [],
        insight="一句话核心洞见",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="kb-conflict-demo-") as tmp:
        root = Path(tmp)
        store = CardStore(root / "wiki")
        print(f"临时知识根: {root}（演示结束自动清理）")

        # ── 0. 构造矛盾场景（还原 demo 产卡） ─────────────────
        _banner("[Step0] 构造矛盾场景：AI 已标记矛盾（status=conflict）")
        store.create(_make_card("卡片盒写作法",
                                links=["知识降噪设计"],
                                contradictions=[{
                                    "target_slug": "知识降噪设计",
                                    "status": "conflict",
                                    "summary": "链接泛滥即噪音，与降噪设计冲突",
                                }]))
        store.create(_make_card("知识降噪设计", links=["卡片盒写作法"]))
        print("  已建卡: 卡片盒写作法（contradictions=[冲突: 知识降噪设计]）、知识降噪设计")

        # ── 1. 人工查看未裁决矛盾 ─────────────────────────────
        _banner("[Step1] 人工查看未裁决矛盾（list_unresolved）")
        pending = list_unresolved(store)
        print(f"  未裁决矛盾 {len(pending)} 条: {pending}")
        assert len(pending) == 1, "应恰好 1 条未裁决矛盾"

        # ── 2. 人工决策 + 调用 resolve_conflict ───────────────
        _banner("[Step2] 人工裁决（resolve_conflict，decision=卡片盒写作法）")
        source, target, decision = "卡片盒写作法", "知识降噪设计", "卡片盒写作法"
        print(f"  调用: resolve_conflict(source={source!r}, target={target!r}, "
              f"decision={decision!r}, card_store=store)")
        ok = resolve_conflict(source, target, decision, card_store=store)
        print(f"  返回: ok={ok}")
        assert ok is True, "裁决应成功"

        # ── 3. 验证裁决结果 ───────────────────────────────────
        _banner("[Step3] 验证裁决结果")
        # 裁决卡仍在线，矛盾已 resolved 并关联裁决卡
        winner = store.get("卡片盒写作法")
        entry = next(it for it in winner.contradictions
                     if it["target_slug"] == "知识降噪设计")
        print(f"  裁决卡「卡片盒写作法」: status={winner.status} "
              f"矛盾→{entry['status']} decision_slug={entry.get('decision_slug')}")
        assert entry["status"] == "resolved" and entry["decision_slug"] == "卡片盒写作法"

        # 被否卡已归档（wiki → archives/，自动重链）
        assert store.get("知识降噪设计") is None, "被否卡应移出 wiki"
        archived = store.get("archives/知识降噪设计")
        print(f"  被否卡「知识降噪设计」: wiki 中已移除 → archives/ 归档 "
              f"status={archived.status}")
        assert archived is not None and archived.status == "archive"

        # 未裁决矛盾清空
        assert list_unresolved(store) == []
        print(f"  未裁决矛盾: {len(list_unresolved(store))} 条（已清空）")

        # log.md 登记裁决
        log_text = (root / "log.md").read_text(encoding="utf-8")
        assert "resolve_conflict" in log_text
        print(f"  log.md 已登记 resolve_conflict（共 {len(log_text.strip().splitlines())} 行）")

        # ── 4. 复检健康分：矛盾已裁决 → 不再扣分 ───────────────
        _banner("[Step4] 裁决后复检（run_audit 健康分）")
        from agent.knowledge.workflow import WorkflowRunner

        report = WorkflowRunner(knowledge_root=root).run_audit()
        print(f"  健康分={report['health_score']} 扣分明细={report['score_breakdown']} "
              f"未裁决矛盾={len(report['unresolved_conflicts'])}")
        assert "conflicts" not in report["score_breakdown"], "矛盾已裁决不应再扣分"

    print("\n人工裁决流程演示完成 ✓（标记→查看→裁决→归档→复检 全链路）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
