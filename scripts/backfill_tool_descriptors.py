#!/usr/bin/env python3
"""工具描述符自动补齐 —— 让 data/descriptors.json 覆盖全部工具

【修复的是什么（评估报告 §4.5b / C5）】
    `data/descriptors.json` 是**能力描述符台账**，承载 risk_level / requires_approval /
    data_class / timeout_ms / retry_policy / idempotent / audit_level 这些**治理字段**。
    但实测只有 3 个内置工具（read_file / shell_execute / write_file）有描述符，
    其余 88 个工具**一条都没有** ⇒ 依赖描述符的治理判定形同虚设，
    而且 shell_execute 那条还写着 `risk_level: null` + `requires_approval: false`
    （最危险的工具在台账里是"无风险"）。

【本脚本做什么】
    从 `data/tool_definitions/*.yaml`（唯一权威）为每个工具生成/更新
    `cp.builtin.<name>` 描述符：
      plane/effect/risk/tags  →  trust.risk_level / requires_approval / data_class
                              →  runtime.timeout_ms / idempotent / retry_policy
                              →  governance.audit_level
                              →  capability.input_schema
      `cp.skill.*` 及其它非 cp.builtin 条目**一律不动**。

【不易】
    - 幂等：已存在的描述符只**补空**不覆盖（除非 --force）
    - 保留人工维护的字段（policy_ref / undo_hint / regression_baseline_id 等）
    - 原子写（.tmp + os.replace）
【变易】TIMEOUT_BY_RISK / AUDIT_BY_RISK 是数据，可调
【简易】
    python scripts/backfill_tool_descriptors.py            # 补齐
    python scripts/backfill_tool_descriptors.py --check    # 只报告覆盖率（CI 用）
"""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESCRIPTORS = os.path.join(_ROOT, "data", "descriptors.json")

#: risk → 默认超时（毫秒）。execute/extend 类给足时间，只读类收紧。
TIMEOUT_BY_RISK = {
    "low": 30_000,
    "medium": 60_000,
    "high": 120_000,
    "critical": 300_000,
}

#: risk → 审计级别
AUDIT_BY_RISK = {
    "low": "summary",
    "medium": "summary",
    "high": "full",
    "critical": "full",
}

#: risk → 是否要求人工审批（与 ToolMeta.needs_approval 同口径，另加 critical）
APPROVAL_RISKS = {"critical"}

#: tags → 数据分级（用于 data_class）
_DATA_CLASS_BY_TAG = {
    "web": "public",
    "channel": "external",
    "memory": "internal",
    "persona": "internal",
    "knowledge": "internal",
    "document": "internal",
}


def _now() -> str:
    return datetime.datetime.now().isoformat()


def _derive(tool: dict, meta_risk: str, meta_plane: str, meta_effect: str,
            tags: list, internal: bool) -> dict:
    """由 YAML 元数据派生描述符字段（只产出"派生得出"的部分）"""
    name = tool["name"]
    needs_approval = bool(meta_plane == "govern" or meta_effect == "extend"
                          or meta_risk in APPROVAL_RISKS)
    data_class = None
    for t in tags:
        if t in _DATA_CLASS_BY_TAG:
            data_class = _DATA_CLASS_BY_TAG[t]
            break
    if data_class is None:
        data_class = "internal"
    timeout_ms = TIMEOUT_BY_RISK.get(meta_risk, 60_000)
    idempotent = (meta_effect == "read")

    return {
        "capability": {
            "name": name,
            "description": tool.get("description", ""),
            "input_schema": tool.get("schema", {}) or {},
            "output_schema": {},
        },
        "trust": {
            "risk_level": meta_risk,
            "data_class": data_class,
            "requires_approval": needs_approval,
        },
        "runtime": {
            "timeout_ms": timeout_ms,
            "retry_policy": {
                "mode": "none" if not idempotent else "fixed",
                "max_retries": 0 if not idempotent else 2,
                "backoff_ms": 0 if not idempotent else 500,
                "backoff_factor": 1.0,
            },
            "idempotent": idempotent,
        },
        "governance": {
            "audit_level": AUDIT_BY_RISK.get(meta_risk, "summary"),
        },
        "evolution": {
            "stage": "shadow",
            "internalize_attempts": 0,
            "shadow_config": {},
        },
    }


