"""E4-1 第一期：脚本编排的工具白名单（三重交集，fail-closed）

【不易·白名单 = 交集，任一为空则该调用被拒】（设计稿 §3）
  1. 该 run 的**主线装配集**（agent.lines.assembler，经
     agent/subagent/assembly.py 的唯二出口 —— 与 fan_out 同源，不另算一份）；
  2. E4 静态允许清单（只读为主；write_file / shell / 网络工具默认不进）；
  3. 调用方显式勾选（allow_tools；**只能收窄不能放宽**：与静态清单再交一次）；
  4. 宿主**真实注册**的工具名（与 fan_out 的候选池同源；注册表为空 ⇒ 白名单为空）。

【永远拒绝】治理平面（govern：ext_install / generate_tool / connect_mcp / schedule_*）、
编排工具自身（嵌套=0）、terminal（PTY 会绕过命令沙箱）、以及一切非 effect=read 的工具。

【为什么还用 effect=read 再兜一刀】静态清单是人维护的；工具 YAML 的 effect 是声明式
事实。两者不一致时按**更严**的一侧处置（声明改成 write 就自动出局，不必记得改清单）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

logger = logging.getLogger(__name__)

#: E4 静态允许清单：只读、本地、无网络。新增条目必须同时是 effect=read 的工具。
STATIC_ALLOWED: Tuple[str, ...] = (
    # 文件系统读
    "read_file", "list_directory", "get_file_info", "search_files", "diff_files",
    "read_spill",
    # 代码检索/结构化读
    "grep", "json_query", "data_format_detect", "sqlite_query",
    # Office / PDF 纯库读取（E3-2）
    "docx_read", "xlsx_read", "pptx_read",
    "read_pdf", "get_pdf_info", "read_pdf_tables",
)

#: 永远不进白名单的工具（即使静态清单/装配集里出现）
NEVER_ALLOWED: Tuple[str, ...] = ("run_orchestration_script", "terminal")

#: 只读效果（唯一放行的 effect）
_READ_EFFECT = "read"


def _available_tool_names() -> Tuple[str, ...]:
    """宿主**真实注册**的工具名（与 fan_out 同口径；注册表为空 ⇒ 空集 fail-closed）"""
    try:
        from agent import tools as _tools
        names = []
        for item in _tools.list_tools():
            if isinstance(item, Mapping):
                name = str(item.get("name") or "").strip()
                if name and name not in names:
                    names.append(name)
        return tuple(names)
    except Exception as exc:  # noqa: BLE001 注册表读不到 ⇒ 空集（fail-closed）
        logger.warning("[orchestration] 读取工具注册表失败（白名单按空集）: %s",
                       type(exc).__name__)
        return ()


def _normalize(raw: Iterable[Any]) -> Tuple[str, ...]:
    out = []
    for item in raw or ():
        name = str(item or "").strip()
        if name and name not in out:
            out.append(name)
    return tuple(out)


def resolve(line: str = "", allow_tools: Sequence[str] = ()) -> Dict[str, Any]:
    """算出本次 run 的白名单

    Args:
        line: 主线 id（空串 = 读全局激活指针；都没有则用只读默认集）
        allow_tools: 调用方显式勾选的工具名

    Returns:
        {"ok": True, "tools": (...), "line": str, "mode": str, "note": str,
         "dropped": {name: reason}}
        或 {"ok": False, "code": "line_unavailable", "line": str, "error": str}
    """
    from agent.lines import get_line_registry, load_tool_meta
    from agent.subagent.assembly import LineUnavailable, resolve_subagent_assembly

    meta = load_tool_meta()
    available = _available_tool_names()
    registry = get_line_registry()
    try:
        assembly = resolve_subagent_assembly(str(line or ""), registry, meta, available)
    except LineUnavailable as exc:
        # C-6：已知领域异常只回自定义属性 message
        return {"ok": False, "code": "line_unavailable", "line": str(line or ""),
                "error": getattr(exc, "message", "") or "主线不可用"}
    except Exception as exc:  # noqa: BLE001 装配失败同样 fail-closed（不回退全量）
        logger.warning("[orchestration] 主线装配失败（白名单按不可用处理）: %s",
                       type(exc).__name__)
        return {"ok": False, "code": "line_unavailable", "line": str(line or ""),
                "error": "主线装配失败"}

    line_tools = set(assembly.tools)
    static = set(STATIC_ALLOWED)
    extra = set(_normalize(allow_tools))
    # 三重交集：显式勾选只在与静态清单相交时有效（不能放宽静态面）
    # 第四刀（防"装配集里有、注册表里没有"）：只放行**真实注册**的工具名。
    # 判据与 fan_out 的候选池同源；注册表为空 ⇒ 白名单为空（fail-closed）。
    registered = set(available)
    base = ((line_tools & static) | (extra & static)) & registered
    tools = []
    dropped: Dict[str, str] = {}
    for name in sorted(base):
        if name in NEVER_ALLOWED:
            dropped[name] = "never_allowed"
            continue
        item = meta.get(name)
        if item is None:
            dropped[name] = "no_meta"
            continue
        if str(getattr(item, "plane", "") or "").strip().lower() == "govern":
            dropped[name] = "govern_plane"
            continue
        if str(getattr(item, "effect", "") or "").strip().lower() != _READ_EFFECT:
            dropped[name] = "not_read"
            continue
        tools.append(name)
    return {
        "ok": True,
        "tools": tuple(tools),
        "line": str(assembly.line_id or ""),
        "mode": str(assembly.mode or ""),
        "note": str(assembly.note or ""),
        "dropped": dropped,
    }


__all__ = ["NEVER_ALLOWED", "STATIC_ALLOWED", "resolve"]
