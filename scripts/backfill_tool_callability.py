#!/usr/bin/env python3
"""工具可调用性回填脚本 —— 为 data/tool_definitions/*.yaml 补齐"可被 LLM 调用"声明

【为什么需要】
    一个工具"能不能被模型自己发起调用"此前是四处分散事实**隐式**决定的（`internal`
    标记、注册表里有没有 handler、有没有 JSON Schema、权限策略是否拒绝），既看不出来、
    也无法自动解析。本脚本把这组策略字段收敛进工具定义本身（与 plane/effect/risk 同一处），
    使 `data/tool_definitions/*.yaml` 依旧是唯一权威，清单则由
    `scripts/sync_capability_manifest.py` 派生。

【补哪些字段】（取值域见 `agent/lines/callability.py`）
    tool_type         tool | skill | api | script      —— 工具恒为 tool
    llm_callable      是否允许 LLM 发起调用（声明；生效值还要过 schema/执行器/权限三关）
    callable_mode     auto | required | manual         —— manual = 仅人工/系统调用
    permission_level  public | internal | restricted   —— **由 plane/effect/risk 派生**
    sandbox_allowed   是否允许在沙箱（受限会话，默认只读）中执行
    reason            不可调用原因（仅 llm_callable=false 时写入，必填）

【不易】
    - 幂等：已存在的字段**不覆盖**（只补缺失），可反复运行；
    - 只做文本插入，不用 yaml.dump 回写 —— 保住 description 的折行与字段顺序；
    - 插入位置固定：`risk:`（治理三元组的末行）之后，故声明与治理字段相邻可读；
    - `--check` 除了查缺失，还**对拍** `permission_level` 与 plane/effect/risk 的派生值：
      两者不一致 = 同一件事有了两份口径，必须当场报错而不是静默共存。
【变易】
    TOOL_TYPE / MODE / SANDBOX 例外表是数据，可随工具增减调整。
【简易】
    python scripts/backfill_tool_callability.py            # 回填
    python scripts/backfill_tool_callability.py --check    # 只检查不写（CI 用）
"""
from __future__ import annotations

import argparse
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent.lines.callability import (  # noqa: E402
    CALLABLE_MODES,
    PERMISSION_LEVELS,
    TOOL_TYPES,
    effective_permission_level,
)
from agent.lines.models import needs_approval_for  # noqa: E402

_DEFS_DIR = os.path.join(_ROOT, "data", "tool_definitions")

#: 字段名（写进 YAML 的顺序）
_DECL_FIELDS = ("tool_type", "llm_callable", "callable_mode",
                "permission_level", "sandbox_allowed")

#: 仅人工/系统调用的工具（模型不发起）：内部执行体，保留注册供后台链路按名调用
MANUAL_TOOLS: dict[str, str] = {
    "process_distill_run": "内部执行体：仅由 AsyncDistill 后台链路按名调用（保留注册、不进模型可见集）",
}

#: `sandbox_allowed=false` 的例外（只读但会出网/看屏幕/读隐私 ⇒ 沙箱里不放行）。
#: 默认规则：`effect == read` 且不出网不看屏 ⇒ 允许在只读沙箱里跑。
_SANDBOX_DENY_READ = frozenset({
    "get_weather",         # 出网
    "ext_discover",        # 出网（扩展市场发现）
    "look_at_screen",      # 截屏，含隐私内容
    "browser_screenshot",  # 浏览器截屏（其宿主 browser_navigate 已非只读）
})
#: 整类不放进沙箱的分类（只读也一样）：web 取数天然依赖出网
_SANDBOX_DENY_CATEGORIES = frozenset({"web"})

_ANCHOR_RE = re.compile(r"^(risk:\s*\S+)\s*$", re.MULTILINE)
_FALLBACK_RE = re.compile(r"^(tags:\s*.+|effect:\s*\S+|plane:\s*\S+|version:\s*\S+)\s*$",
                          re.MULTILINE)


