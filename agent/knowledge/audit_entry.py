"""知识库审计的**唯一实现**（CI 面与 Agent 面共用）—— TASK-05 §3 第 0 步第 4 项

## 要解决的问题（`TASK-05` §2.3b 的"现实缺口 #1"）

同一能力**有两个入口**，只有 Agent 面受治理：

```
CI 面（.github/workflows/ci.yml 的 knowledge-audit-smoke job）
    python -m agent.knowledge audit … → agent/knowledge/__main__.py::cmd_audit
                                       → run_knowledge_audit(...)      ← 直调，不过闸门
Agent 面（agent/knowledge/tools.py::kb_lint，注册在 _registry）
    → WorkflowRunner.run_audit()                                       ← 另一份实现
```

⇒ **两个入口、两份实现、零共享审计**。CI 面既不产生结构化审计记录，
也不与 Agent 面共享判定口径 —— 它是纯粹的治理盲区。

## 处置（任务书第 4 项的原文要求）

> **保留两个入口**（CI 用 CLI、Agent 用 tool）是合理的，**但必须让它们共用
> 同一实现、同一审计、同一身份语义**。抽公共实现，两个入口只做参数适配。
> CI 面必须至少产生**结构化审计记录**（"以 CI 身份执行了 knowledge_audit"），
> 否则它就是治理盲区。

## 本模块的设计取舍

- **不合并两个入口**：CLI 入口是设计意图（CI 不该依赖"模型可见集"，也不该被
  给交互式高危动作设计的审批边界拦住）；Agent 入口有参数契约与模型可见性。
  合并它们会破坏其中一侧的合理性。
- **合并"实现"**：两个入口都落到 `run_knowledge_audit_entry()`。
  底层仍复用既有的 `agent/knowledge/audit_job.py::run_knowledge_audit()`
  （md/html 报告 + log.md 登记）与 `agent/knowledge/lint.py::lint_all()`
  —— **不重写检测逻辑**，只统一入口形态、返回结构与审计。
- **审计记录**（无论成败都写）：`data/audit/knowledge_audit.jsonl`。
  只写**叶子字段**（channel/actor/参数摘要/结果摘要/耗时），
  **不写**报告正文（那是 `data/knowledge/reports/` 的职责，D6：不重复落盘大产物）。
- **不抛异常**：与 `agent/knowledge/tools.py` 的既有纪律一致
  （"所有工具对 LLM 不可用降级，不抛异常"），失败一律返回 `{"ok": False, ...}`。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

__all__ = ["run_knowledge_audit_entry", "AUDIT_LOG_REL", "audit_log_path"]

#: 结构化审计落盘位置（append-only JSONL；与既有的审批/审计台账同族）
AUDIT_LOG_REL = os.path.join("data", "audit", "knowledge_audit.jsonl")

#: 参数里可能很长的字段摘要上限（审计记录不放正文）
_SUMMARY_MAX = 200


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def audit_log_path() -> str:
    """审计台账路径（`CP_KNOWLEDGE_AUDIT_LOG` 可覆写；缺省在建仓根下）"""
    override = os.environ.get("CP_KNOWLEDGE_AUDIT_LOG", "")
    if override and str(override).strip():
        return str(override).strip()
    return os.path.join(_repo_root(), AUDIT_LOG_REL)


def _write_audit_record(record: Dict[str, Any]) -> None:
    """追加一条结构化审计记录（**失败只告警，绝不影响审计本身的结果**）

    【为什么 append-only 且不轮转】与 `data/tool_approval_uses.jsonl` 同款：
    它是"这件事发生过"的证据链，删改它会销毁证据（D6 也禁止碰 `data/` 下的台账）。
    """
    path = audit_log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001  审计写失败不得让审计动作失败
        logger.warning("[knowledge.audit_entry] 结构化审计记录写入失败: %s: %s",
                       type(exc).__name__, exc)


def run_knowledge_audit_entry(
    wiki_root: Optional[str | Path] = None, *,
    index_path: Optional[str | Path] = None,
    reports_dir: Optional[str | Path] = None,
    now: Optional[date] = None,
    persist_reports: bool = True,
    actor: str = "unknown",
    channel: str = "unknown",
    source: str = "",
    report_sink: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """知识库健康巡检的**统一入口**（CI 面与 Agent 面都调它）

    Args:
        wiki_root: wiki 根目录；`None` 时走各调用方的默认布局
        index_path / reports_dir: 传给底层实现的路径覆盖
        now: "今天"的注入点（跨进程把父进程时钟口径传下去，消除 delta 天分叉）
        persist_reports: 是否落盘 md/html 报告并登记 `log.md`
            - CI 面 `True`（CI 就是要拿到报告产物）
            - Agent 面 `False`（`kb_lint` 的既有契约只返回报告 dict，
              **不改**它的行为，见 D2）
        actor: 执行者身份（`ci` / `llm` / `human` / 服务账号名）
        channel: 入口形态（`cli` / `agent_tool`）
        source: 自由文本来源（如 CI job 名），便于溯源
        report_sink: **可选出参**。传入非空 dict 时，把底层的 `HealthReport`
            **对象**放进去（键 `"report"`）。CLI 需要对象来发邮件 / 走
            `report_to_json`，而返回值必须保持 JSON 友好 —— 故用显式出参而不是
            往返回值里塞活对象（否则"统一结果 dict"就不再是可序列化契约了）。

    Returns:
        统一结果 dict（**键集固定**，两个入口返回同一形状）：
        `ok / health_score / total_cards / broken_links / orphans / index_drift
         / stale_cards / unresolved_conflicts / score_breakdown / audited_at
         / suggestions / persisted / actor / channel / duration_ms / error?`
    """
    started = time.perf_counter()
    result: Dict[str, Any] = {
        "ok": False, "health_score": None, "total_cards": 0,
        "broken_links": [], "orphans": [], "index_drift": [], "stale_cards": [],
        "unresolved_conflicts": [], "score_breakdown": {}, "audited_at": "",
        "suggestions": [], "persisted": bool(persist_reports),
        "actor": str(actor), "channel": str(channel), "duration_ms": 0.0,
        "wiki_root": str(wiki_root or ""),
    }
    try:
        if persist_reports:
            # 落盘路径：复用既有实现（md + html + log.md 登记）
            from agent.knowledge.audit_job import run_knowledge_audit  # noqa: PLC0415
            report = run_knowledge_audit(
                wiki_root, index_path=index_path, reports_dir=reports_dir, now=now)
            if report_sink is not None:
                report_sink["report"] = report
            result.update(_report_to_dict(report))
        else:
            # 不落盘路径：只做检测 + 打分（**不重复实现**：直接复用 lint_all/score_breakdown）
            from agent.knowledge.audit_job import _get_store  # noqa: PLC0415
            from agent.knowledge.lint import lint_all, score_breakdown  # noqa: PLC0415
            store = _get_store(wiki_root)
            hr = lint_all(store, index_path=index_path or store._index_path)  # noqa: SLF001
            if report_sink is not None:
                report_sink["report"] = hr
            result.update(_hr_to_dict(hr, score_breakdown(hr)))
        result["ok"] = _is_ok(result)
    except Exception as exc:  # noqa: BLE001  **不抛**：与既有 kb_* 工具同纪律
        logger.error("[knowledge.audit_entry] 巡检失败: %s", exc, exc_info=True)
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["duration_ms"] = round((time.perf_counter() - started) * 1000.0, 3)

    _write_audit_record({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "capability": "knowledge_audit",
        "actor": str(actor),
        "channel": str(channel),
        "source": str(source)[:_SUMMARY_MAX],
        "wiki_root": str(wiki_root or "")[:_SUMMARY_MAX],
        "persist_reports": bool(persist_reports),
        "ok": bool(result["ok"]),
        "health_score": result["health_score"],
        "counts": {
            "broken_links": len(result["broken_links"] or []),
            "orphans": len(result["orphans"] or []),
            "index_drift": len(result["index_drift"] or []),
            "stale_cards": len(result["stale_cards"] or []),
            "unresolved_conflicts": len(result["unresolved_conflicts"] or []),
        },
        "duration_ms": result["duration_ms"],
        "error": str(result.get("error") or "")[:_SUMMARY_MAX],
    })
    return result


def _report_to_dict(report: Any) -> Dict[str, Any]:
    """`HealthReport` → 统一结果 dict（只取叶子字段，不搬运对象）"""
    return {
        "health_score": getattr(report, "health_score", None),
        "total_cards": getattr(report, "total_cards", 0),
        "broken_links": list(getattr(report, "broken_links", []) or []),
        "orphans": list(getattr(report, "orphans", []) or []),
        "index_drift": list(getattr(report, "index_drift", []) or []),
        "stale_cards": list(getattr(report, "stale_cards", []) or []),
        "unresolved_conflicts": list(getattr(report, "unresolved_conflicts", []) or []),
        "score_breakdown": dict(getattr(report, "score_breakdown", {}) or {}),
        "audited_at": str(getattr(report, "checked_at", "") or ""),
        "suggestions": list(getattr(report, "suggestions", []) or []),
    }


def _hr_to_dict(hr: Any, breakdown: Any) -> Dict[str, Any]:
    """`HealthReport`（lint 层产物）→ 统一结果 dict"""
    return {
        "health_score": getattr(hr, "health_score", None),
        "total_cards": getattr(hr, "total_cards", 0),
        "broken_links": list(getattr(hr, "broken_links", []) or []),
        "orphans": list(getattr(hr, "orphans", []) or []),
        "index_drift": list(getattr(hr, "index_drift", []) or []),
        "stale_cards": list(getattr(hr, "stale_cards", []) or []),
        "unresolved_conflicts": list(getattr(hr, "unresolved_conflicts", []) or []),
        "score_breakdown": dict(breakdown or {}),
        "audited_at": str(getattr(hr, "checked_at", "") or ""),
        "suggestions": list(getattr(hr, "suggestions", []) or []),
    }


def _is_ok(result: Dict[str, Any]) -> bool:
    """健康判定：五类问题全空 ⇒ ok（与 `WorkflowRunner.run_audit` 的口径逐字一致）"""
    if result.get("error"):
        return False
    return not (result.get("orphans") or result.get("broken_links")
                or result.get("index_drift") or result.get("stale_cards")
                or result.get("unresolved_conflicts"))
