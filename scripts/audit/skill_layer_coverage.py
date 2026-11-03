# -*- coding: utf-8 -*-
"""S4 前置：分层加载的**保留率基线**（只读，不改任何行为）。

【口径修正 2026-11-04】第一版量的是「L1 可动手率」（L1 里同时有"何时使用"+"步骤"），得出 41.3%，
看着像分层不可用 —— 其实是**口径错了**：46 条里 29 条技能本身就没有"步骤"节（概念/手册类），
它们不是"L1 丢了步骤"，而是"本来就没有步骤"。

正确的问题是：**L2 里已有的面，L1 保住了没有**。于是本脚本改量保留率：
  · 步骤保留率 = L2 有步骤节的技能中，L1 仍带步骤 的比例；
  · 触发保留率 = L2 有触发节的技能中，L1 仍带触发 的比例。
挡在装配前面的判据就是这两条（阈值 95%），而不是"所有技能都必须可动手"。

用法：python scripts/audit/skill_layer_coverage.py [--json reports/skill_layer_coverage.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.skills_mgmt import layered  # noqa: E402

#: L2 里"有这一面"的判定（与 capability_cards.FACETS 的关键词同源）
_STEPS_RE = re.compile(r"^#{2,3}\s*(步骤|流程|操作|做法|how to|step)", re.M | re.I)
_TRIGGER_RE = re.compile(
    r"^#{2,3}\s*(何时使用|使用场景|使用时机|适用场景|适用条件|触发条件|when to use)",
    re.M | re.I)

#: 保留率门槛：低于它就别把装配切到 L1
GATE_PCT = 95.0


def measure(repo: Path) -> dict:
    rows = []
    for child in sorted(repo.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        l1 = layered.load_layer(child.name, "L1", repo=repo)
        l2 = layered.load_layer(child.name, "L2", repo=repo)
        if not l1.get("ok") or not l2.get("ok"):
            continue
        l1_text = str(l1.get("text") or "")
        l2_text = str(l2.get("text") or "")
        has_steps = bool(_STEPS_RE.search(l2_text))
        has_trigger = bool(_TRIGGER_RE.search(l2_text))
        rows.append({
            "skill_id": child.name,
            "source": l1.get("source"),
            "l1_chars": l1.get("chars", 0),
            "l2_chars": l2.get("chars", 0),
            "saving_pct": (round((l2["chars"] - l1["chars"]) / l2["chars"] * 100, 1)
                           if l2.get("chars") else 0.0),
            "has_steps": has_steps,
            "steps_kept": ("步骤" in l1_text) if has_steps else None,
            "has_trigger": has_trigger,
            "trigger_kept": ("何时使用" in l1_text) if has_trigger else None,
        })
    total = len(rows)
    with_card = sum(1 for r in rows if r["source"] == "card")
    proc = [r for r in rows if r["has_steps"]]
    trig = [r for r in rows if r["has_trigger"]]
    steps_kept = sum(1 for r in proc if r["steps_kept"])
    trig_kept = sum(1 for r in trig if r["trigger_kept"])
    sav = [r["saving_pct"] for r in rows if r["l2_chars"]]
    return {
        "skills": total,
        "with_card_md": with_card,
        "derived": total - with_card,
        "procedural": len(proc),
        "steps_kept": steps_kept,
        "steps_kept_pct": round(steps_kept / len(proc) * 100, 1) if proc else 100.0,
        "triggered": len(trig),
        "trigger_kept": trig_kept,
        "trigger_kept_pct": round(trig_kept / len(trig) * 100, 1) if trig else 100.0,
        "avg_saving_pct": round(sum(sav) / len(sav), 1) if sav else 0.0,
        "gate_pct": GATE_PCT,
        "rows": rows,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="分层加载保留率基线（只读）")
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
    print("[分层基线] 有步骤 %d 条，L1 保住 %d（%.1f%%）；有触发面 %d 条，保住 %d（%.1f%%）"
          % (data["procedural"], data["steps_kept"], data["steps_kept_pct"],
             data["triggered"], data["trigger_kept"], data["trigger_kept_pct"]))
    print("[分层基线] L1 相对 L2 平均省 %.1f%%" % data["avg_saving_pct"])
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("[OK] 已写入 %s" % out)
    ok = data["steps_kept_pct"] >= GATE_PCT and data["trigger_kept_pct"] >= GATE_PCT
    if not ok:
        print("[WARN] 保留率 < %.0f%%：先补 L1 提取口径（capability_cards.FACETS）再切装配" % GATE_PCT)
    else:
        print("[OK] 保留率达标：可以把装配切到 L1（仍建议先灰度）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())