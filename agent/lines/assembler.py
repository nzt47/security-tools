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
    - **高危工具不参与截断**（TASK-08 E8 / v1.4 §7）：`risk >= high` 或
      `confirm_level >= L2` 的工具在第 ⑤ 步一律保留。判据唯一实现在
      `agent/capregistry/pruning.py`（与工具级 token 预算裁剪共用），本模块只接线。
      故 `len(tools)` 可能略超 `max_tools` —— 这是**刻意的**：名额不足时牺牲高危
      能力，会让模型改用可见的低危工具拼出等价效果，比多给一个工具危险得多。
    - **无 plane/effect 声明的工具一律拒绝**（fail-closed）：无法证明其安全边界
      就不给它 —— 宁可少一个工具，不可悄悄多给一个未定级的
【变易】
    plane_floors / boost / mute / plane_weights 全部来自档案，是数据
    `protect_high_risk` 可关（回滚路径），关闭后退回"无保护的旧截断"
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

#: 平面**声明序**（models.PLANES）—— 本模块把它当作「权重并列时的次级键」。
#: 为什么是它而不是字典序：第 ② 步的 pools 本来就是按 PLANES 建的，
#: 即「这个子系统本来就存在的那条确定次序」（与 DET-2 的次级键口径一致）。
_PLANE_DECL_ORDER = {p: i for i, p in enumerate(PLANES)}
_PLANE_ORDER_MISSING = 1 << 30


def _plane_order(planes: Iterable[str], weights: Dict[str, float]) -> List[str]:
    """平面迭代序：主键 = 平面权重降序（**未变**），次级键 = 声明序 → 名字

    【DET-3】为什么必须补次级键：active_planes 是 set（第 ① 步的集合推导式），
    权重**并列**时 sorted(..., key=-weight) 的先后就落到 set 迭代序上 ⇒ 同一份
    代码、同一份档案，res.by_plane 的键序、res.reasons 的插入序、进而
    AssemblyResult.to_dict() 载荷的**字节**都随 PYTHONHASHSEED 变。
    实测（5 种子 × 7 条真实档案）：dev / engineering / harness 三条档案的
    perceive 与 act 权重并列（=1.0），载荷指纹各取 2 种；其余 4 条档案权重互异 ⇒ 1 种。

    【它到不到达用户可见输出】res.tools（喂给模型的工具表）**不受影响** ——
    第 ⑥ 步 out_key 末位是工具名字，是全序；实测 7/7 档案跨种子逐位相同。
    受影响的是 API/UI 载荷：routes_agent_lines._preview_dict（前端
    yunshu-ui/src/pages/hub/tools/lines.tsx 按 Object.entries(by_plane) 渲染分组）
    与 integration.describe_line（状态面板）—— 键序即上屏的分组先后。
    """
    return sorted(planes,
                  key=lambda p: (-weights.get(p, 0.0),
                                 _PLANE_DECL_ORDER.get(p, _PLANE_ORDER_MISSING),
                                 str(p)))


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
    #: 【TASK-08 E8】本可被截断、**因受保护（risk>=high 或 confirm_level>=L2）而免裁**的
    protected_kept: List[str] = field(default_factory=list)
    #: 裁剪保护判据不可用 ⇒ 降级为"本轮不截断"（见 `_protected_for_truncation`）
    protection_degraded: bool = False
    #: 【TASK-08 E8】token 预算（0 = 未启用）
    token_budget: int = 0
    #: 因 token 预算被裁掉的可裁工具（**"裁剪确实发生了"的证据**）
    token_pruned: List[str] = field(default_factory=list)
    #: 裁剪后的 token 粗估
    token_used: int = 0
    #: 受保护项太多、裁完可裁项仍超预算 ⇒ 如实暴露（不静默牺牲高危）
    token_over_budget: bool = False
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
            "protected_kept": list(self.protected_kept),
            "protection_degraded": bool(self.protection_degraded),
            "token_budget": int(self.token_budget),
            "token_pruned": list(self.token_pruned),
            "token_used": int(self.token_used),
            "token_over_budget": bool(self.token_over_budget),
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


