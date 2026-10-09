"""scoped 分身侧主动记忆操作的母体守门器（协议内声明式 + 母体执行 + 守域）

【任务定位（方案 B）】
    子代理是独立进程，执行咽喉只到通道调用（agent/subagent/executor.py），**没有
    工具执行循环**。因此分身侧的 memory.read / memory.write 不能"自己动手"，只能在
    输出载荷里**声明**意图；由母体（DelegationExecutor）在拿到通道输出后、写回私人
    记忆域**之前**，经本守门器代为执行 —— 而域标识始终取自母体持有的
    ScopedMemoryDomain.scope，子代理声明的 tenant/workspace/subject 一律丢弃。

【四道闸门（顺序固定）】
    1. from_context：仅当 memory_mode == scoped 且 domain 有域（has_domain）且
       toolset 已显式开启 scoped_memory 时才返回守门器；否则 None —— 默认档
       none/brokered 与"未开启 scoped"绝不触达底层（零调用）。
    2. 权限闸门：读写前先 toolset.allows(记忆类工具名)；越权 => E_TOOL_NOT_AUTHORIZED，
       **不触达底层**（授权口径唯一权威在 §7.0 矩阵 + 授权子集，本模块不复制）。
    3. 守域闸门：写构造 ScopedMemoryEntry 时用 domain.scope **强制覆盖**
       tenant/workspace/subject；entry.scope 之类显式域仍交 domain.check_domain
       判定（越域 => E_MEMORY_SCOPE_MISMATCH，不落库）。
    4. fail-soft：任何异常都收敛成 ok=False + error_code + degraded，**绝不抛**，
       绝不阻断委派主路径。

【读取侧域过滤（显式镜像；#1063 未合入时的防御冗余）】
    read(query) 复用 domain.read，随后再做一道**后置域过滤**。这道过滤**显式镜像**
    agent/memory/scoped_store.py 的 mem0 规则，存在的唯一原因是 PR #1063
    （fix/scoped-read-domain-filter）尚未合入 master：
      · mem0：逐条按 scope 比对 tenant/workspace/subject，域名不符或**缺 metadata**
        一律剔除（判不了归属就不读）；
      · 其它 provider（holographic）：只剔除"带 metadata 且与 scope 不符"的行，
        **绝不动**无 metadata 的行（holographic 的 recall 已由 tenancy 过滤）。
    #1063 合入后 domain.read 已过滤，本过滤是**幂等**的防御冗余（同一批行再滤一遍，
    结果与计数不变），**不得**再演化出与 scoped_store 不同的判定。

【审计】
    每次 read / write / 拒绝 / 降级都写一条 subagent.memory.scoped.gate（只含
    计数与错误码，**不含记忆正文**）；审计失败不阻断判定（fail-soft）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from agent.subagent.toolset import E_TOOL_NOT_AUTHORIZED

logger = logging.getLogger(__name__)

__all__ = [
    "AUDIT_SCOPED_GATE",
    "MODE_SCOPED",
    "READ_TOOL_NAMES",
    "WRITE_TOOL_NAMES",
    "ScopedMemoryGate",
]

#: 守门器审计事件（与 subagent.memory.scoped.* 同族）
AUDIT_SCOPED_GATE = "subagent.memory.scoped.gate"

#: 声明的记忆档位取值（与 memory_broker / executor 同词）
MODE_SCOPED = "scoped"

#: 读取类工具名（任一放行即可读）；与 §5.7 机制 3 的"记忆读写"类别同名同义。
READ_TOOL_NAMES: Tuple[str, ...] = (
    "memory.read", "memory.recall", "memory.search", "memory.query",
    "memory.list", "search_memory")

#: 写入类工具名（任一放行即可写）
WRITE_TOOL_NAMES: Tuple[str, ...] = (
    "memory.write", "remember", "memory.update", "memory.promote",
    "memory.forget", "memory.delete")


def _memory_error_codes() -> Tuple[str, str]:
    """记忆域错误码（惰性取权威；不可用 => 同值兜底，绝不因依赖缺失而放宽）"""
    try:
        from agent.memory.scoped_store import (
            E_MEMORY_DEGRADED,
            E_MEMORY_SCOPE_MISMATCH,
        )
        return E_MEMORY_DEGRADED, E_MEMORY_SCOPE_MISMATCH
    except Exception:  # noqa: BLE001
        return "E_MEMORY_DEGRADED", "E_MEMORY_SCOPE_MISMATCH"


def _row_metadata(row: Any) -> Mapping[str, Any]:
    """取后端条目的 metadata（MemoryResult.metadata 或 Mapping["metadata"]）

    与 scoped_store._row_metadata 同口径（**显式镜像**）。取不到一律空表 =>
    调用方按"域名不可判"处理。
    """
    meta = getattr(row, "metadata", None)
    if not isinstance(meta, Mapping) and isinstance(row, Mapping):
        meta = row.get("metadata")
    return meta if isinstance(meta, Mapping) else {}


@dataclass
class ScopedMemoryGate:
    """scoped 分身的记忆操作守门器（子代理声明、母体执行、母体守域）"""

    domain: Any
    toolset: Any
    audit: Any = None
    actor: str = "sub_agent"
    subject: str = ""

    # ── 构造 ──

    @classmethod
    def from_context(cls, meta: Any, *, domain: Any, toolset: Any,
                     audit: Any = None) -> Optional["ScopedMemoryGate"]:
        """从上下文元数据构造守门器；不满足条件一律 None（绝不触达底层）

        返回 None 当且仅当：
          · meta 不是 Mapping / memory_mode != scoped；
          · domain 为 None；
          · domain.scope.has_domain 为假（空域：谁的记忆都判不了）；
          · toolset 为 None 或未显式开启 scoped_memory。
        """
        try:
            if not isinstance(meta, Mapping):
                return None
            if str(meta.get("memory_mode", "") or "").strip().lower() != MODE_SCOPED:
                return None
            if domain is None:
                return None
            scope = getattr(domain, "scope", None)
            if scope is None or not bool(getattr(scope, "has_domain", False)):
                return None
            if toolset is None or not bool(getattr(toolset, "scoped_memory", False)):
                return None
        except Exception as e:  # noqa: BLE001 判定本身异常 => 不启用（fail-closed）
            logger.warning("[ScopedGate] from_context 判定异常（不启用守门器）: %s", e)
            return None
        subject = "scoped:%s" % (str(getattr(scope, "subject_id", "") or "") or "-")
        return cls(domain=domain, toolset=toolset, audit=audit, subject=subject)

    # ── 权限 ──

    def _authorize(self, names: Sequence[str]) -> Dict[str, Any]:
        """按工具名逐个问 toolset；任一放行即可（口径唯一权威在 toolset/矩阵）"""
        for name in names:
            try:
                if self.toolset.allows(name):
                    return {"allowed": True, "tool": name}
            except Exception as e:  # noqa: BLE001
                logger.debug("[ScopedGate] toolset.allows(%s) 异常: %s", name, e)
        return {"allowed": False, "tool": str(names[0] if names else "")}

    # ── 读 ──

    def read(self, query: str = "") -> Dict[str, Any]:
        """执行一次声明的 scoped 读取（fail-soft；结果不含正文）"""
        degraded_code, _ = _memory_error_codes()
        result: Dict[str, Any] = {
            "op": "read", "ok": False, "error_code": "", "degraded": "",
            "count": 0, "dropped": 0, "query_chars": len(str(query or "")),
        }
        try:
            decision = self._authorize(READ_TOOL_NAMES)
            if not decision["allowed"]:
                result.update(error_code=E_TOOL_NOT_AUTHORIZED,
                              degraded="tool_not_authorized",
                              tool=str(decision.get("tool", "")))
                result["recorded"] = self._emit(result, status="rejected")
                return result
            outcome = self.domain.read(str(query or ""))
            entries = tuple(getattr(outcome, "entries", ()) or ())
            kept, dropped = self._filter_rows(entries)
            result["ok"] = bool(getattr(outcome, "ok", False))
            result["count"] = len(kept)
            result["dropped"] = int(dropped)
            result["error_code"] = str(getattr(outcome, "error_code", "") or "")
            result["degraded"] = str(getattr(outcome, "degraded", "") or "")
            if dropped and not result["degraded"]:
                result["degraded"] = "scope_filtered"
            result["recorded"] = self._emit(
                result, status="read" if result["ok"] else "degraded")
            return result
        except Exception as e:  # noqa: BLE001 fail-soft：绝不抛
            logger.warning("[ScopedGate] read 异常（降级，不阻断委派）: %s", e)
            result.update(ok=False, error_code=degraded_code,
                          degraded="gate_read_failed:%s" % type(e).__name__)
            result["recorded"] = self._emit(result, status="degraded")
            return result

    def _filter_rows(self, rows: Sequence[Any]) -> Tuple[List[Any], int]:
        """读取侧后置域过滤（**显式镜像** scoped_store 的 mem0 规则；幂等冗余）

        见模块头。mem0 缺 metadata 一律剔除；其它 provider 的无 metadata 行原样保留。
        """
        provider = str(getattr(self.domain, "provider", "") or "")
        scope = getattr(self.domain, "scope", None)
        kept: List[Any] = []
        dropped = 0
        for row in rows or ():
            meta = _row_metadata(row)
            if not meta:
                if provider == "mem0":
                    dropped += 1
                else:
                    kept.append(row)
                continue
            if self._in_scope(meta, scope):
                kept.append(row)
            else:
                dropped += 1
        return kept, dropped

    @staticmethod
    def _in_scope(meta: Mapping[str, Any], scope: Any) -> bool:
        """逐维比对（只比 scope 里非空的维度；与 scoped_store 同口径）"""
        for dim in ("tenant_id", "workspace_id", "subject_id"):
            want = str(getattr(scope, dim, "") or "")
            if not want:
                continue
            if str(meta.get(dim) or "") != want:
                return False
        return True

    # ── 写 ──

    def write_declared(self, specs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        """执行声明的 scoped 写入（fail-soft；聚合结果不含正文）"""
        degraded_code, _ = _memory_error_codes()
        items: List[Dict[str, Any]] = []
        result: Dict[str, Any] = {
            "op": "write", "ok": False, "error_code": "", "degraded": "",
            "attempted": False, "written": 0, "rejected": 0, "bytes": 0,
            "items": items,
        }
        try:
            decision = self._authorize(WRITE_TOOL_NAMES)
            if not decision["allowed"]:
                result.update(error_code=E_TOOL_NOT_AUTHORIZED,
                              degraded="tool_not_authorized",
                              tool=str(decision.get("tool", "")))
                result["recorded"] = self._emit(result, status="rejected")
                return result
            result["attempted"] = True
            for spec in specs or ():
                items.append(self._write_one(spec))
            written = sum(1 for item in items if item.get("ok"))
            result["written"] = written
            result["rejected"] = len(items) - written
            result["bytes"] = sum(int(item.get("bytes", 0) or 0) for item in items)
            result["ok"] = bool(items) and written == len(items)
            result["recorded"] = self._emit(
                result, status="written" if result["ok"] else "degraded")
            return result
        except Exception as e:  # noqa: BLE001 fail-soft
            logger.warning("[ScopedGate] write_declared 异常（降级，不阻断委派）: %s", e)
            result.update(ok=False, error_code=degraded_code,
                          degraded="gate_write_failed:%s" % type(e).__name__)
            result["recorded"] = self._emit(result, status="degraded")
            return result

    def _write_one(self, spec: Any) -> Dict[str, Any]:
        """写一条声明（域标识一律取母体 scope；显式 scope 交 domain.check_domain）"""
        degraded_code, _ = _memory_error_codes()
        item: Dict[str, Any] = {"ok": False, "error_code": "", "degraded": "",
                                "bytes": 0, "entries": 0}
        try:
            from agent.memory.scoped_store import ScopedMemoryEntry
        except Exception as e:  # noqa: BLE001 依赖缺失 => 如实降级
            item.update(error_code=degraded_code,
                        degraded="import_failed:%s" % type(e).__name__)
            return item
        data: Mapping[str, Any] = spec if isinstance(spec, Mapping) else {}
        scope = getattr(self.domain, "scope", None)
        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        try:
            entry = ScopedMemoryEntry(
                content=str(data.get("content") or data.get("text") or ""),
                memory_type=str(data.get("memory_type") or data.get("type") or "fact"),
                # 【守域强制覆盖】域标识只认母体 scope，声明里的同名字段一律忽略
                tenant_id=str(getattr(scope, "tenant_id", "") or ""),
                workspace_id=str(getattr(scope, "workspace_id", "") or ""),
                subject_id=str(getattr(scope, "subject_id", "") or ""),
                scope=str(data.get("scope") or "").strip(),
                key=str(data.get("key") or "").strip(),
                confidence=confidence,
                extra={"source": "subagent_declared"},
            )
            outcome = self.domain.write(entry)
        except Exception as e:  # noqa: BLE001 fail-soft
            item.update(error_code=degraded_code,
                        degraded="write_failed:%s" % type(e).__name__)
            return item
        item["ok"] = bool(getattr(outcome, "ok", False))
        item["error_code"] = str(getattr(outcome, "error_code", "") or "")
        item["degraded"] = str(getattr(outcome, "degraded", "") or "")
        item["bytes"] = int(getattr(outcome, "bytes", 0) or 0)
        item["entries"] = int(getattr(outcome, "entries", 0) or 0)
        return item

    # ── 审计 ──

    def _emit(self, result: Mapping[str, Any], *, status: str) -> bool:
        """写一条守门器审计（fail-soft；payload 只含计数与错误码，不含正文）"""
        try:
            from agent.memory.quota import emit_scoped_audit
        except Exception:  # noqa: BLE001 审计依赖不可用 => 不写（不影响判定）
            return False
        payload = {
            "op": str(result.get("op", "") or ""),
            "ok": bool(result.get("ok")),
            "error_code": str(result.get("error_code", "") or ""),
            "degraded": str(result.get("degraded", "") or ""),
            "count": int(result.get("count", 0) or 0),
            "dropped": int(result.get("dropped", 0) or 0),
            "written": int(result.get("written", 0) or 0),
            "rejected": int(result.get("rejected", 0) or 0),
        }
        return emit_scoped_audit(
            self.audit, AUDIT_SCOPED_GATE, actor=self.actor,
            subject=self.subject, payload=payload, status=status)