def _has_field(text: str, field: str) -> bool:
    return re.search(rf"^{field}:\s*\S?", text, re.MULTILINE) is not None


def _fields_from_text(text: str) -> dict[str, str]:
    """从 YAML 文本里取已声明的可调用性字段（只看顶层行，够用且不引 yaml 回写）"""
    out: dict[str, str] = {}
    for field in _DECL_FIELDS + ("reason",):
        m = re.search(rf"^{field}:\s*(.*)$", text, re.MULTILINE)
        if m:
            out[field] = m.group(1).strip().strip("'\"")
    return out


def derive(name: str, plane: str, effect: str, risk: str, category: str,
           tags: list[str], internal: bool) -> dict[str, str]:
    """按治理轴派生声明值（单一来源；`--check` 用它与本文件对拍）"""
    manual = name in MANUAL_TOOLS
    # 【TASK-06 / D1 修复（2026-09-20）】这里原先**手写**了一份审批规则副本：
    #     needs_approval = plane == "govern" or effect == "extend" or risk == "critical"
    #   它正是 `agent/lines/models.py::needs_approval_for` docstring 警告的"三份手写副本"
    #   中**漏掉的那一份**（另外两份：`ToolMeta.needs_approval` 与
    #   `callability._tool_entry` 已改为共用函数）。TASK-06 把阈值从 critical 提到
    #   `{critical, high}` 后，本副本仍按旧口径 ⇒ `--check` 报"10 个 YAML 声明与治理轴
    #   派生值不一致"（声明 restricted、派生 internal），而那 10 条恰是本次要收紧的
    #   `risk: high` 工具。**这不是数据错了，是判据的第二真相源过期了** ——
    #   若不修，唯一能"修好"它的动作是把 YAML 从 restricted 改回 internal，
    #   即用一份过期副本把安全收紧回滚掉。故必须共用同一实现。
    needs_approval = needs_approval_for(plane, effect, risk)
    sandbox_ok = (
        effect == "read"
        and category not in _SANDBOX_DENY_CATEGORIES
        and "vision" not in tags
        and name not in _SANDBOX_DENY_READ
    )
    out = {
        "tool_type": "tool",
        "llm_callable": "false" if (manual or internal) else "true",
        "callable_mode": "manual" if manual else "auto",
        "permission_level": effective_permission_level(effect, risk, needs_approval, False),
        "sandbox_allowed": "true" if sandbox_ok else "false",
    }
    if manual or internal:
        out["reason"] = MANUAL_TOOLS.get(
            name, "内部工具（internal: true）：保留注册供后台链路按名调用，不进模型可见集")
    return out


def _render_block(values: dict[str, str]) -> str:
    lines = [f"{f}: {values[f]}" for f in _DECL_FIELDS]
    if values.get("reason"):
        lines.append(f"reason: {values['reason']}")
    return "\n".join(lines)


def _insert_after_anchor(text: str, block: str) -> str:
    """插到 `risk:` 行之后（无则退到 tags/effect/plane/version 行之后）"""
    m = _ANCHOR_RE.search(text) or _FALLBACK_RE.search(text)
    if not m:
        raise ValueError("找不到 risk:/version: 等锚点")
    end = m.end()
    return text[:end] + "\n" + block + text[end:]


def _read_governance(text: str) -> tuple[str, str, str, str, list[str], bool]:
    """取 plane/effect/risk/category/tags/internal（缺失按保守默认）"""
    def _one(field: str, default: str) -> str:
        m = re.search(rf"^{field}:\s*(\S+)\s*$", text, re.MULTILINE)
        return m.group(1) if m else default

    m = re.search(r"^tags:\s*\[(.*?)\]", text, re.MULTILINE)
    tags = [t.strip() for t in m.group(1).split(",")] if m and m.group(1).strip() else []
    internal = re.search(r"^internal:\s*true\s*$", text, re.MULTILINE) is not None
    return (_one("plane", "act"), _one("effect", "execute"), _one("risk", "medium"),
            _one("category", ""), tags, internal)