def _merge_keep_existing(existing: dict, derived: dict, force: bool = False) -> dict:
    """把派生字段合并进既有描述符

    【不易】默认**只补空、不覆盖非空**——守住人工维护/上游写入的字段
            （policy_ref / undo_hint / quality / meta / origin / tenancy / trace_policy 等
             都不在 derived 里，本函数不碰它们）。
    【不易】force=True 表示"覆盖 **derived 覆盖范围内的** 非空字段"，
            **不是**"整条替换描述符"。早先的实现写成 `desc[cid] = derived`，
            会连带丢掉 meta / origin / tenancy / quality / governance.policy_ref 等必需段，
            使描述符不再符合 schema —— 这是本脚本自查发现的缺陷，已修。
    """
    out = copy.deepcopy(existing)
    for section, fields in derived.items():
        cur = out.get(section)
        if not isinstance(cur, dict):
            out[section] = copy.deepcopy(fields)
            continue
        for k, v in fields.items():
            if force or k not in cur or cur[k] in (None, "", {}, [], 0, False):
                cur[k] = copy.deepcopy(v)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="补齐工具能力描述符")
    ap.add_argument("--check", action="store_true", help="只报告覆盖率，不写文件")
    ap.add_argument("--force", action="store_true", help="覆盖既有非空字段（默认只补空）")
    args = ap.parse_args()

    sys.path.insert(0, _ROOT)
    from agent.lines import load_tool_meta  # 唯一权威读取入口

    meta = load_tool_meta()
    defs_dir = os.path.join(_ROOT, "data", "tool_definitions")
    tools: dict[str, dict] = {}
    import yaml
    for fn in sorted(os.listdir(defs_dir)):
        if fn.endswith(".yaml"):
            with open(os.path.join(defs_dir, fn), "r", encoding="utf-8") as f:
                doc = yaml.safe_load(f)
            if isinstance(doc, dict) and doc.get("name"):
                tools[doc["name"]] = doc

    if os.path.exists(_DESCRIPTORS):
        with open(_DESCRIPTORS, "r", encoding="utf-8") as f:
            doc = json.load(f)
    else:
        doc = {"schema_version": 1, "descriptors": {}, "aliases": {}, "variants": {}, "audit": []}
    desc = doc.setdefault("descriptors", {})

    have = {k for k in desc if k.startswith("cp.builtin.")}
    missing = [n for n in sorted(tools) if f"cp.builtin.{n}" not in desc]

    if args.check:
        print(f"[{'OK' if not missing else 'FAIL'}] 描述符覆盖 {len(have)}/{len(tools)} 个工具")
        if missing:
            print(f"   缺失 {len(missing)} 个: {missing[:20]}{' …' if len(missing) > 20 else ''}")
            print("   运行 python scripts/backfill_tool_descriptors.py 补齐")
            return 1
        return 0

    created = updated = 0
    for name, tool in tools.items():
        cid = f"cp.builtin.{name}"
        m = meta.get(name)
        risk = m.risk if m else "medium"
        plane = m.plane if m else "act"
        effect = m.effect if m else "execute"
        tags = list(m.tags) if m else []
        internal = bool(m.internal) if m else False
        derived = _derive(tool, risk, plane, effect, tags, internal)

        if cid in desc:
            before = json.dumps(desc[cid], ensure_ascii=False, sort_keys=True)
            desc[cid] = _merge_keep_existing(desc[cid], derived, force=args.force)
            if json.dumps(desc[cid], ensure_ascii=False, sort_keys=True) != before:
                updated += 1
            continue

        d = {
            "meta": {"id": cid, "version": "0.1.0",
                     "created_at": _now(), "updated_at": _now()},
            "origin": {"source_type": "builtin", "source_id": "builtin",
                       "provenance": "derived:tool_definitions",
                       "evidence": [], "external_endpoint": False, "manifest_ref": ""},
            "tenancy": {"tenant_id": "default", "scope": "project"},
        }
        d.update(derived)
        d["quality"] = {"success_rate": 0.0, "p99_latency_ms": 0.0,
                        "sample_count": 0, "regression_baseline_id": ""}
        d["governance"].setdefault("policy_ref", "")
        d["governance"].setdefault("undo_hint", "")
        d["evolution"]["trace_policy"] = (
            f"trace:builtin:call-side:ledger=unified_traces"
            f"@agent/data/tool_trace.db#capability_id={cid}"
            f"#read=UnifiedTraceStore.list_by_capability")
        desc[cid] = d
        created += 1

    doc.setdefault("audit", []).append({
        "ts": _now(), "action": "descriptor.backfill",
        "actor": "scripts/backfill_tool_descriptors.py",
        "detail": {"created": created, "updated": updated,
                   "total_builtin": len([k for k in desc if k.startswith("cp.builtin.")])},
    })

    tmp = _DESCRIPTORS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _DESCRIPTORS)

    total = len([k for k in desc if k.startswith("cp.builtin.")])
    print(f"[OK] 描述符补齐：新建 {created}，更新 {updated}，"
          f"cp.builtin 覆盖 {total}/{len(tools)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
