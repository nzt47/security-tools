"""主线装配与编排器的接线层

【接线原则（向后兼容）】
    有激活主线  → 用装配器算工具集（主线优先）
    无激活主线  → 完全保持旧行为（hybrid 智能选择 → 关键词兜底）
    ⇒ 不装线就等于没改过；装线才启用新链路。这样可逐线灰度、随时回退。

【为什么放在这一层而不是改 tool_router】
    `tool_router` / `tool_router_hybrid` 的语义是"按用户输入选工具"（查询相关），
    而主线装配的语义是"按 Agent 身份定能力边界"（身份相关）。两者正交：
    身份先做**减法**（effect 上限、mute、平面启用），查询再做**排序**。
    把减法做在 identity 层，才不会出现"某次查询把超出权限的工具召回来"。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .assembler import AssemblyResult, assemble
from .models import LineProfile, load_tool_meta
from .registry import LineRegistryError, get_line_registry
from .skillpack import SkillPack, resolve_skill_pack, unrestricted_pack

logger = logging.getLogger(__name__)


def resolve_line_id(explicit: Optional[str] = None) -> Optional[str]:
    """解析本次生效的主线 id：显式 > 全局激活指针 > None"""
    if explicit:
        return explicit
    try:
        return get_line_registry().get_active()
    except Exception as e:  # noqa: BLE001
        logger.warning("[lines] 读取激活主线失败（按未装线处理）: %s", e)
        return None


def assemble_for_line(
    available: Optional[List[str]] = None,
    *,
    line_id: Optional[str] = None,
    max_tools: Optional[int] = None,
) -> Optional[AssemblyResult]:
    """按生效主线装配工具集

    Args:
        available: 候选工具（缺省 = 注册表全量）
        line_id: 显式主线（缺省读全局激活指针）
        max_tools: 覆盖档案的 max_tools

    Returns:
        AssemblyResult；**未装线 / 主线不存在 / 主线停用 / 装配异常时返回 None**
        （调用方据此回退旧路径，绝不让装配故障阻断对话）
    """
    lid = resolve_line_id(line_id)
    if not lid:
        return None
    try:
        profile = get_line_registry().load(lid)
    except LineRegistryError as e:
        logger.warning("[lines] 主线档案不可用（回退旧路径）: %s", e)
        return None
    if profile is None or not profile.enabled:
        logger.info("[lines] 主线 %s 不存在或已停用，回退旧路径", lid)
        return None

    try:
        if available is None:
            from agent import tools as _tools
            available = [t["name"] for t in _tools.list_tools()]
        return assemble(profile, available, max_tools=max_tools)
    except Exception as e:  # noqa: BLE001  装配故障绝不断对话
        logger.warning("[lines] 装配异常（回退旧路径）: %s", e)
        return None


def line_whitelist(
    base_whitelist: Optional[List[str]] = None,
    *,
    line_id: Optional[str] = None,
    max_tools: Optional[int] = None,
) -> Tuple[Optional[List[str]], Optional[AssemblyResult]]:
    """给编排器用的便捷入口

    Args:
        base_whitelist: 旧口径的启用白名单（`_get_enabled_tools_whitelist()`）；
                        None 表示"不限制" ⇒ 用注册表全量作为候选
        line_id / max_tools: 透传

    Returns:
        (tools, result)。未装线时返回 (None, None)，调用方继续走旧路径。
    """
    available = base_whitelist
    result = assemble_for_line(available, line_id=line_id, max_tools=max_tools)
    if result is None:
        return None, None
    return list(result.tools), result


def line_skill_pack(line_id: Optional[str] = None) -> SkillPack:
    """按生效主线算「本线允许注入哪些技能」（**永不抛**）

    【为什么与 `assemble_for_line` 同纪律】技能段的异常会直接进系统提示词，
    「装配故障绝不阻断对话」在这里的具体含义是：解析不出技能包 ⇒ 回退**不限制**
    （= 未装线的旧行为），而不是回退成「一个技能都不给」。后者是静默的能力剥夺，
    且用户看到的只是「提示词少了一段」，排查成本极高。

    Returns:
        `SkillPack`。未装线 / 主线不存在 / 主线停用 / 任何异常 ⇒
        `mode="unrestricted"`（技能侧没有「身份」可减，见 agent/lines/skillpack.py）。
    """
    try:
        lid = resolve_line_id(line_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("[lines] 读取激活主线失败（技能按不限制处理）: %s", e)
        return unrestricted_pack("resolve-error")
    if not lid:
        # 未装线 == 等于没改过（技能侧同样如此）
        return resolve_skill_pack(None)
    try:
        profile = get_line_registry().load(lid)
    except LineRegistryError as e:
        logger.warning("[lines] 主线档案不可读（技能按不限制处理）: %s", e)
        return unrestricted_pack("profile-error", line_id=lid)
    if profile is None:
        logger.info("[lines] 主线 %s 不存在（技能按不限制处理）", lid)
        return unrestricted_pack("profile-missing", line_id=lid)
    if not profile.enabled:
        # 停用 = 本轮不装线（与 assemble_for_line 同口径：回退旧路径）
        logger.info("[lines] 主线 %s 已停用（技能按不限制处理）", lid)
        return unrestricted_pack("line-disabled", line_id=lid)
    try:
        return resolve_skill_pack(profile)
    except Exception as e:  # noqa: BLE001 技能包解析故障绝不断对话
        logger.warning("[lines] 技能包解析异常（按不限制处理）: %s", e)
        return unrestricted_pack("resolve-error", line_id=lid)


def filter_skill_entries(
    entries: List[Dict[str, Any]],
    *,
    key: str = "skill_id",
    line_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按本线技能包过滤「按意图命中」的技能条目（返回 `(保留, 被剔除)`）

    【为什么还需要第二个闸门】
        技能有**两条**进提示词的路：
          ① 系统提示词的**技能段** —— `_build_skill_instructions`（已按 `skills:` 过滤）
          ② **按意图命中注入** —— ContextAssembler 旁路 →
             `Orchestrator._context_assembler_procedural` → `SkillLoader.match`
        只堵①会让②成为绕开白名单的第二条通道，那么"本线 `skills:` 是技能面的权威"
        这句话就是**假的**（22 条提示词型技能会不受约束地进提示词）。本函数是②的闸门。

    【这是治理口径变更，不是普通接线（必须显式）】
        ① 那一半是"接线"：接上之前 persona 段每轮都在注入，故补数据保证行为逐字一致。
        ② 这一半是**身份减法本身**：由意图命中的技能属于"某条线才该有的专家技能"，
        按线声明才是设计意图。当前 7 条内置主线都没声明文件轨技能 ⇒ 过滤生效后
        旁路不再注入它们；**要让某条线在旁路里拿到某个技能，把 id 写进该线 `skills:` 即生效**
        （预览接口 `/api/agent-lines/preview` 的 `skills.allowed` 会如实显示本线允许集）。

    【失败语义：一律原样保留（不限制）】任何异常、或条目形状不认识（非 Mapping）
        ⇒ **整批原样返回**、`dropped` 为空。与 `line_skill_pack` 的"解析不出来就回退不限制"
        同纪律：宁可少拦，不可静默剥夺（技能侧 fail-open 的理由见 skillpack.py 开头）。

    Args:
        entries: 技能条目（每项至少含 `key` 指定的 id 字段）。
        key: id 字段名（旁路用 `skill_id`）。
        line_id: 显式主线（缺省读全局激活指针）。

    Returns:
        `(保留, 被剔除)`；两者之和恒等于入参（同一对象，未复制）。
    """
    items = list(entries or [])
    if not items:
        return [], []
    if not all(isinstance(e, Mapping) for e in items):
        logger.warning("[lines] 技能条目形状不认识 ⇒ 跳过白名单过滤（原样保留）")
        return items, []
    try:
        pack = line_skill_pack(line_id)
    except Exception as e:  # noqa: BLE001 过滤失败不得让注入链路挂掉
        logger.warning("[lines] 技能包解析失败 ⇒ 跳过白名单过滤（原样保留）: %s", e)
        return items, []
    if pack.unrestricted:
        return items, []
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for entry in items:
        sid = str(entry.get(key) or "").strip()
        (kept if pack.allows(sid) else dropped).append(entry)
    return kept, dropped


