"""主线装配器 —— 由主线档案算出"本轮该给模型看哪些工具"

【它解决什么】
    旧口径（`tool_router.get_tools_for_input`）是"按 category.priority 全局排序 →
    截断 25"，而 `core(6)+web(9)+file(10) = 25` 恰好等于上限 ⇒ 只要同时命中 web 与
    file，`code/system/extension/pdf/software/schedule/v2` **全部返回 0 个**，
    含 `shell_execute`（见 docs/工具集评估与重分类报告.md §4.3）。

    本装配器用**平面保底**取代全局截断：
        ① 先按 effect 上限过滤（治理）
        ② 每个权重 > 0 的平面各自先取 top-N（N = plane_floors，默认 2）
        ③ 剩余名额按"平面权重 × 核心工具加成 × 标签匹配"打分补充
        ④ 最后才截到 max_tools，且**保底名额整体优先于打分名额**

    ⇒ 命中 `act` 平面就一定能拿到它的保底工具，不再被别的平面吃光。

【不易】
    - 保底名额整体优先：宁可超出 max_tools 一点，也不让某个已启用平面清零
      （与旧 `PINNED_TOOLS` 同精神，但按平面推广）
    - **无 plane/effect 声明的工具一律拒绝**（fail-closed）：无法证明其安全边界
      就不给它 —— 宁可少一个工具，不可悄悄多给一个未定级的
【变易】
    plane_floors / boost / mute / plane_weights 全部来自档案，是数据
【简易】
    纯函数（除读取一次工具元数据），无副作用；返回 trace 便于 UI 解释
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .models import PLANES, LineProfile, ToolMeta, load_tool_meta

#: 打分常数（权重化，便于调参）
_W_PLANE = 100.0     # 平面权重系数
_W_BOOST = 60.0      # 主线核心工具加成
_W_TAG = 12.0        # 标签命中加成
_W_RESIDENT = 40.0   # 常驻平面固定加成（保证永远排最前）

_DEFAULT_FLOOR = 2


@dataclass
class AssemblyResult:
    """一次装配的结果（含可解释 trace）"""

    line_id: str
    tools: List[str] = field(default_factory=list)
    #: plane -> 该平面最终入选的工具
    by_plane: Dict[str, List[str]] = field(default_factory=dict)
    #: 被 effect 上限拦下的工具（治理拒绝）
    denied_by_effect: List[str] = field(default_factory=list)
    #: 被 mute 显式排除的
    muted: List[str] = field(default_factory=list)
    #: 因名额不足被截断的
    truncated: List[str] = field(default_factory=list)
    #: 无 plane/effect 声明 ⇒ **fail-closed 拒绝**（无法证明其安全边界）
    denied_unknown: List[str] = field(default_factory=list)
    #: 每个工具的入选理由
    reasons: Dict[str, str] = field(default_factory=dict)
    #: 需要人工确认的工具（来自 risk/extend）
    needs_approval: List[str] = field(default_factory=list)
    max_tools: int = 0
    over_budget: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "line_id": self.line_id,
            "tools": list(self.tools),
            "count": len(self.tools),
            "by_plane": {k: list(v) for k, v in self.by_plane.items()},
            "denied_by_effect": list(self.denied_by_effect),
            "denied_unknown": list(self.denied_unknown),
            "muted": list(self.muted),
            "truncated": list(self.truncated),
            "reasons": dict(self.reasons),
            "needs_approval": list(self.needs_approval),
            "max_tools": self.max_tools,
            "over_budget": self.over_budget,
        }


def _score(tool: str, meta: ToolMeta, profile: LineProfile,
           boost_set: set, tag_set: set) -> float:
    weight = profile.plane_weights.get(meta.plane, 0.0)
    score = weight * _W_PLANE
    if meta.plane == "resident":
        score += _W_RESIDENT
    if tool in boost_set:
        score += _W_BOOST
    if tag_set and set(meta.tags) & tag_set:
        score += _W_TAG
    return score


def assemble(
    profile: LineProfile,
    available: Iterable[str],
    *,
    meta: Optional[Dict[str, ToolMeta]] = None,
    max_tools: Optional[int] = None,
) -> AssemblyResult:
    """按主线档案装配工具集

    Args:
        profile: 主线档案
        available: 候选工具名（通常为 `agent.tools.list_tools()` 的名字集合）
        meta: 工具元数据（缺省现场加载 data/tool_definitions）
        max_tools: 覆盖档案里的 max_tools（None = 用档案值）

    Returns:
        AssemblyResult（tools 已按"常驻优先、平面权重降序"排好）
    """
    meta = meta if meta is not None else load_tool_meta()
    cap = int(max_tools if max_tools is not None else profile.max_tools)
    boost_set = set(profile.boost)
    tag_set = set(profile.tags)
    mute_set = set(profile.mute)
    active_planes = {p for p, w in profile.plane_weights.items() if w > 0}

    res = AssemblyResult(line_id=profile.id, max_tools=cap)

    # ── ① 治理过滤：effect 上限 + mute + 平面启用 ──
    candidates: List[str] = []
    seen = set()
    for raw in available:
        name = str(raw)
        if not name or name in seen:
            continue
        seen.add(name)
        m = meta.get(name)
        if m is None:
            # 未登记 plane/effect ⇒ **fail-closed 拒绝**：
            # 无法证明其安全边界，就不给。这类工具应先补 YAML 定义。
            res.denied_unknown.append(name)
            continue
        if name in mute_set:
            res.muted.append(name)
            continue
        if not profile.allows_effect(m.effect):
            res.denied_by_effect.append(name)
            continue
        if m.plane not in active_planes:
            res.denied_by_effect.append(name)
            continue
        candidates.append(name)

    # ── ② 打分并分平面归组 ──
    scored: List[tuple] = []
    pools: Dict[str, List[str]] = {p: [] for p in PLANES if p in active_planes}
    for name in candidates:
        m = meta[name]
        s = _score(name, m, profile, boost_set, tag_set)
        scored.append((s, name))
        pools.setdefault(m.plane, []).append(name)

    # 平面内按分数降序（同分按名字稳定排序）
    for p in pools:
        pools[p].sort(key=lambda n: (-_score(n, meta[n], profile, boost_set, tag_set), n))
    scored.sort(key=lambda x: (-x[0], x[1]))

    # ── ③ 平面保底：每个启用平面先各取 plane_floors 个 ──
    kept: List[str] = []
    kept_set: set = set()
    for p in sorted(active_planes, key=lambda x: -profile.plane_weights.get(x, 0.0)):
        floor = int(profile.plane_floors.get(p, _DEFAULT_FLOOR))
        if floor <= 0:
            continue
        for name in pools.get(p, [])[:floor]:
            if name not in kept_set:
                kept.append(name)
                kept_set.add(name)
                res.reasons[name] = f"平面保底（{p} top{floor}）"

    # ── ④ 按打分补足剩余名额 ──
    for s, name in scored:
        if len(kept) >= cap:
            break
        if name in kept_set:
            continue
        kept.append(name)
        kept_set.add(name)
        res.reasons[name] = f"打分入选（score={s:.0f}, plane={meta[name].plane}）"

    # ── ⑤ 截断：只裁"打分入选"的部分，**全部保底一律保留** ──
    #     若保底名额本身已用满/超出 cap，则允许总数略超 cap（宁多给一个，
    #     也不让某个已启用平面清零）——这与旧 PINNED_TOOLS 同精神，但按平面推广。
    if len(kept) > cap:
        floor_kept = [n for n in kept if res.reasons.get(n, "").startswith("平面保底")]
        floor_set = set(floor_kept)
        scored_kept = [n for n in kept if n not in floor_set]
        room = max(0, cap - len(floor_kept))
        dropped = set(scored_kept[room:])
        kept = [n for n in kept if n not in dropped]
        res.truncated = [n for n in scored_kept if n in dropped]
        res.over_budget = len(kept) > cap

    # ── ⑥ 输出排序：常驻最前，其后按平面权重降序、分数降序 ──
    def out_key(n: str) -> tuple:
        m = meta[n]
        return (
            0 if m.plane == "resident" else 1,
            -profile.plane_weights.get(m.plane, 0.0),
            -_score(n, m, profile, boost_set, tag_set),
            n,
        )

    kept.sort(key=out_key)
    res.tools = kept
    res.by_plane = {
        p: [n for n in kept if meta[n].plane == p]
        for p in sorted(active_planes, key=lambda x: -profile.plane_weights.get(x, 0.0))
    }
    res.needs_approval = [n for n in kept if meta[n].needs_approval]
    return res


def estimate_tokens(tools: Sequence[str], meta: Optional[Dict[str, ToolMeta]] = None) -> int:
    """粗略估算工具 schema 的 token 占用（按描述长度，仅用于 UI 展示排序）"""
    meta = meta if meta is not None else load_tool_meta()
    return sum(len(meta[t].description) // 3 + 40 for t in tools if t in meta)


__all__ = ["assemble", "AssemblyResult", "estimate_tokens"]
