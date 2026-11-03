# -*- coding: utf-8 -*-
"""S4 前置：分层加载的**覆盖率基线**（只读，不改任何行为）。

C-8 的要求是「接入分层装配之前必须先有回归基线」，不然改了模型看到的内容也说不清影响。
这条脚本量三件事，并对每一项给出可否接入的判据：

1. **卡片覆盖率**：多少技能有 CARD.md（有卡 = L1 是精编的；没有 = 派生）；
2. **L1 信息完整度**：五面（何时使用/步骤/参数/约束/示例）里 L1 覆盖了几面；
   判据：**触发 + 步骤**两面齐全才算"模型看了能动手"；
3. **省多少**：L1 相对 L2 的字符节省率（判断分层值不值得做）。

用法：python scripts/audit/skill_layer_coverage.py [--json reports/skill_layer_coverage.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.skills_mgmt import layered  # noqa: E402

FACETS = ("何时使用", "步骤", "参数", "约束", "示例")


def _facets_present(text: str) -> list:
    return [f for f in FACETS if f in text]


def measure(repo: Path) -> dict:
    rows = []
    for child in sorted(repo.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        l1 = layered.load_layer(child.name, "L1", repo=repo)
        l2 = layered.load_layer(child.name, "L2", repo=repo)
        if not l1.get("ok") or not l2.get("ok"):
            continue
        present = _facets_present(str(l1.get("text") or ""))
        rows.append({
            "skill_id": child.name,
            "source": l1.get("source"),
            "l1_chars": l1.get("chars", 0),
            "l2_chars": l2.get("chars", 0),
            "saving_pct": (round((l2["chars"] - l1["chars"]) / l2["chars"] * 100, 1)
                           if l2.get("chars") else 0.0),
            "facets": present,
            "actionable": "何时使用" in present and "步骤" in present,
        })
    total = len(rows)
    with_card = sum(1 for r in rows if r["source"] == "card")
    actionable = sum(1 for r in rows if r["actionable"])
    sav = [r["saving_pct"] for r in rows if r["l2_chars"]]
    return {
        "skills": total,
        "with_card_md": with_card,
        "derived": total - with_card,
        "actionable_l1": actionable,
        "actionable_pct": round(actionable / total * 100, 1) if total else 0.0,
        "avg_saving_pct": round(sum(sav) / len(sav), 1) if sav else 0.0,
        "rows": rows,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="分层加载覆盖率基线（只读）")
    ap.add_argument("--repo", default=str(layered.DEFAULT_REPO))
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)
    repo = Path(args.repo)
    if not repo.is_dir():
        print("[FAIL] 技能目录不存在：%s" % repo)
        return 2
    data = measure(repo)
    print("[分层基线] 技能 %d 条：CARD.md %d / 派生 %d"
          % (data["skills"], data["with_card_md"], data["derived"]))
    print("[分层基线] L1 可动手（含何时使用 + 步骤）%d 条（%.1f%%）；平均省 %.1f%%"
          % (data["actionable_l1"], data["actionable_pct"], data["avg_saving_pct"]))
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("[OK] 已写入 %s" % out)
    # 判据：可动手率 < 90% 时不建议把装配切到 L1（会丢掉"怎么做"）
    if data["actionable_pct"] < 90:
        print("[WARN] 可动手率 < 90%：先补 CARD.md（intake --llm-sections）再考虑切 L1 装配")
    return 0 if data["actionable_pct"] >= 90 else 1


if __name__ == "__main__":
    raise SystemExit(main())