def describe_line(line_id: Optional[str] = None) -> Dict[str, Any]:
    """给 UI / 状态面板用的主线摘要（含 token 粗估 + 技能段判定）"""
    lid = resolve_line_id(line_id)
    # 技能段与工具段同源同算：面板拿到的必须是**后端判定结果**，前端不再重算
    # （重算 = 第二份口径，两份迟早分叉）。
    skills = line_skill_pack(lid).to_dict()
    if not lid:
        return {"active": None, "line": None, "tool_count": None, "skills": skills}
    try:
        profile = get_line_registry().load(lid)
    except LineRegistryError as e:
        return {"active": lid, "line": None, "error": str(e), "skills": skills}
    if profile is None:
        return {"active": lid, "line": None, "error": "主线不存在", "skills": skills}
    result = assemble_for_line(line_id=lid)
    return {
        "active": lid,
        "line": profile.to_dict(),
        "tool_count": len(result.tools) if result else 0,
        "by_plane": {k: len(v) for k, v in (result.by_plane.items() if result else [])},
        "needs_approval": list(result.needs_approval) if result else [],
        "over_budget": bool(result.over_budget) if result else False,
        "skills": skills,
    }


__all__ = ["resolve_line_id", "assemble_for_line", "line_whitelist", "line_skill_pack",
           "filter_skill_entries", "describe_line"]