def _protected_for_truncation(names: Iterable[str],
                              meta: Dict[str, ToolMeta]) -> Optional[set]:
    """取"不参与名额截断"的名字集合（判据唯一实现在 `agent/capregistry/pruning.py`）

    【降级方向】判据模块不可得 ⇒ 返回 `None`（调用方解读为"全员受保护 = 本轮不截断"）：
    宁可多给一个工具，也不误裁一个高危 —— 与 E8 的指标方向一致，且候选集此前
    已过 effect 上限 / mute / 平面启用三道治理减法，不会失控。
    """
    try:
        from agent.capregistry.pruning import lookup_protection  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001  判据不可得 ⇒ 不截断（见 docstring）
        logger.warning("[lines] 裁剪保护判据不可用 ⇒ 本轮不做名额截断: %s", e)
        return None
    return {str(n) for n in names if lookup_protection(str(n), meta=meta).protected}


def _as_budget(value: Any) -> int:
    """把 `token_budget` 归一成整数（`None`/非法 ⇒ 0 = 不裁剪，默认不误伤）"""
    try:
        return int(value or 0)
    except Exception:  # noqa: BLE001
        return 0


def _def_for(name: str, meta: Dict[str, ToolMeta]) -> Dict[str, Any]:
    """按 `name` 合成 OpenAI 格式 tool_def（**只用于 token 估算**）

    形状与 `agent.tool_schema_pruner` 拿到的完全一致，故估算口径只有一份
    （`agent.capregistry.pruning.estimate_def_tokens`）。
    """
    m = meta.get(name)
    return {
        "type": "function",
        "function": {
            "name": str(name),
            "description": str(getattr(m, "description", "") or ""),
            "parameters": getattr(m, "input_schema", None) or {},
        },
    }


def _prune_by_token_budget(kept: List[str], meta: Dict[str, ToolMeta], *,
                           budget: int, protect: bool) -> Optional[Any]:
    """按 token 预算裁装配结果（受保护项不裁）

    Returns:
        `BudgetPlan`；判据模块不可用 ⇒ `None`（调用方标记降级并**不裁剪**）
    """
    try:
        from agent.capregistry.pruning import plan_token_budget  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001  判据不可得 ⇒ 不裁剪（安全方向）
        logger.warning("[lines] 裁剪保护判据不可用 ⇒ 跳过 token 预算裁剪: %s", e)
        return None
    defs = {n: _def_for(n, meta) for n in kept}
    from agent.capregistry.pruning import estimate_def_tokens  # noqa: PLC0415

    def _tokens(n: str) -> int:
        return estimate_def_tokens(defs[n])

    def _verdict(n: str):
        from agent.capregistry.pruning import lookup_protection  # noqa: PLC0415
        if not protect:
            from agent.capregistry.pruning import ProtectionVerdict  # noqa: PLC0415
            return ProtectionVerdict(name=n, protected=False,
                                     reason="裁剪保护已关闭")
        return lookup_protection(n, meta=meta)

    _items, plan = plan_token_budget(
        list(kept), budget_tokens=int(budget), name_of=lambda n: n,
        tokens_of=_tokens, protected_of=_verdict)
    return plan


