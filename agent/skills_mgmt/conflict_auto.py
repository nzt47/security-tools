"""技能冲突 / 无增量技能的自动处置（2026-10-30，Owner 要求「自动替判断处理」）。

此前评估报告只会抛两句提示，处置全靠人工：
  · 「存在交互抢占冲突风险」  ← assessor finding code INT_SHARED_TRIGGER
  · 「原生未覆盖的增量内容仍会保留…若确无增量建议停用或删除」
                              ← absorbed / native-overlap 标签 + OVL_OPERATION_OVERLAP

本模块把它们变成**可预览、可回滚的处置计划**：
  1. 规则优先（零 token）：只对「近 7 天 0 命中/0 加载/0 执行」且「评分明显偏低」的技能
     建议停用；对**在用**的技能一律 keep（不拿用户正在用的东西冒险）；
  2. 判据不足时**不动手**，标 escalate（留给人工），或交给可选 judge（LLM）——
     由调用方注入，默认不注入 ⇒ 零 token；
  3. apply 只做「停用 + 打 auto_dormant 标签」，**从不删除**；每次动作写审计 + 决策台账
     （data/skill_conflict_decisions.jsonl），undo 可一键恢复。

为什么不停用「在用」技能：命中台账是唯一的真实使用证据；没有使用 + 分数低，才是可以被
自动收起的候选。删除永远留给人工（破坏性且不可从台账恢复）。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent.logging_utils import log_dict

logger = __import__("logging").getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
DECISIONS_PATH = _ROOT / "data" / "skill_conflict_decisions.jsonl"

#: 触发「交互抢占冲突」/「操作重叠」的评估 finding code
CONFLICT_CODES = frozenset({"INT_SHARED_TRIGGER", "OVL_OPERATION_OVERLAP"})

#: 低于这个评分才建议「自动收起」（0-100）
AUTO_DISABLE_SCORE = 85.0

#: 近 7 天有任何命中/加载/执行 ⇒ 视为在用，永不自动停用
ACTIVE_ANY_USAGE = 1


def _usage_of(stats: Dict[str, Any], skill_id: str) -> Dict[str, int]:
    row = (stats.get("skills") or {}).get(skill_id) or {}
    return {
        "hits": int(row.get("hits") or 0),
        "loads": int(row.get("loads") or 0),
        "execs": int(row.get("execs") or 0),
    }


def _conflict_findings(skill: Any) -> List[Dict[str, Any]]:
    rv = getattr(skill, "review", None)
    findings = getattr(rv, "findings", None) or []
    out = []
    for f in findings:
        code = str(getattr(f, "code", "") or (f.get("code") if isinstance(f, dict) else ""))
        if code in CONFLICT_CODES:
            out.append({
                "code": code,
                "severity": str(getattr(f, "severity", "") or (f.get("severity") if isinstance(f, dict) else "")),
                "message": str(getattr(f, "message", "") or (f.get("message") if isinstance(f, dict) else "")),
            })
    return out


def plan(service: Any, *, usage_days: int = 7,
         judge: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
         max_judge_calls: int = 6) -> Dict[str, Any]:
    """产出一份处置计划（只读，不写任何状态）。

    Returns: {"items": [...], "summary": {...}, "judge_calls": n}
    每项 item: {skill_id, name, kind, action, score, usage, reason, evidence, code}
      action ∈ disable | keep | escalate
    """
    from agent.skills_mgmt import usage_log

    try:
        stats = usage_log.stats(usage_days)
    except Exception:  # noqa: BLE001 台账不可读时按「无使用证据」处理，但只允许 escalate
        stats = {}
    items: List[Dict[str, Any]] = []
    judge_calls = 0
    seen = set()
    for skill in service.list_all():
        if not getattr(skill, "enabled", False):
            continue
        findings = _conflict_findings(skill)
        if not findings:
            continue
        sid = str(skill.id)
        if sid in seen:
            continue
        seen.add(sid)
        rv = getattr(skill, "review", None)
        score = float(getattr(rv, "score", 0) or 0)
        usage = _usage_of(stats, sid)
        active = any(usage[k] >= ACTIVE_ANY_USAGE for k in ("hits", "loads", "execs"))
        kind = "trigger_collision" if any(f["code"] == "INT_SHARED_TRIGGER" for f in findings) \
            else "native_overlap"
        base = {
            "skill_id": sid,
            "name": str(getattr(skill, "name", "") or sid),
            "kind": kind,
            "score": round(score, 1),
            "usage": usage,
            "code": findings[0]["code"],
            "evidence": findings[0]["message"][:200],
        }
        if active:
            base.update(action="keep", reason="近 %d 天有使用记录（在用技能不自动停用）" % usage_days)
            items.append(base)
            continue
        if score < AUTO_DISABLE_SCORE:
            base.update(action="disable",
                        reason="近 %d 天 0 命中/0 加载/0 执行，且综合评分 %.1f < %.0f"
                        % (usage_days, score, AUTO_DISABLE_SCORE))
            items.append(base)
            continue
        # 高分 + 无使用 ⇒ 规则判不了：可选 LLM 裁决，否则交人工
        verdict = None
        if judge is not None and judge_calls < max_judge_calls:
            judge_calls += 1
            try:
                verdict = judge(base)
            except Exception as e:  # noqa: BLE001 judge 失败不影响计划
                logger.warning("[conflict_auto] judge 失败（按 escalate 处理）: %s", e)
                verdict = None
        if verdict in ("disable", "keep"):
            base.update(action=verdict,
                        reason="LLM 裁决：%s（评分 %.1f、近 %d 天无使用）" % (verdict, score, usage_days))
        else:
            base.update(action="escalate",
                        reason="高分（%.1f）且无使用记录，规则判不了：需人工确认是否停用" % score)
        items.append(base)
    disabled = [i for i in items if i["action"] == "disable"]
    return {
        "items": items,
        "judge_calls": judge_calls,
        "summary": {
            "scanned": len(seen),
            "disable": len(disabled),
            "keep": sum(1 for i in items if i["action"] == "keep"),
            "escalate": sum(1 for i in items if i["action"] == "escalate"),
            "usage_days": usage_days,
        },
    }


def apply(service: Any, items: List[Dict[str, Any]], *, actor: str = "auto_conflict",
          confirm: bool = False) -> Dict[str, Any]:
    """执行计划里 action=disable 的项（只停用 + 打标签，不删除）。"""
    if not confirm:
        raise ValueError("apply 需要 confirm=True（先看计划再执行）")
    done: List[Dict[str, Any]] = []
    for it in items:
        if it.get("action") != "disable":
            continue
        sid = str(it.get("skill_id") or "")
        if not sid:
            continue
        skill = service.get(sid)
        before = {"enabled": bool(skill.enabled), "tags": list(skill.tags or [])}
        if not skill.enabled:
            continue
        service.set_enabled(sid, False)
        try:
            tags = list(skill.tags or [])
            if "auto_dormant" not in tags:
                tags.append("auto_dormant")
                service.update(sid, {"tags": tags})
        except Exception as e:  # noqa: BLE001 标签失败不影响停用动作
            logger.warning("[conflict_auto] 打 auto_dormant 标签失败 %s: %s", sid, e)
        decision = {
            "decision_id": uuid.uuid4().hex[:12],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "skill_id": sid,
            "kind": it.get("kind"),
            "action": "disable",
            "reason": it.get("reason"),
            "evidence": it.get("evidence"),
            "before": before,
            "actor": actor,
        }
        _append(decision)
        _audit(sid, decision, status="ok")
        done.append(decision)
    return {"ok": True, "applied": done, "count": len(done)}


def undo(service: Any, decision_id: str, *, confirm: bool = False) -> Dict[str, Any]:
    """撤销一次自动停用（恢复 enabled 与标签）。"""
    if not confirm:
        raise ValueError("undo 需要 confirm=True")
    rec = None
    for row in read_decisions(limit=2000):
        if row.get("decision_id") == decision_id:
            rec = row
            break
    if rec is None:
        raise KeyError("找不到 decision_id=%s" % decision_id)
    sid = str(rec["skill_id"])
    service.set_enabled(sid, bool((rec.get("before") or {}).get("enabled", True)))
    try:
        service.update(sid, {"tags": list((rec.get("before") or {}).get("tags") or [])})
    except Exception as e:  # noqa: BLE001
        logger.warning("[conflict_auto] 恢复标签失败 %s: %s", sid, e)
    _audit(sid, rec, status="undone", action="skill.conflict.auto_undo")
    return {"ok": True, "skill_id": sid, "restored_enabled": bool((rec.get("before") or {}).get("enabled", True))}


def read_decisions(limit: int = 100) -> List[Dict[str, Any]]:
    try:
        if not DECISIONS_PATH.exists():
            return []
        rows = []
        with DECISIONS_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        return rows[-max(1, limit):][::-1]
    except OSError:
        return []


def _append(rec: Dict[str, Any]) -> None:
    try:
        DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DECISIONS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("[conflict_auto] 决策台账写入失败: %s", e)


def _audit(skill_id: str, rec: Dict[str, Any], *, status: str,
           action: str = "skill.conflict.auto_disable") -> None:
    try:
        from agent.audit import audit as _audit_facade
        _audit_facade.record(action, subject="skill:" + skill_id,
                             payload={"decision_id": rec.get("decision_id"),
                                      "kind": rec.get("kind"),
                                      "reason": rec.get("reason"),
                                      "before": rec.get("before")},
                             source="agent", status=status)
    except Exception as e:  # noqa: BLE001 审计失败不回滚动作
        logger.warning("[conflict_auto] 审计写入失败 %s: %s", skill_id, e)


PLAN_PATH = _ROOT / "data" / "skill_conflict_plan.json"
TASK_NAME = "技能冲突自动处置"


def _service() -> Any:
    from agent.skills_mgmt.service import SkillsMgmtService
    return SkillsMgmtService()


def write_plan(service: Any = None) -> Dict[str, Any]:
    """产一份计划并落盘（供界面直接读，不用每次重算）。"""
    svc = service if service is not None else _service()
    out = plan(svc)
    out["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
        PLAN_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        logger.warning("[conflict_auto] 计划落盘失败: %s", e)
    return out


def register_scheduler(interval_hours: float = 24.0) -> Dict[str, Any]:
    """注册每日「技能冲突自动处置」任务。

    **只产计划、不动手**：任务把建议写进 data/skill_conflict_plan.json，界面读它展示；
    真正的停用必须由人在界面上确认（破坏性动作不交给定时任务独自做）。
    """
    from agent.task_scheduler import get_scheduler

    sched = get_scheduler()

    def _tick() -> None:
        try:
            p = write_plan()
            logger.info("[conflict_auto] 计划已生成 disable=%d escalate=%d",
                        p["summary"]["disable"], p["summary"]["escalate"])
        except Exception as e:  # noqa: BLE001 失败只记日志，不中断调度
            logger.warning("[conflict_auto] 计划生成失败: %s", e)

    sched.add_interval_task(
        TASK_NAME, func=_tick, interval_seconds=int(max(1.0, interval_hours) * 3600),
        zero_token=True, owner="agent/skills_mgmt/conflict_auto.py",
        zero_token_note="规则判据产计划（不调 LLM）；本任务只写计划文件，停用动作仍需界面确认",
    )
    return {"task": TASK_NAME, "registered": True, "interval_hours": interval_hours,
            "mode": "plan_only"}
