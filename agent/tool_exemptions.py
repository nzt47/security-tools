"""逐工具「需确认」豁免（开关中心的一个键 + 界面徽章开关的后端）

【它改的是什么】`CP_TOOL_CONFIRM_LEVEL_EXEMPT` —— 一个逗号分隔的工具名单；
被列进去的工具免「摘要确认」，不再进审批收件箱（详见 `agent/tool_gate.py` 里该键的注释）。

【为什么没有自己的存储】名单只存在于那**一个开关**里：默认来自 `.env`，界面改动落
`data/ui_settings.json` 覆盖层。另立一个 store 就是同一事实的第二份口径（D1 禁止）；
而且覆盖层已自带三条纪律——来源标注（env / ui_override / config / default）、原子写、
**绝不修改 .env 与 config.yaml**。

【为什么有些工具放不了】描述符 `trust.requires_approval=true` 的那批
（shell_execute / run_sandbox / generate_tool / ext_* / *_mcp）判在分级层**之前**，
本名单够不着。界面据此把它们标成「锁定 + 原因」，而不是给一个点了没反应的开关
（后者正是"看起来能用、实际不生效"的那类假绿灯）。

【谁能改】`settings.change` 由 Actor 矩阵单表判定：只有 **human** 放行，
auto / sub_agent / service_account 一律拒（`agent/security/actor_matrix.py` 的
`(OP_SETTINGS_CHANGE, ACTOR_*)` 四行）。⇒ 云枢自己**无法**给自己松绑，
必须人在界面上点；每次改动都经 `settings.change` 落审计链。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 生效值所在的开关键（与 `agent/tool_gate.py::CONFIRM_LEVEL_EXEMPT_ENV` 同一个字符串）
EXEMPT_SETTING_KEY = "CP_TOOL_CONFIRM_LEVEL_EXEMPT"


def _meta() -> Dict[str, Any]:
    from agent.lines import load_tool_meta          # noqa: PLC0415 惰性：读 91 个 YAML
    try:
        return dict(load_tool_meta())
    except Exception as e:                          # noqa: BLE001 读不到 ⇒ 空表（调用方如实报空）
        logger.warning("[tool_exemptions] 工具元数据不可用: %s: %s", type(e).__name__, e)
        return {}


def descriptor_gated_tools() -> set:
    """描述符 `trust.requires_approval=true` 的工具名集合（本名单**够不着**的那批）

    复用 `tool_gate` 已缓存好的审批索引（按路径 + mtime 缓存）⇒ 这里不产生新 IO，
    也不会出现"两处各读一遍 descriptors.json、口径还不一致"的第二真相源。
    """
    from agent import tool_gate as G                   # noqa: PLC0415
    try:
        index = G._cached_derived(G.DESCRIPTORS_PATH, G._build_approval_index)
    except Exception as e:                          # noqa: BLE001 读不到 ⇒ 空集（从严：UI 不谎报可点）
        logger.warning("[tool_exemptions] 描述符索引不可用: %s: %s", type(e).__name__, e)
        return set()
    return {str(cid).rsplit(".", 1)[-1] for cid in index.values() if cid}


def effective_value() -> Dict[str, Any]:
    """取该开关的**生效值**与来源（env / ui_override / config / default），如实标注

    为什么不直接读 `os.environ`：界面要能回答"这个值是哪来的、还能不能改"。
    开关中心的解析器本来就是为这个问题写的，这里复用它（D1：不另写一套来源判定）。
    """
    try:
        from agent.settings.resolver import resolve     # noqa: PLC0415
        res = resolve(EXEMPT_SETTING_KEY)
        if res is not None:
            return {
                "raw": str(res.value if res.value is not None else ""),
                "source": str(getattr(res, "source", "") or ""),
                "source_label": str(getattr(res, "source_label", "") or ""),
                "env_locked": bool(getattr(res, "env_locked", False)),
                "editable": bool(getattr(res, "editable", False)),
            }
    except Exception as e:                              # noqa: BLE001 解析器不可用 ⇒ 退回 env
        logger.warning("[tool_exemptions] 开关解析器不可用（退回 env）: %s: %s",
                       type(e).__name__, e)
    raw = ""
    try:
        raw = str(os.environ.get(EXEMPT_SETTING_KEY, "") or "")
    except Exception:                                   # noqa: BLE001
        raw = ""
    return {"raw": raw, "source": "env" if raw else "default",
            "source_label": "", "env_locked": False, "editable": True}


def exempt_names() -> List[str]:
    """当前名单里的工具名（去重、保持书写顺序）；空 → `[]`"""
    raw = effective_value().get("raw", "")
    names: List[str] = []
    for part in str(raw or "").split(","):
        text = part.strip()
        if text and text not in names:
            names.append(text)
    return names


def candidates() -> List[Dict[str, Any]]:
    """界面用的逐工具清单（只列**本来就要人确认**的工具；L0 免确认的不在此列）"""
    gated = descriptor_gated_tools()
    current = {n.lower() for n in exempt_names()}
    from agent import tool_gate as G                    # noqa: PLC0415
    out: List[Dict[str, Any]] = []
    for name, meta in sorted(_meta().items()):
        level = str(getattr(meta, "effective_confirm_level", "") or "")
        if level == "L0":
            continue
        by_descriptor = name in gated
        out.append({
            "tool": name,
            "level": level,
            "effect": str(getattr(meta, "effect", "") or ""),
            "risk": str(getattr(meta, "risk", "") or ""),
            # 生效值既接受工具名也接受 canonical id ⇒ 两种写法都算"已豁免"
            "exempt": bool({name.lower(), G._canonical_candidate(name).lower()} & current),
            "exemptable": not by_descriptor,
            "blocked_reason": (
                "该工具的审批要求来自**能力描述符**（trust.requires_approval），"
                "判定排在分级层之前 ⇒ 豁免名单够不着它。要放行只能改 "
                "data/descriptors.json，或关掉整个审批边界开关"
                "（CP_TOOL_GATE_APPROVAL_ENFORCE=0，B 级开关）"
            ) if by_descriptor else "",
        })
    return out


def view() -> Dict[str, Any]:
    """`GET /api/cp/tool-exemptions` 的响应体"""
    state = effective_value()
    return {
        "exempt": exempt_names(),
        "source": state.get("source", ""),
        "source_label": state.get("source_label", ""),
        "env_locked": bool(state.get("env_locked", False)),
        "editable": bool(state.get("editable", True)),
        "items": candidates(),
        "key": EXEMPT_SETTING_KEY,
    }


def set_exempt(tool: str, exempt: bool, *, reason: str = "", actor: str = "",
               actor_type: str = "human", session_id: str = "",
               identity_source: str = "") -> Dict[str, Any]:
    """增/删名单中的一项（**经开关中心的服务层**落值 + 授权 + 审计）

    Returns:
        `{"ok": True, "changed": bool, "exempt": [...], "outcome": {...}}`
        或 `{"ok": False, "code": ..., "message": ..., "status": ...}`。
    """
    name = str(tool or "").strip()
    if not name:
        return {"ok": False, "code": "missing_tool", "message": "缺少 tool", "status": 400}

    metas = _meta()
    if name not in metas:
        return {"ok": False, "code": "unknown_tool",
                "message": f"未登记的工具：{name}", "status": 404}

    if name in descriptor_gated_tools():
        return {"ok": False, "code": "descriptor_gated", "status": 409,
                "message": (f"{name} 的审批要求来自能力描述符（trust.requires_approval），"
                            "判定排在分级层之前 ⇒ 豁免名单放不了它。"
                            "要放行只能改 data/descriptors.json 或关掉审批边界总开关。")}

    # L0 = 本来就免确认（effect=read ∧ risk=low）⇒ 列进豁免名单没有任何效果。
    # 与其让人写进去一条"看着生效、其实什么都不做"的记录，不如如实拒绝。
    if str(getattr(metas[name], "effective_confirm_level", "") or "") == "L0":
        return {"ok": False, "code": "not_required", "status": 409,
                "message": f"{name} 本来就是 L0（免确认），不需要豁免"}

    names = exempt_names()
    lowered = {n.lower() for n in names}
    if exempt:
        if name.lower() in lowered:
            return {"ok": True, "changed": False, "exempt": names,
                    "message": f"{name} 已在豁免名单里（未重复写入）"}
        names.append(name)
    else:
        names = [n for n in names if n.lower() != name.lower()]

    new_value = ",".join(names)

    from agent.settings.service import get_settings_service   # noqa: PLC0415
    service = get_settings_service()
    outcome = service.change(
        EXEMPT_SETTING_KEY, new_value, actor=actor, actor_type=actor_type,
        reason=reason or ("加入豁免名单" if exempt else "移出豁免名单"),
        session_id=session_id, identity_source=identity_source)
    if not outcome.ok:
        return {"ok": False, "code": str(getattr(outcome, "code", "") or "exempt_failed"),
                "message": str(getattr(outcome, "message", "") or "开关变更未通过"),
                "status": int(getattr(outcome, "status", 403) or 403)}

    return {"ok": True, "changed": True, "exempt": exempt_names(),
            "outcome": outcome.to_dict()}
