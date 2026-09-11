"""治理字段桥接（S1-02 descriptor 的 undo_hint / 补偿动作 / risk）

【任务定位】
    任务书步骤 2 要求 `stage.promote` 审批「其 `undo_hint`/补偿动作**可回溯**
    （对齐 S1-02 的 governance 字段）」，且 §5.7⑦ 的 destructive 判定需要真实
    风险等级。二者都来自 **S1-02 的 ToolDescriptor**（`trust.risk_level` /
    `governance.undo_hint` / `governance.compensating_action`）。

【为什么是「桥接」而不是直接 import】
    审批路径必须**轻且能失败**：`agent.descriptors` 会加载 pydantic 契约层与
    descriptor JSON。故本模块：
      - **惰性**获取 registry（首次使用时才 import + load），失败即返回空；
      - 提供 `install_descriptor_resolvers()` 显式接线到
        `approval_guard.set_risk_resolver`；
      - 未接线时风险按「未知」处理（**不**臆造 destructive）。

【可回溯口径】
    `governance_trace_fields()` 输出三个稳定键：
      - `undo_hint`            撤销指引（可为空串）
      - `compensating_action`  补偿动作（可为空串）
      - `undo_hint_status`     `resolved` / `missing` / `unresolved`
    审计载荷带这三键，事后即可回答「这次推进能不能退、怎么退」。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger("agent.security.governance_bridge")

#: 状态口径
STATUS_RESOLVED = "resolved"
STATUS_MISSING = "missing"
STATUS_UNRESOLVED = "unresolved"

#: 对象类型 → descriptor 查询是否适用（其它对象类型不做 descriptor 查询）
_DESCRIPTOR_OBJECT_TYPES = ("stage.promote", "capability", "skill", "tool", "stage")

_lock = threading.RLock()
_registry: Any = None
_registry_tried = False
_installed = False


def reset_bridge() -> None:
    """复位缓存（测试隔离）"""
    global _registry, _registry_tried, _installed
    with _lock:
        _registry = None
        _registry_tried = False
        _installed = False


def _get_registry() -> Any:
    """惰性获取 descriptor registry（失败一次后不再重试，避免反复 IO）"""
    global _registry, _registry_tried
    with _lock:
        if _registry_tried:
            return _registry
        _registry_tried = True
        try:
            from agent.descriptors.registry import DescriptorRegistry
            _registry = DescriptorRegistry()
        except Exception as e:  # noqa: BLE001 桥接失败 → 按未接线处理
            logger.info("[GovernanceBridge] descriptor registry 不可用（按未接线处理）: %s", e)
            _registry = None
        return _registry


def set_registry(registry: Any) -> None:
    """注入 registry（测试 / 调用方自带实例）"""
    global _registry, _registry_tried
    with _lock:
        _registry = registry
        _registry_tried = True


def _applicable(object_type: str) -> bool:
    key = str(object_type or "").strip().lower()
    return any(key == t or key.startswith(f"{t}.") or key.startswith(f"{t}:")
               for t in _DESCRIPTOR_OBJECT_TYPES)


def _lookup(object_type: str, object_id: str) -> Any:
    if not _applicable(object_type) or not object_id:
        return None
    registry = _get_registry()
    if registry is None:
        return None
    try:
        return registry.get(str(object_id))
    except Exception as e:  # noqa: BLE001 查询失败不阻断审批
        logger.debug("[GovernanceBridge] descriptor 查询失败 %s: %s", object_id, e)
        return None


def descriptor_risk(object_type: str, object_id: str,
                    payload: Optional[Mapping[str, Any]] = None) -> str:
    """descriptor 风险等级（供 `set_risk_resolver` 接线）；不可得 → "" """
    if payload and str(payload.get("risk_level") or payload.get("risk") or ""):
        return str(payload.get("risk_level") or payload.get("risk")).strip().lower()
    descriptor = _lookup(object_type, object_id)
    if descriptor is None:
        return ""
    try:
        trust = getattr(descriptor, "trust", None)
        risk = getattr(trust, "risk_level", "")
        return str(getattr(risk, "value", risk) or "").strip().lower()
    except Exception as e:  # noqa: BLE001
        logger.debug("[GovernanceBridge] 读取 risk_level 失败: %s", e)
        return ""


def governance_trace_fields(object_type: str, object_id: str,
                            payload: Optional[Mapping[str, Any]] = None
                            ) -> Dict[str, Any]:
    """治理可回溯字段（undo_hint / 补偿动作 / 状态）

    来源顺序：审批载荷叶子字段 → descriptor registry。
    两者皆缺 → `undo_hint_status="unresolved"`（**如实标注，不臆造**）。
    """
    payload = payload or {}
    undo = str(payload.get("undo_hint") or "").strip()
    comp = str(payload.get("compensating_action") or "").strip()
    if undo or comp:
        return {"undo_hint": undo, "compensating_action": comp,
                "undo_hint_status": STATUS_RESOLVED}
    descriptor = _lookup(object_type, object_id)
    if descriptor is None:
        return {"undo_hint": "", "compensating_action": "",
                "undo_hint_status": STATUS_UNRESOLVED}
    try:
        governance = getattr(descriptor, "governance", None)
        undo = str(getattr(governance, "undo_hint", "") or "").strip()
        comp = str(getattr(governance, "compensating_action", "") or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.debug("[GovernanceBridge] 读取 governance 失败: %s", e)
        return {"undo_hint": "", "compensating_action": "",
                "undo_hint_status": STATUS_UNRESOLVED}
    status = STATUS_RESOLVED if (undo or comp) else STATUS_MISSING
    return {"undo_hint": undo, "compensating_action": comp,
            "undo_hint_status": status}


#: 外来/不可信来源的 provenance 等级（§3.2 provenance：borrowed/mirrored 为外来）
_TAINT_PROVENANCE = ("borrowed", "mirrored", "external", "derived")
#: 载荷中可直接声明污染的键
_TAINT_PAYLOAD_KEYS = ("taint", "tainted", "untrusted", "external_content")


def taint_flags(object_type: str, object_id: str,
                payload: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """审批上下文是否含**外来内容**（前端据此渲染 TaintBadge，§5.7⑦）

    Returns:
        {"taint": bool, "taint_reason": str}——`taint_reason` 为空表示无污染。
    """
    payload = payload or {}
    for key in _TAINT_PAYLOAD_KEYS:
        if payload.get(key):
            return {"taint": True, "taint_reason": f"载荷声明 {key}"}
    for key in ("provenance_level", "provenance"):
        val = str(payload.get(key) or "").strip().lower()
        if val in _TAINT_PROVENANCE:
            return {"taint": True, "taint_reason": f"provenance={val}（外来来源）"}
    descriptor = _lookup(object_type, object_id)
    if descriptor is None:
        return {"taint": False, "taint_reason": ""}
    try:
        origin = getattr(descriptor, "origin", None)
        if bool(getattr(origin, "external_endpoint", False)):
            return {"taint": True, "taint_reason": "origin.external_endpoint=True"}
        provenance = getattr(descriptor, "provenance", None)
        level = getattr(provenance, "level", "")
        level_text = str(getattr(level, "value", level) or "").strip().lower()
        if level_text in _TAINT_PROVENANCE:
            return {"taint": True, "taint_reason": f"provenance={level_text}"}
    except Exception as e:  # noqa: BLE001
        logger.debug("[GovernanceBridge] 污染标记判定失败: %s", e)
    return {"taint": False, "taint_reason": ""}


def install_descriptor_resolvers(*, registry: Any = None,
                                 force: bool = False) -> bool:
    """把 descriptor 的 risk / governance 接线到审批守卫

    Returns:
        是否完成接线（已接线且非 force → 直接返回 True）。
    """
    global _installed
    with _lock:
        if _installed and not force:
            return True
        if registry is not None:
            set_registry(registry)
        try:
            from agent.security.approval_guard import set_risk_resolver
            set_risk_resolver(descriptor_risk)
            _installed = True
            logger.info("[GovernanceBridge] descriptor 风险/治理字段已接线到审批守卫")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("[GovernanceBridge] 接线失败（风险按未知处理）: %s", e)
            return False


__all__ = [
    "STATUS_RESOLVED", "STATUS_MISSING", "STATUS_UNRESOLVED",
    "descriptor_risk", "governance_trace_fields", "taint_flags",
    "install_descriptor_resolvers", "set_registry", "reset_bridge",
]
