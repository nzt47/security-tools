#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「活体但无静态引用」路由台账校核 —— 把 52 条摊成可判定的账（2026-10-18 · D 项走②）。

【解决什么】route_ui_coverage 只回答「静态扫描没看到引用」，它**不回答"那到底是不是死代码"**。
2026-10-18 按族复查时实测到三类它看不见的消费者（都是真调用，不是注释）：
  · static/js/approval_console.js:193 —— base 常量 + 拼接（API + '/' + id + '/approve'）；
  · scripts/verify_key_revocation.ps1:278 —— 绝对 URL（http://localhost:5678/api/news?...）；
  · templates/log_dashboard.html:244 —— 模板串 fetch(/logs/dashboard/data?hours=...)。
所以「unreferenced」是**待判定**，不是结论。本文件把每条路由的判定与证据落成台账，
并做双向一致校核（与 route_ui_pending_backlog / ui_reachability_triage 同款理由）。

【判定口径】（四类，都要写证据——"我觉得"不算）
  · keep      仓库内可指认的消费者（写 file:line）；
  · keep-ops  无仓库内消费者，但有**文档化**的运维/外部契约（手册里给了怎么调）；
  · deprecate 已被新端点取代、处于兼容窗口（写清取代者与窗口依据）；
  · retire    无消费者、无文档契约（写清查过哪些根，下一轮再删）。

【为什么两个方向都查】只查"新 unreferenced 未登记"会让台账变墓地：端点删了/接好了却
忘了删登记，下次同名路径回来时会被**静默放行**。

用法：
    python scripts/audit/unreferenced_triage.py            # 打印判定分布 + 待办（retire/deprecate）
    python scripts/audit/unreferenced_triage.py --check    # 只校核（CI / 收尾），不一致即非零
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit.route_ui_coverage import classify  # noqa: E402

LEDGER = ROOT / "config" / "route_unreferenced_triage.json"
#: 判定取值与含义（与台账 note 一致；这里只做机器可校验的那部分）
VERDICTS = {
    "keep": "仓库内可指认的消费者",
    "keep-ops": "无仓库内消费者，但有文档化的运维/外部契约",
    "deprecate": "已被新端点取代、处于兼容窗口",
    "retire": "无消费者、无文档契约",
}
#: 这两类必须写 action（否则这本账只是形容词）
ACTION_REQUIRED = {"deprecate", "retire"}


def load_ledger() -> Dict[str, dict]:
    """读台账；文件缺失/损坏 ⇒ 空表（所有条目按未登记处理，方向是 fail-closed）。"""
    try:
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def unreferenced_norms() -> List[str]:
    """当前「活体 + 静态无引用」的归一化路径（与 reports/route_ui_coverage.json 同源）。"""
    return sorted(
        e["norm"] for e in classify()["entries"]
        if e.get("live") and e.get("category") == "unreferenced"
    )


def check(targets: Sequence[str], ledger: Dict[str, dict]) -> List[str]:
    """返回问题清单（空列表 = 一致）。四类问题：未登记 / 陈旧 / verdict 非法 / 证据缺失。"""
    problems: List[str] = []
    target_set = set(targets)
    untracked = sorted(p for p in target_set if p not in ledger)
    stale = sorted(p for p in ledger if p not in target_set)
    if untracked:
        problems.append("未登记的 unreferenced 路由（先在 config/route_unreferenced_triage.json 判定）：" + repr(untracked))
    if stale:
        problems.append("台账里的陈旧项（端点已删或已接线，请删除登记）：" + repr(stale))
    for path, entry in sorted(ledger.items()):
        if not isinstance(entry, dict):
            problems.append(path + "：条目必须是对象")
            continue
        verdict = entry.get("verdict")
        if verdict not in VERDICTS:
            problems.append(path + "：verdict 非法（" + repr(verdict) + "），只允许 " + ", ".join(sorted(VERDICTS)))
        if not str(entry.get("evidence") or "").strip():
            problems.append(path + "：必须写 evidence（消费者 file:line，或查过哪些根），不允许空")
        if verdict in ACTION_REQUIRED and not str(entry.get("action") or "").strip():
            problems.append(path + "：" + str(verdict) + " 必须写 action（删什么 / 何时删）")
    return problems


def summarize(ledger: Dict[str, dict]) -> Dict[str, int]:
    counts = {v: 0 for v in VERDICTS}
    for entry in ledger.values():
        verdict = (entry or {}).get("verdict") if isinstance(entry, dict) else None
        if verdict in counts:
            counts[verdict] += 1
    return counts


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="「活体但无静态引用」路由台账校核")
    ap.add_argument("--check", action="store_true", help="只校核，不一致即返回非零（CI / 收尾用）")
    args = ap.parse_args(argv)

    targets = unreferenced_norms()
    ledger = load_ledger()
    counts = summarize(ledger)
    problems = check(targets, ledger)

    if args.check:
        if problems:
            for p in problems:
                print("[ERR] " + p)
            return 1
        print("[OK] 台账与扫描结果一致：%d 条 unreferenced 全部有判定（%s）"
              % (len(targets), " / ".join("%s=%d" % (k, counts[k]) for k in ("keep", "keep-ops", "deprecate", "retire"))))
        return 0

    print("== 活体但无静态引用：%d 条 ==" % len(targets))
    for verdict, desc in sorted(VERDICTS.items()):
        print("  %-10s %2d  %s" % (verdict, counts.get(verdict, 0), desc))
    todo = [(p, e) for p, e in sorted(ledger.items()) if isinstance(e, dict) and e.get("verdict") in ACTION_REQUIRED]
    if todo:
        print("\n== 待办（retire / deprecate，%d 条）==" % len(todo))
        for path, entry in todo:
            print("  - %-38s [%s] %s" % (path, entry.get("verdict"), entry.get("action") or ""))
    if problems:
        print()
        for p in problems:
            print("[ERR] " + p)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
