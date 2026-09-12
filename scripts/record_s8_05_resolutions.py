"""TASK-S8-05 步骤 4 落地 — 登记 2026-09-12 人工复核的 6 条裁定（D4）

## 这个脚本做什么

把 [`人工复核裁定记录_20260912.md`](docs/zh/人工复核裁定记录_20260912.md) §三 的
**人工裁定**登记进 `ResolutionStore` 裁定留痕台账，使 needs 重算能读到"已裁定"这个
一等输入 —— 从而 6 条 NEEDS_REVIEW **不再重复提醒**（D4 的验收口径）。

## 这个脚本**不做**什么（同样重要）

1. **不改写任何裁定结论**：脚本只**记录** Owner 已下的裁定，绝不写
   `registry.update_trust` / `mark_provenance`（那不是本任务的事，且已由 Owner 完成）；
2. **不放宽任何规则**：登记的是"这条已有人裁过"，不是"这条不算问题"——
   未裁定的项在 needs 报告里**逐条照报**；
3. **不静默**：每条记录的 ``reason`` / ``evidence`` 逐条写清，最终报告与
   `--verify` 都把它们打印出来。

## 裁定清单（逐条对应裁定记录 §三）

| 资产 | 域 | 规则 | 裁定 | 依据 |
|---|---|---|---|---|
| `code-observability` | provenance | PRV-5 | 保持 `unknown` | §3.1：来源 `external_agent`，正文无 license/来源声明；**无证据可补**，升 `verified` 即是造假 |
| `frontend-state-sync` | provenance | PRV-5 | 保持 `unknown` | 同上 |
| `self-explanatory-ui` | provenance | PRV-5 | 保持 `unknown` | 同上 |
| `testing-anti-patterns` | provenance | PRV-5 | 保持 `unknown` | 同上 |
| `engineering-test-delivery` | data_class | DC-2 | 写入 `internal` | §3.2：通用测试与交付流程规范（871B），不含敏感数据；原 `confidential` 系"输出审计报告/过程日志"措辞自动推断 ⇒ **误伤** |
| `global-core-principles` | data_class | DC-2 | 写入 `internal` | §3.2：通用行为准则（854B），规则而非数据；原判系"隐私边界"字样被推断 ⇒ **误伤** |

> RK-5（`global-core-principles` 风险 HIGH→MEDIUM）**不在本清单**：它因**正文修订**
> 而触发条件消失，规则重算**已能自动识别**（D4 的关键对比："改内容"能被规则识别，
> "改 trust 值"不能）。把它也登记成"已裁定"反而会把可自动识别的事伪装成人工豁免。

## 用法

```powershell
# 登记（幂等：同一三元组以最后一条为准，重复执行不会重复提醒）
python scripts/record_s8_05_resolutions.py --record

# 只读核对（不写盘）：打印台账与 6 条裁定的落库状态
python scripts/record_s8_05_resolutions.py --verify
```
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.digestion.resolutions import (  # noqa: E402
    SCOPE_DESCRIPTOR,
    VERDICT_ACCEPTED,
    ResolutionRecord,
    ResolutionStore,
)

#: 裁定记录（唯一来源：`docs/zh/人工复核裁定记录_20260912.md` §三；**逐字不改写**）
DECISIONS: List[Dict[str, Any]] = [
    {
        "asset_id": "code-observability", "rule": "PRV-5",
        "written_value": "unknown",
        "reason": ("4 条 provenance 保持 unknown（裁定记录 §3.1）：来源 external_agent、"
                   "正文无 license/来源声明，没有证据可补，升 verified 即是造假；"
                   "而『仅人工单步』对规范型技能（非可执行 capability）实际代价很小"),
        "evidence": ["docs/zh/人工复核裁定记录_20260912.md §3.1（provenance 4 条）"],
    },
    {
        "asset_id": "frontend-state-sync", "rule": "PRV-5",
        "written_value": "unknown",
        "reason": ("4 条 provenance 保持 unknown（裁定记录 §3.1）：来源 external_agent、"
                   "正文无 license/来源声明，没有证据可补，升 verified 即是造假；"
                   "而『仅人工单步』对规范型技能（非可执行 capability）实际代价很小"),
        "evidence": ["docs/zh/人工复核裁定记录_20260912.md §3.1（provenance 4 条）"],
    },
    {
        "asset_id": "self-explanatory-ui", "rule": "PRV-5",
        "written_value": "unknown",
        "reason": ("4 条 provenance 保持 unknown（裁定记录 §3.1）：来源 external_agent、"
                   "正文无 license/来源声明，没有证据可补，升 verified 即是造假；"
                   "而『仅人工单步』对规范型技能（非可执行 capability）实际代价很小"),
        "evidence": ["docs/zh/人工复核裁定记录_20260912.md §3.1（provenance 4 条）"],
    },
    {
        "asset_id": "testing-anti-patterns", "rule": "PRV-5",
        "written_value": "unknown",
        "reason": ("4 条 provenance 保持 unknown（裁定记录 §3.1）：来源 external_agent、"
                   "正文无 license/来源声明，没有证据可补，升 verified 即是造假；"
                   "而『仅人工单步』对规范型技能（非可执行 capability）实际代价很小"),
        "evidence": ["docs/zh/人工复核裁定记录_20260912.md §3.1（provenance 4 条）"],
    },
    {
        "asset_id": "engineering-test-delivery", "rule": "DC-2",
        "written_value": "internal",
        "reason": ("data_class 降为 internal（裁定记录 §3.2）：内容为通用测试与交付流程规范"
                   "（871B），不含敏感数据；原 confidential 系『输出审计报告/过程日志』措辞"
                   "自动推断 ⇒ 误伤（标 confidential 会触发隐私闸门一票否决，使最基础的"
                   "技能永远无法进入内化/自动化）。已由 Owner 经 "
                   "registry.update_trust(actor=\"Owner\") 写入"),
        "evidence": ["docs/zh/人工复核裁定记录_20260912.md §3.2（data_class 2 条）",
                     "registry.update_trust(actor=\"Owner\") 审计留痕"],
    },
    {
        "asset_id": "global-core-principles", "rule": "DC-2",
        "written_value": "internal",
        "reason": ("data_class 降为 internal（裁定记录 §3.2）：内容为通用行为准则（854B），"
                   "规则而非数据；原文『隐私边界』字样被自动推断为敏感 ⇒ 误伤。"
                   "已由 Owner 经 registry.update_trust(actor=\"Owner\") 写入"),
        "evidence": ["docs/zh/人工复核裁定记录_20260912.md §3.2（data_class 2 条）",
                     "registry.update_trust(actor=\"Owner\") 审计留痕"],
    },
]

#: 裁定人（裁定记录 §一：裁定人 = Owner）
DECIDED_BY = "Owner"
DEFAULT_PATH = str(Path("data/descriptors/resolutions.jsonl"))
DEFAULT_REGISTRY_PATH = str(Path("data/descriptors.json"))


def build_records(*, decided_at: float = 0.0,
                  source: str = "s8-05/裁定记录_20260912") -> List[ResolutionRecord]:
    """裁定清单 → `ResolutionRecord` 序列（确定性、可复算）"""
    return [ResolutionRecord(
        scope=SCOPE_DESCRIPTOR, rule=row["rule"], verdict=VERDICT_ACCEPTED,
        asset_id=row["asset_id"], capability_id=f"cp.skill.{row['asset_id']}",
        written_value=row["written_value"], reason=row["reason"],
        evidence=list(row["evidence"]), decided_by=DECIDED_BY,
        decided_at=float(decided_at or 0.0), source=source,
    ) for row in DECISIONS]


def _current_state(registry_path: str) -> Dict[str, Dict[str, Any]]:
    """读取 6 个资产的**当前** terminal 值（只读核对，绝不写入）"""
    try:
        from agent.descriptors.registry import DescriptorRegistry
    except Exception as exc:  # noqa: BLE001
        return {"_error": {"message": str(exc)}}
    reg = DescriptorRegistry(path=registry_path)
    out: Dict[str, Dict[str, Any]] = {}
    for row in DECISIONS:
        desc = reg.get(f"cp.skill.{row['asset_id']}")
        if desc is None:
            out[row["asset_id"]] = {"present": False}
            continue
        out[row["asset_id"]] = {
            "present": True,
            "provenance": (desc.origin.provenance.value
                           if desc.origin.provenance else None),
            "data_class": (desc.trust.data_class.value
                           if desc.trust.data_class else None),
            "risk_level": (desc.trust.risk_level.value
                           if desc.trust.risk_level else None),
        }
    return out


def verify(store: ResolutionStore, *, registry_path: str,
           decided_at: float = 0.0) -> int:
    """核对：6 条裁定是否已登记、登记后 needs 是否清空、依据是否可见"""
    expected = build_records(decided_at=decided_at)
    missing = [rec for rec in expected
               if store.lookup(SCOPE_DESCRIPTOR, rule=rec.rule,
                               asset_id=rec.asset_id) is None]
    print(f"台账路径: {store.path}")
    print(f"台账总览: {json.dumps(store.summary(), ensure_ascii=False)}")
    print(f"应登记 {len(expected)} 条｜未登记 {len(missing)} 条")
    state = _current_state(registry_path)
    for rec in expected:
        live = store.lookup(SCOPE_DESCRIPTOR, rule=rec.rule, asset_id=rec.asset_id)
        mark = "✓" if live is not None else "✗"
        cur = state.get(rec.asset_id, {})
        field = "provenance" if rec.rule.startswith("PRV") else "data_class"
        same = cur.get(field) == rec.written_value
        print(f"  {mark} {rec.asset_id:30s} {rec.rule:6s} "
              f"裁定={rec.written_value!r} 现值={cur.get(field)!r} "
              f"{'一致' if same else '⚠️ 与裁定不一致'}")
        if live is not None:
            print(f"      依据：{live.basis()}")
    if missing:
        print("\n未全部登记 ⇒ 请先执行 --record")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="S8-05 人工裁定登记（6 条）")
    ap.add_argument("--record", action="store_true", help="写入裁定台账")
    ap.add_argument("--verify", action="store_true", help="只读核对（默认行为）")
    ap.add_argument("--path", default=DEFAULT_PATH,
                    help=f"裁定台账路径（默认 {DEFAULT_PATH}）")
    ap.add_argument("--registry-path", default=DEFAULT_REGISTRY_PATH,
                    help=f"descriptor 台账路径（默认 {DEFAULT_REGISTRY_PATH}）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    store = ResolutionStore(path=args.path)
    if not args.record:
        return verify(store, registry_path=args.registry_path)

    records = build_records()
    store.record_many(records)
    if args.json:
        print(json.dumps({"recorded": [r.to_dict() for r in records],
                          "summary": store.summary()},
                         ensure_ascii=False, indent=1))
    else:
        print(f"已登记 {len(records)} 条人工裁定 → {store.path}")
        for rec in records:
            print(f"  · {rec.asset_id:30s} {rec.rule:6s} → {rec.written_value!r}")
        print(f"台账总览: {json.dumps(store.summary(), ensure_ascii=False)}")
        print("审计动作: resolution.record（可追溯、可验签）")
    return verify(store, registry_path=args.registry_path)


if __name__ == "__main__":
    raise SystemExit(main())
