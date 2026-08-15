"""反馈建议自动执行体（TASK-05 Step 1）

背景（Why）:
    agent/feedback.py 的 get_skill_feedback_summary() 已能按满意度/评分给出
    recommended_action（no_data / promote_to_published / consider_deprecate_or_merge
    / improve_params / keep），但这些建议此前只停留在 API 返回值，没有自动执行体。
    本模块把建议接到既有执行 API（service.publish / merge_duplicate_skills /
    optimize_params / 状态迁移），默认 dry-run（零副作用），动作前打版本快照
    可回滚，动作写 JSONL 审计日志，并经 task_scheduler 定时触发（与 TASK-04
    precipitate 同一调度收口，避免双调度器）。

【不易】约束（禁止触碰）:
    - 不修改 feedback.py 的 recommended_action 生成逻辑与 SQLite schema
    - 不修改 SkillStore.merge_skills / SkillEnhancer.bump_version/rollback_version
      / 状态机转换表 — 本模块全部走既有 API
    - 所有自动动作默认 dry_run=true；正式执行写审计日志并受总开关控制
    - DEPRECATED 仅是状态迁移（models.py 状态机），绝不物理删除文件

开关/参数（优先级: 环境变量 > config.yaml > 硬编码默认值）:
    LEARNING_FEEDBACK_AGENT_ENABLED        / learning.feedback_agent.enabled        (默认 false)
    LEARNING_FEEDBACK_AGENT_INTERVAL_HOURS / learning.feedback_agent.interval_hours (默认 24)
    LEARNING_FEEDBACK_AGENT_DRY_RUN        / learning.feedback_agent.dry_run        (默认 true)
    LEARNING_FEEDBACK_AGENT_AUDIT_FILE     / learning.feedback_agent.audit_file     (默认 data/feedback_agent_audit.jsonl)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TASK_NAME = "反馈建议执行"
DEFAULT_INTERVAL_HOURS = 24
DEFAULT_AUDIT_FILE = "data/feedback_agent_audit.jsonl"
DEFAULT_DRY_RUN = True
_MIN_MERGE_JACCARD = 0.7
# 环境变量覆盖 config.yaml 的前缀
_ENV_PREFIX = "LEARNING_FEEDBACK_AGENT"

# 建议类型（与 feedback.py get_skill_feedback_summary 的 recommended_action 对齐）
ACTION_PROMOTE = "promote_to_published"
ACTION_DEPRECATE_MERGE = "consider_deprecate_or_merge"
ACTION_IMPROVE = "improve_params"
ACTION_KEEP = "keep"
ACTION_NO_DATA = "no_data"
_EXECUTABLE_ACTIONS = (ACTION_PROMOTE, ACTION_DEPRECATE_MERGE, ACTION_IMPROVE)


def _config_yaml() -> Optional[Dict[str, Any]]:
    """读取仓库根 config.yaml（失败返回 None，不抛异常）。"""
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
    if not cfg_path.exists():
        return None
    import yaml as _yaml

    with open(cfg_path, "r", encoding="utf-8") as f:
        return _yaml.safe_load(f) or {}


def _enabled() -> bool:
    """优先级: 环境变量 > config.yaml learning.feedback_agent.enabled > 默认 false。"""
    env = os.environ.get(f"{_ENV_PREFIX}_ENABLED")
    if env is not None and env.strip():
        return env.strip().lower() in ("true", "1", "yes")
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("feedback_agent", {})
                   or {}).get("enabled")
            if val is not None:
                return str(val).strip().lower() in ("true", "1", "yes")
    except Exception as e:  # noqa: BLE001 配置解析失败回退默认
        logger.debug("[FeedbackAgent] config.yaml 读取失败: %s", e)
    return False


def _interval_hours() -> int:
    """优先级: 环境变量 > config.yaml learning.feedback_agent.interval_hours > 默认 24。"""
    env = os.environ.get(f"{_ENV_PREFIX}_INTERVAL_HOURS")
    if env is not None and env.strip():
        try:
            return max(1, int(env.strip()))
        except ValueError:
            logger.warning("[FeedbackAgent] 非法 interval_hours=%r，使用默认 %d",
                           env, DEFAULT_INTERVAL_HOURS)
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("feedback_agent", {})
                   or {}).get("interval_hours")
            if val is not None:
                try:
                    return max(1, int(val))
                except (TypeError, ValueError):
                    pass
    except Exception as e:  # noqa: BLE001
        logger.debug("[FeedbackAgent] config.yaml 读取失败: %s", e)
    return DEFAULT_INTERVAL_HOURS


def _dry_run() -> bool:
    """dry-run 默认 true（不可变约束：默认不产生写操作）。"""
    env = os.environ.get(f"{_ENV_PREFIX}_DRY_RUN")
    if env is not None and env.strip():
        return env.strip().lower() in ("true", "1", "yes")
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("feedback_agent", {})
                   or {}).get("dry_run")
            if val is not None:
                return str(val).strip().lower() in ("true", "1", "yes")
    except Exception as e:  # noqa: BLE001
        logger.debug("[FeedbackAgent] config.yaml 读取失败: %s", e)
    return DEFAULT_DRY_RUN


def _audit_file() -> str:
    """审计日志路径（默认 data/feedback_agent_audit.jsonl，env 覆盖）。"""
    env = os.environ.get(f"{_ENV_PREFIX}_AUDIT_FILE")
    if env is not None and env.strip():
        return env.strip()
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("feedback_agent", {})
                   or {}).get("audit_file")
            if val:
                return str(val)
    except Exception as e:  # noqa: BLE001
        logger.debug("[FeedbackAgent] config.yaml 读取失败: %s", e)
    return DEFAULT_AUDIT_FILE


class FeedbackAgent:
    """反馈建议自动执行体（默认 dry-run，全部走既有 API）"""

    def __init__(self, *, service: Optional[Any] = None,
                 audit_path: Optional[str] = None):
        """Args:
            service: SkillsMgmtService 实例（None 时懒加载默认实例）
            audit_path: 审计日志路径（None 时按配置/默认）
        """
        self._service = service
        self._audit_path = audit_path
        self._scheduled_task_id: Optional[str] = None

    # ─── 依赖注入（便于测试） ───

    def _get_service(self) -> Any:
        if self._service is None:
            from agent.skills_mgmt.service import SkillsMgmtService
            self._service = SkillsMgmtService()
        return self._service

    def _find_best_duplicate(self, skill: Any, others: List[Any]) -> Optional[Dict[str, Any]]:
        """取与 skill Jaccard 相似度最高的重复技能（<min_jaccard 返回 None）。"""
        try:
            from agent.skills_mgmt.reviewer import SkillReviewer
        except Exception as e:  # noqa: BLE001
            logger.debug("[FeedbackAgent] 加载 SkillReviewer 失败: %s", e)
            return None
        try:
            dups = SkillReviewer().find_duplicates_for(
                skill, others, min_jaccard=_MIN_MERGE_JACCARD)
        except Exception as e:  # noqa: BLE001
            logger.warning("[FeedbackAgent] 重复检测失败 skill=%s: %s",
                           skill.id, e)
            return None
        if not dups:
            return None
        return max(dups, key=lambda d: d.get("jaccard", 0.0))

    # ─── 主入口 ───

    def execute_recommendations(self, dry_run: bool = True,
                                days: int = 30) -> Dict[str, Any]:
        """遍历全部 Skill 的反馈建议并按建议执行（默认 dry-run）。

        Args:
            dry_run: True 只产出报告零副作用；False 实际执行并写审计日志
            days: 反馈统计窗口（天），透传 get_skill_feedback_summary

        Returns:
            报告 dict：started_at/finished_at/dry_run/total_skills/processed/
            actions(计数)/planned/executed/rejected/errors/audit_log
        """
        svc = self._get_service()
        logger.info(
            "[FeedbackAgent] execute_recommendations start dry_run=%s "
            "days=%d 审计文件=%s", dry_run, days,
            self._audit_path or _audit_file())
        report: Dict[str, Any] = {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "dry_run": bool(dry_run),
            "total_skills": 0,
            "processed": 0,
            "actions": {ACTION_PROMOTE: 0, ACTION_DEPRECATE_MERGE: 0,
                        ACTION_IMPROVE: 0, ACTION_KEEP: 0, ACTION_NO_DATA: 0},
            "planned": [],
            "executed": [],
            "rejected": [],
            "errors": [],
            "audit_log": self._audit_path or _audit_file(),
        }
        try:
            skills = svc.store.list_all()
        except Exception as e:  # noqa: BLE001
            logger.error("[FeedbackAgent] 读取技能库失败: %s", e)
            report["errors"].append({"skill_id": "*", "error": str(e)})
            report["finished_at"] = datetime.now().isoformat(timespec="seconds")
            return report

        report["total_skills"] = len(skills)
        for skill in skills:
            # 逐 skill try/except：任一技能失败不中断批量（验收要求）
            try:
                self._process_skill(svc, skill, days=days, dry_run=dry_run,
                                    report=report)
                report["processed"] += 1
            except Exception as e:  # noqa: BLE001
                logger.error("[FeedbackAgent] 技能 %s 处理失败: %s",
                             skill.id, e)
                report["errors"].append(
                    {"skill_id": skill.id, "error": str(e)})
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        logger.info(
            "[FeedbackAgent] execute_recommendations done dry_run=%s "
            "processed=%s/%s executed=%d rejected=%d errors=%d",
            dry_run, report["processed"], report["total_skills"],
            len(report["executed"]), len(report["rejected"]),
            len(report["errors"]))
        return report

    def _process_skill(self, svc: Any, skill: Any, *, days: int,
                       dry_run: bool, report: Dict[str, Any]) -> None:
        """单个技能的反馈建议分派。

        注意: get_skill_feedback_summary 对"无反馈"正常返回 recommended_action=
        no_data（不抛异常）；此处捕获到异常即真故障（如反馈库不可用），
        重新抛出交由外层记录 errors（故障显性化，且不中断批量）。
        """
        summary = svc.get_skill_feedback_summary(skill.id, days=days)
        action = summary.get("recommended_action", ACTION_NO_DATA)
        if action not in report["actions"]:
            action = ACTION_NO_DATA

        if action == ACTION_KEEP or action == ACTION_NO_DATA:
            report["actions"][action] += 1
            report["planned"].append(
                {"skill_id": skill.id, "action": action, "reason": "跳过"})
            logger.info(
                "[FeedbackAgent] skill=%s action=%s dry_run=%s 跳过（无动作）",
                skill.id, action, dry_run)
            return

        report["actions"][action] += 1
        reason = _recommendation_reason(action, summary)
        if dry_run:
            report["planned"].append(
                {"skill_id": skill.id, "action": action, "reason": reason,
                 "snapshot_version": None})
            logger.info(
                "[FeedbackAgent] skill=%s action=%s dry_run=True 计划执行（零副作用）原因=%s",
                skill.id, action, reason)
            return

        logger.info(
            "[FeedbackAgent] skill=%s action=%s dry_run=False 开始执行 原因=%s",
            skill.id, action, reason)

        # 正式执行：动作前打版本快照（可回滚）
        if action == ACTION_PROMOTE:
            self._execute_promote(svc, skill, reason, report)
        elif action == ACTION_DEPRECATE_MERGE:
            self._execute_deprecate_or_merge(svc, skill, reason, report)
        elif action == ACTION_IMPROVE:
            self._execute_improve(svc, skill, summary, reason, report)

    # ─── 动作实现（正式执行路径） ───

    def _execute_promote(self, svc: Any, skill: Any, reason: str,
                         report: Dict[str, Any]) -> None:
        """promote_to_published：bump 快照后走强制审核链 publish。

        TASK-04 强制链联动：无 PASSED ReviewResult 时 service.publish
        抛 SkillReviewError → 记录 rejected，不绕过审核。
        """
        from .exceptions import SkillReviewError
        try:
            bump = svc.bump_version(
                skill.id, "minor",
                changelog="[feedback_agent] promote 前快照（可回滚）")
            published = svc.publish(
                skill.id, actor="feedback_agent", reason=reason)
            record = {
                "skill_id": skill.id, "action": ACTION_PROMOTE,
                "reason": reason, "result": "published",
                "snapshot_version": bump.new_version,
                "status": published.status.value if hasattr(published, "status") else None,
            }
            report["executed"].append(record)
            self._audit(record)
            logger.info(
                "[FeedbackAgent] promote 成功 skill=%s 快照版本=%s status=%s",
                skill.id, bump.new_version, record["status"])
        except SkillReviewError as e:
            report["rejected"].append({
                "skill_id": skill.id, "action": ACTION_PROMOTE,
                "reason": reason, "result": "rejected",
                "error": str(e)})
            logger.warning(
                "[FeedbackAgent] promote 被拒 skill=%s 原因=%s 错误=%s（强制审核链联动）",
                skill.id, reason, e)

    def _execute_deprecate_or_merge(self, svc: Any, skill: Any, reason: str,
                                    report: Dict[str, Any]) -> None:
        """consider_deprecate_or_merge：有高相似技能 → merge；否则 DEPRECATED。"""
        others = [s for s in svc.store.list_all() if s.id != skill.id]
        best = self._find_best_duplicate(skill, others)
        if best is not None and best.get("other_id"):
            dst_id = best["other_id"]
            bump = svc.bump_version(
                dst_id, "minor",
                changelog="[feedback_agent] merge 前快照（保留方可回滚）")
            merged = svc.merge_duplicate_skills(
                skill.id, dst_id, strategy="auto", rebind_feedback=True)
            record = {
                "skill_id": skill.id, "action": ACTION_DEPRECATE_MERGE,
                "reason": reason, "result": "merged",
                "merged_into": dst_id,
                "jaccard": round(best.get("jaccard", 0.0), 4),
                "snapshot_version": bump.new_version,
            }
            report["executed"].append(record)
            self._audit(record)
            logger.info(
                "[FeedbackAgent] merge 执行 skill=%s -> %s jaccard=%s "
                "快照版本=%s（保留方）", skill.id, dst_id,
                record["jaccard"], bump.new_version)
            return
        # 无高相似技能 → 状态迁移 DEPRECATED（仅状态，绝不删除文件）
        from .models import SkillStatus
        bump = svc.bump_version(
            skill.id, "patch",
            changelog="[feedback_agent] deprecate 前快照（可回滚）")
        skill.status = SkillStatus.DEPRECATED
        svc.store.upsert(skill)
        record = {
            "skill_id": skill.id, "action": ACTION_DEPRECATE_MERGE,
            "reason": reason, "result": "deprecated",
            "snapshot_version": bump.new_version,
        }
        report["executed"].append(record)
        self._audit(record)
        logger.info(
            "[FeedbackAgent] deprecate 执行 skill=%s 快照版本=%s（仅状态迁移，不删文件）",
            skill.id, bump.new_version)

    def _execute_improve(self, svc: Any, skill: Any, summary: Dict[str, Any],
                         reason: str, report: Dict[str, Any]) -> None:
        """improve_params：bump 快照后调 SkillEnhancer 参数优化。"""
        bump = svc.bump_version(
            skill.id, "patch",
            changelog="[feedback_agent] improve_params 前快照（可回滚）")
        result = svc.optimize_params(skill.id, feedback_summary=summary)
        record = {
            "skill_id": skill.id, "action": ACTION_IMPROVE,
            "reason": reason, "result": "params_optimized",
            "snapshot_version": bump.new_version,
            "optimized": bool(result.get("optimized", False)),
        }
        report["executed"].append(record)
        self._audit(record)
        logger.info(
            "[FeedbackAgent] improve_params 执行 skill=%s 快照版本=%s optimized=%s",
            skill.id, bump.new_version, record["optimized"])

    # ─── 审计 ───

    def _audit(self, record: Dict[str, Any]) -> None:
        """正式动作逐条写 JSONL 审计日志（失败仅告警不阻断）。"""
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": "feedback_action",
            **record,
        }
        try:
            path = Path(self._audit_path or _audit_file())
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("[FeedbackAgent] 审计日志写入失败: %s", e)

    # ─── 调度注册（与 TASK-04 precipitate 同一 task_scheduler 收口） ───

    def schedule(self, *, interval_hours: Optional[int] = None) -> Dict[str, Any]:
        """注册每日反馈建议执行任务（默认关闭，安全底线）。"""
        hours = interval_hours if interval_hours is not None else _interval_hours()
        if not _enabled():
            logger.warning(
                "[FeedbackAgent] 调度默认关闭（安全底线）；"
                "开启: config learning.feedback_agent.enabled=true / "
                ".env LEARNING_FEEDBACK_AGENT_ENABLED=true")
            return {
                "status": "disabled",
                "interval_hours": hours,
                "dry_run": _dry_run(),
                "note": "learning.feedback_agent.enabled=false，默认关闭（安全底线）",
            }
        try:
            from agent.task_scheduler import get_scheduler
            sched = get_scheduler()
        except Exception as e:  # noqa: BLE001 调度器不可用
            logger.error("[FeedbackAgent] 调度器不可用: %s", e)
            return {"status": "error", "error": str(e)}
        sched.add_interval_task(
            TASK_NAME, func=self._scheduled_run, interval_seconds=hours * 3600)
        self._scheduled_task_id = (
            sched.tasks[-1]["task_id"] if sched.tasks else TASK_NAME)
        logger.info("[FeedbackAgent] 定时任务已注册 interval_hours=%d dry_run=%s",
                    hours, _dry_run())
        return {
            "status": "scheduled",
            "task_id": self._scheduled_task_id,
            "interval_hours": hours,
            "dry_run": _dry_run(),
            "note": "定时执行 execute_recommendations(dry_run=配置值)，默认不写",
        }

    def unschedule(self) -> bool:
        """注销反馈建议执行任务（按固定任务名定位，可跨实例）。"""
        try:
            from agent.task_scheduler import get_scheduler
            sched = get_scheduler()
        except Exception as e:  # noqa: BLE001
            logger.error("[FeedbackAgent] 调度注销失败: %s", e)
            return False
        for task in sched.tasks:
            if task.get("name") == TASK_NAME:
                removed = sched.remove_task(task["task_id"])
                self._scheduled_task_id = None
                return removed
        return False

    def _scheduled_run(self) -> None:
        """调度触发入口：跑一轮反馈建议执行；异常不抛出（调度线程稳定性）。"""
        logger.info("[FeedbackAgent] scheduled_run.start dry_run=%s", _dry_run())
        try:
            self.execute_recommendations(dry_run=_dry_run())
        except Exception as e:  # noqa: BLE001
            logger.error("[FeedbackAgent] scheduled_run 失败: %s", e)


def _recommendation_reason(action: str, summary: Dict[str, Any]) -> str:
    """把反馈汇总转成可读的触发原因（供审计与报告）。"""
    satisfaction = summary.get("satisfaction_rate_percent")
    total = summary.get("total_feedback", 0)
    avg = summary.get("avg_rating")
    if action == ACTION_PROMOTE:
        return (f"满意率 {satisfaction}% 达 90% 且反馈数 {total}>=5")
    if action == ACTION_DEPRECATE_MERGE:
        return (f"满意率 {satisfaction}% 低于 50% 且反馈数 {total}>=5")
    if action == ACTION_IMPROVE:
        return f"平均评分 {avg} < 3.0"
    return "keep"


__all__: List[str] = [
    "FeedbackAgent", "TASK_NAME", "DEFAULT_INTERVAL_HOURS",
    "DEFAULT_AUDIT_FILE", "ACTION_PROMOTE", "ACTION_DEPRECATE_MERGE",
    "ACTION_IMPROVE", "ACTION_KEEP", "ACTION_NO_DATA",
    "_enabled", "_interval_hours", "_dry_run", "_audit_file",
]
