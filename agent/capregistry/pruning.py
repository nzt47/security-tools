"""裁剪保护 —— **高危工具永不参与裁剪**（v1.4 §7；TASK-08 E8）

## 这条判据为什么必须**只有一处**

它同时被两条真实的裁剪路径消费：

| 路径 | 落点 | 裁什么 |
|---|---|---|
| ① Schema/token 裁剪 | `agent/tool_schema_pruner.py::prune_tool_defs` | 整条 tool_def 被移除（token 预算） |
| ② 名额截断 | `agent/lines/assembler.py::assemble` 第 ⑤ 步 | 打分入选段超出 `max_tools` 的尾巴 |

若两处各写一遍 `risk in ("high", "critical")`，改阈值时**必漏一处** ——
TASK-06 的 `needs_approval` 曾在仓库里有**三份手写副本**（见
`agent/lines/models.py::needs_approval_for` 的 docstring，那次是踩过的坑）。
故判据统一在本模块，两条路径都从这里取。

## 判据（v1.4 §7 逐字，不做任何"等价替换"）

    risk >= high  **或**  confirm_level >= L2  ⇒  **不参与裁剪**

- `risk` 偏序：`low < medium < high < critical`（权威值域 `agent.lines.models.RISKS`）
- `confirm_level` 偏序：`L0 < L1 < L2 < L3`（权威值域 `agent.lines.models.CONFIRM_LEVELS`）

**为什么不用既有的 `needs_approval` 代替**：`needs_approval` =
`govern ∨ extend ∨ risk ∈ {high, critical}`，它带**平面/效果**两轴，而规格的裁剪判据是
**risk + confirm_level** 两轴。两者在实测数据上不相等（例：`compress` 是
`medium` ⇒ `needs_approval=False`，但 `effect=write` ⇒ `confirm_level=L1`；
`generate_tool` 是 `govern+critical` ⇒ 两条都命中）。把判据替换成 `needs_approval`
会让"裁剪误伤率"这个指标**测的不是规格要求的那件事** ⇒ 不做替换。

## 未知风险 ⇒ 不裁（**刻意选择**的失败方向）

拿不到元数据时（名字不在 `data/tool_definitions/*.yaml`，例如运行时注册的 MCP /
插件 / 生成工具），**不得**假设它可裁：宁可少裁一个，也不可误裁一个高危。
这是"裁剪误伤率 = 0"唯一正确的失败方向（与 `agent/lines/assembler.py` 的
fail-closed 同精神，只是方向相反：装配器拒"无法证明安全"的，裁剪器护"无法证明安全"的）。

## 显式声明可裁的逃逸口

规格允许"必须显式声明可裁"的例外。本模块把它做成**调用方传入的显式集合**
（`prunable={...}`），**不新增 YAML 字段** —— 新增字段等于给能力定义再开一个声明面，
而能力定义的权威只有 `data/tool_definitions/*.yaml` 一处（D1）；
`ToolMeta` 的字段由 TASK-04 统一管理，本任务不越界。

## 回滚

`CP_TOOLSET_PRUNE_PROTECT=0` ⇒ 退回"无保护"的旧裁剪行为（不写数据文件）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple

__all__ = [
    "PROTECTED_RISKS",
    "PROTECTED_CONFIRM_RANK",
    "PROTECTED_CONFIRM_LEVELS",
    "CP_TOOLSET_PRUNE_PROTECT",
    "CP_TOOLSET_SCHEMA_TOKEN_BUDGET",
    "BudgetPlan",
    "ProtectionVerdict",
    "default_token_budget",
    "estimate_def_tokens",
    "is_prune_protected",
    "protect_enabled",
    "protection_for",
    "protection_of_meta",
    "protection_of_record",
    "lookup_protection",
    "protected_of_names",
    "plan_token_budget",
    "prune_tool_defs_for_budget",
    "tool_def_name",
]


# ════════════════════════════════════════════════════════════
#  开关（读取模式与 `agent/tool_schema_pruner.py` 的 SCHEMA_* 家族一致：
#  模块加载时一次性读、便于 monkeypatch.setattr 注入；两处都已登记进
#  `agent/settings/registry.py`（D5 零缺口守卫 `test_settings_registry.py`））
# ════════════════════════════════════════════════════════════


def _env_bool(key: str, default: bool) -> bool:
    return os.environ.get(key, "1" if default else "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except Exception:  # noqa: BLE001  非法值回退默认（默认不误伤）
        return default


#: 裁剪保护总开关（默认开）。置 0 ⇒ **退回无保护的旧裁剪行为**（回滚路径）。
#: 【为什么默认开】保护的默认方向必须是"不裁高危"；默认关等于 E8 的缺陷仍在。
CP_TOOLSET_PRUNE_PROTECT: bool = _env_bool("CP_TOOLSET_PRUNE_PROTECT", True)

#: tool schema 的 token 预算（默认 0 = **不按预算裁剪**，保持既有行为）。
#: 【为什么不给非零默认】既有生产链路从无"按 token 预算裁工具"的能力，
#: 直接给默认预算会改变线上工具集（可能把工具裁掉）。本任务补上这条**路径**
#: 并把保护规则前置；是否启用在运维侧显式声明（`CP_TOOLSET_SCHEMA_TOKEN_BUDGET=N`）。
CP_TOOLSET_SCHEMA_TOKEN_BUDGET: int = _env_int("CP_TOOLSET_SCHEMA_TOKEN_BUDGET", 0)


def protect_enabled() -> bool:
    """裁剪保护是否生效（读模块级常量，便于 monkeypatch 注入）"""
    return bool(CP_TOOLSET_PRUNE_PROTECT)


def default_token_budget() -> int:
    """默认 token 预算（≤0 = 不裁剪）"""
    try:
        return int(CP_TOOLSET_SCHEMA_TOKEN_BUDGET)
    except Exception:  # noqa: BLE001
        return 0


#: 受保护的风险档（v1.4 §7：`risk >= high`）
PROTECTED_RISKS: Tuple[str, ...] = ("high", "critical")

#: 受保护的确认级别序号（v1.4 §7：`confirm_level >= L2`）
PROTECTED_CONFIRM_RANK: int = 2
PROTECTED_CONFIRM_LEVELS: Tuple[str, ...] = ("L2", "L3")

#: token 估算口径：**与 `agent/context/assembler.py::estimate_tokens` 同一公式**
#: （字符数 // 3，CJK 密集场景偏保守）。这里不 import 那个模块，是为了不把上下文
#: 装配整包（含记忆/技能检索）拉进裁剪热路径 —— 公式只有一行，重复的代价远小于
#: 一条意外的重依赖链（D3）。
_CHARS_PER_TOKEN = 3


# ════════════════════════════════════════════════════════════
#  判据本体（纯函数；无 IO、无缓存、无副作用）
# ════════════════════════════════════════════════════════════


def _risk_rank(risk: Any) -> int:
    """风险档 → 序号（`low`=0 … `critical`=3）；**未知 ⇒ -1**

    值域取自 `agent.lines.models.RISKS`（单一真相源，惰性 import 避免 import 环）。
    未知返回 -1 会被 :func:`is_prune_protected` 解读为"不可判定" ⇒ **保护**。
    """
    text = str(risk or "").strip().lower()
    try:
        from agent.lines.models import RISKS  # noqa: PLC0415
        return RISKS.index(text)
    except Exception:  # noqa: BLE001  值域不可得 ⇒ 不可判定（保护）
        return -1


def _confirm_rank(level: Any) -> int:
    """确认级别 → 序号（`L0`=0 … `L3`=3）；**未知 ⇒ -1**

    值域与序号都取自 `agent.lines.models`（`CONFIRM_LEVELS` / `confirm_level_rank`），
    本模块**不复制**这张表 —— 只把"阈值 2"作为规格常量声明（`PROTECTED_CONFIRM_RANK`）。
    """
    try:
        from agent.lines.models import confirm_level_rank  # noqa: PLC0415
        return int(confirm_level_rank(str(level or "")))
    except Exception:  # noqa: BLE001
        return -1


def is_prune_protected(*, risk: Any, confirm_level: Any,
                       explicitly_prunable: bool = False) -> bool:
    """该工具是否**不得**被裁剪（纯函数，判据的唯一实现）

    Args:
        risk: 风险档（`low/medium/high/critical`）；空/未知 ⇒ 判"受保护"
        confirm_level: 确认级别（`L0`–`L3`）；空/未知 ⇒ 判"受保护"
        explicitly_prunable: 调用方**显式声明可裁**的逃逸口；仅当它是 `True`
            时才可能返回 `False`（未知风险也不例外 —— 显式声明优先于推断）

    Returns:
        `True` = 不参与裁剪
    """
    if explicitly_prunable:
        return False
    r = _risk_rank(risk)
    c = _confirm_rank(confirm_level)
    if r < 0 or c < 0:
        # 不可判定 ⇒ 保护（见模块 docstring 的"失败方向"）
        return True
    return r >= _risk_rank("high") or c >= PROTECTED_CONFIRM_RANK


@dataclass(frozen=True)
class ProtectionVerdict:
    """一条保护判定（**带原因**，供 trace / 测试断言"为什么"）"""

    name: str
    protected: bool
    reason: str
    risk: str = ""
    confirm_level: str = ""
    declared_prunable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "protected": bool(self.protected),
            "reason": self.reason,
            "risk": self.risk,
            "confirm_level": self.confirm_level,
            "declared_prunable": bool(self.declared_prunable),
        }


def _confirm_level_of(obj: Any) -> str:
    """从 `ToolMeta` / `CapabilityRecord` / 任意对象取**生效**确认级别

    三条取值路径（**不新增第三套口径**）：
      1. `effective_confirm_level`（`ToolMeta` 的属性，已裁定 override）；
      2. `confirm_level` 字段（`CapabilityRecord` 若将来带上）；
      3. 用 `agent.lines.models.derive_confirm_level(plane, effect, risk)` **派生**
         —— 与 TASK-06 进闸门的那条判据是**同一个函数**。
    """
    for attr in ("effective_confirm_level", "confirm_level"):
        raw = str(getattr(obj, attr, "") or "").strip().upper()
        if raw in PROTECTED_CONFIRM_LEVELS or raw in ("L0", "L1"):
            return raw
    try:
        from agent.lines.models import derive_confirm_level  # noqa: PLC0415
        return derive_confirm_level(
            str(getattr(obj, "plane", "") or ""),
            str(getattr(obj, "effect", "") or ""),
            str(getattr(obj, "risk", "") or ""),
        )
    except Exception:  # noqa: BLE001  派生不可得 ⇒ 留空 ⇒ 判"不可判定 ⇒ 保护"
        return ""


def protection_for(*, name: str, risk: Any, confirm_level: Any,
                   explicitly_prunable: bool = False) -> ProtectionVerdict:
    """判据 + 人类可读原因的组装（trace 与测试用）"""
    protected = is_prune_protected(risk=risk, confirm_level=confirm_level,
                                   explicitly_prunable=explicitly_prunable)
    r = str(risk or "").strip().lower()
    c = str(confirm_level or "").strip().upper()
    if not protected and explicitly_prunable:
        why = "调用方显式声明可裁（逃逸口）"
    elif not protected:
        why = f"可裁：risk={r or '未知'} < high 且 confirm_level={c or '未知'} < L2"
    elif r in PROTECTED_RISKS:
        why = f"不参与裁剪：risk={r} ≥ high（v1.4 §7）"
    elif c in PROTECTED_CONFIRM_LEVELS:
        why = f"不参与裁剪：confirm_level={c} ≥ L2（v1.4 §7）"
    else:
        why = (f"不参与裁剪：风险不可判定（risk={r or '空'}, "
               f"confirm_level={c or '空'}）⇒ 按未知处理")
    return ProtectionVerdict(name=str(name or ""), protected=protected, reason=why,
                             risk=r, confirm_level=c,
                             declared_prunable=bool(explicitly_prunable))


def protection_of_meta(meta: Any, *, prunable: bool = False) -> ProtectionVerdict:
    """`agent.lines.models.ToolMeta` → 判定"""
    return protection_for(name=str(getattr(meta, "name", "") or ""),
                          risk=getattr(meta, "risk", ""),
                          confirm_level=_confirm_level_of(meta),
                          explicitly_prunable=prunable)


def protection_of_record(record: Any, *, prunable: bool = False) -> ProtectionVerdict:
    """`agent.capregistry.spec.CapabilityRecord`（或任何带 plane/effect/risk 的视图）

    `CapabilityRecord` 目前**没有** `confirm_level` 字段（TASK-04 的能力规格里
    确认分级归 TASK-06 的工具侧），故这里走 :func:`_confirm_level_of` 的第 3 条路径
    按 `derive_confirm_level` 派生 —— 与闸门用的**同一个函数**，不是第二套口径。
    """
    return protection_for(name=str(getattr(record, "tool_name", "") or ""),
                          risk=getattr(record, "risk", ""),
                          confirm_level=_confirm_level_of(record),
                          explicitly_prunable=prunable)


def lookup_protection(name: str, *,
                      meta: Optional[Mapping[str, Any]] = None,
                      prunable: Iterable[str] = ()) -> ProtectionVerdict:
    """按工具名判定（缺省现查 `load_tool_meta()`；查不到 ⇒ **保护**）"""
    prunable_set = set(prunable or ())
    if meta is None:
        try:
            from agent.lines.models import load_tool_meta  # noqa: PLC0415
            meta = load_tool_meta()
        except Exception:  # noqa: BLE001  元数据不可得 ⇒ 全部按未知处理（保护）
            meta = {}
    m = (meta or {}).get(str(name or ""))
    if m is None:
        return protection_for(name=name, risk="", confirm_level="",
                              explicitly_prunable=str(name or "") in prunable_set)
    return protection_of_meta(m, prunable=str(name or "") in prunable_set)


def protected_of_names(names: Iterable[str], *,
                       meta: Optional[Mapping[str, Any]] = None,
                       prunable: Iterable[str] = ()) -> Dict[str, ProtectionVerdict]:
    """批量判定：`{名字: 判定}`（顺序由调用方的 `names` 决定，保留以便对拍）"""
    return {str(n): lookup_protection(str(n), meta=meta, prunable=prunable)
            for n in names}


# ════════════════════════════════════════════════════════════
#  token 预算裁剪的**规划**（不落地、不写文件；返回可断言的账）
# ════════════════════════════════════════════════════════════


def estimate_def_tokens(tool_def: Any) -> int:
    """单个 tool_def 的 token 粗估（序列化长度 // 3，见 `_CHARS_PER_TOKEN`）"""
    try:
        text = json.dumps(tool_def, ensure_ascii=False, sort_keys=True)
    except Exception:  # noqa: BLE001  不可序列化 ⇒ 按空壳估（不阻塞裁剪）
        text = str(tool_def)
    return max(1, len(text) // _CHARS_PER_TOKEN)


def tool_def_name(tool_def: Any) -> str:
    """从 OpenAI 格式 tool_def 取工具名（取不到 ⇒ 空串 ⇒ 判"未知 ⇒ 保护"）"""
    if not isinstance(tool_def, Mapping):
        return ""
    func = tool_def.get("function")
    if isinstance(func, Mapping):
        return str(func.get("name") or "")
    return str(tool_def.get("name") or "")


@dataclass
class BudgetPlan:
    """一次 token 预算裁剪的完整账目（**含"裁剪确实发生了"的证据**）

    【为什么要把 `dropped` 与 `protected` 分开记账】只报"高危一个都没被裁"是
    **假绿**：若裁剪根本没生效（例如预算给得过大、或判据把所有人都判成受保护），
    这句话同样成立。故本结构强制同时披露 `dropped`（真的被裁掉的）与
    `protected_kept`（**因为受保护而得以保留**的），并给出
    :meth:`misprune_rate` —— 它的分母是"本可被裁的受保护项"，分子是"实际被裁的
    受保护项"，即任务书 E8 的**裁剪误伤率**。
    """

    budget_tokens: int = 0
    used_tokens: int = 0
    kept: Tuple[str, ...] = ()
    dropped: Tuple[str, ...] = ()
    #: 受保护且**因此**没被裁的名字（本来按排序会落进裁剪区）
    protected_kept: Tuple[str, ...] = ()
    #: 受保护却仍被裁掉的（**恒为空**；非空即违规，测试锁死这条不变量）
    protected_dropped: Tuple[str, ...] = ()
    #: 受保护总数（含那些本来也轮不到裁的）—— 误伤率的分母口径说明见 `misprune_rate`
    protected_total: int = 0
    applied: bool = False
    reason: str = ""
    reasons: Dict[str, str] = field(default_factory=dict)

    @property
    def over_budget(self) -> bool:
        """受保护项太多、裁完可裁项仍超预算 ⇒ **如实暴露**（不静默牺牲高危）"""
        return bool(self.applied and self.used_tokens > self.budget_tokens > 0)

    def misprune_rate(self) -> float:
        """裁剪误伤率 = 被裁的受保护项 / 进入裁剪区的受保护项（无受保护项 ⇒ 0.0）"""
        denom = len(self.protected_kept) + len(self.protected_dropped)
        if denom <= 0:
            return 0.0
        return len(self.protected_dropped) / denom

    def to_dict(self) -> Dict[str, Any]:
        return {
            "budget_tokens": int(self.budget_tokens),
            "used_tokens": int(self.used_tokens),
            "kept_count": len(self.kept),
            "dropped": list(self.dropped),
            "protected_kept": list(self.protected_kept),
            "protected_dropped": list(self.protected_dropped),
            "protected_total": int(self.protected_total),
            "applied": bool(self.applied),
            "over_budget": self.over_budget,
            "misprune_rate": self.misprune_rate(),
            "reason": self.reason,
        }


def plan_token_budget(items: Sequence[Any], *,
                      budget_tokens: int,
                      name_of: Callable[[Any], str],
                      tokens_of: Callable[[Any], int],
                      protected_of: Callable[[Any], ProtectionVerdict],
                      ) -> Tuple[Tuple[Any, ...], BudgetPlan]:
    """按 token 预算裁剪一个**已排序**的列表（保序裁剪：从尾部往前裁）

    语义（v1.4 §7）：
      · 预算 ≤ 0 ⇒ **不裁剪**（`applied=False`，等价于"开关关闭"）；
      · 否则从**尾部**（调用方认为最不重要的那一端）逐个裁，**跳过受保护项**；
      · 受保护项一律保留 ⇒ 可能超预算（`over_budget=True`），**不得**为了满足
        预算去牺牲高危工具：那正是 E8 要清零的"误裁"。

    Args:
        items: 已按重要性降序排好的列表（保序裁剪依赖这个前提）
        budget_tokens: 预算上限（token）；≤0 = 不裁剪
        name_of / tokens_of / protected_of: 三个访问器（纯函数式，便于用真实数据喂）

    Returns:
        `(keep_items, plan)` —— `keep_items` 与 `items` **同序**
    """
    ordered = list(items)
    total = sum(int(tokens_of(x)) for x in ordered)
    names = [name_of(x) for x in ordered]
    verdicts = [protected_of(x) for x in ordered]
    plan = BudgetPlan(budget_tokens=int(budget_tokens or 0), used_tokens=total,
                      kept=tuple(names), protected_total=sum(
                          1 for v in verdicts if v.protected))
    if budget_tokens <= 0:
        plan.applied = False
        plan.reason = "预算 ≤ 0 ⇒ 不裁剪（开关关闭口径）"
        return tuple(ordered), plan

    keep = [True] * len(ordered)
    used = total
    dropped_names = []
    protected_kept = []
    # 从尾部往前，跳过受保护项
    for idx in range(len(ordered) - 1, -1, -1):
        if used <= budget_tokens:
            break
        if verdicts[idx].protected:
            protected_kept.append(names[idx])
            continue
        keep[idx] = False
        used -= int(tokens_of(ordered[idx]))
        dropped_names.append(names[idx])

    kept_items = tuple(x for i, x in enumerate(ordered) if keep[i])
    plan.applied = bool(dropped_names)
    plan.kept = tuple(names[i] for i in range(len(ordered)) if keep[i])
    # 记账时**从后往前**得到的名单反过来，保持与 `items` 同序（可对拍）
    plan.dropped = tuple(sorted(dropped_names, key=names.index))
    plan.protected_kept = tuple(sorted(protected_kept, key=names.index))
    plan.used_tokens = used
    plan.reason = (f"预算 {budget_tokens} token：裁掉 {len(plan.dropped)} 个可裁工具；"
                   f"{len(plan.protected_kept)} 个受保护工具因判据被保留"
                   if plan.applied else
                   f"未超预算（{used} ≤ {budget_tokens}）⇒ 未裁剪")
    for name, v in zip(names, verdicts):
        plan.reasons[name] = v.reason
    return kept_items, plan


def prune_tool_defs_for_budget(tool_defs: Sequence[Any], *,
                               budget_tokens: int,
                               meta: Optional[Mapping[str, Any]] = None,
                               prunable: Iterable[str] = (),
                               protect: bool = True,
                               ) -> Tuple[list, BudgetPlan]:
    """OpenAI 格式 tool_defs 的 token 预算裁剪（受保护项一律不裁）

    这是 `agent/tool_schema_pruner.py` 的**唯一**预算裁剪入口（本模块提供实现，
    裁剪器只做接线），以保证判据与排序口径只有一份。

    Args:
        protect: `False` ⇒ **关闭保护**（回滚口径，等价于"所有工具都可裁"）。
            保护关闭时 `protected_kept` 恒空、`misprune_rate()` 恒 0 —— 但那是
            "没有保护"而不是"没误伤"，故 `BudgetPlan.to_dict()` 里同时带
            `applied` 与 `dropped`，让调用方分得清这两种情况。
    """
    defs = list(tool_defs or [])

    def _verdict(td: Any) -> ProtectionVerdict:
        name = tool_def_name(td)
        if not protect:
            return ProtectionVerdict(name=name, protected=False,
                                     reason="裁剪保护已关闭（CP_TOOLSET_PRUNE_PROTECT=0）")
        return lookup_protection(name, meta=meta, prunable=prunable)

    kept, plan = plan_token_budget(
        defs,
        budget_tokens=budget_tokens,
        name_of=tool_def_name,
        tokens_of=estimate_def_tokens,
        protected_of=_verdict,
    )
    return list(kept), plan
