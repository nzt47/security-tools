"""TASK-S8-05 端到端闭环演示 — 入队 → 裁定 → 重算（D1–D4 一次跑齐）

## 演示什么

把 4 个缺陷的修复串成**一条可复现的取证链**，全部在临时目录里跑（不污染运行时区），
每一步都打印机器可核对的数字：

| 段 | 演示内容 | 对应缺陷 |
|---|---|---|
| 1 | 单能力用例入队 → **多能力链被形状过滤挡下** → 孤儿 case 被拒 | D2 / D1 |
| 2 | 判定集**重生成**（旧 case 消失）→ 队列项转 `stale`（**记录保留不删**） | D1 |
| 3 | 归组键改进前后**同键冲突率**对比 + 归组声明缺口 | D3 |
| 4 | `--reconcile`：对**真实** 6 条资产裁定登账 → needs 清单**清空且依据可见** | D4 |
| 5 | 清空后**再跑一遍规则**：未裁定项仍照报（证明没放宽规则） | D4 反面纪律 |

## 用法

```powershell
# 全量（段 4/5 需要真实资产台账；缺省用仓库根 data/descriptors.json）
python scripts/demo_s8_05_forensics.py

# 只跑 1–3 段（纯自造数据，零外部依赖）
python scripts/demo_s8_05_forensics.py --skip-registry

# 指定资产台账/主轨（旁证另一份数据）
python scripts/demo_s8_05_forensics.py --registry-path data/descriptors.json `
    --main-path data/skills_mgmt.json
```
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CAP = "cp.builtin.read_file"
TRAILING = ("cp.builtin.shell_execute", "cp.builtin.write_file")


# ════════════════════════════════════════════════════════════
#  构造（自造数据，确定性；不读也不写运行时区）
# ════════════════════════════════════════════════════════════


def _single_step_case(index: int, capability_id: str = CAP) -> Any:
    """单能力用例（形状与被评能力匹配 ⇒ 可入队）"""
    from agent.digestion import cases as C
    return C.EquivalenceCase(
        case_id=f"demo-single-{index:03d}", capability_id=capability_id,
        input={"path": f"C:/sandbox/in/{index}.txt"},
        upstream=[C.ProgramStep(label="read_file", capability_id=capability_id,
                                params={"path": f"C:/sandbox/in/{index}.txt"})],
        expected_output_schema={"text": "str"},
        expected_status="success", sandbox_root="C:/sandbox")


def _chain_case(index: int) -> Any:
    """三步多能力链用例（形状不匹配 ⇒ 必须被挡在单能力取样之外）"""
    from agent.digestion import cases as C
    return C.EquivalenceCase(
        case_id=f"demo-chain-{index:03d}", capability_id=CAP,
        input={"path": f"C:/sandbox/in/{index}.txt"},
        upstream=[
            C.ProgramStep(label="read_file", capability_id=CAP,
                          params={"path": f"C:/sandbox/in/{index}.txt"}),
            C.ProgramStep(label="shell_execute", capability_id=TRAILING[0],
                          params={"cmd": "lint"}),
            C.ProgramStep(label="write_file", capability_id=TRAILING[1],
                          params={"path": f"C:/sandbox/out/{index}.txt"}),
        ],
        expected_status="success", sandbox_root="C:/sandbox")


def _orphan_case_id(index: int) -> str:
    """孤儿引用 id（**从不入判定集** ⇒ 入队必然被存活校验拒绝）"""
    return f"demo-orphan-{index:03d}"


# ════════════════════════════════════════════════════════════
#  打印工具
# ════════════════════════════════════════════════════════════


def banner(index: int, title: str, defect: str) -> None:
    print("")
    print("═" * 78)
    print(f" 段 {index}｜{title}    【{defect}】")
    print("═" * 78)


def kv(label: str, value: Any) -> None:
    print(f"  {label:<34s}{value}")


# ════════════════════════════════════════════════════════════
#  段 1 / 2：队列完整性（D1 + D2）
# ════════════════════════════════════════════════════════════


def run_queue(tmp: Path, *, now: float) -> Dict[str, Any]:
    from agent.digestion import cases as C
    from agent.digestion import shadow as SH

    banner(1, "抽检取样按形状过滤 + 入队存活校验", "D2 / D1")
    case_root = tmp / "cases"
    store = C.open_case_store(str(case_root))
    # 判定集 v1：2 条单能力（形状匹配）+ 2 条多能力链（形状不匹配）
    singles = [_single_step_case(i) for i in range(2)]
    chains = [_chain_case(i) for i in range(2)]
    v1 = C.build_case_set(CAP, singles + chains, version=1)
    store.save(v1, record_cost=False)

    kept, excluded = C.shape_applicable_cases(v1.active_cases(), CAP)
    report = C.shape_report(v1.active_cases(), CAP)
    kv("判定集版本", f"v{v1.version}（{v1.size} 条）")
    kv("形状分布", json.dumps(report["shapes"], ensure_ascii=False))
    kv("形状匹配（可取样）", f"{len(kept)} 条 → {[c.case_id for c in kept]}")
    kv("形状排除（不取样）", f"{len(excluded)} 条")
    for row in excluded:
        print(f"      拒因：{row['case_id']} — {row['reason']}")
    assert all(c.case_id in {x.case_id for x in singles} for c in kept), \
        "形状过滤失误：多能力链不得进入单能力取样"
    print("  ✓ 断言通过：对 cp.builtin.read_file 取样，采出用例**均为单能力形状**")

    queue = SH.ManualReviewQueue(str(tmp / "shadow" / "manual_reviews.jsonl"),
                                 case_store=store, clock=lambda: now)
    expect: Dict[str, Any] = {}
    # ① 正常入队（2 条单能力）
    queued = queue.enqueue(CAP, [c.case_id for c in singles],
                           reasons={singles[0].case_id: ["10% 确定性抽检"]},
                           case_set=v1, cases=singles, now=now, expect=expect)
    kv("入队成功", f"{len(queued)} 条 → {[i.case_id for i in queued]}")
    kv("入队版本（留痕）", f"v{queued[0].case_version_at_enqueue}")

    # ② 多能力链企图入队 → 形状不符，拒绝
    expect2: Dict[str, Any] = {}
    queue.enqueue(CAP, [chains[0].case_id], case_set=v1, cases=chains,
                  now=now, expect=expect2)
    kv("多能力链入队", f"拒绝 → {expect2['rejected']}")

    # ③ 孤儿用例入队 → case 不存在，拒绝
    expect3: Dict[str, Any] = {}
    queue.enqueue(CAP, ["case_does_not_exist"], case_set=v1, now=now,
                  expect=expect3)
    kv("孤儿 case 入队", f"拒绝 → {expect3['rejected']}")

    print("")
    banner(2, "判定集重生成 → 受影响队列项转 stale（记录保留不删）", "D1")
    # 重生成 v2：**不含**旧用例（模拟 S7-05 用真实轨迹重建判定集）
    regenerated = [_single_step_case(100 + i) for i in range(2)]
    v2 = C.build_case_set(CAP, regenerated, version=2)
    store.save(v2, record_cost=False)
    before = SH.queue_liveness_report(queue, CAP, case_set=v2)
    scan = queue.scan_after_regeneration(CAP, case_set=v2, now=now + 60)
    after = SH.queue_liveness_report(queue, CAP, case_set=v2)
    kv("重生成后判定集版本", f"v{v2.version}（{v2.size} 条，旧用例已不存在）")
    kv("扫描前 stale 数", before["stale"])
    kv("联动扫描标记", f"{scan['marked']} 条（already={scan['already']}）")
    for item in scan["items"]:
        print(f"      {item['case_id']} ← {item['reason']}"
              f"（入队时 v{item['case_version_at_enqueue']}）")
    kv("扫描后 stale 数", after["stale"])
    kv("台账样本总数（**未删除**）", f"{after['sampled']} 条")
    kv("待裁定", after["pending"])
    kv("队列是否闭合", after["closed"])
    assert after["stale"] == 2, "重生成联动未生效"
    assert after["sampled"] == 2, "失效项被删除了（违反『留痕优先』）"
    print("  ✓ 断言通过：旧引用**标为 stale 且保留在台账**，不再占住待裁定")

    sheet = queue.review_sheet(CAP)
    stale_lines = [ln for ln in sheet.splitlines() if "已失效" in ln]
    print(f"  ✓ 复核表显式披露失效：{len(stale_lines)} 行（不再静默消失）")

    return {"singles": [c.case_id for c in singles],
            "chains": [c.case_id for c in chains],
            "stale": after["stale"], "sampled": after["sampled"],
            "summary": queue.summary(CAP), "scan": scan}


# ════════════════════════════════════════════════════════════
#  段 3：归组键（D3）
# ════════════════════════════════════════════════════════════


def topic_for(capabilities: str, step_count: int) -> str:
    """任务主题（**刻意与形状无关**，用于演示"同文本、不同形状"的误合并）"""
    return "+".join(sorted({*capabilities.split("+"), f"steps{step_count}"}))


def run_grouping(case_file: Path) -> Dict[str, Any]:
    from agent.digestion import cases as C
    from agent.digestion import cleaning as cl

    banner(3, "归组键加入结构维度（能力集合 + 步数档位）", "D3")

    records: List[Dict[str, Any]] = []
    source = "（无真实判定集，使用自造语料）"
    if case_file.exists():
        payload = json.loads(case_file.read_text(encoding="utf-8"))
        version = payload["versions"][-1]
        for case in version["cases"]:
            steps = case.get("upstream") or []
            caps = sorted({(s.get("capability_id") or s.get("label") or "")
                           for s in steps})
            text = cl.text_key_of(case.get("intent_key") or "")
            if not text:
                continue
            records.append({
                "v1": text,
                "v2": cl.structural_intent_key(text, capability_set="+".join(caps),
                                               step_count=len(steps)),
                # `declared` = 该用例**声明**的被评能力（判定集里的 capability_id）：
                # 多能力链声明成单能力正是 D3 在真实语料上的可测量后果
                "declared": case.get("capability_id") or "",
                "capability_set": "+".join(caps),
                "step_count_bucket": cl.step_count_bucket(len(steps)),
            })
        source = (f"{case_file.name} v{version['version']}"
                  f"（真实判定集，{len(records)} 条）")

    # 跨形状对照：同一条任务文本、两种执行形状（只读 → 读+执行+写）
    read_only = "审查模块并生成报告"
    same_text = cl.normalize_intent(read_only)
    for caps, steps in ((CAP, 1), ("+".join((CAP, *TRAILING)), 3)):
        records.append({
            "v1": same_text,
            "v2": cl.structural_intent_key(cl.normalize_intent(read_only),
                                           capability_set=caps, step_count=steps),
            "declared": CAP,
            "capability_set": caps,
            "step_count_bucket": cl.step_count_bucket(steps),
        })

    v1 = cl.grouping_conflict_rate(records, key_fn=lambda r: r["v1"])
    v2 = cl.grouping_conflict_rate(records, key_fn=lambda r: r["v2"])
    kv("语料", source)
    kv("记录数", v1["total"])
    print("")
    print("  改进前 / 改进后（同键冲突率 = 同键内形状不一致的记录占比）")
    print(f"    v1 纯文本键     ：键 {v1['keys']:3d} 个｜冲突 {v1['conflict_items']:3d} 条"
          f"｜冲突率 {v1['ratio']:.4f}")
    print(f"    v2 结构+文本键  ：键 {v2['keys']:3d} 个｜冲突 {v2['conflict_items']:3d} 条"
          f"｜冲突率 {v2['ratio']:.4f}")
    for group in v1["conflicts"]:
        print(f"      v1 冲突组 key={group['key'][:44]!r} size={group['size']} "
              f"shapes={group['shapes']}")

    gap = cl.capability_declaration_gap(records,
                                        normalize=C.normalize_capability_id)
    print("")
    kv("归组声明缺口（多能力链挂在单能力名下）",
       f"{gap['gap_items']}/{gap['total']} = {gap['ratio']:.4f}")
    for sample in gap["samples"][:2]:
        print(f"      声明 {sample['declared']!r} 而实际能力集合 {sample['capabilities']}")

    # S7-05 的 7 条：证实多能力链不与"单能力 read_file"任务同键
    chain_caps = "+".join(sorted({CAP, *TRAILING}))
    single_key = cl.structural_intent_key(same_text, capability_set=CAP,
                                          step_count=1)
    chain_keys = {r["v2"] for r in records if r["capability_set"] == chain_caps}
    chain_count = sum(1 for r in records if r["capability_set"] == chain_caps)
    single_count = sum(1 for r in records if r["capability_set"] == CAP)
    print("")
    kv("单能力记录 / 多能力链记录", f"{single_count} 条 / {chain_count} 条")
    kv("两类结构键是否相交", bool(chain_keys & {single_key}))
    assert not (chain_keys & {single_key}), "结构维度未隔离跨形状同文本"
    print("  ✓ 断言通过：同文本、不同形状的任务**不再同键**（D3 的核心区分度）")
    assert v2["conflict_items"] <= v1["conflict_items"], "结构维度未改善冲突"
    return {"v1": v1, "v2": v2, "gap": gap}


# ════════════════════════════════════════════════════════════
#  段 4 / 5：裁定留痕（D4）
# ════════════════════════════════════════════════════════════


def run_resolutions(tmp: Path, *, registry_path: Path, main_path: Path,
                    ledger: Path) -> Dict[str, Any]:
    from agent.descriptors.backfill import needs_markdown, plan_backfill
    from agent.digestion import resolutions as R

    banner(4, "人工裁定登账 → needs 清单清空且依据可见", "D4")
    if not registry_path.exists():
        print(f"  资产台账不存在：{registry_path}")
        print("  段 4/5 需要真实的 descriptor 台账（运行时区，worktree 内通常为空）。")
        print("  请在主工作区执行，或显式传路径：")
        print("    python scripts/demo_s8_05_forensics.py "
              r"--registry-path C:\Users\Administrator\agent\data\descriptors.json "
              r"--main-path C:\Users\Administrator\agent\data\skills_mgmt.json")
        return {"skipped": True, "reason": "registry_absent"}
    if not ledger.exists():
        print(f"  裁定台账不存在：{ledger}")
        print("  请先执行：python scripts/record_s8_05_resolutions.py --record")
        return {"skipped": True, "reason": "ledger_absent"}

    store = R.ResolutionStore(path=str(ledger))
    summary = store.summary()
    kv("裁定台账", f"{store.path}")
    kv("生效裁定", f"{summary['active']} 条｜按规则 {summary['by_rule']}")

    kwargs: Dict[str, Any] = {"resolutions": store}
    if main_path.exists():
        kwargs["main_path"] = main_path
    planned = plan_backfill(**kwargs)
    needs = planned["needs"]
    kv("待人工复核（needs_review）", f"{len(needs['needs_review'])} 条")
    kv("已裁定（不再提醒）", f"{len(needs['needs_review_resolved'])} 条")
    for row in needs["needs_review_resolved"]:
        print(f"      · {row['asset_id']:32s} {row['scope']}/{row['rule']}")
        print(f"        依据：{row.get('basis', '')[:100]}")
    print("")
    print("  needs 报告节选（依据始终可见）：")
    for line in needs_markdown(needs).splitlines():
        if line.strip():
            print(f"    {line}")
    assert not needs["needs_review"], "登记后仍有待复核项（D4 未达成）"
    print("  ✓ 断言通过：`needs_review` = 0，且 6 条依据逐条可见")

    banner(5, "反面纪律：不放宽规则 —— 未裁定项照报", "D4 反面")
    empty = R.ResolutionStore(path=str(tmp / "empty_resolutions.jsonl"))
    planned_empty = plan_backfill(resolutions=empty, **({"main_path": main_path}
                                                        if main_path.exists() else {}))
    kv("空台账 needs_review",
       f"{len(planned_empty['needs']['needs_review'])} 条（应为登记前的原始条数）")
    partial = R.ResolutionStore(path=str(tmp / "partial_resolutions.jsonl"))
    for rec in R.ResolutionStore(path=str(ledger)).records():
        if rec.rule == "DC-2":
            partial.record(rec, audit=False)
    planned_partial = plan_backfill(resolutions=partial,
                                    **({"main_path": main_path}
                                       if main_path.exists() else {}))
    kv("部分台账（仅 2 条 data_class）needs_review",
       f"{len(planned_partial['needs']['needs_review'])} 条"
       f"（provenance 4 条应仍报）")
    still = [r["rule"] for r in planned_partial["needs"]["needs_review"]]
    kv("仍被报告的规则", f"{sorted(set(still))}")
    assert "PRV-5" in still, "已裁定项过滤把未裁定规则也吞掉了（放宽了规则）"
    print("  ✓ 断言通过：只跳过**已裁定**项，未裁定项逐条照报")
    return {"needs": needs, "planned_empty": planned_empty,
            "planned_partial": planned_partial}


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════


def main() -> int:
    ap = argparse.ArgumentParser(description="TASK-S8-05 端到端闭环演示")
    ap.add_argument("--case-file", default=str(
        Path("data/digestion/cases/cp.builtin.read_file.json")),
        help="真实判定集文件（段 3 的语料；缺省不存在则用自造语料）")
    ap.add_argument("--registry-path", default=str(Path("data/descriptors.json")))
    ap.add_argument("--main-path", default=str(Path("data/skills_mgmt.json")))
    ap.add_argument("--resolutions", default=str(
        Path("data/descriptors/resolutions.jsonl")))
    ap.add_argument("--skip-registry", action="store_true",
                    help="跳过段 4/5（纯自造数据即可，零外部依赖）")
    ap.add_argument("--keep-tmp", action="store_true")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="s8_05_demo_"))
    os.environ.setdefault("CP_EVENTS_DIR", str(tmp / "events"))
    try:
        print("TASK-S8-05 抽检与复核取证链修复 — 端到端闭环演示")
        print(f"临时工作目录（全部产物落此，**不触碰运行时区**）：{tmp}")
        out: Dict[str, Any] = {}
        out["queue"] = run_queue(tmp, now=1_800_000_000.0)
        out["grouping"] = run_grouping(Path(args.case_file))
        if not args.skip_registry:
            out["resolutions"] = run_resolutions(
                tmp, registry_path=Path(args.registry_path),
                main_path=Path(args.main_path), ledger=Path(args.resolutions))
        print("")
        print("═" * 78)
        print(" 闭环结论：入队（形状过滤 + 存活校验）→ 重生成（stale 留痕）"
              "→ 裁定（needs 清空且依据可见）")
        print("═" * 78)
        d4 = out.get("resolutions") or {}
        needs = d4.get("needs") if isinstance(d4, dict) else None
        print(json.dumps({
            "D1_stale_marked": out["queue"]["stale"],
            "D1_records_kept": out["queue"]["sampled"],
            "D2_chain_excluded": out["grouping"]["gap"]["multi_capability_items"],
            "D3_conflict_before": out["grouping"]["v1"]["ratio"],
            "D3_conflict_after": out["grouping"]["v2"]["ratio"],
            "D3_declaration_gap": out["grouping"]["gap"]["ratio"],
            "D4_attempted": bool(d4) and not d4.get("skipped"),
            "D4_needs_review": (len(needs["needs_review"]) if needs else None),
            "D4_needs_resolved": (len(needs["needs_review_resolved"])
                                  if needs else None),
        }, ensure_ascii=False, indent=1))
        return 0
    finally:
        if args.keep_tmp:
            print(f"\n（--keep-tmp：临时目录保留于 {tmp}）")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