def assemble(
    profile: LineProfile,
    available: Iterable[str],
    *,
    meta: Optional[Dict[str, ToolMeta]] = None,
    max_tools: Optional[int] = None,
    protect_high_risk: bool = True,
    token_budget: Optional[int] = None,
) -> AssemblyResult:
    """按主线档案装配工具集

    Args:
        profile: 主线档案
        available: 候选工具名（通常为 `agent.tools.list_tools()` 的名字集合）
        meta: 工具元数据（缺省现场加载 data/tool_definitions）
        max_tools: 覆盖档案里的 max_tools（None = 用档案值）
        protect_high_risk: **裁剪保护**（TASK-08 E8 / v1.4 §7，默认开）：
            受保护工具不参与名额截断与 token 预算裁剪；`False` ⇒ 退回旧口径（回滚路径）
        token_budget: 装配结果的 token 预算；`None`/≤0 ⇒ 不裁剪（默认口径，
            行为与改动前完全一致）。>0 ⇒ 从最不重要的一端裁**可裁**工具

    Returns:
        AssemblyResult（tools 已按"常驻优先、平面权重降序"排好）
    """
    meta = meta if meta is not None else load_tool_meta()
    cap = int(max_tools if max_tools is not None else profile.max_tools)
    boost_set = set(profile.boost)
    tag_set = set(profile.tags)
    mute_set = set(profile.mute)
    active_planes = {p for p, w in profile.plane_weights.items() if w > 0}
    #: 【DET-3】迭代序与成员判定分开：成员判定继续用 set（O(1) 且与次序无关），
    #: 「按什么次序迭代」一律走 _plane_order（见其 docstring 的实测证据）。
    active_plane_order = _plane_order(active_planes, profile.plane_weights)

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
    for p in active_plane_order:
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
    #
    # 【TASK-08 E8】裁剪误伤率 = 0（v1.4 §7）：
    #   `risk >= high` 或 `confirm_level >= L2` 的工具**不参与截断**。
    #   判据的唯一实现在 `agent/capregistry/pruning.py`（与工具级 token 预算裁剪
    #   共用同一处），此处只接线 —— 两处各写一遍阈值 = 改阈值必漏一处。
    #   【为什么宁可超 cap 也不裁高危】本函数在第 ①步已经用 effect 上限做过治理
    #   减法：能走到这里的工具都是**这条主线被允许调用**的。此时若因名额不足把
    #   `shell_execute`/`write_file` 这类裁掉，模型会转而用**可见的低危工具**
    #   拼出等价效果（或直接失败）—— 那比"多给一个工具"危险得多。
    _protected: set = set()
    if protect_high_risk:
        _cand = _protected_for_truncation(kept, meta)
        if _cand is None:
            # 判据不可用 ⇒ 降级为"全员受保护"（本轮不截断），并如实标记
            _protected = set(kept)
            res.protection_degraded = True
        else:
            _protected = _cand
    if len(kept) > cap:
        floor_kept = [n for n in kept if res.reasons.get(n, "").startswith("平面保底")]
        floor_set = set(floor_kept)
        scored_kept = [n for n in kept if n not in floor_set]
        room = max(0, cap - len(floor_kept))
        # 受保护项**先占名额**：可裁项只剩 `room - len(shielded)` 个位置
        shielded = [n for n in scored_kept if n in _protected]
        cuttable = [n for n in scored_kept if n not in _protected]
        room_for_cuttable = max(0, room - len(shielded))
        dropped = set(cuttable[room_for_cuttable:])
        kept = [n for n in kept if n not in dropped]
        res.truncated = [n for n in cuttable if n in dropped]
        #: **因受保护而免于被裁**的名字（E8 的证据字段：裁剪确实发生，且没碰到它们）
        res.protected_kept = [n for n in shielded if n in kept]
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

    # ── ⑦ token 预算裁剪（可选；TASK-08 E8 / v1.4 §7）──
    #     【为什么放在排序之后】`out_key` 之后 `kept` 是"最重要的在前"，故从**尾部**
    #     裁 = 裁掉最不重要的那一端（`plan_token_budget` 的保序裁剪依赖这个前提）。
    #     【为什么默认关】本仓库此前没有"按 token 预算裁装配结果"这条路径；默认开启
    #     会改变线上工具集。显式给 `token_budget`（或运维侧置
    #     `CP_TOOLSET_SCHEMA_TOKEN_BUDGET`）才启用。
    #     【保护】受保护项（risk >= high 或 confirm_level >= L2）一律不裁；判据与
    #     工具级 schema 裁剪**同一处实现**（`agent/capregistry/pruning.py`）。
    _budget = _as_budget(token_budget)
    if _budget > 0:
        _plan = _prune_by_token_budget(kept, meta, budget=_budget,
                                       protect=protect_high_risk)
        if _plan is None:
            res.protection_degraded = True
        else:
            kept = list(_plan.kept)
            res.token_budget = _budget
            res.token_pruned = list(_plan.dropped)
            res.token_used = int(_plan.used_tokens)
            res.token_over_budget = bool(_plan.over_budget)
            res.protected_kept = sorted(set(res.protected_kept)
                                        | set(_plan.protected_kept),
                                        key=out_key)
            for n in res.token_pruned:
                res.reasons.pop(n, None)

    res.tools = kept
    res.by_plane = {
        p: [n for n in kept if meta[n].plane == p]
        for p in active_plane_order
    }
    res.needs_approval = [n for n in kept if meta[n].needs_approval]
    return res


def estimate_tokens(tools: Sequence[str], meta: Optional[Dict[str, ToolMeta]] = None) -> int:
    """粗略估算工具 schema 的 token 占用（按描述长度，仅用于 UI 展示排序）"""
    meta = meta if meta is not None else load_tool_meta()
    return sum(len(meta[t].description) // 3 + 40 for t in tools if t in meta)


__all__ = ["assemble", "AssemblyResult", "estimate_tokens"]
