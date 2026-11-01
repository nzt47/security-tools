#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""技能转化器 · P1（只读计划）。

设计：docs/rfc/技能转化器_2026-11-01.md
口径：外来技能是**素材**——抽内涵 → 与自身能力逐面对比（只留增量）→ 审核 → 给重写建议；
**本脚本只读**：不写技能目录、不动清单、不建分支（跑完 `git status` 必须为空）。

用法：
    python scripts/skill_transform.py --plan <file|dir>        # 人读报告
    python scripts/skill_transform.py --plan <file> --json      # 机器可读
    python scripts/skill_transform.py --plan <file> --against pdf   # 只看与某条的对比

退出码：0 正常（含 block 结论，结论在报告里）/ 1 参数或读取错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.skills_mgmt.transform import build_plan  # noqa: E402


def _print_report(plan) -> None:
    print("== 转化计划（只读）==")
    print("artifact : %s" % plan["artifact"])
    print("sha256   : %s…  %d 字符" % (plan["sha256"][:16], plan["chars"]))
    meta = plan.get("meta") or {}
    print("meta     : id=%s name=%s" % (meta.get("id"), meta.get("name")))

    print("-- 内涵（抽不到就是空，不编造）--")
    for facet in ("trigger", "steps", "params", "guard"):
        items = plan["facets"].get(facet) or []
        print("  %-8s %d 条%s" % (facet, len(items), "：" + items[0][:60] if items else ""))
    ex = plan["facets"].get("example") or ""
    print("  %-8s %s" % ("example", (ex.splitlines()[0][:60] if ex else "（无）")))

    print("-- 与自身能力对比（前 5，按最高面分排序）--")
    for cand in plan["compare"]["candidates"]:
        s = cand["scores"]
        print("  %-28s %-11s trig=%.2f steps=%.2f params=%.2f ex=%.2f guard=%.2f"
              % (cand["skill_id"][:28], cand["verdict"], s["trigger"], s["steps"],
                 s["params"], s["example"], s["guard"]))
    print("  => 总体结论：%s" % plan["compare"]["verdict"])

    print("-- 合规/安全审核 --")
    review = plan["review"]
    print("  verdict：%s" % review["verdict"])
    for f in review["findings"]:
        loc = ("第 %d 行" % f["line"]) if f["line"] else "全文"
        print("  [%s] %-32s %s%s" % (f["severity"], f["rule"], loc,
                                     ("：" + f["note"]) if f.get("note") else ""))

    print("-- 卡片草案（L1）--")
    card = plan["card_draft"]
    print("  name   : %s" % card["name"])
    print("  summary: %s" % (card["summary"] or "（无）"))
    print("  trigger/steps/params/guard：%d/%d/%d/%d"
          % (len(card["trigger"]), len(card["steps"]), len(card["params"]), len(card["guard"])))

    print("-- 下一步 --")
    for a in plan["next"]:
        print("  · %s" % a)

    print("\n（P1 只读：没有写任何文件。应用要等 P2 的 --apply。）")


def _run_apply(args) -> int:
    "P2：落产物 → （可选）并入/新建技能 → （可选）commit/push/PR。"
    from agent.skills_mgmt.transform import apply_plan

    if args.pr and not (args.into or args.new):
        print("--pr 需要配 --into 或 --new（只有落进 skills_repo 的改动才该开 PR）")
        return 2
    if not args.execute:
        print("[干跑] 将落 transform 归档；加 --execute 才写盘")

    rep = apply_plan(args.apply, into=args.into, new=args.new, execute=args.execute)
    if not rep.get("ok"):
        print("[ERR] %s" % rep.get("error"))
        return 1
    print("transform_id : %s" % rep["transform_id"])
    print("transform_dir: %s" % rep["transform_dir"])
    print("判定          : %s / 审核 %s%s"
          % (rep["verdict"], rep["review_verdict"], "（block，转化不启动）" if rep["blocked"] else ""))
    if rep["written"]:
        for w in rep["written"]:
            print("  + %s" % w)
    else:
        print("计划写入: %s" % ", ".join(rep["planned_files"]))

    if rep["blocked"]:
        return 1

    if args.pr and args.execute:
        sid = args.into or args.new
        from scripts.intake_skill import intake_one, open_pr
        ir = intake_one(sid, execute=True, fix_meta=True, fix_baselines=True)
        if not ir.get("ok"):
            print("[ERR] 入轨失败: %s" % (ir.get("error") or ir))
            return 1
        pr = open_pr(sid)
        if not pr.get("ok"):
            print("[ERR] 建 PR 失败(%s): %s" % (pr.get("stage"), pr.get("error")))
            return 1
        print("已建 PR: %s" % (pr.get("pr_url") or pr.get("branch")))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="技能转化器：P1 只读计划 / P2 落地")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", metavar="PATH", help="只读计划：artifact（.md 或目录）")
    group.add_argument("--apply", metavar="PATH", help="落地：写 transforms/ + CARD.md（需 --execute）")
    parser.add_argument("--into", metavar="ID", default=None, help="并入既有技能（只写 CARD.md）")
    parser.add_argument("--new", metavar="ID", default=None, help="新建技能目录（写 skill.md + CARD.md）")
    parser.add_argument("--execute", action="store_true", help="真正写盘")
    parser.add_argument("--pr", action="store_true", help="落地后 commit → push → 建 PR")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--against", metavar="ID", default=None, help="只与指定技能对比")
    args = parser.parse_args(argv)

    if args.apply:
        return _run_apply(args)

    try:
        plan = build_plan(args.plan)
    except FileNotFoundError as e:
        print("[ERR] %s" % e)
        return 1

    if args.against:
        keep = [c for c in plan["compare"]["candidates"] if c["skill_id"] == args.against]
        plan["compare"]["candidates"] = keep
        plan["compare"]["best"] = keep[0] if keep else None
        plan["compare"]["verdict"] = keep[0]["verdict"] if keep else "distinct"

    if args.json:
        print(json.dumps({k: v for k, v in plan.items() if k != "facets"}, ensure_ascii=False, indent=1))
    else:
        _print_report(plan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
