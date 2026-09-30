"""EgressGuard — 数据出域的**执行点**（P7.1-20 执行侧）

【它在架构里的位置：策略引擎的对侧】
    ┌───────────────────────────┐        ┌──────────────────────────────┐
    │ 决策侧 agent/policy/       │        │ 执行侧 agent/guardrails/      │
    │  egress.decide_egress()    │───────▶│  egress_guard.EgressGuard     │
    │  → PolicyEngine.check()    │ 决策    │  → 放行 / 拦截（无中间态）      │
    │  **不做网络动作**           │        │  → HttpClient 真发请求         │
    └───────────────────────────┘        └──────────────────────────────┘

    本文件**只做一件事**：把决策应用到真实调用点。它不判定策略、不读策略文件、
    不认识 match 语法——策略怎么想是决策侧的事。这样 S4-03（guardrails 注入防御
    六机制）与本文件的改动面完全不重叠：本文件是**新增**文件，既有 guardrails
    模块一行未改。

【与既有网络能力的关系（守不易）】
    - **默认开启**：出域是本任务的安全属性所在（§2.5/§5.7-4）。开启不等于收紧——
      只有在**有策略命中**或**见到凭据/读到密钥**时才会拦截，其余情况
      ``decide_egress`` 返回「策略层无覆盖」，行为与加装前**逐字节一致**。
    - **内部错误 fail-open**：决策层异常 ⇒ 放行并告警（通用硬约束 1：新增机制失败
      不得阻断主流程）。但**真实 deny 绝不 fail-open**——那是安全机制的全部意义。
    - 可用 ``CP_POLICY_EGRESS_GUARD=0`` 临时关闭（排障用；默认 "1"）。

【为什么用 dict 而不是抛异常】
    ``HttpClient`` 的既有契约是「异常一律转成 ``{"ok": False, "error": ...}``」
    （见 ``_error_result``）。拦截走同一条返回路径，调用方（工具层/搜索层）无需
    新增异常处理分支——«不改变既有接口行为» 的具体做法。需要异常语义的场景用
    ``EgressGuard.enforce``。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("agent.guardrails.egress_guard")

#: 环境变量：是否启用出域执行点（"0" 关闭）
ENV_EGRESS_GUARD = "CP_POLICY_EGRESS_GUARD"

#: 拦截时返回给调用方的错误前缀（稳定文案；测试与前端按此前缀判定）
BLOCKED_ERROR_PREFIX = "出域被策略拒绝"

#: 需人工确认时的错误前缀
ASK_ERROR_PREFIX = "出域需人工确认"


class EgressBlocked(PermissionError):
    """出域被策略拒绝（``enforce`` 路径抛出）

    Attributes:
        decision: 决策层的 :class:`~agent.policy.egress.EgressDecision`。
    """

    def __init__(self, message: str, decision: Any = None) -> None:
        super().__init__(message)
        self.decision = decision


def _enabled() -> bool:
    return str(os.environ.get(ENV_EGRESS_GUARD, "1")).strip().lower() \
        not in ("0", "false", "no", "off")


def _header_names(headers: Any) -> Tuple[str, ...]:
    if not headers:
        return ()
    if isinstance(headers, dict):
        return tuple(str(k) for k in headers)
    try:
        return tuple(str(k) for k, _ in headers)
    except Exception:  # noqa: BLE001
        return ()


class EgressGuard:
    """出域执行点（无状态；全部方法为类方法，便于调用点直接接线）"""

    # ── 判定 ──

    @classmethod
    def precheck(
        cls,
        *,
        method: str,
        url: str,
        params: Any = None,
        data: Any = None,
        json_data: Any = None,
        headers: Any = None,
        capability_id: str = "",
        data_class: str = "",
        risk_level: str = "",
        actor: str = "",
        tenant_id: str = "default",
        engine: Any = None,
    ) -> Optional[Any]:
        """在**发起网络动作之前**取得出域决策

        Returns:
            ``None`` ＝ 未启用（``CP_POLICY_EGRESS_GUARD=0``）或决策层出现内部错误
            ——两种情况都表示「本守卫没有意见，按既有网络策略继续」；否则返回
            :class:`~agent.policy.egress.EgressDecision`，调用方据 ``allowed``
            决定是否继续。

            注意：**决策层无覆盖时也会返回决策**（``allowed=True``、
            ``matched=False``、``reason_code=no_policy_match``）。这不矛盾——
            「问过策略层、它没意见」与「根本没问」是两件事，前者要能落审计。
            调用方只需看 ``allowed``。
        """
        if not _enabled():
            return None
        try:
            from agent.policy.egress import EgressRequest, decide_egress
            request = EgressRequest(
                url=str(url or ""), method=str(method or "GET"),
                payload={"params": params, "data": data, "json": json_data},
                header_names=_header_names(headers),
                capability_id=str(capability_id or ""),
                data_class=str(data_class or ""),
                risk_level=str(risk_level or ""),
                actor=str(actor or ""), tenant_id=str(tenant_id or "default"),
            )
            decision = decide_egress(request, engine=engine)
            cls._observe(decision, url=str(url or ""))
            return cls._apply_chain_monitor(
                decision, url=str(url or ""), method=str(method or "GET"),
                capability_id=str(capability_id or ""),
                data_class=str(data_class or ""),
                tenant_id=str(tenant_id or "default"))
        except Exception as exc:  # noqa: BLE001 决策层异常 ⇒ 放行（fail-open）
            logger.warning("出域策略判定失败（按放行处理，不阻断主流程）: %s: %s",
                           type(exc).__name__, exc)
            return None

    @classmethod
    def _apply_chain_monitor(cls, decision: Any, *, url: str, method: str,
                             capability_id: str, data_class: str,
                             tenant_id: str) -> Any:
        """叠加 §5.7 机制 4「出域链路监测」（**本次接线补齐**）

        【它补的是逐请求判定查不到的那一半】上游 decide_egress 判的是
        "**本次**请求的 payload / 目标是否合规"；而「读过密钥 → 之后用无关 payload
        外发」这种**跨请求**链路，逐请求判定天然看不见（拆包 / 编码 / 延时外发
        都能绕过）。agent/guardrails/egress_chain.py 正是为此而写
        （其模块头自述了与 S4-02 的分工），但此前**没有生产调用方**。

        【方向】只可能**更严**，不可能更松：原判定已拒绝时原样返回；只有原判定放行、
        而链路命中时才翻成拒绝。监测器异常 ⇒ 返回原判定（不因守卫故障而改变结论）。
        """
        try:
            from agent.guardrails.egress_chain import get_egress_chain_monitor
            monitor = get_egress_chain_monitor()
        except Exception as exc:  # noqa: BLE001  监测器不可用不得改变判定结果
            logger.warning("出域链路监测不可用（按原判定继续）: %s: %s",
                           type(exc).__name__, exc)
            return decision
        if monitor is None:
            return decision
        try:
            # enforce=False：命中由本函数转成"拒绝决策"而不是抛异常 ——
            # precheck 的契约是"返回决策或 None"，抛异常会绕过调用方的既有分支。
            verdict = monitor.evaluate(
                url=url, method=method, capability_id=capability_id,
                data_class=data_class, tenant_id=tenant_id, enforce=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("出域链路判定失败（按原判定继续）: %s: %s",
                           type(exc).__name__, exc)
            return decision
        if verdict is None or getattr(verdict, "allowed", True):
            return decision
        if decision is not None and not decision.allowed:
            return decision          # 已经拒绝了：保持原判定与原原因
        logger.warning("[EgressGuard] §5.7 机制 4 命中「读密钥→外发」链路: %s",
                       getattr(verdict, "reason", ""))
        if decision is None:
            return None              # 原判定为"无意见" ⇒ 不凭空造一个拒绝决策
        try:
            from dataclasses import replace
            evidence = dict(getattr(decision, "evidence", {}) or {})
            evidence["egress_chain"] = verdict.to_dict()
            reason = ("出域被拒绝：命中「读本地密钥 → 外发」链路（§5.7 机制 4）。"
                      "该链路意味着可能已经泄露，应按事故流程止损并轮换凭证。")
            return replace(decision, allowed=False, needs_human=True,
                           reason=reason, evidence=evidence)
        except Exception as exc:  # noqa: BLE001
            logger.error("出域链路命中但无法构造拒绝决策（保守放行原判定）: %s", exc)
            return decision

    @classmethod
    def enforce(cls, **kwargs: Any) -> None:
        """``precheck`` 的异常语义包装：拦截即抛 :class:`EgressBlocked`"""
        decision = cls.precheck(**kwargs)
        if decision is None or decision.allowed:
            return
        raise EgressBlocked(cls.blocked_error(decision), decision=decision)

    # ── 结果成形 ──

    @staticmethod
    def blocked_error(decision: Any) -> str:
        """拦截错误文案（**不含匹配值**；只出策略 id 与目标主机）"""
        evidence = getattr(decision, "evidence", {}) or {}
        host = str((evidence.get("target") or {}).get("host") or "")
        prefix = ASK_ERROR_PREFIX if getattr(decision, "needs_human", False) \
            else BLOCKED_ERROR_PREFIX
        detail = str(getattr(decision, "reason", "") or "")
        policy_id = str(getattr(decision, "policy_id", "") or "")
        bits = [prefix]
        if policy_id:
            bits.append(f"policy={policy_id}")
        if host:
            bits.append(f"host={host}")
        if detail:
            bits.append(detail)
        return "｜".join(bits)

    @classmethod
    def block_result(cls, decision: Any, url: str = "") -> Dict[str, Any]:
        """拦截结果（与 ``HttpClient._error_result`` 同形，调用方可直接返回）"""
        evidence = getattr(decision, "evidence", {}) or {}
        return {
            "ok": False,
            "status_code": None,
            "headers": {},
            "content": None,
            "text": None,
            "content_length": 0,
            "url": url,
            "elapsed": 0.0,
            "error": cls.blocked_error(decision),
            "blocked": True,
            "blocked_by": "policy.egress",
            "policy_id": str(getattr(decision, "policy_id", "") or ""),
            "effect": str(getattr(decision, "effect", "") or ""),
            "needs_human": bool(getattr(decision, "needs_human", False)),
            "data_class": str(evidence.get("data_class") or ""),
            "secret_kinds": list(evidence.get("secret_kinds") or []),
        }

    # ── 执行留痕 ──

    @classmethod
    def _observe(cls, decision: Any, *, url: str) -> None:
        """把**执行**（不只是决策）写进审计：``egress.blocked``

        决策侧已经写过 ``policy.decision``；这里再写一条 ``egress.blocked`` 是为了
        让「决策发生了但执行点没拦」这类事故可被查询——决策日志本身证明不了执行。
        与 S2 同纪律：best-effort、只写叶子、绝不阻断。
        """
        if decision is None or getattr(decision, "allowed", True):
            return
        try:
            from agent.audit.facade import audit
            from agent.policy.egress import classify_target
            target = classify_target(url)
            audit.record(
                "egress.blocked",
                actor="guardrails.egress_guard",
                subject=f"egress:{target.get('host') or url}",
                payload={
                    "policy_id": str(getattr(decision, "policy_id", "") or ""),
                    "effect": str(getattr(decision, "effect", "") or ""),
                    "host": str(target.get("host") or ""),
                    "scheme": str(target.get("scheme") or ""),
                    "data_class": str((getattr(decision, "evidence", {}) or {})
                                      .get("data_class") or ""),
                    "secret_kinds": list((getattr(decision, "evidence", {}) or {})
                                         .get("secret_kinds") or []),
                    "enforced": True,
                    "network_action_taken": False,
                },
                source="agent",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("出域拦截审计写入失败（不影响拦截本身）: %s", exc)


__all__ = [
    "ENV_EGRESS_GUARD", "BLOCKED_ERROR_PREFIX", "ASK_ERROR_PREFIX",
    "EgressBlocked", "EgressGuard",
]