def main() -> int:
    ap = argparse.ArgumentParser(description="回填工具可调用性声明字段")
    ap.add_argument("--check", action="store_true", help="只检查不写文件（CI 用）")
    ap.add_argument("--defs-dir", default=_DEFS_DIR, help="工具定义目录")
    args = ap.parse_args()

    yamls = sorted(f for f in os.listdir(args.defs_dir) if f.endswith(".yaml"))
    changed, skipped, missing, invalid, drifted = [], [], [], [], []

    for fname in yamls:
        name = os.path.splitext(fname)[0]
        path = os.path.join(args.defs_dir, fname)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()

        plane, effect, risk, category, tags, internal = _read_governance(text)
        values = derive(name, plane, effect, risk, category, tags, internal)

        # 取值域自校验（写进去的东西必须是合法的）
        assert values["tool_type"] in TOOL_TYPES, name
        assert values["callable_mode"] in CALLABLE_MODES, name
        assert values["permission_level"] in PERMISSION_LEVELS, name

        have = _fields_from_text(text)
        need = [f for f in _DECL_FIELDS if f not in have]

        if args.check:
            if need:
                missing.append((name, need))
                continue
            bad = [f for f in _DECL_FIELDS if have.get(f) not in _allowed_values(f)]
            if bad:
                invalid.append((name, {f: have.get(f) for f in bad}))
            elif have.get("permission_level") != values["permission_level"]:
                drifted.append((name, have.get("permission_level"),
                                values["permission_level"]))
            elif values.get("reason") and have.get("reason") != values["reason"]:
                drifted.append((name, have.get("reason") or "(空)", values["reason"]))
            continue

        if not need and all(have.get(f) == values[f] for f in _DECL_FIELDS):
            skipped.append(name)
            continue

        merged = dict(values)
        merged.update({f: have[f] for f in _DECL_FIELDS if f in have})  # 已有字段不覆盖
        block = _render_block(merged)
        # 只补缺失字段：先删掉已有行，再整块插入，避免同名字段出现两次
        for field in _DECL_FIELDS + ("reason",):
            if field in have:
                text = re.sub(rf"^{field}:.*\n?", "", text, count=1, flags=re.MULTILINE)
        text = _insert_after_anchor(text, block)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        changed.append(name)

    if args.check:
        ok = True
        if missing:
            ok = False
            print(f"[FAIL] {len(missing)} 个 YAML 缺可调用性字段：")
            for n, flds in missing:
                print(f"   - {n}: 缺 {flds}")
        if invalid:
            ok = False
            print(f"[FAIL] {len(invalid)} 个 YAML 取值非法：")
            for n, kv in invalid:
                print(f"   - {n}: {kv}")
        if drifted:
            ok = False
            print(f"[FAIL] {len(drifted)} 个 YAML 声明与治理轴派生值不一致"
                  f"（同一件事两份口径）：")
            for n, got, want in drifted:
                print(f"   - {n}: 声明={got!r} 派生={want!r}")
        if not ok:
            print("\n运行 python scripts/backfill_tool_callability.py 补齐/对齐")
            return 1
        print(f"[OK] 全部 {len(yamls)} 个 YAML 已具备可调用性声明，且与治理轴一致")
        return 0

    print(f"[OK] 回填完成：改写 {len(changed)} 个，已一致跳过 {len(skipped)} 个")
    from collections import Counter
    kinds = Counter("manual" if n in MANUAL_TOOLS else "auto" for n in changed)
    print(f"     本次写入的模式分布: {dict(kinds)}")
    return 0


def _allowed_values(field: str) -> tuple[str, ...]:
    return {
        "tool_type": TOOL_TYPES,
        "callable_mode": CALLABLE_MODES,
        "permission_level": PERMISSION_LEVELS,
        "llm_callable": ("true", "false"),
        "sandbox_allowed": ("true", "false"),
    }[field]


if __name__ == "__main__":
    sys.exit(main())
