"""流水线入口的能力键归一（TASK-S3-01 L1 收口 / 能力地图统一口径）

S2-01 的工具级统一 Trace 以**工具名**落账 `capability_id`（遗留 #1）；S3-01 已在
落账点完成改写（`agent/descriptors/bridge.resolve_capability_id` +
`agent/tool_calling.resolve_tool_capability_id`）。但**改写之前已落账的历史轨迹**
仍以工具名存放 —— 若入口只按 canonical 键取数，历史样本会被静默丢掉，
"S2-01 台账 → 消化流水线"这条链就断了。

本模块因此提供**入口侧的两件事**：

1. `merge_keys()`：由 canonical capability_id 反推**同名历史键**（末段工具名、
   台账 alias、`capability.name`），给出"同一能力的全部落账键"；
2. `collect_rows()`：按上述键集合**一次扫描**统一台账，把 canonical 行与历史
   工具名行**一起取出**（并统一改写为 canonical 键），使清洗/挖掘看到的是
   完整样本集，而非"改写之后才开始有新数据"。

`normalize_trace_capability()` 同时是**入口改写**的公共函数：把任意一条 Trace 的
`capability_id` 归一为 canonical 值（不修改原对象，返回新值）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger("agent.digestion.capability")


def resolve(name: str, *, registry: Any = None) -> Dict[str, Any]:
    """`bridge.resolve_capability_id` 的入口封装（best-effort，台账不可用即回退）"""
    try:
        from agent.descriptors.bridge import resolve_capability_id
        return resolve_capability_id(name, registry=registry)
    except Exception as e:  # noqa: BLE001
        logger.debug("capability 解析失败（回退原文）: %s", e)
        return {"capability_id": str(name or ""), "tool_name": str(name or ""),
                "joined": False, "rewritten": False,
                "resolved_by": "advisory_fallback"}


def normalize_trace_capability(trace: Any, *, registry: Any = None) -> str:
    """一条 Trace 的 `capability_id` → canonical（入口改写；不修改原对象）"""
    raw = str(getattr(trace, "capability_id", "") or "")
    if not raw:
        return ""
    return str(resolve(raw, registry=registry)["capability_id"] or raw)


def merge_keys(capability_id: str, *, registry: Any = None) -> List[str]:
    """canonical 能力键 → **同一能力的全部落账键**（含历史工具名/alias/name）

    顺序即优先级：canonical → 末段工具名 → 台账 alias → 台账 `capability.name`。
    去重后按出现顺序返回（canonical 恒定在首位）。
    """
    cid = str(capability_id or "").strip()
    if not cid:
        return []
    keys: List[str] = [cid]
    if cid.startswith("cp."):
        tail = cid.rsplit(".", 1)[-1]
        if tail and tail not in keys:
            keys.append(tail)
    try:
        reg = registry
        if reg is None:
            from agent.descriptors.registry import DescriptorRegistry
            reg = DescriptorRegistry()
        for alias in reg.aliases_of(cid):
            if alias and alias not in keys:
                keys.append(alias)
        desc = reg.get(cid)
        name = str(getattr(getattr(desc, "capability", None), "name", "") or "")
        if name and name not in keys:
            keys.append(name)
    except Exception as e:  # noqa: BLE001  台账不可用 → 只用 canonical + 末段
        logger.debug("merge_keys 台账不可用: %s", e)
    return keys


def collect_rows(
    store: Any,
    capability_id: str,
    *,
    limit: int = 200,
    registry: Any = None,
) -> Tuple[List[Any], Dict[str, Any]]:
    """统一台账 → 命中的**轨迹行**（整条任务链，含任务级收口行）

    S2-01 契约 `list_by_capability(capability_id, limit)` 回答的是"哪些**行**属于
    该能力"；而挖掘需要的是"哪些**轨迹**用到了该能力，且每条轨迹的完整步骤序列"。
    本函数因此分两步（一次扫描内完成）：

    1. **入口匹配**：按 `merge_keys()` 的键集合找出该能力的行（canonical + 历史
       工具名），得到 **task_id 集合**；
    2. **轨迹展开**：把这些 task 的**全部行**（含能力级子 Trace 与任务级收口行
       ``capability_id=""``）取出并升序返回 —— 任务级收口行用于读取任务结果状态
       （同类判定键的 outcome 元）与 workspace，故必须一并取出。

    Args:
        limit: **轨迹条数**上限（按每条轨迹最早的 ``started_at`` 取最近 N 条）

    Returns:
        (rows, meta)；meta 含 ``keys`` / ``legacy_keys`` / ``matched``（命中行数）/
        ``legacy_matched`` / ``trajectories``（展开出的轨迹数）/
        ``trajectory_rows``（返回行数）/ ``total_in_ledger`` / ``truncated``
    """
    keys = merge_keys(capability_id, registry=registry)
    key_set: Set[str] = set(keys)
    legacy_keys = key_set - {str(capability_id)}
    meta: Dict[str, Any] = {
        "capability_id": capability_id,
        "keys": list(keys),
        "legacy_keys": sorted(legacy_keys),
        "matched": 0,
        "legacy_matched": 0,
        "trajectories": 0,
        "trajectory_rows": 0,
        "total_in_ledger": 0,
        "truncated": False,
    }
    try:
        all_rows = store.query() or []
    except Exception as e:  # noqa: BLE001  台账不可用 → 空集（不抛，由上游判不足）
        logger.warning("统一台账读取失败: %s", e)
        return [], meta
    meta["total_in_ledger"] = len(all_rows)

    hit_tasks: Set[str] = set()
    for row in all_rows:
        raw = str(getattr(row, "capability_id", "") or "")
        if raw in key_set:
            hit_tasks.add(str(getattr(row, "task_id", "") or ""))
            meta["matched"] += 1
            if raw in legacy_keys:
                meta["legacy_matched"] += 1
    if not hit_tasks:
        return [], meta

    expanded = [row for row in all_rows
                if str(getattr(row, "task_id", "") or "") in hit_tasks]
    # 【顺序】保持统一台账的返回顺序（``started_at`` 升序 + 等值时写入序稳定），
    # **不**再叠加 trace_id 之类的随机兜底键——那会把同一时刻记录的步骤打乱，
    # 使 LCS 骨架失去意义（实现期实测到的真实缺陷）。
    meta["trajectories"] = len(hit_tasks)

    if limit is not None and limit > 0 and len(hit_tasks) > int(limit):
        first_seen: Dict[str, float] = {}
        for row in expanded:
            task_id = str(getattr(row, "task_id", "") or "")
            started = float(getattr(row.timing, "started_at", 0.0) or 0.0)
            if task_id not in first_seen:
                first_seen[task_id] = started
        keep = {task_id for task_id, _ in sorted(
            first_seen.items(), key=lambda kv: (kv[1], kv[0]))[-int(limit):]}
        expanded = [row for row in expanded
                    if str(getattr(row, "task_id", "") or "") in keep]
        meta["trajectories"] = len(keep)
        meta["truncated"] = True

    meta["trajectory_rows"] = len(expanded)
    return expanded, meta


def group_rows_by_task(rows: Sequence[Any]) -> Dict[str, List[Any]]:
    """轨迹行按 ``task_id`` 分组（任务 = 一条轨迹）

    **保留输入顺序**（统一台账的 ``started_at`` 升序），不重新排序——步骤顺序即
    执行顺序，加入随机兜底键会破坏它。分组键按首次出现顺序排列（确定性来自输入）。
    """
    groups: Dict[str, List[Any]] = {}
    for row in rows:
        task_id = str(getattr(row, "task_id", "") or "")
        groups.setdefault(task_id, []).append(row)
    return groups


__all__ = [
    "resolve", "normalize_trace_capability", "merge_keys", "collect_rows",
    "group_rows_by_task",
]
